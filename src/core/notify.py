"""Windows 通知的身份标识。

默认情况下，Python 程序弹的 toast 在「操作中心」里显示的应用名是
``python.exe`` 或 exe 的文件描述（我们这里是「校园网助手」）。
要让它显示成「校园网」，需要两步：

1. 给进程设一个自己的 **AppUserModelID**（AUMID）；
2. 在注册表 ``HKCU\\Software\\Classes\\AppUserModelId\\<AUMID>`` 下登记显示名与图标。

这是微软给桌面程序控制 toast 品牌的官方做法，只写当前用户、不需要管理员权限。
"""
from __future__ import annotations

import os
import sys


NOTIFY_APP_NAME = "校园网"

AUMID = "CampusNet.CampusNetwork"

_registered = False


def register_notify_identity(icon_path: str = "") -> dict:
    """把自己登记成通知里的「校园网」。返回 {ok, aumid, name, message}。

任何一步失败都不抛异常 —— 通知品牌化失败不该影响程序运行。
"""
    global _registered

    result = {"ok": False, "aumid": AUMID, "name": NOTIFY_APP_NAME, "message": ""}
    if os.name != "nt":
        result["message"] = "非 Windows 平台，跳过"
        return result

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception as exc:
        result["message"] = f"设置 AUMID 失败：{exc}"

    try:
        import winreg

        key_path = f"Software\\Classes\\AppUserModelId\\{AUMID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, NOTIFY_APP_NAME)

            if icon_path and os.path.exists(icon_path):
                import tempfile

                temp_root = os.path.abspath(tempfile.gettempdir()).lower().rstrip("\\")
                if not os.path.abspath(icon_path).lower().startswith(temp_root):
                    winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)
        result["ok"] = True
        result["message"] = f"通知应用名已登记为「{NOTIFY_APP_NAME}」"
        _registered = True
    except Exception as exc:
        result["message"] = (result["message"] + "；" if result["message"] else "") + f"写注册表失败：{exc}"
    return result


def notify_identity() -> dict:
    """当前通知身份（给自检/开发者页展示）。"""
    icon = ""
    try:
        if icon == "":
            from .deps import resource_path

            icon = resource_path("app.ico") or ""
    except Exception:
        pass
    return {
        "aumid": AUMID, "name": NOTIFY_APP_NAME, "icon": icon, "registered": _registered,
        "frozen": bool(getattr(sys, "frozen", False)),
    }
