"""应用服务：把 WiFi、认证、流量统计、诊断串成后台服务，并把变化推送给界面。

线程模型：
- 所有耗时操作（netsh、PowerShell、HTTP）都在后台线程执行；
- 通过 Qt 信号把状态/日志/采样推给界面线程，界面不做任何阻塞调用。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal

from ..core import autostart
from ..core.settings import Settings
from ..core.sysinfo import gateway_info, is_elevated, os_info, public_ip, system_summary
from ..core.traffic import TrafficMonitor
from ..core.deps import PROJECT_ROOT, resource_path
from ..net import diagnostics, wifi
from ..portal import selfbase
from ..portal.auth import AuthEngine
from ..portal.selfservice import SelfService

PORTAL_HOST = "10.100.2.2"
PORTAL_PORT = 9090
PORTAL_ORIGIN = f"http://{PORTAL_HOST}:{PORTAL_PORT}"


def _local_ip(host: str = PORTAL_HOST, port: int = PORTAL_PORT) -> str:
    """取本机在校园网里的地址（问一下网关就知道走哪张网卡）。"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, port))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def _default_portal_query(ip: str, mac: str, acname: str = "Panabit") -> str:
    """按本校网关的下发格式拼查询串（自助接口只需要原样回传它）。"""
    return f"wlanuserip={ip}&clientip={ip}&wlanacname={acname}&clientmac={mac}&paip=10.100.1.2&vlan=100.0&iarmdst=demo.com/"


def _parse_size(text: str) -> int:
    """把学校侧给的可读大小（``"4 TB"`` / ``"1.5 GB"`` / ``"0 B"``）换成字节数。"""
    import re as _re
    match = _re.match(r"\s*([\d.]+)\s*([KMGTP]?)B?\s*$", str(text or ""), _re.I)
    if not match:
        return 0
    try:
        value = float(match.group(1))
    except ValueError:
        return 0
    unit = (match.group(2) or "").upper()
    scale = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}.get(unit, 1)
    return int(value * scale)


