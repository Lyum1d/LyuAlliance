"""OS 级输入原语：真实鼠标点击 + 真实键盘（Unicode）输入。

CDP 的 Input 域在 WorkBuddy 这个构建里被禁用（insertText / dispatchKeyEvent 均无效），
只有 OS 级 SendInput 能穿透；应用无法把它和真人操作区分开。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

# DPI 感知，否则坐标会被虚拟化
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def focus_window(title: str = "WorkBuddy") -> int:
    h = user32.FindWindowW(None, title)
    if h:
        user32.ShowWindow(h, 9)          # SW_RESTORE
        user32.SetForegroundWindow(h)
        time.sleep(0.35)
    return h


def click(x: int, y: int) -> None:
    """物理像素坐标点击。"""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    ev = (INPUT * 2)()
    ev[0].type = INPUT_MOUSE
    ev[0].u.mi.dwFlags = MOUSEEVENTF_LEFTDOWN
    ev[1].type = INPUT_MOUSE
    ev[1].u.mi.dwFlags = MOUSEEVENTF_LEFTUP
    user32.SendInput(2, ev, ctypes.sizeof(INPUT))
    time.sleep(0.12)


def type_text(text: str, chunk: int = 24, delay: float = 0.06) -> int:
    """按 Unicode 码点逐字发送真实键盘事件，返回发出的字符数。"""
    sent = 0
    for start in range(0, len(text), chunk):
        part = text[start:start + chunk]
        n = len(part) * 2
        ev = (INPUT * n)()
        i = 0
        for ch in part:
            code = ord(ch)
            # 代理对（emoji 等）拆成两个 UTF-16 单元由系统处理
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                ev[i].type = INPUT_KEYBOARD
                ev[i].u.ki.wVk = 0
                ev[i].u.ki.wScan = code & 0xFFFF
                ev[i].u.ki.dwFlags = flags
                i += 1
            sent += 1
        user32.SendInput(n, ev, ctypes.sizeof(INPUT))
        time.sleep(delay)
    return sent


def key_combo(vk: int, ctrl: bool = False, shift: bool = False) -> None:
    """发组合键，如 Ctrl+A / Enter。"""
    def key(v, up=False):
        e = INPUT()
        e.type = INPUT_KEYBOARD
        e.u.ki.wVk = v
        e.u.ki.wScan = 0
        f = 0
        if up:
            f |= KEYEVENTF_KEYUP
        e.u.ki.dwFlags = f
        return e

    seq = []
    if ctrl:
        seq.append(key(0x11))       # VK_CONTROL down
    if shift:
        seq.append(key(0x10))
    seq.append(key(vk))
    seq.append(key(vk, True))
    if shift:
        seq.append(key(0x10, True))
    if ctrl:
        seq.append(key(0x11, True))
    arr = (INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))


def press_enter() -> None:
    key_combo(0x0D)


# ---------- 剪贴板 ----------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

kernel32 = ctypes.windll.kernel32

# 64 位下必须声明句柄类型，否则返回值被截断成 32 位，GlobalLock 拿到野指针
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
kernel32.GlobalFree.argtypes = (ctypes.c_void_p,)
user32.OpenClipboard.argtypes = (ctypes.c_void_p,)
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = (wintypes.UINT, ctypes.c_void_p)


def set_clipboard(text: str) -> bool:
    """把文本写进系统剪贴板（支持中文/emoji/换行）。"""
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        if not p:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True                 # 成功后所有权归系统，不可 GlobalFree
    finally:
        user32.CloseClipboard()


def paste() -> None:
    """Ctrl+V。"""
    key_combo(0x56, ctrl=True)
    time.sleep(0.35)


def clear_editor() -> None:
    """Ctrl+A + Delete，清空输入框现有内容。"""
    key_combo(0x41, ctrl=True)
    time.sleep(0.12)
    key_combo(0x2E)          # VK_DELETE
    time.sleep(0.12)

