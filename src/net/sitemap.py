"""校园网站点爬取：把学校服务器上能直接访问的页面、静态资源与接口整理成一张地图。

实现思路很朴素：从入口地址开始做广度优先遍历，只跟随同源链接，
边抓边把 ``href`` / ``src`` / ``action`` / CSS ``url()`` 与脚本里出现的
``/api/...`` 之类路径都登记下来。学校站点的页面基本是静态 HTML + 伪静态
跳转，所以不需要浏览器内核，普通 HTTP 请求就够了。

判断一个地址是「页面」还是「资源」只看扩展名；没有扩展名的一律当页面。
同样的路径会保留第一次拿到的状态码，后续重复出现只累计引用次数，
这样站点地图里不会出现几十条 ``index.html``。
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import urldefrag, urljoin, urlsplit

from .http import get

PAGE_EXTENSIONS = ("", ".html", ".htm", ".shtml", ".asp", ".aspx", ".jsp", ".php", ".do", ".action")
ASSET_EXTENSIONS = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".json", ".xml", ".txt", ".map",
    ".mp4", ".mp3", ".pdf", ".zip", ".rar", ".doc", ".docx", ".xls", ".xlsx",
)
API_PREFIXES = ("/api/", "/selfbase/", "/selfnet/", "/selfuser/", "/cgi-bin/")

_LINK_RE = re.compile(r"""(?:href|src|action)\s*=\s*["']([^"'>\s]+)["']""", re.I)
_CSS_URL_RE = re.compile(r"""url\(\s*["']?([^)"'\s]+)["']?\s*\)""", re.I)
_API_RE = re.compile(r"""["'](/(?:api|selfbase|selfnet|selfuser|cgi-bin)/[A-Za-z0-9_\-/]*)["']""")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SCRIPT_SRC_RE = re.compile(r"""<script[^>]+src\s*=\s*["']([^"']+)["']""", re.I)


def _ext(value: str) -> str:
    """取 URL 路径的小写扩展名，没扩展名返回空串。"""
    path = urlsplit(value).path or ""
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def classify(url: str) -> str:
    """把地址分成 ``page`` / ``asset`` / ``api`` / ``other``。"""
    parts = urlsplit(url)
    path = (parts.path or "/").lower()
    if any(path.startswith(prefix) for prefix in API_PREFIXES):
        return "api"
    if parts.query and not _ext(url):
        return "page"
    ext = _ext(url)
    if ext in ASSET_EXTENSIONS:
        return "asset"
    if ext in PAGE_EXTENSIONS:
        return "page"
    return "other"


def extract_links(html: str, base_url: str) -> dict[str, list[str]]:
    """从一段 HTML 里抽出链接、资源、接口与脚本文件。"""
    text = html or ""
    found: dict[str, list[str]] = {"page": [], "asset": [], "api": [], "other": []}

    def add(raw: str) -> None:
        raw = (raw or "").strip()
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            return
        target = urldefrag(urljoin(base_url, raw))[0]
        kind = classify(target)
        if target not in found[kind]:
            found[kind].append(target)

    for match in _LINK_RE.finditer(text):
        add(match.group(1))
    for match in _CSS_URL_RE.finditer(text):
        add(match.group(1))
    for match in _API_RE.finditer(text):
        add(match.group(1))
    for match in _SCRIPT_SRC_RE.finditer(text):
        add(match.group(1))
    return found


def _title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    if not match:
        return ""
    return " ".join(match.group(1).split())[:120]


def repair_text(text: str) -> str:
    """把被当成 Latin-1 解码的 UTF-8 中文还原回来。

    学校站点是 UTF-8，但响应头里没有 ``charset``，``requests`` 会退化成
    ISO-8859-1，标题就变成一串乱码。这里只在「全部字符都落在 0x00–0xFF」
    时才尝试重解码，正常文本不会被误伤。
    """
    value = text or ""
    if not value or any(ord(char) > 255 for char in value):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def crawl(
    base: str,
    *,
    max_pages: int = 120,
    max_depth: int = 3,
    timeout: float = 8.0,
    scan_scripts: bool = True,
    max_scripts: int = 40,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """从 ``base`` 开始抓取同源页面，返回站点地图。"""
    started = time.perf_counter()
    entry = (base or "").strip()
    if not entry:
        return {"ok": False, "message": "缺少入口地址"}
    parts = urlsplit(entry)
    if not parts.scheme:
        entry = "http://" + entry
        parts = urlsplit(entry)
    origin = f"{parts.scheme}://{parts.netloc}"

    pages: dict[str, dict[str, Any]] = {}
    assets: dict[str, dict[str, Any]] = {}
    apis: dict[str, dict[str, Any]] = {}
    queue: list[tuple[str, int]] = [(entry, 0)]
    visited: set[str] = set()
    errors: list[dict[str, str]] = []

    while queue and len(pages) < max_pages:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if on_progress is not None:
            try:
                on_progress(url, len(pages), len(visited))
            except Exception:
                pass
        result = get(url, timeout=timeout, max_body=400000)
        if not result.ok:
            errors.append({"url": url, "error": result.error or f"HTTP {result.status}"})
            continue

        kind = classify(url)
        html = repair_text(result.text or "")
        size = len(html)
        if kind == "api":
            apis[url] = {"url": url, "status": result.status, "size": size}
        else:
            pages[url] = {
                "url": url,
                "status": result.status,
                "size": size,
                "content_type": result.content_type,
                "title": _title(html),
                "depth": depth,
            }
        if kind != "page":
            continue
        if depth >= max_depth:
            continue

        links = extract_links(html, url)
        for target in links["api"]:
            apis.setdefault(target, {"url": target, "status": 0, "size": 0})
        for target in links["asset"]:
            if target in assets:
                assets[target]["refs"] = int(assets[target].get("refs", 1)) + 1
                continue
            assets[target] = {"url": target, "ext": _ext(target), "refs": 1, "from": url}
        for target in links["page"]:
            if target in visited:
                continue
            if urlsplit(target).netloc != parts.netloc:
                continue
            queue.append((target, depth + 1))

    # 页面里未必写死所有入口：脚本里常拼出 ``xxx.html?id=`` 这类地址，
    # 所以再扫一遍同源 JS，把藏在脚本里的页面与接口捞出来（只登记不递归）。
    discovered: dict[str, dict[str, Any]] = {}
    if scan_scripts:
        count = 0
        for asset in list(assets.values()):
            if count >= max_scripts:
                break
            if (asset.get("ext") or "") != ".js":
                continue
            count += 1
            result = get(asset["url"], timeout=timeout, max_body=600000)
            if not result.ok:
                continue
            found = extract_links(repair_text(result.text or ""), asset["url"])
            for target in found["api"]:
                apis.setdefault(target, {"url": target, "status": 0, "size": 0})
            for target in found["page"]:
                if target in pages or target in visited:
                    continue
                if urlsplit(target).netloc != parts.netloc:
                    continue
                discovered.setdefault(target, {"url": target, "from": asset["url"]})

    elapsed = round(time.perf_counter() - started, 2)
    return {
        "ok": True,
        "origin": origin,
        "entry": entry,
        "elapsed": elapsed,
        "pages": sorted(pages.values(), key=lambda item: (item["depth"], item["url"])),
        "assets": sorted(assets.values(), key=lambda item: item["url"]),
        "apis": sorted(apis.values(), key=lambda item: item["url"]),
        "discovered": sorted(discovered.values(), key=lambda item: item["url"]),
        "errors": errors,
        "stats": {
            "pages": len(pages),
            "assets": len(assets),
            "apis": len(apis),
            "errors": len(errors),
            "visited": len(visited),
            "discovered": len(discovered),
        },
    }
