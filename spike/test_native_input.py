"""验证：用 Windows 原生鼠标/键盘事件驱动 WorkBuddy 输入框。

思路：CDP 的 Input 域在这个构建里被禁用了（insertText / dispatchKeyEvent 全无反应），
但 OS 级的 SendInput 是应用无法分辨的真输入，会走完整的 React/Slate 管线。
先只验证坐标命中（:hover），再考虑真点击。
"""
import asyncio
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient  # noqa: E402

user32 = ctypes.windll.user32

# 让本进程 DPI 感知，否则 GetCursorPos/SetCursorPos 会被虚拟化到 96 DPI
for api in ("SetProcessDpiAwarenessContext", "SetProcessDpiAwareness", "SetProcessDPIAware"):
    try:
        if api == "SetProcessDpiAwarenessContext":
            getattr(ctypes.windll.user32, api)(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        elif api == "SetProcessDpiAwareness":
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        else:
            getattr(ctypes.windll.user32, api)()
        print(f"[dpi] {api} ok")
        break
    except Exception as e:
        print(f"[dpi] {api} fail: {e}")

JS_BOX = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  const btn = document.querySelector('.cr-send-button');
  if (!ed) return JSON.stringify({err: 'no editor'});
  const er = ed.getBoundingClientRect(), br = btn.getBoundingClientRect();
  return JSON.stringify({
    screen: [window.screenX, window.screenY], dpr: window.devicePixelRatio,
    editor: [er.x, er.y, er.width, er.height],
    send: [br.x, br.y, br.width, br.height],
  });
})()
"""

JS_HOVER = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  const btn = document.querySelector('.cr-send-button');
  return JSON.stringify({
    edHover: ed ? ed.matches(':hover') : null,
    btnHover: btn ? btn.matches(':hover') : null,
    activeIsEditor: document.activeElement === ed,
    editorText: ed ? (ed.innerText || '').slice(0, 40) : null,
    sendDisabled: btn ? (!!btn.disabled || btn.getAttribute('aria-disabled') === 'true') : null,
  });
})()
"""


def dip_to_physical(x: float, y: float, dpr: float) -> tuple[int, int]:
    return int(round(x * dpr)), int(round(y * dpr))


async def main() -> None:
    # 先把窗口提到前台
    h = user32.FindWindowW(None, "WorkBuddy")
    if h:
        user32.ShowWindow(h, 9)
        user32.SetForegroundWindow(h)
        print(f"[win] hwnd={h} 已激活")
    await asyncio.sleep(0.8)

    c = await CdpClient.connect()
    try:
        box = json.loads(await c.js(JS_BOX) or "{}")
        print("[box]", json.dumps(box, ensure_ascii=False))
        dpr = box["dpr"]
        sx, sy = box["screen"]
        ex, ey, ew, eh = box["editor"]

        # 输入框中心（DIP → 物理）
        cx_dip = sx + ex + ew / 2
        cy_dip = sy + ey + eh / 2
        px, py = dip_to_physical(cx_dip, cy_dip, dpr)
        print(f"[cursor] dip=({cx_dip:.0f},{cy_dip:.0f}) -> physical=({px},{py})")

        user32.SetCursorPos(px, py)
        await asyncio.sleep(0.5)

        got = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(got))
        print(f"[cursor] GetCursorPos=({got.x},{got.y})")

        await asyncio.sleep(0.3)
        print("[hover]", await c.js(JS_HOVER))
    finally:
        await c.close()


asyncio.run(main())
