"""内置浏览器：在程序内部打开 Portal 认证页，不再调起系统浏览器。

为什么要内置：

* 校园门户经常需要人工介入（验证码、公告确认、选择套餐），
  自动登录失败时用户必须在**同一个网络环境**里手动点一下；
* 调起外部浏览器会切走焦点，用户还得自己找回来，体验割裂。

**三条硬性约束**（都是实测踩出来的，改这里时务必保留）：

1. ``QtWebEngineWidgets`` 必须在 ``QApplication`` 之前导入 ——
   所以本模块在 import 时就完成导入，``app.py`` 里保证先 import 本模块。
2. ``Qt.AA_ShareOpenGLContexts`` 必须在 ``QApplication`` 之前设置 ——
   否则创建 ``QWebEngineView`` 时直接 0xC0000005 崩溃。
3. ``QWebEngineProfile`` **不能**在 ``QApplication`` 之前访问 ——
   同样会 0xC0000005 崩溃，所以 Profile 配置放在窗口构造里。
"""

from __future__ import annotations

import html
import os
import sys

from PyQt5.QtCore import QSettings, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import PrimaryPushButton

from ..core.deps import resource_path
from ..core.settings import display_name

from . import icons, theme
from .widgets import body_label

try:
    from ..portal.selfbase import SELFPAGE
except Exception:
    SELFPAGE = "http://10.100.2.2"

WEBENGINE_AVAILABLE = False
WEBENGINE_ERROR = ""
try:
    from PyQt5.QtWebEngineWidgets import (
        QWebEnginePage,
        QWebEngineProfile,
        QWebEngineSettings,
        QWebEngineView,
    )

    class _InlineWebEngineView(QWebEngineView):
        """所有 ``window.open`` / 目标为 _blank 的链接都在当前视图打开。

        默认实现会另开一个没有地址栏的裸窗口，用户关掉后原窗口还会停在
        旧页面上，体验很割裂。返回 ``self`` 就是让新页面直接在当前视图加载。
        """

        def createWindow(self, _type):  # noqa: N802 - Qt 命名
            return self

    class _BrowserPage(QWebEnginePage):
        """放行证书错误，并把错误页里的 ``system:`` 伪协议交给系统浏览器。

        校园网 Portal 与部分学校站点普遍使用自签证书 / SSL 拦截，Chromium
        默认会把它们判成错误页；本程序自动登录那边本来也是 ``verify=False``，
        这里保持同样的取舍，只弹一次提示告诉用户证书没通过。

        ``acceptNavigationRequest`` 只拦截 ``system:`` 开头的伪协议，正常
        ``http/https`` 地址原样交给基类，不会误伤页面内的普通链接。
        """

        certificate_ignored = pyqtSignal(str)
        system_link_requested = pyqtSignal(str)

        def certificateError(self, error):  # noqa: N802 - Qt 命名
            host = ""
            try:
                host = str(error.url().host() or "")
            except Exception:
                host = ""
            try:
                self.certificate_ignored.emit(host)
            except Exception:
                pass
            # 返回 True 表示「接受该证书」，页面继续加载。
            return True

        def acceptNavigationRequest(self, url, _nav_type, _is_main_frame):  # noqa: N802
            try:
                if str(url.scheme()).lower() == "system":
                    raw = url.toString()
                    prefix = "system:"
                    target = raw[len(prefix):] if raw.lower().startswith(prefix) else url.path()
                    if target:
                        self.system_link_requested.emit(target)
                    return False
            except Exception:
                pass
            try:
                return super().acceptNavigationRequest(url, _nav_type, _is_main_frame)
            except Exception:
                return True

    WEBENGINE_AVAILABLE = True
except Exception as exc:
    QWebEngineView = None
    QWebEnginePage = None
    QWebEngineProfile = None
    QWebEngineSettings = None
    _InlineWebEngineView = None
    _BrowserPage = None
    WEBENGINE_ERROR = f"{type(exc).__name__}: {exc}"


def _offscreen_platform() -> bool:
    """离屏平台没有 OpenGL 上下文，碰 ``defaultProfile()`` / ``page()`` 会崩。"""
    try:
        return QApplication.platformName().lower() == "offscreen"
    except Exception:
        return False


