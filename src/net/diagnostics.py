"""网络诊断：延迟、丢包、DNS、路由、下载测速。"""

from __future__ import annotations

import re
import time
from typing import Any

from ..core.winutil import run_cmd
from .probe import dns_probe, tcp_ping

_TIME_RE = re.compile(r"(?:time|时间)\s*[=<]\s*(\d+)\s*ms", re.I)
_LOSS_RE = re.compile(r"\((\d+)%\s*(?:loss|丢失)", re.I)
_AVG_RE = re.compile(r"(?:Average|平均)\s*=\s*(\d+)\s*ms", re.I)
_TTL_RE = re.compile(r"TTL=(\d+)", re.I)


TARGETS = [
    {"id": "gateway", "name": "默认网关", "host": ""},
    {"id": "dns", "name": "校园 DNS", "host": "211.138.24.66"},
    {"id": "baidu", "name": "百度", "host": "www.baidu.com"},
    {"id": "qq", "name": "腾讯", "host": "www.qq.com"},
    {"id": "aliyun", "name": "阿里云 DNS", "host": "223.5.5.5"},
    {"id": "edu", "name": "教育网", "host": "www.edu.cn"},
]


def ping(host: str, count: int = 4, timeout: float = 15.0) -> dict[str, Any]:
    """Ping 一个主机，解析出延迟与丢包率。"""
    if not host:
        return {"host": host, "ok": False, "message": "未指定主机"}
    result = run_cmd(f"chcp 65001>nul & ping -n {int(count)} -w 1500 {host}", timeout=timeout)
    text = result.stdout or ""
    times = [int(m.group(1)) for m in _TIME_RE.finditer(text)]
    loss_match = _LOSS_RE.search(text)
    avg_match = _AVG_RE.search(text)
    ttl_match = _TTL_RE.search(text)

    return {
        "host": host,
        "ok": bool(times),
        "count": count,
        "received": len(times),
        "loss_percent": (
            int(loss_match.group(1))
            if loss_match
            else round((count - len(times)) / count * 100) if times else 100
        ),
        "min_ms": min(times) if times else None,
        "max_ms": max(times) if times else None,
        "avg_ms": int(avg_match.group(1)) if avg_match else round(sum(times) / len(times)) if times else None,
        "ttl": int(ttl_match.group(1)) if ttl_match else None,
        "samples": times,
        "raw": text[:1200],
    }


def dns_benchmark(names: list[str] | None = None) -> dict[str, Any]:
    """DNS 解析耗时测试。"""
    names = names or ["www.baidu.com", "www.qq.com", "www.163.com"]
    results = []
    for name in names:
        started = time.perf_counter()
        parsed = dns_probe(name)
        results.append({
            "name": name,
            "ok": parsed["ok"],
            "ms": int((time.perf_counter() - started) * 1000),
            "addresses": parsed["addresses"][:3],
        })
    servers = _system_dns_servers()
    return {"servers": servers, "results": results}


def _system_dns_servers() -> list[str]:
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8") as fp:
            return [line.split()[1] for line in fp if line.startswith("nameserver")]
    except OSError:
        pass
    result = run_cmd("chcp 65001>nul & ipconfig /all", timeout=12)
    servers = []
    for match in re.finditer(r"(?:DNS Servers|DNS 服务器)[^\d]*((?:\d{1,3}(?:\.\d{1,3}){3}[\s,]*)+)", result.stdout or ""):
        servers += re.findall(r"\d{1,3}(?:\.\d{1,3}){3}", match.group(1))
    return list(dict.fromkeys(servers))[:6]


def routes() -> dict[str, Any]:
    """默认路由表。"""
    result = run_cmd("chcp 65001>nul & route print -4", timeout=12)
    defaults = []
    for line in (result.stdout or "").splitlines():
        if not line.strip().startswith("0.0.0.0"):
            continue
        parts = line.split()
        if len(parts) >= 5:
            defaults.append({
                "destination": parts[0],
                "mask": parts[1],
                "gateway": parts[2],
                "interface": parts[3],
                "metric": parts[4],
            })

    return {"default_routes": defaults, "raw": (result.stdout or "")[:2000]}


DOWNLOAD_TARGETS: list[tuple[str, str]] = [
    ("清华镜像", "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-releases/22.04/ubuntu-22.04.5-desktop-amd64.iso"),
    ("Cloudflare", "https://speed.cloudflare.com/__down?bytes=104857600"),
    ("中科大镜像", "https://mirrors.ustc.edu.cn/ubuntu-releases/22.04/ubuntu-22.04.5-desktop-amd64.iso"),
    ("华中科大镜像", "https://mirrors.hust.edu.cn/ubuntu-releases/22.04/ubuntu-22.04.5-desktop-amd64.iso"),
    ("阿里云镜像", "https://mirrors.aliyun.com/ubuntu-releases/22.04/ubuntu-22.04.5-desktop-amd64.iso"),
    ("网易镜像", "https://mirrors.163.com/ubuntu-releases/22.04/ubuntu-22.04.5-desktop-amd64.iso"),
]

