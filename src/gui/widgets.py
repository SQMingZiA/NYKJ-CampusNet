"""FluentUI 共享控件。

这些控件都建立在 ``qfluentwidgets`` 原生组件之上（CardWidget / SettingCard /
SwitchButton / ComboBox / SpinBox ...），所以外观与 Fluent 2 完全一致，
同时把「图标 + 标题 + 说明 … 右侧控件」这种设置行封装成了可复用的小类。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    LineEdit,
    PasswordLineEdit,
    PushButton,
    SettingCard,
    SmoothScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
)

from . import theme


def body_label(text: str = "", muted: bool = False, wrap: bool = True, parent: QWidget | None = None) -> BodyLabel:
    """正文字号标签；``muted`` 用次要色。"""
    label = BodyLabel(text, parent)
    if muted:
        label.setTextColor(theme.color("text_secondary"), theme.color("text_secondary"))
    if wrap:
        label.setWordWrap(True)
    return label


def caption_label(text: str = "", parent: QWidget | None = None) -> CaptionLabel:
    label = CaptionLabel(text, parent)
    label.setTextColor(theme.color("text_muted"), theme.color("text_muted"))
    label.setWordWrap(True)
    return label


def divider(parent: QWidget | None = None) -> QFrame:
    """1px 分隔线（Fluent 风格用极淡的分隔线而不是投影）。"""
    line = QFrame(parent)
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {theme.color('card_border')}; border: none;")
    return line


def scroll_page(parent: QWidget | None = None, margins: tuple[int, int, int, int] = (28, 24, 28, 28), spacing: int = 16) -> tuple[SmoothScrollArea, QVBoxLayout]:
    """创建一个「背景跟随主题」的滚动页，返回 (滚动区, 内容布局)。

    ⚠ 必须把**视口**设成透明：``QAbstractScrollArea`` 的视口默认会用调色板的
    窗口色（浅色 #f0f0f0）填充整个可视区，深色主题下就会在卡片后面露出
    一大片浅色背景 —— 实测（``recon/fix_scroll_dark.py``）只有设置视口
    透明这一种写法有效，只给 ``SmoothScrollArea`` 自己设样式是没用的。
    """
    area = SmoothScrollArea(parent)
    area.setWidgetResizable(True)
    area.setFrameShape(SmoothScrollArea.NoFrame)
    area.viewport().setStyleSheet("background: transparent;")

    container = QWidget()
    area.setWidget(container)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return area, layout









class Card(CardWidget):
    """带标题的卡片。内容加到 ``card.body`` 里。"""

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        self._bg_light = None
        self._bg_dark = None
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(20, 16, 20, 20)
        self.vbox.setSpacing(12)

        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            self.title_label = StrongBodyLabel(title, self)
            head.addWidget(self.title_label)
            if subtitle:
                self.subtitle_label = CaptionLabel(subtitle, self)
                self.subtitle_label.setTextColor(theme.color("text_muted"), theme.color("text_muted"))
                head.addWidget(self.subtitle_label)
            head.addStretch(1)
            self.header = head
            self.vbox.addLayout(head)
            self.vbox.addWidget(divider(self))

        self.body = self.vbox

    def set_background(self, light: str, dark: str = "") -> None:
        """给卡片换一个底色（例如公告卡的淡黄色）。

        为什么要在卡片**上面再画一层**而不是用样式表：``CardWidget`` 是在
        ``paintEvent`` 里自己重绘背景的，外部 ``setStyleSheet`` 改不动它的填充色。
        这里让卡片先正常画好（圆角、边框都在），再叠一层半透明底色，
        所以外观完全还是"和其它卡片一样"，只是颜色变了。
        """
        from PyQt5.QtGui import QColor

        self._bg_light = QColor(light) if light else None
        self._bg_dark = QColor(dark or light) if dark or light else None
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        base = self._bg_dark if theme.is_dark() else self._bg_light
        if base is None:
            return
        from PyQt5.QtCore import Qt as _Qt
        from PyQt5.QtGui import QColor as _QColor
        from PyQt5.QtGui import QPainter

        color = _QColor(base)
        color.setAlpha(235)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(_Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 7, 7)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        """往卡片主体里加一行布局。"""
        self.body.addLayout(layout)



class StatTile(CardWidget):
    """一个小的指标块：标题 + 大数字 + 备注。

    备注行**始终占位**（没有内容时显示 ``—``），这样一排指标块高度一致 ——
    否则有的块两行、有的块三行，看上去参差不齐（用户反馈过这个问题）。
    """

    def __init__(self, caption: str, value: str = "-", hint: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 14)
        box.setSpacing(4)

        self.caption_label = CaptionLabel(caption, self)
        self.caption_label.setTextColor(theme.color("text_muted"), theme.color("text_muted"))
        box.addWidget(self.caption_label)

        self.value_label = SubtitleLabel(value, self)
        box.addWidget(self.value_label)

        self.hint_label = CaptionLabel(hint or "—", self)
        self.hint_label.setTextColor(theme.color("text_secondary"), theme.color("text_secondary"))
        self.hint_label.setWordWrap(True)
        box.addWidget(self.hint_label)

    def set_value(self, value: str, hint: str | None = None) -> None:
        self.value_label.setText(value)
        if hint is not None:
            self.hint_label.setText(hint or "—")

    def set_caption(self, caption: str) -> None:
        self.caption_label.setText(caption)

    def set_color(self, color: str) -> None:
        self.value_label.setTextColor(color, color)

    def reset_color(self) -> None:
        table = theme.palette()
        self.value_label.setTextColor(table["text"], table["text"])


class KVGrid(QWidget):
    """若干「标签 → 值」行，值可随时刷新。"""

    def __init__(self, keys: list[tuple[str, str]], columns: int = 2, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)

        for index, (key, text) in enumerate(keys):
            row, col = divmod(index, max(1, columns))
            cell = QWidget(self)
            line = QHBoxLayout(cell)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            name = CaptionLabel(f"{text}", cell)
            name.setTextColor(theme.color("text_muted"), theme.color("text_muted"))
            name.setMinimumWidth(72)
            value = BodyLabel("-", cell)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            line.addWidget(name)
            line.addWidget(value)
            line.addStretch(1)
            grid.addWidget(cell, row, col)
            self._values[key] = value

        for col in range(max(1, columns)):
            grid.setColumnStretch(col, 1)

    def set(self, key: str, value: str) -> None:
        label = self._values.get(key)
        if label is not None:
            label.setText(value)

    def update_values(self, mapping: dict[str, str]) -> None:
        for key, value in mapping.items():
            self.set(key, value)





class SwitchRow(SettingCard):
    """开关设置行。"""

    changed = pyqtSignal(bool)

    def __init__(self, icon, title: str, content: str = "", checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(icon, title, content, parent)
        self.switch = SwitchButton(self)
        self.switch.setOnText("开")
        self.switch.setOffText("关")
        self.switch.setChecked(bool(checked))
        self.switch.checkedChanged.connect(self.changed.emit)
        self.hBoxLayout.addWidget(self.switch, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def set_checked(self, value: bool) -> None:
        self.switch.setChecked(bool(value))

    def is_checked(self) -> bool:
        return self.switch.isChecked()


class ComboRow(SettingCard):
    """下拉选择设置行。``options`` 为 [(显示文本, 值), ...]。"""

    changed = pyqtSignal(str)

    def __init__(self, icon, title: str, content: str, options: list[tuple[str, str]], current: str = "", parent: QWidget | None = None) -> None:
        super().__init__(icon, title, content, parent)
        self.combo = ComboBox(self)
        self.combo.setMinimumWidth(150)
        self._values = []
        for text, value in options:
            self.combo.addItem(text)
            self._values.append(value)
        if current in self._values:
            self.combo.setCurrentIndex(self._values.index(current))
        self.combo.currentIndexChanged.connect(self._emit)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _emit(self, index: int) -> None:
        if 0 <= index < len(self._values):
            self.changed.emit(self._values[index])

    def value(self) -> str:
        index = self.combo.currentIndex()
        return self._values[index] if 0 <= index < len(self._values) else ""

    def set_value(self, value: str) -> None:
        if value in self._values:
            self.combo.setCurrentIndex(self._values.index(value))



class SpinRow(SettingCard):
    """数字输入设置行。"""

    changed = pyqtSignal(int)

    def __init__(self, icon, title: str, content: str, value: int = 0, minimum: int = 0, maximum: int = 100000, suffix: str = "", parent: QWidget | None = None) -> None:
        super().__init__(icon, title, content, parent)
        self.spin = SpinBox(self)
        self.spin.setRange(minimum, maximum)
        self.spin.setValue(int(value))
        if suffix:
            self.spin.setSuffix(f" {suffix}")
        self.spin.setMinimumWidth(140)
        self.spin.valueChanged.connect(self.changed.emit)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def value(self) -> int:
        return int(self.spin.value())

    def set_value(self, value: int) -> None:
        self.spin.setValue(int(value))



class DoubleSpinRow(SettingCard):
    """浮点数字设置行（套餐流量用）。"""

    changed = pyqtSignal(float)

    def __init__(self, icon, title: str, content: str, value: float = 0.0, minimum: float = 0.0, maximum: float = 100000.0, suffix: str = "", parent: QWidget | None = None) -> None:
        super().__init__(icon, title, content, parent)
        self.spin = SpinBox(self)
        self.spin.setRange(int(minimum), int(maximum))
        self.spin.setValue(int(value))
        if suffix:
            self.spin.setSuffix(f" {suffix}")
        self.spin.setMinimumWidth(140)
        self.spin.valueChanged.connect(lambda v: self.changed.emit(float(v)))
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def value(self) -> float:
        return float(self.spin.value())

    def set_value(self, value: float) -> None:
        self.spin.setValue(int(value))



class TextRow(SettingCard):
    """文本输入设置行（可当密码框）。"""

    submitted = pyqtSignal(str)

    def __init__(self, icon, title: str, content: str, value: str = "", placeholder: str = "", password: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(icon, title, content, parent)
        self.edit = PasswordLineEdit(self) if password else LineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setText(value)
        self.edit.setMinimumWidth(230)
        self.edit.setClearButtonEnabled(True)
        self._password = password
        if password:
            self.edit.setPlaceholderText(placeholder or ("已保存（留空则不修改）" if value else "请输入密码"))
        self.edit.returnPressed.connect(lambda: self.submitted.emit(self.edit.text()))
        self.hBoxLayout.addWidget(self.edit, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def text(self) -> str:
        return self.edit.text()

    def set_text(self, value: str) -> None:
        self.edit.setText(value)




class ButtonRow(SettingCard):
    """右侧带按钮的设置行。"""

    clicked = pyqtSignal()

    def __init__(self, icon, title: str, content: str, button_text: str, primary: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(icon, title, content, parent)
        self.button = PushButton(button_text, self)
        self.button.clicked.connect(self.clicked.emit)
        self.button.setMinimumWidth(110)
        self.hBoxLayout.addWidget(self.button, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def set_text(self, text: str) -> None:
        self.button.setText(text)

    def set_enabled(self, value: bool) -> None:
        self.button.setEnabled(bool(value))
