"""诊断前台窗口：SetForegroundWindow 是否真的把 WorkBuddy 提到前面。"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()


def fg_title() -> str:
    h = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(300)
    user32.GetWindowTextW(h, buf, 300)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return f"{h} | {buf.value!r} | pid={pid.value}"


print("1) 当前前台:", fg_title())

h = user32.FindWindowW(None, "WorkBuddy")
print("2) FindWindowW(WorkBuddy):", h)

# 经典技巧：先发一下 Alt，解除前台窗口锁
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
user32.keybd_event(VK_MENU, 0, 0, 0)
user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
time.sleep(0.1)

if h:
    user32.ShowWindow(h, 9)
    ok = user32.SetForegroundWindow(h)
    print("3) SetForegroundWindow ->", ok)
    time.sleep(0.5)
    print("4) 激活后前台:", fg_title())

    # 再试 BringWindowToTop
    user32.BringWindowToTop(h)
    time.sleep(0.4)
    print("5) BringWindowToTop 后前台:", fg_title())

    print("6) IsWindowVisible:", user32.IsWindowVisible(h))
    print("   IsIconic(最小化):", user32.IsIconic(h))
