"""连通性探测（Captive Portal Detection, CPD）。

思路与 Windows/Android 的 NCSI 一致：

1. 请求一个「必须返回固定内容」的探测页；
2. 返回重定向 / 非预期内容 → 被 Portal 劫持，抓取 Portal 页面；
3. 连接失败 → 无网络。

状态取值：``online`` / ``portal`` / ``no-internet`` / ``no-network``
"""

from __future__ import annotations

import re
import socket
import time
import urllib.parse
from typing import Any

from .http import get


PROBES: list[dict[str, str | int]] = [
    {
        "id": "miui",
        "url": "http://connect.rom.miui.com/generate_204",
        "expect_status": 204,
        "expect_body": "",
        "note": "小米连通性检测（国内最稳）",
    },
    {
        "id": "gstatic",
        "url": "http://www.gstatic.com/generate_204",
        "expect_status": 204,
        "expect_body": "",
        "note": "Google 204",
    },
    {
        "id": "microsoft",
        "url": "http://www.msftconnecttest.com/connecttest.txt",
        "expect_status": 200,
        "expect_body": "Microsoft Connect Test",
        "note": "Windows NCSI",
    },
    {
        "id": "apple",
        "url": "http://captive.apple.com/hotspot-detect.html",
        "expect_status": 200,
        "expect_body": "Success",
        "note": "Apple 热点检测",
    },
]

_PORTAL_HINT_RE = re.compile("<html|<form|portal|login", re.I)


def absolutize(url: str, base: str) -> str:
    """把可能相对的跳转地址补全。"""
    try:
        return urllib.parse.urljoin(base or "http://localhost/", url)
    except ValueError:
        return url


def has_local_address() -> bool:
    """本机是否存在非回环 IPv4 地址（即是否有可用网络）。"""
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addr = info[4][0]
            if addr and not addr.startswith("127."):
                return True
    except OSError:
        pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        sock.connect(("223.5.5.5", 53))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def tcp_ping(host: str, port: int = 53, timeout: float = 2.5) -> dict[str, Any]:
    """TCP 连接探测。"""
    started = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return {
            "ok": True, "host": host, "port": port,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
    except OSError as exc:
        return {
            "ok": False, "host": host, "port": port, "error": str(exc),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        sock.close()


def dns_probe(name: str = "www.baidu.com") -> dict[str, Any]:
    """DNS 解析探测。"""
    started = time.perf_counter()
    try:
        infos = socket.getaddrinfo(name, None, socket.AF_INET)
        addresses = list({info[4][0] for info in infos})
        return {
            "ok": bool(addresses),
            "name": name,
            "addresses": addresses,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
    except OSError as exc:
        return {
            "ok": False,
            "name": name,
            "addresses": [],
            "error": str(exc),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }


def _extract_portal_url(result) -> str:
    """从响应里提取 Portal 地址（Location / meta refresh / JS 跳转）。"""
    if result.location:
        return absolutize(result.location, result.url or "")
    body = result.text or ""
    match = re.search('<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\'>\\s]+)', body, re.I)
    if match:
        return absolutize(match.group(1), result.url or "")
    for pattern in (
        'location\\.(?:replace|href)\\s*=\\s*["\']([^"\']+)["\']',
        'window\\.location\\s*=\\s*["\']([^"\']+)["\']',
        'top\\.location\\s*=\\s*["\']([^"\']+)["\']',
    ):
        match = re.search(pattern, body, re.I)
        if match:
            return absolutize(match.group(1), result.url or "")
    return ""


def _judge(probe: dict, result) -> tuple[str, str]:
    """判断单次探测结果：online / portal / offline。"""
    if not result.ok:
        return ("offline", result.error or "请求失败")
    if result.status != int(probe["expect_status"]):
        return ("portal", f"期望 HTTP {probe['expect_status']}，实际 {result.status}")
    expect_body = str(probe.get("expect_body") or "")
    if expect_body and expect_body not in (result.text or ""):
        return ("portal", "响应内容不是预期内容（疑似被劫持）")
    return ("online", "OK")


def probe(*,
          timeout: float = 6.0,
          custom_probe_url: str = "",
          probe_id: str = "") -> dict[str, Any]:
    """执行一次完整探测。"""
    started = time.perf_counter()
    if not has_local_address():
        return {
            "state": "no-network",
            "label": "未连接网络",
            "online": False,
            "portal_url": "",
            "portal_raw": None,
            "probes": [],
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    targets = []
    if custom_probe_url:
        targets.append({
            "id": "custom",
            "url": custom_probe_url.replace("%T", str(int(time.time() * 1000))),
            "expect_status": 204,
            "expect_body": "",
            "note": "自定义探测地址",
        })
    targets.extend(PROBES)
    if probe_id:
        filtered = [t for t in targets if t["id"] == probe_id]
        if filtered:
            targets = filtered

    results = []
    portal_hit = None
    for probe_def in targets[:5]:
        url = str(probe_def["url"]).replace("%T", str(int(time.time() * 1000)))
        result = get(url, timeout=timeout, allow_redirects=False)
        verdict, reason = _judge(probe_def, result)
        results.append({
            "id": probe_def["id"],
            "url": url,
            "verdict": verdict,
            "reason": reason,
            "status": result.status,
            "elapsed_ms": result.elapsed_ms,
            "location": result.location,
        })
        if verdict == "online":
            return {
                "state": "online",
                "label": "已联网（认证通过）",
                "online": True,
                "portal_url": "",
                "portal_raw": None,
                "probes": results,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "probe_used": probe_def["id"],
            }
        if verdict == "portal" and portal_hit is None:
            portal_hit = (probe_def, result)
            break

    if portal_hit is not None:
        probe_def, result = portal_hit
        portal_url = _extract_portal_url(result)
        page = None
        if portal_url:
            follow = get(portal_url, timeout=timeout, allow_redirects=True, max_body=400000)
            if follow.ok:
                page = {
                    "url": follow.url or portal_url,
                    "status": follow.status,
                    "content_type": follow.content_type,
                    "server": follow.server,
                    "html": (follow.text or "")[:400000],
                }
            else:
                page = {
                    "url": portal_url,
                    "status": 0,
                    "html": "",
                    "error": follow.error,
                }
        if page is None:
            body = result.text or ""
            if re.search("<html|<form|portal|login", body, re.I):
                page = {
                    "url": result.url or str(probe_def["url"]),
                    "status": result.status,
                    "content_type": result.content_type,
                    "server": result.server,
                    "html": body[:400000],
                }
        return {
            "state": "portal",
            "label": "需要 Portal 认证",
            "online": False,
            "portal_url": portal_url or (page or {}).get("url", ""),
            "portal_raw": page,
            "probes": results,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "probe_used": probe_def["id"],
        }

    tcp = tcp_ping("223.5.5.5", 53, 2.5)
    dns = dns_probe()
    no_network = not tcp["ok"] and not dns["ok"]
    return {
        "state": "no-network" if no_network else "no-internet",
        "label": "无法访问外网（链路异常）" if no_network else "外网不可达（可能被限制）",
        "online": False,
        "portal_url": "",
        "portal_raw": None,
        "probes": results,
        "tcp": tcp,
        "dns": dns,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
