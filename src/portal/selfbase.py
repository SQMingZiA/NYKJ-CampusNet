"""蓝海卓越 / 派网门户的「自助接口」封装。

这些接口不是认证用的，而是查账号状态的（实测于本校门户 ``10.100.2.2:9090``）：

===============================  ==========================================
``POST /api/selfbase/checkUserOnline2``
    返回当前会话详情：**学校侧计费流量** ``AcctInputOctets``（上行）/
    ``AcctOutputOctets``（下行）、在线时长、会话开始时间、套餐订单号
    ``OrderId``、剩余流量 ``LeftData``、NAS/AP/VLAN 信息
``POST /api/selfbase/checkUserOnline``
    返回**在线设备列表**（``CallingStationId``/``FramedIPAddress``/上线时间）；
    带 ``info: "用户超过最大在线数"``、``status: -1`` 时说明设备数已达上限
``POST /api/selfbase/downLineUser``
    把指定设备踢下线（见 ``lanhai.down_line_user``）
``POST /api/selfbase/checkUserStatus``
    校验账号密码是否正确（``{"code":0,"msg":"user check right"}``）
===============================  ==========================================

注意：门户前端里**没有**「修改密码」「账单明细」的接口，
那两项在自助系统（``http://10.100.2.2/``，含 ``find_pwd.html``）里，
所以界面上给的是「打开自助系统」的入口，而不是自己造一个改密接口。
"""

from __future__ import annotations

import time
from typing import Any

from ..net.http import post_form

# 剩余流量大于这个值就认为是「不限量」套餐（约 1 PB）
UNLIMITED_THRESHOLD = 1024 ** 5

SELFPAGE = "http://10.100.2.2"
FIND_PASSWORD = "http://10.100.2.2/find_pwd.html"


def _payload(username: str, password: str, mac: str, query: str) -> dict[str, str]:
    return {
        "username": username or "",
        "password": password or "",
        "userpasswd": password or "",
        "mac": mac or "",
        "query": query or "",
    }


def check_online(*, origin: str, query: str, username: str, password: str, mac: str, timeout: float = 10.0) -> dict[str, Any]:
    """查询该账号当前在线的设备。"""
    result = post_form(
        f"{origin.rstrip('/')}/api/selfbase/checkUserOnline",
        _payload(username, password, mac, query),
        timeout=timeout,
    )
    if not result.ok:
        return {"ok": False, "message": f"请求失败：{result.error}", "devices": [], "raw": None}
    data = result.json()
    if data is None:
        return {"ok": False, "message": "返回内容无法解析", "devices": [], "raw": result.text[:300]}

    raw_devices = data.get("data")
    if isinstance(raw_devices, dict):
        raw_devices = [raw_devices]
    devices = []
    now = time.time()
    for item in raw_devices or []:
        if not isinstance(item, dict):
            continue
        mac_addr = str(item.get("CallingStationId") or item.get("mac") or "")
        started_raw = item.get("AcctStartTime") or 0
        try:
            started = float(started_raw)
        except (TypeError, ValueError):
            started = 0.0
        devices.append({
            "mac": mac_addr,
            "ip": str(item.get("FramedIPAddress") or item.get("ip") or ""),
            "started_at": started,
            "started_text": str(item.get("AcctStartTimeStr") or ""),
            "online_for": max(0.0, now - started) if started else 0.0,
            "current": False,
        })

    info = str(data.get("info") or "")
    status = data.get("status")
    return {
        "ok": True,
        "devices": devices,
        "status": status,
        "at_max": status == -1 or "最大在线数" in info,
        "message": info or ("已获取在线设备" if devices else "当前没有在线设备"),
        "raw": data,
    }


def session_info(*, origin: str, query: str, username: str, password: str, mac: str, timeout: float = 10.0) -> dict[str, Any]:
    """查询当前会话详情（学校侧计费流量 / 在线时长 / 套餐信息）。"""
    result = post_form(
        f"{origin.rstrip('/')}/api/selfbase/checkUserOnline2",
        _payload(username, password, mac, query),
        timeout=timeout,
    )
    if not result.ok:
        return {"ok": False, "message": f"请求失败：{result.error}", "session": None, "raw": None}
    data = result.json()
    if data is None:
        return {"ok": False, "message": "返回内容无法解析", "session": None, "raw": result.text[:300]}

    info = data.get("info")
    if not isinstance(info, dict):
        return {
            "ok": False,
            "message": str(data.get("data") or "不在线"),
            "session": None,
            "raw": data,
        }

    def as_int(key: str) -> int:
        try:
            return int(info.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    started = as_int("AcctStartTime")
    left = as_int("LeftData")
    session = {
        "started_at": started,
        "online_for": as_int("AcctSessionTime") or (int(time.time()) - started if started else 0),
        "updated_at": as_int("AcctUpdateTime"),
        "tx_bytes": as_int("AcctInputOctets"),
        "rx_bytes": as_int("AcctOutputOctets"),
        "order_id": str(info.get("OrderId") or ""),
        "left_bytes": left,
        "unlimited": left >= UNLIMITED_THRESHOLD,
        "mac": str(info.get("CallingStationId") or ""),
        "ip": str(info.get("FramedIPAddress") or ""),
        "ap_mac": str(info.get("CalledStationId") or ""),
        "nas_ip": str(info.get("NASIPAddress") or ""),
        "nas_port": str(info.get("NASPortId") or ""),
        "auth_type": str(info.get("AuthType") or ""),
        "session_id": str(info.get("AcctSessionId") or ""),
        "username": str(info.get("UserName") or ""),
    }
    return {"ok": True, "session": session, "message": str(data.get("data") or ""), "raw": data}


def verify_account(*, origin: str, query: str, username: str, password: str, mac: str, timeout: float = 10.0) -> dict[str, Any]:
    """校验账号密码是否正确（不会产生登录动作）。"""
    result = post_form(
        f"{origin.rstrip('/')}/api/selfbase/checkUserStatus",
        _payload(username, password, mac, query),
        timeout=timeout,
    )
    data = result.json() if result.ok else None
    ok = bool(isinstance(data, dict) and data.get("code") == 0)
    return {
        "ok": ok,
        "message": ((data or {}).get("msg") if isinstance(data, dict) else result.error),
        "raw": (data if data is not None else (result.text or "")[:200]),
    }
