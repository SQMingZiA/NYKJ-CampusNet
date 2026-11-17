"""流量用量统计。

数据来源（``resources/traffic.ps1``）：

1. ``Get-NetAdapterStatistics`` —— 按网卡，最准确（部分环境需管理员权限）
2. ``netstat -e`` —— 全系统合计，任何权限都能读（兜底）

统计口径：

* 系统/网卡计数器是「开机以来累计值」，本模块按**差值**累加；
* 计数器回绕（网卡重启、系统重启）时按新值处理，绝不产生负数；
* 每次采样进入 5 秒粒度的环形缓冲（实时曲线），
  同时聚合到**小时桶**落盘（``history.json``），可统计今日/本月/全部。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .winutil import run_script_file

RING_SIZE = 720
MAX_HISTORY_HOURS = 60 * 24 * 2


def hour_key(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%dT%H')


def key_to_ts(key: str) -> float | None:
    try:
        return dt.datetime.strptime(key, '%Y-%m-%dT%H').timestamp()
    except (ValueError, TypeError):
        return None


@dataclass
class Timeline:
    """一次采样得到的累计值与增量。"""

    at: float
    rx: int
    tx: int
    delta_rx: int
    delta_tx: int
    rx_rate: float
    tx_rate: float
    source: str
    mode: str
    adapters: dict[str, dict[str, Any]] = field(default_factory=dict)


class TrafficMonitor:
    """流量采样与统计。"""

    def __init__(
        self,
        script_path: str,
        data_dir: str,
        interval_ms: int = 5000,
        logger: Callable[[str, str], None] | None = None,
    ) -> None:
        self.script_path = script_path
        self.data_dir = data_dir
        self.interval_ms = interval_ms
        self.log = logger or (lambda level, msg: None)

        self.history_path = os.path.join(data_dir, 'history.json')
        self.counters_path = os.path.join(data_dir, 'counters.json')

        self._history = {}
        self._ring = deque(maxlen=RING_SIZE)
        self._last = None
        self._last_sample = None
        self._source = ''
        self._errors = 0
        self._session = {'rx': 0, 'tx': 0, 'started_at': time.time()}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._persist_dirty = False
        self._last_persist = 0.0

        # 记录闸门：返回 False 时本次差值只用于实时速率，不写入历史/会话/配额。
        # 由 CampusService 按「是否在校园网」设置（见 traffic_only_campus）。
        self.record_gate: Callable[[], bool] | None = None

    def _record_allowed(self) -> bool:
        gate = self.record_gate
        if gate is None:
            return True
        try:
            return bool(gate())
        except Exception:
            return True

    def start(self) -> None:
        self.load()
        self.sample()
        self._thread = threading.Thread(target=self._loop, name='traffic', daemon=True)
        self._thread.start()
        self.log('info', f'流量统计已启动（采样间隔 {self.interval_ms / 1000:.0f}s，数据源 {self._source or "未知"}）')

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self.flush()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception as exc:
                self.log('warn', f'流量采样异常：{exc}')
            if self._persist_dirty and time.time() - self._last_persist > 15:
                self.flush()
            self._stop.wait(max(0.5, self.interval_ms / 1000.0))

    def load(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        try:
            with open(self.history_path, 'r', encoding='utf-8') as fp:
                raw = json.load(fp)
            for key, value in (raw.get('hours') or {}).items():
                self._history[key] = {'rx': int(value.get('rx', 0)), 'tx': int(value.get('tx', 0))}
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        try:
            with open(self.counters_path, 'r', encoding='utf-8') as fp:
                state = json.load(fp)
            self._last = state.get('last') or None
            session = state.get('session') or {}
            if session:
                self._session = {
                    'rx': int(session.get('rx', 0)),
                    'tx': int(session.get('tx', 0)),
                    'started_at': float(session.get('started_at', time.time())),
                }
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        self._compact()

    def flush(self) -> None:
        if not self.data_dir:
            return
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with self._lock:
                self._compact()
                payload = {
                    'version': 1,
                    'updated_at': time.time(),
                    'hours': {k: self._history[k] for k in sorted(self._history)},
                }
                state = {
                    'updated_at': time.time(),
                    'last': self._last,
                    'session': self._session,
                }
            self._atomic_write(self.history_path, payload)
            self._atomic_write(self.counters_path, state)
            self._persist_dirty = False
            self._last_persist = time.time()
        except OSError as exc:
            self.log('warn', f'流量数据落盘失败：{exc}')

    @staticmethod
    def _atomic_write(path: str, payload: dict) -> None:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fp:
            json.dump(payload, fp, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)

    def _compact(self) -> None:
        if len(self._history) <= MAX_HISTORY_HOURS:
            return
        for key in sorted(self._history)[:len(self._history) - MAX_HISTORY_HOURS]:
            self._history.pop(key, None)

    def sample(self) -> Timeline | None:
        """读取一次计数器并累计差值。"""
        result = run_script_file(self.script_path, timeout=25, json_output=True)
        data = None
        text = (result.stdout or '').strip().lstrip('\ufeff')
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:

                try:
                    parsed = json.loads(f'[{text}]')
                    data = parsed[0] if parsed else None
                except json.JSONDecodeError:
                    data = None
        if data is None and self._errors < 3:
            self.log('debug', f'traffic.ps1 原始输出：{(result.stdout or "(空)")[:200]!r}')

        if not data or not data.get('ok'):
            self._errors += 1
            if self._errors <= 3:
                self.log('warn', f'读取流量计数器失败（第 {self._errors} 次）')
            return None
        self._errors = 0
        self._source = str(data.get('source') or '')

        now = time.time()
        raw_adapters = data.get('adapters') or []
        use_system = not raw_adapters

        if use_system:
            system = data.get('system') or {}
            totals = {'rx': int(system.get('rx') or 0), 'tx': int(system.get('tx') or 0)}
        else:
            totals = {'rx': 0, 'tx': 0}
            for item in raw_adapters:
                totals['rx'] += int(item.get('rx') or 0)
                totals['tx'] += int(item.get('tx') or 0)

        delta_rx = delta_tx = 0
        delta_ms = 0.0
        adapter_rates = {}

        if self._last:
            prev = self._last.get('system') if use_system else self._last.get('totals')
            if prev:
                drx = totals['rx'] - int(prev.get('rx') or 0)
                dtx = totals['tx'] - int(prev.get('tx') or 0)

                delta_rx = drx if drx >= 0 else totals['rx']
                delta_tx = dtx if dtx >= 0 else totals['tx']
            delta_ms = max(1.0, (now - float(self._last.get('at') or now)) * 1000)

            if not use_system:
                prev_map = {a.get('name'): a for a in (self._last.get('adapters') or [])}
                for item in raw_adapters:
                    previous = prev_map.get(item.get('name'))
                    if not previous:
                        continue
                    drx = int(item.get('rx') or 0) - int(previous.get('rx') or 0)
                    dtx = int(item.get('tx') or 0) - int(previous.get('tx') or 0)
                    if drx < 0 or dtx < 0:
                        continue
                    seconds = max(1.0, now - float(self._last.get('at') or now))
                    adapter_rates[str(item.get('name'))] = {
                        'name': item.get('name'),
                        'desc': item.get('desc', ''),
                        'status': item.get('status', ''),
                        'rx_rate': drx / seconds,
                        'tx_rate': dtx / seconds,
                    }

        self._last = {
            'at': now,
            'totals': totals,
            'system': totals if use_system else None,
            'adapters': None if use_system else raw_adapters,
            'mode': 'system' if use_system else 'adapters',
        }

        if delta_rx > 0 or delta_tx > 0:
            if self._record_allowed():
                self._add_to_history(now, delta_rx, delta_tx)
                self._session['rx'] += delta_rx
                self._session['tx'] += delta_tx
                self._persist_dirty = True

        timeline = Timeline(
            at=now,
            rx=totals['rx'],
            tx=totals['tx'],
            delta_rx=delta_rx,
            delta_tx=delta_tx,
            rx_rate=delta_rx * 1000.0 / delta_ms if delta_ms else 0.0,
            tx_rate=delta_tx * 1000.0 / delta_ms if delta_ms else 0.0,
            source=self._source,
            mode='system' if use_system else 'adapters',
            adapters=adapter_rates,
        )
        with self._lock:
            self._ring.append(timeline)
        self._last_sample = timeline
        return timeline

    def _add_to_history(self, ts: float, rx: int, tx: int) -> None:
        key = hour_key(ts)
        with self._lock:
            bucket = self._history.setdefault(key, {'rx': 0, 'tx': 0})
            bucket['rx'] += rx
            bucket['tx'] += tx

    @property
    def healthy(self) -> bool:
        return self._errors == 0

    @property
    def error_count(self) -> int:
        return self._errors

    def ring(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._ring)
        return [
            {
                'at': t.at,
                'rx_rate': t.rx_rate,
                'tx_rate': t.tx_rate,
                'delta_rx': t.delta_rx,
                'delta_tx': t.delta_tx,
            }
            for t in items
        ]

    def summarize(self, since_ts: float) -> dict[str, int]:
        rx = tx = 0
        with self._lock:
            for key, value in self._history.items():
                ts = key_to_ts(key)
                if ts is None or ts < since_ts:
                    continue
                rx += value['rx']
                tx += value['tx']
        return {'rx': rx, 'tx': tx, 'total': rx + tx}

    def series(self, since_ts: float | None = None, max_points: int = 500) -> list[dict[str, Any]]:
        out = []
        with self._lock:
            keys = sorted(self._history)
            for key in keys:
                ts = key_to_ts(key)
                if ts is None or (since_ts is not None and ts < since_ts):
                    continue
                value = self._history[key]
                out.append({
                    'ts': ts,
                    'key': key,
                    'rx': value['rx'],
                    'tx': value['tx'],
                    'total': value['rx'] + value['tx'],
                })
        return out[-max_points:]

    def range_summary(self, start_ts: float, end_ts: float) -> dict[str, int]:
        """统计 ``[start_ts, end_ts)`` 内的用量（小时桶相加）。"""
        rx = tx = 0
        with self._lock:
            for key, value in self._history.items():
                ts = key_to_ts(key)
                if ts is None or ts < start_ts or ts >= end_ts:
                    continue
                rx += value['rx']
                tx += value['tx']
        return {'rx': rx, 'tx': tx, 'total': rx + tx}

    def range_series(
        self,
        start_ts: float,
        end_ts: float,
        max_points: int = 240,
    ) -> list[dict[str, Any]]:
        """取 ``[start_ts, end_ts)`` 的曲线数据，点数超过 ``max_points`` 时按桶合并。

        时间范围可以跨任意天数（「近 7 天」「上一个 7 天」「自定义」都走这里）。
        """
        items: list[dict[str, Any]] = []
        with self._lock:
            for key in sorted(self._history):
                ts = key_to_ts(key)
                if ts is None or ts < start_ts or ts >= end_ts:
                    continue
                value = self._history[key]
                items.append({
                    'ts': ts,
                    'key': key,
                    'rx': value['rx'],
                    'tx': value['tx'],
                    'total': value['rx'] + value['tx'],
                })

        if len(items) <= max_points:
            return items

        span = max(1.0, end_ts - start_ts)
        bucket_sec = span / max_points
        buckets: dict[int, dict[str, Any]] = {}
        for item in items:
            index = int((item['ts'] - start_ts) // bucket_sec)
            bucket = buckets.setdefault(index, {
                'ts': start_ts + index * bucket_sec,
                'key': item['key'],
                'rx': 0,
                'tx': 0,
                'total': 0,
            })
            bucket['rx'] += item['rx']
            bucket['tx'] += item['tx']
            bucket['total'] += item['total']
        return [buckets[index] for index in sorted(buckets)]

    def daily_totals(self, days: int = 84) -> list[dict[str, Any]]:
        """最近 ``days`` 天的每日用量（升序，最后一天是今天，缺失日期补 0）。

        热力图用：按小时桶 key 的前 10 位归日，先在锁内拷贝快照再计算，
        避免统计期间长时间占用采样锁。
        """
        span = max(1, min(366, int(days or 1)))
        today = dt.date.today()
        start = today - dt.timedelta(days=span - 1)
        with self._lock:
            snapshot = [(key, dict(value)) for key, value in self._history.items()]

        daily: dict[str, dict[str, int]] = {}
        for key, value in snapshot:
            if key_to_ts(key) is None:
                continue
            day = str(key)[:10]
            bucket = daily.setdefault(day, {'rx': 0, 'tx': 0})
            bucket['rx'] += int(value.get('rx', 0))
            bucket['tx'] += int(value.get('tx', 0))

        out: list[dict[str, Any]] = []
        for offset in range(span):
            day = (start + dt.timedelta(days=offset)).isoformat()
            bucket = daily.get(day) or {'rx': 0, 'tx': 0}
            rx = int(bucket['rx'])
            tx = int(bucket['tx'])
            out.append({'date': day, 'rx': rx, 'tx': tx, 'total': rx + tx})
        return out

    def summary(self) -> dict[str, Any]:
        now = dt.datetime.now()
        start_today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        start_yesterday = start_today - 86400
        start_week = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        start_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()

        today = self.summarize(start_today)
        yesterday_all = self.summarize(start_yesterday)
        yesterday = {
            'rx': max(0, yesterday_all['rx'] - today['rx']),
            'tx': max(0, yesterday_all['tx'] - today['tx']),
            'total': max(0, yesterday_all['total'] - today['total']),
        }
        last = self._last_sample
        with self._lock:
            hours_recorded = len(self._history)
            history_start = key_to_ts(sorted(self._history)[0]) if self._history else None

        return {
            'now': time.time(),
            'source': self._source,
            'mode': last.mode if last else 'unknown',
            'healthy': self.healthy,
            'error_count': self._errors,
            'retry_hint': (
                '读取网卡计数器失败：请尝试以管理员身份运行，或检查系统是否禁用了 Get-NetAdapterStatistics / netstat'
                if self._errors
                else ''
            ),
            'live': {
                'rx_rate': last.rx_rate if last else 0.0,
                'tx_rate': last.tx_rate if last else 0.0,
                'total_rate': (last.rx_rate + last.tx_rate) if last else 0.0,
                'adapters': last.adapters if last else {},
                'at': last.at if last else 0.0,
                'counter_rx': last.rx if last else 0,
                'counter_tx': last.tx if last else 0,
            },
            'today': today,
            'yesterday': yesterday,
            'week': self.summarize(start_week),
            'month': self.summarize(start_month),
            'year': self.summarize(start_year),
            'all': self.summarize(0),
            'session': {
                'rx': self._session['rx'],
                'tx': self._session['tx'],
                'total': self._session['rx'] + self._session['tx'],
                'started_at': self._session['started_at'],
            },
            'last_hour': self.summarize(time.time() - 3600),
            'history_start': history_start,
            'hours_recorded': hours_recorded,
        }

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._ring.clear()
            self._session = {'rx': 0, 'tx': 0, 'started_at': time.time()}
        self.flush()
        self.log('info', '流量统计已清零')

    def export_csv(self, path: str) -> str:
        """导出按小时的用量到 CSV（带 BOM，Excel 打开不乱码）。

        如果目标路径不可写（例如父目录权限受限），自动退回到数据目录。
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['hour', 'download_bytes', 'upload_bytes', 'download_MB', 'upload_MB'])
        with self._lock:
            for key in sorted(self._history):
                value = self._history[key]
                writer.writerow([
                    key,
                    value['rx'],
                    value['tx'],
                    f'{value["rx"] / 1048576:.2f}',
                    f'{value["tx"] / 1048576:.2f}',
                ])

        text = buffer.getvalue()
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as fp:
                fp.write(text)
            return path
        except OSError:
            fallback = os.path.join(self.data_dir, os.path.basename(path) or 'traffic.csv')
            os.makedirs(self.data_dir, exist_ok=True)
            with open(fallback, 'w', encoding='utf-8-sig', newline='') as fp:
                fp.write(text)
            return fallback
