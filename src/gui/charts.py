"""实时流量曲线、历史用量柱状图与每日用量热力图（外观跟着主题走）。

前两个图基于 pyqtgraph，热力图是纯 ``QWidget`` 自绘的 GitHub 贡献图样式。

两条曲线都支持**鼠标悬停查看具体数值**：
把鼠标移到图上，会出现竖线 + 数值气泡（时刻、下行、上行）。
"""
from __future__ import annotations

import calendar
import datetime as dt
import time
from collections import deque
from typing import Any

import pyqtgraph as pg
from PyQt5.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import StrongBodyLabel, TransparentToolButton

from . import theme
from .formatting import format_bytes, format_rate

DEFAULT_WINDOW_SECONDS = 300


class LiveChart(QWidget):
    """最近 N 秒的上行/下行速率曲线（滑动时间窗）。"""

    def __init__(
        self,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.window_seconds = max(30, int(window_seconds))
        capacity = max(60, self.window_seconds * 4)
        self._rx = deque(maxlen=capacity)
        self._tx = deque(maxlen=capacity)
        self._times = deque(maxlen=capacity)

        pg.setConfigOptions(antialias=True, useOpenGL=False)

        self.plot = pg.PlotWidget()
        self.plot.setBackground(None)
        self.plot.showGrid(x=False, y=True, alpha=0.16)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.setLabel('left', 'MB/s')
        self.plot.getAxis('left').setWidth(72)
        self.plot.getAxis('bottom').setStyle(showValues=True)
        for axis in ('left', 'bottom'):
            self.plot.getAxis(axis).setPen(
                pg.mkPen(color=(140, 140, 140, 110), width=1)
            )
        self.plot.getAxis('left').setTextPen(pg.mkPen(color=(140, 140, 140)))

        colors = theme.chart_colors()
        rx_color = pg.mkColor(colors['rx'])
        rx_fill = pg.mkColor(colors['rx'])
        rx_fill.setAlpha(60)
        tx_fill = pg.mkColor(colors['tx'])
        tx_fill.setAlpha(50)
        self.rx_curve = self.plot.plot(
            pen=pg.mkPen(color=rx_color, width=2),
            brush=pg.mkBrush(rx_fill),
            fillLevel=0.0,
            name='下载',
        )
        self.tx_curve = self.plot.plot(
            pen=pg.mkPen(color=pg.mkColor(colors['tx']), width=2),
            brush=pg.mkBrush(tx_fill),
            fillLevel=0.0,
            name='上传',
        )

        self._cross = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen(color=(150, 150, 150, 160), width=1, style=Qt.DashLine),
        )
        self._cross.setVisible(False)
        self.plot.addItem(self._cross, ignoreBounds=True)
        self._bubble = pg.TextItem(anchor=(0, 1), color=theme.color('text'))
        self._bubble.setVisible(False)
        self.plot.addItem(self._bubble, ignoreBounds=True)
        self._proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved, rateLimit=40, slot=self._on_mouse
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)
        self.setMinimumHeight(200)
        self.plot.setYRange(0, 1.0, padding=0)
        self._hint = '把鼠标移到曲线上可查看该时刻的速率'
        self.plot.setToolTip(self._hint)

    def apply_theme(self) -> None:
        """主题切换后刷新轴线、曲线与气泡颜色。"""
        colors = theme.chart_colors()
        for axis in ('left', 'bottom'):
            self.plot.getAxis(axis).setPen(
                pg.mkPen(color=colors['grid'], width=1)
            )
            self.plot.getAxis(axis).setTextPen(pg.mkPen(color=colors['text']))
        self.rx_curve.setPen(pg.mkPen(color=colors['rx'], width=2))
        self.tx_curve.setPen(pg.mkPen(color=colors['tx'], width=2))
        self._bubble.setColor(theme.color('text'))
        self._redraw()

    def append(
        self, rx_rate: float, tx_rate: float, at: float | None = None
    ) -> None:
        """追加一个采样点（字节/秒）。"""
        moment = float(at or time.time())
        self._rx.append(float(rx_rate or 0) / 1048576.0)
        self._tx.append(float(tx_rate or 0) / 1048576.0)
        self._times.append(moment)
        self._redraw()

    def load_history(self, samples: list[dict]) -> None:
        """用历史采样初始化曲线（只取时间窗内的）。"""
        self._rx.clear()
        self._tx.clear()
        self._times.clear()
        now = time.time()
        for item in samples or []:
            moment = float(item.get('at') or now)
            if now - moment > self.window_seconds:
                continue
            self._rx.append(float(item.get('rx_rate') or 0) / 1048576.0)
            self._tx.append(float(item.get('tx_rate') or 0) / 1048576.0)
            self._times.append(moment)
        self._redraw()

    def _prune(self, now: float) -> None:
        """丢掉超出时间窗的老记录。"""
        cutoff = now - self.window_seconds
        while self._times and self._times[0] < cutoff:
            self._times.popleft()
            self._rx.popleft()
            self._tx.popleft()

    def _redraw(self) -> None:
        now = time.time()
        self._prune(now)
        if not self._times:
            self.rx_curve.setData([], [])
            self.tx_curve.setData([], [])
            self.plot.setYRange(0, 1.0, padding=0)
            self.plot.setXRange(-self.window_seconds, 0, padding=0)
            self._apply_time_ticks(now)
            return
        xs = [t - now for t in self._times]
        rx_values = list(self._rx)
        tx_values = list(self._tx)
        self.rx_curve.setData(xs, rx_values)
        self.tx_curve.setData(xs, tx_values)
        peak = max(1e-06, max(rx_values + tx_values, default=0.0))
        self.plot.setYRange(0, peak * 1.18, padding=0)
        self.plot.setXRange(-self.window_seconds, 0, padding=0)
        self._apply_time_ticks(now)

    def _apply_time_ticks(self, now: float) -> None:
        """把横轴刻度标成真实时刻（HH:MM:SS）。"""
        span = self.window_seconds
        step = 60 if span <= 600 else 300
        ticks = []
        offset = 0
        while offset <= span:
            x = -offset
            ticks.append(
                (x, time.strftime('%H:%M:%S', time.localtime(now - offset)))
            )
            offset += step
        ticks.reverse()
        self.plot.getAxis('bottom').setTicks([ticks])

    def _on_mouse(self, event) -> None:
        if not self._times:
            return
        position = event[0]
        if not self.plot.sceneBoundingRect().contains(position):
            self._cross.setVisible(False)
            self._bubble.setVisible(False)
            return
        view_point = self.plot.getPlotItem().vb.mapSceneToView(position)
        now = time.time()
        target = now + view_point.x()
        index = min(
            range(len(self._times)),
            key=lambda i: abs(self._times[i] - target),
        )
        x = self._times[index] - now
        rx = self._rx[index]
        tx = self._tx[index]
        self._cross.setPos(x)
        self._cross.setVisible(True)
        self._bubble.setText(
            f"{time.strftime('%H:%M:%S', time.localtime(self._times[index]))}"
            f"\n↓ {format_rate(rx * 1048576)}"
            f"\n↑ {format_rate(tx * 1048576)}"
        )
        self._bubble.setPos(x, max(rx, tx))
        self._bubble.setVisible(True)

    def current_text(self) -> str:
        rx = self._rx[-1] if self._rx else 0.0
        tx = self._tx[-1] if self._tx else 0.0
        return f"↓ {rx * 1048576:.0f} B/s  ↑ {tx * 1048576:.0f} B/s"


