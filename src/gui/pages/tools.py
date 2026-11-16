"""网络工具页：无线网络 / 网络诊断 / 流量统计 / 运行日志。

用 FluentUI 的 ``SegmentedWidget`` 做分区切换，每个分区是一张张 Fluent 卡片。
所有耗时操作都通过 ``run_async`` 丢到线程池，界面不阻塞。
"""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
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
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SmoothScrollArea,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
)

from .. import theme
from ..async_task import run_async
from ..charts import HistoryChart
from ..formatting import (
    format_bytes,
    format_datetime,
    format_duration,
    format_rate,
    group_by_day,
    signal_label,
)

import time

from ..dialogs import confirm_typed
from ..toasts import Toast
from ..widgets import Card, KVGrid, StatTile, caption_label, scroll_page

# 测速最长允许挂在「测速中」的秒数；超过就认为测速源不可达。
SPEED_TIMEOUT_SEC = 150.0


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
    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._state = {}
        self._networks = []
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
        self.scan_button.clicked.connect(self.scan)
        scan_row.addWidget(self.scan_button)
        self.connect_button = PushButton(FIF.CONNECT, "连接选中网络", self)
        self.connect_button.clicked.connect(self.connect_selected)
        scan_row.addWidget(self.connect_button)
        scan_row.addStretch(1)
        self.scan_hint = caption_label("", self)
        scan_row.addWidget(self.scan_hint)
        self.scan_card.add_layout(scan_row)

        self.network_table = _make_table(["网络名称", "信号", "强度", "信道", "自动连接名单"])
        self.scan_card.add(self.network_table)
        _insert(layout, self.scan_card)

        self.profile_card = Card("已保存的配置文件", parent=self)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)
        self.profile_combo = ComboBox(self)
        self.profile_combo.setMinimumWidth(240)
        profile_row.addWidget(self.profile_combo)
        self.profile_connect = PushButton(FIF.WIFI, "连接", self)
        self.profile_connect.clicked.connect(self.connect_profile)
        profile_row.addWidget(self.profile_connect)
        self.profile_refresh = PushButton(FIF.SYNC, "刷新", self)
        self.profile_refresh.clicked.connect(self.load_profiles)
        profile_row.addWidget(self.profile_refresh)
        profile_row.addStretch(1)
        self.profile_card.add_layout(profile_row)
        _insert(layout, self.profile_card)

    def refresh_status(self) -> None:
        run_async(self.service.wifi_status, on_done=self._apply_status, on_error=self._fail)

    def load_profiles(self) -> None:
        run_async(self.service.saved_profiles, on_done=self._apply_profiles)

    def scan(self) -> None:
        self.scan_button.setEnabled(False)
        self.scan_hint.setText("正在扫描…")
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
            Toast.info(self, "没有可连接的配置", "先刷新已保存的配置文件。")
            return
        self._connect_ssid(ssid)

    def _connect_ssid(self, ssid: str) -> None:
        self.connect_button.setEnabled(False)
        Toast.info(self, "正在连接", f"正在连接 {ssid}…")

        def done(result: Any) -> None:
            self.connect_button.setEnabled(True)
            self._toast(result, f"连接 {ssid}")
            self.refresh_status()

        run_async(self.service.connect_ssid, ssid, on_done=done, on_error=self._fail)

    def _apply_status(self, status: Any) -> None:
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

    def _apply_profiles(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        profiles = result.get("profiles") or []
        self.profile_combo.clear()
        for name in profiles:
            self.profile_combo.addItem(name)
        if not profiles:
            self.profile_combo.addItem("")
            self.profile_combo.setPlaceholderText("没有已保存的配置")

    def _apply_scan(self, result: Any) -> None:
        self.scan_button.setEnabled(True)
        self._networks = list((result or {}).get("networks") or [])

        added = []
        campus = self.service.campus_candidates(self._networks)
        if campus:
            added = self.service.adopt_networks([n["ssid"] for n in campus])

        rows = []
        for item in self._networks:
            signal = item.get("signal")
            rows.append([
                item.get("ssid", ""),
                f"{signal}%" if isinstance(signal, (int, float)) else "—",
                signal_label(signal),
                str(item.get("channel") or "—"),
                "✔ 在名单内" if item.get("in_allow_list") else "",
            ])

        _fill_table(self.network_table, rows)
        if added:
            self.scan_hint.setText(f"共发现 {len(rows)} 个网络；已自动把 {', '.join(added)} 加入自动连接名单")
        else:
            self.scan_hint.setText(f"共发现 {len(rows)} 个网络。" if rows else "没有扫描到网络。")

    def on_state(self, state: dict) -> None:
        self._state = state or {}
        wifi = self._state.get("wifi_status") or {}
        if wifi:
            self._apply_status(wifi)

    def _toast(self, result: Any, what: str) -> None:
        if isinstance(result, dict):
            if result.get("ok"):
                Toast.success(self, f"{what}成功", str(result.get("message", ""))[:120])
            else:
                Toast.warning(self, f"{what}未成功", str(result.get("message", ""))[:160])
        else:
            Toast.success(self, f"{what}完成", "")

    def _fail(self, message: str) -> None:
        self.scan_button.setEnabled(True)
        self.connect_button.setEnabled(True)
        Toast.error(self, "操作失败", (message or "").splitlines()[0])

    def on_theme_changed(self) -> None:
        pass













class DiagView(QWidget):
    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, layout = _scroll_page()
        outer.addWidget(scroll)

        card = Card("连通性概览", parent=self)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.run_button = PrimaryPushButton(FIF.SPEED_HIGH, "一键诊断", self)
        self.run_button.clicked.connect(self.run_diagnostic)
        row.addWidget(self.run_button)
        self.ip_button = PushButton(FIF.GLOBE, "查询出口 IP", self)
        self.ip_button.clicked.connect(self.fetch_ip)
        row.addWidget(self.ip_button)
        row.addStretch(1)
        card.add_layout(row)

        self.kv = KVGrid([("gateway", "默认网关"), ("public_ip", "出口 IP"), ("dns", "DNS 服务器"), ("tcp", "TCP 延迟"), ("elapsed", "诊断耗时")], columns=2, parent=self)
        card.add(self.kv)
        _insert(layout, card)

        self.speed_card = Card("测速", parent=self)
        speed_row = QHBoxLayout()
        speed_row.setSpacing(8)
        self.speed_button = PrimaryPushButton(FIF.DOWNLOAD, "开始完整测速", self)
        self.speed_button.clicked.connect(self.speed_test)
        speed_row.addWidget(self.speed_button)
        self.download_button = PushButton(FIF.CLOUD, "只测下载", self)
        self.download_button.clicked.connect(self.download_only)
        speed_row.addWidget(self.download_button)
        speed_row.addStretch(1)
        self.speed_hint = caption_label("", self)
        speed_row.addWidget(self.speed_hint)
        self.speed_card.add_layout(speed_row)

        from PyQt5.QtCore import QTimer as _QTimer
        from qfluentwidgets import IndeterminateProgressBar

        self.speed_progress = IndeterminateProgressBar(self)
        self.speed_progress.setVisible(False)
        self.speed_card.add(self.speed_progress)
        self._speed_started = 0.0
        self._speed_timer = _QTimer(self)
        self._speed_timer.setInterval(200)
        self._speed_timer.timeout.connect(self._tick_speed)
        self.speed_button.clicked.connect(lambda: self._speed_busy(True))
        self.download_button.clicked.connect(lambda: self._speed_busy(True))

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tile_down = StatTile("下载", "-", "—", parent=self)
        self.tile_up = StatTile("上传", "-", "—", parent=self)
        self.tile_latency = StatTile("延迟", "-", "—", parent=self)
        self.tile_jitter = StatTile("抖动", "-", "—", parent=self)
        for tile in (self.tile_down, self.tile_up, self.tile_latency, self.tile_jitter):
            tiles.addWidget(tile, 1)
        self.speed_card.add_layout(tiles)

        self.speed_table = _make_table(["测速项", "结果", "说明"])
        self.speed_table.setMinimumHeight(150)
        self.speed_card.add(self.speed_table)
        _insert(layout, self.speed_card)

        self.ping_card = Card("延迟与 DNS", parent=self)
        self.ping_table = _make_table(["延迟目标", "主机", "平均延迟", "最小 / 最大", "丢包"])
        self.ping_table.setMinimumHeight(140)
        self.ping_card.add(self.ping_table)

        self.dns_table = _make_table(["DNS 域名", "耗时", "解析结果"])
        self.dns_table.setMinimumHeight(140)
        self.ping_card.add(self.dns_table)
        self.dns_card = self.ping_card
        _insert(layout, self.ping_card)

    def _speed_busy(self, busy: bool) -> None:
        """测速的"运行中"提示：转圈进度条 + 已用秒数。"""
        if busy:
            if self._speed_started:
                return
            self._speed_started = time.time()
            self.speed_hint.setText("测速中… 0.0 秒")
            self.speed_progress.setVisible(True)
            try:
                self.speed_progress.start()
            except Exception:
                pass
            self._speed_timer.start()
        else:
            self._speed_timer.stop()
            self._speed_started = 0.0
            try:
                self.speed_progress.stop()
            except Exception:
                pass
            self.speed_progress.setVisible(False)

    def _tick_speed(self) -> None:
        if not self._speed_started:
            return
        elapsed = time.time() - self._speed_started
        self.speed_hint.setText(f"测速中… 已 {elapsed:.1f} 秒")
        if elapsed > SPEED_TIMEOUT_SEC:
            # 兜底：测速源被拦截时可能久到离谱，别让界面永远停在「测速中」。
            self._speed_busy(False)
            self.speed_hint.setText("测速超时：测速源不可达，请稍后再试")
            self.speed_button.setEnabled(True)
            self.download_button.setEnabled(True)

    def run_diagnostic(self) -> None:
        self.run_button.setEnabled(False)
        self.kv.set("elapsed", "诊断中…")
        run_async(self.service.run_diagnostic, on_done=self._apply_diag, on_error=self._fail)

    def fetch_ip(self) -> None:
        """public_ip"""
        self.kv.set("public_ip", "查询中…")
        run_async(self.service.fetch_public_ip, on_done=self._apply_ip, on_error=self._fail)

    def speed_test(self) -> None:
        """完整测速：延迟/抖动 + 下载 + 上传。"""
        self.speed_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.speed_hint.setText("正在测速…（延迟 → 下载 → 上传）")
        self.tile_down.set_value("测速中…", "正在挑选可用镜像")
        self.tile_up.set_value("等待中…", "—")
        run_async(self.service.speed_test, 8000, on_done=self._apply_speed, on_error=self._fail)

    def download_only(self) -> None:
        self.speed_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.speed_hint.setText("正在测下载…")
        run_async(self.service.speed_test_download_only, 8000, on_done=self._apply_speed, on_error=self._fail)

    def _apply_diag(self, result: Any) -> None:
        self.run_button.setEnabled(True)
        if not isinstance(result, dict):
            return
        rows = []
        tips = []
        for item in result.get("results") or []:
            avg = item.get("avg_ms")
            via = item.get("via")
            rows.append([
                item.get("name", ""),
                item.get("host", ""),
                (f"{avg} ms（TCP）" if via == "tcp" else f"{avg} ms") if avg is not None else "—",
                f"{item.get('min_ms') if item.get('min_ms') is not None else '—'} / {item.get('max_ms') if item.get('max_ms') is not None else '—'}",
                f"{item.get('loss_percent', 100)}%",
            ])
            tips.append(f"{item.get('name', '')}（{item.get('host', '')}）\n平均 {avg} ms，丢包 {item.get('loss_percent', 100)}%" + (f"\nICMP 不通，改用 TCP:{item.get('port')} 握手测延迟" if via == "tcp" else ""))
        _fill_table(self.ping_table, rows, tips or None)

        dns = result.get("dns") or {}
        dns_rows = [
            [
                entry.get("name", ""),
                f"{entry.get('ms', 0)} ms",
                ", ".join(entry.get("addresses") or []) or "解析失败",
            ]
            for entry in dns.get("results") or []
        ]
        _fill_table(self.dns_table, dns_rows)

        tcp = result.get("tcp") or {}
        routes = (result.get("routes") or {}).get("default_routes") or []
        self.kv.update_values({
            "gateway": routes[0].get("gateway", "—") if routes else "—",
            "dns": ", ".join(dns.get("servers") or []) or "—",
            "tcp": f"{tcp.get('ms')} ms" if tcp.get("ok") else "不可达",
            "elapsed": f"{result.get('elapsed_ms', 0)} ms",
        })

    def _apply_ip(self, result: Any) -> None:
        """ok"""
        if isinstance(result, dict) and result.get("ok"):
            self.kv.set("public_ip", str(result.get("ip") or "—"))
        else:
            self.kv.set("public_ip", "查询失败")

    def _apply_speed(self, result: Any) -> None:
        self._speed_busy(False)
        self.speed_button.setEnabled(True)
        self.download_button.setEnabled(True)
        if not isinstance(result, dict):
            self.speed_hint.setText("测速失败")
            return

        if "download" in result or "latency" in result:
            latency = result.get("latency") or {}
            download = result.get("download") or {}
            upload = result.get("upload") or {}
        else:
            latency, download, upload = {}, result, {}

        if download.get("ok"):
            self.tile_down.set_value(
                f"{download.get('mbps')} Mbps",
                f"{download.get('mb_per_sec')} MB/s · {download.get('label', '')}"
                + ("（稳态）" if download.get("measured") == "steady" else ""),
            )
        else:
            self.tile_down.set_value("失败", str(download.get("message") or "所有源不可用")[:40])

        if upload.get("ok"):
            self.tile_up.set_value(
                f"{upload.get('mbps')} Mbps",
                f"{upload.get('mb_per_sec')} MB/s · {upload.get('label', '')}"
                + ("（稳态）" if upload.get("measured") == "steady" else ""),
            )
        else:
            self.tile_up.set_value("不可用", str(upload.get("message") or "端点不可达")[:40])

        best = (latency.get("best") or {}) if latency else {}
        if best:
            self.tile_latency.set_value(f"{best.get('avg_ms')} ms", f"最快：{best.get('name')}")
            self.tile_jitter.set_value(f"{best.get('jitter_ms')} ms", f"TCP 握手抖动（{best.get('name')}）")
        else:
            self.tile_latency.set_value("—", "未测到")
            self.tile_jitter.set_value("—", "未测到")

        rows = []
        tips = []
        if download:
            rows.append([
                "下载",
                f"{download.get('mbps')} Mbps" if download.get("ok") else "失败",
                f"来源 {download.get('label') or '-'}，用时 {download.get('seconds')}s，共 {format_bytes(download.get('bytes'))}",
            ])
            tips.append(
                f"下载测速\n来源：{download.get('label')}（{download.get('url')}）\n耗时 {download.get('seconds')}s，共 {format_bytes(download.get('bytes'))}"
            )
            for failed in download.get("tried") or []:
                rows.append(["下载（跳过）", failed.get("label", ""), str(failed.get("error", ""))[:70]])
        if upload:
            rows.append([
                "上传",
                f"{upload.get('mbps')} Mbps" if upload.get("ok") else "不可用",
                f"来源 {upload.get('label') or '-'}，共 {format_bytes(upload.get('bytes'))}"
                + (f"；{upload.get('note')}" if upload.get("note") else ""),
            ])
            tips.append(
                f"上传测速\n来源：{upload.get('label')}\n共 {format_bytes(upload.get('bytes'))}，用时 {upload.get('seconds')}s"
            )
        for target in (latency.get("targets") if latency else []) or []:
            rows.append([
                f"延迟 · {target.get('name')}",
                f"{target.get('avg_ms')} ms" if target.get("ok") else "不可达",
                f"抖动 {target.get('jitter_ms')} ms，丢包 {target.get('loss')}%",
            ])
            tips.append(
                f"{target.get('name')}（{target.get('host')}:{target.get('port')}）\n平均 {target.get('avg_ms')} ms，抖动 {target.get('jitter_ms')} ms，丢包 {target.get('loss')}%"
            )
        _fill_table(self.speed_table, rows, tips or None)

        bits = []
        if download.get("ok"):
            bits.append(f"下载 {download.get('mbps')} Mbps（{download.get('label')}）")
        if upload.get("ok"):
            bits.append(f"上传 {upload.get('mbps')} Mbps")
        elif upload:
            bits.append("上传不可用")
        self.speed_hint.setText("测速完成：" + "；".join(bits) if bits else "测速完成，但所有源都不可用")

    def _fail(self, message: str) -> None:
        was_speeding = bool(self._speed_started)
        self._speed_busy(False)
        self.run_button.setEnabled(True)
        self.speed_button.setEnabled(True)
        self.download_button.setEnabled(True)
        if was_speeding:
            self.speed_hint.setText("测速失败")
        Toast.error(self, "诊断失败", (message or "").splitlines()[0])

    def on_theme_changed(self) -> None:
        pass






















class TrafficView(QWidget):
    def __init__(self, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._summary = {}
        self._mode_known = False
        self._mode_retry = 0
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
        self.granularity = ComboBox(self)
        self.granularity.addItems(["按天", "按小时"])
        self.granularity.setCurrentIndex(0)
        self.granularity.currentIndexChanged.connect(lambda _i: self.refresh())
        chart_row.addWidget(StrongBodyLabel("统计粒度", self))
        chart_row.addWidget(self.granularity)
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
        self.chart_card = chart_card

        self.chart = HistoryChart(parent=self)
        chart_card.add(self.chart)
        _insert(layout, chart_card)

        detail = Card("最近明细", parent=self)
        self.table = _make_table(["时间", "下行", "上行", "合计"])
        detail.add(self.table)
        _insert(layout, detail)

    def refresh(self) -> None:
        run_async(self.service.traffic.summary, on_done=self._apply_summary)
        run_async(self.service.traffic.series, None, 720, on_done=self._apply_series)

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

    def _apply_series(self, series: Any) -> None:
        items = list(series or [])
        if self.granularity.currentIndex() == 0:
            items = group_by_day(items)
        items = items[-90:]
        try:
            self.chart.set_data(items, label_key="key")
        except Exception:
            pass
        rows = [
            [
                str(item.get("key", "")),
                format_bytes(item.get("rx")),
                format_bytes(item.get("tx")),
                format_bytes(item.get("total")),
            ]
            for item in reversed(items[-24:])
        ]
        _fill_table(self.table, rows)
        self.table.resizeRowsToContents()

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
        self.hint = caption_label("", self)
        row.addWidget(self.hint)
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
        self.device_hint = caption_label("", self)
        dev_row.addWidget(self.device_hint)
        self.device_card.add_layout(dev_row)

        self.device_table = _make_table(["设备 MAC", "IP 地址", "上线时间", "在线时长", "会话号", "备注"])
        self.device_card.add(self.device_table)
        _insert(layout, self.device_card)


    def refresh(self) -> None:
        self.refresh_button.setEnabled(False)
        self.hint.setText("正在读取学校侧账户数据…")
        run_async(self.service.selfservice_overview, on_done=self._apply_overview, on_error=self._fail)

    def load_devices(self) -> None:
        self.device_button.setEnabled(False)
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
        if not (isinstance(result, dict) and result.get("ok")):
            self.hint.setText(str((result or {}).get("message") or "读取失败，请检查账号密码"))
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
        self.grid.update_values(
            {
                "username": account.get("username") or "—",
                "user_id": str(account.get("id") or "—"),
                "realname": str(account.get("realname") or "—"),
                "created": _fmt_ts(account.get("create_time")),
                "ip": str(first.get("FramedIPAddress") or "—"),
                "online": ("在线" if str(online.get("status")) == "online" else "离线") if online else "—",
                "device_count": str(len(records or [])) + " 台",
                "server": str(first.get("NASIPAddress") or "—"),
            }
        )
        used = flow.get("used") or "—"
        left = flow.get("flowBalanceFormat") or "—"
        self.hint.setText(f"已读取：套餐流量已用 {used}，剩余 {left}；到期 {account.get('order_end_time') or '—'}")

        devices = _as_list(result.get("devices") or {})
        self._apply_devices({"ok": True, "devices": devices})

    def _apply_devices(self, result: Any) -> None:
        self.device_button.setEnabled(True)
        if not (isinstance(result, dict) and result.get("ok")):
            self._devices = []
            _fill_table(self.device_table, [])
            self.device_hint.setText(str((result or {}).get("message") or "查询失败"))
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
        self.device_hint.setText(f"共 {len(self._devices)} 台设备在线。")

    def _fail(self, message: str) -> None:
        self.refresh_button.setEnabled(True)
        self.device_button.setEnabled(True)
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
        self.flow_hint = caption_label("", self)
        flow_row.addWidget(self.flow_hint)
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
        self.pwd_hint = caption_label("", self)
        self.pwd_card.add(self.pwd_hint)

        pause_row = QHBoxLayout()
        pause_row.setSpacing(8)
        self.pause_button = PushButton("申请暂停", self)

        from PyQt5.QtGui import QColor
        from qfluentwidgets import setCustomStyleSheet
        try:
            self.pause_button.setIcon(FIF.POWER_BUTTON.colored(QColor("#c42b1c"), QColor("#ff99a4")))
        except Exception:
            self.pause_button.setIcon(FIF.POWER_BUTTON)
        setCustomStyleSheet(
            self.pause_button,
            "PushButton{color:#c42b1c;border:1px solid #c42b1c;background-color:transparent;}PushButton:hover{color:#c42b1c;border:1px solid #c42b1c;background-color:rgba(196,43,28,0.08);}PushButton:pressed{color:#a02316;border:1px solid #a02316;background-color:rgba(196,43,28,0.16);}PushButton:disabled{color:rgba(196,43,28,0.45);border:1px solid rgba(196,43,28,0.45);background-color:transparent;}",
            "PushButton{color:#ff99a4;border:1px solid #ff99a4;background-color:transparent;}PushButton:hover{color:#ff99a4;border:1px solid #ff99a4;background-color:rgba(255,153,164,0.12);}PushButton:pressed{color:#ffb3bb;border:1px solid #ffb3bb;background-color:rgba(255,153,164,0.20);}PushButton:disabled{color:rgba(255,153,164,0.45);border:1px solid rgba(255,153,164,0.45);background-color:transparent;}",
        )
        self.pause_button.clicked.connect(lambda: self._pause(True))
        pause_row.addWidget(self.pause_button)
        self.resume_button = PushButton(FIF.CONNECT, "恢复使用", self)
        self.resume_button.clicked.connect(lambda: self._pause(False))
        pause_row.addWidget(self.resume_button)
        pause_row.addStretch(1)
        self.pwd_card.add_layout(pause_row)
        _insert(layout, self.pwd_card)

        self.order_card = Card("订单记录", parent=self)
        order_row = QHBoxLayout()
        order_row.setSpacing(8)
        order_button = PushButton(FIF.SYNC, "刷新订单", self)
        order_button.clicked.connect(self.load_orders)
        order_row.addWidget(order_button)
        order_row.addStretch(1)
        self.order_card.add_layout(order_row)
        self.order_table = _make_table(["订单号", "产品", "价格", "开始", "结束", "状态"])
        self.order_card.add(self.order_table)
        _insert(layout, self.order_card)

        self.bill_card = Card("账单流水", parent=self)
        bill_row = QHBoxLayout()
        bill_row.setSpacing(8)
        bill_button = PushButton(FIF.SYNC, "刷新账单", self)
        bill_button.clicked.connect(self.load_bills)
        bill_row.addWidget(bill_button)
        bill_row.addStretch(1)
        self.bill_card.add_layout(bill_row)
        self.bill_table = _make_table(["时间", "项目", "金额", "方式", "操作员"])
        self.bill_card.add(self.bill_table)
        _insert(layout, self.bill_card)

        self.log_card = Card("上网明细", parent=self)
        log_row = QHBoxLayout()
        log_row.setSpacing(8)
        self.date_edit = LineEdit(self)
        self.date_edit.setPlaceholderText("日期，如 2026-10-10（留空=今天）")
        self.date_edit.setMinimumWidth(200)
        log_row.addWidget(self.date_edit)
        log_button = PushButton(FIF.SEARCH, "查询", self)
        log_button.clicked.connect(self.load_net_log)
        log_row.addWidget(log_button)
        log_row.addStretch(1)
        self.log_hint = caption_label("", self)
        log_row.addWidget(self.log_hint)
        self.log_card.add_layout(log_row)
        self.log_table = _make_table(["开始", "结束", "上行", "下行", "时长", "IP"])
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
        self.misc_hint = caption_label("", self)
        misc_row.addWidget(self.misc_hint)
        self.misc_card.add_layout(misc_row)

        self.misc_table = _make_table(["接口 / 项目", "结果", "说明"])
        self.misc_table.setMinimumHeight(240)
        self.misc_card.add(self.misc_table)

        self.misc_card.hide()

    def refresh_all(self) -> None:
        self.load_flow()
        self.load_orders()
        self.load_bills()
        self.load_net_log()
        self.load_captcha()

    def load_flow(self) -> None:
        self.flow_button.setEnabled(False)
        self.flow_hint.setText("正在读取…")

        def done(result: Any) -> None:
            self.flow_button.setEnabled(True)
            if not (isinstance(result, dict) and result.get("ok")):
                self.flow_hint.setText(str((result or {}).get("message") or "读取失败"))
                return
            data = result.get("data") or {}
            self.tile_total.set_value(data.get("totalFlowFormat") or "—", data.get("productName") or "套餐总量")
            self.tile_used.set_value(data.get("used") or "—", "本周期已用")
            self.tile_left.set_value(data.get("flowBalanceFormat") or "—", "本周期剩余")
            self.tile_period.set_value(
                str(data.get("endTime") or "—")[:10],
                f"起 {str(data.get('startTime') or '')[:10]}",
            )
            self.flow_hint.setText("已读取套餐流量。")

        run_async(self.service.selfservice_call, "flow_info", on_done=done, on_error=self._fail)

    def load_orders(self) -> None:
        def done(result: Any) -> None:
            if not (isinstance(result, dict) and result.get("ok")):
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
            _fill_table(self.order_table, rows, tips or None)

        run_async(self.service.selfservice_call, "orders", on_done=done, on_error=self._fail)

    def load_bills(self) -> None:
        def done(result: Any) -> None:
            if not (isinstance(result, dict) and result.get("ok")):
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
            _fill_table(self.bill_table, rows, tips or None)

        run_async(self.service.selfservice_call, "bills", on_done=done, on_error=self._fail)

    def load_net_log(self) -> None:
        date = self.date_edit.text().strip()
        self.log_hint.setText("正在查询…")

        def done(result: Any) -> None:
            if not (isinstance(result, dict) and result.get("ok")):
                self.log_hint.setText(str((result or {}).get("message") or "查询失败"))
                return
            data = result.get("data") or {}
            logs = _as_list(data.get("logs") if isinstance(data, dict) else data)
            rows, tips = [], []
            for item in logs:
                rows.append([
                    _fmt_ts(item.get("acctstarttime") or item.get("startTime")),
                    _fmt_ts(item.get("acctstoptime") or item.get("stopTime")),
                    str(item.get("upFlow") or item.get("inputOctets") or ""),
                    str(item.get("downFlow") or item.get("outputOctets") or ""),
                    format_duration(item.get("acctsessiontime") or item.get("sessionTime")),
                    str(item.get("framedipaddress") or item.get("ip") or ""),
                ])
                tips.append(" | ".join(str(v) for v in rows[-1]))
            _fill_table(self.log_table, rows, tips or None)
            self.log_hint.setText(f"共 {len(rows)} 条记录" + (f"（{data.get('curMonth')}）" if isinstance(data, dict) and data.get("curMonth") else ""))

        run_async(self.service.selfservice_call, "net_log", date, on_done=done, on_error=self._fail)

    def load_misc_all(self) -> None:
        """依次读取所有剩余只读接口，汇总成一张表。"""
        self.misc_button.setEnabled(False)
        self.misc_hint.setText("正在依次读取…")
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
        self.misc_hint.setText(f"已读取 {len(table_rows)} 个接口。")
        self.load_captcha()

    def load_misc(self, name: str) -> None:
        def done(result: Any) -> None:
            data = (result or {}).get("data")
            self.misc_hint.setText(f"{name}：{_summarize(name, data) or (result or {}).get('message') or '无数据'}")

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
        self.pwd_hint.setText("正在提交…")

        def done(result: Any) -> None:
            self.pwd_button.setEnabled(True)
            if isinstance(result, dict) and result.get("ok"):
                self.pwd_hint.setText("密码已修改，本机保存的密码也已同步更新。")
                Toast.success(self, "密码已修改", "请记住新密码，下次认证将使用它。")
                self.new_pwd.clear()
                self.captcha_edit.clear()
            else:
                message = str((result or {}).get("message") or "修改失败")
                self.pwd_hint.setText(message)
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
                "暂停后你的校园网账号会在学校侧被停用，本机以及其它已登录设备会立即断网，需要重新「恢复使用」才能上网。",
                "暂停",
                detail="这是对学校侧账号生效的操作，请确认你确实不再需要上网。",
                confirm_text="确认申请暂停",
            )
            if not confirmed:
                Toast.info(self, "已取消", "没有执行暂停，账号状态未改变。")
                return
        name = "set_pause" if enable else "restore_pause"

        def done(result: Any) -> None:
            if isinstance(result, dict) and result.get("ok"):
                Toast.success(self, "已提交", str(result.get("data") or ("已申请暂停" if enable else "已恢复")))
            else:
                Toast.warning(self, "未成功", str((result or {}).get("message") or "学校侧未接受该操作"))

        run_async(self.service.selfservice_call, name, on_done=done, on_error=self._fail)

    def _fail(self, message: str) -> None:
        self.flow_button.setEnabled(True)
        self.pwd_button.setEnabled(True)
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
