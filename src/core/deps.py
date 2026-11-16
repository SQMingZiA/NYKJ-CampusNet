"""在导入 PyQt5 之前准备好依赖搜索路径。

打包/免安装运行时，第三方依赖放在项目里的 ``vendor/`` 目录（而不是 site-packages），
因此需要手动处理两件事：

1. 把 ``vendor/`` 加入 ``sys.path``；
2. PyQt5 的 Qt DLL 与 pywin32 的扩展 DLL 需要加入 DLL 搜索目录，
   否则会报 "DLL load failed while importing QtCore" / "No module named 'win32api'"。
"""

from __future__ import annotations

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
def _base_dir() -> str:
    """根目录：打包后是 PyInstaller 的解压目录，否则是项目根。"""
    return getattr(sys, "_MEIPASS", None) or PROJECT_ROOT


VENDOR_DIR = os.path.join(_base_dir(), "vendor")
_APP_VENDOR_DIR = os.path.join(PROJECT_ROOT, "vendor")


_DLL_SUBDIRS = [
    os.path.join("PyQt5", "Qt5", "bin"),
    os.path.join("PyQt5", "Qt5", "plugins", "platforms"),
    "pywin32_system32",
    "win32",
    os.path.join("win32", "lib"),
]



_REQUIRED_MODULES = ("numpy", "PyQt5.QtCore", "PyQt5.QtSvg", "PyQt5.QtWebEngineWidgets")


def _deps_in_site_packages() -> bool:
    """依赖是否已装在 Python 自己的 site-packages 里。

用 ``find_spec`` 而不是 import：import 会受当前 ``sys.path`` 影响，
而我们要判断的是「磁盘上有没有装在标准位置」。
"""
    import importlib.util

    vendor = _APP_VENDOR_DIR.replace("\\", "/").lower()
    found = 0
    for module in _REQUIRED_MODULES:
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):
            continue
        if spec is None or not spec.origin:
            continue
        if str(spec.origin).replace("\\", "/").lower().startswith(vendor):
            return False
        found += 1

    return found >= 3


def _add_sys_path(path: str) -> None:
    if path and os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)


def _add_dll_dir(path: str) -> None:
    if not path or not os.path.isdir(path):
        return
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(path)
        except OSError:
            pass

    os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")


def setup() -> dict[str, str]:
    """准备搜索路径。可重复调用。

优先级：**site-packages 优先，vendor 兜底**。

这样开发时与打包后的依赖布局一致（打包脚本也用 site-packages），
不会出现「源码能跑、打包后缺模块」的错配。
打包（PyInstaller）后两个目录都不需要，直接跳过——
那时依赖已经在 sys.path 与 DLL 搜索目录里了。
"""
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        use_site_packages = _deps_in_site_packages()
        if not use_site_packages:

            _add_sys_path(VENDOR_DIR)
            _add_sys_path(os.path.join(VENDOR_DIR, "win32"))
            _add_sys_path(os.path.join(VENDOR_DIR, "win32", "lib"))
            _add_sys_path(os.path.join(VENDOR_DIR, "pythonwin"))


        dll_roots = []
        if use_site_packages:
            try:
                import PyQt5

                dll_roots.append(os.path.dirname(os.path.dirname(PyQt5.__file__)))
            except ImportError:
                pass
        if not use_site_packages or os.path.isdir(VENDOR_DIR):
            dll_roots.append(VENDOR_DIR)

        for root in dll_roots:
            for sub in _DLL_SUBDIRS:
                _add_dll_dir(os.path.join(root, sub))

        if not use_site_packages and os.path.isdir(os.path.join(VENDOR_DIR, "win32")):
            try:
                import pywin32_bootstrap
            except Exception:
                pass

    return {
        "project_root": PROJECT_ROOT,
        "vendor": VENDOR_DIR,
        "frozen": str(frozen),
        "deps_source": "frozen" if frozen else ("site-packages" if _deps_in_site_packages() else "vendor"),
        "resources": resource_path(""),
    }


def resource_path(name: str) -> str:
    """资源文件的绝对路径（resources/ 下）。

PyInstaller 打包后会解压到 ``sys._MEIPASS``，因此两处都找一下。
"""
    base = getattr(sys, "_MEIPASS", None)
    candidates = []
    if base:
        candidates.append(os.path.join(base, "resources", name))
        candidates.append(os.path.join(base, name))
    candidates.append(os.path.join(PROJECT_ROOT, "resources", name))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[-1]
