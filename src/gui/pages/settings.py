"""设置页：安卓式一级列表 + 二级分组页。

第一页是分组列表（通用 / 连接 / 流量与用量 / 通知与启动 / 关于），点一行
进入该组的滚动页，组内顶部有返回按钮回到列表。所有控件只构建一次，
切换分组不会重建控件，因此未保存的输入会原样保留，也不会触发保存。
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    HyperlinkButton,
    IconWidget,
    LineEdit,
    PushButton,
    TableWidget,
    TitleLabel,
    TransparentToolButton,
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
    caption_label,
    divider,
    scroll_page,
)

from ...core.deps import resource_path
from ...core.settings import APP_NAME_MAX, display_name

# qfluentwidgets 1.11 还没有 RENAME 图标；用同样表示「重命名」的 EDIT 顶上。
# 新版本一旦提供官方 FIF.RENAME，就优先使用官方图标。
if not hasattr(FIF, 'RENAME'):
    FIF.RENAME = FIF.EDIT

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

# 左侧一级导航：(标题, 图标)。索引与 QStackedWidget 的页序一一对应。
NAV_GROUPS = [
    ('通用', FIF.SETTING),
    ('连接', FIF.WIFI),
    ('流量与用量', FIF.SPEED_HIGH),
    ('通知与启动', FIF.MESSAGE),
    ('关于', FIF.INFO),
]

# 一级菜单每行的副标题（与 NAV_GROUPS 同序）。
MENU_SUBTITLES = [
    '账号、外观与主页内容',
    '自动连接、网络名单与认证',
    '流量统计、套餐额度与数据管理',
    '通知、启动方式与托盘',
    '版本、许可与开源致谢',
]


def _about_section(text: str, parent: QWidget | None = None) -> CaptionLabel:
    """「关于」卡片里的小栏目标题（弱化色 + 半粗）。"""
    label = CaptionLabel(text, parent)
    label.setTextColor(theme.color('text_muted'), theme.color('text_muted'))
    label.setStyleSheet('font-weight: 600;')
    return label


class _MenuRow(QFrame):
    """设置一级菜单里的一行：图标 + 标题/副标题 + 右侧箭头，整行可点。"""

    clicked = pyqtSignal()

    def __init__(self, title: str, subtitle: str, icon, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('SettingsMenuRow')
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(68)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 14, 10)
        layout.setSpacing(14)

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName('SettingsMenuIcon')
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setPixmap(icon.icon().pixmap(24, 24))
        self.icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.icon_label, 0, Qt.AlignVCenter)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        self.title_label = BodyLabel(title, self)
        self.title_label.setObjectName('SettingsMenuTitle')
        self.subtitle_label = CaptionLabel(subtitle, self)
        self.subtitle_label.setObjectName('SettingsMenuSubtitle')
        texts.addWidget(self.title_label)
        texts.addWidget(self.subtitle_label)
        layout.addLayout(texts, 1)

        self.chevron = QLabel(self)
        self.chevron.setObjectName('SettingsMenuChevron')
        self.chevron.setFixedSize(16, 16)
        self.chevron.setPixmap(FIF.RIGHT_ARROW.icon().pixmap(14, 14))
        self.chevron.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.chevron, 0, Qt.AlignVCenter)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SettingsPage(QWidget):
    """设置页（一级导航 + 二级分组页）。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('SettingsPage')
        self.service = service
        self._window = None
        self._config = {}
        # (Card, 该 Card 内的设置行)；供 filter_rows 使用。
        self._groups: list[tuple[QWidget, list[QWidget]]] = []
        # 一级菜单 / 二级分组页 / 每页滚动区与内容布局。
        self.menu_page: QWidget | None = None
        self.stack: QStackedWidget | None = None
        self._nav_buttons: list[_MenuRow] = []
        self._back_buttons: list[Any] = []
        self._pages: list[QWidget] = []
        self._scrolls: list[Any] = []
        self._page_layouts: list[QVBoxLayout] = []
        # 不参与分组高度修正的独立卡片（兼容旧代码）。
        self._loose: list[QWidget] = []
        # 回填配置期间抑制控件信号
        self._loading = True
        # 账户「保存并测试」流程进行中：按钮禁用并显示进度文案
        self._saving = False
        # -- 构建界面 --
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------
    # 界面骨架
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.stack = QStackedWidget(self)
        outer.addWidget(self.stack, 1)

        self._build_menu_page()
        self._build_general_page()
        self._build_connection_page()
        self._build_traffic_page()
        self._build_notification_page()
        self._build_about_page()

        self.stack.currentChanged.connect(self._on_group_changed)
        self._apply_nav_style()
        # 控件信号统一在最后连接，避免构建期触发保存
        self._wire()
        self.go_home()

    def _build_menu_page(self) -> None:
        """安卓式一级菜单：整页只有一个分组列表，点行进二级页。"""
        page = QWidget(self.stack)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll, layout = scroll_page(page, margins=(28, 16, 28, 16), spacing=14)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page_layout.addWidget(scroll)

        title = TitleLabel('设置', page)
        title.setObjectName('SettingsMenuHead')
        layout.addWidget(title)
        hint = CaptionLabel('选一个分组进去调整；每页顶部都能返回这里。', page)
        hint.setTextColor(theme.color('text_muted'), theme.color('text_muted'))
        layout.addWidget(hint)
        layout.addSpacing(4)

        card = Card('', '', page)
        card.vbox.setContentsMargins(0, 6, 0, 6)
        card.vbox.setSpacing(0)
        for index, (title_text, icon) in enumerate(NAV_GROUPS):
            row = _MenuRow(title_text, MENU_SUBTITLES[index], icon, card)
            row.clicked.connect(lambda _=False, i=index: self.switch_group(i))
            if index:
                line = QFrame(card)
                line.setObjectName('SettingsMenuDivider')
                line.setFixedHeight(1)
                card.vbox.addWidget(line)
            card.vbox.addWidget(row)
            self._nav_buttons.append(row)

        layout.addWidget(card)
        layout.addStretch(1)

        self.menu_page = page
        self.stack.addWidget(page)

    def _create_page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        """创建二级分组页：顶部返回条 + 滚动内容，返回 (页面, 内容布局)。"""
        page = QWidget(self.stack)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        header = QWidget(page)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 14, 28, 0)
        header_layout.setSpacing(10)

        back = TransparentToolButton(FIF.LEFT_ARROW, header)
        back.setObjectName('SettingsBackButton')
        back.setFixedSize(34, 34)
        back.setToolTip('返回设置')
        back.clicked.connect(self.go_home)
        header_layout.addWidget(back, 0, Qt.AlignTop)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        page_title = TitleLabel(title, header)
        page_title.setObjectName('SettingsGroupTitle')
        texts.addWidget(page_title)
        description = CaptionLabel(subtitle, header)
        description.setTextColor(theme.color('text_muted'), theme.color('text_muted'))
        texts.addWidget(description)
        header_layout.addLayout(texts, 1)
        page_layout.addWidget(header)

        scroll, layout = scroll_page(page, margins=(28, 10, 28, 16), spacing=14)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        page_layout.addWidget(scroll, 1)

        self.stack.addWidget(page)
        self._pages.append(page)
        self._scrolls.append(scroll)
        self._page_layouts.append(layout)
        self._back_buttons.append(back)
        return page, layout

    def _new_card(self, layout: QVBoxLayout, title: str, subtitle: str = '') -> Card:
        """在分组页里追加一张主题 Card。"""
        card = Card(title, subtitle, parent=self.stack)
        layout.addWidget(card)
        return card

    # ------------------------------------------------------------------
    # 「通用」分组
    # ------------------------------------------------------------------
    def _build_general_page(self) -> None:
        _page, layout = self._create_page('通用', '账户、外观与主页等日常设置')

        account = self._new_card(layout, '认证账户', '账号密码仅加密保存在本机')
        self.username_row = TextRow(
            FIF.PEOPLE,
            '校园网账号',
            '校园卡手机号，只填号码本身',
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
        # 对外沿用保存 / 测试按钮的旧名字（当前由同一个按钮完成两步）。
        self.save_button = self.save_account_row.button
        self.test_button = self.save_account_row.button
        for row in (self.username_row, self.password_row, self.save_account_row):
            account.add(row)
        self._register(
            account,
            [self.username_row, self.password_row, self.save_account_row],
        )

        appearance = self._new_card(layout, '外观', '程序名称、主题与窗口材质')
        self.app_name_row = TextRow(
            FIF.RENAME,
            '程序名称',
            '自定义窗口与托盘里显示的名字，留空则用默认名称',
            placeholder='留空则使用 CampusNet',
            parent=appearance,
        )
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
        for row in (self.app_name_row, self.theme_row, self.mica_row):
            appearance.add(row)
        self._register(appearance, [self.app_name_row, self.theme_row, self.mica_row])

        home = self._new_card(layout, '主页内容', '首页卡片的显示开关')
        self.home_notice_row = SwitchRow(
            FIF.MESSAGE,
            '主页显示校园网公告',
            '关闭公告卡后，这里重新打开会清掉关闭记录并再读一次',
            parent=home,
        )
        self.home_package_row = SwitchRow(
            FIF.CHAT,
            '主页显示学校套餐',
            '在主页展示套餐名称、到期时间与流量（来自学校自助系统）',
            parent=home,
        )
        for row in (self.home_notice_row, self.home_package_row):
            home.add(row)
        self._register(home, [self.home_notice_row, self.home_package_row])

        maintenance = self._new_card(layout, '维护', '数据目录与恢复出厂设置')
        open_dir = ButtonRow(
            FIF.FOLDER,
            '数据目录',
            '配置、流量历史与内置浏览器数据都放在这里',
            '打开目录',
            parent=maintenance,
        )
        open_dir.clicked.connect(lambda: self.service.open_data_dir())
        reset_all = ButtonRow(
            FIF.DELETE,
            '重置全部设置',
            '恢复出厂配置（账号密码也会清除）',
            '重置设置',
            parent=maintenance,
        )
        reset_all.clicked.connect(self.reset_all)
        maintenance.add(open_dir)
        maintenance.add(reset_all)
        self._register(maintenance, [open_dir, reset_all])
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # 「连接」分组
    # ------------------------------------------------------------------
    def _build_connection_page(self) -> None:
        _page, layout = self._create_page('连接', '自动连接、网络名单与 Portal 认证')

        auto = self._new_card(layout, '自动连接与认证', '进入校园网后的自动化行为')
        self.auto_connect_row = SwitchRow(
            FIF.WIFI, '自动连接校园网', '检测到校园网 SSID 时自动连接', parent=auto
        )
        self.auto_login_row = SwitchRow(
            FIF.CONNECT, '自动提交认证', '检测到认证页面时自动登录', parent=auto
        )
        self.auto_retry_row = SwitchRow(
            FIF.SYNC, '断线自动重试', '网络掉线后按下面的次数与间隔重试', parent=auto
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
        for row in (
            self.auto_connect_row,
            self.auto_login_row,
            self.auto_retry_row,
            self.auto_reconnect_row,
            self.kick_row,
        ):
            auto.add(row)
        self._register(
            auto,
            [
                self.auto_connect_row,
                self.auto_login_row,
                self.auto_retry_row,
                self.auto_reconnect_row,
                self.kick_row,
            ],
        )

        self.ssid_card = Card(
            '自动连接的网络名单',
            '支持写法互通：CMCC-NYKJ 与 CMCCNYKJ 视为同一个网络',
            parent=self.stack,
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
        layout.addWidget(self.ssid_card)
        self._register(self.ssid_card, [])

        portal = self._new_card(layout, 'Portal 认证', '认证驱动与门户地址')
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
        self.probe_url_row = TextRow(
            FIF.SEARCH,
            '自定义探测地址',
            '留空则使用内置的联网探测地址；%T 会被替换成时间戳',
            placeholder='留空 = 自动探测',
            parent=portal,
        )
        for row in (
            self.driver_row,
            self.acip_row,
            self.portal_url_row,
            self.probe_url_row,
        ):
            portal.add(row)
        self._register(
            portal,
            [
                self.driver_row,
                self.acip_row,
                self.portal_url_row,
                self.probe_url_row,
            ],
        )

        timing = self._new_card(layout, '重试与超时', '通常保持默认即可')
        self.retry_count_row = SpinRow(
            FIF.SYNC,
            '重试次数',
            '自动重试的最大次数',
            minimum=0,
            maximum=20,
            parent=timing,
        )
        self.retry_delay_row = SpinRow(
            FIF.HISTORY,
            '重试间隔',
            '每次重试之间的等待时间',
            minimum=1,
            maximum=300,
            suffix='秒',
            parent=timing,
        )
        self.auth_timeout_row = SpinRow(
            FIF.CERTIFICATE,
            '认证超时',
            '单次认证请求的等待上限',
            minimum=3000,
            maximum=60000,
            suffix='毫秒',
            parent=timing,
        )
        self.wifi_wait_row = SpinRow(
            FIF.WIFI,
            '连接等待',
            '点击连接后等待 Wi-Fi 关联的时间',
            minimum=500,
            maximum=30000,
            suffix='毫秒',
            parent=timing,
        )
        self.reconnect_delay_row = SpinRow(
            FIF.CONNECT,
            '重连延迟',
            '掉线自动重连前的等待时间',
            minimum=200,
            maximum=60000,
            suffix='毫秒',
            parent=timing,
        )
        for row in (
            self.retry_count_row,
            self.retry_delay_row,
            self.auth_timeout_row,
            self.wifi_wait_row,
            self.reconnect_delay_row,
        ):
            timing.add(row)
        self._register(
            timing,
            [
                self.retry_count_row,
                self.retry_delay_row,
                self.auth_timeout_row,
                self.wifi_wait_row,
                self.reconnect_delay_row,
            ],
        )
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # 「流量与用量」分组
    # ------------------------------------------------------------------
    def _build_traffic_page(self) -> None:
        _page, layout = self._create_page('流量与用量', '采样、套餐额度与数据管理')

        traffic = self._new_card(layout, '流量统计', '采样频率与历史保留')
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
        self.retention_row = SpinRow(
            FIF.CALENDAR,
            '历史保留天数',
            '超过该天数的按小时记录会被清理',
            minimum=7,
            maximum=3650,
            suffix='天',
            parent=traffic,
        )
        for row in (self.interval_row, self.traffic_only_row, self.retention_row):
            traffic.add(row)
        self._register(
            traffic,
            [self.interval_row, self.traffic_only_row, self.retention_row],
        )

        quota = self._new_card(layout, '套餐额度', '主页套餐进度与月度重置')
        self.quota_auto_row = SwitchRow(
            FIF.SYNC,
            '自动读取套餐额度',
            '从学校自助系统读取本月总额度；「不限量」套餐会自动置 0',
            parent=quota,
        )
        self.quota_row = DoubleSpinRow(
            FIF.CHAT,
            '每月套餐流量',
            '填写后主页会显示套餐进度；0 表示不统计配额',
            minimum=0,
            maximum=100000,
            suffix='GB',
            parent=quota,
        )
        self.monthly_start_row = SpinRow(
            FIF.CALENDAR,
            '月度起始日',
            '每月从这一天开始重新累计套餐用量',
            minimum=1,
            maximum=28,
            suffix='日',
            parent=quota,
        )
        for row in (self.quota_auto_row, self.quota_row, self.monthly_start_row):
            quota.add(row)
        self._register(
            quota,
            [self.quota_auto_row, self.quota_row, self.monthly_start_row],
        )

        data = self._new_card(layout, '数据管理', '导出或清空本机流量历史')
        export_row = ButtonRow(
            FIF.SAVE,
            '导出流量数据',
            '把按小时的用量导出成 CSV',
            '导出 CSV',
            parent=data,
        )
        export_row.clicked.connect(self.export_csv)
        reset_row = ButtonRow(
            FIF.DELETE,
            '重置流量统计',
            '清空全部历史用量记录',
            '重置',
            parent=data,
        )
        reset_row.clicked.connect(self.reset_traffic)
        data.add(export_row)
        data.add(reset_row)
        self._register(data, [export_row, reset_row])
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # 「通知与启动」分组
    # ------------------------------------------------------------------
    def _build_notification_page(self) -> None:
        _page, layout = self._create_page('通知与启动', '系统通知、开机启动与开发者选项')

        notify = self._new_card(layout, '通知', '认证结果、掉线与账号冲突提示')
        self.notify_row = SwitchRow(
            FIF.INFO,
            '系统通知',
            '总开关：认证结果、掉线、账号冲突等提示都由它控制',
            parent=notify,
        )
        self.notify_drop_row = SwitchRow(
            FIF.INFO,
            '掉线时发通知',
            '被其它设备挤下线、学校侧断会话或手动退出时，弹 Windows 通知',
            parent=notify,
        )
        self.notify_app_name_row = TextRow(
            FIF.RENAME,
            '通知中的应用名',
            'Windows 通知标题里显示的程序名，留空则使用「校园网」',
            placeholder='留空则使用 校园网',
            parent=notify,
        )
        self.notice_refresh_row = SpinRow(
            FIF.SYNC,
            '公告刷新间隔',
            '主页校园网公告的自动刷新周期',
            minimum=1,
            maximum=240,
            suffix='分钟',
            parent=notify,
        )
        for row in (
            self.notify_row,
            self.notify_drop_row,
            self.notify_app_name_row,
            self.notice_refresh_row,
        ):
            notify.add(row)
        self._register(
            notify,
            [
                self.notify_row,
                self.notify_drop_row,
                self.notify_app_name_row,
                self.notice_refresh_row,
            ],
        )

        startup = self._new_card(layout, '启动与托盘', '开机启动与关闭行为')
        self.auto_launch_row = SwitchRow(
            FIF.UPDATE,
            '开机自动启动',
            '写入当前用户的启动项，登录后自动运行',
            parent=startup,
        )
        self.start_min_row = SwitchRow(
            FIF.HOME,
            '启动后最小化',
            '开机自启时直接缩到托盘，不打扰',
            parent=startup,
        )
        self.tray_row = SwitchRow(
            FIF.CLOSE,
            '关闭时最小化到托盘',
            '关闭窗口不退出程序，继续后台守护',
            parent=startup,
        )
        for row in (self.auto_launch_row, self.start_min_row, self.tray_row):
            startup.add(row)
        self._register(
            startup,
            [self.auto_launch_row, self.start_min_row, self.tray_row],
        )

        advanced = self._new_card(layout, '高级', '面向调试与排障的选项')
        self.developer_row = SwitchRow(
            FIF.ROBOT,
            '开发者模式',
            '在左侧导航底部显示「开发者」页：可逐个调用接口并查看原始返回',
            parent=advanced,
        )
        advanced.add(self.developer_row)
        self._register(advanced, [self.developer_row])
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # 「关于」分组
    # ------------------------------------------------------------------
    def _build_about_page(self) -> None:
        page, layout = self._create_page('关于', '版本、许可证与开源致谢')

        self.about_card = Card('关于', parent=page)

        # 品牌区：图标在左、纵向居中于「标题 + 一句话简介」两行之间，右侧文字两行左对齐
        brand = QHBoxLayout()
        brand.setContentsMargins(0, 2, 0, 0)
        brand.setSpacing(14)

        icon_size = 52
        app_icon = IconWidget(QIcon(resource_path('app.ico')), self.about_card)
        app_icon.setFixedSize(icon_size, icon_size)
        brand.addWidget(app_icon, 0, Qt.AlignVCenter)

        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        self.about_name_label = TitleLabel(display_name(''), self.about_card)
        title_row.addWidget(self.about_name_label, 0, Qt.AlignVCenter)
        version_chip = CaptionLabel(f'v{_app_version()}', self.about_card)
        version_chip.setStyleSheet(
            f'color: {theme.color("accent")};'
            f'background-color: {theme.color("accent_soft")};'
            'border-radius: 9px; padding: 2px 8px;'
        )
        title_row.addWidget(version_chip, 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        brand_text.addLayout(title_row)
        brand_text.addWidget(
            body_label(
                '校园网自动连接、认证与流量统计，一个窗口完成。',
                parent=self.about_card,
            )
        )
        brand.addLayout(brand_text, 1)
        self.about_card.add_layout(brand)
        self.about_card.add(divider(self.about_card))

        # 版本信息与链接并排，致谢放在下一行，避免窄窗口下三栏被压扁。
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(36)

        info = QWidget(self.about_card)
        info_box = QVBoxLayout(info)
        info_box.setContentsMargins(0, 0, 0, 0)
        info_box.setSpacing(10)
        info_box.addWidget(_about_section('版本信息', info))
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
            parent=info,
        )
        info_box.addWidget(self.about_grid)
        info_box.addStretch(1)
        info.setMinimumWidth(260)
        info.setMaximumWidth(420)
        top.addWidget(info, 0, Qt.AlignTop)

        links = QWidget(self.about_card)
        links_box = QVBoxLayout(links)
        links_box.setContentsMargins(0, 0, 0, 0)
        links_box.setSpacing(6)
        links_box.addWidget(_about_section('链接与许可', links))
        self.github_link = HyperlinkButton(
            FIF.GITHUB, GITHUB_URL, '项目主页', parent=links
        )
        self.github_link.setToolTip(GITHUB_URL)
        self.github_link.setMinimumWidth(150)
        links_box.addWidget(self.github_link)
        license_button = PushButton(FIF.CERTIFICATE, 'MIT 许可证', links)
        license_button.setToolTip('打开项目根目录的 LICENSE 文件')
        license_button.setMinimumWidth(150)
        license_button.clicked.connect(self._open_license)
        links_box.addWidget(license_button)
        issues_url = f'{GITHUB_URL}/issues'
        feedback_link = HyperlinkButton(
            FIF.FEEDBACK, issues_url, '问题反馈', parent=links
        )
        feedback_link.setToolTip(issues_url)
        feedback_link.setMinimumWidth(150)
        links_box.addWidget(feedback_link)
        links_box.addStretch(1)
        top.addWidget(links, 0, Qt.AlignTop)
        top.addStretch(1)
        self.about_card.add_layout(top)

        self.about_card.add(divider(self.about_card))

        thanks = QWidget(self.about_card)
        thanks_box = QVBoxLayout(thanks)
        thanks_box.setContentsMargins(0, 0, 0, 0)
        thanks_box.setSpacing(10)
        thanks_box.addWidget(_about_section('开源致谢', thanks))
        ack_grid = QGridLayout()
        ack_grid.setContentsMargins(0, 0, 0, 0)
        ack_grid.setHorizontalSpacing(12)
        ack_grid.setVerticalSpacing(6)
        for row, (name, license_name, url) in enumerate(ACKNOWLEDGEMENTS):
            name_label = body_label(name, wrap=False, parent=thanks)
            ack_grid.addWidget(name_label, row, 0)
            license_label = caption_label(license_name, parent=thanks)
            license_label.setWordWrap(False)
            ack_grid.addWidget(license_label, row, 1)
            link = HyperlinkButton(url, '源码', parent=thanks)
            link.setToolTip(url)
            ack_grid.addWidget(link, row, 2)
        ack_grid.setColumnStretch(0, 1)
        thanks_box.addLayout(ack_grid)
        thanks_box.addStretch(1)
        self.about_card.add(thanks)

        layout.addWidget(self.about_card)
        self._register(self.about_card, [])
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # 导航与主题
    # ------------------------------------------------------------------
    def switch_group(self, index: int) -> None:
        """进入第 ``index`` 个分组页（一级菜单是第 0 页）。"""
        if self.stack is None or not 0 <= index < len(self._nav_buttons):
            return
        self.stack.setCurrentIndex(index + 1)

    def go_home(self) -> None:
        """回到一级菜单列表。"""
        if self.stack is not None:
            self.stack.setCurrentIndex(0)

    def current_group(self) -> int:
        """当前所在分组；在一级菜单时返回 -1。"""
        return self.stack.currentIndex() - 1 if self.stack is not None else -1

    def _on_group_changed(self, index: int) -> None:
        if index <= 0:
            return
        self._apply_nav_style()

    def _apply_nav_style(self) -> None:
        """按当前主题给一级菜单与返回按钮刷色。"""
        accent = theme.color('accent')
        soft = theme.color('accent_soft')
        text = theme.color('text')
        muted = theme.color('text_muted')
        border = theme.color('card_border')
        for row in self._nav_buttons:
            row.title_label.setTextColor(text, text)
            row.title_label.setStyleSheet('font-size: 15px; font-weight: 600;')
            row.subtitle_label.setTextColor(muted, muted)
        for button in self._back_buttons:
            button.setStyleSheet(
                f"QToolButton {{ background: transparent; border: none; border-radius: 8px; }}"
                f"QToolButton:hover {{ background: {soft}; }}"
            )
        if self.menu_page is not None:
            self.menu_page.setStyleSheet(
                f"""
#SettingsMenuRow {{
    background: transparent;
    border-radius: 10px;
}}
#SettingsMenuRow:hover {{
    background: {soft};
}}
#SettingsMenuDivider {{
    background: {border};
    border: none;
    margin: 0 16px;
}}
#SettingsMenuTitle {{
    color: {text};
}}
#SettingsMenuSubtitle {{
    color: {muted};
}}
"""
            )

    def _register(self, group: QWidget, cards: list[QWidget]) -> None:
        self._groups.append((group, cards))

    # ------------------------------------------------------------------
    # 配置绑定
    # ------------------------------------------------------------------
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
        on(
            self.probe_url_row.edit.editingFinished,
            lambda: self._save({'custom_probe_url': self.probe_url_row.text().strip()}),
        )
        on(self.retry_count_row.changed, lambda v: self._save({'retry_count': int(v)}))
        on(
            self.retry_delay_row.changed,
            lambda v: self._save({'retry_delay_ms': int(v) * 1000}),
        )
        on(
            self.auth_timeout_row.changed,
            lambda v: self._save({'auth_timeout_ms': int(v)}),
        )
        on(
            self.wifi_wait_row.changed,
            lambda v: self._save({'wifi_connect_wait_ms': int(v)}),
        )
        on(
            self.reconnect_delay_row.changed,
            lambda v: self._save({'reconnect_delay_ms': int(v)}),
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
            self.monthly_start_row.changed,
            lambda v: self._save({'monthly_start_day': int(v)}),
        )
        on(
            self.retention_row.changed,
            lambda v: self._save({'traffic_retention_days': int(v)}),
        )
        on(self.theme_row.changed, self._on_theme)
        on(self.mica_row.changed, self._on_mica)
        on(self.tray_row.changed, lambda v: self._save({'close_to_tray': v}))
        on(self.notify_row.changed, lambda v: self._save({'show_notification': v}))
        on(
            self.notify_app_name_row.edit.editingFinished,
            lambda: self._save(
                {'notify_app_name': self.notify_app_name_row.text().strip()}
            ),
        )
        on(
            self.notice_refresh_row.changed,
            lambda v: self._save({'notice_refresh_minutes': int(v)}),
        )
        on(self.auto_launch_row.changed, self._on_auto_launch)
        on(self.start_min_row.changed, lambda v: self._save({'start_minimized': v}))
        on(self.home_package_row.changed, self._on_home_package)
        on(self.home_notice_row.changed, self._on_home_notice)
        on(self.developer_row.changed, self._on_developer_mode)
        on(
            self.app_name_row.edit.editingFinished,
            lambda: self._on_app_name(self.app_name_row.text()),
        )

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
        self.probe_url_row.set_text(str(config.get('custom_probe_url') or ''))
        self.retry_count_row.set_value(int(config.get('retry_count') or 0))
        self.retry_delay_row.set_value(int((config.get('retry_delay_ms') or 8000) / 1000))
        self.auth_timeout_row.set_value(int(config.get('auth_timeout_ms') or 12000))
        self.wifi_wait_row.set_value(
            int(config.get('wifi_connect_wait_ms') or 2500)
        )
        self.reconnect_delay_row.set_value(
            int(config.get('reconnect_delay_ms') or 1500)
        )

        self.interval_row.set_value(int((config.get('traffic_interval_ms') or 5000) / 1000))
        self.traffic_only_row.set_checked(
            bool(config.get('traffic_only_campus', True))
        )
        auto_quota = bool(config.get('quota_auto', True))
        self.quota_auto_row.set_checked(auto_quota)
        self.quota_row.set_value(float(config.get('monthly_quota_gb') or 0))
        self.quota_row.setEnabled(not auto_quota)
        self.monthly_start_row.set_value(int(config.get('monthly_start_day') or 1))
        self.retention_row.set_value(int(config.get('traffic_retention_days') or 120))

        self.theme_row.set_value(str(config.get('theme') or 'auto'))
        self.app_name_row.set_text(str(config.get('app_name') or ''))
        self.mica_row.set_checked(bool(config.get('mica_effect')))
        self.tray_row.set_checked(bool(config.get('close_to_tray')))
        self.notify_row.set_checked(bool(config.get('show_notification')))
        self.notify_app_name_row.set_text(str(config.get('notify_app_name') or ''))
        self.notice_refresh_row.set_value(
            int(config.get('notice_refresh_minutes') or 10)
        )
        self.auto_launch_row.set_checked(bool(config.get('auto_launch')))
        self.start_min_row.set_checked(bool(config.get('start_minimized')))
        self.home_package_row.set_checked(bool(config.get('home_show_package', True)))
        self.home_notice_row.set_checked(bool(config.get('home_show_notice', True)))
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

        if hasattr(self, 'about_name_label'):
            self.about_name_label.setText(
                display_name(str(config.get('app_name') or ''))
            )
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
        self._apply_nav_style()
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

    def _on_home_notice(self, value: bool) -> None:
        patch = {'home_show_notice': value}
        if value:
            patch['notice_dismissed'] = ''
        self._save(patch)
        if self._window is not None and hasattr(
            self._window.home_page, 'set_notice_visible'
        ):
            self._window.home_page.set_notice_visible(value)

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

    def _on_app_name(self, value: str) -> None:
        """保存自定义程序名；留空则恢复默认名称。"""
        text = (value or '').strip()[:APP_NAME_MAX]
        if text == str(self._config.get('app_name') or ''):
            return

        def done(_config: Any) -> None:
            # 关于卡片里的大标题即时跟随；窗口标题与托盘提示下次启动生效。
            self.about_name_label.setText(
                display_name(str(self._config.get('app_name') or ''))
            )
            Toast.success(self, '已保存', '程序名称将在下次启动时生效')

        self._save({'app_name': text}, on_done=done)

    def save_and_test(self) -> None:
        if self._saving:
            return
        raw = self.username_row.text().strip()
        # 只拦截真正会污染账号的提示词；「手机号 / 校园卡」是正常描述，不当作脏字符。
        for bad in ('例如', '请输入', '请填写', '账号：', '账号:'):
            if bad in raw:
                Toast.warning(
                    self,
                    '账号格式不对',
                    f'这里要填号码本身，不要带「{bad}」这类提示文字。',
                )
                return
        username = ''.join(ch for ch in raw if ch.isascii() and ch.isalnum())
        if username != raw:
            self._loading = True
            try:
                self.username_row.set_text(username)
            finally:
                self._loading = False
        if not username or (not username.isdigit() and len(username) < 4):
            Toast.warning(self, '请先填写账号', '校园网账号是校园卡手机号，只填号码本身（数字）。')
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
        """按关键字显示/隐藏各分组页里的卡片与设置行。"""
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

        for card, rows in self._groups:
            card_hit = keyword in title_of(card).lower()
            matched = card_hit or not keyword
            for row in rows:
                hit = card_hit or not keyword or keyword in title_of(row).lower()
                row.setVisible(hit)
                matched = matched or hit
            card.setVisible(matched)

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
        self._apply_nav_style()

    def append_log(self, entry: dict) -> None:
        pass


def _app_version() -> str:
    """程序版本号（与 main.py 保持一致）。"""
    try:
        from main import __version__

        return str(__version__)
    except Exception:
        return '1.8.0'
