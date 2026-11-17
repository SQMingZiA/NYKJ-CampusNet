"""网络工具页：无线网络 / 网络诊断 / 流量统计 / 运行日志。

用 FluentUI 的 ``SegmentedWidget`` 做分区切换，每个分区是一张张 Fluent 卡片。
所有耗时操作都通过 ``run_async`` 丢到线程池，界面不阻塞。
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from PyQt5.QtCore import QDate, Qt, QTimer
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    CompactDateEdit,
    FluentIcon as FIF,
    HyperlinkButton,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SegmentedWidget,
    SmoothScrollArea,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
)

from .. import theme
from ..async_task import run_async
from ..charts import HeatmapChart, HistoryChart
from ..formatting import (
    format_bytes,
    format_datetime,
    format_duration,
    format_rate,
    group_by_day,
    signal_label,
)

from ..dialogs import confirm_typed
from ..toasts import Toast
from ..widgets import Card, KVGrid, StatTile, scroll_page

from ...net import diagnostics, wifi


def _make_table(headers: list[str], stretch_column: int = 0) -> TableWidget:
    table = TableWidget()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setBorderVisible(True)
    table.setBorderRadius(6)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.setWordWrap(False)

    table.setMouseTracking(True)
    table.setToolTip("鼠标移到单元格上可看完整内容；双击复制该格")
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Stretch)
    if 0 <= stretch_column < len(headers):
        header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)
    table.setMinimumHeight(180)
    table.doubleClicked.connect(lambda index: _copy_cell(table, index.row(), index.column()))
    return table


def _fill_table(table: TableWidget, rows: list[list[str]],
                tooltips: list[str] | None = None) -> None:
    """填表。

    每个单元格都会带 tooltip（内容就是完整文本），行首额外带一行摘要 ——
    这样鼠标移到任意一格都能看清完整信息，不用去拖列宽。
    """
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        summary = tooltips[r] if tooltips and r < len(tooltips) else " | ".join(
            str(v) for v in row if v)
        for c, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setToolTip(f"{summary}\n\n（双击可复制这一格）" if c == 0 else str(value))
            table.setItem(r, c, item)
    table.resizeRowsToContents()


def _copy_cell(table: TableWidget, row: int, column: int) -> None:
    from PyQt5.QtWidgets import QApplication

    item = table.item(row, column)
    if item is not None and item.text():
        QApplication.clipboard().setText(item.text())


def _scroll_page() -> tuple[SmoothScrollArea, QVBoxLayout]:
    area, layout = scroll_page(margins=(28, 24, 28, 28), spacing=16)
    layout.addStretch(1)
    return area, layout


def _insert(layout: QVBoxLayout, widget: QWidget) -> QWidget:
    """插到 stretch 之前，保证内容靠上。"""
    layout.insertWidget(max(0, layout.count() - 1), widget)
    return widget


class WifiView(QWidget):
    """无线网络：当前连接、可用网络、已保存配置。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._state = {}
        self._networks = []
        self._profiles: list[str] = []
        self._shown = False
        self._scanning = False
        self._scanned = False
        self._profiles_loading = False
        self._status_loading = False
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = _scroll_page()
        outer.addWidget(scroll)

        self.current_card = Card("当前连接", parent=self)
        self.kv = KVGrid(
            [
                ("state", "连接状态"),
                ("ssid", "网络名称"),
                ("bssid", "BSSID"),
                ("signal", "信号"),
                ("rate", "协商速率"),
                ("channel", "信道"),
                ("mac", "本机 MAC"),
                ("auth", "认证方式"),
            ],
            columns=2,
            parent=self,
        )
        self.current_card.add(self.kv)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.refresh_button = PushButton(FIF.SYNC, "刷新状态", self)
        self.refresh_button.clicked.connect(self.refresh_status)
        row.addWidget(self.refresh_button)
        self.disconnect_button = PushButton(FIF.CLOSE, "断开连接", self)
        self.disconnect_button.clicked.connect(self.disconnect)
        row.addWidget(self.disconnect_button)
        row.addStretch(1)
        self.current_card.add_layout(row)
        _insert(layout, self.current_card)

        self.scan_card = Card("可用无线网络", parent=self)
        scan_row = QHBoxLayout()
        scan_row.setSpacing(8)
        self.scan_button = PrimaryPushButton(FIF.SEARCH, "扫描网络", self)
        self.scan_button.setToolTip("扫描附近的无线网络；扫描在后台进行，不会卡住界面")
        self.scan_button.clicked.connect(self.scan)
        scan_row.addWidget(self.scan_button)
        self.connect_button = PushButton(FIF.CONNECT, "连接选中网络", self)
        self.connect_button.setToolTip("先在上表里选中一个网络，再点这里连接")
        self.connect_button.setEnabled(False)
        self.connect_button.clicked.connect(self.connect_selected)
        scan_row.addWidget(self.connect_button)
        scan_row.addStretch(1)
        self.scan_card.add_layout(scan_row)

        self.network_table = _make_table(["网络名称", "信号", "强度", "信道", "自动连接名单"])
        self.network_table.itemSelectionChanged.connect(self._update_connect_state)
        self.scan_card.add(self.network_table)
        _insert(layout, self.scan_card)

        self.profile_card = Card("已保存的配置文件", parent=self)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)
        self.profile_combo = ComboBox(self)
        self.profile_combo.setMinimumWidth(240)
        self.profile_combo.setPlaceholderText("还没有保存过 WiFi 配置")
        profile_row.addWidget(self.profile_combo)
        self.profile_connect = PushButton(FIF.WIFI, "连接", self)
        self.profile_connect.setEnabled(False)
        self.profile_connect.clicked.connect(self.connect_profile)
        profile_row.addWidget(self.profile_connect)
        self.profile_refresh = PushButton(FIF.SYNC, "刷新", self)
        self.profile_refresh.clicked.connect(self.load_profiles)
        profile_row.addWidget(self.profile_refresh)
        profile_row.addStretch(1)
        self.profile_card.add_layout(profile_row)
        _insert(layout, self.profile_card)

    def showEvent(self, event) -> None:
        """首次显示时自动填好状态、配置，并在后台扫描一次网络。"""
        super().showEvent(event)
        if self._shown:
            return
        self._shown = True
        self.refresh_status()
        self.load_profiles()
        self._fill_known_networks(force=True)
        if not self._scanned and not self._scanning:
            self.scan()

    def refresh_status(self) -> None:
        if self._status_loading:
            return
        self._status_loading = True
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("读取中…")
        run_async(self.service.wifi_status, on_done=self._apply_status, on_error=self._on_status_error)

    def load_profiles(self) -> None:
        if self._profiles_loading:
            return
        self._profiles_loading = True
        self.profile_refresh.setEnabled(False)
        self.profile_refresh.setText("刷新中…")
        run_async(
            self.service.saved_profiles,
            on_done=self._apply_profiles,
            on_error=self._on_profiles_error,
        )

    def scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.scan_button.setEnabled(False)
        self.scan_button.setText("扫描中…")
        run_async(
            self.service.scan_wifi,
            True,
            on_done=self._apply_scan,
            on_error=self._fail,
        )

    def disconnect(self) -> None:
        run_async(self.service.disconnect_wifi, on_done=lambda r: self._toast(r, "断开连接"))

    def connect_selected(self) -> None:
        rows = self.network_table.selectionModel().selectedRows() if self.network_table.selectionModel() else []
        if not rows:
            Toast.info(self, "请先选择", "在上表里点一下要连接的网络。")
            return
        index = rows[0].row()
        if index >= len(self._networks):
            return
        ssid = self._networks[index]["ssid"]
        self._connect_ssid(ssid)

    def connect_profile(self) -> None:
        ssid = self.profile_combo.currentText().strip()
        if not ssid:
            Toast.info(self, "还没有保存过配置", "先在系统里连接一次 WiFi，配置文件会自动出现。")
            return
        self._connect_ssid(ssid)

    def _connect_ssid(self, ssid: str) -> None:
        self.connect_button.setEnabled(False)
        self.profile_connect.setEnabled(False)
        Toast.info(self, "正在连接", f"正在连接 {ssid}…")

        def done(result: Any) -> None:
            self._update_connect_state()
            self.profile_connect.setEnabled(bool(self._profiles))
            self._toast(result, f"连接 {ssid}")
            self.refresh_status()

        run_async(self.service.connect_ssid, ssid, on_done=done, on_error=self._fail)

    def _update_connect_state(self) -> None:
        rows = self.network_table.selectionModel().selectedRows() if self.network_table.selectionModel() else []
        self.connect_button.setEnabled(bool(rows))

    def _apply_status(self, status: Any) -> None:
        self._status_loading = False
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("刷新状态")
        if not isinstance(status, dict):
            return
        rx = status.get("rx_rate_mbps")
        tx = status.get("tx_rate_mbps")
        rate = f"↓ {rx} / ↑ {tx} Mbps" if rx or tx else "—"
        signal = status.get("signal")
        self.kv.update_values({
            "state": status.get("state") or ("已连接" if status.get("connected") else "未连接"),
            "ssid": status.get("ssid") or "—",
            "bssid": status.get("bssid") or "—",
            "signal": f"{signal}%（{signal_label(signal)}）" if isinstance(signal, (int, float)) else "—",
            "rate": rate,
            "channel": str(status.get("channel") or "—"),
            "mac": status.get("mac") or "—",
            "auth": " / ".join(filter(None, [status.get("authentication") or "", status.get("cipher") or ""])) or "—",
        })

    def _on_status_error(self, message: str) -> None:
        self._status_loading = False
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("刷新状态")
        self.refresh_button.setToolTip((message or "").splitlines()[0])

    def _apply_profiles(self, result: Any) -> None:
        self._profiles_loading = False
        self.profile_refresh.setEnabled(True)
        self.profile_refresh.setText("刷新")
        if not isinstance(result, dict):
            return
        profiles = [str(name) for name in result.get("profiles") or []]
        self._profiles = profiles
        self.profile_combo.clear()
        for name in profiles:
            self.profile_combo.addItem(name)
        self.profile_combo.setPlaceholderText(
            "选择一个已保存的配置" if profiles else "还没有保存过 WiFi 配置"
        )
        self.profile_connect.setEnabled(bool(profiles))
        self.profile_refresh.setToolTip(f"已读取 {len(profiles)} 个配置" if profiles else "系统里还没有已保存的 WiFi 配置")
        self._fill_known_networks()

    def _on_profiles_error(self, message: str) -> None:
        self._profiles_loading = False
        self.profile_refresh.setEnabled(True)
        self.profile_refresh.setText("刷新")
        Toast.warning(self, "读取配置失败", (message or "").splitlines()[0])

    def _fill_known_networks(self, force: bool = False) -> None:
        """扫描结果回来前，先用已保存配置和自动连接名单把表格填上。"""
        if self._scanned and not force:
            return
        allow = list(getattr(self.service.settings, "wifi_allow_list", []) or [])
        names = list(dict.fromkeys(self._profiles + allow))
        self._networks = [
            {"ssid": name, "known": True, "in_allow_list": wifi.any_ssid_matches(name, allow)}
            for name in names
        ]
        rows = [
            [item["ssid"], "—", "—", "—", "是" if item["in_allow_list"] else "否"]
            for item in self._networks
        ]
        _fill_table(self.network_table, rows)
        if rows:
            self.scan_button.setToolTip(
                f"表中是已知网络（{len(rows)} 个）；点「扫描网络」获取附近的实时结果"
            )
        self.network_table.clearSelection()
        self._update_connect_state()

    def _apply_scan(self, result: Any) -> None:
        self._scanning = False
        self.scan_button.setEnabled(True)
        self.scan_button.setText("扫描网络")
        networks = list((result or {}).get("networks") or [])
        if not networks:
            self._scanned = False
            self._fill_known_networks(force=True)
            self.scan_button.setToolTip("这次没有扫描到网络；表中显示的是已知网络")
            Toast.warning(self, "没有扫描到网络", "请确认无线网卡已开启，然后重试。")
            return

        self._scanned = True
        self._networks = networks
        added = []
        campus = self.service.campus_candidates(self._networks)
        if campus:
            try:
                added = self.service.adopt_networks([n["ssid"] for n in campus])
            except Exception:
                added = []

        rows = []
        for item in self._networks:
            signal = item.get("signal")
            rows.append([
                item.get("ssid", ""),
                f"{signal}%" if isinstance(signal, (int, float)) else "—",
                signal_label(signal),
                str(item.get("channel") or "—"),
                "是" if item.get("in_allow_list") else "否",
            ])

        _fill_table(self.network_table, rows)
        self.network_table.clearSelection()
        self._update_connect_state()
        self.scan_button.setToolTip(f"共发现 {len(rows)} 个网络（点一下可重新扫描）")
        if added:
            Toast.info(self, "已加入自动连接名单", "、".join(added))

    def on_state(self, state: dict) -> None:
        self._state = state or {}
        wifi_state = self._state.get("wifi_status") or {}
        if wifi_state:
            self._apply_status(wifi_state)

    def _toast(self, result: Any, what: str) -> None:
        if isinstance(result, dict):
            if result.get("ok"):
                Toast.success(self, f"{what}成功", str(result.get("message", ""))[:120])
            else:
                Toast.warning(self, f"{what}未成功", str(result.get("message", ""))[:160])
        else:
            Toast.success(self, f"{what}完成", "")

    def _fail(self, message: str) -> None:
        self._scanning = False
        self.scan_button.setEnabled(True)
        self.scan_button.setText("扫描网络")
        self._profiles_loading = False
        self.profile_refresh.setEnabled(True)
        self.profile_refresh.setText("刷新")
        self._update_connect_state()
        self.profile_connect.setEnabled(bool(self._profiles))
        Toast.error(self, "操作失败", (message or "").splitlines()[0])

    def on_theme_changed(self) -> None:
        pass


