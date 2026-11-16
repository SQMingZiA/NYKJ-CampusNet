"""图标：统一走 FluentUI 自带的 ``FluentIcon`` 图标字体。

对外只暴露三个东西（旧代码与 ``main.py --selftest`` 都依赖它们）：

* ``icon(name, size, color)`` → ``QIcon``
* ``pixmap(name, size, color)`` → ``QPixmap``
* ``NAMES`` → 支持的名字列表

这样界面里不用再手绘线性图标，图标风格与 FluentUI 完全一致。
"""

from __future__ import annotations

from PyQt5.QtGui import QColor, QIcon, QPixmap
from qfluentwidgets import FluentIcon as FIF

_MAP: dict[str, object] = {
    "logo": FIF.APPLICATION,
    "home": FIF.HOME,
    "tools": FIF.DEVELOPER_TOOLS,
    "settings": FIF.SETTING,
    "wifi": FIF.WIFI,
    "globe": FIF.GLOBE,
    "vpn": FIF.VPN,
    "connect": FIF.CONNECT,
    "power": FIF.POWER_BUTTON,
    "sync": FIF.SYNC,
    "refresh": FIF.SYNC,
    "update": FIF.UPDATE,
    "back": FIF.LEFT_ARROW,
    "forward": FIF.RIGHT_ARROW,
    "link": FIF.LINK,
    "share": FIF.SHARE,
    "close": FIF.CLOSE,
    "download": FIF.DOWNLOAD,
    "upload": FIF.UP,
    "history": FIF.HISTORY,
    "speed": FIF.SPEED_HIGH,
    "speed_off": FIF.SPEED_OFF,
    "chart": FIF.CHAT,
    "doc": FIF.DOCUMENT,
    "folder": FIF.FOLDER,
    "save": FIF.SAVE,
    "copy": FIF.COPY,
    "edit": FIF.EDIT,
    "delete": FIF.DELETE,
    "broom": FIF.BROOM,
    "search": FIF.SEARCH,
    "filter": FIF.FILTER,
    "info": FIF.INFO,
    "warning": FIF.INFO,
    "shield": FIF.CERTIFICATE,
    "certificate": FIF.CERTIFICATE,
    "cloud": FIF.CLOUD,
    "calendar": FIF.CALENDAR,
    "view": FIF.VIEW,
    "zoom": FIF.ZOOM,
    "lab": FIF.ROBOT,
    "robot": FIF.ROBOT,
    "palette": FIF.PALETTE,
    "tag": FIF.TAG,
    "pin": FIF.PIN,
    "heart": FIF.HEART,
    "ring": FIF.SYNC,
}

NAMES: list[str] = sorted(_MAP)


def _resolve(name: str):
    return _MAP.get(name) or FIF.GLOBE


def icon(name: str, size: int = 16, color: str | None = None) -> QIcon:
    """取一个 QIcon。``color`` 为空时跟随主题（推荐）。"""
    fluent = _resolve(name)
    try:
        if color:
            return fluent.icon(color=QColor(color))
        return fluent.icon()
    except Exception:
        return QIcon()


def pixmap(name: str, size: int = 16, color: str | None = None) -> QPixmap:
    """取一个 QPixmap（自检用）。"""
    return icon(name, size, color).pixmap(size, size)
