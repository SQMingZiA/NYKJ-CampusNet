"""开发者模式：接口浏览、原始数据、运行日志。

只在「设置 → 界面与启动 → 开发者模式」打开后才会出现在左侧导航里。

界面按**注册表编辑器**的样子做：

* 左边是一棵树：分类 → 接口（点一下就选中，不会自动调用）；
* 右边是选中接口返回的数据，用「名称 / 类型 / 数据」三列铺开，
  对象和数组可以逐层展开，**所有字段都能看到**；想看原文一键切到原始 JSON；
* 顶部一条工具栏：搜索、调用、读取全部（只读）、显示明文、复制。

明文密码接口只在**开发者模式 + 已登录**时可用，默认打码、点「显示明文」才展开，
而且**绝不写日志、不写配置文件**。
"""

from __future__ import annotations

import json
import os
import platform
import sys
from typing import Any

from PyQt5.QtCore import QTimer, QUrl, Qt, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QSplitter,
    QStackedWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    FluentIcon as FIF,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    StrongBodyLabel,
    TableWidget,
)

from .. import theme
from ..async_task import run_async
from ..toasts import Toast
from ..widgets import Card, KVGrid, scroll_page
from ...net.sitemap import crawl

SECRET_ENTRY = "password_plaintext"

SITE_VIEWS: list[tuple[str, str]] = [
    ("pages", "页面"),
    ("apis", "接口"),
    ("assets", "资源"),
    ("discovered", "脚本发现"),
    ("errors", "失败"),
]

SITE_DEFAULT_URL = "http://10.100.2.2/"

ENDPOINTS: list[tuple[str, str, str, str, str, bool]] = [
    ("账户与套餐", "selfservice", "account_info", "账户信息", "套餐名、账号状态、余额、开户时间、实时在线情况", True),
    ("账户与套餐", "selfservice", "flow_info", "套餐流量", "本周期总量 / 已用 / 剩余 / 有效期", True),
    ("账户与套餐", "selfservice", "product_intro", "可选套餐", "学校在售产品、价格与计费方式", True),
    ("账户与套餐", "selfservice", "pause_state", "暂停状态", "账号是否处于暂停 / 停机", True),
    ("账户与套餐", "selfservice", SECRET_ENTRY, "⚠ 明文密码", "读取学校侧保存的明文密码（默认打码）", True),
    ("流量与账单", "selfservice", "bills", "账单流水", "每条消费记录的金额、方式、操作员", True),
    ("流量与账单", "selfservice", "orders", "订单记录", "订单号、产品、价格、起止时间、状态", True),
    ("流量与账单", "selfservice", "net_log", "上网明细", "按日期查询的上网记录（上行/下行/时长/IP）", True),
    ("流量与账单", "selfservice", "invoices", "发票记录", "已开票记录", True),
    ("流量与账单", "portal", "/api/selfbase/checkUserOnline2", "会话与计费详情", "学校侧计费字节、订单号、剩余流量、上线时间", True),
    ("在线设备", "selfservice", "online_devices", "在线设备（自助）", "账号下所有在线设备的 MAC / IP / 会话", True),
    ("在线设备", "portal", "/api/selfbase/checkUserOnline", "在线设备（门户）", "门户侧看到的在线设备，含「超过最大在线数」标记", True),
    ("自助服务", "selfservice", "notices", "通知", "门户通知公告", True),
    ("自助服务", "selfservice", "repairs", "报修记录", "提交过的故障工单", True),
    ("自助服务", "selfservice", "work_order_types", "工单类型", "报修可选类型", True),
    ("自助服务", "selfservice", "rooms", "宿舍列表", "可申请的宿舍", True),
    ("自助服务", "selfservice", "bind_info", "绑定信息", "微信 / 运营商绑定状态", True),
    ("自助服务", "selfservice", "bottom_word", "页脚公告", "客服信息、服务时间、备案号", True),
    ("门户接口", "portal", "/api/selfbase/checkIsLogin", "是否已登录", "权威的登录状态（比本机探测更准）", True),
    ("门户接口", "portal", "/api/selfbase/checkUserStatus", "校验账号密码", "用当前账号密码问一次门户", True),
    ("门户接口", "portal", "/api/selfbase/getMacRegistNum", "已注册 MAC 数", "这个账号下登记了多少 MAC", True),
    ("门户接口", "portal", "/api/selfbase/getQueryParams", "门户查询参数", "门户下发的 wlanuserip / clientmac 等", True),
    ("门户接口", "portal", "/api/selfbase/getMenus", "门户菜单", "门户页面菜单配置", True),
    ("门户接口", "portal", "/api/selfbase/getConfig", "门户配置", "门户项目配置", True),
    ("门户接口", "portal", "/api/selfbase/getIcp", "备案信息", "ICP 备案号等合规信息", True),
    ("门户接口", "portal", "/api/selfbase/getPortalLang", "门户语言", "门户语言设置", True),
    ("门户接口", "portal", "/api/selfbase/login3", "认证接口", "不带参数调用会返回参数错误（用于核对协议）", True),
    ("本机能力", "local", "wifi_status", "WiFi 状态", "解析后的 SSID / 信号 / 速率 / 信道", True),
    ("本机能力", "local", "wifi_scan", "WiFi 扫描", "解析后的附近网络列表", True),
    ("本机能力", "local", "wifi_raw", "WiFi 原始输出", "netsh 的原始文本", True),
    ("本机能力", "local", "traffic_sample", "流量采样一次", "当前网卡的收发累计与速率", True),
    ("本机能力", "local", "traffic_summary", "流量汇总", "今日 / 本月 / 累计 / 本次运行", True),
    ("本机能力", "local", "probe", "连通性探测", "门户、网关、外网的探活结果", True),
    ("本机能力", "local", "portal_params", "当前门户参数", "origin / query / mac / 缓存状态", True),
    ("写操作（会改学校侧状态）", "selfservice", "restore_pause", "恢复使用", "解除暂停，恢复上网", False),
    ("写操作（会改学校侧状态）", "selfservice", "set_pause", "申请暂停", "停用账号（会立即断网）", False),
]


