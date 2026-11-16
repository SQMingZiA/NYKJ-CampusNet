"""WiFi 管理：状态、扫描、连接、断开、已保存配置文件。

数据来源：``netsh wlan``（信息最全），受限时用 PowerShell 兜底。
输出可能是中文（GBK）也可能是英文，解析器对两种都做了兼容。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.winutil import run, run_cmd, run_script_file

_FIELD_ALIASES: dict[str, str] = {
    "名称": "name",
    "Name": "name",
    "描述": "description",
    "Description": "description",
    "GUID": "guid",
    "物理地址": "mac",
    "Physical address": "mac",
    "状态": "state",
    "State": "state",
    "SSID": "ssid",
    "BSSID": "bssid",
    "网络类型": "radio_type",
    "Network type": "radio_type",
    "无线电类型": "radio_type",
    "Radio type": "radio_type",
    "身份验证": "authentication",
    "Authentication": "authentication",
    "密码": "cipher",
    "加密": "cipher",
    "Encryption": "cipher",
    "Cipher": "cipher",
    "连接模式": "connection_mode",
    "Connection mode": "connection_mode",
    "信道": "channel",
    "频道": "channel",
    "Channel": "channel",
    "接收速率": "rx_rate",
    "Receive rate": "rx_rate",
    "传输速率": "tx_rate",
    "Transmit rate": "tx_rate",
    "信号": "signal",
    "Signal": "signal",
    "配置文件": "profile",
    "Profile": "profile",
    **{
        "成本": "cost",
        "Cost": "cost",
        "会话持续时间": "session_duration",
        "Session duration": "session_duration",
    },
}

_KV_RE = re.compile(r"^\s{0,6}([^:：]{1,40}?)\s*[:：]\s*(.*)$")
_PERCENT_RE = re.compile(r"(\d+)\s*%")
_RATE_RE = re.compile(r"([\d.]+)\s*(?:Mbps|Mb/s|兆比特/秒)", re.I)
_ELEVATION_RE = re.compile(r"error 5|requires elevation|需要提升|拒绝访问", re.I)




def parse_key_value_lines(text: str) -> dict[str, str]:
    """解析 netsh 的 ``键 : 值`` 行。"""
    out = {}
    for line in (text or "").splitlines():
        match = _KV_RE.match(line.rstrip())
        if not match:
            continue
        key = match.group(1).strip()
        value = match.group(2).strip()
        canonical = _FIELD_ALIASES.get(key)
        if not canonical:
            continue
        if canonical not in out:
            out[canonical] = value
    return out
def _percent(text: str | None) -> int | None:
    match = _PERCENT_RE.search(text or "")
    if match:
        return int(match.group(1))
    return None
def _rate(text: str | None) -> float | None:
    match = _RATE_RE.search(text or "")
    if match:
        return float(match.group(1))
    return None
def _fix_mojibake(text: str) -> str:
    """修「UTF-8 字节被按 GBK 解码」造成的乱码。

    实测坑：中文 Windows 上 netsh 的**本地化标签**（接口名称/信号/频道）是 GBK 字节，
    但 **SSID 本身是 UTF-8 字节**，同一份输出里混了两种编码，
    整体解码时只能选一种，于是中文 SSID 会变成乱码，例如::

        UTF-8 字节 -> 按 GBK 解码:  鐖卞潳瀹剁殑鍖楅蓟

    判别方法很干净：把字符串按 GBK 编回去再按 UTF-8 解一次。
    只有「本来就是 UTF-8 被误解码」的字符串才能成功往返；
    正常的 GBK 中文（例如「接口名称」）编回 GBK 后不是合法 UTF-8，会失败 → 原样返回。
    """
    if not text or text.isascii():
        return text
    try:
        repaired = text.encode("gbk").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        try:
            loose = text.encode("gbk", errors="ignore").decode("utf-8", errors="ignore")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text
        if len(loose) >= max(2, int(len(text) * 0.6)) and any(ord(ch) > 127 for ch in loose):
            return loose
        return text
    if not repaired or "�" in repaired or repaired == text:
        return text
    return repaired if any(ord(ch) > 127 for ch in repaired) else text





def normalize_ssid(ssid: str) -> str:
    """归一化 SSID：忽略大小写、空格、连字符与常见分隔符。"""
    return re.sub(r"[\s\-_·]+", "", (ssid or "").upper())


def ssid_matches(candidate: str, pattern: str) -> bool:
    """判断 SSID 是否匹配（支持 CMCC-NYKJ 与 CMCCNYKJ 互通）。"""
    a, b = normalize_ssid(candidate), normalize_ssid(pattern)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def any_ssid_matches(candidate: str, patterns: list[str]) -> bool:
    return any(ssid_matches(candidate, p) for p in patterns or [])





def status(ps_script: str | None = None) -> dict[str, Any]:
    """当前 WiFi 连接状态。"""
    result = run(["netsh", "wlan", "show", "interfaces"], timeout=12)
    text = result.stdout or ""
    has_ssid = bool(re.search(r"^\s*SSID\s*[:：]", text, re.M))

    if result.code == 0 and has_ssid:
        kv = parse_key_value_lines(text)
        signal_text = kv.get("signal", "")
        return {
            "ok": True,
            "connected": True,
            "source": "netsh",
            "name": kv.get("name", "WLAN"),
            "ssid": _fix_mojibake(kv.get("ssid", "")),
            "bssid": kv.get("bssid", ""),
            "state": kv.get("state", ""),
            "signal": _percent(signal_text),
            "signal_text": signal_text,
            "rx_rate_mbps": _rate(kv.get("rx_rate")),
            "tx_rate_mbps": _rate(kv.get("tx_rate")),
            "channel": int(kv["channel"]) if kv.get("channel", "").isdigit() else None,
            "authentication": kv.get("authentication", ""),
            "cipher": kv.get("cipher", ""),
            "profile": kv.get("profile") or kv.get("ssid", ""),
            "mac": kv.get("mac", ""),
            "radio_type": kv.get("radio_type", ""),
            **{
                "session_duration": kv.get("session_duration", ""),
                "cost": kv.get("cost", ""),
            },
        }

    disconnected = bool(re.search(r"disconnected|已断开|未连接|没有连接", text, re.I))
    needs_elevation = bool(_ELEVATION_RE.search(text))

    if ps_script and os.path.exists(ps_script):
        fb = run_script_file(ps_script, ["status"], timeout=25)
        import json

        try:
            data = json.loads((fb.stdout or "").strip() or "{}")
        except json.JSONDecodeError:
            data = {}
        if data.get("ok") and data.get("ssid"):
            return {
                "ok": True,
                "connected": True,
                "source": f"powershell/{data.get('source', '')}",
                "name": "WLAN",
                "ssid": data["ssid"],
                "bssid": "",
                "state": "已连接",
                "signal": None,
                "signal_text": "",
                "rx_rate_mbps": None,
                "tx_rate_mbps": None,
                "channel": None,
                "authentication": "",
                "cipher": "",
                "profile": data["ssid"],
                "mac": "",
                "radio_type": "",
                **{
                    "session_duration": "",
                    "cost": "",
                },
            }
    return {
        "ok": True,
        "connected": False,
        "source": "netsh" if result.code == 0 else "none",
        "name": "WLAN",
        "ssid": "",
        "state": "已断开" if disconnected else ("未连接" if result.code == 0 else "未知"),
        "needs_elevation": needs_elevation,
        "raw": text[:800] if not has_ssid else "",
    }
def _parse_networks(text: str) -> list[dict[str, Any]]:
    """从 netsh 输出里解析出网络列表。"""
    networks = []
    for block in re.split(r"\r?\n(?=SSID\s+\d+\s*[:：])", text, flags=re.I):
        if not re.search(r"^\s*SSID\s+\d+\s*[:：]", block, re.I | re.M):
            continue
        head = re.split(r"\r?\n(?=BSSID\s+\d+\s*[:：])", block, flags=re.I)[0]
        kv = parse_key_value_lines(head)
        match = re.search(r"SSID\s+\d+\s*[:：]\s*(.*)", head, re.I)
        ssid = _fix_mojibake((match.group(1) if match else kv.get("ssid", "")).strip())
        if not ssid:
            continue
        bssids = re.findall(r"BSSID\s+\d+\s*[:：]\s*([0-9a-fA-F:]{17})", block)
        signals = [int(s) for s in re.findall(r"(?:Signal|信号)\s*[:：]\s*(\d+)\s*%", block)]
        channels = [int(c) for c in re.findall(r"(?:Channel|信道|频道)\s*[:：]\s*(\d+)", block)]
        networks.append({
            "ssid": ssid,
            "authentication": kv.get("authentication", ""),
            "cipher": kv.get("cipher", ""),
            "signal": max(signals) if signals else _percent(kv.get("signal")),
            "channel": channels[0] if channels else None,
            "bssid": bssids[0] if bssids else "",
            "bssid_count": len(bssids),
            "radio_type": kv.get("radio_type", ""),
        })
    return networks







def scan(force: bool = True) -> dict[str, Any]:
    """扫描附近网络，同名 SSID 取信号最强的那个。

    ⚠ 坑一：``netsh wlan show networks`` **没有** ``force=true`` 这个参数。
    以前这里传了它，netsh 会直接报
    ``'force' is not a valid argument for this command`` 并以退出码 1 结束，
    于是**永远扫不到任何网络**（可用网络列表空白、无网时也不会自动连接）。
    想让扫描结果更新，正确做法是先空跑一次触发重新扫描，再读第二次。

    ⚠ 坑二：一次完整扫描要 1~4 秒。这期间 netsh 只会返回**缓存**列表，
    而缓存里有时**只有当前连接的那一个网络** —— 这就是"可用无线网络
    有时候只能查到当前连接的网络"的原因。所以这里连续读 3 次取**并集**，
    等系统把扫描结果补齐；已经不止一个网络时就提前结束，不浪费时间。
    """
    args = ["netsh", "wlan", "show", "networks", "mode=bssid"]
    attempts = 4 if force else 1
    merged = {}
    result = None
    text = ""

    for attempt in range(attempts):
        if attempt:
            time.sleep(2.5)
        result = run(args, timeout=25)
        text = result.stdout or ""
        for item in _parse_networks(text):
            prev = merged.get(item["ssid"])
            if prev is None or (item["signal"] or -1) > (prev["signal"] or -1):
                merged[item["ssid"]] = item
        if len(merged) > 1:
            break

    ordered = sorted(
        merged.values(),
        key=lambda n: n["signal"] if n["signal"] is not None else -1,
        reverse=True,
    )

    return {
        "ok": bool(result and result.code == 0),
        "needs_elevation": bool(_ELEVATION_RE.search(text)),
        "networks": ordered,
        "raw": text[:1200] if not ordered else "",
    }
def saved_profiles() -> dict[str, Any]:
    """已保存的 WiFi 配置文件（这些才能免密直接连接）。"""
    result = run(["netsh", "wlan", "show", "profiles"], timeout=12)
    text = result.stdout or ""

    names = [
        m.strip()
        for m in re.findall(r"(?:All User Profile|所有用户配置文件|用户配置文件)\s*[:：]\s*(.+)", text)
    ]

    probe = run(["netsh", "wlan", "show", "profiles", "name=*", "key=clear"], timeout=15)
    details = []
    for block in re.split(r"\r?\n(?=\s*(?:Profile|配置文件)\s)", probe.stdout or ""):
        kv = parse_key_value_lines(block)
        if kv.get("ssid"):
            details.append(kv["ssid"])

    merged = list(dict.fromkeys([n for n in names + details if n]))
    if not merged:
        ps = (
            "$d = Join-Path $env:ProgramData 'Microsoft\\Wlansvc\\Profiles\\Interfaces';if (Test-Path $d) { Get-ChildItem $d -Recurse -Filter *.xml | ForEach-Object { try { ([xml](Get-Content $_.FullName -Raw)).WLANProfile.name } catch {} } }"
        )
        from ..core.winutil import run_powershell

        out = run_powershell(ps, timeout=20)
        merged = [line.strip() for line in (out.stdout or "").splitlines() if line.strip()]

    return {
        "ok": result.code == 0,
        "profiles": list(dict.fromkeys(merged)),
        "raw": text[:600],
    }



def connect(ssid: str, ps_script: str | None = None) -> dict[str, Any]:
    """连接到指定 SSID（需要已有保存的配置文件）。

    普通权限失败时自动尝试用 UAC 提权重试。
    """
    result = run(["netsh", "wlan", "connect", f"name={ssid}"], timeout=20)
    text = (result.stdout or "") + (result.stderr or "")
    ok = result.code == 0 and not re.search(r"error|错误|失败|fail", text, re.I)

    if not ok and _ELEVATION_RE.search(text):
        from ..core.winutil import run_elevated
        elevated = run_elevated("netsh", ["wlan", "connect", f"name={ssid}"], wait=True)
        if elevated.code == 0:
            return {"ok": True, "ssid": ssid, "message": "已通过管理员权限发起连接", "elevated": True}


    return {
        "ok": ok,
        "ssid": ssid,
        "message": text.strip()[:300] or ("连接指令已下发" if ok else f"netsh 返回 {result.code}"),
        "needs_elevation": bool(_ELEVATION_RE.search(text)),
    }


def disconnect() -> dict[str, Any]:
    """netsh"""
    result = run(["netsh", "wlan", "disconnect"], timeout=15)
    return {"ok": result.code == 0, "message": ((result.stdout or "") + (result.stderr or "")).strip()[:300]}

def connect_and_wait(ssid: str, timeout_ms: int = 20000, ps_script: str | None = None) -> dict[str, Any]:
    """连接并等待真正连上（轮询状态）。"""
    started = time.time()
    first = connect(ssid, ps_script)
    if not first.get("ok"):
        return first
    deadline = started + timeout_ms / 1000.0
    while time.time() < deadline:
        time.sleep(1.0)
        current = status(ps_script)
        if current.get("connected") and ssid_matches(current.get("ssid", ""), ssid):
            return {
                "ok": True,
                "ssid": current.get("ssid", ssid),
                "waited_ms": int((time.time() - started) * 1000),
                "signal": current.get("signal"),
                "message": f"已连接到 {current.get('ssid', ssid)}",
            }

    return {"ok": False, "ssid": ssid, "message": f"连接 {ssid} 超时", "timeout": True}


def open_wifi_panel() -> dict[str, Any]:
    """打开 Windows 可用网络面板（人工兜底）。"""
    result = run_cmd("start ms-availablenetworks:", timeout=8)
    return {"ok": result.code == 0}


def open_network_settings() -> dict[str, Any]:
    """start ms-settings:network-wifi"""
    result = run_cmd("start ms-settings:network-wifi", timeout=8)
    return {"ok": result.code == 0}