def _configure_profile() -> None:
    """配置默认 Profile：固定 UA、持久化 Cookie 与缓存。

    必须在 QApplication 创建之后调用（见模块头注释第 3 条）。
    """
    if not WEBENGINE_AVAILABLE:
        return

    try:
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return
        if app.platformName().lower() == "offscreen":
            # 实测：离屏平台上访问 defaultProfile() 会直接崩溃（无 OpenGL
            # 上下文）。冒烟测试不需要持久化 Cookie，直接跳过配置。
            return
    except Exception:
        return

    try:
        profile = QWebEngineProfile.defaultProfile()
        profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
        )
        profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        data_dir = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "CampusNet", "webengine")
        os.makedirs(data_dir, exist_ok=True)
        profile.setCachePath(os.path.join(data_dir, "cache"))
        profile.setPersistentStoragePath(os.path.join(data_dir, "storage"))
    except Exception:
        return


def prepare_webengine() -> None:
    """在创建 QApplication 之前调用，准备 WebEngine 的运行环境。

    只做「设置环境变量 / Qt 属性」这类安全操作；**不要**在这里碰
    ``QWebEngineProfile``。
    """
    if not WEBENGINE_AVAILABLE:
        return

    try:
        from PyQt5.QtCore import QCoreApplication, Qt as QtNS

        if hasattr(QtNS, "AA_ShareOpenGLContexts"):
            QCoreApplication.setAttribute(QtNS.AA_ShareOpenGLContexts, True)
    except Exception:
        pass

    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-gpu --disable-software-rasterizer --no-sandbox",
    )
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    try:
        import PyQt5

        qt_root = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5")
        plugins = os.path.join(qt_root, "plugins")
        if os.path.isdir(plugins):
            os.environ.setdefault("QT_PLUGIN_PATH", plugins)
        bin_dir = os.path.join(qt_root, "bin")
        if os.path.isdir(bin_dir):
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        return


