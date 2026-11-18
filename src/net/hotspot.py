"""热点共享：把本机的 Wi-Fi 连接转成热点，并管理连上来的设备。

后端优先级：

1. **WinRT（首选）**——`winrt-Windows.Networking.NetworkOperators`（pywinrt 拆分包）
   直接调用系统的 ``NetworkOperatorTetheringManager``。它能拿到真实能力、
   当前配置、已连接设备（MAC + 主机名）以及每次启停的操作结果，而且不需要
   弹 UAC 之外的东西。
2. **PowerShell + WinRT**——没装 pywinrt 时的退路：内置一段 PowerShell 脚本调同样的
   WinRT 接口，靠解析 JSON 取结果。
3. **netsh 承载网络**——老系统上的兜底（Win10 之前的方式，需要驱动支持）。

判断「本机支不支持」只看 ``GetTetheringCapabilityFromConnectionProfile``：返回
``ENABLED`` 才算支持。驱动信息里的「支持的承载网络」是早就废弃的 hosted network
字段，很多能开热点的网卡也显示「否」，不能用来判断。

设备「断开」不是踢掉 Wi-Fi 关联（Windows 不提供这个接口），而是给该 IP 加一条
入站 + 出站的防火墙拦截规则；设备换 IP 后会恢复，属于临时措施。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import tempfile
import time
from typing import Any

from ..core.winutil import run, run_cmd

# ---- 常量 -------------------------------------------------------------------

FIREWALL_RULE = "CampusNet Hotspot Block {ip}"
DEFAULT_SSID = "CampusNet-{suffix}"
MIN_PASSWORD = 8
MAX_SSID = 32
MAX_PASSWORD = 63
ICS_PREFIX = "192.168.137."

# 随机密码用的字符集：去掉容易看混的 0/O/1/l/I。
PASSWORD_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_CAPABILITY_TEXT = {
    "DISABLED_BY_GROUP_POLICY": "组策略禁用了移动热点",
    "DISABLED_BY_HARDWARE_LIMITATION": "网卡驱动没有提供移动热点能力",
    "DISABLED_BY_OPERATOR": "运营商或网络策略禁用了移动热点",
    "DISABLED_BY_SKU": "当前 Windows 版本不包含移动热点功能",
    "DISABLED_BY_REQUIRED_APP_NOT_INSTALLED": "系统缺少移动热点所需的应用",
    "DISABLED_DUE_TO_UNKNOWN_CAUSE": "系统没有说明原因地禁用了移动热点",
    "DISABLED_BY_SYSTEM_CAPABILITY": "这台设备的系统能力不支持移动热点",
}

# 常见厂商 OUI，只用于把设备列表写得好看一点；查不到就用 MAC 前三段。
_OUI = {
    "F4:3B:D8": "Intel",
    "64:9A:BE": "Apple",
    "A4:B1:C1": "Apple",
    "D0:81:7A": "Apple",
    "F0:18:98": "Apple",
    "8C:85:90": "Apple",
    "3C:22:FB": "Apple",
    "BC:92:6B": "Apple",
    "A0:8C:FD": "Apple",
    "5C:AA:FD": "Samsung",
    "B4:EF:FA": "Samsung",
    "D8:9E:3F": "Samsung",
    "F8:E6:1A": "Samsung",
    "C8:3A:6B": "Samsung",
    "7C:1E:52": "Xiaomi",
    "64:CC:2E": "Xiaomi",
    "34:CE:00": "Xiaomi",
    "50:8F:4C": "Xiaomi",
    "00:9E:C8": "Xiaomi",
    "10:2E:AF": "Honor",
    "F4:9F:54": "Huawei",
    "00:E0:FC": "Huawei",
    "48:DB:50": "Huawei",
    "3C:CD:5D": "Huawei",
    "8C:79:F5": "OPPO",
    "64:A2:F9": "OPPO",
    "E8:BB:A5": "vivo",
    "3C:BD:3E": "vivo",
    "58:44:98": "Xiaomi",
    "20:32:33": "Lenovo",
    "54:EE:75": "Wistron",
    "00:1A:7D": "Cyber-Tan",
    "B0:BE:76": "TP-Link",
}

# ---- WinRT（pywinrt）--------------------------------------------------------

try:  # pragma: no cover - 取决于本机是否装了 pywinrt
    import winrt.windows.networking.connectivity as _conn
    import winrt.windows.networking.networkoperators as _nop

    HAS_WINRT = True
except Exception:  # noqa: BLE001 - 没装就退回 PowerShell 方案
    _conn = None  # type: ignore[assignment]
    _nop = None  # type: ignore[assignment]
    HAS_WINRT = False


def _enum_name(value: Any) -> str:
    return str(getattr(value, "name", value) or "")


def _run_coro(factory: Any, timeout: float = 30.0) -> Any:
    """在独立事件循环里跑一个协程（pywinrt 的异步操作是 awaitable）。"""

    async def _main() -> Any:
        return await asyncio.wait_for(factory(), timeout=timeout)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_main())
    finally:
        loop.close()


def _profile() -> Any:
    """优先用正在上网的配置，没有就随便挑一个 Wi-Fi 配置。"""
    profile = _conn.NetworkInformation.get_internet_connection_profile()
    if profile is not None:
        return profile
    try:
        profiles = _conn.NetworkInformation.get_connection_profiles()
        for item in profiles:
            if item.is_wlan_connection_profile:
                return item
        for item in profiles:
            return item
    except Exception:  # noqa: BLE001 - 拿不到就让调用方处理
        pass
    return None


def _manager() -> Any:
    profile = _profile()
    if profile is None:
        raise RuntimeError("当前没有可用的网络连接配置")
    return _nop.NetworkOperatorTetheringManager.create_from_connection_profile(profile)


def _capability_text(capability: Any) -> str:
    name = _enum_name(capability)
    if name == "ENABLED" or name == "Enabled":
        return ""
    return _CAPABILITY_TEXT.get(name.upper(), f"系统报告的热点能力是 {name or '未知'}")


# ---- PowerShell 退路（没装 pywinrt）----------------------------------------

_PS_SCRIPT = r"""
param([string]$Command = 'status', [string]$Ssid = '', [string]$Passphrase = '')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]
$null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]

