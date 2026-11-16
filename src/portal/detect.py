"""Portal 指纹识别与网关参数解析。

已知目标：蓝海卓越（Lanhai / 派网 Panabit 网关）Portal —— 南阳科技校园网。
同时保留 Dr.COM / 锐捷 / 深澜 等常见系统的识别能力，便于移植。
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

FLAVORS: list[dict] = [
    {
        "id": "lanhai",
        "name": "蓝海卓越 Portal（南阳科技 / Panabit 网关）",
        "patterns": ["/api/selfbase/", "templates/auth/", "userpasswd", "panabit"],
        "notes": [
            "登录接口 /api/selfbase/login3",
            "下线接口 /api/selfbase/downLineUser",
        ],
    },
    {
        "id": "drcom",
        "name": "Dr.COM ePortal",
        "patterns": ["drcom", "eportal"],
        "notes": [
            "登录接口通常为 /eportal/portal/login",
        ],
    },
    {
        "id": "ruijie",
        "name": "锐捷 ePortal",
        "patterns": ["ruijie", "srun_portal", "锐捷"],
        "notes": [],
    },
    {
        "id": "srun",
        "name": "深澜 srun",
        "patterns": ["srun", "深澜"],
        "notes": [],
    },
    {
        "id": "h3c",
        "name": "H3C Portal",
        "patterns": ["h3c", "inode"],
        "notes": [],
    },
]


@dataclass
class PortalParams:
    """从 Portal URL 中解析出的网关下发参数。"""

    url: str = ""
    origin: str = ""
    host: str = ""
    path: str = "/"
    query: dict[str, str] = field(default_factory=dict)
    query_string: str = ""
    known: dict[str, str] = field(default_factory=dict)

    @property
    def wlanuserip(self) -> str:
        return self.known.get("wlanuserip", "")

    @property
    def wlanacname(self) -> str:
        return self.known.get("wlanacname", "")

    @property
    def wlanusermac(self) -> str:
        return self.known.get("wlanusermac", "")

    @property
    def wlanacip(self) -> str:
        return self.known.get("wlanacip", "")


@dataclass
class Detection:
    flavor: str
    name: str
    candidates: list[str]
    notes: list[str]
    params: PortalParams


def parse_portal_params(portal_url: str) -> PortalParams:
    """解析 Portal 地址与其中的认证参数。"""
    params = PortalParams(url=portal_url or "")
    if not portal_url:
        return params
    try:
        parsed = urllib.parse.urlsplit(portal_url)
    except ValueError:
        return params

    params.origin = f"{parsed.scheme}://{parsed.netloc}"
    params.host = parsed.netloc
    params.path = parsed.path or "/"
    params.query_string = parsed.query
    params.query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}

    lowered = {k.lower(): v for k, v in params.query.items()}

    def pick(*names: str) -> str:
        for name in names:
            if name.lower() in lowered:
                return lowered[name.lower()]
        return ""

    params.known = {
        "wlanuserip": pick("wlanuserip", "clientip", "userip", "ip"),
        "wlanusermac": pick("wlanusermac", "clientmac", "usermac", "mac"),
        "wlanacname": pick("wlanacname", "nasname", "acname"),
        "wlanacip": pick("wlanacip", "nasip", "paip", "acip"),
        "ssid": pick("ssid", "wlanssid"),
    }
    return params


def detect(portal_url: str = "", html: str = "") -> Detection:
    """识别 Portal 类型并解析参数。"""
    haystack = f"{portal_url or ''}\n{html or ''}"
    matched = []
    for flavor in FLAVORS:
        if any(re.search(pattern, haystack, re.I) for pattern in flavor["patterns"]):
            matched.append(flavor)

    if matched:
        first = matched[0]
        return Detection(
            flavor=first["id"],
            name=first["name"],
            candidates=[m["id"] for m in matched],
            notes=list(first["notes"]),
            params=parse_portal_params(portal_url),
        )
    return Detection(
        flavor="generic",
        name="通用 Captive Portal",
        candidates=[],
        notes=[],
        params=parse_portal_params(portal_url),
    )


_ATTR_RE = re.compile(
    r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("([^"]*)"|'([^']*)'|([^\s"'>]+))"""
)

_INPUT_RE = re.compile(r"<(input|select|textarea|button)\b([^>]*?)/?>", re.I)
_FORM_RE = re.compile(r"<form\b([^>]*)>([\s\S]*?)</form>", re.I)


def _parse_attrs(text: str) -> dict[str, str]:
    out = {}
    for match in _ATTR_RE.finditer(text):
        out[match.group(1).lower()] = match.group(3) if match.group(3) is not None else (
            match.group(4) if match.group(4) is not None else match.group(5) or ""
        )
    for bare in re.finditer(r"\b(checked|selected|disabled|readonly)\b", text, re.I):
        out.setdefault(bare.group(1).lower(), "")
    return out


def parse_forms(html: str) -> list[dict]:
    """极简 HTML 表单解析，返回 [{action, method, inputs:[{name,type,value,checked}]}]。"""
    forms = []
    for form_match in _FORM_RE.finditer(html or ""):
        attrs = _parse_attrs(form_match.group(1))
        inputs = []
        for input_match in _INPUT_RE.finditer(form_match.group(2)):
            tag = input_match.group(1).lower()
            a = _parse_attrs(input_match.group(2))
            input_type = (a.get("type") or ("select" if tag == "select" else "text")).lower()
            if input_type in {"submit", "button", "image", "reset"}:
                continue
            inputs.append(
                {
                    "name": a.get("name", ""),
                    "id": a.get("id", ""),
                    "type": input_type,
                    "value": a.get("value", ""),
                    "checked": "checked" in a,
                }
            )
        forms.append(
            {
                "action": attrs.get("action", ""),
                "method": (attrs.get("method") or "GET").upper(),
                "inputs": inputs,
            }
        )
    return forms
