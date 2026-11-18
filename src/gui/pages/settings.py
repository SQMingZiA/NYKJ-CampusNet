"""设置页：一级是四个大板块的列表，点进去才是该板块的二级页面。

一级（``SettingsMenuPage``）：连接 / 界面 / 用量 / 关于，每行一个 Fluent 列表条目
（图标 + 标题 + 一句说明 + 右箭头），没有大标题，整页就是一条列表。
二级（``SettingsSectionPage``）：顶部一条返回栏（32px 返回箭头 + 18px 板块名 + 一句说明），
下面是该板块的卡片。全部控件只构建一次，切换只改 ``QStackedWidget`` 索引，
不重建控件、不会触发保存。
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QIcon
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
    CardWidget,
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
GITHUB_URL = "https://github.com/SQMingZiA/NYKJ-CampusNet"

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

# 分组小节：(标题, 图标)，按此顺序自上而下排布。
NAV_GROUPS = [
    ('连接', FIF.WIFI),
    ('界面', FIF.PALETTE),
    ('用量', FIF.SPEED_HIGH),
]

# 一级列表里每行的一句话说明，与 NAV_GROUPS 同序。
MENU_SUBTITLES = [
    '账号、自动连接、网络名单与认证参数',
    '外观、主页内容与通知',
    '流量统计、套餐额度与数据维护',
]

def _wrap_path(path: str) -> str:
    """整段显示路径：在每个分隔符后插入零宽空格，长路径也能自动折行。"""
    text = (path or "").strip()
    if not text:
        return "-"
    pieces: list[str] = []
    for char in text:
        pieces.append(char)
        if char in "\\/":
            pieces.append("\u200b")
    return "".join(pieces)


def _about_section(text: str, parent: QWidget | None = None) -> CaptionLabel:
    """「关于」卡片里的小栏目标题（弱化色 + 半粗）。"""
    label = CaptionLabel(text, parent)
    label.setTextColor(theme.color('text_muted'), theme.color('text_muted'))
    label.setStyleSheet('font-weight: 600;')
    return label


class _MenuRow(CardWidget):
    """一级列表的一行：图标 + 标题 + 说明 + 右箭头，整行可点。"""

    clicked = pyqtSignal()

    def __init__(self, icon, title: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon = icon
        self.setObjectName('SettingsMenuRow')
        self.setClickEnabled(True)
        self.setBorderRadius(6)
        self.setFixedHeight(64)
        self.setCursor(Qt.PointingHandCursor)

        box = QHBoxLayout(self)
        box.setContentsMargins(16, 10, 14, 10)
        box.setSpacing(14)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignCenter)
        box.addWidget(self.icon_label, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(0)
        self.title_label = BodyLabel(title, self)
        self.title_label.setStyleSheet('font-size: 15px; font-weight: 600;')
        self.subtitle_label = CaptionLabel(subtitle, self)
        self.subtitle_label.setWordWrap(False)
        text.addWidget(self.title_label)
        text.addWidget(self.subtitle_label)
        box.addLayout(text, 1)

        self.arrow_label = QLabel(self)
        self.arrow_label.setFixedSize(12, 12)
        self.arrow_label.setAlignment(Qt.AlignCenter)
        box.addWidget(self.arrow_label, 0, Qt.AlignVCenter)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def refresh_theme(self) -> None:
        text = theme.color('text')
        secondary = theme.color('text_secondary')
        self.title_label.setTextColor(text, text)
        self.subtitle_label.setTextColor(secondary, secondary)
        self.icon_label.setPixmap(self._icon.icon(color=QColor(secondary)).pixmap(20, 20))
        self.arrow_label.setPixmap(FIF.CHEVRON_RIGHT.icon(color=QColor(secondary)).pixmap(12, 12))


class SettingsPage(QWidget):
    """设置页：一级板块列表 + 四个二级页面。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('SettingsPage')
        self.service = service
        self._window = None
        self._config = {}
        # (Card, 该 Card 内的设置行)；供 filter_rows 使用。
        self._groups: list[tuple[QWidget, list[QWidget]]] = []
        # 一级列表 / 二级页面容器
        self.stack: Any = None
        self.menu_page: Any = None
        self.menu_scroll: Any = None
        self._menu_rows: list[_MenuRow] = []
        # 兼容旧代码：scroll 指向一级列表的滚动区
        self.scroll: Any = None
        # 各二级页面与小节标题（供主题刷新与定位）
        self._sections: list[QWidget] = []
        self._section_labels: list[BodyLabel] = []
        self._current_group = -1
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
        self.stack.setObjectName('SettingsStack')
        outer.addWidget(self.stack, 1)

        self.menu_page = self._build_menu_page()
        self.stack.addWidget(self.menu_page)
        self.scroll = self.menu_scroll

        self._build_connection_page()
        self._build_interface_page()
        self._build_usage_page()
        self._build_about_block()
        self._menu_layout.addStretch(1)

        self._apply_nav_style()
        # 控件信号统一在最后连接，避免构建期触发保存
        self._wire()
        self.go_home()

    def _build_menu_page(self) -> QWidget:
        """一级页面：四个大板块的列表。"""
        page = QWidget(self)
        page.setObjectName('SettingsMenuPage')
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll, content = scroll_page(page, margins=(24, 16, 24, 24), spacing=8)
        scroll.setObjectName('SettingsMenuScroll')
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)
        self.menu_scroll = scroll

        self._menu_rows = []
        for index, (title, icon) in enumerate(NAV_GROUPS):
            subtitle = MENU_SUBTITLES[index] if index < len(MENU_SUBTITLES) else ''
            row = _MenuRow(icon, title, subtitle, page)
            row.clicked.connect(lambda i=index: self.switch_group(i))
            content.addWidget(row)
            self._menu_rows.append(row)
        self._menu_layout = content
        return page

    def _create_page(self, title: str, subtitle: str = '') -> tuple[QWidget, QVBoxLayout]:
        """创建一个二级页面：顶部返回栏 + 该板块的卡片。"""
        page = QWidget(self)
        page.setObjectName('SettingsSectionPage')
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget(page)
        head.setObjectName('SettingsPageHead')
        head_box = QHBoxLayout(head)
        head_box.setContentsMargins(16, 8, 24, 2)
        head_box.setSpacing(6)

        back = TransparentToolButton(FIF.LEFT_ARROW, head)
        back.setObjectName('SettingsBackButton')
        back.setFixedSize(32, 32)
        back.setIconSize(QSize(16, 16))
        back.setToolTip('返回设置')
        back.clicked.connect(self.go_home)
        head_box.addWidget(back, 0, Qt.AlignVCenter)

        title_label = BodyLabel(title, head)
        title_label.setObjectName('SettingsSectionTitle')
        if subtitle:
            title_label.setToolTip(subtitle)
        head_box.addWidget(title_label, 0, Qt.AlignVCenter)
        head_box.addStretch(1)
        outer.addWidget(head)

        scroll, content = scroll_page(page, margins=(24, 4, 24, 24), spacing=14)
        scroll.setObjectName('SettingsScroll')
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        self.stack.addWidget(page)
        self._sections.append(page)
        self._section_labels.append(title_label)
        return page, content

    def _new_card(self, layout: QVBoxLayout, title: str, subtitle: str = '') -> Card:
        """在小节里追加一张主题 Card。"""
        card = Card(title, subtitle)
        layout.addWidget(card)
        return card

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 「连接」分组
    # ------------------------------------------------------------------
    def _build_connection_page(self) -> None:
        _page, layout = self._create_page('连接', '账号、自动连接、网络名单与认证参数')

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
            '网络名单',
            '支持写法互通：CMCC-NYKJ 与 CMCCNYKJ 视为同一个网络',
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

        # 认证方式与等待时间合成一张卡：都是「怎么连、等多久」。
        portal = self._new_card(layout, '认证方式与超时', '门户地址、认证驱动与各项等待时间')
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

        timing = portal  # 超时参数并入同一张卡，少一个分组
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

    # ------------------------------------------------------------------
    # 「流量与用量」分组

    # ------------------------------------------------------------------
    # 「界面」分组
    # ------------------------------------------------------------------
    def _build_interface_page(self) -> None:
        _page, layout = self._create_page('界面', '外观、主页内容与通知')

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

        notify = self._new_card(layout, '通知与托盘', '通知内容、公告刷新与关闭行为')
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

        startup = notify  # 启动与托盘并入通知卡，少一个分组
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

    # ------------------------------------------------------------------
    # 「用量」分组
    # ------------------------------------------------------------------
    def _build_usage_page(self) -> None:
        _page, layout = self._create_page('用量', '流量统计、套餐额度与数据维护')

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

        data = self._new_card(layout, '数据与高级', '导出、清空数据与调试选项')
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

    # ------------------------------------------------------------------
    # 「通知与启动」分组
    # ------------------------------------------------------------------

        maintenance = data  # 数据目录与重置并入同一张卡
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

        advanced = data  # 开发者模式并入「数据与维护」
        self.developer_row = SwitchRow(
            FIF.ROBOT,
            '开发者模式',
            '在左侧导航底部显示「开发者」页：可逐个调用接口并查看原始返回',
            parent=advanced,
        )
        advanced.add(self.developer_row)
        self._register(advanced, [self.developer_row])

    # ------------------------------------------------------------------
    # 「连接」分组

    # ------------------------------------------------------------------

    # 「关于」分组
    # ------------------------------------------------------------------
    def _build_about_block(self) -> None:
        """一级页面底部直接放「关于」板块，不再单独占一个二级页。"""
        layout = self._menu_layout
        self.about_card = Card('关于', parent=self.menu_page)

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

        # 版本信息、链接、致谢竖排左对齐，和其它二级页的排法一致。
        self.about_card.add(_about_section('版本信息', self.about_card))
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
        for key, value_label in self.about_grid._values.items():
            value_label.setMaximumWidth(420)
            if key == 'data':
                # 数据目录整段显示：给足宽度并允许在分隔符后折行
                value_label.setMinimumWidth(480)
                value_label.setMaximumWidth(900)
                value_label.setWordWrap(True)
        self.about_card.add(self.about_grid)
        self.about_card.add(divider(self.about_card))

        self.about_card.add(_about_section('链接与许可', self.about_card))
        links_row = QHBoxLayout()
        links_row.setContentsMargins(0, 0, 0, 0)
        links_row.setSpacing(12)
        self.github_link = HyperlinkButton(
            FIF.GITHUB, GITHUB_URL, '项目主页', parent=self.about_card
        )
        self.github_link.setToolTip(GITHUB_URL)
        links_row.addWidget(self.github_link, 0, Qt.AlignVCenter)
        license_button = PushButton(FIF.CERTIFICATE, 'MIT 许可证', self.about_card)
        license_button.setToolTip('打开项目根目录的 LICENSE 文件')
        license_button.clicked.connect(self._open_license)
        links_row.addWidget(license_button, 0, Qt.AlignVCenter)
        issues_url = f'{GITHUB_URL}/issues'
        feedback_link = HyperlinkButton(
            FIF.FEEDBACK, issues_url, '问题反馈', parent=self.about_card
        )
        feedback_link.setToolTip(issues_url)
        links_row.addWidget(feedback_link, 0, Qt.AlignVCenter)
        links_row.addStretch(1)
        self.about_card.add_layout(links_row)
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

    # ------------------------------------------------------------------
    # 导航与主题
    # ------------------------------------------------------------------
    def switch_group(self, index: int) -> None:
        """打开第 ``index`` 个板块的二级页面（找不到就忽略）。"""
        if not 0 <= index < len(self._sections):
            return
        self._current_group = index
        self.stack.setCurrentIndex(index + 1)
        self._apply_nav_style()

    def go_home(self) -> None:
        """回到设置首页（一级板块列表）。"""
        self._current_group = -1
        self.stack.setCurrentIndex(0)
        self._apply_nav_style()

    def current_group(self) -> int:
        """当前打开的板块；停留在一级列表时返回 -1。"""
        return self._current_group

    def _apply_nav_style(self) -> None:
        """按当前主题刷新一级条目与二级页头。"""
        text = theme.color('text')
        secondary = theme.color('text_secondary')
        for row in self._menu_rows:
            row.refresh_theme()
        for label in self._section_labels:
            label.setTextColor(text, text)
            label.setStyleSheet('font-size: 18px; font-weight: 600;')
        for page in self._sections:
            back = page.findChild(TransparentToolButton, 'SettingsBackButton')
            if back is not None:
                back.setStyleSheet(
                    'QToolButton{border-radius:4px;}'
                    'QToolButton:hover{background:%s;}'
                    'QToolButton:pressed{background:%s;}'
                    % (theme.color('accent_soft'), theme.color('card_border'))
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
        self.about_grid._values['data'].setToolTip(str(config.get('data_dir') or ''))
        self.about_grid.update_values(
            {
                'version': _app_version(),
                'qt': f'{QT_VERSION_STR}（PyQt {PYQT_VERSION_STR}）',
                'fluent': f'PyQt-Fluent-Widgets {fluent_version}',
                'python': platform.python_version(),
                'os': f'{platform.system()} {platform.release()}',
                'data': _wrap_path(str(config.get('data_dir') or '')),
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
        return '1.11.0'
