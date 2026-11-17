"""配置与凭据存储。

- 普通配置以 JSON 明文保存，便于排查。
- 账号密码用 Windows **DPAPI**（CryptProtectData）加密后存 base64，
  密文与当前 Windows 用户绑定，拷到别的机器无法解密。
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import dataclass, field, fields
from typing import Any

APP_NAME = "CampusNet"
APP_DIR_NAME = "CampusNet"

# 用户自定义程序名的长度上限（它只是窗口/托盘标题，不必过长）。
APP_NAME_MAX = 24

# 配置结构版本号。字段含义变化 / 需要一次性纠正历史坏值时 +1，
# 旧配置会在 Settings.load() 里走 _migrate()。
CONFIG_VERSION = 2

# 每月额度的合理上限（100 TB）。超过它基本可以确定是历史脏数据：
# 早期版本会把「不限量」套餐的总流量（2^62 字节）直接换算成 GB 写进来。
MAX_QUOTA_GB = 100 * 1024.0

# 数值型配置允许的范围，超出则回落到默认值。
_VALUE_RANGES: dict[str, tuple[float, float]] = {
    "auth_timeout_ms": (3000, 60000),
    "retry_count": (0, 10),
    "retry_delay_ms": (1000, 120000),
    "wifi_connect_wait_ms": (500, 30000),
    "reconnect_delay_ms": (200, 60000),
    "traffic_interval_ms": (1000, 60000),
    "traffic_retention_days": (7, 3650),
    "monthly_quota_gb": (0.0, MAX_QUOTA_GB),
    "monthly_start_day": (1, 28),
    "notice_refresh_minutes": (1, 240),
}

DEFAULTS: dict[str, Any] = {
    "version": CONFIG_VERSION,

    "auto_connect": True,
    "auto_login": True,
    "wifi_allow_list": ["NYKJ", "CMCC-NYKJ", "CMCCNYKJ", "NYKY"],
    "wifi_connect_wait_ms": 2500,

    "driver": "auto",
    "acip": "",
    "custom_probe_url": "",
    "custom_portal_url": "",
    "auth_timeout_ms": 12000,
    "retry_count": 3,
    "retry_delay_ms": 8000,
    "auto_retry_on_drop": True,
    "kick_on_conflict": False,

    "notify_on_drop": True,
    "auto_reconnect_on_logout": True,
    "reconnect_delay_ms": 1500,
    "notify_app_name": "校园网",

    "traffic_interval_ms": 5000,
    "traffic_retention_days": 120,
    # 默认只统计校园网内的流量：连的是手机热点/家里宽带时不写入历史与用量。
    "traffic_only_campus": True,
    "monthly_quota_gb": 0.0,
    "monthly_start_day": 1,

    "quota_auto": True,

    "last_product_name": "",

    "home_show_notice": True,
    "notice_refresh_minutes": 10,
    "notice_dismissed": "",

    "app_name": "",

    "theme": "auto",
    "mica_effect": True,
    "start_minimized": False,
    "auto_launch": False,
    "close_to_tray": True,
    "show_notification": True,

    "developer_mode": False,

    "window_geometry": "",

    "home_show_package": True,
}


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_from_bytes(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _bytes_from_blob(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def dpapi_encrypt(plain: str) -> str:
    """用 DPAPI 加密字符串，返回 base64。失败时退回 base64 明文（并在前缀标注）。"""
    data = plain.encode("utf-8")
    if os.name != "nt":
        return "plain:" + base64.b64encode(data).decode("ascii")
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_in = _blob_from_bytes(data)
        blob_out = _DataBlob()
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            "CampusNet",
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not ok:
            raise OSError("CryptProtectData 失败")
        try:
            return "dpapi:" + base64.b64encode(_bytes_from_blob(blob_out)).decode("ascii")
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return "plain:" + base64.b64encode(data).decode("ascii")


def dpapi_decrypt(token: str) -> str:
    """解密 DPAPI 字符串。"""
    if not token:
        return ""
    if token.startswith("plain:"):
        try:
            return base64.b64decode(token[6:]).decode("utf-8")
        except Exception:
            return ""
    if not token.startswith("dpapi:"):
        try:
            return base64.b64decode(token).decode("utf-8")
        except Exception:
            return ""
    if os.name != "nt":
        return ""
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        raw = base64.b64decode(token[6:])
        blob_in = _blob_from_bytes(raw)
        blob_out = _DataBlob()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not ok:
            raise OSError("CryptUnprotectData 失败")
        try:
            return _bytes_from_blob(blob_out).decode("utf-8")
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return ""


def default_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


@dataclass
class Settings:
    """应用配置。

    所有配置项都存放在 ``_data`` 字典里，通过 ``__getattr__`` 以属性方式访问
    （``settings.auto_connect``）。账号密码单独放在 ``_data['username']`` /
    ``_data['password']``，落盘时会合并成 ``_credentials`` 并用 DPAPI 加密。
    """

    data_dir: str = field(default_factory=default_data_dir)
    _data: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS), repr=False)

    def __post_init__(self) -> None:
        self._data.setdefault("username", "")
        self._data.setdefault("password", "")

    @property
    def username(self) -> str:
        return str(self._data.get("username") or "")

    @username.setter
    def username(self, value: str) -> None:
        self._data["username"] = str(value or "")

    @property
    def password(self) -> str:
        return str(self._data.get("password") or "")

    @password.setter
    def password(self, value: str) -> None:
        self._data["password"] = str(value or "")

    @property
    def path(self) -> str:
        return os.path.join(self.data_dir, "settings.json")

    @property
    def history_path(self) -> str:
        return os.path.join(self.data_dir, "history.json")

    @property
    def counters_path(self) -> str:
        return os.path.join(self.data_dir, "counters.json")

    @property
    def log_path(self) -> str:
        return os.path.join(self.data_dir, "campusnet.log")

    def load(self) -> 'Settings':
        os.makedirs(self.data_dir, exist_ok=True)
        try:
            with open(self.path, "r", encoding="utf-8") as fp:
                raw = json.load(fp)
        except (OSError, json.JSONDecodeError):
            return self

        credentials = raw.pop("_credentials", None)
        if credentials:
            self._data["username"] = dpapi_decrypt(credentials.get("username", ""))
            self._data["password"] = dpapi_decrypt(credentials.get("password", ""))

        if raw.get("username") or raw.get("password"):
            self._data["username"] = raw.pop("username", "") or self._data.get("username", "")
            self._data["password"] = raw.pop("password", "") or self._data.get("password", "")
            self.save()

        for key, value in raw.items():
            if key in DEFAULTS:
                self._data[key] = value

        self._migrate(raw.get("version"))
        return self

    def _migrate(self, raw_version: Any) -> None:
        """把旧版本的配置升级到当前结构（只做一次，升级后写盘）。"""
        try:
            version = int(raw_version or 0)
        except (TypeError, ValueError):
            version = 0
        if version >= CONFIG_VERSION:
            self._sanitize()
            return

        # v1 → v2：
        # * developer_mode 曾被早期版本写坏成 true（默认应为关），一次性重置；
        # * monthly_quota_gb 曾被「不限量」套餐写成 4294967296，一次性归零；
        # * open_portal_on_failure 是从未实现的死配置，已从 DEFAULTS 移除，
        #   下次 save() 时自然丢弃。
        if self._data.get("developer_mode"):
            self._data["developer_mode"] = False
        self._data["monthly_quota_gb"] = 0.0
        self._data["version"] = CONFIG_VERSION
        self._sanitize()
        self.save()

    def _sanitize(self) -> None:
        """收敛越界 / 类型异常的值，避免脏配置把界面拖垮。"""
        for key, (low, high) in _VALUE_RANGES.items():
            default = DEFAULTS[key]
            try:
                value = float(self._data.get(key, default))
            except (TypeError, ValueError):
                value = float(default)
            # NaN 与区间外的值一律回落到默认值
            if not low <= value <= high:
                value = float(default)
            self._data[key] = int(value) if isinstance(default, int) else round(value, 3)

        if self._data.get("theme") not in ("auto", "light", "dark"):
            self._data["theme"] = DEFAULTS["theme"]

        name = self._data.get("app_name")
        name = name.strip()[:APP_NAME_MAX] if isinstance(name, str) else ""
        self._data["app_name"] = name

        dismissed = self._data.get("notice_dismissed")
        self._data["notice_dismissed"] = dismissed.strip()[:64] if isinstance(dismissed, str) else ""

        patterns = self._data.get("wifi_allow_list")
        if not isinstance(patterns, list):
            patterns = list(DEFAULTS["wifi_allow_list"])
        cleaned = [str(item).strip() for item in patterns if str(item).strip()]
        self._data["wifi_allow_list"] = cleaned or list(DEFAULTS["wifi_allow_list"])

    def save(self) -> bool:
        os.makedirs(self.data_dir, exist_ok=True)
        payload = {key: value for key, value in self._data.items() if key in DEFAULTS}
        payload["_credentials"] = {
            "username": dpapi_encrypt(self._data.get("username", "")),
            "password": dpapi_encrypt(self._data.get("password", "")),
        }
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        data = self.__dict__.get("_data")
        if isinstance(data, dict) and item in data:
            return data[item]
        if item in DEFAULTS:
            return DEFAULTS[item]
        raise AttributeError(item)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        if key in DEFAULTS:
            self._data[key] = value

    def as_dict(self) -> dict[str, Any]:
        out = {key: self._data[key] for key in DEFAULTS if key in self._data}
        out["username"] = self.username
        out["has_password"] = bool(self.password)
        out["data_dir"] = self.data_dir
        return out

    def update(self, patch: dict[str, Any]) -> None:
        for key, value in patch.items():
            if key == "username":
                self.username = value
            elif key == "password":
                if value != "":
                    self.password = value
            elif key in DEFAULTS:
                self._data[key] = value
        self._sanitize()

    def reset(self) -> None:
        self._data = dict(DEFAULTS)
        self._data["username"] = ""
        self._data["password"] = ""
        self.save()


def settings_field_names() -> list[str]:
    return [f.name for f in fields(Settings) if not f.name.startswith("_")]


def display_name(name: str = "") -> str:
    """把「程序名称」配置规整成可以直接显示的标题（留空就用默认名）。"""
    text = (name or "").strip()
    return text[:APP_NAME_MAX] if text else APP_NAME