def _package_from_flow(flow: dict[str, Any]) -> dict[str, Any]:
    """把 ``/api/selfnet/flowInfo`` 的返回整理成主页要用的套餐信息。

   实测返回形如::

       {"totalFlowFormat": "4194304 TB", "flowBalanceFormat": "4194304 TB",
        "used": "0 B", "startTime": "...", "endTime": "...", "productName": null}

   流量按 1024 进制，``4194304 TB`` 就是 2^62 字节 —— 门户用它表示「不限量」。
   """

    def first_int(keys: tuple[str, ...]) -> int:
        for key in keys:
            try:
                value = int(flow.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        return 0

    total_bytes = first_int(("totalFlow", "totalData", "total")) or _parse_size(flow.get("totalFlowFormat", ""))
    left_bytes = first_int(("flowBalance", "leftData", "left")) or _parse_size(flow.get("flowBalanceFormat", ""))
    used_bytes = first_int(("usedFlow", "monthTotal")) or _parse_size(flow.get("used", ""))

    return {
        "total_bytes": total_bytes,
        "left_bytes": left_bytes,
        "used_bytes": used_bytes,
        "total_text": flow.get("totalFlowFormat") or "",
        "left_text": flow.get("flowBalanceFormat") or "",
        "used_text": flow.get("used") or "",
        "start_time": flow.get("startTime") or "",
        "end_time": flow.get("endTime") or "",
        "product_name": flow.get("productName") or "",
        "unlimited": total_bytes >= 1024 ** 5,
    }


STATE_IDLE = "idle"
STATE_CHECKING = "checking"
STATE_CONNECTING_WIFI = "connecting-wifi"
STATE_AUTHENTICATING = "authenticating"
STATE_ONLINE = "online"
STATE_PORTAL = "portal"
STATE_OFFLINE = "offline"
STATE_ERROR = "error"


class Service(QObject):
    """后台服务，供界面调用。"""

    STARTUP_BURST_SECONDS = 90.0
    STARTUP_BURST_INTERVAL = 3.0

    state_changed = pyqtSignal(dict)
    log_added = pyqtSignal(dict)
    traffic_sample = pyqtSignal(dict)
    notify = pyqtSignal(str, str)

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings or Settings().load()
        self.project_root = PROJECT_ROOT
        self.resource_dir = os.path.join(PROJECT_ROOT, "resources")
        self.traffic_script = resource_path("traffic.ps1")
        self.wifi_script = resource_path("wifi.ps1")

        self.engine = AuthEngine(get_config=self._auth_config, on_log=self._on_engine_log)
        self.traffic = TrafficMonitor(
            script_path=self.traffic_script,
            data_dir=self.settings.data_dir,
            interval_ms=int(self.settings.traffic_interval_ms),
            logger=self.log,
        )
        # 默认只统计校园网内的流量（traffic_only_campus）。
        self.traffic.record_gate = self._traffic_record_allowed

        self._state = {
            "state": STATE_IDLE,
            "label": "待检测",
            "ssid": "",
            "wifi_connected": False,
            "online": False,
            "portal": None,
            "last_check": 0.0,
            "last_auth": None,
            "elevated": None,
            "busy": False,
            "retry": None,
            "online_since": 0.0,
            "wifi_status": {},
        }
        self._logs = []
        self._retry_attempt = 0
        self._last_auth_attempt = 0.0
        self._loop_thread = None
        self._loop_stop = threading.Event()
        self._busy_lock = threading.Lock()
        self._online_since = 0.0
        self._selfservice = None

        self._was_online = False
        self._drop_notified = False
        self._reconnect_pending = False

        self._campus_days = {}
        self._campus_writes = 0
        self._last_traffic_total = 0.0
        self._load_campus_usage()

    def log(self, level: str, message: str, detail: str = "") -> None:
        entry = {
            "at": time.time(),
            "level": level,
            "message": message,
            "detail": detail or None,
            "source": "service",
        }
        self._logs.append(entry)
        if len(self._logs) > 800:
            del self._logs[:-800]
        self.log_added.emit(entry)

    def _on_engine_log(self, entry: dict) -> None:
        self.log_added.emit(entry)

    def logs(self, limit: int = 300) -> list[dict[str, Any]]:
        merged = self._logs + self.engine.get_logs(limit)
        merged.sort(key=lambda item: item.get("at", 0))
        return merged[-limit:]

    def clear_logs(self) -> None:
        self._logs.clear()
        self.engine.clear_logs()

    def _set_state(self, state: str, label: str = "") -> None:
        self._state["state"] = state
        self._state["label"] = label or state
        self.state_changed.emit(self.state())

    def state(self) -> dict[str, Any]:
        snapshot = dict(self._state)
        snapshot["now"] = time.time()
        snapshot["config"] = self.settings.as_dict()
        snapshot["online_for"] = time.time() - self._online_since if self._online_since else 0.0
        snapshot["traffic_healthy"] = self.traffic.healthy
        return snapshot

    def _on_traffic_sample(self, sample: dict) -> None:
        """（保留钩子）采样到达界面线程后的处理。"""

    def _traffic_record_allowed(self) -> bool:
        """当前是否属于「校园网环境」——决定这次流量差值要不要记账。"""
        if not bool(self.settings.traffic_only_campus):
            return True
        if self._online_since:
            return True
        ssid = str(self._state.get("ssid") or "")
        if not ssid:
            return False
        return wifi.any_ssid_matches(ssid, list(self.settings.wifi_allow_list or []))

    def start(self) -> None:
        self.log("info", f"数据目录：{self.settings.data_dir}")
        self.log("info", f"资源目录：{self.resource_dir}")
        self.log("info", f"系统信息：{os_info()}")

        if is_elevated():
            self._state["elevated"] = True
            self.log("ok", "已以管理员身份运行")
        else:
            self._state["elevated"] = False
            self.log("warn", "当前未提权：若连接 WiFi 失败，会在那一刻请求一次 UAC")

        self.traffic.start()
        threading.Thread(target=self._emit_traffic_loop, name="traffic-emit", daemon=True).start()

    def stop(self) -> None:
        self._loop_stop.set()
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=3)
        self.traffic.stop()
        self.settings.save()

    def start_loop(self, interval_ms: int = 15000) -> None:
        if self._loop_thread and self._loop_thread.is_alive():
            return
        self._loop_stop.clear()

        def runner() -> None:
            burst_until = time.time() + self.STARTUP_BURST_SECONDS
            self.tick(manual=False)
            while True:
                online = bool(self._online_since)
                fast = not online and time.time() < burst_until
                delay = self.STARTUP_BURST_INTERVAL if fast else interval_ms / 1000.0
                if self._loop_stop.wait(delay):
                    return
                try:
                    self.tick(manual=False)
                except Exception as exc:
                    self.log("error", f"循环任务异常：{exc}")

        self._loop_thread = threading.Thread(target=runner, name="service-loop", daemon=True)
        self._loop_thread.start()

    def _emit_traffic_loop(self) -> None:
        """把最新采样周期性地推给界面（约 1 秒一次）。"""
        last_at = 0.0
        while not self._loop_stop.is_set():
            sample = self.traffic._last_sample
            if sample and sample.at != last_at:
                last_at = sample.at
                self.traffic_sample.emit(
                    {
                        "at": sample.at,
                        "rx_rate": sample.rx_rate,
                        "tx_rate": sample.tx_rate,
                        "delta_rx": sample.delta_rx,
                        "delta_tx": sample.delta_tx,
                        "mode": sample.mode,
                        "source": sample.source,
                    }
                )
            time.sleep(1.0)

    def _auth_config(self) -> dict[str, Any]:
        return {
            **self.settings.as_dict(),
            "username": self.settings.username,
            "password": self.settings.password,
        }

    def wifi_status(self) -> dict[str, Any]:
        return wifi.status(self.wifi_script)

    def scan_wifi(self, force: bool = True) -> dict[str, Any]:
        result = wifi.scan(force=force)
        allow = list(self.settings.wifi_allow_list or [])
        for network in result.get("networks", []):
            network["in_allow_list"] = wifi.any_ssid_matches(network["ssid"], allow)
        return result

    def saved_profiles(self) -> dict[str, Any]:
        return wifi.saved_profiles()

    def connect_ssid(self, ssid: str) -> dict[str, Any]:
        self._set_state(STATE_CONNECTING_WIFI, f"正在连接 {ssid}…")
        result = wifi.connect_and_wait(
            ssid,
            timeout_ms=int(self.settings.wifi_connect_wait_ms) + 15000,
            ps_script=self.wifi_script,
        )
        self.log("info" if result.get("ok") else "error", f"连接 {ssid}：{result.get('message', '')}")
        return result

    def _pick_target_network(self, exclude: str = "") -> str | None:
        """挑一个最该连的校园网（信号最强的那个）。

        ``exclude`` 用来避开当前已连但连不通的那个，实现「换一个网络试试」。
        """
        result = self.scan_wifi(force=True)
        allow = list(self.settings.wifi_allow_list or [])

        candidates = [
            n
            for n in result.get("networks", [])
            if wifi.any_ssid_matches(n["ssid"], allow)
            if not wifi.ssid_matches(n["ssid"], exclude)
        ]
        if not candidates:
            try:
                saved = wifi.saved_profiles().get("profiles", [])
            except Exception:
                saved = []
            for name in saved:
                if not wifi.any_ssid_matches(name, allow):
                    continue
                if wifi.ssid_matches(name, exclude):
                    continue
                return name
            return None

        candidates.sort(key=lambda n: n.get("signal") if n.get("signal") is not None else -1, reverse=True)
        return candidates[0]["ssid"]

    def seen_networks(self, force: bool = True) -> list[dict[str, Any]]:
        """最近一次扫描到的网络（带是否在名单内的标记）。"""
        result = self.scan_wifi(force=force)
        return list(result.get("networks") or [])

    def adopt_networks(self, ssids: list[str], *, persist: bool = True) -> list[str]:
        """把发现的校园网 SSID 收进「自动连接名单」，返回真正新增的那些。"""
        current = list(self.settings.wifi_allow_list or [])
        added = []
        for ssid in ssids:
            if not ssid or not ssid.strip():
                continue
            if any(wifi.ssid_matches(ssid, pattern) for pattern in current):
                continue
            current.append(ssid)
            added.append(ssid)
        if added and persist:
            self.settings.update({"wifi_allow_list": current})
            self.settings.save()
            self.log("info", f"已把 {', '.join(added)} 加入自动连接名单")
        return added

    def tick(self, manual: bool = False, switched: bool = False) -> dict[str, Any]:
        """一次完整的「确保联网」流程。

        ``switched=True`` 表示这次是「换过网络后的重试」——此时不再抢锁
        （锁已经被外层这一次 tick 持有着），也不会再换第二次网络。
        """
        if not switched:
            if not self._busy_lock.acquire(blocking=False):
                return {"ok": False, "skipped": True, "message": "已有任务在执行"}
        try:
            self._state["busy"] = True
            self._set_state(STATE_CHECKING, "检测网络状态…")

            status = self.wifi_status()
            self._state["ssid"] = status.get("ssid", "")
            self._state["wifi_connected"] = bool(status.get("connected"))
            self._state["wifi_status"] = status
            self.state_changed.emit(self.state())

            if not status.get("connected"):
                if self.settings.auto_connect:
                    self._connect_best_network()
                    status = self.wifi_status()
                    self._state["ssid"] = status.get("ssid", "")
                    self._state["wifi_connected"] = bool(status.get("connected"))
                    self._state["wifi_status"] = status
                if not status.get("connected"):
                    self._state["busy"] = False
                    self._set_state(STATE_OFFLINE, "未连接 WiFi")
                    return self.state()

            result = self.engine.status(timeout=6.0)
            self._state["online"] = result["state"] == "online"
            self._state["last_check"] = time.time()

            self._state["portal"] = (
                {
                    "url": result.get("portal_url", ""),
                    "flavor": (self.engine.portal_cache or {}).get("flavor"),
                }
                if result["state"] == "portal"
                else None
            )

            if result["state"] == "online":
                if not self._online_since:
                    self._online_since = time.time()
                self._retry_attempt = 0
                self._account_campus_traffic()
                self._note_online()

                if manual and self.settings.auto_connect and not switched:
                    better = self._better_network(status)
                    if better:
                        self.log("info", f"当前信号偏弱（{status.get('signal')}%），切换到更强的 {better}…")
                        if self._switch_to_other_network(status.get("ssid", "")):
                            self._state["busy"] = False
                            return self.tick(manual=manual, switched=True)
                self._state["busy"] = False
                self._set_state(STATE_ONLINE, "已联网")
                return self.state()

            self._online_since = 0.0
            self._account_campus_traffic()
            self._note_drop(result)

            if result["state"] == "portal":
                self._set_state(STATE_PORTAL, "需要认证")
                if self.settings.auto_login:
                    auth = self.authenticate(manual=manual)
                    self._state["busy"] = False
                    return {**self.state(), "auth": auth}
                self._state["busy"] = False
                return self.state()

            if self.settings.auto_connect and not switched and self._should_switch(status):
                if self._switch_to_other_network(status.get("ssid", "")):
                    self._state["busy"] = False
                    return self.tick(manual=manual, switched=True)

            self._state["busy"] = False
            self._set_state(STATE_OFFLINE, result.get("label") or "外网不可达")
            if self.settings.auto_login and self.settings.auto_retry_on_drop and not manual:
                self._schedule_retry()
            return self.state()
        finally:
            self._state["busy"] = False
            if not switched:
                self._busy_lock.release()

    WEAK_SIGNAL = 25
    SWITCH_MARGIN = 15

    def _should_switch(self, status: dict[str, Any]) -> bool:
        """当前这个网络上不了网时，判断有没有更值得连的校园网。"""
        return bool(self._pick_target_network(exclude=status.get("ssid", "")))

    def _better_network(self, status: dict[str, Any]) -> str:
        """当前信号很弱、且旁边有强得多的校园网时，返回那个 SSID。"""
        current_signal = status.get("signal")
        if not isinstance(current_signal, (int, float)) or current_signal >= self.WEAK_SIGNAL:
            return ""
        result = self.scan_wifi(force=True)
        allow = list(self.settings.wifi_allow_list or [])
        best = None
        for network in result.get("networks", []):
            ssid = network.get("ssid") or ""
            signal = network.get("signal")
            if not ssid or not isinstance(signal, (int, float)):
                continue
            if wifi.ssid_matches(ssid, status.get("ssid", "")) or not wifi.any_ssid_matches(ssid, allow):
                continue
            if signal < current_signal + self.SWITCH_MARGIN:
                continue
            if best is None or signal > best[0]:
                best = (signal, ssid)
        return best[1] if best else ""

    def _switch_to_other_network(self, current: str) -> str | None:
        """切到另一个校园网。返回真正切过去的 SSID。"""
        target = self._pick_target_network(exclude=current)
        if not target:
            return None
        self.log("info", f"当前网络（{current or '未连接'}）不可用，尝试切换到 {target}…")
        self._set_state(STATE_CONNECTING_WIFI, f"正在切换到 {target}…")
        result = self.connect_ssid(target)
        if result.get("ok"):
            return target
        self.log("warn", f"切换到 {target} 失败：{result.get('message', '')}")
        return None

    def switch_network(self) -> dict[str, Any]:
        """手动「换一个校园网」：优先切到信号更强的另一个校园网。"""
        status = self.wifi_status()
        current = status.get("ssid", "")
        target = self._pick_target_network(exclude=current)
        if not target:
            target = self._pick_target_network()
        if not target:
            return {"ok": False, "message": "附近没有可用的校园网"}
        self._set_state(STATE_CONNECTING_WIFI, f"正在切换到 {target}…")
        result = self.connect_ssid(target)
        return {**result, "ssid": target}

    CAMPUS_HINTS = ("NYKJ", "NYKY", "NYK", "CMCC")

    def campus_candidates(self, networks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """看起来是本校校园网、但还不在自动连接名单里的那些。"""
        allow = list(self.settings.wifi_allow_list or [])
        out = []
        for network in networks:
            ssid = (network.get("ssid") or "").strip()
            if not ssid or wifi.any_ssid_matches(ssid, allow):
                continue
            normalized = wifi.normalize_ssid(ssid)
            if not any(hint in normalized for hint in self.CAMPUS_HINTS):
                continue
            out.append(network)
        return out

    def _connect_best_network(self) -> str | None:
        """扫描 → 必要时把发现的校园网自动加入名单 → 连信号最好的那个。

        这是「无网环境下点一下就能自动找网并连上」的核心，也是开机自启后
        第一件事。为了快，这里**只扫一次**（``force=False``，省掉一次触发扫描），
        并把结果直接喂给选网逻辑，不重复扫。
        """
        networks = self.seen_networks(force=False)
        if not networks:
            networks = self.seen_networks(force=True)
        if not networks:
            self.log("warn", "扫描不到任何无线网络：请确认 WLAN 已开启、且已允许「定位服务」")
        else:
            self.log("info", f"扫描到 {len(networks)} 个网络：" + "、".join(n["ssid"] for n in networks[:6]))

        campus = self.campus_candidates(networks)
        if campus:
            added = self.adopt_networks([n["ssid"] for n in campus])
            if added:
                self.log("info", f"自动收录校园网到连接名单：{', '.join(added)}")

        target = self._pick_from(networks)
        if not target:
            self.log("warn", "没有找到允许自动连接的校园网，可在设置里补充名单或点「扫描并加入」")
            return None

        self._set_state(STATE_CONNECTING_WIFI, f"正在连接 {target}…")
        result = self.connect_ssid(target)
        return target if result.get("ok") else None

    def _pick_from(self, networks: list[dict[str, Any]], exclude: str = "") -> str | None:
        """从已经拿到的扫描结果里挑信号最强的校园网（不再重新扫描）。"""
        allow = list(self.settings.wifi_allow_list or [])
        candidates = [
            n
            for n in networks
            if wifi.any_ssid_matches(n.get("ssid", ""), allow)
            if not wifi.ssid_matches(n.get("ssid", ""), exclude)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda n: n.get("signal") if n.get("signal") is not None else -1, reverse=True)
        return candidates[0]["ssid"]

    def authenticate(self, manual: bool = False, force: bool = True) -> dict[str, Any]:
        """执行认证。"""
        if self.engine.busy:
            return {"ok": False, "skipped": True, "message": "认证正在进行中"}
        if time.time() - self._last_auth_attempt < 5:
            return {"ok": False, "skipped": True, "message": "认证请求过于频繁，请稍候"}
        self._last_auth_attempt = time.time()

        self._set_state(STATE_AUTHENTICATING, "正在提交认证…")
        result = self.engine.auto_login(force=force)
        self._state["last_auth"] = result

        if result.get("ok"):
            self._retry_attempt = 0
            self._state["online"] = True
            self._online_since = time.time()
            self._set_state(STATE_ONLINE, "已联网")
            self._note_online()
            if self.settings.show_notification and not self._drop_notified:
                self._notify(self._online_message("连接成功"))
            if not self.settings.last_product_name:
                threading.Thread(target=self._cache_package_name, name="cache-package", daemon=True).start()
            return result

        if result.get("needs_disconnect"):
            self.log("warn", "账号在线数已满（-1022），可在「账户与设备」页查看在线设备并下线")
            if self.settings.notify_on_drop:
                self._notify("账号已在其它设备登录（在线数已满），正在尝试抢回…")
                self._drop_notified = True
            if self.settings.kick_on_conflict:
                listing = self.engine.list_online()
                devices = listing.get("devices", [])
                if devices:
                    self.log("info", f"尝试下线最早在线的设备 {devices[0]['mac']}…")
                    self.engine.kick_device(devices[0]["mac"])
                    time.sleep(2.5)
                    self._last_auth_attempt = 0
                    return self.authenticate(manual=manual, force=force)
                self.log("warn", "未获取到在线设备列表，改为尝试直接注销当前会话")
                self.engine.logout()
                time.sleep(2.5)
                self._last_auth_attempt = 0
                return self.authenticate(manual=manual, force=force)

        if not manual and self.settings.auto_retry_on_drop:
            self._schedule_retry()
        return result

    def _campus_file(self) -> str:
        """campus_usage.json"""
        return os.path.join(self.settings.data_dir, "campus_usage.json")

    def _load_campus_usage(self) -> None:
        try:
            with open(self._campus_file(), "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                self._campus_days = {str(k): float(v) for k, v in data.items()}
        except (OSError, ValueError):
            self._campus_days = {}

    def _save_campus_usage(self) -> None:
        try:
            with open(self._campus_file(), "w", encoding="utf-8") as fp:
                json.dump(self._campus_days, fp, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def on_campus_network(self) -> bool:
        """当前是否处在校园网环境（决定流量是否计入套餐）。"""
        state = self._state or {}
        ssid = str(state.get("ssid") or "")
        allow = list(self.settings.wifi_allow_list or [])
        if ssid and wifi.any_ssid_matches(ssid, allow):
            return True
        return bool(state.get("online")) and not ssid

    def _account_campus_traffic(self) -> None:
        """把两次采样之间的流量增量记到"校园网内"账上。

        校园网以外的网络（家里 WiFi、手机热点等）产生的流量**不计入套餐** ——
        套餐额度是学校侧的，外面的流量学校也不会算。
        """
        try:
            total = float((self.traffic.summary().get("all") or {}).get("total") or 0)
        except Exception:
            return
        delta = total - self._last_traffic_total
        self._last_traffic_total = total
        if delta <= 0 or not self.on_campus_network():
            return
        day = time.strftime("%Y-%m-%d")
        self._campus_days[day] = self._campus_days.get(day, 0.0) + delta
        self._campus_writes += 1
        if self._campus_writes >= 6:
            self._campus_writes = 0
            self._save_campus_usage()

    def campus_usage(self) -> dict[str, Any]:
        """校园网内的用量（套餐进度只看这个）。"""
        today = time.strftime("%Y-%m-%d")
        month_prefix = today[:7]
        month = sum(v for k, v in self._campus_days.items() if k.startswith(month_prefix))
        return {
            "today": self._campus_days.get(today, 0.0),
            "month": month,
            "days": len(self._campus_days),
            "campus_now": self.on_campus_network(),
            "file": self._campus_file(),
        }

    def _notify(self, message: str, title: str = "") -> None:
        """发一条系统通知（应用名默认「校园网」）。

        「系统通知」总开关关闭时这里直接吞掉——认证、掉线、冲突等所有提示都走
        这一个出口，避免出现「关掉了总开关、掉线通知照样弹」的情况。
        """
        if not self.settings.show_notification:
            return
        self.notify.emit(title or str(self.settings.notify_app_name or "校园网"), message)

    def _online_message(self, prefix: str) -> str:
        """连接类提示的正文：账号名 + 套餐名（有就带）。"""
        account = str(self.settings.username or "").strip()
        package = str(self.settings.last_product_name or "").strip()
        detail = " · ".join(part for part in (account, package) if part)
        return f"{prefix}：{detail}" if detail else prefix

    def _cache_package_name(self) -> None:
        """连上后顺手读一次自助系统，把套餐名缓存下来（供连接提示使用）。"""
        try:
            self.selfservice_overview()
        except Exception:
            pass

    def _note_online(self) -> None:
        """确认在线。之前通知过掉线的话，补一条"已恢复"。"""
        if self._was_online:
            return
        self._was_online = True
        self._reconnect_pending = False
        if self._drop_notified:
            self._drop_notified = False
            if self.settings.notify_on_drop:
                self._notify(self._online_message("已重新连接"))

    def _note_drop(self, result: dict[str, Any]) -> None:
        """从"在线"变成"不在线"—— 被挤下线、学校侧断会话、用户手动退出都走这里。"""
        if not self._was_online:
            return
        self._was_online = False
        state = str(result.get("state") or "")
        reason = {
            "portal": "登录状态已失效，需要重新认证",
            "no-internet": "网络已断开",
            "no-network": "无线连接已断开",
        }.get(state, str(result.get("label") or "连接已中断"))
        self.log("warn", f"检测到掉线：{reason}")
        if self.settings.notify_on_drop:
            self._notify(f"已掉线（{reason}），正在自动重连…")
            self._drop_notified = True
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """掉线后立刻安排一次重连，不等下一轮 15 秒巡检（为了"无感"）。"""
        if not self.settings.auto_reconnect_on_logout or self._reconnect_pending:
            return
        delay = max(0.2, float(self.settings.reconnect_delay_ms or 1500) / 1000.0)
        self._reconnect_pending = True
        self.log("info", f"{delay:.1f} 秒后自动重新连接…")

        def later() -> None:
            time.sleep(delay)
            self._reconnect_pending = False
            try:
                self.tick(manual=False)
            except Exception as exc:
                self.log("warn", f"自动重连失败：{exc}")

        threading.Thread(target=later, name="auto-reconnect", daemon=True).start()

    def _schedule_retry(self) -> None:
        maximum = int(self.settings.retry_count or 0)
        if self._retry_attempt >= maximum:
            if maximum:
                self.log("warn", f"已重试 {self._retry_attempt} 次仍未成功，停止自动重试")
            self._state["retry"] = None
            self.state_changed.emit(self.state())
            return
        self._retry_attempt += 1
        delay = float(self.settings.retry_delay_ms or 8000) / 1000.0
        self._state["retry"] = {
            "attempt": self._retry_attempt,
            "max": maximum,
            "at": time.time() + delay,
        }
        self.state_changed.emit(self.state())
        self.log("info", f"将在 {delay:.0f} 秒后进行第 {self._retry_attempt}/{maximum} 次重试")

        def later() -> None:
            time.sleep(delay)
            self._state["retry"] = None
            try:
                self.tick(manual=False)
            except Exception:
                pass

        threading.Thread(target=later, name="retry", daemon=True).start()

    def manual_probe(self) -> dict[str, Any]:
        """手动探活。"""
        self._set_state(STATE_CHECKING, "探测中…")
        result = self.engine.status(timeout=6.0)
        self._state["online"] = result["state"] == "online"
        self._state["last_check"] = time.time()
        self._state["portal"] = (
            {
                "url": result.get("portal_url", ""),
                "flavor": (self.engine.portal_cache or {}).get("flavor"),
            }
            if result["state"] == "portal"
            else None
        )
        labels = {
            "online": "已联网",
            "portal": "需要认证",
            "no-internet": "外网不可达",
            "no-network": "未连接网络",
        }
        self._set_state(result["state"], labels.get(result["state"], result.get("label", "")))
        return result

    def disconnect_wifi(self) -> dict[str, Any]:
        result = wifi.disconnect()
        self.log("info" if result["ok"] else "warn", f"断开 WiFi：{result.get('message', '')}")
        return result

    def run_diagnostic(self) -> dict[str, Any]:
        gateway = gateway_info().get("gateway", "")
        return diagnostics.full_diagnostic(gateway)

    def speed_test(self, duration_ms: int = 8000) -> dict[str, Any]:
        """完整测速：延迟/抖动 + 下载 + 上传。"""
        return diagnostics.full_speed_test(duration_ms)

    def speed_test_download_only(self, duration_ms: int = 8000) -> dict[str, Any]:
        return diagnostics.download_speed(duration_ms)

    def ping(self, host: str, count: int = 4) -> dict[str, Any]:
        return diagnostics.ping(host, count)

    def fetch_public_ip(self) -> dict[str, Any]:
        return public_ip()

    def system_summary(self) -> dict[str, Any]:
        return system_summary(self.wifi_script)

    def get_config(self) -> dict[str, Any]:
        return self.settings.as_dict()

    def update_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.settings.update(patch)
        self.settings.save()
        if "traffic_interval_ms" in patch:
            self.traffic.interval_ms = max(1000, int(patch["traffic_interval_ms"]))
        self.state_changed.emit(self.state())
        return self.settings.as_dict()

    def reset_config(self) -> dict[str, Any]:
        self.settings.reset()
        self.state_changed.emit(self.state())
        return self.settings.as_dict()

    def set_auto_launch(self, enabled: bool) -> dict[str, Any]:
        """开机自启。

        走**计划任务**而不是 Run 键 —— 本程序要求管理员权限，而 Windows 会
        静默跳过 Run 键里需要提权的程序（用户看不到提示、程序也不启动）。
        细节见 ``src/core/autostart.py``。
        """
        result = autostart.enable() if enabled else autostart.disable()
        mechanism = str(result.get("mechanism") or "")
        if result.get("ok"):
            how = f"（方式：{'计划任务' if mechanism == 'task' else 'Run 键'}）" if mechanism else ""
            self.log("info", f"开机自启已{'开启' if enabled else '关闭'}{how}")
            return result
        self.log("warn", f"设置开机自启失败：{result.get('message')}")
        return result

    def auto_launch_state(self) -> dict[str, Any]:
        """开机自启的**真实**状态（直接查系统，不看配置项）。"""
        return autostart.state()

    def traffic_range(self, start_ts: float, end_ts: float) -> dict[str, Any]:
        """任意时间段的用量与曲线（首页的「近 7 天 / 上一个 7 天 / 自定义」）。

        ``end_ts`` 不含，方便按自然日切分。
        """
        return {
            "start": float(start_ts),
            "end": float(end_ts),
            "summary": self.traffic.range_summary(start_ts, end_ts),
            "series": self.traffic.range_series(start_ts, end_ts),
            "history_start": self.traffic.summary().get("history_start"),
        }

    def export_traffic_csv(self, path: str) -> dict[str, Any]:
        try:
            self.traffic.export_csv(path)
            return {"ok": True, "path": path}
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

    def reset_traffic(self) -> dict[str, Any]:
        self.traffic.reset()
        return {"ok": True}

    def open_data_dir(self) -> None:
        from ..core.winutil import open_path

        open_path(self.settings.data_dir)

    def open_portal_in_browser(self) -> dict[str, Any]:
        from ..core.winutil import open_path

        url = self.settings.custom_portal_url or (self.engine.portal_cache or {}).get("params", {}).url
        if not url:
            return {"ok": False, "message": "尚未捕获 Portal 地址"}
        open_path(url)
        return {"ok": True, "url": url}

    def open_url(self, url: str) -> dict[str, Any]:
        """用系统浏览器打开任意地址（内置浏览器不可用时的兜底）。"""
        from ..core.winutil import open_path

        if not url:
            return {"ok": False, "message": "地址为空"}
        open_path(url)
        return {"ok": True, "url": url}

    def account_portal(self) -> tuple[str, str, str]:
        """返回 ``(origin, query_string, mac)``。

        优先用认证时捕获到的门户参数；已经在线、没捕获过时，
        就按本机 IP/MAC + 本校网关的固定参数现场拼一个（实测可用）。
        """
        cached = self.engine.portal_cache or {}
        params = cached.get("params") or {}
        origin = ""
        query = ""
        if isinstance(params, dict):
            origin = str(params.get("origin") or "")
            query = str(params.get("query_string") or "")
        status = self.wifi_status()
        mac = str(status.get("mac") or "")
        if not origin or not query:
            ip = _local_ip()
            origin = origin or PORTAL_ORIGIN
            query = query or _default_portal_query(ip, mac)
        return origin, query, mac

    def _account_credentials(self) -> tuple[str, str]:
        return self.settings.username, self.settings.password

    def account_devices(self) -> dict[str, Any]:
        """查询该账号当前在线的设备（用于解决「已达最大在线数」）。"""
        origin, query, mac = self.account_portal()
        username, password = self._account_credentials()
        if not username:
            return {"ok": False, "message": "还没有配置账号，请先在「设置 → 认证账户」里填写", "devices": []}
        result = selfbase.check_online(
            origin=origin, query=query, username=username,
            password=password, mac=mac,
        )
        current = str(self._state.get("ssid") and mac or "")
        for device in result.get("devices", []):
            device["current"] = bool(current) and device.get("mac", "").lower() == current.lower()
        if result.get("ok"):
            self.log(
                "info",
                f"在线设备 {len(result.get('devices', []))} 台："
                + "、".join(d.get("mac", "") for d in result.get("devices", [])),
            )
        else:
            self.log("warn", f"查询在线设备失败：{result.get('message')}")
        return result

    def account_session(self) -> dict[str, Any]:
        """查询会话详情：学校侧计费流量、在线时长、套餐订单与剩余流量。"""
        origin, query, mac = self.account_portal()
        username, password = self._account_credentials()
        if not username:
            return {"ok": False, "message": "还没有配置账号，请先在「设置 → 认证账户」里填写", "session": None}
        result = selfbase.session_info(
            origin=origin, query=query, username=username,
            password=password, mac=mac,
        )
        if result.get("ok"):
            session = result.get("session") or {}
            self.log(
                "info",
                f"学校侧计费：↑{session.get('tx_bytes', 0)} B ↓{session.get('rx_bytes', 0)} B，在线 "
                f"{session.get('online_for', 0)} 秒，套餐 #{session.get('order_id', '-')}",
            )
        else:
            self.log("info", f"未取到会话信息：{result.get('message')}")
        return result

    def kick_device(self, mac: str) -> dict[str, Any]:
        """把指定设备踢下线。"""
        if not mac:
            return {"ok": False, "message": "没有指定要下线的设备"}
        outcome = self.engine.kick_device(mac)
        if outcome and outcome.get("ok"):
            self.log("info", f"已把设备 {mac} 踢下线")
            return outcome
        origin, query, _mac_self = self.account_portal()
        username, password = self._account_credentials()
        from ..portal.detect import parse_portal_params
        from ..portal import lanhai as lanhai_driver

        params = parse_portal_params(f"{origin}/?{query}")
        fallback = lanhai_driver.down_line_user(portal=params, username=username, password=password, mac=mac)
        if fallback.get("ok"):
            self.log("info", f"已通过自助接口把设备 {mac} 踢下线")
            return fallback
        self.log("warn", f"下线设备 {mac} 失败：{fallback.get('message')}")
        return fallback


    def self_service_url(self, which: str = "home") -> str:
        if which == "password":
            return selfbase.FIND_PASSWORD
        return selfbase.SELFPAGE

    def self_service(self):
        """返回自助系统客户端；同一套账号复用已登录的 token。"""
        origin, query, _mac = self.account_portal()
        username, password = self._account_credentials()
        client = self._selfservice
        if client is None or client.username != username or client.origin != origin or client.password != password:
            client = SelfService(origin=origin, query=query, username=username, password=password)
            self._selfservice = client
        else:
            client.query = query
        return client

    def selfservice_login(self) -> dict[str, Any]:
        result = self.self_service().login(force=True)
        self.log(
            "info" if result.get("ok") else "warn",
            f"自助系统登录：{result.get('message') or '成功'}",
        )
        return result

    def selfservice_overview(self) -> dict[str, Any]:
        """一次性把「账户 + 套餐流量 + 在线设备」取回来（界面概览用）。"""
        client = self.self_service()
        login = client.login()
        if not login.get("ok"):
            return {"ok": False, "message": login.get("message") or "自助系统登录失败"}
        account = client.account_info()
        flow = client.flow_info()
        devices = client.online_devices()
        orders = client.orders()
        flow_data = flow.get("data") if flow.get("ok") else None
        package = _package_from_flow(flow_data) if isinstance(flow_data, dict) else {}
        result = {
            "ok": bool(account.get("ok") or flow.get("ok")),
            "account": account.get("data") if account.get("ok") else None,
            "flow": flow_data,
            "package": package,
            "devices": devices.get("data") if devices.get("ok") else None,
            "orders": orders.get("data") if orders.get("ok") else None,
            "message": account.get("message") or flow.get("message") or "已读取",
        }
        if package.get("total_bytes") and self.settings.quota_auto:
            if package.get("unlimited"):
                # 「不限量」套餐的总量是 2^62 字节这类哨兵值，换算成 GB 会得到
                # 4294967296 之类的天文数字，比不写还糟。这里直接归零（= 不统计配额）。
                if float(self.settings.monthly_quota_gb or 0) != 0.0:
                    self.settings.update({"monthly_quota_gb": 0.0})
                    self.settings.save()
                    self.log("info", "套餐不限量，已把每月套餐流量设为 0（不统计配额）")
            else:
                gb = round(float(package["total_bytes"]) / 1024 ** 3, 3)
                if abs(float(self.settings.monthly_quota_gb or 0) - gb) > 0.01:
                    self.settings.update({"monthly_quota_gb": gb})
                    self.settings.save()
                    self.log("info", f"已从学校套餐自动读取额度：{package.get('total_text') or gb}")
        account_data = result["account"] or {}
        product = str(account_data.get("product_name") or "").strip()
        if product and product != self.settings.last_product_name:
            self.settings.update({"last_product_name": product})
            self.settings.save()
        self.log(
            "info",
            f"自助系统：套餐 {account_data.get('product_name') or '-'}，到期 "
            f"{account_data.get('order_end_time') or '-'}，余额 {account_data.get('balance') or '-'}",
        )
        return result

    def selfservice_devices(self) -> dict[str, Any]:
        client = self.self_service()
        result = client.online_devices()
        if not result.get("ok"):
            return {"ok": False, "message": result.get("message"), "devices": []}
        data = result.get("data") or {}
        devices = data.get("devices") if isinstance(data, dict) else data
        return {"ok": True, "devices": list(devices or []), "raw": data}

    def selfservice_down_device(self, session_id: str) -> dict[str, Any]:
        result = self.self_service().down_device(session_id)
        self.log(
            "info" if result.get("ok") else "warn",
            f"自助系统下线设备 {session_id}：{result.get('message') or '成功'}",
        )
        return result

    def selfservice_change_password(self, new_password: str, captcha: str) -> dict[str, Any]:
        result = self.self_service().change_password(new_password, captcha)
        if result.get("ok"):
            self.settings.password = new_password
            self.settings.save()
            self.log("info", "已通过自助系统修改密码并更新本机保存的凭据")
            return result
        self.log("warn", f"修改密码失败：{result.get('message')}")
        return result

    def selfservice_captcha(self) -> bytes:
        return self.self_service().captcha_image()

    def selfservice_call(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """通用转发：``name`` 是 SelfService 上的只读方法名（给账单/明细/发票等用）。"""
        client = self.self_service()
        method = getattr(client, name, None)
        if method is None:
            return {"ok": False, "message": f"未知的自助接口：{name}"}
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
