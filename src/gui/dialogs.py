"""通用对话框：需要用户**手动输入确认文字**才会执行的破坏性操作。

用于「申请暂停」这类一旦执行就会断网、且可能影响学校侧状态的动作：
光点一下"确定"太容易误触，所以要求用户照样输入确认词。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
)


class TypedConfirmDialog(MessageBoxBase):
    """警告 + 要求输入确认文字的对话框。

    输入内容与 ``expect`` 完全一致前，「确认」按钮保持禁用。
    """

    def __init__(self, parent: QWidget | None, title: str, message: str, expect: str, detail: str = '',
                 confirm_text: str = '确认执行', danger: bool = True) -> None:
        super().__init__(parent)
        self.expect = expect
        self.titleLabel = SubtitleLabel(title, self)
        self.viewLayout.addWidget(self.titleLabel)

        body = BodyLabel(message, self)
        body.setWordWrap(True)
        self.viewLayout.addWidget(body)

        if detail:
            note = CaptionLabel(detail, self)
            note.setWordWrap(True)
            self.viewLayout.addWidget(note)

        self.viewLayout.addWidget(CaptionLabel(f'如果确定，请输入「{expect}」：', self))
        self.edit = LineEdit(self)
        self.edit.setPlaceholderText(expect)
        self.edit.setClearButtonEnabled(True)
        self.viewLayout.addWidget(self.edit)
        self.edit.textChanged.connect(self._check)

        self.yesButton.setText(confirm_text)
        self.cancelButton.setText('取消')
        if danger:
            from qfluentwidgets import setCustomStyleSheet

            setCustomStyleSheet(
                self.yesButton,
                'PushButton{background-color:#c42b1c;color:#ffffff;border:1px solid #c42b1c;}PushButton:hover{background-color:#d13438;color:#ffffff;border:1px solid #d13438;}PushButton:pressed{background-color:#a02316;color:#ffffff;border:1px solid #a02316;}PushButton:disabled{background-color:rgba(196,43,28,0.35);color:rgba(255,255,255,0.65);border:1px solid transparent;}',
                'PushButton{background-color:#c42b1c;color:#ffffff;border:1px solid #c42b1c;}PushButton:hover{background-color:#d13438;color:#ffffff;border:1px solid #d13438;}PushButton:pressed{background-color:#a02316;color:#ffffff;border:1px solid #a02316;}PushButton:disabled{background-color:rgba(196,43,28,0.35);color:rgba(255,255,255,0.65);border:1px solid transparent;}',
            )
        self.yesButton.setEnabled(False)
        self.widget.setMinimumWidth(420)

    def _check(self, text: str) -> None:
        self.yesButton.setEnabled(text.strip() == self.expect)

    def validate(self) -> bool:
        return self.edit.text().strip() == self.expect


def confirm_typed(parent: QWidget | None, title: str, message: str, expect: str, detail: str = '',
                  confirm_text: str = '确认执行') -> bool:
    """弹出"输入确认文字"对话框，返回用户是否确认。"""
    dialog = TypedConfirmDialog(parent, title, message, expect, detail, confirm_text)
    if dialog.exec():
        return dialog.validate()
    return False