# 整轮诊断的最长等待时间；超过就恢复按钮，避免一直停在「测试中」。
DIAG_TIMEOUT_SEC = 180.0


class DiagView(QWidget):
    """网络诊断：一次点击依次跑完连通性、延迟、DNS、下载、上传。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._run_id = 0
        self._running = False
        self._started = 0.0
        self._rows: list[list[str]] = []
        self._tips: list[str] = []
        self._steps = (
            ("connectivity", self._probe_connectivity),
            ("latency", lambda: diagnostics.latency_test(count=5)),
            ("dns", diagnostics.dns_benchmark),
            ("download", lambda: diagnostics.download_speed(duration_ms=8000)),
            ("upload", lambda: diagnostics.upload_speed(duration_ms=4000)),
        )
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = _scroll_page()
        outer.addWidget(scroll)

        card = Card("网络诊断", parent=self)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.speed_button = PrimaryPushButton(FIF.PLAY, "开始测试", self)
        self.speed_button.setToolTip("依次测试连通性、延迟、DNS、下载与上传，整轮最长 180 秒")
        self.speed_button.clicked.connect(self.start_tests)
        self.run_button = self.speed_button
        row.addWidget(self.speed_button)
        row.addStretch(1)
        card.add_layout(row)

        self.progress = ProgressBar(self)
        self.progress.setRange(0, len(self._steps))
        self.progress.setValue(0)
        self.progress.setVisible(False)
        card.add(self.progress)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tile_latency = StatTile("延迟", "—", "未测试", parent=self)
        self.tile_jitter = StatTile("抖动", "—", "未测试", parent=self)
        self.tile_down = StatTile("下载", "—", "未测试", parent=self)
        self.tile_up = StatTile("上传", "—", "未测试", parent=self)
        self.tile_dns = StatTile("DNS 最快响应", "—", "未测试", parent=self)
        for tile in (self.tile_latency, self.tile_jitter, self.tile_down, self.tile_up, self.tile_dns):
            tiles.addWidget(tile, 1)
        card.add_layout(tiles)

        self.kv = KVGrid(
            [
                ("gateway", "默认网关"),
                ("local_ip", "本机 IP"),
                ("public_ip", "公网 IP"),
                ("dns", "DNS 服务器"),
                ("route", "出口路由"),
            ],
            columns=2,
            parent=self,
        )
        card.add(self.kv)

        self.result_table = _make_table(["探测项目", "结果", "耗时 / 速率", "备注"])
        self.result_table.setMinimumHeight(240)
        self.speed_table = self.result_table
        card.add(self.result_table)
        _insert(layout, card)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._check_timeout)

    def _probe_connectivity(self) -> dict:
        """第一步：网关、本机 / 公网 IP、系统 DNS、默认路由与网关连通性。"""
        from ...core.sysinfo import local_interfaces

        interfaces = local_interfaces()
        iface = interfaces[0] if interfaces else {}
        routes = diagnostics.routes()
        public = self.service.fetch_public_ip()
        default_route = (routes.get("default_routes") or [{}])[0]
        gateway = str(
            default_route.get("gateway")
            or ((iface.get("gateway") or [""])[0])
            or ""
        )
        ping_result = diagnostics.ping(gateway, 3) if gateway else {"ok": False, "message": "没有默认网关"}
        return {
            "gateway": gateway,
            "local_ip": ", ".join(iface.get("ipv4") or []),
            "public_ip": public.get("ip") if public.get("ok") else "",
            "public_message": public.get("message") or "",
            "dns": iface.get("dns") or [],
            "routes": routes.get("default_routes") or [],
            "ping": ping_result,
        }

    def start_tests(self) -> None:
        if self._running:
            return
        self._running = True
        self._run_id += 1
        self._started = time.time()
        self._rows = []
        self._tips = []
        for tile in (self.tile_latency, self.tile_jitter, self.tile_down, self.tile_up, self.tile_dns):
            tile.set_value("—", "未测试")
            tile.setToolTip("")
        self.kv.update_values({
            "gateway": "—", "local_ip": "—", "public_ip": "—", "dns": "—", "route": "—",
        })
        _fill_table(self.result_table, [])
        self.speed_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._timer.start()
        self._run_step(0, self._run_id)

    def _run_step(self, index: int, token: int) -> None:
        if token != self._run_id or not self._running:
            return
        if index >= len(self._steps):
            self._finish()
            return
        self.speed_button.setText(f"测试中 {index + 1}/{len(self._steps)}…")
        self.progress.setValue(index)
        task = self._steps[index][1]
        run_async(
            task,
            on_done=lambda result, i=index, t=token: self._step_done(i, result, t),
            on_error=lambda message, i=index, t=token: self._step_failed(i, message, t),
        )

    def _step_done(self, index: int, result: Any, token: int) -> None:
        if token != self._run_id:
            return
        name = self._steps[index][0]
        apply = {
            "connectivity": self._apply_connectivity,
            "latency": self._apply_latency,
            "dns": self._apply_dns,
            "download": self._apply_download,
            "upload": self._apply_upload,
        }[name]
        try:
            apply(result)
        except Exception as exc:
            self._step_failed(index, f"{type(exc).__name__}: {exc}", token)
            return
        self.progress.setValue(index + 1)
        self._run_step(index + 1, token)

    def _step_failed(self, index: int, message: str, token: int) -> None:
        if token != self._run_id:
            return
        self._mark_failed(index, (message or "").splitlines()[0])
        self.progress.setValue(index + 1)
        self._run_step(index + 1, token)

    def _mark_failed(self, index: int, message: str) -> None:
        """把某一步的失败原因写进 tile 的 subtitle 和 tooltip。"""
        name = self._steps[index][0]
        if name == "connectivity":
            self.kv.update_values({
                "gateway": "—", "local_ip": "—", "public_ip": "—", "dns": "—", "route": "—",
            })
            self._add_row("连通性概览", "失败", "—", message)
        elif name == "latency":
            for tile in (self.tile_latency, self.tile_jitter):
                tile.set_value("—", "失败")
                tile.setToolTip(message)
            self._add_row("延迟测试", "失败", "—", message)
        elif name == "dns":
            self.tile_dns.set_value("—", "失败")
            self.tile_dns.setToolTip(message)
            self._add_row("DNS 基准", "失败", "—", message)
        elif name == "download":
            self.tile_down.set_value("—", "失败")
            self.tile_down.setToolTip(message)
            self._add_row("下载测速", "失败", "—", message)
        elif name == "upload":
            self.tile_up.set_value("—", "失败")
            self.tile_up.setToolTip(message)
            self._add_row("上传测速", "失败", "—", message)

    def _add_row(self, item: str, result: str, metric: str, note: str) -> None:
        self._rows.append([item, result, metric, note])
        self._tips.append(f"{item}\n结果：{result}\n耗时 / 速率：{metric}\n备注：{note}")
        _fill_table(self.result_table, self._rows, self._tips)

    def _apply_connectivity(self, result: Any) -> None:
        result = result or {}
        gateway = str(result.get("gateway") or "—")
        local_ip = str(result.get("local_ip") or "—")
        public_ip = str(result.get("public_ip") or "")
        dns = ", ".join(result.get("dns") or []) or "—"
        routes = result.get("routes") or []
        route = routes[0] if routes else {}
        route_text = (
            f"{route.get('gateway') or '—'} · {route.get('interface') or '—'}" if route else "—"
        )
        self.kv.update_values({
            "gateway": gateway,
            "local_ip": local_ip,
            "public_ip": public_ip or "查询失败",
            "dns": dns,
            "route": route_text,
        })

        ping_result = result.get("ping") or {}
        if ping_result.get("ok"):
            self._add_row(
                "默认网关",
                f"{ping_result.get('avg_ms')} ms",
                f"丢包 {ping_result.get('loss_percent', 0)}%",
                gateway,
            )
        else:
            self._add_row("默认网关", "不可达", "—", str(ping_result.get("message") or gateway))
        if public_ip:
            self._add_row("公网出口", public_ip, "—", "公网 IP 查询成功")
        else:
            self._add_row(
                "公网出口", "查询失败", "—",
                str(result.get("public_message") or "所有查询源都不可用"),
            )
        for item in routes[:2]:
            self._add_row(
                "默认路由",
                str(item.get("gateway") or "—"),
                f"接口 {item.get('interface') or '—'}",
                f"metric {item.get('metric') or '—'}",
            )

    def _apply_latency(self, result: Any) -> None:
        result = result or {}
        targets = result.get("targets") or []
        for target in targets:
            self._add_row(
                f"延迟 · {target.get('name')}",
                f"{target.get('avg_ms')} ms" if target.get("ok") else "不可达",
                f"丢包 {target.get('loss') if target.get('loss') is not None else 100}%",
                f"抖动 {target.get('jitter_ms')} ms · {target.get('host')}:{target.get('port')}",
            )
        best = result.get("best") or {}
        if best:
            self.tile_latency.set_value(f"{best.get('avg_ms')} ms", f"最快 · {best.get('name')}")
            self.tile_jitter.set_value(f"{best.get('jitter_ms')} ms", f"抖动 · {best.get('name')}")
            self.tile_latency.setToolTip(f"{best.get('name')}（{best.get('host')}）平均 {best.get('avg_ms')} ms")
            self.tile_jitter.setToolTip(f"{best.get('name')} 抖动 {best.get('jitter_ms')} ms")
        else:
            for tile in (self.tile_latency, self.tile_jitter):
                tile.set_value("—", "失败")
                tile.setToolTip("所有延迟目标都不可达")

    def _apply_dns(self, result: Any) -> None:
        result = result or {}
        servers = result.get("servers") or []
        if servers:
            self.kv.set("dns", ", ".join(servers))
        entries = result.get("results") or []
        for entry in entries:
            self._add_row(
                f"DNS · {entry.get('name')}",
                f"{entry.get('ms')} ms" if entry.get("ok") else "解析失败",
                ", ".join(entry.get("addresses") or []) or "—",
                "解析成功" if entry.get("ok") else "没有返回地址",
            )
        usable = [entry for entry in entries if entry.get("ok")]
        if usable:
            best = min(usable, key=lambda entry: entry.get("ms") or 0)
            self.tile_dns.set_value(f"{best.get('ms')} ms", f"最快 · {best.get('name')}")
            self.tile_dns.setToolTip(f"{best.get('name')} 解析耗时 {best.get('ms')} ms")
        else:
            self.tile_dns.set_value("—", "失败")
            self.tile_dns.setToolTip("所有 DNS 域名都解析失败")

    def _apply_download(self, result: Any) -> None:
        result = result or {}
        if result.get("ok"):
            label = str(result.get("label") or "")
            self.tile_down.set_value(
                f"{result.get('mbps')} Mbps",
                f"{result.get('mb_per_sec')} MB/s · {label}",
            )
            self.tile_down.setToolTip(
                f"来源：{label}\nURL：{result.get('url')}\n用时 {result.get('seconds')} s"
            )
            self._add_row(
                "下载测速",
                f"{result.get('mbps')} Mbps",
                f"{result.get('seconds')} s",
                f"{label} · {format_bytes(result.get('bytes'))}",
            )
        else:
            message = str(result.get("message") or "所有下载源都不可用")
            self.tile_down.set_value("—", "失败")
            self.tile_down.setToolTip(message)
            self._add_row("下载测速", "失败", "—", message)
        for failed in result.get("tried") or []:
            self._add_row(
                f"下载源 · {failed.get('label') or '—'}",
                "跳过",
                "—",
                str(failed.get("error") or "")[:120],
            )

    def _apply_upload(self, result: Any) -> None:
        result = result or {}
        if result.get("ok"):
            label = str(result.get("label") or "")
            self.tile_up.set_value(
                f"{result.get('mbps')} Mbps",
                f"{result.get('mb_per_sec')} MB/s · {label}",
            )
            self.tile_up.setToolTip(
                f"来源：{label}\n用时 {result.get('seconds')} s"
                + (f"\n{result.get('note')}" if result.get("note") else "")
            )
            self._add_row(
                "上传测速",
                f"{result.get('mbps')} Mbps",
                f"{result.get('seconds')} s",
                str(result.get("note") or label),
            )
        else:
            message = str(result.get("message") or "上传测速端点不可达")
            self.tile_up.set_value("—", "失败")
            self.tile_up.setToolTip(message)
            self._add_row("上传测速", "失败", "—", message)
        for failed in result.get("tried") or []:
            self._add_row(
                f"上传源 · {failed.get('label') or '—'}",
                "跳过",
                "—",
                str(failed.get("error") or "")[:120],
            )

    def _finish(self) -> None:
        self._running = False
        self._timer.stop()
        self.speed_button.setEnabled(True)
        self.speed_button.setText("开始测试")
        self.progress.setValue(len(self._steps))
        self.progress.setVisible(False)
        Toast.success(self, "测试完成", "五项结果已写入上方表格，失败项可在备注中查看原因。")

    def _check_timeout(self) -> None:
        if not self._running:
            return
        if time.time() - self._started < DIAG_TIMEOUT_SEC:
            return
        current = min(self.progress.value(), len(self._steps) - 1)
        self._run_id += 1
        self._running = False
        self._timer.stop()
        self.speed_button.setEnabled(True)
        self.speed_button.setText("开始测试")
        self.progress.setVisible(False)
        self._mark_failed(current, f"整轮测试超过 {int(DIAG_TIMEOUT_SEC)} 秒，已中止")
        Toast.error(self, "测试超时", "网络诊断超过 180 秒仍未完成，请检查网络后重试。")

    def on_theme_changed(self) -> None:
        pass


class _RangeDialog(QDialog):
    """自定义时间段：选择起止日期，结束当天也计入。"""

    def __init__(self, start: dt.date, end: dt.date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自定义时间段")
        self.setModal(True)
        self.setMinimumWidth(340)

        box = QVBoxLayout(self)
        box.setContentsMargins(20, 20, 20, 16)
        box.setSpacing(12)

        start_row = QHBoxLayout()
        start_row.addWidget(StrongBodyLabel("开始日期", self))
        self.start_edit = QDateEdit(QDate(start.year, start.month, start.day), self)
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("yyyy-MM-dd")
        start_row.addWidget(self.start_edit, 1)
        box.addLayout(start_row)

        end_row = QHBoxLayout()
        end_row.addWidget(StrongBodyLabel("结束日期", self))
        self.end_edit = QDateEdit(QDate(end.year, end.month, end.day), self)
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("yyyy-MM-dd")
        end_row.addWidget(self.end_edit, 1)
        box.addLayout(end_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = PushButton("取消", self)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        confirm = PrimaryPushButton("查看", self)
        confirm.clicked.connect(self.accept)
        buttons.addWidget(confirm)
        box.addLayout(buttons)

    def selected_range(self) -> tuple[dt.datetime, dt.datetime]:
        start = self.start_edit.date().toPyDate()
        end = self.end_edit.date().toPyDate()
        if end < start:
            start, end = end, start
        return (
            dt.datetime.combine(start, dt.time()),
            dt.datetime.combine(end + dt.timedelta(days=1), dt.time()),
        )


class TrafficView(QWidget):
    """流量统计：默认最近 7 天，可切换上一个 7 天或自定义时间段。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._summary = {}
        self._mode_known = False
        self._mode_retry = 0
        self._period_index = 0
        self._custom_range: tuple[dt.datetime, dt.datetime] | None = None
        self._range_token = 0
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = _scroll_page()
        outer.addWidget(scroll)

        overview = Card("用量概览", parent=self)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tile_today = StatTile("今日", "-", parent=self)
        self.tile_yesterday = StatTile("昨日", "-", parent=self)
        self.tile_week = StatTile("本周", "-", parent=self)
        self.tile_month = StatTile("本月", "-", parent=self)
        for tile in (self.tile_today, self.tile_yesterday, self.tile_week, self.tile_month):
            tiles.addWidget(tile, 1)
        overview.add_layout(tiles)

        tiles2 = QHBoxLayout()
        tiles2.setSpacing(12)
        self.tile_all = StatTile("累计", "-", parent=self)
        self.tile_session = StatTile("本次运行", "-", parent=self)
        self.tile_rate = StatTile("当前速率", "-", parent=self)
        self.tile_source = StatTile("统计方式", "-", parent=self)
        for tile in (self.tile_all, self.tile_session, self.tile_rate, self.tile_source):
            tiles2.addWidget(tile, 1)
        overview.add_layout(tiles2)
        _insert(layout, overview)

        chart_card = Card("用量趋势", parent=self)
        chart_row = QHBoxLayout()
        chart_row.setSpacing(8)
        chart_row.addWidget(StrongBodyLabel("时间范围", self))
        self.period_combo = ComboBox(self)
        self.period_combo.addItems(["最近 7 天", "上一个 7 天", "自定义…"])
        self.period_combo.setCurrentIndex(0)
        self.period_combo.setMinimumWidth(150)
        chart_row.addWidget(self.period_combo)
        chart_row.addStretch(1)
        self.refresh_button = PushButton(FIF.SYNC, "刷新", self)
        self.refresh_button.clicked.connect(self.refresh)
        chart_row.addWidget(self.refresh_button)
        self.export_button = PushButton(FIF.SAVE, "导出 CSV", self)
        self.export_button.clicked.connect(self.export_csv)
        chart_row.addWidget(self.export_button)
        self.reset_button = PushButton(FIF.DELETE, "重置统计", self)
        self.reset_button.clicked.connect(self.reset)
        chart_row.addWidget(self.reset_button)
        chart_card.add_layout(chart_row)

        range_tiles = QHBoxLayout()
        range_tiles.setSpacing(12)
        self.tile_range_total = StatTile("区间总量", "—", "未读取", parent=self)
        self.tile_range_avg = StatTile("日均", "—", "未读取", parent=self)
        self.tile_range_peak = StatTile("峰值单日", "—", "未读取", parent=self)
        for tile in (self.tile_range_total, self.tile_range_avg, self.tile_range_peak):
            range_tiles.addWidget(tile, 1)
        chart_card.add_layout(range_tiles)

        self.chart_card = chart_card
        self.chart = HistoryChart(parent=self)
        chart_card.add(self.chart)
        _insert(layout, chart_card)

        heat_card = Card("用量热力图", parent=self)
        heat_card.add(BodyLabel("最近 12 周，每天一格；点一下看当天用量。", self))
        self.heatmap = HeatmapChart(parent=self)
        heat_card.add(self.heatmap)
        self.heatmap_summary = BodyLabel("", parent=self)
        heat_card.add(self.heatmap_summary)
        self.heatmap.dayClicked.connect(self._on_heatmap_day)
        _insert(layout, heat_card)

        detail = Card("最近明细", parent=self)
        self.table = _make_table(["时间", "下行", "上行", "合计"])
        detail.add(self.table)
        _insert(layout, detail)

        self.period_combo.currentIndexChanged.connect(self._on_period_changed)

    def _default_range(self) -> tuple[dt.datetime, dt.datetime]:
        today = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return today - dt.timedelta(days=6), today + dt.timedelta(days=1)

    def _current_range(self) -> tuple[dt.datetime, dt.datetime]:
        today = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if self._period_index == 1:
            return today - dt.timedelta(days=13), today - dt.timedelta(days=6)
        if self._period_index == 2 and self._custom_range is not None:
            return self._custom_range
        return self._default_range()

    def _on_period_changed(self, index: int) -> None:
        if index == 2:
            start, end = self._custom_range or self._default_range()
            dialog = _RangeDialog(start.date(), (end - dt.timedelta(days=1)).date(), self)
            if dialog.exec_():
                self._custom_range = dialog.selected_range()
                self._period_index = 2
                self.refresh()
            else:
                self.period_combo.blockSignals(True)
                self.period_combo.setCurrentIndex(self._period_index)
                self.period_combo.blockSignals(False)
            return
        self._period_index = index
        self.refresh()

    def _fill_days(self, daily: list[dict], start: dt.datetime, end: dt.datetime) -> list[dict]:
        """把没有记录的自然日补成 0，7 天视图才不会缺柱子。"""
        by_key = {str(item.get("key")): item for item in daily}
        points = []
        cursor = start.date()
        last = (end - dt.timedelta(days=1)).date()
        while cursor <= last:
            key = cursor.isoformat()
            points.append(
                by_key.get(key)
                or {
                    "key": key,
                    "ts": dt.datetime.combine(cursor, dt.time()).timestamp(),
                    "rx": 0,
                    "tx": 0,
                    "total": 0,
                }
            )
            cursor += dt.timedelta(days=1)
        return points

    def refresh(self) -> None:
        self._range_token += 1
        token = self._range_token
        run_async(self.service.traffic.summary, on_done=self._apply_summary)
        start, end = self._current_range()
        run_async(
            self.service.traffic_range,
            start.timestamp(),
            end.timestamp(),
            on_done=lambda result, t=token: self._apply_range(result, t),
            on_error=lambda message, t=token: self._range_failed(message, t),
        )
        run_async(
            self.service.traffic_daily,
            84,
            on_done=self._apply_heatmap,
            on_error=lambda _m: self.heatmap_summary.setText("热力图加载失败"),
        )

    def _apply_heatmap(self, rows: Any) -> None:
        """热力图数据到位：刷新格子并把汇总设为最近 12 周总量。"""
        items = [item for item in (rows or []) if isinstance(item, dict)]
        self.heatmap.set_days(items)
        total = sum(int(item.get("total") or 0) for item in items)
        self.heatmap_summary.setText(f"最近 12 周共 {format_bytes(total)}")

    def _on_heatmap_day(self, date: str) -> None:
        """点选某一天：在说明行显示当天用量，不跳转页面。"""
        row = next(
            (item for item in self.heatmap._rows if str(item.get("date")) == date),
            None,
        )
        short = date[5:] if len(date) >= 10 else date
        if row is None:
            self.heatmap_summary.setText(f"{short} · 没有记录")
            return
        self.heatmap_summary.setText(
            f"{short} · 共 {format_bytes(row.get('total'))}"
            f"（↓ {format_bytes(row.get('rx'))} ↑ {format_bytes(row.get('tx'))}）"
        )

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出流量数据", "campusnet-traffic.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        run_async(
            self.service.export_traffic_csv,
            path,
            on_done=lambda r: Toast.success(self, "已导出", str((r or {}).get("path", path)))
            if (r or {}).get("ok")
            else Toast.error(self, "导出失败", str((r or {}).get("message", ""))),
        )

    def reset(self) -> None:
        run_async(self.service.reset_traffic, on_done=lambda _r: (Toast.info(self, "已重置", "流量统计已清零。"), self.refresh()))

    def _apply_summary(self, summary: Any) -> None:
        if not isinstance(summary, dict):
            return
        self._summary = summary
        today = summary.get("today") or {}
        yesterday = summary.get("yesterday") or {}
        week = summary.get("week") or {}
        month = summary.get("month") or {}
        alltime = summary.get("all") or {}
        session = summary.get("session") or {}
        live = summary.get("live") or {}

        for tile, data in (
            (self.tile_today, today),
            (self.tile_yesterday, yesterday),
            (self.tile_week, week),
            (self.tile_month, month),
            (self.tile_all, alltime),
            (self.tile_session, session),
        ):
            tile.set_value(
                format_bytes(data.get("total")),
                f"↓ {format_bytes(data.get('rx'))}  ↑ {format_bytes(data.get('tx'))}",
            )

        rx_rate = live.get("rx_rate") or 0.0
        tx_rate = live.get("tx_rate") or 0.0
        self.tile_rate.set_value(
            format_rate(rx_rate + tx_rate),
            f"↓ {format_rate(rx_rate)}  ↑ {format_rate(tx_rate)}",
        )

        mode = live.get("mode") or ""
        mode_label = {"system": "整机网卡", "adapters": "按网卡"}.get(mode, "")
        if mode_label:
            hint = "读取正常" if summary.get("healthy", True) else "读取失败"
            self.tile_source.reset_color()
        else:
            mode_label = "读取中…"
            hint = "等待第一次采样"
        if not summary.get("healthy", True):
            mode_label = "读取失败"
            hint = str(summary.get("retry_hint") or "")[:40] or "需要管理员权限"
            self.tile_source.set_color(theme.color("warn"))
        self.tile_source.set_value(mode_label, hint)
        self._mode_known = bool(mode)

    def _apply_range(self, result: Any, token: int | None = None) -> None:
        if token is not None and token != self._range_token:
            return
        if not isinstance(result, dict):
            self._range_failed("没有取到时间段数据")
            return
        summary = result.get("summary") or {}
        series = list(result.get("series") or [])
        start, end = self._current_range()
        day_count = max(1, (end - start).days)
        total = summary.get("total")
        if total is None:
            total = sum((item.get("total") or 0) for item in series)
        daily = group_by_day(series)
        range_text = f"{start:%m-%d} ~ {(end - dt.timedelta(days=1)):%m-%d}"

        if not series and not total:
            self.tile_range_total.set_value("—", "这个时间段还没有记录")
            self.tile_range_avg.set_value("—", "这个时间段还没有记录")
            self.tile_range_peak.set_value("—", "这个时间段还没有记录")
            self.chart.set_data([])
            self.chart.setToolTip("这个时间段还没有记录")
            _fill_table(self.table, [["这个时间段还没有记录", "", "", ""]])
            return

        peak = max(daily, key=lambda item: item.get("total") or 0) if daily else None
        self.tile_range_total.set_value(format_bytes(total), range_text)
        self.tile_range_avg.set_value(format_bytes(total / day_count), f"共 {day_count} 天")
        if peak:
            self.tile_range_peak.set_value(
                format_bytes(peak.get("total")), f"峰值 · {peak.get('key')}"
            )
        else:
            self.tile_range_peak.set_value("—", "没有单日记录")

        points = self._fill_days(daily, start, end) if day_count <= 62 else daily
        points = points[-90:]
        self.chart.setToolTip("")
        try:
            self.chart.set_data(points, label_key="key")
        except Exception:
            pass

        rows = []
        for item in reversed(points[-31:]):
            rows.append([
                str(item.get("key", "")),
                format_bytes(item.get("rx")),
                format_bytes(item.get("tx")),
                format_bytes(item.get("total")),
            ])
        _fill_table(self.table, rows)
        self.table.resizeRowsToContents()

    def _range_failed(self, message: str, token: int | None = None) -> None:
        if token is not None and token != self._range_token:
            return
        for tile in (self.tile_range_total, self.tile_range_avg, self.tile_range_peak):
            tile.set_value("—", "读取失败")
        _fill_table(self.table, [["这个时间段读取失败", "", "", ""]])
        Toast.warning(self, "读取时间段失败", (message or "").splitlines()[0])

    def on_traffic(self, sample: dict) -> None:
        rx_rate = sample.get("rx_rate") or 0.0
        tx_rate = sample.get("tx_rate") or 0.0
        self.tile_rate.set_value(
            format_rate(rx_rate + tx_rate),
            f"↓ {format_rate(rx_rate)}  ↑ {format_rate(tx_rate)}",
        )
        if not self._mode_known and self._mode_retry < 3:
            self._mode_retry += 1
            self.refresh()

    def on_theme_changed(self) -> None:
        try:
            self.chart.apply_theme()
        except Exception:
            pass
        try:
            self.heatmap.update()
        except Exception:
            pass