UPLOAD_TARGETS: list[tuple[str, str]] = [
    ("Cloudflare", "https://speed.cloudflare.com/__up"),
    ("httpbingo", "https://httpbingo.org/post"),
]

LATENCY_TARGETS: list[tuple[str, str, int]] = [
    ("阿里 DNS", "223.5.5.5", 53),
    ("114 DNS", "114.114.114.114", 53),
    ("中科大镜像", "mirrors.ustc.edu.cn", 443),
    ("腾讯", "www.qq.com", 443),
]


_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
}

_SLOW_START_BYTES = 512 * 1024

_MIN_VALID_BYTES = 512 * 1024


def _payload(label: str, url: str, size: int, seconds: float, *, steady: tuple[int, float] | None = None, partial: bool = False, note: str = "") -> dict[str, Any]:
    seconds = max(0.05, seconds)
    result = {
        "ok": size > 0,
        "label": label,
        "url": url,
        "bytes": size,
        "seconds": round(seconds, 2),
        "mbps": round(size * 8 / seconds / 1000000.0, 2),
        "mb_per_sec": round(size / seconds / 1048576, 2),
    }
    if steady is not None:
        steady_bytes, steady_seconds = steady
        steady_seconds = max(0.05, steady_seconds)
        if steady_bytes > 0 and steady_seconds > 0.15:
            result["steady_mbps"] = round(steady_bytes * 8 / steady_seconds / 1000000.0, 2)
            result["steady_mb_per_sec"] = round(steady_bytes / steady_seconds / 1048576, 2)
            result["mbps"] = result["steady_mbps"]
            result["mb_per_sec"] = result["steady_mb_per_sec"]
            result["measured"] = "steady"
    result.setdefault("measured", "overall")
    if partial:
        result["partial"] = True
    if note:
        result["note"] = note
    return result


def _requests():
    try:
        import requests
    except Exception:
        return None
    return requests


def download_speed(duration_ms: int = 8000, url: str = "") -> dict[str, Any]:
    """下载测速：依次尝试多个镜像，取第一个成功的。

返回里带 ``tried``，便于在界面上说明「哪些源不通」。
"""
    session_requests = _requests()
    targets = [("自定义", url)] if url else DOWNLOAD_TARGETS
    tried = []

    if session_requests is None:
        return {"ok": False, "message": "缺少 requests，无法测速", "tried": tried}

    # 总时长兜底：个别源可能既不报错也不来数据（被网关拦截），
    # 不给上限就会出现「测速一直转」的观感。
    deadline = time.perf_counter() + duration_ms / 1000 * 2 + 15

    for label, target in targets:
        if time.perf_counter() > deadline:
            tried.append({"label": label, "url": target, "error": "超过本次测速总时长，已跳过"})
            break
        started = time.perf_counter()
        skip_mark = None
        try:
            bytes_read = 0
            with session_requests.get(target, stream=True, timeout=(6, 12), verify=False, headers=_UA) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=262144):
                    if not chunk:
                        continue
                    bytes_read += len(chunk)
                    if skip_mark is None and bytes_read >= _SLOW_START_BYTES:
                        skip_mark = time.perf_counter()
                    if (time.perf_counter() - started) * 1000 >= duration_ms:
                        break
            if bytes_read < _MIN_VALID_BYTES:
                tried.append({
                    "label": label,
                    "url": target,
                    "error": f"有效数据不足（只收到 {bytes_read} 字节）",
                })
                continue
            now = time.perf_counter()
            steady = None
            if skip_mark is not None:
                steady = (bytes_read - _SLOW_START_BYTES, now - skip_mark)
            payload = _payload(label, target, bytes_read, now - started, steady=steady)
            payload["tried"] = tried
            return payload
        except Exception as exc:
            tried.append({
                "label": label,
                "url": target,
                "error": f"{type(exc).__name__}: {str(exc)[:80]}",
            })
    return {"ok": False, "message": "所有下载测速源都不可用", "tried": tried}


