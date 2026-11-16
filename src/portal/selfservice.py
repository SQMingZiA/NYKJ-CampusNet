"""学校自助系统（http://10.100.2.2/）的原生客户端。

**为什么需要它**：门户的认证接口（``/api/selfbase/*``）只能查「在线设备」和「会话计费」，
而**套餐、到期时间、余额、账单、上网明细、修改密码**这些都在**自助系统**里。
自助系统是一套独立的 ThinkPHP 应用，用自己的 JWT 鉴权：

1. ``POST /api/selfbase/login``  ``{username, password, query}``
   → ``{errcode: 0, data: {accessToken, user_id, username}}``
   ``query`` 就是门户那串 ``wlanuserip=…&clientmac=…``；
2. 之后所有接口都带请求头 ``Authorization: <accessToken>``（JWT，有效期约 2 小时）。

实测（本校门户 ``10.100.2.2``）确认可用：

===========================  ================================================
``/api/selfuser/accountInfo``  账号、状态、余额、**套餐名**、**到期时间**、在线信息
``/api/selfnet/flowInfo``      套餐总量 / 已用 / 剩余 / 起止时间
``/api/selfnet/orderInfo``     订单列表
``/api/selfnet/billList``      账单流水
``/api/selfnet/netLog``       上网明细（``{d: 'YYYY-MM-DD'}``）
``/api/selfnet/getOnlineDevices``  在线设备
``/api/selfnet/downOneDevice``    下线指定设备（``{session_id}``）
``/api/selfuser/modPwd``      修改密码（``{newPassword, captcha}``）
``/api/selfbase/captcha``     图形验证码图片
``/api/selfuser/getUserPause`` / ``setUserPause`` / ``restoreUserPause``
``/api/selfuser/getNotice`` / ``getInvoice`` / ``getProductIntroByUserId``
===========================  ================================================
"""

from __future__ import annotations

import time
from typing import Any

from ..net.http import post_form


TOKEN_TTL_SECONDS = 60 * 90