class BrowserWindow(QWidget):
    """一个自带地址栏与导航按钮的内置浏览器窗口。"""

    login_clicked = pyqtSignal()

    def __init__(self, service=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._loading = False
        self._showing_error = False
        self._ignore_next_load = False
        self._stop_requested = False
        self._failed_url = ""
        self._shortcuts: list[QShortcut] = []
        self._page = None
        self._render_recovered = False
        self._certificate_warned = False
        self._certificate_notice_shown = False
        self._certificate_host = ""
        self._last_error_code = 0
        self._last_error_text = ""

        self.setWindowFlags(Qt.Window)
        self.setWindowTitle(f"校园网认证 · {self._app_title()}")
        icon_path = resource_path("app.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        _configure_profile()
        self._build_ui()

        self.setMinimumSize(900, 600)
        self.resize(1120, 760)
        self._restore_geometry()
        self._install_shortcuts()

    # ------------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)

        def tool_button(icon_name: str, tooltip: str) -> QPushButton:
            """工具条上的小图标按钮（30×30，紧凑排列）。"""
            button = QPushButton()
            button.setObjectName("IconButton")
            button.setIcon(icons.icon(icon_name, 16, theme.color("text")))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tooltip)
            button.setFixedSize(30, 30)
            return button

        def entry_button(icon_name: str, text: str, tooltip: str) -> QPushButton:
            """快捷入口按钮：自带文字，省掉额外的说明灰字。"""
            button = QPushButton(text)
            button.setIcon(icons.icon(icon_name, 15, theme.color("text")))
            button.setIconSize(QSize(15, 15))
            button.setToolTip(tooltip)
            button.setFixedHeight(30)
            return button

        self.forward_button = tool_button("forward", "前进（Alt+Right）")
        self.back_button = tool_button("back", "后退（Alt+Left）")
        self.reload_button = tool_button("refresh", "刷新（F5）")
        self.home_button = tool_button("home", "回到主页")
        # 新的规范属性名是 external_button；保留 open_external_button 别名，
        # 避免其它模块/旧代码引用时突然断掉。
        self.external_button = tool_button("link", "用系统默认浏览器打开当前地址")
        self.open_external_button = self.external_button
        for button in (self.forward_button, self.back_button, self.reload_button, self.home_button):
            bar.addWidget(button)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("输入 Portal 地址，例如 http://10.100.2.2:9090/")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.setMinimumWidth(260)
        prefix_action = self.url_edit.addAction(
            icons.icon("link", 16, theme.color("text_secondary")),
            QLineEdit.LeadingPosition,
        )
        if prefix_action is not None:
            prefix_action.setToolTip("当前页面地址")
        bar.addWidget(self.url_edit, 1)

        self.status_label = QLabel()
        self.status_label.setFixedSize(24, 24)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setPixmap(icons.pixmap("globe", 16, theme.color("text_muted")))
        self.status_label.setToolTip("等待加载页面")
        self.status_label.setAccessibleName("页面状态")
        bar.addWidget(self.status_label)

        self.portal_entry_button = entry_button(
            "shield",
            "认证页",
            "打开校园网 Portal 登录页（不一定是能上外网的页面）",
        )
        self.selfpage_entry_button = entry_button("globe", "自助系统", "打开学校自助服务首页")
        bar.addWidget(self.portal_entry_button)
        bar.addWidget(self.selfpage_entry_button)

        self.auto_login_button = PrimaryPushButton("自动填表登录")
        self.auto_login_button.setIcon(icons.icon("shield", 15, theme.color("text")))
        self.auto_login_button.setIconSize(QSize(15, 15))
        self.auto_login_button.setToolTip("把已保存的账号密码填进当前门户并尝试提交")
        self.auto_login_button.setFixedHeight(30)
        bar.addWidget(self.auto_login_button)

        bar.addWidget(self.external_button)
        self.close_button = QPushButton("关闭")
        self.close_button.setIcon(icons.icon("close", 15, theme.color("text")))
        self.close_button.setIconSize(QSize(15, 15))
        self.close_button.setFixedHeight(30)
        bar.addWidget(self.close_button)
        root.addLayout(bar)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        root.addWidget(self.progress)

        if WEBENGINE_AVAILABLE:
            self.view = _InlineWebEngineView(self)
            self.view.setMinimumHeight(400)
            if not _offscreen_platform():
                # 先换页面再配 settings：setPage() 之后 settings() 才是新页面的。
                self._install_custom_page()
                # 离屏平台访问 view.settings() 会触发默认 Profile 初始化并崩溃，
                # 冒烟测试不需要这些设置，直接跳过。
                settings = self.view.settings()
                settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
                settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
                settings.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, True)
                settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)
            root.addWidget(self.view, 1)
            if _offscreen_platform():
                # 离屏平台没有可用的 OpenGL 上下文，QWebEngineView 一旦 show()
                # 会直接崩溃；冒烟测试只需要能构造窗口，因此这里保持视图隐藏。
                # 页面与信号仍然正常创建，真实桌面平台不受影响。
                self.view.hide()
            self._wire_webengine()
        else:
            self.view = None
            fallback = body_label(
                f"内置浏览器不可用（缺少 PyQtWebEngine）。\n\n原因：{WEBENGINE_ERROR}"
                "\n\n可以在命令行执行下面命令安装后重试：\n    pip install PyQtWebEngine\n\n"
                "或者点工具条右侧的「用系统浏览器打开」临时顶上。"
            )
            fallback.setAlignment(Qt.AlignCenter)
            root.addWidget(fallback, 1)
            for button in (
                self.forward_button,
                self.back_button,
                self.reload_button,
                self.home_button,
                self.portal_entry_button,
                self.selfpage_entry_button,
                self.auto_login_button,
                self.external_button,
            ):
                button.setEnabled(False)
            self.url_edit.setEnabled(False)
            self._set_status("failed", "缺少 PyQtWebEngine")

    def _install_shortcuts(self) -> None:
        """窗口级快捷键；在网页里输入时也不会和普通输入冲突。"""
        bindings = [
            ("Ctrl+L", self._focus_url),
            ("F5", self._reload_page),
            ("Alt+Left", self._go_back),
            ("Alt+Right", self._go_forward),
            ("Esc", self._hide_window),
        ]
        for sequence, slot in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def _wire_webengine(self) -> None:
        self.view.urlChanged.connect(self._on_url_changed)
        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadFinished.connect(self._on_load_finished)
        try:
            self.view.renderProcessTerminated.connect(self._on_render_terminated)
        except Exception:
            # 旧版本 QtWebEngine 没有这个信号，忽略即可。
            pass

        self.back_button.clicked.connect(self.view.back)
        self.forward_button.clicked.connect(self.view.forward)
        self.reload_button.clicked.connect(self._on_reload_clicked)
        self.home_button.clicked.connect(lambda: self.open_url(self._home_url()))
        self.portal_entry_button.clicked.connect(self._open_portal_entry)
        self.selfpage_entry_button.clicked.connect(lambda: self.open_url(SELFPAGE))
        self.url_edit.returnPressed.connect(self._navigate_from_url)
        self.close_button.clicked.connect(self.close)
        self.external_button.clicked.connect(self._open_external)
        self.auto_login_button.clicked.connect(self._fill_and_submit)

    def _install_custom_page(self) -> None:
        """安装放行证书错误、拦截 ``system:`` 链接的页面对象。

        离屏平台在调用方就已跳过：那里连 ``view.page()`` 都不能碰。
        """
        if not WEBENGINE_AVAILABLE or self.view is None or _BrowserPage is None:
            return
        if _offscreen_platform():
            return
        try:
            page = _BrowserPage(self.view)
        except Exception:
            page = None
        if page is not None:
            try:
                page.certificate_ignored.connect(self._on_certificate_error)
                page.system_link_requested.connect(self._open_system_url)
                self.view.setPage(page)
                self._page = page
                self._connect_load_error(page)
                return
            except Exception:
                self._page = None
        # 兜底：个别 PyQt5 版本不允许自己实例化 Page，就覆盖同名方法。
        try:
            existing = self.view.page()
            if existing is None:
                return
            owner = self

            def _certificate_error(error):
                try:
                    owner._on_certificate_error(str(error.url().host() or ""))
                except Exception:
                    pass
                return True

            existing.certificateError = _certificate_error
            self._page = existing
        except Exception:
            pass

    def _connect_load_error(self, page=None) -> None:
        """Qt 版本提供 ``loadingChanged`` 时记录错误码/描述，供错误页展示。"""
        page = page if page is not None else self._page
        if page is None:
            return
        signal = getattr(page, "loadingChanged", None)
        if signal is None:
            return
        try:
            signal.connect(self._on_loading_changed)
        except Exception:
            pass

    def _on_loading_changed(self, request) -> None:
        try:
            code = int(request.errorCode())
            text = str(request.errorString() or "").strip()
        except Exception:
            return
        if not code:
            return
        self._last_error_code = code
        self._last_error_text = text

    # -------------------------------------------------------------- 窗口状态

    def _restore_geometry(self) -> None:
        try:
            settings = QSettings("CampusNet", "Browser")
            saved = settings.value("geometry")
            if saved and self.restoreGeometry(saved):
                return
        except Exception:
            pass
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _save_geometry(self) -> None:
        try:
            settings = QSettings("CampusNet", "Browser")
            settings.setValue("geometry", self.saveGeometry())
        except Exception:
            pass

    def _hide_window(self) -> None:
        self._save_geometry()
        self.hide()

    def _app_title(self) -> str:
        """可配置的程序名（设置 → 关于里可以改名），空值回落到 CampusNet。"""
        try:
            if self.service is not None:
                return display_name(self.service.settings.get("app_name"))
        except Exception:
            pass
        return display_name("")

    def _update_title(self, page_title: str = "") -> None:
        title = (page_title or "").strip()
        app_title = self._app_title()
        self.setWindowTitle(f"{title} · {app_title}" if title else f"校园网认证 · {app_title}")

    # ------------------------------------------------------------ 状态表达

    def _set_status(self, state: str, detail: str = "") -> None:
        """用地址栏右侧的小图标 + tooltip 表达状态，不再占用底部一行灰字。"""
        detail = (detail or "").strip()
        if state == "loading":
            self.status_label.setPixmap(icons.pixmap("sync", 16, theme.color("accent")))
            tooltip = "正在加载页面…"
        elif state == "loaded":
            self.status_label.setPixmap(icons.pixmap("globe", 16, theme.color("ok")))
            tooltip = f"页面已加载：{detail}" if detail else "页面已加载"
        elif state == "failed":
            self.status_label.setPixmap(icons.pixmap("warning", 16, theme.color("error")))
            tooltip = f"页面加载失败：{detail}" if detail else "页面加载失败"
        else:
            self.status_label.setPixmap(icons.pixmap("globe", 16, theme.color("text_muted")))
            tooltip = detail or "等待加载页面"
        self.status_label.setToolTip(tooltip)
        self.status_label.setAccessibleName(tooltip)

    def _set_loading(self, loading: bool) -> None:
        self._loading = bool(loading)
        if loading:
            self.progress.setRange(0, 0)
            self.progress.show()
            self.reload_button.setIcon(icons.icon("close", 16, theme.color("text")))
            self.reload_button.setToolTip("停止加载")
            self._set_status("loading")
            return
        self.progress.hide()
        self.reload_button.setIcon(icons.icon("refresh", 16, theme.color("text")))
        self.reload_button.setToolTip("刷新（F5）")

    # ---------------------------------------------------------------- 导航

    def _home_url(self) -> str:
        if self.service is not None:
            config = self.service.get_config()
            custom = (config.get("custom_portal_url") or "").strip()
            if custom:
                return custom
            portal = getattr(self.service.engine, "portal_cache", None) or {}
            url = (portal.get("params").url if portal.get("params") else "") or ""
            if url:
                return url
        return "http://10.100.2.2:9090/"

    def _normalise_url(self, text: str) -> str:
        """把用户输入规整成可导航地址：裸域名补 ``http://``，中文/空格交给 QUrl。

        保持「缺协议时优先 http」的现状，``10.100.2.2`` / ``localhost`` / 纯 IP
        都不该被悄悄升级成 https。
        """
        text = (text or "").strip()
        if not text:
            return self._home_url()
        qurl = QUrl.fromUserInput(text)
        scheme = qurl.scheme().lower() if qurl.isValid() else ""
        if qurl.isValid() and scheme in ("http", "https"):
            return qurl.toString()
        # fromUserInput 失败或猜成 file: 等奇怪协议时，退回原来的朴素拼法。
        fallback = text if "://" in text else "http://" + text
        qurl = QUrl(fallback)
        if qurl.isValid() and qurl.scheme().lower() in ("http", "https"):
            return qurl.toString()
        return fallback

    def open_url(self, url: str) -> None:
        """打开指定地址。"""
        target = self._normalise_url(url)
        self._showing_error = False
        self._ignore_next_load = False
        self._stop_requested = False
        self._failed_url = ""
        self._last_error_code = 0
        self._last_error_text = ""
        self.url_edit.setText(target)
        if self.view is not None:
            self.view.setUrl(QUrl(target))
        else:
            self._set_status("failed", WEBENGINE_ERROR or "缺少 PyQtWebEngine")

    def _portal_url_from_service(self) -> str:
        """尽量从 service 拿「当前 Portal 登录页」地址；没有对应方法就返回空串。"""
        if self.service is None:
            return ""
        for name in ("detect_portal", "detect_portal_url", "find_portal"):
            detect = getattr(self.service, name, None)
            if not callable(detect):
                continue
            try:
                result = detect()
            except Exception:
                continue
            if isinstance(result, str):
                value = result.strip()
            elif isinstance(result, dict):
                value = str(result.get("portal_url") or result.get("url") or "").strip()
            else:
                value = ""
            if value:
                return value
        return ""

    def _open_portal_entry(self) -> None:
        """「认证页」按钮：优先 Portal 登录页，拿不到再退回捕获/配置的地址。

        service 侧目前没有单独的 Portal 检测方法，所以实际走 ``open_portal(None)``
        ——它读的是自定义地址或认证时捕获到的 Portal 参数。
        """
        url = self._portal_url_from_service()
        if url:
            self.open_url(url)
            return
        self.open_portal(None)

    def open_portal(self, status: dict | None = None) -> str:
        """打开当前捕获到的 Portal 地址（没有就用配置 / 默认地址）。"""
        url = (status or {}).get("portal_url", "").strip()
        if not url:
            url = self._home_url()
        self.open_url(url)
        return url

    def _navigate_from_url(self) -> None:
        self.url_edit.selectAll()
        self.open_url(self.url_edit.text().strip())

    def _focus_url(self) -> None:
        self.url_edit.setFocus(Qt.ShortcutFocusReason)
        self.url_edit.selectAll()

    def _reload_page(self) -> None:
        if self.view is None:
            return
        if self._showing_error and self._failed_url:
            self.open_url(self._failed_url)
            return
        self.view.reload()

    def _go_back(self) -> None:
        if self.view is not None:
            self.view.back()

    def _go_forward(self) -> None:
        if self.view is not None:
            self.view.forward()

    def _on_reload_clicked(self) -> None:
        if self.view is None:
            return
        if self._loading:
            self._stop_requested = True
            self.view.stop()
            return
        self._reload_page()

    def _on_url_changed(self, url: QUrl) -> None:
        scheme = url.scheme()
        if scheme == "system":
            # 兜底：页面对象没装成时，伪协议也别让它把当前页导航走。
            self._open_system_url(url.toString())
            return
        if self._showing_error:
            if scheme not in ("http", "https"):
                return
            # 用户在错误页点了重试，或地址栏重新导航：退出错误页保护。
            self._showing_error = False
        if not self.url_edit.hasFocus():
            self.url_edit.setText(url.toString())

    # ------------------------------------------------------------ 加载信号

    def _on_load_started(self) -> None:
        if self._ignore_next_load:
            self._ignore_next_load = False
            return
        if self._showing_error:
            self._showing_error = False
        self._failed_url = ""
        self._stop_requested = False
        self._last_error_code = 0
        self._last_error_text = ""
        self._set_loading(True)

    def _on_load_finished(self, ok: bool) -> None:
        self._ignore_next_load = False
        if self.view is None:
            return

        if self._showing_error:
            # 这条信号来自内嵌错误页自身，保留「加载失败」状态。
            self._set_loading(False)
            self._set_status("failed", self._failed_url or "页面打不开")
            return

        url = self.url_edit.text().strip()
        try:
            current = self.view.url().toString()
            if current and current != "about:blank":
                url = current
        except Exception:
            pass

        if not ok:
            self._certificate_warned = False
            if self._stop_requested:
                self._stop_requested = False
                self._failed_url = ""
                self._set_loading(False)
                self._set_status("idle", "已停止加载")
                return
            self._failed_url = url or self._home_url()
            detail = self._error_detail()
            self._set_loading(False)
            self._set_status("failed", f"{self._failed_url}（{detail}）" if detail else self._failed_url)
            self._update_title("页面打不开")
            self._show_error_page(self._failed_url, detail)
            return

        title = ""
        try:
            title = self.view.title()
        except Exception:
            pass
        self._failed_url = ""
        self._render_recovered = False
        detail = title or url
        if self._certificate_warned:
            detail = f"{detail}（已忽略证书错误）"
            self._certificate_warned = False
        self._set_loading(False)
        self._set_status("loaded", detail)
        self._update_title(title)

    def _error_detail(self) -> str:
        """尽量拿到 Chromium 给的错误码/描述；拿不到就返回空串。"""
        parts: list[str] = []
        if self._last_error_text:
            text = self._last_error_text
            if self._last_error_code:
                text = f"{text}（错误码 {self._last_error_code}）"
            parts.append(text)
        elif self._last_error_code:
            parts.append(f"错误码 {self._last_error_code}")
        page = self._page
        if page is not None:
            for name in ("errorDescription", "errorString"):
                try:
                    getter = getattr(page, name, None)
                    value = str(getter() or "").strip() if callable(getter) else ""
                except Exception:
                    value = ""
                if value and value not in parts:
                    parts.append(value)
                    break
        return " · ".join(parts)

    def _on_certificate_error(self, host: str = "") -> None:
        """证书没通过但已放行：状态图标提示一次，不打断用户操作。"""
        host = (host or "").strip()
        self._certificate_warned = True
        self._certificate_host = host or self._certificate_host
        tooltip = f"已忽略证书错误：{self._certificate_host}" if self._certificate_host else "已忽略证书错误"
        self.status_label.setToolTip(tooltip)
        self.status_label.setAccessibleName(tooltip)
        if self._certificate_notice_shown:
            return
        self._certificate_notice_shown = True
        self._notify(
            "warning",
            "已忽略证书错误",
            f"{self._certificate_host or '该站点'} 的证书校验未通过，已临时放行。",
        )

    def _on_render_terminated(self, _status=None, _exit_code: int = 0) -> None:
        """渲染进程崩溃：只自动重载一次，避免崩溃—重载死循环。"""
        if self.view is None:
            return
        if self._render_recovered:
            self._set_status("failed", "页面渲染进程再次崩溃，请点刷新重试")
            return
        self._render_recovered = True
        self._set_status("failed", "页面渲染进程已崩溃，正在重载")
        QTimer.singleShot(800, self._reload_after_render_crash)

    def _reload_after_render_crash(self) -> None:
        if self.view is None:
            return
        try:
            self.view.reload()
        except Exception:
            pass

    def _open_system_url(self, url: str) -> None:
        """用系统默认浏览器打开地址（错误页 ``system:`` 链接与工具条按钮共用）。"""
        target = (url or "").strip()
        if target.lower().startswith("system:"):
            target = target[len("system:"):].strip()
        if not target:
            target = self._home_url()
        try:
            QDesktopServices.openUrl(QUrl.fromUserInput(target))
        except Exception:
            pass

    def _show_error_page(self, url: str, detail: str = "") -> None:
        """把「页面打不开」做成网页本身：居中大字 + 说明 + 重试/系统浏览器按钮。"""
        if self.view is None:
            return
        self._showing_error = True
        self._ignore_next_load = True
        self.view.setHtml(self._error_html(url, detail), QUrl("about:blank"))

    def _error_html(self, url: str, detail: str = "") -> str:
        bg = theme.color("card")
        fg = theme.color("text")
        secondary = theme.color("text_secondary")
        accent = theme.color("accent")
        safe_url = html.escape(url or "")
        action = html.escape(url or "about:blank", quote=True)
        detail_block = (
            f'<p class="detail">错误信息：{html.escape(detail)}</p>' if detail else ""
        )
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>页面打不开</title>
<style>
    html, body {{ height: 100%; margin: 0; background: {bg}; }}
    body {{
        display: flex; align-items: center; justify-content: center;
        font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
        color: {fg};
    }}
    .card {{ max-width: 640px; padding: 40px 32px; text-align: center; }}
    h1 {{ margin: 0 0 14px; font-size: 30px; font-weight: 600; }}
    p {{ margin: 6px 0; font-size: 14px; line-height: 1.8; color: {secondary}; word-break: break-all; }}
    .detail {{ color: {theme.color("error")}; }}
    code {{ font-size: 13px; color: {accent}; }}
    .actions {{ margin-top: 22px; display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }}
    .retry, .external {{
        display: inline-block; padding: 9px 24px; border-radius: 6px;
        font-size: 14px; text-decoration: none;
    }}
    .retry {{ background: {accent}; color: #FFFFFF; }}
    .external {{ color: {accent}; border: 1px solid {accent}; }}
    .retry:hover, .external:hover {{ opacity: 0.86; }}
</style>
</head>
<body>
<div class="card">
    <h1>页面打不开</h1>
    <p>请检查校园网连接是否正常，或确认下面的地址是否正确。</p>
    <p><code>{safe_url}</code></p>
    {detail_block}
    <div class="actions">
        <a class="retry" href="{action}">重新加载</a>
        <a class="external" href="system:{action}">用系统浏览器打开</a>
    </div>
</div>
</body>
</html>"""

    # ------------------------------------------------------------ 自动填表

    def _notify(self, kind: str, title: str, content: str = "") -> None:
        """用户主动操作的反馈走窗口内 InfoBar；没有 Toast 时静默降级。"""
        try:
            from .toasts import Toast

            Toast.show_toast(self, title, content, kind)
        except Exception:
            pass

    def _fill_and_submit(self) -> None:
        """把账号密码填进门户表单并点登录（页面结构差异大，做尽力而为）。"""
        if self.view is None or self.service is None:
            return
        config = self.service.get_config()
        username = str(config.get("username") or "")
        password = str(self.service.settings.password or "")
        if not username:
            self._notify("warning", "无法自动填表", "还没保存账号，请先到「设置 → 认证账户」里填写。")
            self.status_label.setToolTip("无法自动填表：还没保存账号")
            return
        script = f"""
        (function() {{
            function pick(list, types) {{
                for (var i = 0; i < list.length; i++) {{
                    var el = list[i];
                    var key = ((el.name || '') + (el.id || '')).toLowerCase();
                    for (var j = 0; j < types.length; j++) {{
                        if (key.indexOf(types[j]) >= 0) return el;
                    }}
                }}
                return null;
            }}
            var inputs = Array.prototype.slice.call(document.querySelectorAll('input'));
            var visible = inputs.filter(function(e) {{
                var t = (e.type || 'text').toLowerCase();
                return t !== 'hidden' && t !== 'submit' && t !== 'button' && t !== 'checkbox';
            }});
            var user = pick(visible, ['username1', 'username', 'user', 'name', 'account']) || visible[0];
            var pwd  = pick(visible, ['userpasswd', 'password', 'passwd', 'pwd']) || visible[1];
            if (user) {{ user.value = {username!r}; user.dispatchEvent(new Event('input', {{bubbles:true}})); }}
            if (pwd)  {{ pwd.value  = {password!r}; pwd.dispatchEvent(new Event('input', {{bubbles:true}})); }}
            var buttons = Array.prototype.slice.call(document.querySelectorAll('button, input[type=button], input[type=submit], a'));
            for (var k = 0; k < buttons.length; k++) {{
                var text = (buttons[k].value || buttons[k].textContent || '').trim();
                if (text.indexOf('登') >= 0 && text.indexOf('录') >= 0) {{
                    buttons[k].click();
                    return 'clicked:' + text;
                }}
            }}
            return 'filled';
        }})();
        """
        self.view.page().runJavaScript(script, self._on_script_done)

    def _on_script_done(self, result) -> None:
        text = str(result)
        if text.startswith("clicked"):
            self._notify("success", "已自动填写并提交", "若页面没反应，请手动点页面上的登录按钮。")
            self.status_label.setToolTip(f"已自动填写并提交（{text}）")
        else:
            self._notify("info", "已填写账号密码", "请手动确认后点击页面上的登录按钮。")
            self.status_label.setToolTip("已填写账号密码，等待手动登录")

    def _open_external(self) -> None:
        """工具条按钮：把当前地址交给系统默认浏览器。"""
        url = ""
        if self.view is not None:
            try:
                url = self.view.url().toString()
            except Exception:
                url = ""
        if not url or url == "about:blank":
            url = self._failed_url or self.url_edit.text().strip() or self._home_url()
        self._open_system_url(url)

    def closeEvent(self, event) -> None:
        self._save_geometry()
        event.ignore()
        self.hide()


global _singleton
_singleton: BrowserWindow | None = None


def open_browser(
    service=None,
    status: dict | None = None,
    parent: QWidget | None = None,
    url: str = "",
) -> tuple[BrowserWindow | None, str]:
    """打开（或复用）内置浏览器。

    默认加载 Portal 认证页；传 ``url`` 则加载指定地址（例如学校自助系统）。
    返回 (窗口, 实际加载的地址)。
    """
    if not WEBENGINE_AVAILABLE:
        return None, WEBENGINE_ERROR
    global _singleton
    if _singleton is None:
        _singleton = BrowserWindow(service, parent)
    if url:
        _singleton.open_url(url)
        loaded = url
    else:
        loaded = _singleton.open_portal(status)
    _singleton.show()
    _singleton.raise_()
    _singleton.activateWindow()
    return _singleton, loaded
