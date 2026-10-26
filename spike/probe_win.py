"""只读探针 2：看 Electron 应用的启动命令行 + 可见顶层窗口几何。
不做任何修改，不重启任何进程。
"""
import ctypes
import ctypes.wintypes as wt
import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)
TARGETS = ["Doubao", "DeepSeek Harness", "Trae", "WorkBuddy", "Codex"]
FLAGS_OF_INTEREST = ("remote-debugging", "enable-logging", "inspect", "a11y", "accessibility", "no-sandbox")


def cmdlines():
    rows = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            n = (p.info["name"] or "").lower()
            if any(t.lower().split()[0] in n for t in TARGETS):
                cl = " ".join(p.cmdline())
                rows.append((p.info["pid"], p.info["name"], cl))
        except Exception:
            pass
    return rows


def windows():
    result = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 2)
        user32.GetWindowTextW(hwnd, buf, length + 2)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        result.append({
            "hwnd": hwnd,
            "pid": pid.value,
            "title": buf.value,
            "class": cls.value,
            "w": rect.right - rect.left,
            "h": rect.bottom - rect.top,
        })
        return True

    user32.EnumWindows(cb, 0)
    return result


def main():
    rows = cmdlines()
    print("=== 启动命令行（按进程名去重）===")
    seen = set()
    for pid, name, cl in rows:
        key = (name, cl)
        if key in seen:
            continue
        seen.add(key)
        hit = [f for f in FLAGS_OF_INTEREST if f in cl.lower()]
        flag = f"  <== 含调试/无障碍参数: {hit}" if hit else ""
        print(f"pid={pid:<6} {name:<22} {cl[:150]}{flag}")
        if not cl:
            print(f"pid={pid:<6} {name:<22} (命令行不可读，可能权限不足)")

    pids = {pid for pid, _, _ in rows}
    print("\n=== 可见顶层窗口 ===")
    for w in windows():
        if w["pid"] in pids:
            print(f"pid={w['pid']:<6} {w['w']:>5}x{w['h']:<5} class={w['class']:<22} title={w['title'][:60]!r}")


if __name__ == "__main__":
    main()
