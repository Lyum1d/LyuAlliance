"""敏感配置的本地加密（Windows DPAPI）。

为什么不用"自己塞一个固定密钥"：那样加密密钥本身又得找地方存，是个死循环。
DPAPI 的密钥由系统按「当前登录用户」派生，密文拷到别的机器或别的用户下解不开，
正好符合"只在这台机器上生效"的需求，也不需要额外管理任何 secret。

非 Windows 环境降级为明文前缀 `plain:`，保证项目在别处仍能跑起来。
"""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_AVAILABLE = sys.platform == "win32"

if _AVAILABLE:
    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    _crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB),
    )
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB),
    )
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    _kernel32.LocalFree.restype = ctypes.c_void_p

    _UI_FORBIDDEN = 0x1          # 纯后台，不弹任何系统对话框

    def _blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))   # 调用期间必须保活
        return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    if not _AVAILABLE:
        return "plain:" + plain
    src, _keep = _blob(plain.encode("utf-8"))
    out = _BLOB()
    if not _crypt32.CryptProtectData(
        ctypes.byref(src), "agentmesh", None, None, None,
        _UI_FORBIDDEN, ctypes.byref(out),
    ):
        raise OSError(f"CryptProtectData 失败（{ctypes.GetLastError()}）")
    try:
        raw = ctypes.string_at(out.pbData, out.cbData)
        return "dpapi:" + base64.b64encode(raw).decode()
    finally:
        _kernel32.LocalFree(out.pbData)


def decrypt(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith("plain:"):
        return stored[len("plain:"):]
    if not stored.startswith("dpapi:"):
        return stored                     # 兼容早期直接存明文的数据
    if not _AVAILABLE:
        raise OSError("这段密文只能用加密它的 Windows 账号解开")
    raw = base64.b64decode(stored[len("dpapi:"):])
    src, _keep = _blob(raw)
    out = _BLOB()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None,
        _UI_FORBIDDEN, ctypes.byref(out),
    ):
        raise OSError(f"CryptUnprotectData 失败（{ctypes.GetLastError()}）")
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        _kernel32.LocalFree(out.pbData)


def mask(plain: str) -> str:
    """给前端看的掩码。**任何接口都不应返回原文。**"""
    if not plain:
        return ""
    if len(plain) <= 8:
        return "•" * len(plain)
    return f"{plain[:4]}{'•' * 8}{plain[-4:]}"


def scheme() -> str:
    return "dpapi" if _AVAILABLE else "plain"
