"""主页：状态总览 + 一键认证 + 实时速率曲线 + 用量统计。

界面全部由 **FluentUI**（qfluentwidgets）的卡片与控件构成，
业务逻辑一律通过 ``Service`` 调用，界面线程不做任何阻塞操作。
"""

from __future__ import annotations

import hashlib
import time

from typing import Any

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    IndeterminateProgressBar,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TransparentToolButton,
)

from .. import theme
from ..async_task import run_async
from ..charts import LiveChart
from ..formatting import (
    format_bytes,
    format_duration,
    format_rate,
    format_relative,
    human_quota,
    signal_label,
    state_meta,
)
from ..toasts import Toast
from ..widgets import Card, KVGrid, StatTile, divider, scroll_page


CONTENT_MAX_WIDTH = 1000


class HomePage(QWidget):
    """主页。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HomePage")
        self.service = service
        self._window = None
        self._state = {}
        self._summary = {}

        self._painted = {}
        self._painted_at = 0.0
        self._busy_since = 0.0

        self._manual_action = False
        self._last_failed = False

        self._notice_busy = False
        self._notice_ok = False
        self._notice_retry = 0
        self._notice_timer = None

        self._build_ui()

        self._summary_timer = QTimer(self)
        self._summary_timer.setInterval(30000)
        self._summary_timer.timeout.connect(self.refresh_summary)
        self._summary_timer.start()

        self._online_base = 0.0
        self._online_at = 0.0
        self._online_tick = QTimer(self)
        self._online_tick.setInterval(1000)
        self._online_tick.timeout.connect(self._tick_online)
        self._online_tick.start()

        QTimer.singleShot(300, self._load_initial)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll, layout = scroll_page(self)
        outer.addWidget(scroll)

        self.status_card = Card(parent=self)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.status_dot = CaptionLabel("●", self)
        self.status_dot.setStyleSheet("font-size: 16px;")
        title_row.addWidget(self.status_dot)

        self.status_title = SubtitleLabel("待检测", self)
        title_row.addWidget(self.status_title)
        title_row.addStretch(1)

        self.connect_button = PrimaryPushButton(FIF.SYNC, "检测并登录", self)
        self.connect_button.clicked.connect(self._on_connect_clicked)
        self.connect_button.setToolTip(
            "自动搜寻校园网 → 连接 → 提交认证 → 复测；还没填账号时会先带你去设置页"
        )
        title_row.addWidget(self.connect_button)

        self.switch_button = PushButton(FIF.WIFI, "换个网络", self)
        self.switch_button.clicked.connect(self.run_switch_network)
        self.switch_button.setToolTip("扫描并切换到另一个信号更强的校园网（NYKJ / CMCC-NYKJ 等）")
        title_row.addWidget(self.switch_button)

        self.portal_button = PushButton(FIF.GLOBE, "打开认证页面", self)
        self.portal_button.clicked.connect(self.open_portal)
        title_row.addWidget(self.portal_button)
        self.status_card.add_layout(title_row)

        # 状态说明直接放在主状态区里，用正常字号的正文，而不是卡片底部的一行 12px 灰字。
        self.status_detail = BodyLabel("", self)
        self.status_detail.setWordWrap(True)
        self.status_detail.setTextColor(
            theme.color("text_secondary"), theme.color("text_secondary")
        )
        self.status_card.add(self.status_detail)

        self.busy_bar = IndeterminateProgressBar(self)
        self.busy_bar.setVisible(False)
        self.status_card.add(self.busy_bar)

        self.status_card.add(divider(self))
        self.status_grid = KVGrid(
            [
                ("ssid", "无线网络"),
                ("signal", "信号强度"),
                ("online_for", "在线时长"),
                ("last_check", "上次检测"),
                ("mode", "统计方式"),
            ],
            columns=2,
            parent=self,
        )
        self.status_card.add(self.status_grid)
        layout.addWidget(self.status_card)

        self.notice_card = Card(parent=self)
        self.notice_card.set_background("#FFF7D6", "#3B3320")
        notice_head = QHBoxLayout()
        notice_head.setContentsMargins(0, 0, 0, 0)
        notice_head.setSpacing(6)
        self.notice_title = StrongBodyLabel("校园网公告", self)
        notice_head.addWidget(self.notice_title)
        notice_head.addStretch(1)
        self.notice_close_button = TransparentToolButton(FIF.CLOSE, self)
        self.notice_close_button.setToolTip("关闭这条公告；可在「设置 → 界面与启动」重新打开")
        self.notice_close_button.clicked.connect(self._dismiss_notice)
        notice_head.addWidget(self.notice_close_button)
        self.notice_card.add_layout(notice_head)

        self.notice_text = BodyLabel("正在读取公告…", self)
        self.notice_text.setWordWrap(True)
        self.notice_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notice_card.add(self.notice_text)
        self._notice_sig = ""

        self.notice_button = PushButton(FIF.SYNC, "刷新", self)
        self.notice_button.setVisible(False)
        self.selfservice_button = PushButton(FIF.DOCUMENT, "自助服务", self)
        self.selfservice_button.setVisible(False)
        layout.addWidget(self.notice_card)

        self.live_card = Card("实时速率", parent=self)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tile_rx = StatTile("下行", "0 B/s", parent=self)
        self.tile_tx = StatTile("上行", "0 B/s", parent=self)
        self.tile_total = StatTile("合计", "0 B/s", parent=self)
        for tile in (self.tile_rx, self.tile_tx, self.tile_total):
            tiles.addWidget(tile, 1)
        self.live_card.add_layout(tiles)

        self.chart = LiveChart(parent=self)
        self.live_card.add(self.chart)
        self._chart_empty_text = ""
        self._set_chart_empty("等待采样…")
        layout.addWidget(self.live_card)

        self.usage_card = Card("流量用量", parent=self)
        usage_row = QHBoxLayout()
        usage_row.setSpacing(12)
        self.tile_today = StatTile("今日", "-", parent=self)
        self.tile_month = StatTile("本月", "-", parent=self)
        self.tile_all = StatTile("累计", "-", parent=self)
        self.tile_session = StatTile("本次运行", "-", parent=self)
        for tile in (self.tile_today, self.tile_month, self.tile_all, self.tile_session):
            usage_row.addWidget(tile, 1)
        self.usage_card.add_layout(usage_row)

        self.quota_row = QWidget(self)
        quota_box = QVBoxLayout(self.quota_row)
        quota_box.setContentsMargins(0, 4, 0, 0)
        quota_box.setSpacing(6)
        head = QHBoxLayout()
        self.quota_caption = StrongBodyLabel("套餐用量", self)
        head.addWidget(self.quota_caption)
        self.quota_setup_button = PushButton(FIF.SETTING, "去设置", self)
        self.quota_setup_button.clicked.connect(self._goto_settings)
        self.quota_setup_button.setToolTip("在「设置 → 套餐流量」里填写每月套餐额度")
        self.quota_setup_button.setVisible(False)
        head.addWidget(self.quota_setup_button)
        head.addStretch(1)
        self.quota_value = BodyLabel("读取中…", self)
        self.quota_value.setTextColor(
            theme.color("text_secondary"), theme.color("text_secondary")
        )
        head.addWidget(self.quota_value)
        quota_box.addLayout(head)
        self.quota_bar = ProgressBar(self)
        self.quota_bar.setValue(0)
        self.quota_bar.setTextVisible(False)
        self.quota_bar.setToolTip("套餐进度只统计校园网内的流量")
        quota_box.addWidget(self.quota_bar)
        # 最近一次算出的月度校园网用量，以及学校套餐信息（用来判断「不限量」）。
        self._month_total = 0.0
        self._package = {}
        self._set_quota_display("读取中…", "正在读取本月校园网用量")
        self.usage_card.add(self.quota_row)
        layout.addWidget(self.usage_card)

        self.package_card = Card("学校套餐", parent=self)
        package_row = QHBoxLayout()
        package_row.setSpacing(8)
        self.package_button = PushButton(FIF.CERTIFICATE, "刷新套餐", self)
        self.package_button.clicked.connect(self.refresh_package)
        package_row.addWidget(self.package_button)
        self.package_more = PushButton(FIF.DOCUMENT, "更多（订单/账单）", self)
        self.package_more.clicked.connect(lambda: self._goto_tools("selfservice"))
        package_row.addWidget(self.package_more)
        package_row.addStretch(1)
        self.package_card.add_layout(package_row)

        package_tiles = QHBoxLayout()
        package_tiles.setSpacing(12)
        self.tile_package_name = StatTile("套餐", "-", "—", parent=self)
        self.tile_expire = StatTile("到期时间", "-", "—", parent=self)
        self.tile_school_used = StatTile("本周期已用", "-", "—", parent=self)
        self.tile_school_left = StatTile("剩余流量", "-", "—", parent=self)
        for tile in (
            self.tile_package_name,
            self.tile_expire,
            self.tile_school_used,
            self.tile_school_left,
        ):
            package_tiles.addWidget(tile, 1)
        self.package_card.add_layout(package_tiles)
        layout.addWidget(self.package_card)

        layout.addStretch(1)

    def _load_initial(self) -> None:
        run_async(self.service.get_config, on_done=lambda _c: self.refresh_summary())
        run_async(self.service.traffic.ring, on_done=self._apply_ring)
        # 卡片可见时 set_package_visible 自己会去刷新，避免启动时重复请求一次。
        self.set_package_visible(bool(self.service.settings.home_show_package))
        self.notice_card.setVisible(bool(self.service.settings.home_show_notice))
        if self.service.settings.home_show_notice:
            self._notice_retry = 0
            self.refresh_notice()
            self._notice_timer = QTimer(self)
            minutes = max(1, int(self.service.settings.notice_refresh_minutes or 10))
            self._notice_timer.setInterval(minutes * 60 * 1000)
            self._notice_timer.timeout.connect(self.refresh_notice)
            self._notice_timer.start()

    def refresh_notice(self) -> None:
        """读取校园网公告与客服信息。

        开机时无线还没连上、读不到是正常的 —— 这里会自己退避重试几次，
        等网络就绪后自动补上，不需要用户手动点。
        """
        if not self.service.settings.home_show_notice:
            return
        if not self._notice_busy:
            self._notice_busy = True
            self.notice_button.setEnabled(False)

            def done(result: Any) -> None:
                self._notice_busy = False
                self.notice_button.setEnabled(True)
                self._apply_notice(result)

            run_async(self._collect_notice, on_done=done, on_error=self._notice_failed)
            return

    def _collect_notice(self) -> dict[str, Any]:
        notice = self.service.selfservice_call("notices")
        footer = self.service.selfservice_call("bottom_word")
        return {"notice": notice, "footer": footer}

    def _apply_notice(self, result: Any) -> None:
        data = result if isinstance(result, dict) else {}
        notice = (data.get("notice") or {}).get("data")
        footer = (data.get("footer") or {}).get("data")

        lines = []
        for text in _notice_lines(notice):
            lines.append(text)
        for text in _footer_lines(footer):
            lines.append(text)

        if lines:
            self._notice_ok = True
            self._set_notice_body(lines)
            return
        self._notice_failed(str((data.get("notice") or {}).get("message") or "暂时没有公告"))

    def _set_notice_body(self, lines: list[str]) -> None:
        """公告全文展示；卡片高度按实际换行行数计算，避免长公告被截断。"""
        full = chr(10).join(lines)
        self._notice_sig = hashlib.sha1(full.encode("utf-8")).hexdigest()[:16]
        self.notice_text.setText(full)
        self.notice_text.setToolTip(full)
        if self._notice_sig == str(self.service.settings.get("notice_dismissed") or ""):
            # 用户关过这条公告：只要内容没变就不再打扰。
            self.notice_card.setVisible(False)
            return
        self.notice_card.setVisible(True)
        QTimer.singleShot(0, self._fit_notice_height)

    def _dismiss_notice(self) -> None:
        """记住这条公告的指纹，之后内容变了才会再次出现。"""
        sig = getattr(self, "_notice_sig", "")
        if sig:
            run_async(self.service.update_config, {"notice_dismissed": sig})
        self.notice_card.setVisible(False)

    def set_notice_visible(self, visible: bool) -> None:
        """设置页的「主页显示校园网公告」开关；重新打开时清掉关闭记录。"""
        if not visible:
            self.notice_card.setVisible(False)
            return
        run_async(self.service.update_config, {"notice_dismissed": ""})
        self.notice_card.setVisible(True)
        self.refresh_notice()

    def _fit_notice_height(self) -> None:
        """按卡片实际宽度把换行后的文本高度算出来（CardWidget 不传播 heightForWidth）。"""
        text = self.notice_text.text()
        if not text:
            return
        width = max(240, self.notice_card.width() - 48)
        metrics = self.notice_text.fontMetrics()
        rect = metrics.boundingRect(0, 0, width, 20000, Qt.TextWordWrap, text)
        self.notice_text.setFixedHeight(max(rect.height() + 2, metrics.lineSpacing() + 2))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        card = getattr(self, "notice_card", None)
        if card is not None and card.isVisible():
            QTimer.singleShot(0, self._fit_notice_height)

    def _notice_failed(self, message: str = "") -> None:
        self._notice_busy = False
        self.notice_button.setEnabled(True)
        retry = getattr(self, "_notice_retry", 0)
        reason = (message or "").splitlines()[0] if message else ""
        if retry < 8 and not getattr(self, "_notice_ok", False):
            self._notice_retry = retry + 1
            self.notice_text.setText("正在读取公告…")
            self.notice_text.setToolTip(reason or "网络就绪后会自动重新读取公告")
            self.notice_card.setVisible(True)
            QTimer.singleShot(0, self._fit_notice_height)
            QTimer.singleShot(15000, self.refresh_notice)
            return
        # 没有公告就不留这张卡：一张只有提示文字的空卡片没有意义。
        self.notice_card.setVisible(False)

    def set_package_visible(self, visible: bool) -> None:
        self.package_card.setVisible(bool(visible))
        if visible:
            self.refresh_package()

    def refresh_package(self) -> None:
        """从学校自助系统读套餐/到期/流量。"""
        self.package_button.setEnabled(False)
        self.package_button.setText("读取中…")
        run_async(
            self.service.selfservice_overview,
            on_done=self._apply_package,
            on_error=lambda msg: self._apply_package({"ok": False, "message": msg}),
        )

    def _apply_package(self, result: Any) -> None:
        """把套餐结果写进指标块；失败原因进控件 tooltip（不再有底部灰字）。"""
        self.package_button.setEnabled(True)
        self.package_button.setText("刷新套餐")
        if not isinstance(result, dict) or not result.get("ok"):
            message = str((result or {}).get("message") or "读取失败，可在设置里确认账号")
            reason = message.splitlines()[0] if message else "读取失败"
            self.tile_package_name.set_value("读取失败", reason[:40])
            self.package_button.setToolTip(f"套餐读取失败：{message}")
            return
        self.package_button.setToolTip("")
        account = result.get("account") or {}
        flow = result.get("flow") or {}
        self._package = result.get("package") or {}
        self.tile_package_name.set_value(
            account.get("product_name") or "—",
            f"余额 {account.get('balance') or '0.00'} 元",
        )
        self.tile_expire.set_value(account.get("order_end_time") or "—", "套餐到期")
        self.tile_school_used.set_value(flow.get("used") or "—", "本周期已用")
        if self._package.get("unlimited"):
            self.tile_school_left.set_value("不限量", "学校套餐不限量")
        else:
            left = flow.get("flowBalanceFormat") or "—"
            self.tile_school_left.set_value(
                left, f"总量 {flow.get('totalFlowFormat') or '—'}"
            )
        self._render_quota()

    def _goto_tools(self, segment: str) -> None:
        """跳到网络工具的某个分区。"""
        if self._window is None:
            return
        target = getattr(self._window, "account_page", None)
        if target is not None:
            self._window.switchTo(target)

    def _goto_settings(self) -> None:
        """去设置里填套餐额度；页面没有导航能力时就只用 Toast 提示。"""
        switch_to = getattr(self._window, "switch_to", None)
        if callable(switch_to):
            switch_to("settings")
            return
        target = getattr(self._window, "settings_page", None)
        if target is not None and self._window is not None:
            self._window.switchTo(target)
            return
        Toast.info(self, "去设置", "请在「设置 → 套餐流量」里填写每月套餐额度。")

    def _tick_online(self) -> None:
        """每秒刷新「在线时长」。

        以前这个字段只在状态回调时更新（后台 15 秒一轮），所以它一跳一跳地走。
        这里拿"服务给的秒数 + 本地经过的时间"自己算，看起来才是正常计时的样子。
        """
        if not self._online_at or not self._online_base:
            return
        if not (self._state or {}).get("online"):
            return
        try:
            self.status_grid.update_values(
                {"online_for": format_duration(self._online_base + (time.time() - self._online_at))}
            )
        except Exception:
            return

    def refresh_summary(self) -> None:
        run_async(self.service.traffic.summary, on_done=self._apply_summary)

    def _apply_ring(self, ring: Any) -> None:
        if isinstance(ring, list) and ring:
            self.chart.load_history(ring)
            self._set_chart_empty("")

    def _apply_summary(self, summary: Any) -> None:
        if not isinstance(summary, dict):
            return
        self._summary = summary
        today = summary.get("today") or {}
        month = summary.get("month") or {}
        alltime = summary.get("all") or {}
        session = summary.get("session") or {}
        live = summary.get("live") or {}
        self.tile_today.reset_color()

        self.tile_today.set_value(
            format_bytes(today.get("total")),
            f"↓ {format_bytes(today.get('rx'))}  ↑ {format_bytes(today.get('tx'))}",
        )
        self.tile_month.set_value(
            format_bytes(month.get("total")),
            f"↓ {format_bytes(month.get('rx'))}  ↑ {format_bytes(month.get('tx'))}",
        )
        self.tile_all.set_value(
            format_bytes(alltime.get("total")),
            f"↓ {format_bytes(alltime.get('rx'))}  ↑ {format_bytes(alltime.get('tx'))}",
        )
        self.tile_session.set_value(
            format_bytes(session.get("total")),
            f"已运行 {format_duration(max(0, (summary.get('now') or 0) - (session.get('started_at') or 0)))}",
        )

        mode = live.get("mode") or ""
        mode_label = {"system": "整机网卡", "adapters": "按网卡"}.get(mode, "读取中…")
        if not summary.get("healthy", True):
            mode_label = "读取失败"
        elif not mode:
            mode_label = "读取中…"
        self.status_grid.set("mode", mode_label)

        try:
            usage = self.service.campus_usage()
        except Exception:
            usage = {}
        self._apply_quota(usage.get("month") or 0, summary)
        self._update_live_tiles(live.get("rx_rate") or 0, live.get("tx_rate") or 0)

        if not summary.get("healthy", True):
            hint = summary.get("retry_hint") or "流量计数器读取失败"
            self.tile_today.set_color(theme.color("warn"))
            self.tile_today.set_value("读取失败", str(hint)[:40])
            self._set_quota_display("读取失败", str(hint))
            return

    def _set_quota_display(self, text: str, tooltip: str = "", percent: float = 0.0) -> None:
        """配额文案写进进度条自身：format + 右侧数值 + tooltip，不再用底部灰字。"""
        self.quota_bar.setValue(max(0, min(100, int(percent))))
        self.quota_bar.setFormat(text)
        self.quota_bar.setToolTip(tooltip or text)
        self.quota_value.setText(text)

    def _render_quota(self) -> None:
        """按当前套餐信息与本月用量重画套餐进度。"""
        month_total = float(getattr(self, "_month_total", 0.0) or 0.0)
        package = getattr(self, "_package", {}) or {}
        if package.get("unlimited"):
            self.quota_bar.setCustomBarColor(theme.color("accent"), theme.color("accent"))
            self._set_quota_display("不限量套餐", "学校套餐为不限量，不统计月度配额")
            self.quota_setup_button.setVisible(False)
            return
        quota_gb = float(self.service.settings.monthly_quota_gb or 0)
        if quota_gb <= 0:
            self.quota_bar.setCustomBarColor(theme.color("accent"), theme.color("accent"))
            self._set_quota_display(
                "未设置套餐额度", "在「设置 → 套餐流量」里填写每月额度后可显示进度"
            )
            self.quota_setup_button.setVisible(True)
            return
        self.quota_setup_button.setVisible(False)
        quota_bytes = quota_gb * 1024 ** 3
        percent = min(100.0, month_total / quota_bytes * 100 if quota_bytes else 0.0)
        remaining = max(0.0, quota_bytes - month_total)
        warning = percent >= 95
        color = theme.color("error") if warning else theme.color("accent")
        self.quota_bar.setCustomBarColor(color, color)
        text = f"已用 {percent:.1f}% · 剩 {format_bytes(remaining)}"
        if warning:
            text = f"已用 {percent:.1f}% · 仅剩 {format_bytes(remaining)}"
        self._set_quota_display(
            text,
            f"{format_bytes(month_total)} / {human_quota(quota_gb)}，只统计校园网内流量",
            percent,
        )

    def _apply_quota(self, month_total: float, summary: dict) -> None:
        """套餐进度。

        ``month_total`` 传进来的是**校园网内**的月度用量（见 ``service.campus_usage()``）——
        校园网以外的网络（家里 WiFi、手机热点）产生的流量不计入套餐。
        """
        self._month_total = float(month_total or 0.0)
        self._render_quota()

    def _update_live_tiles(self, rx_rate: float, tx_rate: float) -> None:
        self.tile_rx.set_value(format_rate(rx_rate))
        self.tile_tx.set_value(format_rate(tx_rate))
        self.tile_total.set_value(format_rate((rx_rate or 0) + (tx_rate or 0)))

    def bind_window(self, window) -> None:
        self._window = window

    def open_portal(self) -> None:
        if self._window is not None:
            self._window.open_portal_browser(self._state or None)

    def _on_connect_clicked(self) -> None:
        """没填账号时先把人送到设置页：认证必然失败，不如直接给入口。"""
        if not self._has_account():
            self._goto_settings_account()
            return
        self.run_connect()

    def _has_account(self) -> bool:
        try:
            return bool(str(self.service.settings.get("username") or "").strip())
        except Exception:
            return False

    def _goto_settings_account(self) -> None:
        """跳到设置页，并把焦点落在「校园卡账号」输入框上。"""
        self._goto_settings()
        window = self._window
        page = getattr(window, "settings_page", None)
        row = getattr(page, "username_row", None)
        edit = getattr(row, "edit", None)
        if edit is not None:
            QTimer.singleShot(120, lambda: (edit.setFocus(), edit.selectAll()))

    def run_connect(self) -> None:
        """执行一次「扫描校园网 → 连接 → 认证 → 复测」。

        无网/没信号时也走这一条路：``service.tick`` 会先扫描、必要时把发现的校园网
        自动加入名单并连上，连不上外网还会换一个校园网再试一次。
        """
        self._manual_action = True
        self._last_failed = False
        self._set_busy(True, "正在检测并登录：会自动搜寻校园网、连接、认证…")
        self.connect_button.setEnabled(False)

        def done(result: Any) -> None:
            self._manual_action = False
            self._set_busy(False)
            self.connect_button.setEnabled(True)
            self.refresh_summary()
            if isinstance(result, dict):
                if result.get("state") == "online" or result.get("ok"):
                    self._last_failed = False
                    Toast.success(self, "已联网", "认证完成，可以正常上网了。")
                elif result.get("skipped"):
                    Toast.info(self, "已跳过", str(result.get("message", "")))
                else:
                    self._last_failed = True
                    label = result.get("label") or "未联网"
                    Toast.warning(
                        self,
                        "未能联网",
                        f"当前状态：{label}。可在「网络工具 → 无线网络」里手动选网络。",
                    )
            self._sync_connect_button()
            self._request_state()

        def failed(message: str) -> None:
            self._manual_action = False
            self._last_failed = True
            self._set_busy(False)
            self.connect_button.setEnabled(True)
            Toast.error(
                self,
                "检测失败",
                message.splitlines()[0] if message else "未知错误",
            )
            self._sync_connect_button()

        run_async(lambda: self.service.tick(manual=True), on_done=done, on_error=failed)

    def run_switch_network(self) -> None:
        """手动换一个校园网（优先切到信号更强的那个）。"""
        self._manual_action = True
        self._set_busy(True, "正在扫描并切换到其它校园网…")

        def done(result: Any) -> None:
            self._manual_action = False
            self._set_busy(False)
            self._sync_connect_button()
            self._request_state()
            if isinstance(result, dict) and result.get("ok"):
                Toast.success(self, "已切换", f"当前连接：{result.get('ssid', '')}")
            else:
                Toast.warning(
                    self,
                    "切换未成功",
                    str((result or {}).get("message", "附近没有其它校园网")),
                )
                return

        def failed(message: str) -> None:
            self._manual_action = False
            self._set_busy(False)
            self._sync_connect_button()
            Toast.error(self, "切换失败", (message or "").splitlines()[0])

        run_async(self.service.switch_network, on_done=done, on_error=failed)

    def _request_state(self) -> None:
        if self._window is not None:
            self._window.state_received.emit(self.service.state())

    def _set_busy(self, busy: bool, text: str = "") -> None:
        """兼容旧调用：立即设置忙碌态与提示文案。"""
        self._busy_since = time.time() if busy else 0.0
        if busy:
            # 手动操作不一定立刻有 busy 的状态推送，这里自己也排一次显示。
            QTimer.singleShot(400, self._maybe_show_busy)
        self._update_busy(busy)
        if text:
            self._set_status_detail(text)
        self._sync_connect_button()

    def _set_status_detail(self, text: str) -> None:
        """状态说明用正常字号的一行（BodyLabel）表达，并同步为按钮 tooltip。"""
        if self._painted.get("detail") != text:
            self._painted["detail"] = text
            self.status_detail.setText(text)
        self.connect_button.setToolTip(text)

    def _sync_connect_button(self) -> None:
        """主按钮文案随状态变化：按钮本身就是最直接的状态表达。"""
        state_key = (self._state or {}).get("state", "")
        busy = bool((self._state or {}).get("busy")) or self._manual_action
        if busy:
            text = "正在认证…"
        elif not self._has_account():
            text = "先填写账号"
        elif state_key == "online":
            text = "重新认证"
        elif self._last_failed:
            text = "重试"
        else:
            text = "检测并登录"
        if self._painted.get("button") != text:
            self._painted["button"] = text
            self.connect_button.setText(text)
        self.connect_button.setEnabled(not busy)

    def _set_chart_empty(self, text: str) -> None:
        """图表没有采样时，把「等待采样…」写在图表内部（标题行），而不是卡片底部灰字。"""
        self._chart_empty_text = text
        try:
            if text:
                self.chart.plot.setTitle(
                    text, color=theme.color("text_secondary"), size="10pt"
                )
            else:
                self.chart.plot.setTitle(None)
        except Exception:
            pass

    def on_state(self, state: dict) -> None:
        """状态推送（对外入口，实际逻辑在 ``_apply_state``）。"""
        self._apply_state(state)

    def _apply_state(self, state: dict) -> None:
        """状态推送。

        这里刻意做了**去抖**：后台每 15 秒都会例行检测一次，如果把每个字段都无条件
        重画一遍，界面就会不停闪。所以只有「文字真的变了」才动控件；
        一直在变的相对时间（在线时长 / 上次检测）最多每 10 秒刷一次。
        """
        state = state or {}
        previous = self._state or {}
        self._state = state

        state_key = state.get("state", "")
        if state_key == "online":
            # 后台守护任务把网络恢复了就不用再显示「重试」。
            self._last_failed = False
        meta = state_meta(state_key)
        color = meta["color"]
        if theme.is_dark():
            color = {
                "#107c10": "#6CCB5F",
                "#c47f00": "#FCE100",
                "#0f6cbd": "#4CC2FF",
                "#c42b1c": "#FF99A4",
                "#616161": "#9A9A9A",
            }.get(color, color)
        label = state.get("label") or meta["label"]
        if self._painted.get("label") != label:
            self._painted["label"] = label
            self.status_title.setText(label)
        if self._painted.get("color") != color:
            self._painted["color"] = color
            self.status_dot.setStyleSheet(f"font-size: 16px; color: {color};")
            self.status_title.setTextColor(color, color)
        ssid = state.get("ssid") or "—"
        wifi = state.get("wifi_status") or {}
        signal = wifi.get("signal")

        self._online_base = float(state.get("online_for") or 0)
        self._online_at = time.time() if self._online_base else 0.0
        changed = previous.get("state") != state_key or previous.get("ssid") != ssid
        if changed or time.time() - self._painted_at > 10:
            self._painted_at = time.time()
            self.status_grid.update_values(
                {
                    "ssid": f"{ssid}（{signal_label(signal)}）" if ssid != "—" else ssid,
                    "signal": f"{signal}%" if isinstance(signal, (int, float)) else "—",
                    "online_for": format_duration(state.get("online_for") or 0),
                    "last_check": format_relative(state.get("last_check") or 0),
                }
            )

        portal = state.get("portal") or {}
        if state_key == "portal":
            hint = f"检测到认证门户{'：' + str(portal.get('url'))[:70] if portal.get('url') else ''}，点「检测并登录」提交认证。"
        elif state_key == "online":
            hint = "网络已连通，自动认证任务会在后台持续守护。"
        elif state_key in ("offline", "no-network", "no-internet"):
            hint = "当前没有可用网络，点「检测并登录」会自动搜寻校园网并连接。"
        elif state_key in ("checking", "authenticating", "connecting-wifi"):
            hint = state.get("label") or "正在处理…"
        else:
            hint = "点「检测并登录」开始：程序会自动搜寻校园网、连接、提交认证并复测。"
        self._set_status_detail(hint)

        self._update_busy(bool(state.get("busy")))
        self._sync_connect_button()

    def _update_busy(self, busy: bool) -> None:
        """忙碌指示。

        只有**用户主动触发**的操作才显示进度条 —— 后台每 15 秒的例行巡检也走 busy，
        如果它一亮一灭，界面就一直闪，用户会觉得"程序老在动"。
        """
        if not busy:
            self._busy_since = 0.0
            if self.busy_bar.isVisible():
                self.busy_bar.setVisible(False)
            try:
                self.busy_bar.stop()
            except Exception:
                pass
            return
        if not self._manual_action:
            return
        if not self._busy_since:
            self._busy_since = time.time()
            QTimer.singleShot(400, self._maybe_show_busy)
            return

    def _maybe_show_busy(self) -> None:
        busy = bool((self._state or {}).get("busy")) or self._manual_action
        if not self._busy_since or not busy:
            return
        if time.time() - self._busy_since < 0.4:
            return
        if not self.busy_bar.isVisible():
            self.busy_bar.setVisible(True)
            try:
                self.busy_bar.start()
            except Exception:
                pass

    def on_traffic(self, sample: dict) -> None:
        rx_rate = sample.get("rx_rate") or 0.0
        tx_rate = sample.get("tx_rate") or 0.0
        self.chart.append(rx_rate, tx_rate)
        if self._chart_empty_text:
            self._set_chart_empty("")
        self._update_live_tiles(rx_rate, tx_rate)

    def on_theme_changed(self) -> None:
        try:
            self.chart.apply_theme()
        except Exception:
            pass
        self.quota_value.setTextColor(
            theme.color("text_secondary"), theme.color("text_secondary")
        )
        self.status_detail.setTextColor(
            theme.color("text_secondary"), theme.color("text_secondary")
        )
        if self._chart_empty_text:
            text = self._chart_empty_text
            self._chart_empty_text = ""
            self._set_chart_empty(text)
        if self._state:
            self.on_state(self._state)


def _notice_lines(notice: Any) -> list[str]:
    """把 ``/api/selfuser/getNotice`` 的返回整理成可显示的若干行。"""
    if notice in (None, "", [], {}):
        return []
    if isinstance(notice, str):
        return [notice.strip()] if notice.strip() else []
    if isinstance(notice, dict):
        for key in ("content", "notice", "noticeContent", "msg", "title"):
            value = notice.get(key)
            if isinstance(value, str) and value.strip():
                return [value.strip()]
        return [
            "；".join(
                f"{k}：{v}" for k, v in list(notice.items())[:4] if v not in (None, "")
            )
        ]
    if isinstance(notice, list):
        lines = []
        for item in notice[:5]:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("notice_title") or "").strip()
                body = str(item.get("content") or item.get("notice_content") or "").strip()
                when = str(item.get("create_time") or item.get("time") or "").strip()
                text = "\u3000".join(
                    part for part in (f"[{when}]" if when else "", title) if part
                )
                lines.append((text + (f"：{body}" if body else "")).strip())
            else:
                lines.append(str(item).strip())
        return [line for line in lines if line]
    return [str(notice)]


def _footer_lines(footer: Any) -> list[str]:
    """把 ``/api/selfbase/getBottomWord`` 的页脚公告（含客服电话）整理成行。"""
    if not isinstance(footer, dict):
        return []
    import re

    lines = []
    for key in ("bottomword", "bottomword2", "bottomword3"):
        raw = str(footer.get(key) or "")
        if not raw.strip():
            continue
        text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&quot;", '"').replace("&nbsp;", " ").replace("&amp;", "&")
        lines.extend(line.strip() for line in text.splitlines() if line.strip())
    return lines