$conn = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]::GetInternetConnectionProfile()
if ($null -eq $conn) {
    @{ ok = $false; error = '当前没有可用的网络连接'; capability = 'Unknown'; state = 'Unknown'; running = $false } | ConvertTo-Json -Compress
    exit 0
}

$capability = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]::GetTetheringCapabilityFromConnectionProfile($conn)
$manager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]::CreateFromConnectionProfile($conn)

function Get-State($m) {
    return [string]$m.TetheringOperationalState
}

function Wait-State($m, $target, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if ((Get-State $m) -eq $target) { return $true }
        Start-Sleep -Milliseconds 400
    }
    return ((Get-State $m) -eq $target)
}

$errorText = ''
$state = Get-State $manager
switch ($Command) {
    'start' {
        if ($state -ne 'On') {
            try {
                $cfg = $manager.GetCurrentAccessPointConfiguration()
                $changed = $false
                if ($Ssid -and $Ssid -ne [string]$cfg.Ssid) { $cfg.Ssid = $Ssid; $changed = $true }
                if ($Passphrase -and $Passphrase -ne [string]$cfg.Passphrase) { $cfg.Passphrase = $Passphrase; $changed = $true }
                if ($changed) { $opConfig = $manager.ConfigureAccessPointAsync($cfg); Start-Sleep -Milliseconds 800 }
                $opStart = $manager.StartTetheringAsync()
                $null = Wait-State $manager 'On' 20
            } catch {
                $errorText = $_.Exception.Message
            }
            $state = Get-State $manager
        }
    }
    'stop' {
        if ($state -eq 'On') {
            try {
                $opStop = $manager.StopTetheringAsync()
                $null = Wait-State $manager 'Off' 20
            } catch {
                $errorText = $_.Exception.Message
            }
            $state = Get-State $manager
        }
    }
    'configure' {
        try {
            $cfg = $manager.GetCurrentAccessPointConfiguration()
            if ($Ssid) { $cfg.Ssid = $Ssid }
            if ($Passphrase) { $cfg.Passphrase = $Passphrase }
            $opConfig = $manager.ConfigureAccessPointAsync($cfg)
            Start-Sleep -Milliseconds 1200
        } catch {
            $errorText = $_.Exception.Message
        }
    }
}