class LogsView(QWidget):
    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._entries = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 28)
        outer.setSpacing(16)

        card = Card("运行日志", parent=self)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.auto_scroll = SwitchButton(self)
        self.auto_scroll.setOnText("自动滚动")
        self.auto_scroll.setOffText("自动滚动")
        self.auto_scroll.setChecked(True)
        row.addWidget(self.auto_scroll)
        row.addStretch(1)

        load_button = PushButton(FIF.HISTORY, "载入历史", self)
        load_button.clicked.connect(self.load_history)
        row.addWidget(load_button)
        copy_button = PushButton(FIF.COPY, "复制全部", self)
        copy_button.clicked.connect(self.copy_all)
        row.addWidget(copy_button)
        clear_button = PushButton(FIF.BROOM, "清空", self)
        clear_button.clicked.connect(self.clear)
        row.addWidget(clear_button)
        card.add_layout(row)

        self.view = PlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setMinimumHeight(420)
        card.add(self.view)
        outer.addWidget(card)
        outer.addStretch(1)

    def load_history(self) -> None:
        run_async(self.service.logs, 400, on_done=self._apply_history)

    def _apply_history(self, entries: Any) -> None:
        self.view.clear()
        self._entries = []
        for entry in entries or []:
            self.append_log(entry)

    def copy_all(self) -> None:
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(self.view.toPlainText())
        Toast.success(self, "已复制", "日志已复制到剪贴板。")

    def clear(self) -> None:
        self._entries.clear()
        self.view.clear()
        self.service.clear_logs()

    def append_log(self, entry: dict) -> None:
        if not isinstance(entry, dict):
            return
        self._entries.append(entry)
        del self._entries[:-1000]
        stamp = format_datetime(entry.get("at"))
        level = str(entry.get("level", "info")).upper()
        line = f"[{stamp}] {level:<5} {entry.get('message', '')}"
        detail = entry.get("detail")
        if detail:
            line += f"\n{' ' * 30}{detail}"
        self.view.appendPlainText(line)
        if self.auto_scroll.isChecked():
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def on_theme_changed(self) -> None:
        pass