class DeveloperPage(QWidget):
    """开发者模式。"""

    site_progress = pyqtSignal(str, int, int)

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DeveloperPage")
        self.service = service
        self._current = None
        self._secret_value = ""
        self._secret_shown = False
        self._site_result: dict[str, Any] = {}
        self._site_view = "pages"
        self._site_busy = False
        self._site_summary_text = "尚未抓取。填好入口地址后点「开始抓取」。"
        self.site_progress.connect(self._on_site_progress)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 16, 28, 0)
        outer.setSpacing(12)

        self.segmented = SegmentedWidget(self)
        self.stack = QStackedWidget(self)

        self.browse_view = self._make_page(self._build_browser_card())
        self.sitemap_view = self._make_page(self._build_sitemap_card())
        self.env_view = self._make_page(self._build_env_card())

        from .tools import LogsView

        self.logs_view = LogsView(self.service, self)

        for key, text, view in (
            ("browse", "接口浏览", self.browse_view),
            ("sitemap", "站点地图", self.sitemap_view),
            ("env", "运行环境", self.env_view),
            ("logs", "运行日志", self.logs_view),
        ):
            self.segmented.addItem(key, text)
            self.stack.addWidget(view)

        self.segmented.setCurrentItem("browse")
        self.segmented.currentItemChanged.connect(self._on_segment)

        outer.addWidget(self.segmented)
        outer.addWidget(self.stack, 1)

        self.logs_view.load_history()
        self.refresh_env()

    def _make_page(self, *cards: QWidget) -> QWidget:
        """一个带滚动的分区页：卡片按顺序自上而下排。"""
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = scroll_page(page)
        outer.addWidget(scroll)
        for card in cards:
            layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _on_segment(self, key: str) -> None:
        mapping = {
            "browse": self.browse_view,
            "sitemap": self.sitemap_view,
            "env": self.env_view,
            "logs": self.logs_view,
        }
        target = mapping.get(key)
        if target is not None:
            self.stack.setCurrentWidget(target)
        if key == "env":
            self.refresh_env()
            return
        if key == "logs":
            self.logs_view.load_history()
            return
        if key == "browse":
            self.search.setFocus()


    def _build_env_card(self) -> Card:
        card = Card("运行环境", parent=self)
        self.env_grid = KVGrid(
            [
                ("version", "程序版本"),
                ("data_dir", "数据目录"),
                ("root", "项目根目录"),
                ("frozen", "打包形态"),
                ("elevated", "管理员权限"),
                ("python", "Python"),
                ("qt", "Qt / PyQt"),
                ("fluent", "FluentUI"),
                ("theme", "当前主题"),
                ("signal", "最近一次检测"),
            ],
            columns=2,
            parent=self,
        )
        card.add(self.env_grid)

        row = QHBoxLayout()
        row.setSpacing(8)

        for icon, text, handler in (
            (FIF.SYNC, "刷新环境信息", self.refresh_env),
            (FIF.COPY, "复制环境信息", self.copy_env),
            (FIF.FOLDER, "打开数据目录", lambda: self.service.open_data_dir()),
        ):
            button = PushButton(icon, text, self)
            button.clicked.connect(handler)
            row.addWidget(button)

        row.addStretch(1)
        card.add_layout(row)
        return card

    def _build_browser_card(self) -> Card:
        card = Card("接口浏览", parent=self)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.search = LineEdit(self)
        self.search.setPlaceholderText("搜索接口或说明，例如「账单」「设备」")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._refresh_tree())
        toolbar.addWidget(self.search, 1)

        self.call_button = PrimaryPushButton(FIF.SEND, "调用选中接口", self)
        self.call_button.clicked.connect(self.call_endpoint)
        toolbar.addWidget(self.call_button)

        self.read_all_button = PushButton(FIF.SYNC, "读取全部（只读）", self)
        self.read_all_button.clicked.connect(self.read_all)
        toolbar.addWidget(self.read_all_button)

        self.reveal_button = PushButton(FIF.VIEW, "显示明文", self)
        self.reveal_button.clicked.connect(self._toggle_secret)
        self.reveal_button.setVisible(False)
        toolbar.addWidget(self.reveal_button)

        self.raw_button = PushButton(FIF.DOCUMENT, "看原始 JSON", self)
        self.raw_button.clicked.connect(self._toggle_raw)
        toolbar.addWidget(self.raw_button)

        self.copy_button = PushButton(FIF.COPY, "复制结果", self)
        self.copy_button.clicked.connect(self.copy_result)
        toolbar.addWidget(self.copy_button)
        card.add_layout(toolbar)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        left = QWidget(splitter)
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 8, 0)
        left_box.setSpacing(6)
        self.keys = QTreeWidget(left)
        self.keys.setHeaderLabels(["分类 / 接口"])
        self.keys.setMinimumWidth(260)
        self.keys.setMinimumHeight(420)
        self.keys.setAlternatingRowColors(True)
        self.keys.setUniformRowHeights(True)
        self.keys.itemSelectionChanged.connect(self._on_selection_changed)
        self.keys.itemDoubleClicked.connect(lambda *_: self.call_endpoint())
        left_box.addWidget(self.keys)
        splitter.addWidget(left)

        right = QWidget(splitter)
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(6)
        self.detail_label = StrongBodyLabel("在左边选一个接口", right)
        right_box.addWidget(self.detail_label)
        self.status_label = BodyLabel("", right)
        self.status_label.setWordWrap(True)
        self.status_label.setTextColor(theme.color("text_secondary"), theme.color("text_secondary"))
        right_box.addWidget(self.status_label)

        self.values = QTreeWidget(right)

        self.values.setHeaderLabels(["名称", "类型", "数据"])
        self.values.setColumnWidth(0, 240)
        self.values.setColumnWidth(1, 90)
        self.values.setMinimumHeight(420)
        self.values.setAlternatingRowColors(True)
        values_header = self.values.header()
        values_header.setStretchLastSection(True)
        right_box.addWidget(self.values)

        self.raw_output = PlainTextEdit(right)
        self.raw_output.setReadOnly(True)
        self.raw_output.setMinimumHeight(420)
        self.raw_output.setVisible(False)
        right_box.addWidget(self.raw_output)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 780])
        card.add(splitter)

        self._refresh_tree()
        return card

    def _build_sitemap_card(self) -> Card:
        card = Card("校园网站点地图", parent=self)
        self.site_card = card

        row = QHBoxLayout()
        row.setSpacing(8)

        self.site_url = LineEdit(self)
        self.site_url.setPlaceholderText("入口地址，例如 http://10.100.2.2/")
        self.site_url.setText(SITE_DEFAULT_URL)
        self.site_url.setClearButtonEnabled(True)
        self.site_url.returnPressed.connect(self.start_sitemap_crawl)
        row.addWidget(self.site_url, 1)

        self.site_button = PrimaryPushButton(FIF.SYNC, "开始抓取", self)
        self.site_button.clicked.connect(self.start_sitemap_crawl)
        row.addWidget(self.site_button)

        self.site_scan = CheckBox("扫描脚本里的隐藏地址", self)
        self.site_scan.setChecked(True)
        self.site_scan.setToolTip("在 .js 里查找拼出来的 .html 与接口路径，耗时略长")
        row.addWidget(self.site_scan)
        card.add_layout(row)

        self.site_summary = BodyLabel(self._site_summary_text, self)
        self.site_summary.setWordWrap(True)
        card.add(self.site_summary)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)
        self.site_segmented = SegmentedWidget(self)
        for key, text in SITE_VIEWS:
            self.site_segmented.addItem(key, text)
        self.site_segmented.currentItemChanged.connect(self._on_site_view_changed)
        tools_row.addWidget(self.site_segmented)
        tools_row.addStretch(1)

        self.site_copy_button = PushButton(FIF.COPY, "复制选中地址", self)
        self.site_copy_button.clicked.connect(self.copy_selected_site_url)
        tools_row.addWidget(self.site_copy_button)
        card.add_layout(tools_row)

        self.site_table = TableWidget(self)
        self.site_table.setColumnCount(3)
        self.site_table.setHorizontalHeaderLabels(["地址", "标题 / 备注", "大小 / 状态"])
        self.site_table.setBorderVisible(True)
        self.site_table.setBorderRadius(6)
        self.site_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.site_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.site_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.site_table.verticalHeader().setVisible(False)
        self.site_table.setWordWrap(False)
        self.site_table.setAlternatingRowColors(True)
        self.site_table.setMinimumHeight(380)
        header = self.site_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.site_table.doubleClicked.connect(self._on_site_double_clicked)
        card.add(self.site_table)

        self.site_segmented.setCurrentItem("pages")
        return card

    def _on_site_progress(self, url: str, pages_so_far: int, visited: int) -> None:
        self.site_summary.setText(f"正在抓取… 已访问 {visited} 个地址 · 已收录 {pages_so_far} 个页面")

    def start_sitemap_crawl(self) -> None:
        if self._site_busy:
            return
        url = self.site_url.text().strip()
        if not url:
            Toast.warning(self, "缺少入口地址", "先填一个入口地址，例如 http://10.100.2.2/")
            return
        self._site_busy = True
        self.site_button.setEnabled(False)
        self.site_button.setText("抓取中…")
        self.site_summary.setText("正在抓取… 0 个地址")
        run_async(
            crawl,
            url,
            max_pages=200,
            max_depth=3,
            timeout=8.0,
            scan_scripts=self.site_scan.isChecked(),
            max_scripts=40,
            on_progress=self.site_progress.emit,
            on_done=self._on_site_crawl_done,
            on_error=self._on_site_crawl_error,
        )

    def _finish_site_busy(self) -> None:
        self._site_busy = False
        self.site_button.setEnabled(True)
        self.site_button.setText("开始抓取")

    def _on_site_crawl_done(self, result: Any) -> None:
        self._finish_site_busy()
        if not isinstance(result, dict) or not result.get("ok"):
            message = result.get("message") if isinstance(result, dict) else ""
            message = str(message or "没有拿到抓取结果。")
            self._site_summary_text = f"抓取失败：{message}"
            self.site_summary.setText(self._site_summary_text)
            Toast.error(self, "抓取失败", message[:300])
            return
        self._apply_site_result(result)

    def _on_site_crawl_error(self, message: str) -> None:
        self._finish_site_busy()
        text = str(message or "未知错误")
        self._site_summary_text = "抓取失败：" + text.splitlines()[0][:200]
        self.site_summary.setText(self._site_summary_text)
        Toast.error(self, "抓取失败", text[:300])

    def _apply_site_result(self, result: dict[str, Any]) -> None:
        """把一份 crawl 结果填进汇总行与表格（测试直接调用这里）。"""
        self._site_result = result or {}
        data = self._site_result
        stats = data.get("stats") or {}
        pages = int(stats.get("pages", len(data.get("pages") or [])))
        apis = int(stats.get("apis", len(data.get("apis") or [])))
        assets = int(stats.get("assets", len(data.get("assets") or [])))
        errors = int(stats.get("errors", len(data.get("errors") or [])))
        try:
            elapsed = float(data.get("elapsed") or 0.0)
        except (TypeError, ValueError):
            elapsed = 0.0
        text = f"已发现 {pages} 个页面 · {apis} 个接口 · {assets} 个资源 · 用时 {elapsed:.1f}s"
        if errors:
            text += f" · {errors} 个地址抓取失败"
        self._site_summary_text = text
        self.site_summary.setText(text)
        self._refresh_site_table()

    def _on_site_view_changed(self, key: str) -> None:
        self._site_view = key or "pages"
        self._refresh_site_table()

    def _site_rows(self) -> list[tuple[str, str, str, str]]:
        data = self._site_result or {}
        view = self._site_view
        rows: list[tuple[str, str, str, str]] = []

        if view == "pages":
            for item in data.get("pages") or []:
                url = str(item.get("url") or "")
                title = str(item.get("title") or "（无标题）")
                meta = f"HTTP {item.get('status', '?')} · {_format_size(item.get('size'))}"
                rows.append((url, title, meta, f"{url}\n标题：{title}\n深度：{item.get('depth', '?')}"))
            return rows

        if view == "apis":
            for item in data.get("apis") or []:
                url = str(item.get("url") or "")
                status = item.get("status") or 0
                if status:
                    note = "接口"
                    meta = f"HTTP {status} · {_format_size(item.get('size'))}"
                else:
                    note = "脚本中发现，未请求"
                    meta = "未请求"
                rows.append((url, note, meta, url))
            return rows

        if view == "assets":
            for item in data.get("assets") or []:
                url = str(item.get("url") or "")
                ext = str(item.get("ext") or "资源")
                refs = item.get("refs", 1)
                note = f"{ext} · 被引用 {refs} 次"
                rows.append((url, note, "—", f"{url}\n来源：{item.get('from') or '-'}"))
            return rows

        if view == "discovered":
            for item in data.get("discovered") or []:
                url = str(item.get("url") or "")
                rows.append((url, "脚本中发现的页面", "—", f"{url}\n来自：{item.get('from') or '-'}"))
            return rows

        for item in data.get("errors") or []:
            url = str(item.get("url") or "")
            rows.append((url, str(item.get("error") or "抓取失败"), "失败", url))
        return rows

    def _refresh_site_table(self) -> None:
        table = getattr(self, "site_table", None)
        if table is None:
            return
        rows = self._site_rows()
        table.setRowCount(len(rows))
        for r, (url, note, meta, tip) in enumerate(rows):
            for c, value in enumerate((url, note, meta)):
                item = QTableWidgetItem(str(value))
                item.setToolTip(tip)
                table.setItem(r, c, item)
        table.resizeRowsToContents()

    def _copy_site_address(self, url: str) -> None:
        from PyQt5.QtWidgets import QApplication

        if not url:
            return
        QApplication.clipboard().setText(url)
        self.site_summary.setText(f"已复制：{url}")
        QTimer.singleShot(2500, self._restore_site_summary)

    def _restore_site_summary(self) -> None:
        if not self._site_busy:
            self.site_summary.setText(self._site_summary_text)

    def _on_site_double_clicked(self, index: Any) -> None:
        item = self.site_table.item(index.row(), 0)
        if item is None:
            return
        url = item.text().strip()
        if not url:
            return
        if self._site_view == "pages":
            QDesktopServices.openUrl(QUrl(url))
            return
        self._copy_site_address(url)

    def copy_selected_site_url(self) -> None:
        row = self.site_table.currentRow()
        item = self.site_table.item(row, 0) if row >= 0 else None
        if item is None or not item.text().strip():
            Toast.info(self, "先选一行", "在表格里点一行，再复制地址。")
            return
        self._copy_site_address(item.text().strip())

    def _matches(self, entry: tuple) -> bool:
        keyword = self.search.text().strip().lower()
        if not keyword:
            return True
        return keyword in " ".join(str(part) for part in entry).lower()

    def _refresh_tree(self) -> None:
        self.keys.blockSignals(True)
        self.keys.clear()
        groups = {}
        for entry in ENDPOINTS:
            if not self._matches(entry):
                continue
            category, kind, target, name, desc, read_only = entry
            parent = groups.get(category)
            if parent is None:
                parent = QTreeWidgetItem(self.keys, [category])
                groups[category] = parent
            mark = "" if read_only else "  ⚠写"
            child = QTreeWidgetItem(parent, [f"{name}{mark}"])
            child.setToolTip(0, desc)
            child.setData(0, Qt.UserRole, (category, kind, target, name, read_only))
        self.keys.blockSignals(False)
        self.keys.expandAll()

        first = self._first_endpoint_item()
        if first is not None:
            self.keys.setCurrentItem(first)
            return
        self._current = None
        self.status_label.setText("没有匹配的接口")

    def _first_endpoint_item(self) -> QTreeWidgetItem | None:
        for i in range(self.keys.topLevelItemCount()):
            parent = self.keys.topLevelItem(i)
            if parent.childCount():
                return parent.child(0)
        return None

    def _on_selection_changed(self) -> None:
        item = self.keys.currentItem()
        data = item.data(0, Qt.UserRole) if item is not None else None
        if not data:
            self._current = None
            self.reveal_button.setVisible(False)
            return
        category, kind, target, name, read_only = data
        self._current = (kind, target, name, category, read_only)
        self._secret_value = ""
        self._secret_shown = False
        self.reveal_button.setVisible(False)
        where = {"selfservice": "自助系统", "portal": "门户", "local": "本机"}.get(kind, kind)
        desc = next((e[4] for e in ENDPOINTS if e[2] == target and e[3] == name), "")
        self.detail_label.setText(f"{name}\u3000·\u3000{where}" + ("" if read_only else "\u3000·\u3000⚠ 写操作"))
        self.status_label.setText(desc)

    def call_endpoint(self) -> None:
        if not self._current:
            Toast.warning(self, "先选一个接口", "在左边树里点一个接口。")
            return
        kind, target, name, _category, read_only = self._current
        if not read_only:
            from ..dialogs import confirm_typed

            if not confirm_typed(
                self.window(),
                f"{name}：会改学校侧状态",
                f"「{name}」会真的作用到你的校园网账号上。",
                "确认",
                detail="这是写操作，请确认。",
                confirm_text="确认执行",
            ):
                Toast.info(self, "已取消", "没有调用该接口。")
                return
        self.status_label.setText(f"调用中… {name}")
        self.call_button.setEnabled(False)
        run_async(self._dispatch, kind, target, on_done=lambda r: self._show(r, name), on_error=self._error)

    def _dispatch(self, kind: str, target: str) -> dict[str, Any]:
        if kind == "selfservice":
            return self.service.selfservice_call(target)
        if kind == "portal":
            return self._call_portal(target)
        return self._call_local(target)

    def _call_portal(self, path: str) -> dict[str, Any]:
        origin, query, mac = self.service.account_portal()
        username, password = self.service._account_credentials()
        from ...net.http import post_form

        payload = {
            "username": username,
            "password": password,
            "userpasswd": password,
            "mac": mac,
            "query": query,
        }
        result = post_form(f"{origin}{path}", payload, timeout=12)
        try:
            body = result.json()
        except Exception:
            body = (result.text or "")[:2000]
        return {
            "ok": result.ok,
            "http": result.status,
            "url": f"{origin}{path}",
            "data": body,
        }

    def _call_local(self, name: str) -> dict[str, Any]:
        if name == "wifi_status":
            return self.service.wifi_status()
        if name == "wifi_scan":
            return self.service.scan_wifi(True)
        if name == "wifi_raw":
            from ...core.winutil import run as run_cmd

            return {"stdout": run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=12).stdout[:4000]}
        if name == "traffic_sample":
            sample = self.service.traffic.sample()
            return {"sample": None if sample is None else {"at": sample.at, "rx": sample.rx, "tx": sample.tx, "mode": sample.mode, "source": sample.source, "adapters": sample.adapters}}
        if name == "traffic_summary":
            return self.service.traffic.summary()
        if name == "probe":
            from ...net.probe import probe

            return probe(timeout=8)
        if name == "portal_params":
            origin, query, mac = self.service.account_portal()
            return {
                "origin": origin,
                "query": query,
                "mac": mac,
                "portal_cache": bool(self.service.engine.portal_cache),
            }
        return {"error": f"未知的本地动作：{name}"}

    def read_all(self) -> None:
        """把所有只读接口依次拉一遍（尊重当前搜索），结果按接口分组铺开。"""
        entries = [e for e in ENDPOINTS if e[5] and e[2] != SECRET_ENTRY and self._matches(e)]
        if not entries:
            Toast.warning(self, "没有可读接口", "当前搜索下没有只读接口。")
            return
        self.status_label.setText(f"正在读取 {len(entries)} 个只读接口…")
        self.read_all_button.setEnabled(False)
        run_async(self._collect_all, entries, on_done=self._show_all, on_error=self._error)

    def _collect_all(self, entries: list) -> list[dict[str, Any]]:
        out = []
        for category, kind, target, name, _desc, _read in entries:
            try:
                result = self._dispatch(kind, target)
            except Exception as exc:
                result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
            out.append(
                {
                    "category": category,
                    "name": name,
                    "target": target,
                    "ok": bool(result.get("ok")),
                    "data": result.get("data", result),
                }
            )
        return out

    def _show_all(self, results: Any) -> None:
        self.read_all_button.setEnabled(True)
        self.values.clear()
        ok_count = 0
        for item in results or []:
            if item.get("ok"):
                ok_count += 1
            node = QTreeWidgetItem(
                self.values,
                [
                    f"{item.get('category')} · {item.get('name')}",
                    "对象",
                    "已读取" if item.get("ok") else "失败",
                ],
            )
            _fill_values(node, item.get("data"))

        self.values.expandToDepth(0)
        self.status_label.setText(f"已读取 {ok_count}/{len(results or [])} 个接口")
        self.raw_output.setPlainText(json.dumps(results, ensure_ascii=False, indent=2, default=str)[:300000])

    def _show(self, result: Any, name: str = "") -> None:
        self.call_button.setEnabled(True)
        self.values.clear()
        root = QTreeWidgetItem(self.values, [name or "返回", "对象", ""])
        _fill_values(root, result)
        self.values.expandToDepth(1)

        raw = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        self.raw_output.setPlainText(raw[:300000])
        self.status_label.setText(f"{name} 已返回" if name else "已返回")

        if self._current:
            if self._current[1] == SECRET_ENTRY:
                self._secret_value = _find_secret(result)
                self._secret_shown = False
                self.reveal_button.setVisible(bool(self._secret_value))
                if self._secret_value:
                    _mask_secret(self.values, self._secret_value)
                    self.raw_output.setPlainText(raw.replace(self._secret_value, "••••••••"))
                    self.service.log("warn", "已在开发者模式读取明文密码（内容未记录到日志）")

    def _toggle_secret(self) -> None:
        if not self._secret_value:
            return
        self._secret_shown = not self._secret_shown
        text = self.raw_output.toPlainText()
        if self._secret_shown:
            self.raw_output.setPlainText(text.replace("••••••••", self._secret_value))
        else:
            self.raw_output.setPlainText(text.replace(self._secret_value, "••••••••"))
        for node in _iter_nodes(self.values):
            if node.text(2) in ("••••••••",) or self._secret_value in node.text(2):
                node.setText(2, self._secret_value if self._secret_shown else "••••••••")
        self.reveal_button.setText("隐藏明文" if self._secret_shown else "显示明文")

    def _toggle_raw(self) -> None:
        """看数据树"""
        showing_raw = self.raw_output.isVisible()
        self.raw_output.setVisible(not showing_raw)
        self.values.setVisible(showing_raw)
        self.raw_button.setText("看数据树" if not showing_raw else "看原始 JSON")

    def _error(self, message: str) -> None:
        self.call_button.setEnabled(True)
        self.read_all_button.setEnabled(True)
        self.status_label.setText("调用失败")
        self.values.clear()
        root = QTreeWidgetItem(self.values, ["错误", "对象", str(message)[:200]])
        _fill_values(root, {"message": str(message)[:2000]})
        self.raw_output.setPlainText(str(message))

    def copy_result(self) -> None:
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(self.raw_output.toPlainText())
        Toast.success(self, "已复制", "返回内容已复制到剪贴板（打码状态下复制的也是打码内容）。")

    def refresh_env(self) -> None:
        settings = self.service.settings

        try:
            from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

            qt = f"Qt {QT_VERSION_STR} / PyQt {PYQT_VERSION_STR}"
        except Exception:
            qt = "-"

        try:
            import qfluentwidgets

            fluent = f"PyQt-Fluent-Widgets {getattr(qfluentwidgets, '__version__', '?')}"
        except Exception:
            fluent = "-"

        from .. import theme as theme_module

        state = self.service.state() or {}
        self.env_grid.update_values(
            {
                "version": _app_version(),
                "data_dir": str(settings.data_dir),
                "root": str(self.service.project_root),
                "frozen": "打包 exe" if getattr(sys, "frozen", False) else "源码运行",
                "elevated": "是" if state.get("elevated") else "否",
                "python": platform.python_version(),
                "qt": qt,
                "fluent": fluent,
                "theme": f"{settings.theme}（实际 {theme_module.theme_name()}）",
                "signal": f"{state.get('label', '-')} / {state.get('ssid') or '-'}",
            }
        )

    def copy_env(self) -> None:
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(
            "\n".join(
                [
                    f"程序版本: {_app_version()}",
                    f"数据目录: {self.service.settings.data_dir}",
                    f"项目根: {self.service.project_root}",
                    f"frozen: {getattr(sys, 'frozen', False)}",
                    f"Python: {platform.python_version()}",
                    f"系统: {platform.platform()}",
                ]
            )
        )
        Toast.success(self, "已复制", "环境信息已复制到剪贴板。")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            self.logs_view.load_history()
        except Exception:
            pass

    def on_state(self, state: dict) -> None:
        pass

    def on_theme_changed(self) -> None:
        pass