class HistoryChart(QWidget):
    """历史用量柱状图（下载+上传堆叠），支持悬停查看数值。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plot = pg.PlotWidget()
        self.plot.setBackground(None)
        self.plot.showGrid(y=True, alpha=0.18)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.setLabel('left', '用量')
        self.plot.getAxis('left').setWidth(84)
        for axis in ('left', 'bottom'):
            self.plot.getAxis(axis).setPen(
                pg.mkPen(color=(140, 140, 140, 120), width=1)
            )
        self.plot.getAxis('left').setTextPen(pg.mkPen(color=(150, 150, 150)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)
        self.setMinimumHeight(240)
        self._labels = []
        self._items = []
        self._label_key = 'key'
        self.plot.setToolTip('把鼠标移到柱子上可查看该时间段的用量')

        self._cross = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen(color=(150, 150, 150, 150), width=1, style=Qt.DashLine),
        )
        self._cross.setVisible(False)
        self.plot.addItem(self._cross, ignoreBounds=True)
        self._bubble = pg.TextItem(anchor=(0, 1), color=theme.color('text'))
        self._bubble.setVisible(False)
        self.plot.addItem(self._bubble, ignoreBounds=True)
        self._proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved, rateLimit=40, slot=self._on_mouse
        )

    def _bar_width_units(self, count: int) -> float:
        """柱子宽度（数据坐标）—— 按像素反算，窗口再宽柱子也不会变粗。

        ``BarGraphItem`` 的 ``width`` 是**数据单位**：原来固定 0.34，等于
        "每个槽位的 34%"，窗口一拉宽槽位像素变大，柱子就跟着变粗
        （全屏时能有四五十像素）。这里改成"约 11 像素"反算成数据单位。
        """
        target_px = 11.0
        plot_px = self.plot.width() - self.plot.getAxis('left').width() - 8
        if plot_px <= 20:
            return 0.12
        units = target_px * max(1, count) / plot_px
        return max(0.05, min(0.26, units))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._items and abs(self.width() - getattr(self, '_last_width', 0)) > 8:
            self._last_width = self.width()
            self.set_data(self._items, self._label_key)

    def apply_theme(self) -> None:
        """主题切换后刷新轴线、刻度与柱子颜色。"""
        colors = theme.chart_colors()
        for axis in ('left', 'bottom'):
            self.plot.getAxis(axis).setPen(
                pg.mkPen(color=colors['grid'], width=1)
            )
            self.plot.getAxis(axis).setTextPen(pg.mkPen(color=colors['text']))
        self._bubble.setColor(theme.color('text'))
        if self._items:
            self.set_data(self._items, self._label_key)

    def set_data(self, items: list[dict], label_key: str = 'key') -> None:
        """设置数据：每项含 rx / tx（字节）。"""
        self.plot.clear()
        self._cross.setVisible(False)
        self._bubble.setVisible(False)

        self.plot.addItem(self._cross, ignoreBounds=True)
        self.plot.addItem(self._bubble, ignoreBounds=True)

        self._items = list(items or [])
        self._label_key = label_key
        if not self._items:
            self._labels = []
            return
        items = self._items
        colors = theme.chart_colors()
        self._labels = [str(item.get(label_key, '')) for item in items]
        xs = list(range(len(items)))
        rx = [float(item.get('rx', 0)) / 1048576.0 for item in items]
        tx = [float(item.get('tx', 0)) / 1048576.0 for item in items]

        bar_width = self._bar_width_units(len(xs))
        rx_bars = pg.BarGraphItem(x=xs, height=rx, width=bar_width, brush=pg.mkBrush(colors['rx']), pen=pg.mkPen(None))
        tx_bars = pg.BarGraphItem(x=xs, y=rx, height=tx, width=bar_width, brush=pg.mkBrush(colors['tx']), pen=pg.mkPen(None))
        self.plot.addItem(rx_bars)
        self.plot.addItem(tx_bars)

        axis = self.plot.getAxis('bottom')
        ticks = self._build_ticks(xs, self._labels)
        axis.setTicks([ticks])
        axis.setStyle(showValues=True)
        for name in ('left', 'bottom'):
            self.plot.getAxis(name).setPen(
                pg.mkPen(color=colors['grid'], width=1)
            )
            self.plot.getAxis(name).setTextPen(pg.mkPen(color=colors['text']))
        peak = max([r + t for r, t in zip(rx, tx)] or [1.0])
        self.plot.setYRange(0, peak * 1.15, padding=0)
        self.plot.setXRange(-0.6, len(items) - 0.4, padding=0)

    def _on_mouse(self, event) -> None:
        if not self._items:
            return
        position = event[0]
        if not self.plot.sceneBoundingRect().contains(position):
            self._cross.setVisible(False)
            self._bubble.setVisible(False)
            return
        view_point = self.plot.getPlotItem().vb.mapSceneToView(position)
        index = int(round(view_point.x()))
        if index < 0 or index >= len(self._items):
            self._cross.setVisible(False)
            self._bubble.setVisible(False)
            return
        item = self._items[index]
        rx = float(item.get('rx', 0))
        tx = float(item.get('tx', 0))
        self._cross.setPos(index)
        self._cross.setVisible(True)
        self._bubble.setText(
            f"{self._labels[index]}"
            f"\n↓ {format_bytes(rx)}"
            f"\n↑ {format_bytes(tx)}"
            f"\nΣ {format_bytes(rx + tx)}"
        )
        self._bubble.setPos(index, (rx + tx) / 1048576.0)
        self._bubble.setVisible(True)

    @staticmethod
    def _build_ticks(xs: list[int], labels: list[str]) -> list[tuple[int, str]]:
        if not xs:
            return []
        step = max(1, (len(xs) + 6) // 7)
        chosen = list(xs[::step])
        if chosen[-1] != xs[-1]:
            chosen.append(xs[-1])
        ticks = []
        for index in chosen:
            label = labels[index] if index < len(labels) else ''
            if 'T' in label:
                ticks.append((index, label.split('T')[-1] + ':00'))
            else:
                ticks.append((index, label[5:] if len(label) >= 10 else label))
        return ticks



class HeatmapChart(QWidget):
    """月历样式的每日用量热力图（纯 ``QWidget`` 自绘）。

    一张卡只显示**一个月**：顶部是「‹ 2026 年 11 月 ›」导航，下面是
    周一开头的星期表头与 6×7 的日期网格。格子随控件宽度在 40~64px 之间
    自适应，整块网格水平居中；颜色按当月最大用量分 4 级强调色。
    绘制几何统一由 :meth:`_layout` 算出，``paintEvent`` / ``cell_at`` /
    tooltip 共用同一套坐标，避免"画面居中但点击位置没跟上"。
    """

    dayClicked = pyqtSignal(str)
    monthChanged = pyqtSignal(int, int)

    CELL_MIN = 40
    CELL_MAX = 64
    GAP = 4
    RADIUS = 6
    SIDE_PAD = 8
    MONTH_BAR_H = 40
    WEEKDAY_H = 24
    WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")
    _LEVEL_ALPHA = (0.25, 0.45, 0.7, 1.0)
    _FALLBACK_MIN_MONTH = (2025, 1)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []
        self._by_date: dict[str, dict[str, Any]] = {}
        self._cells: dict[tuple[int, int], dict[str, Any]] = {}
        self._month_max = 0
        self.selected_date: str | None = None

        today = dt.date.today()
        self._year = today.year
        self._month = today.month
        self._min_key = self._key(*self._FALLBACK_MIN_MONTH)
        self._max_key = self._key(today.year, today.month)
        self._cell = float(self.CELL_MIN)
        self._pitch = float(self.CELL_MIN + self.GAP)
        self._origin_x = 0.0
        self._origin_y = float(self.MONTH_BAR_H + self.WEEKDAY_H)
        self._weekday_y = float(self.MONTH_BAR_H)
        self._hover_key: tuple[int, int] | None = None

        self.setMouseTracking(True)
        self.setMinimumSize(self.minimumSizeHint())
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("把鼠标移到日期上可查看当天用量；点一下选中当天")

        self._header = QWidget(self)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(8, 0, 8, 0)
        header_layout.setSpacing(4)
        header_layout.addStretch(1)

        self.prev_button = TransparentToolButton(self._header)
        self.prev_button.setIcon(FIF.LEFT_ARROW)
        self.prev_button.setIconSize(QSize(14, 14))
        self.prev_button.setFixedSize(32, 32)
        self.prev_button.setToolTip("上个月")
        self.prev_button.clicked.connect(self.prev_month)
        header_layout.addWidget(self.prev_button)

        self.month_label = StrongBodyLabel("", self._header)
        self.month_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.month_label)

        self.next_button = TransparentToolButton(self._header)
        self.next_button.setIcon(FIF.RIGHT_ARROW)
        self.next_button.setIconSize(QSize(14, 14))
        self.next_button.setFixedSize(32, 32)
        self.next_button.setToolTip("下个月")
        self.next_button.clicked.connect(self.next_month)
        header_layout.addWidget(self.next_button)
        header_layout.addStretch(1)

        self._rebuild_cells()
        self._sync_month_ui()

    # ------------------------------------------------------------------ 月份
    @staticmethod
    def _key(year: int, month: int) -> int:
        return int(year) * 12 + int(month) - 1

    @property
    def current_month(self) -> tuple[int, int]:
        """当前显示的 ``(年, 月)``。"""
        return (self._year, self._month)

    def _month_key(self) -> int:
        return self._key(self._year, self._month)

    def _sync_month_ui(self) -> None:
        self.month_label.setText(f"{self._year} 年 {self._month} 月")
        key = self._month_key()
        self.prev_button.setEnabled(key > self._min_key)
        self.next_button.setEnabled(key < self._max_key)
        self.prev_button.setToolTip(
            "上个月" if key > self._min_key else "已经是最早的数据月份"
        )
        self.next_button.setToolTip(
            "下个月" if key < self._max_key else "已经是最新月份"
        )

    def _set_month_key(self, key: int, emit: bool = True) -> bool:
        """切换到 ``key`` 对应的月份；返回是否真的发生了切换。"""
        key = max(self._min_key, min(self._max_key, int(key)))
        changed = key != self._month_key()
        if changed:
            self._year, month0 = divmod(key, 12)
            self._month = month0 + 1
            if self.selected_date and self.selected_date[:7] != self._month_prefix():
                self.selected_date = None
        self._rebuild_cells()
        self._sync_month_ui()
        self._layout()
        self.update()
        if changed and emit:
            self.monthChanged.emit(self._year, self._month)
        return changed

    def _month_prefix(self) -> str:
        return f"{self._year:04d}-{self._month:02d}"

    def prev_month(self) -> None:
        """切到上一个月（受最早数据月份限制）。"""
        self._set_month_key(self._month_key() - 1)

    def next_month(self) -> None:
        """切到下一个月（到本月为止）。"""
        self._set_month_key(self._month_key() + 1)

    def month_total(self) -> int:
        """当前显示月份所有日期的用量合计。"""
        return sum(int(item.get("total") or 0) for item in self._cells.values())

    # ------------------------------------------------------------------ 数据
    def set_days(self, rows: list[dict[str, Any]]) -> None:
        """设置每日数据。

        ``rows`` 为 ``[{"date", "rx", "tx", "total"}, ...]``，可以跨越多个月。
        设置后默认显示**最新一条数据所在的月份**（不超过本月）；最早可翻到
        数据里最早的月份，没有数据时最早回退到 2025-01。
        """
        self._rows = [dict(item) for item in (rows or [])]
        self._by_date = {}
        dated: list[dt.date] = []
        for item in self._rows:
            raw = str(item.get("date") or "")
            try:
                day = dt.date.fromisoformat(raw[:10])
            except (TypeError, ValueError):
                continue
            item["date"] = day.isoformat()
            self._by_date[day.isoformat()] = item
            dated.append(day)

        today = dt.date.today()
        self._max_key = self._key(today.year, today.month)
        if dated:
            first = min(dated)
            self._min_key = self._key(first.year, first.month)
            target = self._key(max(dated).year, max(dated).month)
        else:
            self._min_key = self._key(*self._FALLBACK_MIN_MONTH)
            target = self._max_key
        self._min_key = min(self._min_key, self._max_key)
        self._set_month_key(target)

    def clear(self) -> None:
        """清空数据、选中状态并回到本月。"""
        self._rows = []
        self._by_date = {}
        self._cells = {}
        self._month_max = 0
        self.selected_date = None
        today = dt.date.today()
        self._min_key = self._key(*self._FALLBACK_MIN_MONTH)
        self._max_key = self._key(today.year, today.month)
        self._set_month_key(self._max_key)
        self._hover_key = None
        self.setToolTip("把鼠标移到日期上可查看当天用量；点一下选中当天")
        self.unsetCursor()

    def _rebuild_cells(self) -> None:
        """按当前月份铺出 6×7 网格；月外位置留空。"""
        self._cells = {}
        self._month_max = 0
        first = dt.date(self._year, self._month, 1)
        lead = first.weekday()  # 周一 = 0
        days = calendar.monthrange(self._year, self._month)[1]
        for day_number in range(1, days + 1):
            day = dt.date(self._year, self._month, day_number)
            iso = day.isoformat()
            raw = self._by_date.get(iso) or {}
            total = int(raw.get("total") or 0)
            item = {
                "date": iso,
                "total": total,
                "rx": int(raw.get("rx") or 0),
                "tx": int(raw.get("tx") or 0),
            }
            index = lead + day_number - 1
            row, column = divmod(index, 7)
            if row >= 6:
                continue
            self._cells[(row, column)] = item
            self._month_max = max(self._month_max, total)

    # ------------------------------------------------------------------ 几何
    def _layout(self) -> None:
        """统一计算格子尺寸、网格原点与星期表头位置。"""
        width = float(max(1, self.width()))
        available = max(0.0, width - 2 * self.SIDE_PAD)
        cell = (available - 6 * self.GAP) / 7.0
        cell = max(float(self.CELL_MIN), min(float(self.CELL_MAX), cell))
        pitch = cell + self.GAP
        grid_w = 7 * cell + 6 * self.GAP
        grid_h = 6 * cell + 5 * self.GAP

        block_h = self.WEEKDAY_H + grid_h
        free = max(0.0, float(self.height()) - self.MONTH_BAR_H)
        top = self.MONTH_BAR_H + max(0.0, (free - block_h) / 2.0)

        self._cell = cell
        self._pitch = pitch
        self._origin_x = (width - grid_w) / 2.0
        self._weekday_y = top
        self._origin_y = top + self.WEEKDAY_H

    def _cell_rect(self, row: int, column: int) -> QRect:
        self._layout()
        return QRect(
            int(round(self._origin_x + column * self._pitch)),
            int(round(self._origin_y + row * self._pitch)),
            int(round(self._cell)),
            int(round(self._cell)),
        )

    def _cell_key_at(self, pos: QPoint) -> tuple[int, int] | None:
        self._layout()
        x = float(pos.x()) - self._origin_x
        y = float(pos.y()) - self._origin_y
        if x < 0 or y < 0:
            return None
        column = int(x // self._pitch)
        row = int(y // self._pitch)
        if column < 0 or column >= 7 or row < 0 or row >= 6:
            return None
        if x - column * self._pitch >= self._cell or y - row * self._pitch >= self._cell:
            return None
        return (row, column)

    def cell_at(self, pos: QPoint) -> dict[str, Any] | None:
        """命中测试：返回日期 dict（``date/total/rx/tx``），空白格返回 ``None``。"""
        key = self._cell_key_at(pos)
        if key is None:
            return None
        return self._cells.get(key)

    # ------------------------------------------------------------------ 配色
    def _cell_color(self, item: dict[str, Any]) -> QColor:
        """0 值用卡片浅灰；非 0 按当月最大值分 4 级强调色。"""
        total = int(item.get("total") or 0)
        if total <= 0 or self._month_max <= 0:
            return QColor(theme.color("card_alt"))
        ratio = total / float(self._month_max)
        if ratio <= 0.25:
            alpha = self._LEVEL_ALPHA[0]
        elif ratio <= 0.5:
            alpha = self._LEVEL_ALPHA[1]
        elif ratio <= 0.75:
            alpha = self._LEVEL_ALPHA[2]
        else:
            alpha = self._LEVEL_ALPHA[3]
        color = QColor(theme.color("accent") or "#0F6CBD")
        color.setAlpha(int(round(255 * alpha)))
        return color

    @staticmethod
    def _text_color(fill: QColor) -> QColor:
        """按格子叠在卡片底色上的亮度挑深/浅字，保证日号看得清。"""
        base = QColor(theme.color("card") or "#FFFFFF")
        alpha = max(0.0, min(1.0, fill.alphaF()))
        red = base.red() * (1 - alpha) + fill.red() * alpha
        green = base.green() * (1 - alpha) + fill.green() * alpha
        blue = base.blue() * (1 - alpha) + fill.blue() * alpha
        luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0
        return QColor("#FFFFFF" if luminance < 0.55 else "#1B1B1B")

    # ------------------------------------------------------------------ 绘制
    def sizeHint(self) -> QSize:
        cell = float(self.CELL_MAX)
        return QSize(
            int(7 * cell + 6 * self.GAP + 2 * self.SIDE_PAD),
            int(self.MONTH_BAR_H + self.WEEKDAY_H + 6 * cell + 5 * self.GAP),
        )

    def minimumSizeHint(self) -> QSize:
        cell = float(self.CELL_MIN)
        return QSize(
            int(7 * cell + 6 * self.GAP + 2 * self.SIDE_PAD),
            int(self.MONTH_BAR_H + self.WEEKDAY_H + 6 * cell + 5 * self.GAP),
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_header"):
            self._header.setGeometry(0, 0, self.width(), self.MONTH_BAR_H)
        self._layout()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._layout()

        header_font = painter.font()
        header_font.setPointSize(9)
        header_font.setBold(False)
        painter.setFont(header_font)
        painter.setPen(QColor(theme.color("text_muted")))
        for column, text in enumerate(self.WEEKDAY_LABELS):
            painter.drawText(
                QRectF(
                    self._origin_x + column * self._pitch,
                    self._weekday_y,
                    self._cell,
                    self.WEEKDAY_H,
                ),
                Qt.AlignCenter,
                text,
            )

        day_font = painter.font()
        day_font.setPointSize(10)
        day_font.setBold(True)
        painter.setFont(day_font)

        blank = QColor(theme.color("card_border") or "#E5E5E5")
        blank.setAlpha(45)
        for row in range(6):
            for column in range(7):
                rect = QRectF(
                    self._origin_x + column * self._pitch,
                    self._origin_y + row * self._pitch,
                    self._cell,
                    self._cell,
                )
                item = self._cells.get((row, column))
                if item is None:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(blank)
                    painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
                    continue

                fill = self._cell_color(item)
                painter.setPen(Qt.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)

                total = int(item.get("total") or 0)
                if total > 0:
                    painter.setPen(self._text_color(fill))
                else:
                    painter.setPen(QColor(theme.color("text_muted")))
                painter.drawText(rect, Qt.AlignCenter, str(int(item["date"][-2:])))

        today = dt.date.today().isoformat()
        for (row, column), item in self._cells.items():
            date = str(item.get("date") or "")
            rect = QRectF(
                self._origin_x + column * self._pitch,
                self._origin_y + row * self._pitch,
                self._cell,
                self._cell,
            )
            if date == self.selected_date:
                pen = QPen(QColor(theme.color("accent")), 2.5)
                inset = 1.8
            elif date == today:
                pen = QPen(QColor(theme.color("accent")), 1.4)
                inset = 1.0
            elif self._hover_key == (row, column):
                hover = QColor(theme.color("text_secondary") or "#5C5C5C")
                hover.setAlpha(150)
                pen = QPen(hover, 1.0)
                inset = 1.0
            else:
                continue
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                rect.adjusted(inset, inset, -inset, -inset),
                self.RADIUS - 1,
                self.RADIUS - 1,
            )

    # ------------------------------------------------------------------ 交互
    def _tooltip_text(self, item: dict[str, Any] | None) -> str:
        if item is None:
            return ""
        date = str(item.get("date") or "")
        if int(item.get("total") or 0) <= 0:
            return f"{date} · 没有记录"
        return (
            f"{date} · 共 {format_bytes(item.get('total'))}"
            f"（↓ {format_bytes(item.get('rx'))} ↑ {format_bytes(item.get('tx'))}）"
        )

    def mouseMoveEvent(self, event) -> None:
        key = self._cell_key_at(event.pos())
        if key != self._hover_key:
            self._hover_key = key
            self.update()
        item = self._cells.get(key) if key is not None else None
        self.setToolTip(self._tooltip_text(item))
        if item is None:
            self.unsetCursor()
        else:
            self.setCursor(Qt.PointingHandCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_key is not None:
            self._hover_key = None
            self.update()
        self.unsetCursor()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            item = self.cell_at(event.pos())
            if item is not None:
                date = str(item.get("date") or "")
                if date:
                    self.selected_date = date
                    self.update()
                    self.dayClicked.emit(date)
        super().mouseReleaseEvent(event)
