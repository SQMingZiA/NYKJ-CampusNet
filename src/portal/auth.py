"""认证引擎：把「连通性探测 → Portal 指纹识别 → 驱动登录 → 复测确认」串起来。

每一步都会记录成可读日志，供界面展示。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from ..net.probe import probe as cpd_probe
from . import detect, generic, lanhai


class AuthEngine:
    """Portal 自动认证。"""

    def __init__(self, get_config: Callable[[], dict[str, Any]], on_log: Callable[[dict], None] | None = None) -> None:
        self.get_config = get_config
        self.on_log = on_log or (lambda entry: None)
        self.logs = []
        self.busy = False
        self.last_result = None
        self.portal_cache = None
        self.last_status = None
        self._lock = threading.Lock()

    def log(self, level: str, message: str, detail: str = "") -> dict[str, Any]:
        entry = {
            "at": time.time(),
            "level": level,
            "message": message,
            "detail": (detail or "")[:1200] or None,
        }
        self.logs.append(entry)
        if len(self.logs) > 500:
            del self.logs[:-500]
        try:
            self.on_log(entry)
        except Exception:
            pass
        return entry

    def get_logs(self, limit: int = 300) -> list[dict[str, Any]]:
        return self.logs[-limit:]

    def clear_logs(self) -> None:
        self.logs.clear()

    def status(self, timeout: float = 6.0) -> dict[str, Any]:
        """仅探测网络状态（online / portal / no-internet / no-network）。"""
        config = self.get_config()
        result = cpd_probe(timeout=timeout, custom_probe_url=config.get("custom_probe_url", ""))

        if result["state"] == "portal" and result.get("portal_url"):
            page = result.get("portal_raw") or {}
            detection = detect.detect(result["portal_url"], page.get("html", ""))
            self.portal_cache = {
                "flavor": detection.flavor,
                "name": detection.name,
                "notes": detection.notes,
                "params": detection.params,
                "at": time.time(),
            }
        self.last_status = {**result, "at": time.time()}
        return result

    def _pick_driver(self, config: dict[str, Any], flavor: str):
        driver = config.get("driver", "auto")
        if driver == "lanhai":
            return lanhai
        if driver == "generic":
            return generic
        return lanhai if flavor == "lanhai" else generic

    def auto_login(self, force: bool = False) -> dict[str, Any]:
        """执行一次「自动连接并登录」。"""
        with self._lock:
            if self.busy:
                return {"ok": False, "skipped": True, "message": "已有认证流程正在进行"}
            self.busy = True

        started = time.time()
        try:
            config = self.get_config()
            self.log("info", "开始检测网络状态…")
            status = self.status(timeout=6.0)

            if status["state"] == "online" and not force:
                self.log("ok", f"网络已连通，无需认证（探测点 {status.get('probe_used', '-')}，耗时 {status['elapsed_ms']}ms）")
                return self._finish({"ok": True, "already": True, "state": "online", "message": "当前网络已认证，可直接上网"})

            if status["state"] == "no-network":
                self.log("warn", "本机没有可用网络连接，请先连接 WiFi")
                return self._finish({"ok": False, "state": status["state"], "message": "未连接任何网络"})

            if status["state"] == "no-internet":
                self.log("warn", f"外网不可达：{status['label']}")
                return self._finish({"ok": False, "state": status["state"], "message": status["label"]})

            portal = self.portal_cache
            if not portal or not portal["params"].origin or time.time() - portal.get("at", 0) > 600:
                page = status.get("portal_raw") or {}
                detection = detect.detect(status.get("portal_url", ""), page.get("html", ""))
                portal = {
                    "flavor": detection.flavor,
                    "name": detection.name,
                    "notes": detection.notes,
                    "params": detection.params,
                    "at": time.time(),
                }
                self.portal_cache = portal

            self.log("info", f"捕获到 Portal：{status.get('portal_url') or '(未拿到跳转地址)'}", portal["name"])
            self.log(
                "debug",
                f"Portal 参数：wlanuserip={portal['params'].wlanuserip or '-'}"
                f" wlanacname={portal['params'].wlanacname or '-'}"
                f" clientmac={portal['params'].wlanusermac or '-'}",
            )

            if not portal["params"].origin:
                self.log("error", "未能解析 Portal 地址，请在设置里手动填写 Portal 网址")
                return self._finish({"ok": False, "state": "portal", "code": "no-portal-url", "message": "未能解析 Portal 地址"})

            username = (config.get("username") or "").strip()
            password = config.get("password") or ""
            if not username or not password:
                self.log("error", "尚未配置账号密码，请到「设置」页填写")
                return self._finish({"ok": False, "state": "portal", "code": "no-credential", "message": "尚未配置账号密码"})

            driver = self._pick_driver(config, portal["flavor"])
            self.log("info", f"使用驱动「{driver.NAME}」提交认证…")

            page_html = ((status.get("portal_raw") or {}).get("html", "") or "")
            page_acip = lanhai.extract_acip_from_html(page_html) if driver is lanhai else ""
            configured_acip = (config.get("acip") or "").strip()
            acip = configured_acip or page_acip
            if acip:
                source = "设置" if configured_acip else "门户页面"
                self.log("debug", f"wlanacip = {acip}（来自{source}）")
            else:
                self.log("warn", "没能确定 wlanacip（门户页面里没有 staticAcip），认证可能报 -113")

            timeout = float(config.get("auth_timeout_ms", 12000)) / 1000.0
            if driver is lanhai:
                login_result = lanhai.login(
                    portal=portal["params"], username=username, password=password,
                    acip=acip, timeout=timeout,
                )
            else:
                login_result = generic.login(
                    portal=portal["params"], portal_raw=status.get("portal_raw"),
                    username=username, password=password, timeout=timeout,
                )

            self.log(
                "ok" if login_result["ok"] else "error",
                f"认证响应：{login_result['message']}",
                json.dumps(login_result.get("raw"), ensure_ascii=False) if login_result.get("raw") else "",
            )

            if (
                not login_result["ok"]
                and str(login_result.get("code")) == "-113"
                and page_acip
                and configured_acip != page_acip
            ):
                self.log("warn", f"上次用的 wlanacip 不对，改用门户声明的 {page_acip} 重试一次…")
                login_result = lanhai.login(
                    portal=portal["params"], username=username, password=password,
                    acip=page_acip, timeout=timeout,
                )
                self.log(
                    "ok" if login_result["ok"] else "error",
                    f"认证响应（重试）：{login_result['message']}",
                )
                acip = page_acip

            if not login_result["ok"]:
                return self._finish(
                    {
                        "ok": False,
                        "state": "portal",
                        "code": login_result.get("code"),
                        "message": login_result["message"],
                        "needs_disconnect": login_result.get("needs_disconnect", False),
                        "portal_url": portal["params"].url,
                    }
                )

            time.sleep(0.9)
            self.log("info", "复测联网状态…")
            verify = self.status(timeout=8.0)
            if verify["state"] == "online":
                self.log("ok", f"认证成功，已可上网（总耗时 {int((time.time() - started) * 1000)}ms）")
                return self._finish(
                    {
                        "ok": True,
                        "state": "online",
                        "message": "认证成功，网络已连通",
                        "portal_url": portal["params"].url,
                        "verified": True,
                    }
                )

            self.log("warn", f"Portal 返回成功，但复测仍为「{verify['label']}」")
            return self._finish(
                {
                    "ok": False,
                    "state": verify["state"],
                    "code": "verify-failed",
                    "message": f"认证接口返回成功，但网络仍未连通（{verify['label']}）",
                    "portal_url": portal["params"].url,
                }
            )
        except Exception as exc:
            self.log("error", f"认证流程异常：{type(exc).__name__}: {exc}")
            return self._finish({"ok": False, "state": "error", "message": str(exc)})
        finally:
            self.busy = False

    def _finish(self, result: dict[str, Any]) -> dict[str, Any]:
        result["at"] = time.time()
        self.last_result = result
        return result

    def list_online(self) -> dict[str, Any]:
        """查询当前账号的在线设备。"""
        if not self.portal_cache or not self.portal_cache["params"].origin:
            self.status(timeout=6.0)
        portal = self.portal_cache
        if not portal or not portal["params"].origin:
            return {"ok": False, "message": "未发现 Portal，无法查询在线设备", "devices": []}
        result = lanhai.list_online(portal=portal["params"])
        self.log(
            "info" if result["ok"] else "warn",
            f"查询在线设备：{'发现 ' + str(len(result['devices'])) + ' 台' if result['ok'] else result['message']}",
        )
        return result

    def kick_device(self, mac: str) -> dict[str, Any]:
        """让指定 MAC 下线。"""
        config = self.get_config()
        portal = self.portal_cache
        if not portal or not portal["params"].origin:
            return {"ok": False, "message": "尚未捕获 Portal，无法执行下线"}
        result = lanhai.down_line_user(
            portal=portal["params"],
            username=config.get("username", ""),
            password=config.get("password", ""),
            mac=mac,
        )
        self.log("ok" if result["ok"] else "error", f"设备下线 {mac}：{'成功' if result['ok'] else result['message']}")
        return result

    def logout(self) -> dict[str, Any]:
        """主动注销当前会话（尽力而为）。"""
        config = self.get_config()
        portal = self.portal_cache
        if not portal or not portal["params"].origin:
            return {"ok": False, "message": "尚未捕获 Portal，无法注销"}
        result = lanhai.down_line(portal=portal["params"], username=config.get("username", ""))
        self.log("ok" if result["ok"] else "warn", f"注销请求：{result.get('message') or '已发送'}")
        return result

    def portal_page(self, custom_url: str = "") -> dict[str, Any]:
        url = custom_url or (self.portal_cache or {}).get("params", {}).url
        if not url:
            return {"ok": False, "message": "尚未捕获 Portal 地址"}
        return lanhai.fetch_portal_page(url)
