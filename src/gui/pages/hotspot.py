"""热点共享页面：开关热点、改名称密码、查看并断开已连接设备。"""

from __future__ import annotations

import time
from typing import Any

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon as FIF,
    IconWidget,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    StrongBodyLabel,
    TableWidget,
    TransparentToolButton,
)

from .. import theme
from ..async_task import run_async
from ..toasts import Toast
from ..widgets import Card, StatTile, scroll_page


class HotspotPage(QWidget):
    """侧边栏「热点共享」：像路由器后台一样管理本机热点。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('HotspotPage')
        self.service = service
        self._status: dict[str, Any] = {}
        self._max_clients = 8
        self._devices: list[dict[str, Any]] = []
        self._busy = False
        self._defaults = service.hotspot_defaults()
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = scroll_page(self)
        outer.addWidget(scroll)

        self._build_power_card(layout)
        self._build_devices_card(layout)
        self._build_about_card(layout)
        layout.addStretch(1)

    def _build_power_card(self, layout: QVBoxLayout) -> None:
        self.power_card = Card(parent=self)
        hero = QHBoxLayout()
        hero.setContentsMargins(0, 0, 0, 0)
        hero.setSpacing(14)

        self.hero_icon = IconWidget(FIF.SHARE.icon(color=theme.color('accent')), self.power_card)
        self.hero_icon.setFixedSize(40, 40)
        hero.addWidget(self.hero_icon, 0, Qt.AlignVCenter)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        self.state_label = StrongBodyLabel('未开启', self.power_card)
        self.state_label.setStyleSheet('font-size: 18px; font-weight: 600;')
        self.state_hint = CaptionLabel('点右侧按钮把本机 Wi-Fi 共享给其它设备', self.power_card)
        self.state_hint.setTextColor(theme.color('text_secondary'), theme.color('text_secondary'))
        titles.addWidget(self.state_label)
        titles.addWidget(self.state_hint)
        hero.addLayout(titles, 1)

        self.toggle_button = PrimaryPushButton('启动热点', self.power_card)
        self.toggle_button.clicked.connect(self._toggle)
        hero.addWidget(self.toggle_button, 0, Qt.AlignVCenter)
        self.power_card.add_layout(hero)

        stats = QHBoxLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(12)
        self.clients_tile = StatTile('已连接设备', '0', '最多 8 台', self.power_card)
        self.backend_tile = StatTile('连接方式', '-', '系统移动热点', self.power_card)
        self.band_tile = StatTile('频段', '-', '2.4 / 5 GHz 由系统决定', self.power_card)
        for tile in (self.clients_tile, self.backend_tile, self.band_tile):
            stats.addWidget(tile, 1)
        self.power_card.add_layout(stats)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)

        form.addWidget(BodyLabel('热点名称', self.power_card), 0, 0)
        self.ssid_edit = LineEdit(self.power_card)
        self.ssid_edit.setPlaceholderText('最多 32 个字符')
        self.ssid_edit.setText(self._defaults.get('ssid', ''))
        self.ssid_edit.setClearButtonEnabled(True)
        form.addWidget(self.ssid_edit, 0, 1)

        form.addWidget(BodyLabel('热点密码', self.power_card), 1, 0)
        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.setSpacing(6)
        self.password_edit = LineEdit(self.power_card)
        self.password_edit.setPlaceholderText('至少 8 位')
        self.password_edit.setText(self._defaults.get('password', ''))
        self.password_edit.setEchoMode(LineEdit.Password)
        password_row.addWidget(self.password_edit, 1)
        self.reveal_button = TransparentToolButton(FIF.VIEW, self.power_card)
        self.reveal_button.setToolTip('显示密码')
        self.reveal_button.clicked.connect(self._toggle_reveal)
        password_row.addWidget(self.reveal_button)
        self.random_button = TransparentToolButton(FIF.SYNC, self.power_card)
        self.random_button.setToolTip('换一个随机密码')
        self.random_button.clicked.connect(self._randomize_password)
        password_row.addWidget(self.random_button)
        form.addLayout(password_row, 1, 1)

        form.addWidget(BodyLabel('频段', self.power_card), 2, 0)
        self.band_combo = ComboBox(self.power_card)
        self.band_combo.addItems(['自动（由系统决定）', '2.4 GHz（兼容性好）', '5 GHz（速度更快）'])
        self.band_combo.setMinimumWidth(220)
        form.addWidget(self.band_combo, 2, 1)
        self.power_card.add_layout(form)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.save_button = PushButton('保存名称与密码', self.power_card)
        self.save_button.clicked.connect(self._save_credentials)
        actions.addWidget(self.save_button)
        self.restart_button = PushButton('重启热点', self.power_card)
        self.restart_button.setToolTip('按新的名称 / 密码 / 频段重新启动')
        self.restart_button.setVisible(False)
        self.restart_button.clicked.connect(self._restart)
        actions.addWidget(self.restart_button)
        self.system_button = TransparentToolButton(FIF.SETTING, self.power_card)
        self.system_button.setToolTip('打开 Windows 的「移动热点」设置')
        self.system_button.clicked.connect(self._open_system_settings)
        actions.addWidget(self.system_button)
        actions.addStretch(1)
        self.power_card.add_layout(actions)

        self.hint_label = CaptionLabel('', self.power_card)
        self.hint_label.setWordWrap(True)
        self.hint_label.setTextColor(theme.color('text_secondary'), theme.color('text_secondary'))
        self.hint_label.setVisible(False)
        self.power_card.add(self.hint_label)
        layout.addWidget(self.power_card)

    def _build_devices_card(self, layout: QVBoxLayout) -> None:
        self.devices_card = Card('已连接设备', parent=self)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.devices_summary = BodyLabel('还没有设备连上来', self.devices_card)
        head.addWidget(self.devices_summary)
        head.addStretch(1)
        self.restore_all_button = PushButton('全部恢复', self.devices_card)
        self.restore_all_button.setVisible(False)
        self.restore_all_button.clicked.connect(self._restore_all)
        head.addWidget(self.restore_all_button)
        self.refresh_devices_button = TransparentToolButton(FIF.SYNC, self.devices_card)
        self.refresh_devices_button.setToolTip('刷新设备列表')
        self.refresh_devices_button.clicked.connect(self.reload_devices)
        head.addWidget(self.refresh_devices_button)
        self.devices_card.add_layout(head)

        self.devices_empty = CaptionLabel(
            '手机或平板连上这个热点后会出现在这里：可以改名、查看厂商、临时断开。',
            self.devices_card,
        )
        self.devices_empty.setTextColor(theme.color('text_secondary'), theme.color('text_secondary'))
        self.devices_empty.setWordWrap(True)
        self.devices_card.add(self.devices_empty)

        self.device_table = TableWidget(self.devices_card)
        self.device_table.setColumnCount(6)
        self.device_table.setHorizontalHeaderLabels(['设备', '类型 / 厂商', '状态', 'IP', 'MAC', '操作'])
        self.device_table.verticalHeader().setVisible(False)
        self.device_table.setBorderVisible(True)
        self.device_table.setBorderRadius(8)
        self.device_table.setWordWrap(False)
        self.device_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.device_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.device_table.setMinimumHeight(200)
        header = self.device_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.device_table.setColumnWidth(5, 56)
        self.device_table.setVisible(False)
        self.devices_card.add(self.device_table)
        layout.addWidget(self.devices_card)

    def _build_about_card(self, layout: QVBoxLayout) -> None:
        self.about_card = Card('使用提示', parent=self)
        tips = [
            (FIF.INFO, '热点由 Windows 的「移动热点」接管，关闭 CampusNet 后仍会继续运行。'),
            (FIF.WIFI, '名称和密码改完要点「保存名称与密码」，下次启动热点时写进系统配置。'),
            (FIF.CANCEL, '「断开」是给该设备 IP 加防火墙拦截，需要管理员权限，重启后失效。'),
        ]
        for icon, text in tips:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            glyph = IconWidget(icon.icon(color=theme.color('text_secondary')), self.about_card)
            glyph.setFixedSize(16, 16)
            row.addWidget(glyph, 0, Qt.AlignTop)
            label = BodyLabel(text, self.about_card)
            label.setWordWrap(True)
            label.setTextColor(theme.color('text_secondary'), theme.color('text_secondary'))
            row.addWidget(label, 1)
            self.about_card.add_layout(row)
        layout.addWidget(self.about_card)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def reload(self) -> None:
        run_async(self.service.hotspot_status, on_done=self._apply_status, on_error=self._fail)
        self.reload_devices()

    def refresh(self) -> None:
        """切到本页时自动刷新（窗口统一调用的入口）。"""
        self.reload()

    def reload_devices(self) -> None:
        run_async(
            self.service.hotspot_devices,
            on_done=self._apply_devices,
            on_error=lambda message: self.devices_summary.setText(f'读取设备失败：{message}'),
        )

    def _apply_status(self, status: dict[str, Any]) -> None:
        self._status = status or {}
        running = bool(status.get('running'))
        supported = bool(status.get('supported', True))
        backend = {
            'winrt': '系统移动热点',
            'powershell': '系统移动热点',
            'hosted': '承载网络（旧版）',
        }.get(str(status.get('backend') or ''), '不可用')

        if not supported:
            self.state_label.setText('不可用')
            self.state_label.setTextColor(theme.color('error'), theme.color('error'))
            self.state_hint.setText('这台机器没有可用的热点能力')
            self.toggle_button.setEnabled(False)
        else:
            self.state_label.setText('热点已开启' if running else '热点未开启')
            self.state_label.setTextColor(
                theme.color('accent') if running else theme.color('text'),
                theme.color('accent') if running else theme.color('text'),
            )
            self.state_hint.setText(
                '其它设备现在可以搜索并连接这个热点'
                if running
                else '点右侧按钮把本机 Wi-Fi 共享给其它设备'
            )
            self.toggle_button.setEnabled(not self._busy)

        count = int(status.get('clients') or 0)
        limit = int(status.get('max_clients') or 0) or 8
        self._max_clients = limit
        self.clients_tile.set_value(str(count), f'最多 {limit} 台')
        self.backend_tile.set_value(backend)
        self.band_tile.set_value(str(status.get('band') or '自动'))

        if status.get('ssid'):
            self.ssid_edit.setText(str(status['ssid']))
        if status.get('passphrase'):
            self.password_edit.setText(str(status['passphrase']))
        if supported and not running:
            band = str(status.get('band') or '')
            self.band_combo.setCurrentIndex(1 if '2.4' in band else 2 if '5' in band else 0)
        self.toggle_button.setText('停止热点' if running else '启动热点')
        self.restart_button.setVisible(running and supported)
        self.save_button.setEnabled(not self._busy)
        self.system_button.setEnabled(supported)

        reason = str(status.get('reason') or '')
        self.hint_label.setText(reason)
        self.hint_label.setVisible(bool(reason))
        self.hint_label.setTextColor(theme.color('error'), theme.color('error'))

    def _apply_devices(self, devices: list[dict[str, Any]]) -> None:
        self._devices = list(devices or [])
        table = self.device_table
        table.setRowCount(len(self._devices))
        for row, device in enumerate(self._devices):
            blocked = bool(device.get('blocked'))
            name = str(device.get('name') or '未知设备')
            vendor = str(device.get('vendor') or '未知')
            kind = str(device.get('kind') or '')
            detail = f'{kind} · {vendor}' if kind else vendor
            if device.get('random_mac'):
                detail += ' · 随机 MAC'
            name_item = QTableWidgetItem(name)
            if device.get('custom_name'):
                name_item.setToolTip('自定义名称')
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QTableWidgetItem(detail))
            status = QTableWidgetItem('已拦截' if blocked else '在线')
            status.setForeground(
                QColor(theme.color('error')) if blocked else QColor(theme.color('accent'))
            )
            table.setItem(row, 2, status)
            table.setItem(row, 3, QTableWidgetItem(str(device.get('ip') or '')))
            table.setItem(row, 4, QTableWidgetItem(str(device.get('mac') or '')))
            table.setCellWidget(row, 5, self._device_menu_button(device))

        count = len(self._devices)
        blocked = [d for d in self._devices if d.get('blocked')]
        stamp = time.strftime('%H:%M:%S')
        text = f'{count} 台设备在线 · 最多 {self._max_clients} 台' if count else '还没有设备连上来'
        self.devices_summary.setText(f'{text}（{stamp} 更新）')
        self.restore_all_button.setVisible(bool(blocked))
        self.restore_all_button.setText(f'全部恢复（{len(blocked)}）' if blocked else '全部恢复')
        self.device_table.setVisible(bool(count))
        self.devices_empty.setVisible(not count)

    def _device_menu_button(self, device: dict[str, Any]) -> QWidget:
        button = TransparentToolButton(FIF.MORE, self.devices_card)
        button.setToolTip('更多操作')
        button.clicked.connect(lambda _=False, dev=device, btn=button: self._show_device_menu(dev, btn))
        return button

    def _show_device_menu(self, device: dict[str, Any], button: QWidget) -> None:
        ip = str(device.get('ip') or '')
        mac = str(device.get('mac') or '')
        name = str(device.get('name') or '')
        blocked = bool(device.get('blocked'))
        menu = RoundMenu(parent=self)
        menu.addAction(Action(FIF.COPY, '复制 IP', triggered=lambda: self._copy(ip, 'IP')))
        menu.addAction(Action(FIF.COPY, '复制 MAC', triggered=lambda: self._copy(mac, 'MAC')))
        menu.addAction(Action(FIF.EDIT, '重命名…', triggered=lambda: self._rename(device)))
        menu.addSeparator()
        if blocked:
            menu.addAction(Action(FIF.SYNC, '恢复连接', triggered=lambda: self._toggle_device(ip, True)))
        else:
            menu.addAction(Action(FIF.CANCEL, '临时断开', triggered=lambda: self._toggle_device(ip, False)))
        menu.exec(button.mapToGlobal(QPoint(0, button.height())))

    def _copy(self, text: str, label: str) -> None:
        if not text:
            return
        QApplication.clipboard().setText(text)
        Toast.success(self, '已复制', f'{label}：{text}')

    def _rename(self, device: dict[str, Any]) -> None:
        mac = str(device.get('mac') or '')
        current = str(device.get('name') or '')
        text, ok = QInputDialog.getText(self, '重命名设备', '给这台设备起个好认的名字（留空恢复原名）：', text=current)
        if not ok:
            return

        def done(result: dict[str, Any]) -> None:
            if result.get('ok'):
                self.reload_devices()
            else:
                Toast.error(self, '重命名失败', str(result.get('message') or ''))

        run_async(self.service.hotspot_rename, mac, text or '', on_done=done, on_error=self._fail)

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool, text: str = '') -> None:
        self._busy = busy
        self.toggle_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy)
        self.restart_button.setEnabled(not busy)
        if busy and text:
            self.toggle_button.setText(text)

    def _fail(self, message: str) -> None:
        self._set_busy(False)
        Toast.error(self, '热点共享', str(message))

    def _toggle(self) -> None:
        if self._busy:
            return
        running = bool(self._status.get('running'))
        self._set_busy(True, '正在停止…' if running else '正在启动…')
        if running:
            run_async(self.service.hotspot_stop, on_done=self._after_toggle, on_error=self._fail)
            return
        ssid = self.ssid_edit.text().strip()
        password = self.password_edit.text()
        run_async(
            self.service.hotspot_start,
            ssid,
            password,
            self._band_value(),
            on_done=self._after_toggle,
            on_error=self._fail,
        )

    def _restart(self) -> None:
        if self._busy:
            return
        self._set_busy(True, '正在重启…')
        run_async(
            self.service.hotspot_restart,
            self.ssid_edit.text().strip(),
            self.password_edit.text(),
            self._band_value(),
            on_done=self._after_toggle,
            on_error=self._fail,
        )

    def _after_toggle(self, result: dict[str, Any]) -> None:
        self._set_busy(False)
        if not result.get('ok'):
            Toast.warning(self, '热点共享', str(result.get('message') or '操作失败'))
            return
        Toast.success(self, '热点共享', str(result.get('message') or '已更新'))
        self.reload()

    def _save_credentials(self) -> None:
        ssid = self.ssid_edit.text().strip()
        password = self.password_edit.text()
        self._set_busy(True, '正在保存…')

        def done(result: dict[str, Any]) -> None:
            self._set_busy(False)
            if result.get('ok'):
                Toast.success(self, '热点共享', str(result.get('message') or '已保存'))
            else:
                Toast.warning(self, '热点共享', str(result.get('message') or '保存失败'))
            self.reload()

        run_async(
            self.service.hotspot_configure,
            ssid,
            password,
            self._band_value(),
            on_done=done,
            on_error=self._fail,
        )

    def _band_value(self) -> str:
        """下拉框 → 设置/接口用的值。"""
        return ('', '2.4', '5')[self.band_combo.currentIndex()]

    def _toggle_reveal(self) -> None:
        hidden = self.password_edit.echoMode() == LineEdit.Password
        self.password_edit.setEchoMode(LineEdit.Normal if hidden else LineEdit.Password)
        self.reveal_button.setIcon(FIF.HIDE if hidden else FIF.VIEW)
        self.reveal_button.setToolTip('隐藏密码' if hidden else '显示密码')

    def _randomize_password(self) -> None:
        defaults = self.service.hotspot_defaults()
        self.password_edit.setText(str(defaults.get('password') or ''))

    def _open_system_settings(self) -> None:
        result = self.service.hotspot_open_settings()
        if not result.get('ok'):
            Toast.warning(self, '热点共享', str(result.get('message') or '打不开系统设置'))

    def _toggle_device(self, ip: str, blocked: bool) -> None:
        if not ip:
            return
        if blocked:
            run_async(self.service.hotspot_unblock, ip, on_done=self._after_device, on_error=self._fail)
        else:
            run_async(self.service.hotspot_block, ip, on_done=self._after_device, on_error=self._fail)

    def _restore_all(self) -> None:
        pending = [d for d in self._devices if d.get('blocked') and d.get('ip')]
        if not pending:
            return
        for device in pending:
            run_async(self.service.hotspot_unblock, str(device['ip']), on_error=self._fail)
        Toast.success(self, '热点共享', f'正在恢复 {len(pending)} 台设备')
        self.reload_devices()

    def _after_device(self, result: dict[str, Any]) -> None:
        if result.get('ok'):
            Toast.success(self, '热点共享', str(result.get('message') or '已处理'))
        else:
            Toast.warning(self, '热点共享', str(result.get('message') or '操作失败'))
        self.reload_devices()

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    def on_theme_changed(self) -> None:
        self.hero_icon.setIcon(FIF.SHARE.icon(color=theme.color('accent')))
        self._apply_status(self._status)
