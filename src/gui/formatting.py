"""格式化辅助函数与界面通用工具。"""

from __future__ import annotations

import datetime as dt
from typing import Any

KB = 1024
MB = KB * 1024
GB = MB * 1024
TB = GB * 1024


def format_bytes(value: float | int | None, digits: int = 2) -> str:
    """字节 → 人类可读。"""
    number = float(value or 0)
    absolute = abs(number)
    if absolute < KB:
        return f"{number:.0f} B"
    if absolute < MB:
        return f"{number / KB:.{digits}f} KB"
    if absolute < GB:
        return f"{number / MB:.{digits}f} MB"
    if absolute < TB:
        return f"{number / GB:.{digits}f} GB"
    return f"{number / TB:.{digits}f} TB"


def format_rate(bytes_per_second: float | int | None) -> str:
    """速率（字节/秒）。"""
    number = float(bytes_per_second or 0)
    if number < KB:
        return f"{number:.0f} B/s"
    if number < MB:
        return f"{number / KB:.1f} KB/s"
    return f"{number / MB:.2f} MB/s"


def to_mbps(bytes_per_second: float | int | None) -> float:
    return float(bytes_per_second or 0) * 8 / 1000000.0


def format_duration(seconds: float | int | None) -> str:
    total = max(0, int(seconds or 0))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def format_time(ts: float | None, with_seconds: bool = False) -> str:
    if not ts:
        return '-'
    moment = dt.datetime.fromtimestamp(ts)
    return moment.strftime('%H:%M:%S' if with_seconds else '%H:%M')


def format_datetime(ts: float | None) -> str:
    if not ts:
        return '-'
    return dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


def format_relative(ts: float | None) -> str:
    if not ts:
        return '-'
    diff = datetime_now() - float(ts)
    if diff < 5:
        return '刚刚'
    if diff < 60:
        return f"{int(diff)} 秒前"
    if diff < 3600:
        return f"{int(diff // 60)} 分钟前"
    if diff < 86400:
        return f"{int(diff // 3600)} 小时前"
    return format_datetime(ts)


def datetime_now() -> float:
    import time

    return time.time()


def signal_label(signal: int | None) -> str:
    if signal is None:
        return '未知'
    if signal >= 75:
        return '极佳'
    if signal >= 60:
        return '良好'
    if signal >= 40:
        return '一般'
    if signal >= 20:
        return '较弱'
    return '很差'


def state_meta(state: str) -> dict[str, str]:
    """状态 → 展示用的颜色与文案。"""
    mapping = {
        'online': {'color': '#107c10', 'label': '已联网', 'bg': '#dff6dd'},
        'portal': {'color': '#c47f00', 'label': '需认证', 'bg': '#fff4ce'},
        'authenticating': {'color': '#0f6cbd', 'label': '认证中', 'bg': '#e8f2fb'},
        'connecting-wifi': {'color': '#0f6cbd', 'label': '连接中', 'bg': '#e8f2fb'},
        'checking': {'color': '#0f6cbd', 'label': '检测中', 'bg': '#e8f2fb'},
        'offline': {'color': '#c42b1c', 'label': '未联网', 'bg': '#fde7e9'},
        'error': {'color': '#c42b1c', 'label': '异常', 'bg': '#fde7e9'},
        'no-internet': {'color': '#c42b1c', 'label': '外网不可达', 'bg': '#fde7e9'},
        'no-network': {'color': '#c42b1c', 'label': '无网络', 'bg': '#fde7e9'},
    }
    return mapping.get(state, {'color': '#616161', 'label': '待检测', 'bg': '#f0f0f0'})


def log_color(level: str) -> str:
    return {
        'error': '#c42b1c',
        'warn': '#c47f00',
        'ok': '#107c10',
        'debug': '#8a8886',
    }.get(level, '#323130')


def downsample(series: list[dict[str, Any]], max_points: int = 60) -> list[dict[str, Any]]:
    """把小时序列合并成不超过 max_points 个点。"""
    if not series or len(series) <= max_points:
        return list(series or [])
    bucket = max(1, (len(series) + max_points - 1) // max_points)
    out = []
    for index in range(0, len(series), bucket):
        chunk = series[index:index + bucket]
        rx = sum(item.get('rx', 0) for item in chunk)
        tx = sum(item.get('tx', 0) for item in chunk)
        out.append({'key': chunk[0].get('key', ''), 'ts': chunk[0].get('ts', 0), 'rx': rx, 'tx': tx, 'total': rx + tx})
    return out


def group_by_day(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按天聚合。"""
    buckets = {}
    for item in series or []:
        ts = item.get('ts') or 0
        day = dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
        bucket = buckets.setdefault(day, {'key': day, 'ts': ts, 'rx': 0, 'tx': 0})
        bucket['rx'] += item.get('rx', 0)
        bucket['tx'] += item.get('tx', 0)
        bucket['total'] = bucket['rx'] + bucket['tx']
    return [buckets[k] for k in sorted(buckets)]


def human_quota(gb: float) -> str:
    if gb:
        return f"{gb:g} GB"
    return '未设置'