def _as_list(payload: Any) -> list:
    """list"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("list", "data", "logs", "devices", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _fmt_ts(value: Any) -> str:
    """把秒级时间戳或已经是字符串的时间统一成可读文本。"""
    if value in (None, "", 0, "0"):
        return "—"
    text = str(value)
    if text.isdigit() and len(text) >= 9:
        return format_datetime(float(text))
    return text

class AccountView(QWidget):
    """账户概览 + 在线设备（数据来自学校自助系统）。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._window = None
        self._devices = []
        self._build_ui()

    def bind_window(self, window) -> None:
        self._window = window

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = _scroll_page()
        outer.addWidget(scroll)

        self.card = Card("账户概览", parent=self)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.refresh_button = PrimaryPushButton(FIF.SYNC, "刷新账户信息", self)
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        row.addStretch(1)
        self.card.add_layout(row)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tile_package = StatTile("套餐", "-", "—", parent=self)
        self.tile_expire = StatTile("到期时间", "-", "—", parent=self)
        self.tile_status = StatTile("账号状态", "-", "—", parent=self)
        self.tile_balance = StatTile("余额", "-", "—", parent=self)
        for tile in (self.tile_package, self.tile_expire, self.tile_status, self.tile_balance):
            tiles.addWidget(tile, 1)
        self.card.add_layout(tiles)

        self.grid = KVGrid(
            [
                ("username", "账号"),
                ("user_id", "用户 ID"),
                ("realname", "实名 / 备注"),
                ("created", "开户时间"),
                ("ip", "当前 IP"),
                ("online", "在线情况"),
                ("flow", "套餐流量"),
                ("device_count", "在线设备"),
                ("server", "接入设备"),
            ],
            columns=2,
            parent=self,
        )
        self.card.add(self.grid)
        _insert(layout, self.card)

        self.device_card = Card("在线设备", parent=self)
        dev_row = QHBoxLayout()
        dev_row.setSpacing(8)
        self.device_button = PushButton(FIF.PEOPLE, "查询在线设备", self)
        self.device_button.clicked.connect(self.load_devices)
        dev_row.addWidget(self.device_button)
        self.kick_button = PushButton(FIF.POWER_BUTTON, "下线选中设备", self)
        self.kick_button.clicked.connect(self.kick_selected)
        dev_row.addWidget(self.kick_button)
        self.kick_others_button = PushButton(FIF.DELETE, "下线其它设备", self)
        self.kick_others_button.clicked.connect(self.kick_others)
        dev_row.addWidget(self.kick_others_button)
        dev_row.addStretch(1)
        self.device_card.add_layout(dev_row)

        self.device_table = _make_table(["设备 MAC", "IP 地址", "上线时间", "在线时长", "会话号", "备注"])
        self.device_card.add(self.device_table)
        _insert(layout, self.device_card)


    def refresh(self) -> None:
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("读取中…")
        run_async(self.service.selfservice_overview, on_done=self._apply_overview, on_error=self._fail)

    def load_devices(self) -> None:
        self.device_button.setEnabled(False)
        self.device_button.setText("查询中…")
        run_async(self.service.selfservice_devices, on_done=self._apply_devices, on_error=self._fail)

    def kick_selected(self) -> None:
        rows = self.device_table.selectionModel().selectedRows() if self.device_table.selectionModel() else []
        if not rows:
            Toast.info(self, "请先选择", "在设备表里点一行再下线。")
            return
        index = rows[0].row()
        if index >= len(self._devices):
            return
        self._kick(self._devices[index])

    def kick_others(self) -> None:
        others = [d for d in self._devices if not d.get("_current")]
        if not others:
            Toast.info(self, "没有其它设备", "当前只有这一台设备在线。")
            return
        self._kick(others[0])

    def _kick(self, device: dict) -> None:
        session_id = str(device.get("AcctSessionId") or device.get("session_id") or "")
        mac = str(device.get("CallingStationId") or device.get("mac") or "")
        if not session_id:
            Toast.warning(self, "无法下线", "这个设备没有可用的会话号。")
            return
        self.kick_button.setEnabled(False)
        self.kick_others_button.setEnabled(False)

        def done(result: Any) -> None:
            self.kick_button.setEnabled(True)
            self.kick_others_button.setEnabled(True)
            if isinstance(result, dict) and result.get("ok"):
                data = result.get("data")
                Toast.success(self, "已下线", str(data) if data else f"设备 {mac} 已下线")
                self.load_devices()
            else:
                Toast.warning(self, "下线未成功", str((result or {}).get("message", "")))

        run_async(self.service.selfservice_down_device, session_id, on_done=done, on_error=self._fail)
    def _apply_overview(self, result: Any) -> None:
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("刷新账户信息")
        if not (isinstance(result, dict) and result.get("ok")):
            Toast.warning(self, "账户信息读取失败",
                          str((result or {}).get("message") or "请检查账号密码"))
            return

        account = result.get("account") or {}
        flow = result.get("flow") or {}
        status = str(account.get("user_status") or "")
        status_text = {"0": "未激活", "1": "正常", "4": "已停机"}.get(status, status or "—")

        self.tile_package.set_value(account.get("product_name") or "—",
                                    f"余额 {account.get('balance') or '0.00'} 元")
        self.tile_expire.set_value(account.get("order_end_time") or "—", "套餐到期时间")
        self.tile_status.set_value(status_text, "user_status=" + status if status else "—")
        self.tile_balance.set_value(f"{account.get('balance') or '0.00'} 元", "账户余额")

        online = account.get("onlineInfo") or {}
        records = online.get("records") if isinstance(online, dict) else None
        first = (records or [{}])[0] if records else {}
        used = flow.get("used") or "—"
        left = flow.get("flowBalanceFormat") or "—"
        balance = account.get("balance") or "0.00"
        self.tile_package.set_value(
            account.get("product_name") or "—", f"余额 {balance} 元 · 剩余 {left}"
        )
        self.tile_expire.set_value(account.get("order_end_time") or "—", "套餐到期时间")
        self.tile_status.set_value(status_text, "user_status=" + status if status else "—")
        self.tile_balance.set_value(f"{balance} 元", f"套餐流量已用 {used}")
        self.grid.update_values(
            {
                "username": account.get("username") or "—",
                "user_id": str(account.get("id") or "—"),
                "realname": str(account.get("realname") or "—"),
                "created": _fmt_ts(account.get("create_time")),
                "ip": str(first.get("FramedIPAddress") or "—"),
                "online": ("在线" if str(online.get("status")) == "online" else "离线") if online else "—",
                "flow": f"已用 {used} / 剩余 {left}",
                "device_count": str(len(records or [])) + " 台",
                "server": str(first.get("NASIPAddress") or "—"),
            }
        )

        devices = _as_list(result.get("devices") or {})
        self._apply_devices({"ok": True, "devices": devices})

    def _apply_devices(self, result: Any) -> None:
        self.device_button.setEnabled(True)
        self.device_button.setText("查询在线设备")
        if not (isinstance(result, dict) and result.get("ok")):
            self._devices = []
            _fill_table(self.device_table, [["—", "—", "—", "—", "—", str((result or {}).get("message") or "查询失败")]])
            return
        self._devices = list(result.get("devices") or [])
        current_mac = str((self.service.wifi_status() or {}).get("mac") or "").lower()
        rows, tips = [], []
        for device in self._devices:
            mac = str(device.get("CallingStationId") or "")
            device["_current"] = bool(current_mac) and mac.lower() == current_mac
            session_id = str(device.get("AcctSessionId") or "")
            rows.append(
                [
                    mac,
                    str(device.get("FramedIPAddress") or ""),
                    _fmt_ts(device.get("AcctStartTime")),
                    format_duration(device.get("AcctSessionTime")),
                    session_id,
                    "当前设备" if device["_current"] else "",
                ]
            )
            tips.append(
                f"设备 {mac}\nIP {device.get('FramedIPAddress')}\n会话 {session_id}"
                f"\n上线 {_fmt_ts(device.get('AcctStartTime'))}"
            )
        _fill_table(self.device_table, rows, tips or None)
        if not rows:
            _fill_table(self.device_table, [["—", "—", "—", "—", "—", "当前没有在线设备"]])

    def _fail(self, message: str) -> None:
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("刷新账户信息")
        self.device_button.setEnabled(True)
        self.device_button.setText("查询在线设备")
        self.kick_button.setEnabled(True)
        self.kick_others_button.setEnabled(True)
        Toast.error(self, "操作失败", (message or "").splitlines()[0])

    def on_theme_changed(self) -> None:
        pass





