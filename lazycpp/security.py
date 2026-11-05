"""Windows DPAPI 小工具：把 API Key 加密后保存到本地配置。"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_PREFIX = "dpapi:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def protect_text(text: str) -> str:
    """用当前 Windows 用户的 DPAPI 加密文本；成功后带回 dpapi: 前缀。"""
    if not text:
        return ""
    if sys.platform != "win32":
        return "plain:" + base64.b64encode(text.encode("utf-8")).decode("ascii")
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        in_blob, _keep = _blob(text.encode("utf-8"))
        out_blob = _DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(in_blob), "CppShot API Key", None, None, None, 0,
            ctypes.byref(out_blob),
        ):
            raise OSError("CryptProtectData failed")
        try:
            raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
        return _PREFIX + base64.b64encode(raw).decode("ascii")
    except Exception:
        # 极端环境下退化为 base64（不显示为明文），保证功能可用。
        return "plain:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


def unprotect_text(value: str) -> str:
    if not value:
        return ""
    try:
        if value.startswith(_PREFIX):
            raw = base64.b64decode(value[len(_PREFIX):])
            if sys.platform != "win32":
                return raw.decode("utf-8")
            crypt32 = ctypes.windll.crypt32
            kernel32 = ctypes.windll.kernel32
            crypt32.CryptUnprotectData.argtypes = [
                ctypes.POINTER(_DATA_BLOB),
                ctypes.POINTER(wintypes.LPWSTR),
                ctypes.POINTER(_DATA_BLOB),
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(_DATA_BLOB),
            ]
            crypt32.CryptUnprotectData.restype = wintypes.BOOL
            in_blob, _keep = _blob(raw)
            out_blob = _DATA_BLOB()
            if not crypt32.CryptUnprotectData(
                ctypes.byref(in_blob), None, None, None, None, 0,
                ctypes.byref(out_blob),
            ):
                raise OSError("CryptUnprotectData failed")
            try:
                plain = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            finally:
                kernel32.LocalFree(out_blob.pbData)
            return plain.decode("utf-8")
        if value.startswith("plain:"):
            return base64.b64decode(value[len("plain:"):]).decode("utf-8")
        return value
    except Exception:
        return ""
