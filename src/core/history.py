"""诊断历史：把每次一键网络诊断的结果存成 JSON，供趋势图回看。"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any


def _as_float(value: Any) -> float | None:
    """把值转成 float；缺失或无法转换时返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _as_int(value: Any) -> int | None:
    """把值转成 int；缺失或无法转换时返回 None。"""
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _at_key(entry: dict[str, Any]) -> float:
    """排序用的时间戳，异常值排在最早。"""
    value = _as_float(entry.get("at"))
    return 0.0 if value is None else value


class DiagnosticHistory:
    """最近 N 次诊断结果的本地存储（JSON，原子写）。"""

    FILE_NAME = "diagnostics.json"
    MAX_ENTRIES = 300

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, self.FILE_NAME)
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except (ValueError, OSError):
            self._entries = []
            try:
                os.replace(self.path, self.path + ".bad")
            except OSError:
                pass
            return
        if not isinstance(data, list):
            self._entries = []
            return
        self._entries = [item for item in data if isinstance(item, dict)]

    def _save(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self._entries, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            raw_at = entry.get("at")
            at = _as_float(raw_at) if raw_at else time.time()
            if at is None:
                at = time.time()

            note = entry.get("note", "")
            if note is None:
                note = ""
            else:
                note = str(note)

            item: dict[str, Any] = {
                "at": at,
                "latency_ms": _as_float(entry.get("latency_ms")),
                "jitter_ms": _as_float(entry.get("jitter_ms")),
                "dns_ms": _as_float(entry.get("dns_ms")),
                "down_bps": _as_int(entry.get("down_bps")),
                "up_bps": _as_int(entry.get("up_bps")),
                "ok": bool(entry.get("ok")),
                "note": note[:200],
            }

            self._entries.append(item)
            self._entries.sort(key=_at_key)
            if len(self._entries) > self.MAX_ENTRIES:
                del self._entries[: -self.MAX_ENTRIES]
            self._save()
            return dict(item)

    def recent(self, limit: int = 120) -> list[dict[str, Any]]:
        with self._lock:
            if limit <= 0:
                return []
            return [dict(item) for item in self._entries[-limit:]]

    def clear(self) -> None:
        with self._lock:
            self._entries = []
            self._save()
