"""HTTP 小工具：统一的超时、重定向策略与 Cookie 处理。

优先用 ``requests``（若未安装则回退到标准库 urllib，保证程序一定能跑）。
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 CampusNet/1.0"
)

try:
    import requests

    HAS_REQUESTS = True
except Exception:
    requests = None
    HAS_REQUESTS = False


@dataclass
class HttpResult:
    ok: bool
    status: int = 0
    text: str = ""
    url: str = ""
    location: str = ""
    content_type: str = ""
    server: str = ""
    elapsed_ms: int = 0
    error: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self):
        try:
            return json.loads((self.text or "").strip())
        except (json.JSONDecodeError, TypeError):
            return None


def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def get(url: str,
        *,
        timeout: float = 8.0,
        allow_redirects: bool = False,
        headers: dict[str, str] | None = None,
        max_body: int = 400000) -> HttpResult:
    """发起 GET 请求。默认不自动跟随重定向（用于捕获 Portal 跳转）。"""
    started = __import__("time").perf_counter()

    hdrs = {
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept": "*/*",
        **(headers or {}),
    }

    if HAS_REQUESTS:
        try:
            resp = requests.get(url, headers=hdrs, timeout=timeout, allow_redirects=allow_redirects, verify=False)
            elapsed = int((__import__("time").perf_counter() - started) * 1000)
            return HttpResult(
                ok=True,
                status=resp.status_code,
                text=(resp.text or "")[:max_body],
                url=resp.url,
                location=resp.headers.get("Location", "") or "",
                content_type=resp.headers.get("Content-Type", "") or "",
                server=resp.headers.get("Server", "") or "",
                elapsed_ms=elapsed,
                headers={k: v for k, v in resp.headers.items()},
            )
        except Exception as exc:
            elapsed = int((__import__("time").perf_counter() - started) * 1000)
            return HttpResult(ok=False, error=f"{type(exc).__name__}: {exc}", elapsed_ms=elapsed, url=url)
    else:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=_make_ssl_context()),
            _NoRedirect() if not allow_redirects else urllib.request.HTTPRedirectHandler(),
        )
        request = urllib.request.Request(url, headers=hdrs)
        try:
            with opener.open(request, timeout=timeout) as resp:
                raw = resp.read(max_body)
                elapsed = int((__import__("time").perf_counter() - started) * 1000)
                charset = resp.headers.get_content_charset() or "utf-8"
                return HttpResult(
                    ok=True,
                    status=resp.status,
                    text=raw.decode(charset, errors="replace"),
                    url=resp.geturl(),
                    location=resp.headers.get("Location", "") or "",
                    content_type=resp.headers.get("Content-Type", "") or "",
                    server=resp.headers.get("Server", "") or "",
                    elapsed_ms=elapsed,
                    headers=dict(resp.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read(max_body) if hasattr(exc, "read") else b""
            elapsed = int((__import__("time").perf_counter() - started) * 1000)
            return HttpResult(
                ok=True,
                status=exc.code,
                text=raw.decode("utf-8", errors="replace"),
                url=url,
                location=(exc.headers.get("Location", "") or "") if exc.headers else "",
                content_type=(exc.headers.get("Content-Type", "") if exc.headers else "") or "",
                server=(exc.headers.get("Server", "") if exc.headers else "") or "",
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((__import__("time").perf_counter() - started) * 1000)
            return HttpResult(ok=False, error=f"{type(exc).__name__}: {exc}", elapsed_ms=elapsed, url=url)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止 urllib 自动跟随 3xx，便于拿到 Location。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_form(url: str, data: dict[str, str],
              *,
              timeout: float = 10.0,
              headers: dict[str, str] | None = None) -> HttpResult:
    """提交表单（application/x-www-form-urlencoded），自动跟随重定向。"""
    hdrs = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    body = urllib.parse.urlencode(data).encode("utf-8")

    if HAS_REQUESTS:
        started = __import__("time").perf_counter()
        try:
            resp = requests.post(url, data=data, headers={"User-Agent": USER_AGENT, **hdrs}, timeout=timeout, verify=False)
            elapsed = int((__import__("time").perf_counter() - started) * 1000)
            return HttpResult(
                ok=True,
                status=resp.status_code,
                text=resp.text or "",
                url=resp.url,
                elapsed_ms=elapsed,
                headers={k: v for k, v in resp.headers.items()},
            )
        except Exception as exc:
            return HttpResult(ok=False, error=f"{type(exc).__name__}: {exc}", url=url)
    else:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=_make_ssl_context()),
        )
        request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, **hdrs})
        try:
            with opener.open(request, timeout=timeout) as resp:
                return HttpResult(
                    ok=True,
                    status=resp.status,
                    text=resp.read(200000).decode(resp.headers.get_content_charset() or "utf-8", errors="replace"),
                    url=resp.geturl(),
                )
        except urllib.error.HTTPError as exc:
            return HttpResult(
                ok=True,
                status=exc.code,
                text=exc.read(200000).decode("utf-8", errors="replace"),
                url=url,
            )
        except Exception as exc:
            return HttpResult(ok=False, error=f"{type(exc).__name__}: {exc}", url=url)
