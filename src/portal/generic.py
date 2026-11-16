"""通用 Captive Portal 驱动：把页面上的第一个登录表单原样提交。

用于非蓝海卓越的部署（Dr.COM / 锐捷等常见变体多数也是普通表单 POST）。
"""


from __future__ import annotations

import re
from typing import Any

from ..net.http import post_form
from .detect import PortalParams, parse_forms

NAME = "通用表单 Portal"

USERNAME_HINTS = ["username", "user", "name", "account", "loginname", "userid", "wlanusername", "uname", "uid"]
PASSWORD_HINTS = ["password", "passwd", "pwd", "userpasswd", "pass", "userpwd", "secret"]


def _guess(inputs: list[dict], hints: list[str], type_match: str = "") -> dict | None:
    for hint in hints:
        for item in inputs:
            if (item["name"] or item["id"]).lower() == hint:
                return item
    for hint in hints:
        for item in inputs:
            if hint in (item["name"] or item["id"]).lower():
                return item
    if type_match:
        for item in inputs:
            if item["type"] == type_match:
                return item
    return None


def login(
    *,
    portal: PortalParams,
    portal_raw: dict[str, Any] | None,
    username: str,
    password: str,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """解析 Portal 页面上的表单并提交。"""
    html = (portal_raw or {}).get("html", "") or ""
    forms = parse_forms(html)
    if not forms:
        return {
            "ok": False,
            "code": "no-form",
            "message": "Portal 页面上没有找到登录表单，请改用「蓝海卓越」驱动或手动在浏览器登录",
        }

    form = forms[0]
    inputs = form["inputs"]
    user_field = _guess(inputs, USERNAME_HINTS) or (inputs[0] if inputs else None)
    pass_field = _guess(inputs, PASSWORD_HINTS, "password") or (inputs[1] if len(inputs) > 1 else None)
    if not user_field or not pass_field:
        return {
            "ok": False,
            "code": "no-field",
            "message": "无法识别账号/密码输入框",
        }

    payload = {}
    for item in inputs:
        if not item["name"]:
            continue
        if item["checked"] or item["type"] in ("checkbox", "radio"):
            payload[item["name"]] = item["value"] or "on"
        else:
            payload[item["name"]] = item["value"] or ""

    payload[user_field["name"]] = username
    payload[pass_field["name"]] = password

    action = form["action"]
    if action:
        from urllib.parse import urljoin

        target = urljoin(portal.url or f"{portal.origin}/", action)
    else:
        target = portal.url or f"{portal.origin}/"

    result = post_form(target, payload, timeout=timeout)
    if not result.ok:
        return {
            "ok": False,
            "code": "network",
            "message": f"表单提交失败：{result.error}",
            "raw": None,
        }

    text = result.text or ""
    negative = bool(
        re.search(
            r"(密码错误|用户名或密码|认证失败|password\s*(is\s*)?(wrong|incorrect)|login\s*failed|error_code\"?\s*:\s*-(?!0))",
            text,
            re.I,
        )
    )

    positive = bool(re.search(r"(认证成功|登录成功|success|已连接|logout|注销|welcome)", text, re.I)) or result.status == 200
    ok = positive and not negative

    return {
        "ok": ok,
        "code": 0 if ok else "form-rejected",
        "message": "表单已提交（需复测确认）" if ok else "Portal 返回了疑似失败的页面，请检查账号密码",
        "raw": {
            "action": target,
            "status": result.status,
            "excerpt": text[:400],
        },
    }
