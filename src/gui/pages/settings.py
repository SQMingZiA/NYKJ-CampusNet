"""设置页：账户 / 自动连接 / Portal 认证 / 流量统计 / 界面 / 维护。

全部用 **FluentUI** 的 ``SettingCard`` 体系（图标 + 标题 + 说明 … 右侧控件），
与 Windows 11 设置应用的排版一致。改动即时生效并落盘。
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
    HyperlinkButton,
    LineEdit,
    PushButton,
    SettingCardGroup,
    StrongBodyLabel,
    TableWidget,
)

from .. import theme
from ..async_task import run_async
from ..toasts import Toast
from ..widgets import (
    ButtonRow,
    Card,
    ComboRow,
    DoubleSpinRow,
    KVGrid,
    SpinRow,
    SwitchRow,
    TextRow,
    body_label,
    scroll_page,
)

# 仓库地址：换成你自己的 GitHub 仓库即可。
GITHUB_URL = "https://github.com/campusnet/CampusNet"

# 设置页位于 <仓库根>/src/gui/pages/settings.py，向上四层即项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 第三方组件及其许可证（关于页展示用）。
ACKNOWLEDGEMENTS = [
    ('PyQt5 / Qt', 'GPLv3 / LGPLv3', 'https://www.qt.io/'),
    (
        'PyQt-Fluent-Widgets',
        'GPLv3',
        'https://github.com/zhiyiYo/PyQt-Fluent-Widgets',
    ),
    (
        'PyQt-Frameless-Window',
        'GPLv3',
        'https://github.com/zhiyiYo/PyQt-Frameless-Window',
    ),
    ('pyqtgraph', 'MIT', 'https://www.pyqtgraph.org/'),
    ('requests', 'Apache-2.0', 'https://requests.readthedocs.io/'),
]

DRIVERS = [
    ('自动识别', 'auto'),
    ('蓝海卓越 / 派网 Portal', 'lanhai'),
    ('通用 Portal（表单提交）', 'generic'),
]

THEMES = [
    ('跟随系统', 'auto'),
    ('浅色', 'light'),
    ('深色', 'dark'),
]

DEFAULT_SSIDS = ['NYKJ', 'CMCC-NYKJ', 'CMCCNYKJ', 'NYKY']


class SettingsPage(QWidget):
    """设置页。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('SettingsPage')
        self.service = service
        self._window = None
        self._config = {}
        self._groups = []
        # 不参与分组高度修正的独立卡片（SSID 名单、关于）
        self._loose = []
        # 回填配置期间抑制控件信号
        self._loading = True
        # 账户「保存并测试」流程进行中：按钮禁用并显示进度文案
        self._saving = False
        # -- 构建界面 --
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = scroll_page(self, margins=(28, 20, 28, 28), spacing=18)
        outer.addWidget(scroll)
        self._body_layout = layout

        # 认证账户
        account = SettingCardGroup('认证账户', self)
        self.username_row = TextRow(
            FIF.PEOPLE,
            '校园网账号',
            '只填号码本身（校园卡手机号），不要带「例如」这类提示文字',
            placeholder='例如 16697763375',
            parent=account,
        )
        self.password_row = TextRow(
            FIF.CERTIFICATE,
            '密码',
            '初始密码通常是手机号后 6 位；留空表示不修改',
            password=True,
            parent=account,
        )
        self.save_account_row = ButtonRow(
            FIF.SAVE,
            '保存账户',
            '账号密码会用 Windows DPAPI 加密后保存在本机',
            '保存并测试',
            parent=account,
        )
        self.save_account_row.clicked.connect(self.save_and_test)
        for card in (self.username_row, self.password_row, self.save_account_row):
            account.addSettingCard(card)
        layout.addWidget(account)
        self._register(
            account,
            [self.username_row, self.password_row, self.save_account_row],
        )

        # 自动连接与认证
        auto = SettingCardGroup('自动连接与认证', self)
        self.auto_connect_row = SwitchRow(
            FIF.WIFI, '自动连接校园网', '检测到校园网 SSID 时自动连接', parent=auto
        )
        self.auto_login_row = SwitchRow(
            FIF.CONNECT, '自动提交认证', '检测到认证页面时自动登录', parent=auto
        )
        self.auto_retry_row = SwitchRow(
            FIF.SYNC, '断线自动重试', '网络掉线后按下面的次数与间隔重试', parent=auto
        )
        self.notify_drop_row = SwitchRow(
            FIF.INFO,
            '掉线时发通知',
            '被其它设备挤下线、学校侧断会话或手动退出时，弹 Windows 通知（应用名「校园网」）',
            parent=auto,
        )
        self.auto_reconnect_row = SwitchRow(
            FIF.CONNECT,
            '掉线后立刻自动重连',
            '不等下一轮 15 秒巡检，马上重新认证（尽量无感）',
            parent=auto,
        )
        self.kick_row = SwitchRow(
            FIF.PEOPLE,
            '挤掉其它在线设备',
            '账号在线数已满（-1022）时自动下线最早在线的设备',
            parent=auto,
        )
        for card in (
            self.auto_connect_row,
            self.auto_login_row,
            self.auto_retry_row,
            self.notify_drop_row,
            self.auto_reconnect_row,
            self.kick_row,
        ):
            auto.addSettingCard(card)

        self.ssid_card = Card(
            '自动连接的网络名单',
            '支持写法互通：CMCC-NYKJ 与 CMCCNYKJ 视为同一个网络',
            parent=auto,
        )
        rule = QHBoxLayout()
        rule.setSpacing(8)
        self.ssid_edit = LineEdit(self.ssid_card)
        self.ssid_edit.setPlaceholderText('输入 SSID 后回车添加，例如 NYKJ')
        self.ssid_edit.setMinimumWidth(240)
        self.ssid_edit.returnPressed.connect(self.add_ssid)
        rule.addWidget(self.ssid_edit, 1)
        add_button = PushButton(FIF.ADD, '添加', self.ssid_card)
        add_button.clicked.connect(self.add_ssid)
        rule.addWidget(add_button)
        remove_button = PushButton(FIF.DELETE, '移除选中', self.ssid_card)
        remove_button.clicked.connect(self.remove_ssid)
        rule.addWidget(remove_button)
        scan_button = PushButton(FIF.SEARCH, '扫描并加入', self.ssid_card)
        scan_button.clicked.connect(self.scan_and_add)
        rule.addWidget(scan_button)
        reset_button = PushButton(FIF.UPDATE, '恢复默认', self.ssid_card)
        reset_button.clicked.connect(
            lambda: self._save({'wifi_allow_list': list(DEFAULT_SSIDS)})
        )
        rule.addWidget(reset_button)
        self.ssid_card.add_layout(rule)

        self.ssid_table = TableWidget(self.ssid_card)
        self.ssid_table.setColumnCount(2)
        self.ssid_table.setHorizontalHeaderLabels(['SSID', '说明'])
        self.ssid_table.setBorderVisible(True)
        self.ssid_table.setBorderRadius(6)
        self.ssid_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ssid_table.verticalHeader().setVisible(False)
        self.ssid_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.ssid_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.ssid_table.setMinimumHeight(150)
        self.ssid_card.add(self.ssid_table)
        auto.addSettingCard(self.ssid_card)

        layout.addWidget(auto)
        self._register(
            auto,
            [
                self.auto_connect_row,
                self.auto_login_row,
                self.auto_retry_row,
                self.notify_drop_row,
                self.auto_reconnect_row,
                self.kick_row,
            ],
        )

        # SSID 卡片自带表格，不参与分组高度修正
        layout.addWidget(self.ssid_card)
        self._loose.append(self.ssid_card)

        # Portal 认证
        portal = SettingCardGroup('Portal 认证', self)
        self.driver_row = ComboRow(
            FIF.GLOBE, '认证驱动', '一般保持「自动识别」即可', DRIVERS, parent=portal
        )
        self.acip_row = TextRow(
            FIF.IOT,
            '强制 AC 地址',
            '留空则自动从认证页面读取（本校门户声明为 10.100.0.1，不要乱填）',
            placeholder='留空 = 自动读取',
            parent=portal,
        )
        self.portal_url_row = TextRow(
            FIF.LINK,
            '自定义 Portal 地址',
            '留空则自动探测认证门户',
            placeholder='http://10.100.2.2:9090/',
            parent=portal,
        )
        self.retry_count_row = SpinRow(
            FIF.SYNC,
            '重试次数',
            '自动重试的最大次数',
            minimum=0,
            maximum=20,
            parent=portal,
        )
        self.retry_delay_row = SpinRow(
            FIF.HISTORY,
            '重试间隔',
            '每次重试之间的等待时间',
            minimum=1,
            maximum=300,
            suffix='秒',
            parent=portal,
        )
        for card in (
            self.driver_row,
            self.acip_row,
            self.portal_url_row,
            self.retry_count_row,
            self.retry_delay_row,
        ):
            portal.addSettingCard(card)
        layout.addWidget(portal)
        self._register(
            portal,
            [
                self.driver_row,
                self.acip_row,
                self.portal_url_row,
                self.retry_count_row,
                self.retry_delay_row,
            ],
        )

        # 流量统计
        traffic = SettingCardGroup('流量统计', self)
        self.interval_row = SpinRow(
            FIF.SPEED_HIGH,
            '采样间隔',
            '每隔多久读一次网卡计数器',
            minimum=1,
            maximum=120,
            suffix='秒',
            parent=traffic,
        )
        self.traffic_only_row = SwitchRow(
            FIF.WIFI,
            '仅记录校园网流量',
            '连的是手机热点、家里宽带时不写入历史与用量；关掉后任何网络环境都记账',
            parent=traffic,
        )
        self.quota_auto_row = SwitchRow(
            FIF.SYNC,
            '自动读取套餐额度',
            '从学校自助系统读取本月总额度；「不限量」套餐会自动置 0',
            parent=traffic,
        )
        self.quota_row = DoubleSpinRow(
            FIF.CHAT,
            '每月套餐流量',
            '填写后主页会显示套餐进度；0 表示不统计配额',
            minimum=0,
            maximum=100000,
            suffix='GB',
            parent=traffic,
        )
        self.retention_row = SpinRow(
            FIF.CALENDAR,
            '历史保留天数',
            '超过该天数的按小时记录会被清理',
            minimum=7,
            maximum=3650,
            suffix='天',
            parent=traffic,
        )
        for card in (
            self.interval_row,
            self.traffic_only_row,
            self.quota_auto_row,
            self.quota_row,
            self.retention_row,
        ):
            traffic.addSettingCard(card)

        export_row = ButtonRow(
            FIF.SAVE,
            '导出流量数据',
            '把按小时的用量导出成 CSV',
            '导出 CSV',
            parent=traffic,
        )
        export_row.clicked.connect(self.export_csv)
        reset_row = ButtonRow(
            FIF.DELETE,
            '重置流量统计',
            '清空全部历史用量记录',
            '重置',
            parent=traffic,
        )
        reset_row.clicked.connect(self.reset_traffic)
        traffic.addSettingCard(export_row)
        traffic.addSettingCard(reset_row)
        layout.addWidget(traffic)
        self._register(
            traffic,
            [
                self.interval_row,
                self.traffic_only_row,
                self.quota_auto_row,
                self.quota_row,
                self.retention_row,
                export_row, reset_row,
            ],
        )

        # 界面与启动
        appearance = SettingCardGroup('界面与启动', self)
        self.theme_row = ComboRow(
            FIF.PALETTE,
            '主题',
            'FluentUI 浅色 / 深色 / 跟随系统',
            THEMES,
            parent=appearance,
        )
        self.mica_row = SwitchRow(
            FIF.VIEW,
            '云母材质',
            'Windows 11 上的半透明背景效果（Win10 无效）',
            parent=appearance,
        )
        self.tray_row = SwitchRow(
            FIF.CLOSE,
            '关闭时最小化到托盘',
            '关闭窗口不退出程序，继续后台守护',
            parent=appearance,
        )
        self.notify_row = SwitchRow(
            FIF.INFO,
            '系统通知',
            '总开关：认证结果、掉线、账号冲突等提示都由它控制',
            parent=appearance,
        )
        self.auto_launch_row = SwitchRow(
            FIF.UPDATE,
            '开机自动启动',
            '写入当前用户的启动项，登录后自动运行',
            parent=appearance,
        )
        self.start_min_row = SwitchRow(
            FIF.HOME,
            '启动后最小化',
            '开机自启时直接缩到托盘，不打扰',
            parent=appearance,
        )
        self.home_package_row = SwitchRow(
            FIF.CHAT,
            '主页显示学校套餐',
            '在主页展示套餐名称、到期时间与流量（来自学校自助系统）',
            parent=appearance,
        )
        self.developer_row = SwitchRow(
            FIF.ROBOT,
            '开发者模式',
            '在左侧导航底部显示「开发者」页：可逐个调用接口并查看原始返回',
            parent=appearance,
        )
        for card in (
            self.theme_row,
            self.mica_row,
            self.tray_row,
            self.notify_row,
            self.auto_launch_row,
            self.start_min_row,
            self.home_package_row,
            self.developer_row,
        ):
            appearance.addSettingCard(card)
        layout.addWidget(appearance)
        self._register(
            appearance,
            [
                self.theme_row,
                self.mica_row,
                self.tray_row,
                self.notify_row,
                self.auto_launch_row,
                self.start_min_row,
                self.home_package_row,
                self.developer_row,
            ],
        )

        # 维护
        maintenance = SettingCardGroup('维护', self)
        open_dir = ButtonRow(
            FIF.FOLDER,
            '数据目录',
            '配置、流量历史与内置浏览器数据都放在这里',
            '打开目录',
            parent=maintenance,
        )
        open_dir.clicked.connect(lambda: self.service.open_data_dir())
        maintenance.addSettingCard(open_dir)

        reset_all = ButtonRow(
            FIF.DELETE,
            '重置全部设置',
            '恢复出厂配置（账号密码也会清除）',
            '重置设置',
            parent=maintenance,
        )
        reset_all.clicked.connect(self.reset_all)
        maintenance.addSettingCard(reset_all)
        layout.addWidget(maintenance)
        self._register(maintenance, [open_dir, reset_all])

        # 关于
        self.about_card = Card(
            '关于 CampusNet', '本机运行 · 数据不出网', parent=self
        )
        self.about_card.add(
            body_label(
                'CampusNet 是一个面向校园网的 Windows 桌面助手：自动连接、认证、'
                '流量统计与网络诊断都在一个窗口里完成。',
                parent=self.about_card,
            )
        )

        self.about_grid = KVGrid(
            [
                ('version', '程序版本'),
                ('qt', 'Qt'),
                ('fluent', 'FluentUI'),
                ('python', 'Python'),
                ('os', '系统'),
                ('data', '数据目录'),
            ],
            columns=1,
            parent=self.about_card,
        )
        self.about_card.add(self.about_grid)

        links = QHBoxLayout()
        links.setSpacing(8)
        links.addWidget(body_label('许可证：MIT License', parent=self.about_card))
        license_button = PushButton(FIF.CERTIFICATE, '查看 LICENSE', self.about_card)
        license_button.setToolTip('打开项目根目录的 LICENSE 文件')
        license_button.clicked.connect(self._open_license)
        links.addWidget(license_button)

        self.github_link = HyperlinkButton(
            FIF.GITHUB,
            GITHUB_URL,
            '打开 GitHub 仓库',
            parent=self.about_card,
        )
        self.github_link.setToolTip(GITHUB_URL)
        links.addWidget(self.github_link)
        links.addStretch(1)
        self.about_card.add_layout(links)

        self.about_card.add(StrongBodyLabel('开源致谢', self.about_card))
        for name, license_name, url in ACKNOWLEDGEMENTS:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(
                body_label(f'{name} — {license_name}', parent=self.about_card), 1
            )
            link = HyperlinkButton(url, '项目主页', self.about_card)
            link.setToolTip(url)
            row.addWidget(link, 0)
            self.about_card.add_layout(row)

        self.about_card.add(
            body_label(
                r'数据只保存在本机 %APPDATA%\CampusNet，不会上传到任何服务器。',
                muted=True,
                parent=self.about_card,
            )
        )

        layout.addWidget(self.about_card)
        self._loose.append(self.about_card)

        layout.addStretch(1)
        self._wire()

    def _register(self, group: SettingCardGroup, cards: list[QWidget]) -> None:
        self._groups.append((group, cards))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 延后一帧：等样式表与布局稳定后再统一修正分组高度
        QTimer.singleShot(0, self._fix_group_heights)
        QTimer.singleShot(150, self._fix_group_heights)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fix_group_heights()

    def _fix_group_heights(self) -> None:
        """修 SettingCardGroup 那片多余的空白（以及随后的重叠）。

        qfluentwidgets 的 ``SettingCardGroup`` 用 ``ExpandLayout`` 管卡片，而那个布局：

        * ``heightForWidth()`` 读的是**控件当时的高度**，并且只在 ``addSettingCard`` 时算一次 ——
          那时卡片还没被样式表撑到 70px，算出来的高度偏大，之后再也不重算；
        * ``addWidget()`` 甚至不往 ``__items`` 里放，``count()`` 永远是 0。

        结果就是分组底部留出一大片空白（实测「自动连接与认证」分组高 647，内容只有 ~290）。
        只调用 ``adjustSize()`` 会把它改矮，但父布局不会跟着重排，卡片就会**叠在一起**。

        所以这里改成确定性做法：按子控件的真实高度算好总高并**固定**分组高度，
        最后强制父布局立刻重排一次。
        """
        for group, cards in self._groups:
            visible = [card for card in cards if card.isVisible()]
            if not visible:
                continue
            spacing = 2
            body = sum(
                max(card.height(), card.minimumSizeHint().height()) for card in visible
            )
            title = 0
            label = getattr(group, 'titleLabel', None)
            if label is not None:
                title = label.height() or 29
            total = title + (12 if title else 0) + body + spacing * max(0, len(visible) - 1) + 10
            try:
                group.setFixedHeight(total)
            except Exception:
                continue

        layout = getattr(self, '_body_layout', None)
        if layout is not None:
            try:
                layout.invalidate()
                layout.activate()
            except Exception:
                return

    def _wire(self) -> None:
        def on(signal, handler):
            """包装一层：回填配置期间忽略控件的变更信号。"""

            def wrapper(*args):
                if self._loading:
                    return
                return handler(*args)

            signal.connect(wrapper)

        on(self.auto_connect_row.changed, lambda v: self._save({'auto_connect': v}))
        on(self.auto_login_row.changed, lambda v: self._save({'auto_login': v}))
        on(
            self.auto_retry_row.changed,
            lambda v: self._save({'auto_retry_on_drop': v}),
        )
        on(self.notify_drop_row.changed, lambda v: self._save({'notify_on_drop': v}))
        on(
            self.auto_reconnect_row.changed,
            lambda v: self._save({'auto_reconnect_on_logout': v}),
        )
        on(self.kick_row.changed, lambda v: self._save({'kick_on_conflict': v}))
        on(self.driver_row.changed, lambda v: self._save({'driver': v}))
        on(self.acip_row.submitted, lambda v: self._save({'acip': v.strip()}))
        on(
            self.acip_row.edit.editingFinished,
            lambda: self._save({'acip': self.acip_row.text().strip()}),
        )
        on(
            self.portal_url_row.edit.editingFinished,
            lambda: self._save({'custom_portal_url': self.portal_url_row.text().strip()}),
        )
        on(self.retry_count_row.changed, lambda v: self._save({'retry_count': int(v)}))
        on(
            self.retry_delay_row.changed,
            lambda v: self._save({'retry_delay_ms': int(v) * 1000}),
        )
        on(
            self.interval_row.changed,
            lambda v: self._save({'traffic_interval_ms': int(v) * 1000}),
        )
        on(
            self.traffic_only_row.changed,
            lambda v: self._save({'traffic_only_campus': bool(v)}),
        )
        on(self.quota_auto_row.changed, self._on_quota_auto)
        on(self.quota_row.changed, lambda v: self._save({'monthly_quota_gb': float(v)}))
        on(
            self.retention_row.changed,
            lambda v: self._save({'traffic_retention_days': int(v)}),
        )
        on(self.theme_row.changed, self._on_theme)
        on(self.mica_row.changed, self._on_mica)
        on(self.tray_row.changed, lambda v: self._save({'close_to_tray': v}))
        on(self.notify_row.changed, lambda v: self._save({'show_notification': v}))
        on(self.auto_launch_row.changed, self._on_auto_launch)
        on(self.start_min_row.changed, lambda v: self._save({'start_minimized': v}))
        on(self.home_package_row.changed, self._on_home_package)
        on(self.developer_row.changed, self._on_developer_mode)

    def bind_window(self, window) -> None:
        self._window = window

    def reload(self) -> None:
        run_async(self.service.get_config, on_done=self._apply_config)
        run_async(self.service.auto_launch_state, on_done=self._apply_autostart)

    def _apply_config(self, config: Any) -> None:
        if not isinstance(config, dict):
            self._loading = False
            return
        self._config = config
        self._loading = True
        try:
            self._apply_config_values(config)
        finally:
            self._loading = False

    def _apply_config_values(self, config: dict) -> None:
        self.username_row.set_text(str(config.get('username') or ''))
        self.password_row.set_text('')
        self.password_row.edit.setPlaceholderText(
            '已保存（留空则不修改）' if config.get('has_password') else '请输入密码'
        )
        self.auto_connect_row.set_checked(bool(config.get('auto_connect')))
        self.auto_login_row.set_checked(bool(config.get('auto_login')))
        self.auto_retry_row.set_checked(bool(config.get('auto_retry_on_drop')))
        self.notify_drop_row.set_checked(bool(config.get('notify_on_drop', True)))
        self.auto_reconnect_row.set_checked(
            bool(config.get('auto_reconnect_on_logout', True))
        )
        self.kick_row.set_checked(bool(config.get('kick_on_conflict')))

        self.driver_row.set_value(str(config.get('driver') or 'auto'))
        self.acip_row.set_text(str(config.get('acip') or ''))
        self.portal_url_row.set_text(str(config.get('custom_portal_url') or ''))
        self.retry_count_row.set_value(int(config.get('retry_count') or 0))
        self.retry_delay_row.set_value(int((config.get('retry_delay_ms') or 8000) / 1000))

        self.interval_row.set_value(int((config.get('traffic_interval_ms') or 5000) / 1000))
        self.traffic_only_row.set_checked(
            bool(config.get('traffic_only_campus', True))
        )
        auto_quota = bool(config.get('quota_auto', True))
        self.quota_auto_row.set_checked(auto_quota)
        self.quota_row.set_value(float(config.get('monthly_quota_gb') or 0))
        self.quota_row.setEnabled(not auto_quota)
        self.retention_row.set_value(int(config.get('traffic_retention_days') or 120))

        self.theme_row.set_value(str(config.get('theme') or 'auto'))
        self.mica_row.set_checked(bool(config.get('mica_effect')))
        self.tray_row.set_checked(bool(config.get('close_to_tray')))
        self.notify_row.set_checked(bool(config.get('show_notification')))
        self.auto_launch_row.set_checked(bool(config.get('auto_launch')))
        self.start_min_row.set_checked(bool(config.get('start_minimized')))
        self.home_package_row.set_checked(bool(config.get('home_show_package', True)))
        self.developer_row.set_checked(bool(config.get('developer_mode')))

        self._apply_ssid_table(config.get('wifi_allow_list') or [])
        self._apply_about(config)

    def _apply_ssid_table(self, patterns: list[str]) -> None:
        self._fill_ssid_rows(patterns, '')

    def _apply_about(self, config: dict) -> None:
        try:
            from qfluentwidgets import __version__ as fluent_version
        except Exception:
            fluent_version = '-'
        from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

        self.about_grid.update_values(
            {
                'version': _app_version(),
                'qt': f'{QT_VERSION_STR}（PyQt {PYQT_VERSION_STR}）',
                'fluent': f'PyQt-Fluent-Widgets {fluent_version}',
                'python': platform.python_version(),
                'os': f'{platform.system()} {platform.release()}',
                'data': str(config.get('data_dir') or ''),
            }
        )

    def _save(
        self,
        patch: dict[str, Any],
        on_done=None,
        on_error=None,
    ) -> None:
        """异步写配置；保存失败时统一弹提示，避免界面静默丢设置。"""

        def done(config: Any) -> None:
            self._on_saved(config)
            if on_done is not None:
                on_done(config)

        def failed(error: str) -> None:
            Toast.error(self, '保存失败', str(error))
            if on_error is not None:
                on_error(error)

        run_async(self.service.update_config, patch, on_done=done, on_error=failed)

    def _on_saved(self, config: Any) -> None:
        if isinstance(config, dict):
            self._config = config

    def _open_license(self) -> None:
        """打开项目根目录的 LICENSE；打包后找不到时回退到 GitHub。"""
        path = PROJECT_ROOT / 'LICENSE'
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QDesktopServices.openUrl(QUrl(f'{GITHUB_URL}/blob/main/LICENSE'))

    def _finish_save(self) -> None:
        """恢复账户保存按钮。"""
        self._saving = False
        self.save_account_row.set_text('保存并测试')
        self.save_account_row.set_enabled(True)

    def _on_theme(self, value: str) -> None:
        self._save({'theme': value})
        theme.apply_theme(value)
        if self._window is not None:
            self._window.refresh_theme()

    def _on_mica(self, value: bool) -> None:
        """云母材质：即时生效，不必重启。

        以前这里只把开关写进配置，实际效果要等下次启动才生效，看起来就像
        「开关坏了」。现在顺手把效果应用到当前窗口。
        """
        self._save({'mica_effect': value})
        window = self._window if self._window is not None else self.window()
        setter = getattr(window, 'setMicaEffectEnabled', None)
        if callable(setter):
            try:
                setter(bool(value))
            except Exception:
                pass

    def _on_quota_auto(self, value: bool) -> None:
        """自动读取额度时，手动输入框置灰（它的值会被学校数据覆盖）。"""
        self._save({'quota_auto': value})
        self.quota_row.setEnabled(not value)

    def _on_auto_launch(self, value: bool) -> None:
        """开机自启：写**计划任务**（需要提权的程序走 Run 键会被系统静默跳过）。

结果如实反馈 —— 之前"明明勾了、开机就是不启动"的原因就是：
程序要求管理员权限，Windows 会跳过 Run 键里的提权程序，
而界面只记住了勾选状态、没有反映系统的真实情况。
"""
        self._save({'auto_launch': value})

        def done(result: Any) -> None:
            ok = isinstance(result, dict) and result.get('ok')
            message = str((result or {}).get('message', ''))
            if ok:
                Toast.success(
                    self,
                    '开机自启已开启' if value else '开机自启已关闭',
                    message,
                )
            else:
                Toast.error(self, '开机自启设置失败', message or '未知原因')
            self._apply_autostart(self.service.auto_launch_state())

        run_async(self.service.set_auto_launch, value, on_done=done)

    def _apply_autostart(self, state: Any) -> None:
        """把开关与说明文字对齐到系统的真实状态。"""
        if not isinstance(state, dict):
            return
        enabled = bool(state.get('enabled'))
        mechanism = (
            '计划任务（登录时以最高权限启动，不弹 UAC）'
            if state.get('task')
            else 'Run 键（注意：需要提权的程序可能被系统跳过）'
            if state.get('run_key')
            else '未设置'
        )
        self._loading = True
        try:
            self.auto_launch_row.set_checked(enabled)
            text = (
                f'当前：{mechanism}'
                if enabled
                else '开启后会创建计划任务（提权程序用 Run 键会被系统跳过），登录 Windows 即自动运行'
            )
            try:
                self.auto_launch_row.setContent(text)
            except Exception:
                pass
        finally:
            self._loading = False

    def _on_home_package(self, value: bool) -> None:
        self._save({'home_show_package': value})
        if self._window is not None and hasattr(
            self._window.home_page, 'set_package_visible'
        ):
            self._window.home_page.set_package_visible(value)

    def _on_developer_mode(self, value: bool) -> None:
        if self._window is not None and hasattr(self._window, 'set_developer_mode'):
            self._window.set_developer_mode(value)
            return
        self._save({'developer_mode': value})

    def save_and_test(self) -> None:
        if self._saving:
            return
        raw = self.username_row.text().strip()
        for bad in ('例如', '手机号', '学号', '校园卡', '请输入', '请填写'):
            if bad in raw:
                Toast.warning(
                    self,
                    '账号格式不对',
                    f'这里要填号码本身，不要带「{bad}」这类提示文字。',
                )
                return
        username = ''.join(ch for ch in raw if ch.isalnum())
        if username != raw:
            self._loading = True
            try:
                self.username_row.set_text(username)
            finally:
                self._loading = False
        if not username or (not username.isdigit() and len(username) < 4):
            Toast.warning(self, '请先填写账号', '校园网账号是校园卡手机号，只填数字。')
            return
        patch = {'username': username}
        password = self.password_row.text()
        if password:
            patch['password'] = password

        self._saving = True
        self.save_account_row.set_enabled(False)
        self.save_account_row.set_text('保存中…')

        def done(_config: Any) -> None:
            self.password_row.set_text('')
            self.password_row.edit.setPlaceholderText('已保存（留空则不修改）')
            Toast.info(self, '已保存', '正在测试认证…')
            self.save_account_row.set_text('测试中…')
            self._test_login()

        def failed(_error: str) -> None:
            self._finish_save()

        self._save(patch, on_done=done, on_error=failed)

    def _test_login(self) -> None:
        def done(result: Any) -> None:
            if isinstance(result, dict) and (
                result.get('ok') or result.get('state') == 'online'
            ):
                Toast.success(self, '认证成功', '账号可用，已联网。')
            elif isinstance(result, dict) and result.get('auth', {}).get('ok'):
                Toast.success(self, '认证成功', '账号可用，已联网。')
            else:
                message = ''
                if isinstance(result, dict):
                    message = str(
                        (result.get('auth') or {}).get('message')
                        or result.get('label')
                        or ''
                    )
                Toast.warning(
                    self,
                    '认证未成功',
                    message or '请检查账号密码，或用「打开认证页面」手动登录。',
                )
            self._finish_save()

        def failed(error: str) -> None:
            Toast.error(self, '测试认证失败', str(error))
            self._finish_save()

        run_async(
            lambda: self.service.tick(manual=True),
            on_done=done,
            on_error=failed,
        )

    def add_ssid(self) -> None:
        value = self.ssid_edit.text().strip()
        if not value:
            return
        current = list(self._config.get('wifi_allow_list') or [])
        if value not in current:
            current.append(value)
        self.ssid_edit.clear()
        self._save({'wifi_allow_list': current})

    def remove_ssid(self) -> None:
        rows = (
            self.ssid_table.selectionModel().selectedRows()
            if self.ssid_table.selectionModel()
            else []
        )
        if not rows:
            Toast.info(self, '请先选择', '在名单里点一行再移除。')
            return
        current = list(self._config.get('wifi_allow_list') or [])
        index = rows[0].row()
        if 0 <= index < len(current):
            removed = current.pop(index)
            self._save({'wifi_allow_list': current})
            Toast.info(self, '已移除', f'{removed} 不再自动连接。')

    def scan_and_add(self) -> None:
        def done(result: Any) -> None:
            found = [
                str(n.get('ssid', '')) for n in (result or {}).get('networks') or []
            ]
            current = list(self._config.get('wifi_allow_list') or [])
            from ...net import wifi as wifi_module
            added = []
            for ssid in found:
                if not ssid:
                    continue
                if any(wifi_module.ssid_matches(ssid, p) for p in current):
                    continue
                current.append(ssid)
                added.append(ssid)
            if added:
                self._save({'wifi_allow_list': current})
                Toast.success(self, '已加入名单', '、'.join(added))
            else:
                Toast.info(self, '没有新增', '附近没有发现新的网络。')

        run_async(self.service.scan_wifi, True, on_done=done)

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            '导出流量数据',
            'campusnet-traffic.csv',
            'CSV 文件 (*.csv)',
        )
        if not path:
            return
        run_async(self.service.export_traffic_csv, path, on_done=self._on_exported)

    def _on_exported(self, result: Any) -> None:
        if isinstance(result, dict) and result.get('ok'):
            Toast.success(self, '已导出', str(result.get('path', '')))
        else:
            Toast.error(
                self,
                '导出失败',
                str((result or {}).get('message', '未知错误')),
            )

    def reset_traffic(self) -> None:
        run_async(
            self.service.reset_traffic,
            on_done=lambda _r: Toast.info(self, '已重置', '流量历史已清空。'),
        )

    def reset_all(self) -> None:
        def done(_config: Any) -> None:
            Toast.info(self, '已重置', '全部设置已恢复默认。')
            self.reload()

        run_async(self.service.reset_config, on_done=done)

    def filter_rows(self, text: str) -> None:
        """按关键字显示/隐藏设置分组与自定义卡片。"""
        keyword = (text or '').strip().lower()

        def title_of(widget) -> str:
            label = getattr(widget, 'titleLabel', None) or getattr(
                widget, 'title_label', None
            )
            try:
                if label is not None:
                    return str(label.text())
                return ''
            except Exception:
                return ''

        for group, cards in self._groups:
            matched = False
            for card in cards:
                hit = not keyword or keyword in title_of(card).lower()
                card.setVisible(hit)
                matched = matched or hit
            group.setVisible(matched or not keyword)

        for card in self._loose:
            card.setVisible(not keyword or keyword in title_of(card).lower())

    def on_state(self, state: dict) -> None:
        patterns = list(self._config.get('wifi_allow_list') or [])
        active = (state or {}).get('ssid') or ''
        self._fill_ssid_rows(patterns, active)

    def _fill_ssid_rows(self, patterns: list[str], active_ssid: str) -> None:
        from ...net import wifi as wifi_module

        self.ssid_table.setRowCount(len(patterns))
        for row, pattern in enumerate(patterns):
            matched = bool(active_ssid) and wifi_module.ssid_matches(
                active_ssid, pattern
            )
            self.ssid_table.setItem(row, 0, QTableWidgetItem(pattern))
            self.ssid_table.setItem(
                row,
                1,
                QTableWidgetItem(
                    '当前已连接'
                    if matched
                    else '校园网'
                    if 'NYK' in pattern.upper()
                    else '自定义'
                ),
            )

    def on_theme_changed(self) -> None:
        pass

    def append_log(self, entry: dict) -> None:
        pass


def _app_version() -> str:
    """程序版本号（与 main.py 保持一致）。"""
    try:
        from main import __version__
        return str(__version__)
    except Exception:
        return '1.2.0'
