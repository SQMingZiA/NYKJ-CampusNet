"""开机自连程序（无界面）。

**只负责开机那一下**，干完就退出，之后所有事情交给主程序：

    连接校园网 → 提交认证 → 发"连接成功"通知（账号 + 套餐）→ 启动主程序 → 自己退出

几个刻意的设计：

* **绝不显示窗口**：不调用任何 ``show()``，所以不会有"一闪而过的窗口"。
* **干完就退**：不驻留、不开后台循环 —— 否则会和主程序抢着采样、写同一份历史数据。
* **不额外弹 UAC**：它由计划任务以最高权限静默启动；它再启动主程序时子进程继承提权，
  主程序那个 ``requireAdministrator`` 也不会再弹窗。这是"整条链路不出 UAC"的关键。
* **尊重"启动后最小化"**：启动主程序时**不带任何参数**，由主程序自己读配置决定
  是显示窗口还是缩到托盘。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from ..core import single_instance


DEADLINE_SECONDS = 90.0
RETRY_INTERVAL = 2.5


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def is_running() -> bool:
    """主程序是否已经在运行（不会把它的窗口弹出来）。"""
    try:
        return single_instance.is_running()
    except Exception:
        return False


def launch_main_program() -> bool:
    """启动主程序本体。已在运行就不再启动（避免两份实例抢流量统计）。"""
    if is_running():
        return False
    frozen = bool(getattr(sys, "frozen", False))
    try:
        if frozen:
            subprocess.Popen([sys.executable], close_fds=True)
        else:
            subprocess.Popen([sys.executable, os.path.join(_project_root(), "main.py")], close_fds=True)
        return True
    except Exception:
        return False


def _toast(app, title: str, message: str) -> None:
    """发一条 Windows 通知（应用名沿用已注册的「校园网」）。"""
    try:
        from PyQt5.QtGui import QIcon
        from PyQt5.QtWidgets import QSystemTrayIcon

        from ..core.deps import resource_path

        icon_path = resource_path("app.ico")
        tray = QSystemTrayIcon(QIcon(icon_path) if os.path.exists(icon_path) else QIcon(), app)
        tray.setToolTip(title)
        tray.show()
        tray.showMessage(title, message, QSystemTrayIcon.Information, 8000)
        setattr(app, "_connector_tray", tray)
        from PyQt5.QtCore import QTimer

        QTimer.singleShot(9000, app.quit)
        app.exec_()
    except Exception:
        return None


def run_connect_only() -> int:
    """开机自连的完整流程。返回 0 表示已连上（或已经在线）。"""
    from PyQt5.QtWidgets import QApplication

    from .service import Service
    from .settings import Settings

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("校园网")
    boot_started = time.time()
    settings = Settings().load()
    service = Service(settings)

    online = False
    deadline = time.time() + DEADLINE_SECONDS
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        cycle_started = time.perf_counter()
        try:
            state = service.tick(manual=True)
        except Exception as exc:
            service.log("warn", f"自连第 {attempt} 次出错：{exc}")
            state = {}
        cycle_ms = (time.perf_counter() - cycle_started) * 1000
        if isinstance(state, dict) and state.get("online"):
            online = True
            service.log("info", f"第 {attempt} 次尝试成功，本轮 {cycle_ms:.0f} 毫秒（进程启动到连上共 "
                                f"{time.time() - boot_started:.1f} 秒）")
            break
        service.log("info", f"第 {attempt} 次尚未连上（本轮 {cycle_ms:.0f} 毫秒，state="
                            f"{state.get('state') if isinstance(state, dict) else '?'}），"
                            f"{RETRY_INTERVAL} 秒后重试")
        time.sleep(RETRY_INTERVAL)

    if not online:
        service.log("warn", f"开机自连失败（试了 {attempt} 次，{DEADLINE_SECONDS:.0f} 秒内没连上）")
        service.stop()
        return 2

    account = str(settings.username or "").strip()
    package = str(settings.last_product_name or "").strip()
    detail = " · ".join(part for part in (account, package) if part)
    message = f"连接成功：{detail}" if detail else "连接成功"
    service.log("ok", f"开机自连成功（第 {attempt} 次），{message}")


    launched = launch_main_program()
    service.log("info", "已启动主程序" if launched else "主程序已在运行，直接交接")

    _toast(app, "校园网", message)
    service.stop()
    return 0
