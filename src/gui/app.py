"""主窗口：**FluentUI**（qfluentwidgets）外壳 —— 导航 + 页面堆叠 + 托盘。

界面用的是 qfluentwidgets 的 ``FluentWindow``：
左侧 Fluent 导航（带展开/收起动画）、云母（Mica）材质标题栏、
右上角原生风格的窗口按钮，这些都由框架负责，本文件只做组装。

注意（沿用之前踩坑的结论）：
``Qt.AA_ShareOpenGLContexts`` 与 ``prepare_webengine()`` 必须在
``QApplication`` 之前执行，所以 browser 模块要在文件顶部就导入。
"""

from __future__ import annotations

import os
import sys
import threading
import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QMenu,
    QShortcut,
    QSystemTrayIcon,
    QWidget,
)


from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, NavigationItemPosition

from . import browser as browser_module
from . import theme
from .pages.developer import DeveloperPage
from .pages.home import HomePage
from .pages.settings import SettingsPage
from .pages.hotspot import HotspotPage
from .pages.tools import AccountView, DiagView, SelfServiceView, TrafficView, WifiView
from .toasts import Toast

from ..core.deps import resource_path
from ..core import notify as notify_identity
from ..core.service import Service
from ..core.settings import Settings, display_name
from ..core.winutil import is_admin

APP_TITLE = "校园网客户端"
APP_SUBTITLE = "南阳科技校园网 · 自动认证与流量统计"
NOTIFY_APP_NAME = notify_identity.NOTIFY_APP_NAME