class SelfService:
    """自助系统客户端。一个实例持有一次登录的 token。"""

    def __init__(self, *, origin: str, query: str, username: str, password: str,
                 timeout: float = 12.0) -> None:
        self.origin = (origin or "http://10.100.2.2").rstrip("/")
        self.query = query or ""
        self.username = username or ""
        self.password = password or ""
        self.timeout = timeout
        self.token = ""
        self.user_id = ""
        self.token_at = 0.0
        self.last_error = ""


    def _headers(self, auth: bool = True) -> dict[str, str]:
        headers = {"Accept": "application/json, text/plain, */*"}
        if auth and self.token:
            headers["Authorization"] = self.token
        return headers

    def login(self, force: bool = False) -> dict[str, Any]:
        """登录拿 token（默认复用未过期的）。"""
        if not force and self.token and time.time() - self.token_at < TOKEN_TTL_SECONDS:
            return {"ok": True, "cached": True, "user_id": self.user_id}
        if not self.username or not self.password:
            return {"ok": False, "message": "还没有配置账号密码"}

        result = post_form(
            f"{self.origin}/api/selfbase/login",
            {"username": self.username, "password": self.password, "query": self.query},
            timeout=self.timeout,
        )
        data = result.json() if result.ok else None
        if not isinstance(data, dict):
            self.last_error = f"自助系统登录失败：HTTP {result.status} {result.error}"
            return {"ok": False, "message": self.last_error, "raw": (result.text or "")[:200]}
        if data.get("errcode") != 0:
            self.last_error = str(data.get("errmsg") or "登录被拒绝")
            return {"ok": False, "message": self.last_error, "raw": data}

        info = data.get("data") or {}
        self.token = str(info.get("accessToken") or "")
        self.user_id = str(info.get("user_id") or "")
        self.token_at = time.time()
        if not self.token:
            return {"ok": False, "message": "登录返回里没有 accessToken", "raw": data}
        return {"ok": True, "user_id": self.user_id, "username": info.get("username")}

    def _call(self, path: str, data: dict[str, Any] | None = None, *,
              retry_on_expired: bool = True) -> dict[str, Any]:
        """调一个自助接口，返回统一结构 ``{ok, data, message}``。"""
        ready = self.login()
        if not ready.get("ok"):
            return {"ok": False, "message": ready.get("message"), "data": None}

        result = post_form(
            f"{self.origin}{path}",
            data or {},
            timeout=self.timeout,
            headers=self._headers(),
        )
        payload = result.json() if result.ok else None
        if not isinstance(payload, dict):
            return {"ok": False,
                    "message": f"HTTP {result.status} {result.error}",
                    "data": None, "raw": (result.text or "")[:200]}

        code = payload.get("errcode")
        if code == 0:
            return {"ok": True, "data": payload.get("data"), "raw": payload}

        message = str(payload.get("errmsg") or payload.get("msg") or f"errcode={code}")
        if retry_on_expired and any(word in message for word in ("登录", "token", "Token", "过期", "认证")):
            self.token = ""
            self.token_at = 0.0
            if self.login(force=True).get("ok"):
                return self._call(path, data, retry_on_expired=False)
        return {"ok": False, "message": message, "data": None, "raw": payload}

    def account_info(self) -> dict[str, Any]:
        """账号信息：状态、余额、套餐名、到期时间、在线记录。"""
        return self._call("/api/selfuser/accountInfo")

    def password_plaintext(self) -> dict[str, Any]:
        """读取学校侧保存的明文密码。

        ⚠ 这个接口存在本身说明**学校把密码明文存放在服务端**。我们只在
        「开发者模式 + 已登录」时调用，用途是帮用户找回自己忘掉的密码；
        返回内容**绝不写日志、不写配置文件**，由调用方打码展示。
        """
        return self._call("/api/selfbase/getPasswordPlaintext")

    def flow_info(self) -> dict[str, Any]:
        """套餐流量：总量 / 已用 / 剩余 / 起止时间。"""
        return self._call("/api/selfnet/flowInfo")

    def orders(self) -> dict[str, Any]:
        return self._call("/api/selfnet/orderInfo")

    def bills(self) -> dict[str, Any]:
        return self._call("/api/selfnet/billList")

    def net_log(self, date: str = "") -> dict[str, Any]:
        """上网明细；``date`` 形如 ``2026-10-10``，留空取当天。"""
        payload = {"d": date} if date else {}
        return self._call("/api/selfnet/netLog", payload)

    def online_devices(self) -> dict[str, Any]:
        return self._call("/api/selfnet/getOnlineDevices")

    def pause_state(self) -> dict[str, Any]:
        return self._call("/api/selfuser/getUserPause")

    def notices(self) -> dict[str, Any]:
        return self._call("/api/selfuser/getNotice")

    def invoices(self) -> dict[str, Any]:
        return self._call("/api/selfuser/getInvoice")

    def product_intro(self) -> dict[str, Any]:
        return self._call("/api/selfuser/getProductIntroByUserId")

    def work_order_types(self) -> dict[str, Any]:
        """报修工单可选类型。"""
        return self._call("/api/selfuser/getWorkOrderType")

    def rooms(self) -> dict[str, Any]:
        """可申请的宿舍列表（换宿舍用）。"""
        return self._call("/api/selfuser/getRoomList")

    def repairs(self) -> dict[str, Any]:
        """报修记录。"""
        return self._call("/api/selfuser/getRepair")

    def bind_info(self) -> dict[str, Any]:
        """绑定信息（微信 / 运营商等）。"""
        return self._call("/api/selfuser/getBindProxyInfo")

    def bottom_word(self) -> dict[str, Any]:
        """门户页脚公告（客服电话、缴费提示之类）。"""
        return self._call("/api/selfbase/getBottomWord")

    def down_device(self, session_id: str) -> dict[str, Any]:
        """把指定会话的设备踢下线。"""
        if not session_id:
            return {"ok": False, "message": "缺少 session_id"}
        return self._call("/api/selfnet/downOneDevice", {"session_id": session_id})

    def change_password(self, new_password: str, captcha: str) -> dict[str, Any]:
        """修改密码（需要图形验证码）。"""
        if not new_password:
            return {"ok": False, "message": "新密码不能为空"}
        if not captcha:
            return {"ok": False, "message": "请输入验证码"}
        return self._call("/api/selfuser/modPwd", {"newPassword": new_password, "captcha": captcha})

    def set_pause(self, **extra: Any) -> dict[str, Any]:
        """申请暂停（流程细节随部署不同，参数透传）。"""
        return self._call("/api/selfuser/setUserPause", dict(extra))

    def restore_pause(self, **extra: Any) -> dict[str, Any]:
        """恢复（取消暂停）。"""
        return self._call("/api/selfuser/restoreUserPause", dict(extra))

    def captcha_url(self) -> str:
        return f"{self.origin}/api/selfbase/captcha?t={int(time.time() * 1000)}"

    def captcha_image(self) -> bytes:
        """取图形验证码的原始图片字节（给界面直接显示）。"""
        url = self.captcha_url()
        try:
            import requests
            resp = requests.get(url, timeout=self.timeout, verify=False, headers=self._headers())
            return resp.content or b""
        except Exception:
            pass
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return resp.read() or b""
        except Exception:
            return b""
