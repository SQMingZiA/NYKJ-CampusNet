"""实时流量曲线、历史用量柱状图与每日用量热力图（外观跟着主题走）。

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
from PyQt5.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

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
    """GitHub 贡献图样式的每日用量热力图（纯 QWidget 自绘）。

    格子随控件宽度自适应：窄窗口回到 16px 一格，宽窗口放大到最多 22px，
    并把整块网格水平、垂直居中，避免右边留下大片空白、看起来像"偏了"。
    """

    dayClicked = pyqtSignal(str)

    CELL = 16
    MAX_CELL = 22
    GAP = 3
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
        # 绘制几何，由 _layout() 统一算出来，paintEvent / cell_at 共用。
        self._origin_x = float(self.LEFT_W)
        self._origin_y = float(self.TOP_H)
        self._cell = float(self.CELL)
        self._pitch = float(self.CELL + self.GAP)
        self.setMouseTracking(True)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
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
        self._layout()
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
        self._layout()
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

        seen_months: set[tuple[int, int]] = set()
        for day, item in dated:
            column = (day - first_monday).days // 7
            if column < 0 or column >= self._week_count:
                continue
            self._grid[(column, day.weekday())] = item
            key = (day.year, day.month)
            if day.day == 1 and key not in seen_months:
                seen_months.add(key)
                self._month_labels.append((column, f"{day.month}月"))

    def _layout(self) -> None:
        """算出格子大小与网格原点：宽度够就放大格子，并把网格居中。"""
        weeks = max(1, self._week_count)
        available = float(self.width()) - self.LEFT_W - 8
        pitch = available / weeks if available > 0 else float(self.CELL + self.GAP)
        pitch = max(float(self.CELL + self.GAP), min(float(self.MAX_CELL + self.GAP), pitch))
        cell = max(float(self.CELL), pitch - self.GAP)
        grid_w = weeks * pitch - self.GAP
        grid_h = 7 * pitch - self.GAP
        self._pitch = pitch
        self._cell = cell
        self._origin_x = self.LEFT_W + max(0.0, (available - grid_w) / 2.0)
        self._origin_y = self.TOP_H + max(0.0, (self.height() - self.TOP_H - grid_h) / 2.0)

    def _cell_rect(self, column: int, row: int) -> QRect:
        self._layout()
        return QRect(
            int(round(self._origin_x + column * self._pitch)),
            int(round(self._origin_y + row * self._pitch)),
            int(round(self._cell)),
            int(round(self._cell)),
        )

    def cell_at(self, pos: QPoint) -> dict[str, Any] | None:
        """命中测试：返回 ``pos`` 所在格子的数据行，空格子/空白返回 ``None``。"""
        self._layout()
        x = int(pos.x()) - int(round(self._origin_x))
        y = int(pos.y()) - int(round(self._origin_y))
        if x < 0 or y < 0:
            return None
        column, offset_x = divmod(x, int(round(self._pitch)))
        row, offset_y = divmod(y, int(round(self._pitch)))
        if offset_x >= self._cell or offset_y >= self._cell:
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
        pitch = float(self.CELL + self.GAP)
        return QSize(
            int(self.LEFT_W + weeks * pitch + 4),
            int(self.TOP_H + 7 * pitch + 8),
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout()
        self.update()

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

        self._layout()
        pitch = self._pitch
        cell = self._cell
        for row, text in ((0, "一"), (2, "三"), (4, "五"), (6, "日")):
            rect = QRect(
                0,
                int(round(self._origin_y + row * pitch)),
                int(self.LEFT_W) - 6,
                int(round(cell)),
            )
            painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, text)

        for column, text in self._month_labels:
            rect = QRect(
                int(round(self._origin_x + column * pitch)),
                2,
                int(round(pitch * 2 + cell)),
                int(self.TOP_H) - 6,
            )
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)

        for (column, row), item in self._grid.items():
            rect = QRect(
                int(round(self._origin_x + column * pitch)),
                int(round(self._origin_y + row * pitch)),
                int(round(cell)),
                int(round(cell)),
            )
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
                    QRect(
                        int(round(self._origin_x + column * pitch)),
                        int(round(self._origin_y + row * pitch)),
                        int(round(cell)) - 1,
                        int(round(cell)) - 1,
                    ),
                    3,
                    3,
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