class MainWindow(FluentWindow):
    """主窗口。"""

    log_received = pyqtSignal(dict)
    state_received = pyqtSignal(dict)
    traffic_received = pyqtSignal(dict)

    def __init__(self, service: Service) -> None:
        super().__init__()
        self.service = service
        self._closing = False
        self._browser = None
        self._state = {}
        self._last_title = ""
        self._last_tray_status = ""

        self.app_title = display_name(self.service.settings.get("app_name"))
        self.setWindowTitle(f"{self.app_title}（未连接）")
        icon_path = resource_path("app.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(*theme.WINDOW_DEFAULT)
        self.setMinimumSize(*theme.WINDOW_MIN)
        self._restore_or_center()

        theme.apply_theme(service.settings.theme or "auto", QApplication.instance())
        if service.settings.mica_effect:
            try:
                self.setMicaEffectEnabled(True)
            except Exception:
                pass

        self.home_page = HomePage(service, self)
        self.wifi_page = WifiView(service, self)
        self.diag_page = DiagView(service, self)
        self.traffic_page = TrafficView(service, self)
        self.account_page = AccountView(service, self)
        self.selfservice_page = SelfServiceView(service, self)
        self._merge_selfservice_into_account()

        for page, name in (
            (self.wifi_page, "WifiPage"),
            (self.diag_page, "DiagPage"),
            (self.traffic_page, "TrafficPage"),
            (self.account_page, "AccountPage"),
        ):
            if not page.objectName():
                page.setObjectName(name)

        self.settings_page = SettingsPage(service, self)
        self.developer_page = DeveloperPage(service, self)
        self.developer_page.hide()
        self._developer_added = False

        try:
            self.navigationInterface.setReturnButtonVisible(False)
        except Exception:
            pass

        self.addSubInterface(self.home_page, FIF.HOME, "主页")
        self.hotspot_page: HotspotPage | None = None
        try:
            supported = bool(service.hotspot_supported())
        except Exception:
            supported = False
        if supported:
            self.hotspot_page = HotspotPage(service, self)
            self.hotspot_page.setObjectName("HotspotPage")

        self.addSubInterface(self.wifi_page, FIF.WIFI, "无线网络")
        if self.hotspot_page is not None:
            self.addSubInterface(self.hotspot_page, FIF.SHARE, "热点共享")
        self.addSubInterface(self.diag_page, FIF.SPEED_HIGH, "网络诊断")
        self.addSubInterface(self.traffic_page, FIF.CLOUD, "流量统计")
        self.addSubInterface(self.account_page, FIF.PEOPLE, "账户与设备")
        self.stackedWidget.currentChanged.connect(self._on_page_changed)
        self.addSubInterface(self.settings_page, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM)
        self._sync_developer_page()

        try:
            self.navigationInterface.setExpandWidth(190)
        except Exception:
            pass

        self._build_tray()
        self._install_shortcuts()
        self._wire_signals()
        self.switchTo(self.home_page)
        QTimer.singleShot(250, self._post_start)

    def nav_pages(self) -> list[QWidget]:
        """导航里的六个常驻页面，顺序与左侧导航一致（开发者页按需插入）。"""
        pages = [
            self.home_page,
            self.wifi_page,
            self.diag_page,
            self.traffic_page,
            self.account_page,
            self.settings_page,
        ]
        if self.hotspot_page is not None:
            pages.insert(2, self.hotspot_page)
        return pages

    def _install_shortcuts(self) -> None:
        """全局快捷键：没有鼠标也能走完常用路径。"""
        self._shortcuts: list[QShortcut] = []

        def add(sequence: str, slot) -> None:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

        add("Ctrl+,", self.open_settings)
        add("Ctrl+R", self.refresh_current_page)
        add("Ctrl+Shift+W", self.open_portal_browser)
        for index, page in enumerate(self.nav_pages(), start=1):
            add(f"Ctrl+{index}", lambda target=page: self.switchTo(target))

    def open_settings(self, section: int | None = None) -> None:
        """打开设置页；``section`` 给了就顺带滚动到那一节。"""
        self.show_window()
        self.switchTo(self.settings_page)
        if section is not None:
            QTimer.singleShot(60, lambda: self.settings_page.switch_group(section))

    def refresh_current_page(self) -> None:
        """刷新当前页：优先 refresh()，其次 refresh_all()，都没有就提示。"""
        page = self.stackedWidget.currentWidget()
        for name in ("refresh", "refresh_all", "reload"):
            method = getattr(page, name, None)
            if callable(method):
                method()
                return
        Toast.info(self, "刷新", "当前页面没有可刷新的内容。")

    def show_window(self) -> None:
        """把窗口叫到前台（托盘与快捷键共用）。"""
        self._show_from_tray()

    def pages(self) -> list[QWidget]:
        pages = [
            self.home_page,
            self.wifi_page,
            self.diag_page,
            self.traffic_page,
            self.account_page,
            self.settings_page,
            self.developer_page,
        ]
        if self.hotspot_page is not None:
            pages.insert(2, self.hotspot_page)
        return pages

    def _sync_developer_page(self) -> None:
        """按设置决定「开发者」是否出现在导航里。"""
        enabled = bool(self.service.settings.developer_mode)
        if enabled == self._developer_added:
            return
        try:
            if enabled:
                self.addSubInterface(
                    self.developer_page,
                    FIF.ROBOT,
                    "开发者",
                    NavigationItemPosition.BOTTOM,
                )
                self._developer_added = True
                return
            self.navigationInterface.removeWidget(self.developer_page.objectName())
            self.stackedWidget.removeWidget(self.developer_page)
            self.developer_page.setParent(self)
            self.developer_page.hide()
            self._developer_added = False
        except Exception:
            self._developer_added = enabled

    def set_developer_mode(self, enabled: bool) -> None:
        """设置页切换开发者模式后调用。"""
        self.service.settings.update({"developer_mode": bool(enabled)})
        self.service.settings.save()
        self._sync_developer_page()
        if enabled:
            self.switchTo(self.developer_page)
            Toast.info(self, "开发者模式已开启", "左侧导航底部出现了「开发者」，可以逐个调用接口看原始返回。")
        else:
            Toast.info(self, "开发者模式已关闭", "「开发者」已从导航中移除。")

    def _merge_selfservice_into_account(self) -> None:
        """把自助服务的每张卡片平铺进「账户与设备」页。

        这段原来写在 ``ToolsPage`` 里；「网络工具」拆掉之后必须搬到窗口层，
        否则账户页会丢掉套餐流量、改密码、订单、账单、上网明细那几块。
        """
        try:
            area = self.account_page.layout().itemAt(0).widget()
            host = area.widget().layout()
            inner = self.selfservice_page.layout().itemAt(0).widget()
            content = inner.takeWidget() if hasattr(inner, "takeWidget") else None
            if content is None:
                host.addWidget(self.selfservice_page)
                return
            host.setContentsMargins(28, 20, 28, 28)
            host.setSpacing(18)
            source = content.layout()
            while source is not None and source.count():
                item = source.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(self.account_page)
                    host.addWidget(widget)
                else:
                    child = item.layout()
                    if child is None:
                        continue
                    host.addLayout(child)
            content.setParent(None)
            content.hide()
            self.selfservice_page.hide()
        except Exception:
            pass

    def _on_page_changed(self, index: int) -> None:
        """切页时刷新该页数据（原来是分段切换时做的事）。"""
        page = self.stackedWidget.widget(index)
        for method in ("refresh_all", "refresh", "refresh_status"):
            fn = getattr(page, method, None)
            if not callable(fn):
                continue
            try:
                fn()
            except Exception:
                pass
            break
        if page is self.account_page:
            try:
                self.selfservice_page.refresh_all()
            except Exception:
                pass

    def page_for_key(self, key: str) -> QWidget | None:
        return {
            "home": self.home_page,
            "wifi": self.wifi_page,
            "diag": self.diag_page,
            "traffic": self.traffic_page,
            "account": self.account_page,
            "settings": self.settings_page,
        }.get(key)

    def switch_to(self, key: str) -> None:
        page = self.page_for_key(key)
        if page is not None:
            self.switchTo(page)

    def _wire_signals(self) -> None:
        for page in self.pages():
            if hasattr(page, "bind_window"):
                page.bind_window(self)

        self.log_received.connect(self._on_log)
        self.state_received.connect(self._on_state)
        self.traffic_received.connect(self._on_traffic)

        self.service.log_added.connect(self.log_received.emit)
        self.service.state_changed.connect(self.state_received.emit)
        self.service.traffic_sample.connect(self.traffic_received.emit)
        self.service.notify.connect(self._on_notify)

    def _post_start(self) -> None:
        threading.Thread(target=self._start_service, name="service-start", daemon=True).start()

    def _start_service(self) -> None:
        try:
            self.service.start()
        except Exception as exc:
            self.log_received.emit(
                {
                    "at": time.time(),
                    "level": "error",
                    "message": f"服务启动失败：{exc}",
                }
            )
            return
        self.service.start_loop(15000)
        self.state_received.emit(self.service.state())

    def _on_log(self, entry: dict) -> None:
        for page in self.pages():
            if hasattr(page, "append_log"):
                page.append_log(entry)

    def _campus_online(self, state: dict) -> bool:
        """是否处在校园网环境 —— 决定顶栏显示「已连接」还是「未连接」。

        直接用传进来的那份状态判断（不读服务内部状态，免得时序上拿到旧值）：
        连的是家里 WiFi / 手机热点时，哪怕能上网也显示"未连接"。
        """
        if not state.get("online"):
            return False
        ssid = str(state.get("ssid") or "")
        allow = list(self.service.settings.wifi_allow_list or [])
        if ssid:
            try:
                from ..net import wifi as wifi_module

                return bool(wifi_module.any_ssid_matches(ssid, allow))
            except Exception:
                return False
        try:
            return bool(self.service.on_campus_network())
        except Exception:
            return True

    def _on_state(self, state: dict) -> None:
        self._state = state
        for page in self.pages():
            if hasattr(page, "on_state"):
                page.on_state(state)
        label = state.get("label", "")
        ssid = state.get("ssid", "")
        suffix = f" · {ssid}" if ssid else ""
        title = f"{self.app_title}（{'已连接' if self._campus_online(state) else '未连接'}）"
        if title != self._last_title:
            self._last_title = title
            self.setWindowTitle(title)
        if hasattr(self, "tray"):
            status_text = f"状态：{label}{suffix}"
            if status_text != self._last_tray_status:
                self._last_tray_status = status_text
                self._tray_status.setText(status_text)
                self.tray.setToolTip(title)

    def _on_traffic(self, sample: dict) -> None:
        for page in self.pages():
            if hasattr(page, "on_traffic"):
                page.on_traffic(sample)

    def _on_notify(self, title: str, message: str) -> None:
        """连接事件走 Windows 通知（应用名「校园网」）。

        刻意**不**在窗口里再弹一条提示条：掉线/恢复这种事件本来就要求「尽量无感」，
        弹两次反而更打扰。窗口内的 InfoBar 只留给用户主动触发的操作。
        """
        name = title or NOTIFY_APP_NAME
        try:
            self.tray.showMessage(name, message, QSystemTrayIcon.Information, 5000)
        except Exception:
            Toast.show_toast(self, name, message, "info")

    def notify(self, title: str, content: str = "", kind: str = "info") -> None:
        """给页面用的提示入口。"""
        Toast.show_toast(self, title, content, kind)

    def refresh_theme(self) -> None:
        """主题切换后，通知那些自绘的部件刷新颜色。"""
        for page in self.pages():
            if hasattr(page, "on_theme_changed"):
                page.on_theme_changed()
        if self._browser is not None:
            self._browser.hide()

    def open_portal_browser(self, status: dict | None = None, url: str = "") -> None:
        """在程序内打开 Portal（或指定的自助系统页面），不调起系统浏览器。"""
        window, info = browser_module.open_browser(self.service, status, self, url=url)
        if window is None:
            Toast.warning(
                self,
                "内置浏览器不可用",
                f"{info}。可执行 pip install PyQtWebEngine 后重试；已改用系统浏览器。",
                8000,
            )
            result = self.service.open_url(url) if url else self.service.open_portal_in_browser()
            if not result.get("ok"):
                Toast.error(self, "也打不开系统浏览器", str(result.get("message", "")))
            return
        self._browser = window
        if url:
            Toast.info(self, "已打开页面", f"在程序内部浏览器里打开了 {url}")
        else:
            Toast.info(self, "已打开认证页面", "在窗口里手动登录后，回主界面点「检测并登录」即可")

    @staticmethod
    def _tray_icon() -> QIcon:
        for name in ("tray@2x.png", "tray.png", "app.ico"):
            path = resource_path(name)
            if not path:
                continue
            if os.path.exists(path):
                return QIcon(path)
        return QIcon()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self._tray_icon(), self)
        self.tray.setToolTip(f"{self.app_title} — 待检测")

        menu = QMenu()
        self._tray_status = QAction("状态：待检测", menu)
        self._tray_status.setEnabled(False)
        menu.addAction(self._tray_status)
        menu.addSeparator()
        menu.addAction("显示主界面", self._show_from_tray)
        menu.addAction("立即检测并登录", self._quick_login)
        menu.addAction("打开认证页面（内置浏览器）", lambda: self.open_portal_browser())
        menu.addSeparator()
        menu.addAction("设置…", lambda: self.open_settings())
        menu.addAction("流量统计", lambda: self.open_traffic())
        menu.addSeparator()
        menu.addAction("退出", self._quit_app)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_from_tray()
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
            else None
        )

        self.tray.show()

    def open_traffic(self) -> None:
        """托盘菜单：跳到流量统计页并显示窗口。"""
        self.show_window()
        self.switchTo(self.traffic_page)

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quick_login(self) -> None:
        self._show_from_tray()
        self.switch_to("home")
        self.home_page.run_connect()

    def _quit_app(self) -> None:
        self._closing = True
        self.hide()
        self.tray.hide()
        QApplication.instance().quit()

    def _restore_or_center(self) -> None:
        """决定窗口开在哪：优先恢复上次的位置，但必须夹在可见范围内。

        以前没做这件事，Qt 会把它放在系统给的默认位置（偏左上），
        上次存的位置如果来自副屏或换分辨率之前，窗口还会跑到屏幕外。
        """
        from PyQt5.QtWidgets import QApplication

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()

        saved = str(self.service.settings.window_geometry or "")
        if saved:
            try:
                from PyQt5.QtCore import QByteArray

                if self.restoreGeometry(QByteArray.fromBase64(saved.encode())):
                    rect = self.frameGeometry()
                    if any(s.availableGeometry().intersects(rect) for s in QApplication.screens()):
                        return
            except Exception:
                pass
        self._center_on(area)

    def _center_on(self, area) -> None:
        """在给定区域里居中。"""
        size = self.size()
        width = size.width() if size.width() > 400 else theme.WINDOW_DEFAULT[0]
        height = size.height() if size.height() > 300 else theme.WINDOW_DEFAULT[1]
        self.resize(width, height)
        self.move(
            area.x() + max(0, (area.width() - width) // 2),
            area.y() + max(0, (area.height() - height) // 2),
        )

    def show_window(self) -> None:
        """把主窗口调出来（托盘菜单、以及第二个实例启动时都会用）。"""
        try:
            self.showNormal()
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        if self.service.settings.close_to_tray and not self._closing:
            event.ignore()
            self.hide()
            # 只在第一次提示，之后安静地缩到托盘（提示语重复反而烦）。
            if not bool(self.service.settings.get("tray_hint_shown")):
                self.tray.showMessage(
                    APP_TITLE,
                    "已最小化到托盘，右键托盘图标可退出；下次不再提示",
                    QSystemTrayIcon.Information,
                    2500,
                )
                try:
                    self.service.settings.set("tray_hint_shown", True)
                    self.service.settings.save()
                except Exception:
                    pass
            return
        self._closing = True

        try:
            geometry = bytes(self.saveGeometry().toBase64()).decode()
            self.service.settings.update({"window_geometry": geometry})
            self.service.settings.save()
        except Exception:
            pass

        try:
            self.tray.hide()
        except Exception:
            pass

        if self._browser is not None:
            try:
                self._browser.deleteLater()
            except Exception:
                pass

        threading.Thread(target=self.service.stop, daemon=True).start()
        event.accept()


def run_app() -> int:
    """启动应用。"""
    for attribute in (
        "AA_ShareOpenGLContexts",
        "AA_EnableHighDpiScaling",
        "AA_UseHighDpiPixmaps",
    ):
        if hasattr(Qt, attribute):
            try:
                QApplication.setAttribute(getattr(Qt, attribute), True)
            except Exception:
                pass

    browser_module.prepare_webengine()

    identity = notify_identity.register_notify_identity(resource_path("app.ico") or "")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)

    from ..core import single_instance
    from ..core.settings import default_data_dir

    data_dir = default_data_dir()

    # 已有实例：只把它叫出来，自己不再建界面。
    if single_instance.notify_existing():
        return 0
    if single_instance.is_running():
        # 命名管道被提权级别挡住时，用标记文件让已有实例自己弹窗。
        single_instance.request_show(data_dir)
        return 0

    # 监听要尽早建立：晚于窗口构造的话，用户在这期间再点一次图标
    # 就会因为「连不上服务器」而启动第二个进程，反过来把对方的监听顶掉。
    holder: dict[str, object] = {}

    def _show_window() -> None:
        window = holder.get("window")
        if window is not None:
            window.show_window()

    server = single_instance.create_server(_show_window, data_dir)

    settings = Settings().load()
    theme.apply_theme(settings.theme or "auto", app)

    service = Service(settings)
    service.log("info", identity.get("message") or "通知身份已初始化")
    window = MainWindow(service)
    holder["window"] = window
    app._single_instance_server = server  # 跟着应用活，避免被 GC

    if not is_admin() and "--elevated-retry" not in sys.argv:
        service.log("warn", "当前未提权：若连接 WiFi 失败，会在那一刻请求一次 UAC")

    if "--minimized" in sys.argv or settings.start_minimized:
        window.hide()
    else:
        window.show()

    QTimer.singleShot(
        1000,
        lambda: window.state_received.emit(window.service.state()),
    )
    return app.exec_()
