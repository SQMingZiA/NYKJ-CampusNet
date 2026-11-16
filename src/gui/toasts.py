"""提示条：基于 FluentUI 的 ``InfoBar``。

保留旧的 ``Toast.xxx(parent, title, content, duration)`` 调用方式，
这样页面与 ``app.py`` 都不用关心 InfoBar 的细节。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget
from qfluentwidgets import InfoBar, InfoBarPosition

_KINDS = {
    "success": InfoBar.success,
    "ok": InfoBar.success,
    "info": InfoBar.info,
    "warning": InfoBar.warning,
    "warn": InfoBar.warning,
    "error": InfoBar.error,
}


class Toast:
    """右下角（这里用右上角）提示条。"""

    @staticmethod
    def show_toast(parent: QWidget | None, title: str, content: str = "", kind: str = "info", duration: int = 4000) -> None:
        ""
        factory = _KINDS.get(str(kind).lower(), InfoBar.info)

        target = parent
        if isinstance(parent, QWidget):
            target = parent.window()
        try:
            factory(
                title=title,
                content=content or "",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=max(1000, int(duration)),
                parent=target,
            )
        except Exception:
            return

    @staticmethod
    def success(parent, title: str, content: str = "", duration: int = 3500) -> None:
        """success"""
        Toast.show_toast(parent, title, content, "success", duration)

    @staticmethod
    def info(parent, title: str, content: str = "", duration: int = 3500) -> None:
        """info"""
        Toast.show_toast(parent, title, content, "info", duration)

    @staticmethod
    def warning(parent, title: str, content: str = "", duration: int = 5000) -> None:
        """warning"""
        Toast.show_toast(parent, title, content, "warning", duration)

    @staticmethod
    def error(parent, title: str, content: str = "", duration: int = 6000) -> None:
        """error"""
        Toast.show_toast(parent, title, content, "error", duration)
