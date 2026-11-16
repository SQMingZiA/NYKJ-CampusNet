"""主题适配层：把应用的 auto/light/dark 设置接到 **FluentUI**（qfluentwidgets）上。

本版本界面整体交给 ``qfluentwidgets`` 渲染（Fluent 2 / WinUI 风格），
这个模块只负责三件事：

1. 把 ``settings.theme``（auto/light/dark）翻译成 qfluentwidgets 的 ``Theme``；
2. 给 pyqtgraph 曲线、内置浏览器这些「不吃 Fluent 样式表」的地方提供颜色；
3. 保留 ``color()`` / ``is_dark()`` / ``SPACE_*`` 这些旧接口，
   让 ``charts.py``、``browser.py`` 不用改就能跟着主题走。
"""

from __future__ import annotations

from qfluentwidgets import Theme, isDarkTheme, setTheme, setThemeColor

ACCENT = "#0F6CBD"


SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 16
SPACE_LG = 24
SPACE_XL = 32

WINDOW_DEFAULT = (1180, 800)
WINDOW_MIN = (960, 660)

_LIGHT: dict[str, str] = {
    "text": "#1B1B1B",
    "text_secondary": "#5C5C5C",
    "text_muted": "#8A8A8A",
    "card": "#FFFFFF",
    "card_alt": "#FAFAFA",
    "card_border": "#E5E5E5",
    "canvas": "#F3F3F3",
    "accent": ACCENT,
    "accent_soft": "#E8F1FB",
    "ok": "#0F7B0F",
    "warn": "#9D5D00",
    "error": "#C42B1C",
    "close_hover": "#C42B1C",
    "grid": "#D6D6D6",
    "chart_rx": "#2F6FED",
    "chart_tx": "#E0A93C",
}

_DARK: dict[str, str] = {
    "text": "#FFFFFF",
    "text_secondary": "#C8C8C8",
    "text_muted": "#9A9A9A",
    "card": "#2B2B2B",
    "card_alt": "#323232",
    "card_border": "#3D3D3D",
    "canvas": "#202020",
    "accent": "#4CC2FF",
    "accent_soft": "#1F3A4D",
    "ok": "#6CCB5F",
    "warn": "#FCE100",
    "error": "#FF99A4",
    "close_hover": "#C42B1C",
    "grid": "#4A4A4A",
    "chart_rx": "#5CA8FF",
    "chart_tx": "#F0C05A",
}

_PALETTES = {"light": _LIGHT, "dark": _DARK}


def is_dark() -> bool:
    """当前是否深色主题（以 qfluentwidgets 实际生效的主题为准）。"""
    try:
        return bool(isDarkTheme())
    except Exception:
        return False


def palette() -> dict[str, str]:
    if is_dark():
        return _DARK
    return _LIGHT


def color(name: str) -> str:
    """取一个语义色，例如 ``color("text_secondary")``。"""
    table = palette()
    return table.get(name, table["text"])


def chart_colors() -> dict[str, str]:
    """rx"""
    table = palette()
    return {
        "rx": table["chart_rx"],
        "tx": table["chart_tx"],
        "grid": table["grid"],
        "text": table["text_secondary"],
    }


def theme_name() -> str:
    """当前实际生效的是 light 还是 dark。"""
    if is_dark():
        return "dark"
    return "light"


def apply_theme(name: str, app=None) -> None:
    """应用主题。``name`` 取 ``auto`` / ``light`` / ``dark``。"""
    mapping = {
        "light": Theme.LIGHT,
        "dark": Theme.DARK,
        "auto": Theme.AUTO,
    }
    theme = mapping.get(str(name or "auto").lower(), Theme.AUTO)
    try:
        setTheme(theme)
        setThemeColor(ACCENT)
    except Exception:
        return