class SelfServiceView(QWidget):
    """套餐流量 / 订单 / 账单 / 上网明细 / 修改密码 / 暂停恢复。

    **不打开浏览器**：直接调自助系统的接口，在程序里展示与操作。
    """

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = _scroll_page()
        outer.addWidget(scroll)

        self.flow_card = Card("套餐与流量", parent=self)
        flow_row = QHBoxLayout()
        flow_row.setSpacing(8)
        self.flow_button = PrimaryPushButton(FIF.SYNC, "刷新套餐", self)
        self.flow_button.clicked.connect(self.load_flow)
        flow_row.addWidget(self.flow_button)
        flow_row.addStretch(1)
        self.flow_card.add_layout(flow_row)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tile_total = StatTile("套餐总量", "-", "—", parent=self)
        self.tile_used = StatTile("已用", "-", "—", parent=self)
        self.tile_left = StatTile("剩余", "-", "—", parent=self)
        self.tile_period = StatTile("有效期", "-", "—", parent=self)
        for tile in (self.tile_total, self.tile_used, self.tile_left, self.tile_period):
            tiles.addWidget(tile, 1)
        self.flow_card.add_layout(tiles)
        _insert(layout, self.flow_card)

        self.pwd_card = Card("修改密码", parent=self)
        pwd_row = QHBoxLayout()
        pwd_row.setSpacing(8)
        self.new_pwd = LineEdit(self)
        self.new_pwd.setPlaceholderText("新密码")
        self.new_pwd.setEchoMode(LineEdit.Password)
        self.new_pwd.setMinimumWidth(180)
        pwd_row.addWidget(self.new_pwd)
        self.captcha_edit = LineEdit(self)
        self.captcha_edit.setPlaceholderText("验证码")
        self.captcha_edit.setMinimumWidth(110)
        pwd_row.addWidget(self.captcha_edit)
        self.captcha_label = QLabel(self)
        self.captcha_label.setFixedSize(110, 38)
        self.captcha_label.setCursor(Qt.PointingHandCursor)
        self.captcha_label.setToolTip("看不清？点一下换一张")
        self.captcha_label.mousePressEvent = lambda _e: self.load_captcha()
        pwd_row.addWidget(self.captcha_label)
        self.captcha_button = PushButton(FIF.SYNC, "换一张", self)
        self.captcha_button.clicked.connect(self.load_captcha)
        pwd_row.addWidget(self.captcha_button)
        self.pwd_button = PushButton(FIF.CERTIFICATE, "修改密码", self)
        self.pwd_button.clicked.connect(self.change_password)
        pwd_row.addWidget(self.pwd_button)
        pwd_row.addStretch(1)
        self.pwd_card.add_layout(pwd_row)
        _insert(layout, self.pwd_card)

        self.order_card = Card("订单记录", parent=self)
        order_row = QHBoxLayout()
        order_row.setSpacing(8)
        self.order_button = PushButton(FIF.SYNC, "刷新订单", self)
        self.order_button.clicked.connect(self.load_orders)
        order_row.addWidget(self.order_button)
        order_row.addStretch(1)
        self.order_card.add_layout(order_row)
        self.order_table = _make_table(["订单号", "产品", "价格", "开始", "结束", "状态"])
        self.order_card.add(self.order_table)
        _insert(layout, self.order_card)

        self.bill_card = Card("账单流水", parent=self)
        bill_row = QHBoxLayout()
        bill_row.setSpacing(8)
        self.bill_button = PushButton(FIF.SYNC, "刷新账单", self)
        self.bill_button.clicked.connect(self.load_bills)
        bill_row.addWidget(self.bill_button)
        bill_row.addStretch(1)
        self.bill_card.add_layout(bill_row)
        self.bill_table = _make_table(["时间", "项目", "金额", "方式", "操作员"])
        self.bill_card.add(self.bill_table)
        _insert(layout, self.bill_card)

        self.log_card = Card("上网明细", parent=self)
        log_row = QHBoxLayout()
        log_row.setSpacing(8)
        self.date_edit = CompactDateEdit(self)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setDateRange(QDate(2015, 1, 1), QDate.currentDate())
        self.date_edit.setFixedWidth(136)
        self.date_edit.setFixedHeight(32)
        log_row.addWidget(self.date_edit)
        self.today_button = PushButton("今天", self)
        self.today_button.setFixedHeight(32)
        self.today_button.clicked.connect(lambda: self._set_log_date(0))
        log_row.addWidget(self.today_button)
        self.yesterday_button = PushButton("昨天", self)
        self.yesterday_button.setFixedHeight(32)
        self.yesterday_button.clicked.connect(lambda: self._set_log_date(1))
        log_row.addWidget(self.yesterday_button)
        self.log_button = PrimaryPushButton(FIF.SEARCH, "查询", self)
        self.log_button.setFixedHeight(32)
        self.log_button.clicked.connect(self.load_net_log)
        log_row.addWidget(self.log_button)
        log_row.addStretch(1)
        self.log_summary = BodyLabel("", self)
        log_row.addWidget(self.log_summary)
        self.log_card.add_layout(log_row)
        self.log_table = _make_table(["开始", "结束", "时长", "上传", "下载", "合计", "IP", "设备(MAC)"])
        self.log_card.add(self.log_table)
        _insert(layout, self.log_card)

        self.misc_card = Card("其他接口数据", parent=self)
        misc_row = QHBoxLayout()
        misc_row.setSpacing(8)
        all_button = PrimaryPushButton(FIF.SYNC, "读取全部", self)
        all_button.clicked.connect(self.load_misc_all)
        misc_row.addWidget(all_button)
        self.misc_button = all_button

        for label, icon, name in (
            ("通知", FIF.INFO, "notices"),
            ("发票", FIF.DOCUMENT, "invoices"),
            ("套餐介绍", FIF.CHAT, "product_intro"),
        ):
            button = PushButton(icon, label, self)
            button.clicked.connect(lambda _checked=False, n=name: self.load_misc(n))
            misc_row.addWidget(button)
        misc_row.addStretch(1)
        self.misc_card.add_layout(misc_row)

        self.misc_table = _make_table(["接口 / 项目", "结果", "说明"])
        self.misc_table.setMinimumHeight(240)
        self.misc_card.add(self.misc_table)

        self.misc_card.hide()

        self.pause_card = Card("账户状态", parent=self)
        pause_head = QHBoxLayout()
        pause_head.setSpacing(8)
        self.pause_toggle = PushButton(FIF.CHEVRON_DOWN_MED, "展开账户状态操作", self)
        self.pause_toggle.setToolTip("申请暂停 / 恢复使用；学校端可能已停用，仅在确实需要时展开")
        self.pause_toggle.clicked.connect(self._toggle_pause_panel)
        pause_head.addWidget(self.pause_toggle)
        pause_head.addStretch(1)
        self.pause_card.add_layout(pause_head)

        self.pause_panel = QWidget(self)
        pause_panel_layout = QVBoxLayout(self.pause_panel)
        pause_panel_layout.setContentsMargins(0, 0, 0, 0)
        pause_panel_layout.setSpacing(10)

        pause_row = QHBoxLayout()
        pause_row.setSpacing(8)
        self.pause_button = PushButton("申请暂停", self.pause_panel)
        self._style_pause_button()
        self.pause_button.clicked.connect(lambda: self._pause(True))
        pause_row.addWidget(self.pause_button)
        self.resume_button = PushButton(FIF.CONNECT, "恢复使用", self.pause_panel)
        self.resume_button.clicked.connect(lambda: self._pause(False))
        pause_row.addWidget(self.resume_button)
        pause_row.addStretch(1)
        pause_panel_layout.addLayout(pause_row)

        pause_note = BodyLabel(
            "暂停 / 恢复由学校自助系统受理，当前学校端接口已停用（提交后无响应）。",
            self.pause_panel,
        )
        pause_note.setWordWrap(False)
        pause_panel_layout.addWidget(pause_note)

        note_row = QHBoxLayout()
        note_row.setSpacing(8)
        note_row.addWidget(
            BodyLabel("如需停机或恢复，可通过自助系统联系校园网服务中心：", self.pause_panel)
        )
        note_row.addWidget(
            HyperlinkButton(
                "http://10.100.2.2/after_service.html",
                "在线联系校园网服务中心",
                self.pause_panel,
            )
        )
        note_row.addStretch(1)
        pause_panel_layout.addLayout(note_row)

        self.pause_card.add(self.pause_panel)
        self.pause_panel.hide()
        _insert(layout, self.pause_card)

        QTimer.singleShot(0, self.load_net_log)

    def refresh_all(self) -> None:
        self.load_flow()
        self.load_orders()
        self.load_bills()
        self.load_net_log()
        self.load_captcha()

    def load_flow(self) -> None:
        self.flow_button.setEnabled(False)
        self.flow_button.setText("读取中…")

        def done(result: Any) -> None:
            self.flow_button.setEnabled(True)
            self.flow_button.setText("刷新套餐")
            if not (isinstance(result, dict) and result.get("ok")):
                self.tile_left.set_value("—", "读取失败")
                Toast.warning(self, "套餐流量读取失败", str((result or {}).get("message") or "读取失败"))
                return
            data = result.get("data") or {}
            self.tile_total.set_value(data.get("totalFlowFormat") or "—", data.get("productName") or "套餐总量")
            self.tile_used.set_value(data.get("used") or "—", "本周期已用")
            self.tile_left.set_value(data.get("flowBalanceFormat") or "—", "本周期剩余")
            self.tile_period.set_value(
                str(data.get("endTime") or "—")[:10],
                f"起 {str(data.get('startTime') or '')[:10]}",
            )

        run_async(self.service.selfservice_call, "flow_info", on_done=done, on_error=self._fail)

    def load_orders(self) -> None:
        self.order_button.setEnabled(False)
        self.order_button.setText("读取中…")

        def done(result: Any) -> None:
            self.order_button.setEnabled(True)
            self.order_button.setText("刷新订单")
            if not (isinstance(result, dict) and result.get("ok")):
                _fill_table(self.order_table, [["—", "—", "—", "—", "—", "订单读取失败"]])
                Toast.warning(self, "订单读取失败", str((result or {}).get("message") or ""))
                return
            rows, tips = [], []
            for item in _as_list(result.get("data")):
                rows.append([
                    str(item.get("order_no") or item.get("id") or ""),
                    str(item.get("product_name") or ""),
                    f"{item.get('price') or '0.00'} 元",
                    _fmt_ts(item.get("begin_time")),
                    _fmt_ts(item.get("finish_time")),
                    _order_status(item.get("status")),
                ])
                tips.append(f"订单 {item.get('order_no')}\n产品 {item.get('product_name')}\n价格 {item.get('price')} 元\n{_fmt_ts(item.get('begin_time'))} → {_fmt_ts(item.get('finish_time'))}")
            _fill_table(self.order_table, rows or [["—", "—", "—", "—", "—", "没有订单记录"]], tips or None)

        run_async(self.service.selfservice_call, "orders", on_done=done, on_error=self._fail)

    def load_bills(self) -> None:
        self.bill_button.setEnabled(False)
        self.bill_button.setText("读取中…")

        def done(result: Any) -> None:
            self.bill_button.setEnabled(True)
            self.bill_button.setText("刷新账单")
            if not (isinstance(result, dict) and result.get("ok")):
                _fill_table(self.bill_table, [["—", "—", "—", "—", "账单读取失败"]])
                Toast.warning(self, "账单读取失败", str((result or {}).get("message") or ""))
                return
            rows, tips = [], []
            for item in _as_list(result.get("data")):
                rows.append([
                    _fmt_ts(item.get("create_at")),
                    str(item.get("name") or item.get("desc") or ""),
                    f"{item.get('money') or '0.00'} 元",
                    str(item.get("pay_method") or ""),
                    str(item.get("operator") or ""),
                ])
                tips.append(f"{_fmt_ts(item.get('create_at'))} {item.get('name')}\n金额 {item.get('money')} 元，产品 {item.get('product_name')}")
            _fill_table(self.bill_table, rows or [["—", "—", "—", "—", "没有账单记录"]], tips or None)

        run_async(self.service.selfservice_call, "bills", on_done=done, on_error=self._fail)

    def _set_log_date(self, days_ago: int = 0) -> None:
        """快捷切换上网明细日期并立即查询。"""
        self.date_edit.setDate(QDate.currentDate().addDays(-days_ago))
        self.load_net_log()

    def _style_pause_button(self) -> None:
        """把「申请暂停」画成红色描边按钮：会立刻断网的动作必须一眼可辨。"""
        from PyQt5.QtGui import QColor
        from qfluentwidgets import setCustomStyleSheet

        try:
            self.pause_button.setIcon(
                FIF.POWER_BUTTON.colored(QColor("#c42b1c"), QColor("#ff99a4"))
            )
        except Exception:
            self.pause_button.setIcon(FIF.POWER_BUTTON)
        setCustomStyleSheet(
            self.pause_button,
            "PushButton{color:#c42b1c;border:1px solid #c42b1c;background-color:transparent;}"
            "PushButton:hover{color:#c42b1c;border:1px solid #c42b1c;background-color:rgba(196,43,28,0.08);}"
            "PushButton:pressed{color:#a02316;border:1px solid #a02316;background-color:rgba(196,43,28,0.16);}"
            "PushButton:disabled{color:rgba(196,43,28,0.45);border:1px solid rgba(196,43,28,0.45);background-color:transparent;}",
            "PushButton{color:#ff99a4;border:1px solid #ff99a4;background-color:transparent;}"
            "PushButton:hover{color:#ff99a4;border:1px solid #ff99a4;background-color:rgba(255,153,164,0.12);}"
            "PushButton:pressed{color:#ffb3bb;border:1px solid #ffb3bb;background-color:rgba(255,153,164,0.20);}"
            "PushButton:disabled{color:rgba(255,153,164,0.45);border:1px solid rgba(255,153,164,0.45);background-color:transparent;}",
        )

    def _toggle_pause_panel(self) -> None:
        show = self.pause_panel.isHidden()
        self.pause_panel.setVisible(show)
        self.pause_toggle.setText("收起账户状态操作" if show else "展开账户状态操作")
        self.pause_toggle.setIcon(FIF.CHEVRON_RIGHT_MED if show else FIF.CHEVRON_DOWN_MED)

    def load_net_log(self) -> None:
        date = self.date_edit.date().toString("yyyy-MM-dd")
        self.log_button.setEnabled(False)
        self.log_button.setText("查询中…")
        self.log_summary.setText("查询中…")

        def done(result: Any) -> None:
            self.log_button.setEnabled(True)
            self.log_button.setText("查询")
            if not (isinstance(result, dict) and result.get("ok")):
                _fill_table(self.log_table, [["查询失败"] + ["—"] * 7])
                self.log_summary.setText("查询失败")
                Toast.warning(self, "上网明细查询失败", str((result or {}).get("message") or ""))
                return
            data = result.get("data") or {}
            records = _as_list(data.get("records") if isinstance(data, dict) else None)
            rows, tips = [], []
            total_up = total_down = 0
            for item in records:
                if not isinstance(item, dict):
                    continue
                start = str(item.get("start") or "")
                stop = str(item.get("stop") or "")
                try:
                    duration = int(float(item.get("duration") or 0))
                except (TypeError, ValueError):
                    duration = 0
                try:
                    total_up += int(float(item.get("up") or 0))
                    total_down += int(float(item.get("down") or 0))
                except (TypeError, ValueError):
                    pass
                rows.append([
                    start or "—",
                    stop or "在线中",
                    format_duration(duration),
                    str(item.get("up_text") or "0 B"),
                    str(item.get("down_text") or "0 B"),
                    str(item.get("total_text") or "0 B"),
                    str(item.get("ip") or "—"),
                    str(item.get("mac") or "—"),
                ])
                tips.append(
                    f"{start or '—'} → {stop or '在线中'}\n"
                    f"会话号：{item.get('session_id') or '—'}｜结束原因：{item.get('terminate') or '—'}"
                )
            _fill_table(
                self.log_table,
                rows or [["这一天没有上网记录"] + [""] * 7],
                tips or None,
            )
            if rows:
                self.log_summary.setText(
                    f"共 {len(rows)} 条 · 上传 {format_bytes(total_up)} · 下载 {format_bytes(total_down)}"
                )
            else:
                self.log_summary.setText("这一天没有上网记录")

        def fail(message: str) -> None:
            self.log_summary.setText("查询失败")
            self._fail(message)

        run_async(self.service.selfservice_call, "net_log", date, on_done=done, on_error=fail)

    def load_misc_all(self) -> None:
        """依次读取所有剩余只读接口，汇总成一张表。"""
        self.misc_button.setEnabled(False)
        self.misc_button.setText("读取中…")
        run_async(self._collect_misc, on_done=self._apply_misc, on_error=self._fail)

    def _collect_misc(self) -> list[dict]:
        rows = []
        for name, label, desc in (
            ("pause_state", "暂停状态", "是否处于暂停/停机状态"),
            ("notices", "通知", "门户公告"),
            ("invoices", "发票", "发票记录"),
            ("product_intro", "套餐介绍", "可用套餐与本账号套餐"),
            ("work_order_types", "工单类型", "报修可选类型"),
            ("repairs", "报修记录", "提交过的故障工单"),
            ("rooms", "宿舍列表", "可申请的宿舍"),
            ("bind_info", "绑定信息", "微信 / 运营商绑定状态"),
            ("bottom_word", "门户公告", "页脚公告与客服信息"),
        ):
            try:
                result = self.service.selfservice_call(name)
            except Exception as exc:
                result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
            rows.append({
                "name": name,
                "label": label,
                "desc": desc,
                "ok": bool(result.get("ok")),
                "message": result.get("message") or "",
                "data": result.get("data"),
            })
        return rows

    def _apply_misc(self, rows: Any) -> None:
        self.misc_button.setEnabled(True)
        self.misc_button.setText("读取全部")
        table_rows, tips = [], []
        for item in rows or []:
            data = item.get("data")
            summary = _summarize(item["name"], data)
            table_rows.append([
                item["label"],
                "读取成功" if item.get("ok") else "失败",
                summary or item.get("message") or "",
            ])
            tips.append(f"{item['label']}（{item['desc']}）\n{summary or item.get('message') or ''}")
        _fill_table(self.misc_table, table_rows, tips or None)
        if not table_rows:
            _fill_table(self.misc_table, [["—", "没有数据", "这些接口当前都没有返回内容"]])
        self.load_captcha()

    def load_misc(self, name: str) -> None:
        def done(result: Any) -> None:
            data = (result or {}).get("data")
            summary = _summarize(name, data) or (result or {}).get("message") or "无数据"
            Toast.info(self, name, summary)

        run_async(self.service.selfservice_call, name, on_done=done, on_error=self._fail)

    def load_captcha(self) -> None:
        def done(payload: Any) -> None:
            data = payload or b""
            pixmap = QPixmap()
            if data and pixmap.loadFromData(data):
                self.captcha_label.setPixmap(
                    pixmap.scaled(self.captcha_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                self.captcha_label.setText("验证码不可用")

        run_async(self.service.selfservice_captcha, on_done=done)

    def change_password(self) -> None:
        password = self.new_pwd.text().strip()
        captcha = self.captcha_edit.text().strip()
        if not password:
            Toast.warning(self, "请输入新密码", "新密码不能为空。")
            return
        if not captcha:
            Toast.warning(self, "请输入验证码", "点验证码图片可以换一张。")
            return
        self.pwd_button.setEnabled(False)
        self.pwd_button.setText("修改中…")

        def done(result: Any) -> None:
            self.pwd_button.setEnabled(True)
            self.pwd_button.setText("修改密码")
            if isinstance(result, dict) and result.get("ok"):
                Toast.success(self, "密码已修改", "请记住新密码，下次认证将使用它。")
                self.new_pwd.clear()
                self.captcha_edit.clear()
            else:
                message = str((result or {}).get("message") or "修改失败")
                Toast.warning(self, "修改未成功", message)
                self.load_captcha()

        run_async(
            self.service.selfservice_change_password, password, captcha,
            on_done=done, on_error=self._fail,
        )

    def _pause(self, enable: bool) -> None:
        if enable:
            confirmed = confirm_typed(
                self.window(),
                "申请暂停：会立即断网",
                "暂停后你的校园网账号会在学校侧被停用，本机以及所有已登录设备会立即断网。"
                "学校端可能已停用该功能；恢复需要回到这里点「恢复使用」，或联系校园网服务中心办理。",
                "我已确认要暂停账号",
                detail="这是对学校侧账号生效的操作，请确认你确实不再需要上网。",
                confirm_text="确认申请暂停",
            )
            if not confirmed:
                Toast.info(self, "已取消", "没有执行暂停，账号状态未改变。")
                return
        else:
            confirmed = confirm_typed(
                self.window(),
                "恢复使用",
                "恢复后账号会重新启用，本机及其它已登录设备可以继续认证上网。"
                "若学校端接口已停用，提交后可能没有响应，可联系校园网服务中心办理。",
                "恢复账号",
                detail="这会把学校侧的账号状态改回正常，请确认你确实需要恢复上网。",
                confirm_text="确认恢复使用",
            )
            if not confirmed:
                Toast.info(self, "已取消", "没有执行恢复，账号状态未改变。")
                return
        name = "set_pause" if enable else "restore_pause"

        def done(result: Any) -> None:
            if isinstance(result, dict) and result.get("ok"):
                Toast.success(self, "已提交", str(result.get("data") or ("已申请暂停" if enable else "已恢复")))
            else:
                message = str((result or {}).get("message") or "")
                detail = "该功能已停用或暂不支持，请联系校园网服务中心办理。"
                if message and "HTTP 200" not in message:
                    detail = f"{detail}（{message}）"
                Toast.warning(self, "学校端未受理", detail)

        run_async(self.service.selfservice_call, name, on_done=done, on_error=self._fail)

    def _fail(self, message: str) -> None:
        self.flow_button.setEnabled(True)
        self.flow_button.setText("刷新套餐")
        self.pwd_button.setEnabled(True)
        self.pwd_button.setText("修改密码")
        for button in (self.order_button, self.bill_button, self.log_button):
            button.setEnabled(True)
        self.order_button.setText("刷新订单")
        self.bill_button.setText("刷新账单")
        self.log_button.setText("查询")
        self.misc_button.setEnabled(True)
        self.misc_button.setText("读取全部")
        Toast.error(self, "操作失败", (message or "").splitlines()[0])

    def on_theme_changed(self) -> None:
        pass

def _order_status(value: Any) -> str:
    return {
        "0": "进行中",
        "1": "已完成",
        "2": "已取消",
    }.get(str(value), str(value or ""))


def _summarize(name: str, data: Any) -> str:
    """把各种接口的返回压成一句话，便于在表格里一眼看懂。"""
    if data in (None, "", [], {}):
        return "无数据"
    if name == "pause_state":
        if isinstance(data, dict):
            state = data.get("status") or data.get("pause") or data
            return f"暂停状态：{state}"
        return str(data)[:80]
    if name in ("invoices", "getInvoice"):
        items = _as_list(data.get("list") if isinstance(data, dict) else data)
        if items:
            return f"发票记录 {len(items)} 条"
        return "没有发票记录"
    if name in ("orders", "orderInfo"):
        return f"订单 {len(_as_list(data))} 条"
    if name == "bills":
        return f"账单 {len(_as_list(data))} 条"
    if name in ("product_intro", "getProductIntroByUserId"):
        items = _as_list(data)
        names = [
            str(item.get("product_name") or "")
            for item in items[:3]
            if isinstance(item, dict)
        ]
        return f"套餐 {len(items)} 个" + (f"：{'、'.join(n for n in names if n)}" if names else "")
    if name in ("getWorkOrderType", "work_order_types"):
        items = _as_list(data)
        if items:
            return f"工单类型 {len(items)} 个"
        return "未开放"
    if name in ("getRoomList", "rooms"):
        items = _as_list(data)
        if items:
            return f"可选宿舍 {len(items)} 个"
        return "无可用宿舍（或已有在途订单）"
    if name == "repairs":
        items = _as_list(data)
        if items:
            return f"报修记录 {len(items)} 条"
        return "没有报修记录"
    if name == "bind_info":
        if isinstance(data, dict):
            bound = [k for k, v in data.items() if v not in (None, "", [], {}, "0", 0, False)]
            if bound:
                return "已绑定：" + "、".join(str(k) for k in bound[:5])
            return "未绑定任何第三方"
        return str(data)[:80]
    if name == "bottom_word":
        if isinstance(data, dict):
            parts = [str(data.get(k)) for k in ("bottomword", "bottomword2", "bottomword3") if data.get(k)]
            if parts:
                return " / ".join(parts)[:120]
            return "无公告内容"
        return str(data)[:120]
    if name == "notices":
        if data:
            return str(data)[:120]
        return "没有通知"
    if isinstance(data, list):
        return f"{len(data)} 条记录"
    if isinstance(data, dict):
        keys = list(data.keys())[:6]
        return "字段：" + "、".join(str(k) for k in keys)
    return str(data)[:120]



















class ToolsPage(QWidget):
    """网络工具页：六个分区。"""

    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolsPage")
        self.service = service
        self._window = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 16, 28, 0)
        outer.setSpacing(12)

        self.segmented = SegmentedWidget(self)
        self.stack = QStackedWidget(self)

        self.wifi_view = WifiView(service, self)
        self.diag_view = DiagView(service, self)
        self.traffic_view = TrafficView(service, self)
        self.account_view = AccountView(service, self)
        self.selfservice_view = SelfServiceView(service, self)
        self.logs_view = LogsView(service, self)

        self.logs_view.hide()

        for key, text, view in (
            ("wifi", "无线网络", self.wifi_view),
            ("diag", "网络诊断", self.diag_view),
            ("traffic", "流量统计", self.traffic_view),
            ("account", "账户与设备", self.account_view),
        ):
            self.segmented.addItem(key, text)
            self.stack.addWidget(view)

        try:
            area = self.account_view.layout().itemAt(0).widget()
            host = area.widget().layout()
            inner = self.selfservice_view.layout().itemAt(0).widget()
            content = inner.takeWidget() if hasattr(inner, "takeWidget") else None
            if content is not None:
                host.setContentsMargins(28, 20, 28, 28)
                host.setSpacing(18)
                source = content.layout()
                while source is not None and source.count():
                    item = source.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        widget.setParent(self.account_view)
                        host.addWidget(widget)
                        continue
                    child = item.layout()
                    if child is None:
                        continue
                    host.addLayout(child)
                content.setParent(None)
                content.hide()
                self.selfservice_view.hide()
            else:
                host.addWidget(self.selfservice_view)
        except Exception:
            self.stack.addWidget(self.selfservice_view)

        self.segmented.setCurrentItem("wifi")
        self.segmented.currentItemChanged.connect(self._on_segment)

        outer.addWidget(self.segmented)
        outer.addWidget(self.stack, 1)

        self.logs_view.load_history()
        self.wifi_view.refresh_status()
        self.wifi_view.load_profiles()
        self.traffic_view.refresh()

    def _on_segment(self, key: str) -> None:
        mapping = {
            "wifi": self.wifi_view,
            "diag": self.diag_view,
            "traffic": self.traffic_view,
            "account": self.account_view,
        }
        target = mapping.get(key)
        if target is not None:
            self.stack.setCurrentWidget(target)
            if key == "account":
                try:
                    self.selfservice_view.refresh_all()
                except Exception:
                    pass
            if key == "traffic":
                self.traffic_view.refresh()
                return
            if key == "wifi":
                self.wifi_view.refresh_status()
                return
            if key == "account":
                self.account_view.refresh()
                return
            if key == "selfservice":
                self.selfservice_view.refresh_all()
                return

    def bind_window(self, window) -> None:
        self._window = window
        if hasattr(self.account_view, "bind_window"):
            self.account_view.bind_window(window)
            return

    def on_state(self, state: dict) -> None:
        self.wifi_view.on_state(state)

    def on_traffic(self, sample: dict) -> None:
        self.traffic_view.on_traffic(sample)

    def append_log(self, entry: dict) -> None:
        self.logs_view.append_log(entry)

    def on_theme_changed(self) -> None:
        for view in (
            self.wifi_view,
            self.diag_view,
            self.traffic_view,
            self.account_view,
            self.selfservice_view,
            self.logs_view,
        ):
            if hasattr(view, "on_theme_changed"):
                view.on_theme_changed()
