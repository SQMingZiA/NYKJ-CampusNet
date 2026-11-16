"""蓝海卓越（Lanhai / 派网 Panabit 网关）Portal 驱动。

实测（南阳科技校园网，NYKJ3 模板）::

    Portal  : http://10.100.2.2:9090/?wlanuserip=…&clientip=…&wlanacname=Panabit
              &clientmac=…&paip=…&vlan=…&iarmdst=…
    登录接口: GET  {origin}/api/selfbase/login3?<portal 全部查询参数>
              &username=…&userpasswd=…&wlanacip=…
    成功判定: JSON {"error_code": 0}（仍需探测网络确认真正通了）
    设备下线: POST {origin}/api/selfbase/downLineUser {username,password,mac,query}
    在线查询: GET  {origin}/api/selfbase/checkMacIsOnline?<query>
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from ..net.http import get, post_form
from .detect import PortalParams

NAME = "蓝海卓越 Portal"

ERROR_CODES: dict[int, str] = {
    0: "认证成功",
    -1: "认证失败",
    -2: "认证被拒绝",
    -101: "用户不存在",
    -102: "密码错误",
    -103: "用户被锁定",
    -104: "用户正在认证",
    -105: "用户正在下线",
    -106: "认证被拒绝",
    -109: "wlanuserip 参数错误",
    -110: "wlanusermac 参数错误",
    -111: "wlanac 参数错误",
    -112: "wlanusername 参数错误",
    -113: "未找到 NAS",
    -114: "device_id 参数出错",
    -115: "radius 拒绝",
    -300: "没有可用订单",
    -1018: "用户名或密码错误",
    -1022: "已达到最大用户在线数（该账号已在其它设备登录）",
    -1210: "用户已停机，请点击『充值续费』购买套餐后进行认证",
    -1211: "用户被暂停",
    -1212: "用户未激活，请点击『充值续费』购买套餐后进行认证",
    -10000: "需要购买套餐",
    -10001: "需要购买套餐",
}


def describe_error(code: Any, fallback: str = "") -> str:
    try:
        key = int(code)
    except (TypeError, ValueError):
        return fallback or f"未知错误（{code}）"
    return ERROR_CODES.get(key, fallback or f"未知错误（{code}）")


def extract_acip_from_html(html: str) -> str:
    """从 Portal 页面里取出正确的 wlanacip。

    本校门户（NYKJ3 模板）在页面里直接声明了 AC 地址，例如::

        <input type="hidden" id="acipType" value="static">
        <input type="hidden" id="staticAcip" value="10.100.0.1">

    实测结论：**必须**用页面声明的这个地址当 ``wlanacip``，
    否则 Portal 会返回 ``-113 没有发现可用的AC/NAS！``
    （用 paip、网关地址、或不带该参数都不行）。
    """
    if not html:
        return ""
    dynamic = re.search(r'id="dynamicAcip"\s+value="([^"]*)"', html)
    if dynamic and dynamic.group(1).strip():
        return dynamic.group(1).strip()
    static = re.search(r'id="staticAcip"\s+value="([^"]*)"', html)
    if static and static.group(1).strip():
        return static.group(1).strip()
    for name in ("staticAcip", "dynamicAcip"):
        match = re.search(f"var\\s+{name}\\s*=\\s*[\"\\']([^\"\\']+)[\"\\']", html)
        if not match: continue
        if not match.group(1).strip(): continue
        return match.group(1).strip()
    return ""


def build_login_url(
    *, origin: str, query_string: str, username: str, password: str, wlanacip: str = ""
) -> str:
    """构造登录 URL：保留 Portal 下发的全部参数，追加账号密码与 AC IP。"""
    pairs = urllib.parse.parse_qsl(query_string or "", keep_blank_values=True)
    filtered = [(k, v) for k, v in pairs if k.lower() not in {"username", "userpasswd", "wlanacip"}]
    filtered.append(("username", username))
    filtered.append(("userpasswd", password))
    if wlanacip:
        filtered.append(("wlanacip", wlanacip))
    query = urllib.parse.urlencode(filtered)
    return f"{origin.rstrip('/')}/api/selfbase/login3?{query}"


def login(
    *,
    portal: PortalParams,
    username: str,
    password: str,
    acip: str = "",
    timeout: float = 12.0,
) -> dict[str, Any]:
    """提交认证。返回 {ok, code, message, raw, login_url, needs_disconnect}。"""
    origin = portal.origin
    wlanacip = acip or portal.wlanacip
    login_url = build_login_url(
        origin=origin,
        query_string=portal.query_string,
        username=username,
        password=password,
        wlanacip=wlanacip,
    )
    get(f"{origin}/?{portal.query_string}", timeout=timeout)

    result = get(login_url, timeout=timeout, headers={"Accept": "application/json, text/plain, */*"})
    if not result.ok:
        return {
            "ok": False,
            "code": "network",
            "message": f"登录请求失败：{result.error}",
            "raw": None,
            "login_url": login_url,
            "needs_disconnect": False,
        }
    data = result.json()
    if data is None:
        return {
            "ok": False,
            "code": "bad-response",
            "message": f"Portal 返回了非 JSON 内容（HTTP {result.status}）：{result.text[:160]}",
            "raw": result.text[:2000],
            "login_url": login_url,
            "needs_disconnect": False,
        }
    code = data.get("error_code", data.get("errorCode", data.get("code")))
    message = data.get("msg", "") or ""
    success = code in (0, "0") or code is None and data.get("code") == 0
    return {
        "ok": bool(success),
        "code": code if code is not None else "unknown",
        "message": "Portal 已接受认证请求" if success else describe_error(code, message),
        "raw_message": message,
        "raw": data,
        "login_url": login_url,
        "needs_disconnect": str(code) == "-1022",
    }


def down_line_user(
    *, portal: PortalParams, username: str, password: str, mac: str, timeout: float = 12.0
) -> dict[str, Any]:
    """把指定设备踢下线（账号在线数已满时使用）。"""
    origin = portal.origin
    payload = {
        "username": username or "",
        "password": password or "",
        "mac": mac or "",
        "query": portal.query_string or "",
    }
    result = post_form(f"{origin}/api/selfbase/downLineUser", payload, timeout=timeout)
    if not result.ok:
        return {"ok": False, "message": result.error, "raw": None}
    data = result.json()
    ok = bool(data) and (data.get("status") == 1 or data.get("code") == 0)
    return {"ok": ok, "message": (data or {}).get("msg") or (data or {}).get("info", ""), "raw": data or result.text[:400]}


def list_online(*, portal: PortalParams, timeout: float = 10.0) -> dict[str, Any]:
    """查询当前账号在线的设备列表。"""
    origin = portal.origin
    result = get(f"{origin}/api/selfbase/checkMacIsOnline?{portal.query_string}", timeout=timeout)
    if not result.ok:
        return {"ok": False, "message": result.error, "devices": []}
    data = result.json()
    if data is None:
        return {"ok": False, "message": "返回内容无法解析", "devices": [], "raw": result.text[:400]}
    devices = []

    def push(item: Any) -> None:
        if not isinstance(item, dict):
            return
        mac = item.get("mac") or item.get("CallingStationId") or item.get("usermac") or item.get("wlanusermac")
        if not mac:
            return
        devices.append(
            {
                "mac": str(mac),
                "ip": str(item.get("ip") or item.get("wlanuserip") or item.get("userip") or ""),
                "username": str(item.get("username") or item.get("wlanusername") or ""),
                "online_time": str(item.get("online_time") or item.get("acctstarttime") or ""),
            }
        )

    if isinstance(data, list):
        for item in data:
            push(item)
    else:
        for key in ("info", "data", "list"):
            if not isinstance(data.get(key), list):
                continue
            for item in data[key]:
                push(item)
            break
        else:
            push(data)
    return {"ok": True, "devices": devices, "status": data.get("status") if isinstance(data, dict) else None, "raw": data}


def down_line(*, portal: PortalParams, username: str, timeout: float = 12.0) -> dict[str, Any]:
    """主动注销当前会话（尽力而为，不同部署支持度不同）。"""
    origin = portal.origin
    result = post_form(
        f"{origin}/api/selfbase/downLine",
        {"username": username or "", "query": portal.query_string},
        timeout=timeout,
    )
    data = result.json() if result.ok else None
    ok = bool(data) and (data.get("status") == 1 or data.get("code") == 0 or data.get("error_code") == 0)
    return {
        "ok": ok,
        "message": (data or {}).get("msg") if isinstance(data, dict) else result.error,
        "raw": data or (result.text or "")[:400],
    }


def fetch_portal_page(portal_url: str, timeout: float = 10.0) -> dict[str, Any]:
    """抓取 Portal 首页，用于指纹识别与调试。"""
    result = get(portal_url, timeout=timeout, allow_redirects=True, max_body=200000)
    title = ""
    match = re.search(r"<title[^>]*>([\s\S]{0,120}?)</title>", result.text or "", re.I)
    if match:
        title = match.group(1).strip()
    return {
        "ok": result.ok,
        "status": result.status,
        "url": result.url or portal_url,
        "title": title,
        "html": (result.text or "")[:200000],
        "error": result.error,
    }