def _format_size(value: Any) -> str:
    try:
        size = int(value or 0)
    except (TypeError, ValueError):
        return "—"
    if size <= 0:
        return "—"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _kind_of(value: Any) -> str:
    if value is None:
        return "空"
    if isinstance(value, bool):
        return "布尔"
    if isinstance(value, (int, float)):
        return "数字"
    if isinstance(value, str):
        return "字符串"
    if isinstance(value, dict):
        return "对象"
    if isinstance(value, list):
        return "数组"
    return type(value).__name__


def _display(value: Any) -> str:
    if value is None:
        return "（空）"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list)):
        return f"（{len(value)} 项）"
    return str(value)[:500]


def _fill_values(parent: QTreeWidgetItem, value: Any, depth: int = 0) -> None:
    """把任意 JSON 铺成「名称 / 类型 / 数据」三行式（对象与数组可继续展开）。"""
    if depth > 8:
        QTreeWidgetItem(parent, ["…", "提示", "层级过深，已省略"])
        return

    if isinstance(value, dict):
        for key, sub in value.items():
            node = QTreeWidgetItem(parent, [str(key), _kind_of(sub), _display(sub)])
            if isinstance(sub, (dict, list)) and sub:
                _fill_values(node, sub, depth + 1)
        return

    if isinstance(value, list):
        for index, sub in enumerate(value):
            node = QTreeWidgetItem(parent, [f"[{index}]", _kind_of(sub), _display(sub)])
            if isinstance(sub, (dict, list)) and sub:
                _fill_values(node, sub, depth + 1)
        return

    QTreeWidgetItem(parent, ["值", _kind_of(value), _display(value)])


def _iter_nodes(root: QTreeWidget):
    stack = [root.topLevelItem(i) for i in range(root.topLevelItemCount())]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        yield node
        stack.extend(node.child(i) for i in range(node.childCount()))


def _mask_secret(tree: QTreeWidget, secret: str) -> None:
    for node in _iter_nodes(tree):
        if secret in node.text(2):
            node.setText(2, "••••••••")


def _find_secret(result: Any) -> str:
    """从返回里找出明文密码（字段名含 pass/pwd 的第一个非空字符串）。"""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, sub in value.items():
                if (
                    isinstance(sub, str)
                    and sub.strip()
                    and any(token in str(key).lower() for token in ("pass", "pwd", "userpasswd"))
                ):
                    found.append(sub.strip())
                else:
                    walk(sub)
        elif isinstance(value, list):
            for sub in value:
                walk(sub)

    walk(result)
    return found[0] if found else ""


def _app_version() -> str:
    try:
        from main import __version__

        return str(__version__)
    except Exception:
        return "1.8.0"
