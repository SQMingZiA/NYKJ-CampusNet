"""系统信息：网卡、IP、网关、DNS、系统代理、公网 IP、运行权限。"""

from __future__ import annotations

import json
import os
import platform
import re
import socket
import time
from typing import Any

from .winutil import is_admin, run_cmd, run_powershell

_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def os_info() -> dict[str, Any]:
    """操作系统与主机信息。"""
    return {
        "platform": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
        "cpus": os.cpu_count() or 0,
        "user": os.environ.get("USERNAME", ""),
    }


def is_elevated() -> bool:
    return is_admin()


def local_interfaces() -> list[dict[str, Any]]:
    """本机网卡（用 PowerShell 取，信息比 socket 全）。"""
    script = (
        "Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' } | "
        "ForEach-Object { [pscustomobject]@{  name=$_.InterfaceAlias; desc=$_.InterfaceDescription;  "
        "ipv4=@($_.IPv4Address.IPAddress); gateway=@($_.IPv4DefaultGateway.NextHop);  "
        "dns=@($_.DNSServer | Where-Object {$_.AddressFamily -eq 2} | ForEach-Object {$_.ServerAddresses}) } }"
    )
    result = run_powershell(script, timeout=20)
    if result.ok and result.stdout.strip():
        try:
            data = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            data = None
        if data:
            items = data if isinstance(data, list) else [data]
            return [
                {
                    "name": str(item.get("name") or ""),
                    "desc": str(item.get("desc") or ""),
                    "ipv4": [str(x) for x in (item.get("ipv4") or []) if x],
                    "gateway": [str(x) for x in (item.get("gateway") or []) if x],
                    "dns": [str(x) for x in (item.get("dns") or []) if x],
                }
                for item in items
            ]
    result = run_cmd("chcp 65001>nul & netsh interface ip show config", timeout=15)
    interfaces = []
    current = None
    for line in (result.stdout or "").splitlines():
        if re.search("配置|Configuration", line) and ":" in line:
            if current:
                interfaces.append(current)
            current = {
                "name": line.split('"')[1] if '"' in line else line.split(":", 1)[-1].strip(),
                "desc": "",
                "ipv4": [],
                "gateway": [],
                "dns": [],
            }
        elif current is not None:
            if re.search("IP 地址|IP Address", line):
                current["ipv4"] += _IPV4_RE.findall(line)
            elif re.search("默认网关|Default Gateway", line):
                current["gateway"] += _IPV4_RE.findall(line)
            elif re.search("DNS 服务器|DNS servers", line):
                current["dns"] += _IPV4_RE.findall(line)
    if current:
        interfaces.append(current)
    return [i for i in interfaces if i["ipv4"]]


def gateway_info() -> dict[str, Any]:
    """默认网关与 DNS。"""
    gateways = []
    dns = []
    result = run_cmd("chcp 65001>nul & ipconfig /all", timeout=15)
    text = result.stdout or ""
    for match in re.finditer(r"(?:Default Gateway|默认网关)[^\d]*((?:\d{1,3}(?:\.\d{1,3}){3}[\s,]*)+)", text):
        gateways += _IPV4_RE.findall(match.group(1))
    for match in re.finditer(r"(?:DNS Servers|DNS 服务器)[^\d]*((?:\d{1,3}(?:\.\d{1,3}){3}[\s,]*)+)", text):
        dns += _IPV4_RE.findall(match.group(1))

    unique_gateways = list(dict.fromkeys(gateways))
    return {
        "gateway": unique_gateways[0] if unique_gateways else "",
        "gateways": unique_gateways,
        "dns": list(dict.fromkeys(dns))[:6],
    }


def system_proxy() -> dict[str, Any]:
    """系统代理设置。"""
    script = (
        r"$p = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'; "
        r"[pscustomobject]@{ enabled=[bool]($p.ProxyEnable -eq 1); server=[string]$p.ProxyServer; pac=[string]$p.AutoConfigURL }"
    )
    result = run_powershell(script + " | ConvertTo-Json -Compress", timeout=15)
    try:
        data = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        data = {}
    return {
        "enabled": bool(data.get("enabled")),
        "server": str(data.get("server") or ""),
        "pac": str(data.get("pac") or ""),
    }


def public_ip(timeout: float = 6.0) -> dict[str, Any]:
    """公网出口 IP（用于识别校园网出口）。"""
    from ..net.http import get

    for url in ("https://api.ipify.org?format=json", "https://myip.ipip.net", "http://ip.3322.net"):
        result = get(url, timeout=timeout, allow_redirects=True)
        if not result.ok:
            continue
        match = _IPV4_RE.search(result.text or "")
        if not match:
            continue
        return {
            "ok": True,
            "ip": match.group(1),
            "raw": (result.text or "").strip()[:200],
            "source": url,
        }
    return {"ok": False, "ip": "", "raw": ""}


def system_summary(ps_wifi_script: str | None = None) -> dict[str, Any]:
    """一次性汇总系统信息（供设置页/诊断页展示）。"""
    from ..net import wifi

    started = time.time()
    return {
        "os": os_info(),
        "interfaces": local_interfaces(),
        "gateway": gateway_info(),
        "proxy": system_proxy(),
        "elevated": is_elevated(),
        "wifi": wifi.status(ps_wifi_script),
        "elapsed_ms": int((time.time() - started) * 1000),
    }
