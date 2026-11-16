"""Windows 命令行调用工具。

要点：
- netsh / netstat 等输出的是系统 ANSI 代码页（简体中文为 GBK），必须正确解码，
  否则界面上的 SSID 会变成乱码。
- 所有子进程都加 ``CREATE_NO_WINDOW``，避免 GUI 程序运行时弹出黑框。
- 需要管理员权限时弹 UAC 提权（``run_elevated``）。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Sequence

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 134217728 if IS_WINDOWS else 0


@dataclass
class CmdResult:
    """一次命令执行的结果。"""

    code: int = -1
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str = ""
    argv: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out

    @property
    def text(self) -> str:
        return (self.stdout or "") + (self.stderr or "").strip()


def _decode(data: bytes) -> str:
    """按实际情况解码命令行输出。

    Windows 上老工具（netsh/netstat/route/ipconfig）用 ANSI 代码页输出 GBK，
    个别新工具输出 UTF-8，这里做自动判别。
    """
    if not data:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", errors="replace")

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    for encoding in ("gbk", "gb18030", "cp936"):
        try:
            text = data.decode(encoding)
            if "\ufffd" not in text:
                return text
        except (UnicodeDecodeError, LookupError):
            continue

    return data.decode("utf-8", errors="replace")


def run(argv: Sequence[str], timeout: float = 20.0, cwd: str | None = None) -> CmdResult:
    """执行命令并返回结果，不会抛异常。"""
    argv = [str(a) for a in argv]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            creationflags=CREATE_NO_WINDOW,
        )
        return CmdResult(
            code=proc.returncode,
            stdout=_decode(proc.stdout),
            stderr=_decode(proc.stderr),
            argv=argv,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or b""
        err = exc.stderr or b""
        return CmdResult(
            code=-1,
            stdout=_decode(out) if isinstance(out, bytes) else str(out),
            stderr=_decode(err) if isinstance(err, bytes) else str(err),
            timed_out=True,
            error=f"命令超时（{timeout}s）",
            argv=argv,
        )
    except FileNotFoundError:
        return CmdResult(code=-1, error=f"找不到可执行文件：{argv[0]}", argv=argv)
    except OSError as exc:
        return CmdResult(code=-1, error=f"{type(exc).__name__}: {exc}", argv=argv)


def run_cmd(command_line: str, timeout: float = 20.0) -> CmdResult:
    """在 cmd.exe 里执行一行命令（便于使用管道/重定向）。"""
    return run(["cmd.exe", "/d", "/s", "/c", command_line], timeout=timeout)


def run_powershell(script: str, timeout: float = 30.0) -> CmdResult:
    """执行一段 PowerShell 脚本，输出统一为 UTF-8。

    优先使用系统自带的 Windows PowerShell 5.1（一定存在）。
    """
    prelude = (
        "$ProgressPreference='SilentlyContinue';"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$ErrorActionPreference='SilentlyContinue';"
    )
    for exe in ("powershell.exe", "pwsh.exe"):
        result = run(
            [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", prelude + script],
            timeout=timeout,
        )
        if result.code != -1 or "找不到可执行文件" not in result.error:
            return result
    return CmdResult(code=-1, error="系统里没有可用的 PowerShell")


def run_script_file(path: str, args: Sequence[str] = (), timeout: float = 30.0, json_output: bool = False) -> CmdResult:
    """执行一个 .ps1 文件。

    ``json_output=True`` 时会在管道末尾自动接上 ``ConvertTo-Json``，
    这样脚本里只需输出对象，调用方拿到的一定是 JSON 文本。
    """
    quoted = str(path).replace("'", "''")
    arg_list = " ".join(
        f"-{a}" if a.startswith("-") else f"'{str(a).replace(chr(39), chr(39) * 2)}'" for a in args
    )
    tail = " | ConvertTo-Json -Depth 5 -Compress" if json_output else ""
    return run_powershell(f"& '{quoted}' {arg_list}{tail}", timeout=timeout)


def is_admin() -> bool:
    """当前进程是否具备管理员权限。"""
    if not IS_WINDOWS:
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_elevated(exe: str, args: Sequence[str] = (), wait: bool = True, timeout: float = 60.0) -> CmdResult:
    """以管理员身份运行（会弹 UAC）。返回是否成功启动。

    返回值里 code==0 表示提权命令已成功执行（或已启动）。
    """
    if is_admin():
        return run([exe, *args], timeout=timeout)

    arg_string = " ".join(f'"{str(a)}"' if " " in str(a) else str(a) for a in args)

    SW_SHOWNORMAL = 1
    try:
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = wintypes.HINSTANCE
        shell32.ShellExecuteW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        ret = shell32.ShellExecuteW(None, "runas", exe, arg_string, None, SW_SHOWNORMAL)
        if ret <= 32:
            return CmdResult(code=-1, error=f"提权失败（ShellExecuteW 返回 {ret}），可能被用户取消")
        return CmdResult(code=0, stdout="已通过 UAC 提权执行")
    except Exception as exc:
        return CmdResult(code=-1, error=f"提权异常：{exc}")


def open_path(target: str) -> CmdResult:
    """用系统默认方式打开文件/目录/URL。"""
    if IS_WINDOWS:
        return run_cmd(f'start "" "{target}"', timeout=8)
    return run(["xdg-open", target], timeout=8)


def restart_as_admin() -> bool:
    """以管理员身份重新启动当前程序（会弹 UAC）。"""
    if not IS_WINDOWS:
        return False
    try:
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, " ".join(sys.argv[1:])
        else:
            exe = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv)
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return ret > 32
    except Exception:
        return False
