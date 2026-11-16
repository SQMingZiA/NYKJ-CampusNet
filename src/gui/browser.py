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

import os
import sys

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.deps import resource_path

from . import icons, theme
from .widgets import body_label

WEBENGINE_AVAILABLE = False
WEBENGINE_ERROR = ""
try:
    from PyQt5.QtWebEngineWidgets import (
        QWebEngineProfile,
        QWebEngineSettings,
        QWebEngineView,
    )

    WEBENGINE_AVAILABLE = True
except Exception as exc:
    QWebEngineView = None
    QWebEngineProfile = None
    QWebEngineSettings = None
    WEBENGINE_ERROR = f"{type(exc).__name__}: {exc}"


def _configure_profile() -> None:
    """配置默认 Profile：固定 UA、持久化 Cookie 与缓存。

    必须在 QApplication 创建之后调用（见模块头注释第 3 条）。
    """
    if not WEBENGINE_AVAILABLE:
        return

    try:
        from PyQt5.QtWidgets import QApplication

        if QApplication.instance() is None:
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
        self.setWindowTitle("校园网认证 · 内置浏览器")
        self.setWindowFlags(Qt.Window)
        self.resize(1000, 740)
        icon_path = resource_path("app.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        _configure_profile()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD)
        root.setSpacing(theme.SPACE_SM)

        bar = QHBoxLayout()
        bar.setSpacing(theme.SPACE_XS)

        def tool_button(icon_name: str, tooltip: str):
            """IconButton"""
            button = QPushButton()
            button.setObjectName("IconButton")
            button.setIcon(icons.icon(icon_name, 16, theme.color("text")))
            button.setToolTip(tooltip)
            button.setFixedSize(32, 32)
            return button

        self.back_button = tool_button("back", "后退")
        self.forward_button = tool_button("forward", "前进")
        self.reload_button = tool_button("refresh", "刷新")
        self.home_button = tool_button("home", "回到认证页")
        self.open_external_button = tool_button("link", "用系统浏览器打开")
        for button in (self.back_button, self.forward_button, self.reload_button, self.home_button):
            bar.addWidget(button)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("输入 Portal 地址，例如 http://10.100.2.2:9090/")
        self.url_edit.setClearButtonEnabled(True)
        bar.addWidget(self.url_edit, 1)

        self.auto_login_button = QPushButton("在本页自动填表登录")
        self.auto_login_button.setIcon(icons.icon("shield", 15, theme.color("text")))
        bar.addWidget(self.auto_login_button)

        bar.addWidget(self.open_external_button)
        self.close_button = QPushButton("关闭")
        self.close_button.setIcon(icons.icon("close", 15, theme.color("text")))
        bar.addWidget(self.close_button)
        root.addLayout(bar)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        root.addWidget(self.progress)

        if WEBENGINE_AVAILABLE:
            self.view = QWebEngineView(self)
            self.view.setMinimumHeight(400)
            settings = self.view.settings()
            settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
            settings.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, True)
            settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)
            root.addWidget(self.view, 1)
            self._wire_webengine()
        else:
            self.view = None
            fallback = body_label(
                f"内置浏览器不可用（缺少 PyQtWebEngine）。\n\n原因：{WEBENGINE_ERROR}"
                "\n\n可以执行下面命令安装后重试：\n    pip install PyQtWebEngine\n\n或者点工具条最右侧的「用系统浏览器打开」临时顶上。"
            )
            fallback.setAlignment(Qt.AlignCenter)
            root.addWidget(fallback, 1)

        self.hint = body_label(
            "提示：在这个窗口里手动登录后，回到主界面点「检测并登录」即可；Cookie 会保留，下次不用重复输入。",
            muted=True,
        )
        self.hint.setStyleSheet(f"font-size: 12px; color: {theme.color('text_secondary')};")
        root.addWidget(self.hint)

    def _wire_webengine(self) -> None:
        self.view.urlChanged.connect(self._on_url_changed)
        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadFinished.connect(self._on_load_finished)

        self.back_button.clicked.connect(self.view.back)
        self.forward_button.clicked.connect(self.view.forward)
        self.reload_button.clicked.connect(self.view.reload)
        self.home_button.clicked.connect(lambda: self.open_url(self._home_url()))
        self.url_edit.returnPressed.connect(lambda: self.open_url(self.url_edit.text().strip()))
        self.close_button.clicked.connect(self.close)
        self.open_external_button.clicked.connect(self._open_external)
        self.auto_login_button.clicked.connect(self._fill_and_submit)

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

    def open_url(self, url: str) -> None:
        """打开指定地址。"""
        if not url:
            url = self._home_url()
        if "://" not in url:
            url = "http://" + url
        self.url_edit.setText(url)
        if self.view is not None:
            self.view.setUrl(QUrl(url))

    def open_portal(self, status: dict | None = None) -> str:
        """打开当前捕获到的 Portal 地址（没有就用配置 / 默认地址）。"""
        url = (status or {}).get("portal_url", "").strip()
        if not url:
            url = self._home_url()
        self.open_url(url)
        return url

    def _on_url_changed(self, url: QUrl) -> None:
        if not self.url_edit.hasFocus():
            self.url_edit.setText(url.toString())

    def _on_load_started(self) -> None:
        """正在加载认证页面…"""
        self.progress.show()
        self.hint.setText("正在加载认证页面…")

    def _on_load_finished(self, ok: bool) -> None:
        """页面加载失败，请检查校园网连接或地址是否正确。"""
        self.progress.hide()
        if not ok:
            self.hint.setText("页面加载失败，请检查校园网连接或地址是否正确。")
            return
        title = ""
        try:
            title = self.view.title()
        except Exception:
            pass
        self.hint.setText(f"已加载：{title or self.url_edit.text()}\u3000|\u3000在这里手动登录后，回主界面点「检测并登录」")
        self.setWindowTitle(f"校园网认证 · {title}" if title else "校园网认证 · 内置浏览器")

    def _fill_and_submit(self) -> None:
        """把账号密码填进门户表单并点登录（页面结构差异大，做尽力而为）。"""
        if self.view is None or self.service is None:
            return
        config = self.service.get_config()
        username = str(config.get("username") or "")
        password = str(self.service.settings.password or "")
        if not username:
            self.hint.setText("还没保存账号，请先到「设置 → 认证账户」里填写。")
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
        """clicked"""
        text = str(result)
        if text.startswith("clicked"):
            self.hint.setText(f"已自动填写并提交（{text}）。若没反应，请手动点页面上的登录按钮。")
        else:
            self.hint.setText("已把账号密码填进页面，请手动确认后点击登录按钮。")

    def _open_external(self) -> None:
        from ..core.winutil import open_path

        open_path(self.url_edit.text().strip() or self._home_url())

    def closeEvent(self, event) -> None:
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