$cfg = $null
try { $cfg = $manager.GetCurrentAccessPointConfiguration() } catch { }

$clients = @()
if ([string]$capability -eq 'Enabled') {
    try {
        $list = $manager.GetTetheringClients()
        for ($i = 0; $i -lt $list.Count; $i++) {
            $item = $list.GetAt($i)
            $name = ''
            try {
                if ($item.HostNames.Count -gt 0) { $name = [string]$item.HostNames[0].DisplayName }
            } catch { }
            $clients += @{ mac = [string]$item.MacAddress; name = $name }
        }
    } catch { }
}

$result = @{
    ok = ($errorText -eq '')
    error = $errorText
    capability = [string]$capability
    state = (Get-State $manager)
    running = ((Get-State $manager) -eq 'On')
    client_count = [int]$manager.ClientCount
    max_clients = [int]$manager.MaxClientCount
    clients = $clients
    ssid = if ($cfg) { [string]$cfg.Ssid } else { '' }
    passphrase = if ($cfg) { [string]$cfg.Passphrase } else { '' }
    band = if ($cfg) { [string]$cfg.Band } else { '' }
}
$result | ConvertTo-Json -Compress -Depth 4
"""


def _script_path() -> str:
    """把 PowerShell 退路脚本写到临时目录（内容变化才重写）。"""
    digest = hashlib.sha1(_PS_SCRIPT.encode("utf-8")).hexdigest()[:10]
    path = os.path.join(tempfile.gettempdir(), f"campusnet_hotspot_{digest}.ps1")
    if os.path.exists(path):
        return path
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(_PS_SCRIPT)
    return path


def _run_ps(command: str, ssid: str = "", passphrase: str = "", timeout: float = 45.0) -> dict[str, Any]:
    """执行脚本并解析最后一行 JSON。

    用 ``-File`` 直接跑脚本文件：``-Command "& 'x.ps1' -Command stop"`` 这种包装
    在本机实测会让 WinRT 的启停异步操作失效（返回 ok 但状态不变）。
    """
    argv = [
        "powershell", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", _script_path(),
        "-Command", command,
    ]
    if ssid:
        argv += ["-Ssid", ssid]
    if passphrase:
        argv += ["-Passphrase", passphrase]
    try:
        result = run(argv, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - 任何异常都当作不可用
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "capability": "Unknown"}
    text = (result.stdout or "").strip()
    if not text:
        detail = (result.stderr or result.error or "").strip() or "没有输出"
        return {"ok": False, "error": detail, "capability": "Unknown"}
    line = [row for row in text.splitlines() if row.strip().startswith("{")][-1]
    try:
        data = json.loads(line)
    except (ValueError, IndexError):
        return {"ok": False, "error": text[-300:], "capability": "Unknown"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "返回格式不对", "capability": "Unknown"}
    return data


def _adapter_name() -> str:
    result = run(["netsh", "wlan", "show", "interfaces"], timeout=12.0)
    for line in (result.stdout or "").splitlines():
        if "名称" in line or "Name" in line:
            parts = re.split(r"[:\uff1a]", line, maxsplit=1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()
    return ""


def _hosted_supported() -> bool:
    result = run(["netsh", "wlan", "show", "drivers"], timeout=15.0)
    for line in (result.stdout or "").splitlines():
        if ("承载网络" in line or "Hosted network" in line) and ":" in line:
            tail = line.split(":", 1)[1].strip()
            return tail in ("是", "Yes", "yes")
    return False


def _hosted_state() -> dict[str, Any]:
    result = run(["netsh", "wlan", "show", "hostednetwork"], timeout=12.0)
    text = result.stdout or ""
    state = ""
    ssid = ""
    clients = 0
    for line in text.splitlines():
        low = line.replace("\uff1a", ":")
        if "状态" in low or "Status" in low:
            state = low.split(":", 1)[1].strip() if ":" in low else ""
        elif "SSID" in low and "名称" not in low:
            ssid = low.split(":", 1)[1].strip() if ":" in low else ""
        elif "客户端" in low or "number of clients" in low.lower():
            digits = re.findall(r"\d+", low)
            clients = int(digits[0]) if digits else 0
    running = state in ("已启动", "Started", "started")
    return {"running": running, "state": state, "ssid": ssid, "clients": clients}


# ---- 能力探测 ---------------------------------------------------------------


def detect() -> dict[str, Any]:
    """返回 ``{"supported", "backend", "reason", "adapter", "capability"}``。"""
    adapter = _adapter_name()
    if HAS_WINRT:
        try:
            profile = _profile()
            if profile is None:
                raise RuntimeError("没有可用的网络连接")
            capability = _nop.NetworkOperatorTetheringManager.get_tethering_capability_from_connection_profile(profile)
            if _enum_name(capability) in ("ENABLED", "Enabled"):
                return {
                    "supported": True,
                    "backend": "winrt",
                    "reason": "",
                    "adapter": adapter,
                    "capability": "Enabled",
                }
            reason = _capability_text(capability)
        except Exception as exc:  # noqa: BLE001 - 失败就试下一条后端
            reason = f"WinRT 调用失败：{exc}"
    else:
        info = _run_ps("status", timeout=40.0)
        if str(info.get("capability") or "") in ("Enabled", "ENABLED"):
            return {
                "supported": True,
                "backend": "powershell",
                "reason": "",
                "adapter": adapter,
                "capability": "Enabled",
            }
        reason = _capability_text(info.get("capability")) or "这台机器上没有可用的移动热点接口"

    if _hosted_supported():
        return {
            "supported": True,
            "backend": "hosted",
            "reason": "",
            "adapter": adapter,
            "capability": "Hosted",
        }
    return {
        "supported": False,
        "backend": "none",
        "reason": reason or "这台机器不支持热点共享",
        "adapter": adapter,
        "capability": "Disabled",
    }


def is_supported() -> bool:
    return bool(detect().get("supported"))


# ---- 状态 -------------------------------------------------------------------


def _winrt_status() -> dict[str, Any]:
    manager = _manager()
    state = _enum_name(manager.tethering_operational_state)
    running = state in ("ON", "On")
    ssid = ""
    passphrase = ""
    band = ""
    try:
        cfg = manager.get_current_access_point_configuration()
        ssid = str(cfg.ssid or "")
        passphrase = str(cfg.passphrase or "")
        band = _enum_name(cfg.band)
    except Exception:  # noqa: BLE001 - 配置偶尔读不到，不影响状态
        pass
    clients: list[dict[str, Any]] = []
    try:
        for item in manager.get_tethering_clients():
            mac = str(item.mac_address or "").upper().replace("-", ":")
            name = ""
            try:
                names = [str(host.display_name or "") for host in item.host_names]
                name = next((text for text in names if text), "")
            except Exception:  # noqa: BLE001
                name = ""
            clients.append({"mac": mac, "name": name})
    except Exception:  # noqa: BLE001
        pass
    return {
        "supported": True,
        "backend": "winrt",
        "reason": "",
        "running": running,
        "state": state or "UNKNOWN",
        "ssid": ssid,
        "passphrase": passphrase,
        "band": band or "Auto",
        "clients": len(clients),
        "client_list": clients,
        "max_clients": int(manager.max_client_count or 0),
        "detail": "热点已开启" if running else "热点未启动",
    }


def status() -> dict[str, Any]:
    """当前热点状态；不支持时 ``supported=False`` 并给出原因。"""
    info = detect()
    if not info.get("supported"):
        return {
            "supported": False,
            "backend": info.get("backend", "none"),
            "reason": info.get("reason", ""),
            "running": False,
            "state": "Unsupported",
            "ssid": "",
            "passphrase": "",
            "band": "",
            "clients": 0,
            "client_list": [],
            "max_clients": 0,
            "detail": "本机不支持热点共享",
        }
    backend = info.get("backend")
    try:
        if backend == "winrt":
            data = _winrt_status()
        elif backend == "powershell":
            raw = _run_ps("status", timeout=40.0)
            data = {
                "supported": True,
                "backend": "powershell",
                "reason": "",
                "running": bool(raw.get("running")),
                "state": str(raw.get("state") or ""),
                "ssid": str(raw.get("ssid") or ""),
                "passphrase": str(raw.get("passphrase") or ""),
                "band": str(raw.get("band") or "Auto"),
                "clients": int(raw.get("client_count") or 0),
                "client_list": list(raw.get("clients") or []),
                "max_clients": int(raw.get("max_clients") or 0),
                "detail": "热点已开启" if raw.get("running") else "热点未启动",
            }
        else:
            hosted = _hosted_state()
            data = {
                "supported": True,
                "backend": "hosted",
                "reason": "",
                "running": bool(hosted.get("running")),
                "state": str(hosted.get("state") or ""),
                "ssid": str(hosted.get("ssid") or ""),
                "passphrase": "",
                "band": "2.4 GHz",
                "clients": int(hosted.get("clients") or 0),
                "client_list": [],
                "max_clients": 0,
                "detail": "热点已开启" if hosted.get("running") else "热点未启动",
            }
    except Exception as exc:  # noqa: BLE001 - 后端异常不要炸掉界面
        return {
            "supported": True,
            "backend": backend or "none",
            "reason": str(exc),
            "running": False,
            "state": "Error",
            "ssid": "",
            "passphrase": "",
            "band": "",
            "clients": 0,
            "client_list": [],
            "max_clients": 0,
            "detail": f"读取热点状态失败：{exc}",
        }
    data["supported"] = True
    return data


# ---- 名称 / 密码 ------------------------------------------------------------


def check_credentials(ssid: str, passphrase: str) -> dict[str, Any]:
    """校验热点名称/密码，返回 ``{"ok": bool, "error": str}``。"""
    error = _check_credentials(ssid, passphrase)
    return {"ok": not error, "error": error}


def _check_credentials(ssid: str, passphrase: str) -> str:
    ssid = (ssid or "").strip()
    if not ssid:
        return "热点名称不能为空"
    if len(ssid) > MAX_SSID:
        return f"热点名称最多 {MAX_SSID} 个字符"
    if len(passphrase or "") < MIN_PASSWORD:
        return f"热点密码至少 {MIN_PASSWORD} 位"
    if len(passphrase or "") > MAX_PASSWORD:
        return f"热点密码最多 {MAX_PASSWORD} 位"
    return ""


def random_password(length: int = 12) -> str:
    return "".join(random.choice(PASSWORD_ALPHABET) for _ in range(length))


def default_ssid() -> str:
    return DEFAULT_SSID.format(suffix=f"{random.randint(0x1000, 0xFFFF):04X}")


def _with_config(ssid: str, passphrase: str) -> Any:
    """构造一份访问点配置。"""
    cfg = _nop.NetworkOperatorTetheringAccessPointConfiguration()
    if ssid:
        cfg.ssid = ssid
    if passphrase:
        cfg.passphrase = passphrase
    return cfg


def configure(ssid: str, passphrase: str) -> dict[str, Any]:
    """写入热点名称/密码。

    注意：热点**没开**的时候系统不会真正落盘这份配置（WinRT 与 netsh 都一样），
    所以界面把名称/密码存在自己的设置里，``start()`` 时会再传进来。
    """
    error = _check_credentials(ssid, passphrase)
    if error:
        return {"ok": False, "error": error}
    info = detect()
    backend = info.get("backend")
    try:
        if backend == "winrt":
            manager = _manager()
            if _enum_name(manager.tethering_operational_state) not in ("ON", "On"):
                return {"ok": True, "error": "", "pending": True}
            _run_coro(lambda: manager.configure_access_point_async(_with_config(ssid, passphrase)), timeout=30.0)
            return {"ok": True, "error": ""}
        if backend == "powershell":
            raw = _run_ps("configure", ssid, passphrase, timeout=45.0)
            return {"ok": bool(raw.get("ok")), "error": str(raw.get("error") or "")}
        result = run(
            ["netsh", "wlan", "set", "hostednetwork", "mode=allow", f"ssid={ssid}", f"key={passphrase}"],
            timeout=20.0,
        )
        return {"ok": result.code == 0, "error": (result.stderr or "").strip()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def start(ssid: str = "", passphrase: str = "") -> dict[str, Any]:
    """启动热点；给了名称/密码就先写进去再开。"""
    if ssid or passphrase:
        check = check_credentials(ssid, passphrase)
        if not check["ok"]:
            return {"ok": False, "error": check["error"], "status": status()}
    backend = detect().get("backend")
    try:
        if backend == "winrt":
            manager = _manager()
            cfg = None
            if ssid or passphrase:
                cfg = manager.get_current_access_point_configuration()
                if ssid:
                    cfg.ssid = ssid
                if passphrase:
                    cfg.passphrase = passphrase
                _run_coro(lambda: manager.configure_access_point_async(cfg), timeout=30.0)
            result = _run_coro(manager.start_tethering_async, timeout=45.0)
            state = _enum_name(result.status) if result is not None else ""
            data = status()
            ok = bool(data.get("running"))
            return {
                "ok": ok,
                "error": "" if ok else _start_error_text(state),
                "state": state,
                "status": data,
            }
        if backend == "powershell":
            raw = _run_ps("start", ssid, passphrase, timeout=90.0)
            data = status()
            ok = bool(data.get("running"))
            return {"ok": ok, "error": "" if ok else str(raw.get("error") or "热点没有进入运行状态"), "status": data}
        if ssid or passphrase:
            run(["netsh", "wlan", "set", "hostednetwork", "mode=allow", f"ssid={ssid}", f"key={passphrase}"], timeout=20.0)
        result = run(["netsh", "wlan", "start", "hostednetwork"], timeout=30.0)
        data = status()
        ok = bool(data.get("running"))
        return {"ok": ok, "error": "" if ok else (result.stdout or result.stderr or "").strip(), "status": data}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "status": status()}


def _start_error_text(state: str) -> str:
    mapping = {
        "MOBILE_BROADBAND_DEVICE_OFF": "移动宽带设备没有就绪",
        "WI_FI_DEVICE_OFF": "Wi-Fi 已关闭，请先打开 Wi-Fi",
        "ENTITLEMENT_CHECK_TIMEOUT": "运营商校验超时",
        "ENTITLEMENT_CHECK_FAILURE": "运营商校验未通过",
        "OPERATION_IN_PROGRESS": "上一次操作还没结束，请稍后再试",
        "RADIO_RESTRICTION": "无线功能被系统限制（飞行模式或策略）",
        "BAND_INTERFERENCE": "频段冲突，试试切到另一个频段",
        "NETWORK_LIMITED_CONNECTIVITY": "当前网络连接受限，无法共享",
    }
    return mapping.get(state.upper(), "热点没有进入运行状态")


def stop() -> dict[str, Any]:
    backend = detect().get("backend")
    try:
        if backend == "winrt":
            manager = _manager()
            result = _run_coro(manager.stop_tethering_async, timeout=45.0)
            state = _enum_name(result.status) if result is not None else ""
            data = status()
            ok = not bool(data.get("running"))
            return {"ok": ok, "error": "" if ok else _start_error_text(state), "status": data}
        if backend == "powershell":
            raw = _run_ps("stop", timeout=90.0)
            data = status()
            ok = not bool(data.get("running"))
            return {"ok": ok, "error": "" if ok else str(raw.get("error") or "热点没有停止"), "status": data}
        result = run(["netsh", "wlan", "stop", "hostednetwork"], timeout=30.0)
        data = status()
        ok = not bool(data.get("running"))
        return {"ok": ok, "error": "" if ok else (result.stdout or "").strip(), "status": data}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "status": status()}


# ---- 已连接设备 -------------------------------------------------------------


def _hotspot_prefix() -> str:
    result = run(["ipconfig"], timeout=12.0)
    for line in (result.stdout or "").splitlines():
        if ICS_PREFIX in line:
            return ICS_PREFIX
    return ICS_PREFIX


def _vendor(mac: str) -> str:
    key = (mac or "").upper().replace("-", ":")[:8]
    return _OUI.get(key, "")


def _hostname(ip: str) -> str:
    try:
        result = run(["ping", "-a", "-n", "1", "-w", "300", ip], timeout=5.0)
        text = result.stdout or ""
        for line in text.splitlines():
            if ip in line and "[" not in line:
                name = line.split(ip)[0].strip().rstrip(".").strip()
                if name and name != ip:
                    return name
    except Exception:  # noqa: BLE001
        pass
    return ""


def parse_arp(text: str, prefix: str = ICS_PREFIX) -> list[dict[str, Any]]:
    """从 ``arp -a`` 输出里挑出热点网段的设备。"""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})")
    for match in pattern.finditer(text or ""):
        ip = match.group(1)
        mac = match.group(2).upper().replace("-", ":")
        if not ip.startswith(prefix):
            continue
        if mac == "FF:FF:FF:FF:FF:FF" or ip.endswith(".255"):
            continue
        if ip in seen:
            continue
        seen.add(ip)
        rows.append({"ip": ip, "mac": mac, "vendor": _vendor(mac), "name": ""})
    rows.sort(key=lambda item: int(item["ip"].rsplit(".", 1)[-1]))
    return rows


def blocked_ips() -> set[str]:
    """当前被防火墙规则拦住的 IP。"""
    result = run_cmd('netsh advfirewall firewall show rule name=all dir=in', timeout=30.0)
    text = result.stdout or ""
    blocked: set[str] = set()
    pattern = re.compile(r"^\s*CampusNet Hotspot Block (\d{1,3}(?:\.\d{1,3}){3})\s*$", re.MULTILINE)
    for match in pattern.finditer(text):
        blocked.add(match.group(1))
    return blocked


def devices() -> list[dict[str, Any]]:
    """已连接设备：WinRT 的 MAC/主机名 + ARP 的 IP/厂商 合并后返回。"""
    info = status()
    rows = parse_arp((run(["arp", "-a"], timeout=15.0).stdout or ""), _hotspot_prefix())
    by_mac = {row["mac"]: row for row in rows}
    blocked = blocked_ips()
    result: list[dict[str, Any]] = []
    for item in info.get("client_list") or []:
        mac = str(item.get("mac") or "").upper().replace("-", ":")
        row = by_mac.pop(mac, None)
        result.append(
            {
                "ip": (row or {}).get("ip", ""),
                "mac": mac,
                "name": str(item.get("name") or "") or (row or {}).get("name", "") or "未命名设备",
                "vendor": _vendor(mac),
                "blocked": bool((row or {}).get("ip") in blocked) if row else False,
            }
        )
    for row in by_mac.values():
        row = dict(row)
        row["name"] = row.get("name") or _hostname(row["ip"]) or "未命名设备"
        row["blocked"] = row["ip"] in blocked
        result.append(row)
    for row in result:
        if not row.get("name") or row["name"] == "未命名设备":
            host = _hostname(row["ip"]) if row.get("ip") else ""
            if host:
                row["name"] = host
    result.sort(key=lambda item: item.get("ip") or "999")
    return result


# ---- 断开 / 恢复（防火墙）---------------------------------------------------


def block_device(ip: str) -> dict[str, Any]:
    if not ip:
        return {"ok": False, "error": "没有拿到这个设备的 IP"}
    errors = []
    for direction in ("in", "out"):
        rule = FIREWALL_RULE.format(ip=ip)
        result = run(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={rule}", f"dir={direction}", "action=block", f"remoteip={ip}",
            ],
            timeout=25.0,
        )
        if result.code != 0:
            errors.append((result.stdout or result.stderr or "").strip())
    if errors:
        detail = "；".join(text for text in errors if text) or "需要管理员权限"
        return {"ok": False, "error": detail}
    return {"ok": True, "error": ""}


def unblock_device(ip: str) -> dict[str, Any]:
    if not ip:
        return {"ok": False, "error": "没有拿到这个设备的 IP"}
    rule = FIREWALL_RULE.format(ip=ip)
    for direction in ("in", "out"):
        run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}", f"dir={direction}"], timeout=25.0)
    return {"ok": True, "error": ""}


def open_settings() -> None:
    """打开系统的「移动热点」设置页。"""
    try:
        os.startfile("ms-settings:network-mobilehotspot")  # noqa: S606 - Windows 专用
    except Exception:  # noqa: BLE001
        pass


def wait_ready(timeout: float = 8.0) -> dict[str, Any]:
    """等热点进入运行状态。"""
    deadline = time.time() + timeout
    data = status()
    while time.time() < deadline and not data.get("running"):
        time.sleep(0.5)
        data = status()
    return data
