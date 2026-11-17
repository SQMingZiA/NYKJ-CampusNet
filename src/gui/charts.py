"""实时流量曲线、历史用量柱状图、每日用量热力图与诊断趋势图（外观跟着主题走）。

前两个图基于 pyqtgraph，热力图是纯 ``QWidget`` 自绘的 GitHub 贡献图样式。

两条曲线都支持**鼠标悬停查看具体数值**：
把鼠标移到图上，会出现竖线 + 数值气泡（时刻、下行、上行）。
"""
from __future__ import annotations

import datetime as dt
import time
from collections import deque
from typing import Any

import pyqtgraph as pg
from PyQt5.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QVBoxLayout, QWidget

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
    """GitHub 贡献图样式的每日用量热力图（纯 QWidget 自绘）。"""

    dayClicked = pyqtSignal(str)

    CELL = 16
    GAP = 3
    PITCH = CELL + GAP
    LEFT_W = 34
    TOP_H = 24

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []
        self._grid: dict[tuple[int, int], dict[str, Any]] = {}
        self._week_count = 12
        self._month_labels: list[tuple[int, str]] = []
        self._max_total = 0
        self.selected_date: str | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(150)
        self.setToolTip("把鼠标移到格子上可查看当天用量；点一下选中当天")

    def set_days(self, rows: list[dict[str, Any]]) -> None:
        """设置每日数据（按日期升序，最后一天是今天）。"""
        self._rows = [dict(item) for item in (rows or [])]
        self._max_total = max(
            (int(item.get("total") or 0) for item in self._rows), default=0
        )
        self._rebuild_cells()
        if self.selected_date and self.selected_date not in self._grid_dates():
            self.selected_date = None
        self.updateGeometry()
        self.update()

    def clear(self) -> None:
        """清空数据与选中状态。"""
        self._rows = []
        self._grid = {}
        self._month_labels = []
        self._max_total = 0
        self._week_count = 12
        self.selected_date = None
        self.updateGeometry()
        self.update()

    def _grid_dates(self) -> set[str]:
        return {str(item.get("date") or "") for item in self._rows}

    def _rebuild_cells(self) -> None:
        """按自然周（周一为列首）把日期铺进网格。"""
        self._grid = {}
        self._month_labels = []
        dated: list[tuple[dt.date, dict[str, Any]]] = []
        for item in self._rows:
            raw = str(item.get("date") or "")
            try:
                day = dt.date.fromisoformat(raw[:10])
            except ValueError:
                continue
            dated.append((day, item))
        if not dated:
            self._week_count = 12
            return

        first_day = min(day for day, _ in dated)
        first_monday = first_day - dt.timedelta(days=first_day.weekday())
        today = dt.date.today()
        last_day = max(today, max(day for day, _ in dated))
        last_monday = last_day - dt.timedelta(days=last_day.weekday())
        self._week_count = max(1, (last_monday - first_monday).days // 7 + 1)

        seen_months: set[int] = set()
        for day, item in dated:
            column = (day - first_monday).days // 7
            if column < 0 or column >= self._week_count:
                continue
            self._grid[(column, day.weekday())] = item
            if day.day == 1 and day.month not in seen_months:
                seen_months.add(day.month)
                self._month_labels.append((column, f"{day.month}月"))

    def _cell_rect(self, column: int, row: int) -> QRect:
        return QRect(
            self.LEFT_W + column * self.PITCH,
            self.TOP_H + row * self.PITCH,
            self.CELL,
            self.CELL,
        )

    def cell_at(self, pos: QPoint) -> dict[str, Any] | None:
        """命中测试：返回 ``pos`` 所在格子的数据行，空格子/空白返回 ``None``。"""
        x = int(pos.x()) - self.LEFT_W
        y = int(pos.y()) - self.TOP_H
        if x < 0 or y < 0:
            return None
        column, offset_x = divmod(x, self.PITCH)
        row, offset_y = divmod(y, self.PITCH)
        if offset_x >= self.CELL or offset_y >= self.CELL:
            return None
        if column >= self._week_count or row >= 7:
            return None
        return self._grid.get((column, row))

    def _cell_color(self, item: dict[str, Any]) -> QColor:
        """按该图最大值的四分位分 4 级，叠加不同透明度。"""
        total = int(item.get("total") or 0)
        if total <= 0:
            return QColor("#2B2B2B" if theme.is_dark() else "#EDEDED")
        ratio = total / float(self._max_total or 1)
        if ratio <= 0.25:
            alpha = 0.28
        elif ratio <= 0.5:
            alpha = 0.5
        elif ratio <= 0.75:
            alpha = 0.72
        else:
            alpha = 1.0
        color = QColor(theme.color("accent") or "#0F6CBD")
        color.setAlpha(int(round(255 * alpha)))
        return color

    def sizeHint(self) -> QSize:
        weeks = max(7, self._week_count)
        return QSize(
            self.LEFT_W + weeks * self.PITCH + 4,
            self.TOP_H + 7 * self.PITCH + 4,
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QColor(theme.color("text_muted")))

        if not self._rows:
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            return

        for row, text in ((0, "一"), (2, "三"), (4, "五"), (6, "日")):
            rect = QRect(0, self.TOP_H + row * self.PITCH, self.LEFT_W - 6, self.CELL)
            painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, text)

        for column, text in self._month_labels:
            rect = QRect(
                self.LEFT_W + column * self.PITCH,
                2,
                self.PITCH * 2 + self.CELL,
                self.TOP_H - 6,
            )
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)

        for (column, row), item in self._grid.items():
            rect = self._cell_rect(column, row)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._cell_color(item))
            painter.drawRoundedRect(rect, 3, 3)

        if self.selected_date:
            for (column, row), item in self._grid.items():
                if str(item.get("date") or "") != self.selected_date:
                    continue
                painter.setPen(QPen(QColor(theme.color("accent")), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(
                    self._cell_rect(column, row).adjusted(0, 0, -1, -1), 3, 3
                )
                break

    def mouseMoveEvent(self, event) -> None:
        item = self.cell_at(event.pos())
        if item is None:
            self.setToolTip("")
        elif int(item.get("total") or 0) <= 0:
            self.setToolTip(f"{item.get('date')} · 没有记录")
        else:
            self.setToolTip(
                f"{item.get('date')} · 共 {format_bytes(item.get('total'))}"
                f"（↓ {format_bytes(item.get('rx'))} ↑ {format_bytes(item.get('tx'))}）"
            )
        super().mouseMoveEvent(event)

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

class TrendChart(QWidget):
    """网络诊断的历史趋势：上面是延迟（毫秒），下面是速率（Mbps）。

    每条记录来自一次「一键网络诊断」，横轴是时间。失败的那几次会在延迟曲线
    上打一个红点，方便回看「什么时候开始变差」。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[dict[str, Any]] = []

        self.latency_plot = pg.PlotWidget()
        self.latency_plot.setBackground(None)
        self.latency_plot.showGrid(y=True, alpha=0.16)
        self.latency_plot.setMouseEnabled(x=False, y=False)
        self.latency_plot.hideButtons()
        self.latency_plot.setMenuEnabled(False)
        self.latency_plot.setLabel('left', '延迟')
        self.latency_plot.getAxis('left').setWidth(56)
        self.latency_plot.getAxis('bottom').setStyle(showValues=False)
        self.latency_plot.setToolTip('每次诊断的延迟与抖动（毫秒）')

        self.speed_plot = pg.PlotWidget()
        self.speed_plot.setBackground(None)
        self.speed_plot.showGrid(y=True, alpha=0.16)
        self.speed_plot.setMouseEnabled(x=False, y=False)
        self.speed_plot.hideButtons()
        self.speed_plot.setMenuEnabled(False)
        self.speed_plot.setLabel('left', '速率')
        self.speed_plot.getAxis('left').setWidth(56)
        self.speed_plot.setToolTip('每次诊断的下载与上传速率（Mbps）')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.latency_plot)
        layout.addWidget(self.speed_plot)
        self.setMinimumHeight(260)

        self._style_axes()

    def _style_axes(self) -> None:
        colors = theme.chart_colors()
        for plot in (self.latency_plot, self.speed_plot):
            for axis in ('left', 'bottom'):
                plot.getAxis(axis).setPen(pg.mkPen(color=colors['grid'], width=1))
                plot.getAxis(axis).setTextPen(pg.mkPen(color=colors['text']))

    def apply_theme(self) -> None:
        """主题切换后重画。"""
        self._style_axes()
        if self._records:
            self.set_records(self._records)

    def clear(self) -> None:
        """清空两张图。"""
        self._records = []
        self.latency_plot.clear()
        self.speed_plot.clear()

    def set_records(self, records: list[dict[str, Any]]) -> None:
        """设置诊断记录（时间升序，字段见 ``DiagnosticHistory``）。"""
        self._records = list(records or [])
        self.latency_plot.clear()
        self.speed_plot.clear()
        if not self._records:
            return

        colors = theme.chart_colors()
        items = self._records
        xs = list(range(len(items)))
        labels = [time.strftime('%m-%d %H:%M', time.localtime(float(item.get('at') or 0))) for item in items]

        latency = [float(item.get('latency_ms') or 0.0) for item in items]
        jitter = [float(item.get('jitter_ms') or 0.0) for item in items]
        dns = [float(item.get('dns_ms') or 0.0) for item in items]

        self.latency_plot.addItem(pg.PlotDataItem(
            xs, latency, pen=pg.mkPen(colors['rx'], width=2),
            symbol='o', symbolSize=5, symbolBrush=pg.mkBrush(colors['rx']),
            symbolPen=pg.mkPen(None), name='延迟',
        ))
        self.latency_plot.addItem(pg.PlotDataItem(
            xs, jitter, pen=pg.mkPen(colors['tx'], width=1, style=Qt.DashLine), name='抖动',
        ))
        self.latency_plot.addItem(pg.PlotDataItem(
            xs, dns, pen=pg.mkPen(colors['text'], width=1), name='DNS',
        ))

        bad = [i for i, item in enumerate(items) if not item.get('ok', True)]
        if bad:
            self.latency_plot.addItem(pg.ScatterPlotItem(
                [xs[i] for i in bad], [latency[i] for i in bad],
                symbol='x', size=9, brush=pg.mkBrush(colors['rx']),
                pen=pg.mkPen(colors['rx'], width=2),
            ))
        peak = max(latency + jitter + dns + [1.0])
        self.latency_plot.setYRange(0, peak * 1.2, padding=0)

        down = [float(item.get('down_bps') or 0) / 125000.0 for item in items]
        up = [float(item.get('up_bps') or 0) / 125000.0 for item in items]
        self.speed_plot.addItem(pg.PlotDataItem(
            xs, down, pen=pg.mkPen(colors['rx'], width=2),
            symbol='o', symbolSize=5, symbolBrush=pg.mkBrush(colors['rx']),
            symbolPen=pg.mkPen(None), name='下载',
        ))
        self.speed_plot.addItem(pg.PlotDataItem(
            xs, up, pen=pg.mkPen(colors['tx'], width=2),
            symbol='t', symbolSize=5, symbolBrush=pg.mkBrush(colors['tx']),
            symbolPen=pg.mkPen(None), name='上传',
        ))
        top = max(down + up + [0.1])
        self.speed_plot.setYRange(0, top * 1.2, padding=0)

        for plot in (self.latency_plot, self.speed_plot):
            plot.setXRange(-0.6, len(items) - 0.4, padding=0)
            ticks = self._ticks(xs, labels)
            plot.getAxis('bottom').setTicks([ticks])
        self.latency_plot.getAxis('bottom').setStyle(showValues=False)
        self.speed_plot.getAxis('bottom').setStyle(showValues=True)
        self._style_axes()

    @staticmethod
    def _ticks(xs: list[int], labels: list[str]) -> list[tuple[float, str]]:
        """横轴上最多 5 个刻度，避免挤在一起。"""
        if not xs:
            return []
        step = max(1, (len(xs) + 4) // 5)
        return [(float(xs[i]), labels[i]) for i in range(0, len(xs), step)]