def upload_speed(duration_ms: int = 6000, payload_kb: int = 4096) -> dict[str, Any]:
    """上传测速：POST 一段数据到接受任意体的端点。

只统计**发送阶段**的耗时 —— 服务端回包可能很慢（实测 Cloudflare 收完 8MB 后
要等十几秒才回响应），把它算进分母会严重低估上行带宽。
"""
    session_requests = _requests()
    tried = []
    if session_requests is None:
        return {"ok": False, "message": "缺少 requests，无法测速", "tried": tried}

    chunk = b"0" * 65536
    total_limit = max(256 * 1024, payload_kb * 1024)

    for label, target in UPLOAD_TARGETS:
        started = time.perf_counter()
        state = {"sent": 0, "skip_mark": None, "send_done": None}

        def generator():
            while state["sent"] < total_limit and (time.perf_counter() - started) * 1000 < duration_ms:
                state["sent"] += len(chunk)
                if state["skip_mark"] is None and state["sent"] >= _SLOW_START_BYTES:
                    state["skip_mark"] = time.perf_counter()
                yield chunk
            state["send_done"] = time.perf_counter()

        response_error = ""
        try:
            resp = session_requests.post(target, data=generator(), timeout=(6, 8), verify=False, headers={**_UA, "Content-Type": "application/octet-stream"})
            if resp.status_code >= 400:
                tried.append({
                    "label": label,
                    "url": target,
                    "error": f"HTTP {resp.status_code}",
                })
                continue
        except Exception as exc:
            if state["send_done"] is None:
                if state["sent"] <= 0:
                    tried.append({
                        "label": label,
                        "url": target,
                        "error": f"{type(exc).__name__}: {str(exc)[:80]}",
                    })
                    continue
                state["send_done"] = time.perf_counter()
            response_error = f"{type(exc).__name__}"
        sent = int(state["sent"])
        if sent < _MIN_VALID_BYTES:
            tried.append({
                "label": label,
                "url": target,
                "error": f"有效数据不足（只发出 {sent} 字节）",
            })
            continue
        done = float(state["send_done"] or time.perf_counter())
        skip_mark = state["skip_mark"]
        steady = (sent - _SLOW_START_BYTES, done - float(skip_mark)) if skip_mark else None
        note = f"服务端响应较慢（{response_error}），速率按发送阶段计算" if response_error else ""
        payload = _payload(label, target, sent, done - started, steady=steady, note=note)
        payload["tried"] = tried
        return payload
    return {"ok": False, "message": "上传测速不可用（端点不可达）", "tried": tried}


def latency_test(count: int = 5) -> dict[str, Any]:
    """TCP 握手延迟与抖动（校园网封 ICMP，所以不用 ping）。"""
    from .probe import tcp_ping

    targets = []
    for name, host, port in LATENCY_TARGETS:
        samples = []
        for _ in range(max(2, count)):
            outcome = tcp_ping(host, port, 2.5)
            if not outcome.get("ok"):
                continue
            samples.append(int(outcome.get("elapsed_ms") or 0))
        if not samples:
            targets.append({
                "name": name,
                "host": host,
                "port": port,
                "ok": False,
                "loss": 100,
                "avg_ms": None,
                "jitter_ms": None,
            })
            continue
        diffs = [abs(b - a) for a, b in zip(samples, samples[1:])]
        targets.append({
            "name": name,
            "host": host,
            "port": port,
            "ok": True,
            "loss": round((count - len(samples)) / count * 100),
            "avg_ms": round(sum(samples) / len(samples)),
            "min_ms": min(samples),
            "max_ms": max(samples),
            "jitter_ms": round(sum(diffs) / len(diffs), 1) if diffs else 0.0,
            "samples": samples,
        })
    usable = [t for t in targets if t["ok"]]
    best = min(usable, key=lambda t: t["avg_ms"]) if usable else None
    return {"ok": bool(usable), "targets": targets, "best": best}


def speed_test(duration_ms: int = 8000, url: str = "") -> dict[str, Any]:
    """兼容旧接口：只测下载。"""
    return download_speed(duration_ms, url)


def full_speed_test(duration_ms: int = 8000, with_upload: bool = True) -> dict[str, Any]:
    """完整测速：延迟/抖动 → 下载 → 上传。"""
    started = time.time()
    latency = latency_test(count=5)
    download = download_speed(duration_ms)
    upload = upload_speed(max(4000, duration_ms // 2)) if with_upload else {"ok": False, "message": "已跳过"}
    return {
        "ok": bool(download.get("ok")) or bool(latency.get("ok")),
        "at": time.time(),
        "latency": latency,
        "download": download,
        "upload": upload,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _tcp_fallback(host: str) -> dict[str, Any]:
    """ICMP 不通时的 TCP 延迟兜底（校园网普遍封 ICMP）。"""
    from .probe import tcp_ping

    for port in (443, 53, 80):
        outcome = tcp_ping(host, port, 2.5)
        if not outcome.get("ok"):
            continue
        ms = int(outcome.get("elapsed_ms") or 0)
        return {
            "ok": True,
            "via": "tcp",
            "port": port,
            "avg_ms": ms,
            "min_ms": ms,
            "max_ms": ms,
            "loss_percent": 0,
            "samples": [ms],
        }
    return {"ok": False, "via": "tcp", "loss_percent": 100, "avg_ms": None}


def full_diagnostic(gateway: str = "") -> dict[str, Any]:
    """一次性完整诊断：网关/常用站点延迟丢包 + DNS 解析 + 路由 + TCP。"""
    started = time.time()
    results = []
    for target in TARGETS:
        host = gateway if target["id"] == "gateway" else target["host"]
        if not host:
            continue
        outcome = ping(host, 3)
        entry = {**target, "host": host, **{k: v for k, v in outcome.items() if k != "host"}}
        if not outcome.get("ok") and target["id"] != "gateway":
            fallback = _tcp_fallback(host)
            entry["icmp_loss_percent"] = outcome.get("loss_percent", 100)
            entry.update({k: v for k, v in fallback.items()})
        results.append(entry)
    return {
        "at": time.time(),
        "results": results,
        "latency": latency_test(count=4),
        "dns": dns_benchmark(),
        "routes": routes(),
        "tcp": _tcp_fallback("223.5.5.5"),
        "elapsed_ms": int((time.time() - started) * 1000),
    }
