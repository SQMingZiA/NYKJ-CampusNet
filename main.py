"""校园网助手（CampusNet）—— FluentUI 校园网自动认证与流量统计工具。

启动入口。程序**默认要求管理员权限**（连接 WiFi、按网卡统计流量都需要）：

1. 打包成 exe 时通过 UAC 清单（``--uac-admin``）在启动时自动弹出提权确认；
2. 直接以 python 运行时，如果没拿到管理员权限，会自动尝试用 UAC 重新启动自己。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from src.core import deps

DEPS = deps.setup()

from src.core.winutil import is_admin, restart_as_admin

__version__ = '1.2.0'
__all__ = ['main', '__version__']



def _force_utf8_streams() -> None:
    """把 stdout/stderr 切成 UTF-8。

    中文 Windows 的控制台默认是 GBK，打印 emoji 或部分中文会直接抛
    UnicodeEncodeError；windowed（无控制台）模式下 stdout 甚至是 None。
    这里统一处理，避免因为一句日志把程序搞崩。
    """
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            continue


_force_utf8_streams()


_NO_ELEVATE_FLAGS = ('--no-elevate', '--elevated-retry')


def _ensure_admin() -> None:
    """确保以管理员权限运行；否则尝试提权重启。

    默认就提权（与 exe 里的 requireAdministrator 清单一致）；``--no-elevate`` 可跳过，调试用。
    """
    if is_admin():
        return
    if any(flag in sys.argv for flag in _NO_ELEVATE_FLAGS):
        print('[warn] 未以管理员身份运行（已按参数跳过自动提权）')
        return

    print('[info] 需要管理员权限，正在请求提权（会弹出 UAC 确认框）…')
    if restart_as_admin():
        sys.exit(0)
    print('[warn] 提权被取消，将以普通权限继续运行；连接 WiFi 若失败会再请求一次 UAC')


def _selftest() -> int:
    """打包后的自检：验证 exe 里的依赖、资源与系统能力都正常。

    用法：``校园网助手.exe --selftest``（不会启动界面，直接打印结果并退出）
    """
    lines = []

    def emit(text: str = '') -> None:
        print(text, flush=True)
        lines.append(text)

    emit('=' * 46)
    emit('校园网助手 自检')
    emit('=' * 46)
    emit(f"  frozen={getattr(sys, 'frozen', False)}  _MEIPASS={getattr(sys, '_MEIPASS', None)}")
    emit(f"  executable={sys.executable}")
    emit(f"  sys.path[0:4]={sys.path[:4]}")
    emit(f"  文件搜索位置={list(getattr(os, '_added_dll_directories', [])) or '-'}")
    emit('')

    ok = True

    def report(name: str, passed: bool, detail: str = '') -> None:
        nonlocal ok
        emit(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" :: {detail}" if detail and not passed else ''))
        if not passed:
            ok = False


    report('运行模式', True, 'frozen' if getattr(sys, 'frozen', False) else '源码')
    report('管理员权限', True, '已提权' if is_admin() else '普通权限（连接 WiFi 失败时会请求一次 UAC）')
    report('项目根目录', os.path.isdir(DEPS['project_root']), DEPS['project_root'])


    for name in ('traffic.ps1', 'wifi.ps1', 'app.ico'):
        path = deps.resource_path(name)
        report(f'资源 {name}', os.path.exists(path), path)


    try:
        import PyQt5.QtCore as qtcore


        report('PyQt5', True, f'Qt {qtcore.QT_VERSION_STR}')
    except Exception as exc:
        report('PyQt5', False, str(exc))


    for module in ('pyqtgraph', 'requests'):
        try:
            __import__(module)
            report(module, True)
        except Exception as exc:
            report(module, False, str(exc))


    try:
        from PyQt5.QtSvg import QSvgRenderer


        report('PyQt5.QtSvg', True)
    except Exception as exc:
        report('PyQt5.QtSvg', False, str(exc))


    try:
        import qfluentwidgets


        report('qfluentwidgets', True, getattr(qfluentwidgets, '__version__', ''))
    except Exception as exc:
        report('qfluentwidgets', False, f'{exc}（可执行 pip install PyQt-Fluent-Widgets 后重打包）')


    for module in ('win32api', 'win32con', 'win32gui'):
        try:
            __import__(module)
            report(f'pywin32:{module}', True)
        except Exception as exc:
            report(f'pywin32:{module}', False, str(exc))


    for module in ('src.gui.theme', 'src.gui.icons', 'src.gui.widgets', 'src.gui.pages.home', 'src.gui.pages.tools', 'src.gui.pages.settings'):
        try:
            __import__(module)
            report(f"界面模块 {module.split('.')[-1]}", True)
        except Exception as exc:
            report(f"界面模块 {module.split('.')[-1]}", False, str(exc))


    try:
        from PyQt5.QtWidgets import QApplication


        if QApplication.instance() is None:
            emit('  [跳过] FluentUI 图标 :: 当前进程还没有 QApplication，跳过像素渲染检查')
        else:
            from src.gui import icons


            pix = icons.pixmap('wifi', 16, '#000000')
            report('FluentUI 图标', not pix.isNull() and pix.width() > 0, f'{len(icons.NAMES)} 个图标映射')
    except Exception as exc:
        report('FluentUI 图标', False, str(exc))


    try:
        from PyQt5.QtWebEngineWidgets import QWebEngineView


        report('内置浏览器 QtWebEngine', True)
    except Exception as exc:
        report('内置浏览器 QtWebEngine', False, f'{exc}（可执行 pip install PyQtWebEngine 后重打包）')


    try:
        from src.net import wifi


        status_result = wifi.status(deps.resource_path('wifi.ps1'))
        report('读取 WiFi 状态', isinstance(status_result, dict), str(status_result)[:200])
    except Exception as exc:
        report('读取 WiFi 状态', False, str(exc))

    try:
        from src.net.probe import probe

        outcome = probe(timeout=5)
        report(
            '连通性探测',
            outcome['state'] in {'no-network', 'online', 'no-internet', 'portal'},
            f"state={outcome['state']}",
        )
    except Exception as exc:
        report('连通性探测', False, str(exc))

    try:
        from src.core.traffic import TrafficMonitor

        monitor = TrafficMonitor(
            script_path=deps.resource_path('traffic.ps1'),
            data_dir=os.path.join(os.path.expanduser('~'), '.campusnet-selftest'),
            logger=lambda *_a: None,
        )
        sample = monitor.sample()
        report('流量计数器', sample is not None, '读取失败（可能需要管理员权限）')
    except Exception as exc:
        report('流量计数器', False, str(exc))

    emit('=' * 46)
    emit('结论：' + ('全部通过 ✅' if ok else '存在失败项 ❌'))

    log_path = os.path.join(os.path.expanduser('~'), 'CampusNet-selftest.log')
    try:
        with open(log_path, 'w', encoding='utf-8') as fp:
            fp.write('\n'.join(lines) + '\n')
        print(f'（结果已写入 {log_path}）', flush=True)
    except OSError:
        pass

    return 0 if ok else 1



def _report_fatal(exc: BaseException) -> None:
    """把致命错误写进日志并弹窗告知用户。

    windowed（--noconsole）打包后没有控制台，如果不这样做，
    程序崩溃时会「静默消失」，用户完全看不到原因。
    """
    import traceback

    detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    header = (
        f"\n{'=' * 60}\n时间: {__import__('time').strftime('%Y-%m-%d %H:%M:%S')}"
        f"\nfrozen: {getattr(sys, 'frozen', False)}"
        f"  _MEIPASS: {getattr(sys, '_MEIPASS', None)}"
        f"\nvendor: {DEPS.get('vendor')}"
        f"\nargv: {sys.argv}\n"
    )
    log_path = os.path.join(os.path.expanduser('~'), 'CampusNet-crash.log')
    try:
        with open(log_path, 'a', encoding='utf-8') as fp:
            fp.write(header)
            fp.write(detail)
            fp.flush()
        log_hint = f'\n\n详细信息已写入：\n{log_path}'
    except OSError:
        log_hint = ''

    print(detail, file=sys.stderr, flush=True)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"程序启动失败：\n\n{type(exc).__name__}: {exc}{log_hint}",
            '校园网助手',
            16,
        )
    except Exception:
        pass


def main() -> int:
    """程序入口。"""
    try:
        if '--selftest' in sys.argv:
            return _selftest()

        if '--connect-only' in sys.argv:
            from src.core.connector import run_connect_only

            return run_connect_only()

        _ensure_admin()

        from src.gui.app import run_app

        return run_app()
    except BaseException as exc:
        _report_fatal(exc)
        return 1




if __name__ == '__main__':
    raise SystemExit(main())
