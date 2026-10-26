"""验证：普通 VK 键能否输入；页面有几个 contenteditable。"""
import asyncio
import ctypes
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import wb_input as wi  # noqa: E402
from app.wb_bridge import CdpClient  # noqa: E402

JS_EDITORS = r"""
(() => {
  const all = [...document.querySelectorAll('[contenteditable="true"]')].map((e, i) => {
    const r = e.getBoundingClientRect();
    return {i, cls: (typeof e.className === 'string' ? e.className : '').slice(0, 40),
            vis: r.width > 60 && r.height > 0,
            rect: [r.x|0, r.y|0, r.width|0, r.height|0],
            isActive: document.activeElement === e,
            text: (e.innerText || '').slice(0, 40)};
  });
  return JSON.stringify({count: all.length, list: all});
})()
"""

JS_ACTIVE = r"""
(() => {
  const a = document.activeElement;
  return JSON.stringify({
    tag: a ? a.tagName : null,
    isEd: a ? a.getAttribute('contenteditable') === 'true' : null,
    text: a ? (a.innerText || '').slice(0, 60) : null,
    allTexts: [...document.querySelectorAll('[contenteditable="true"]')]
      .map(e => (e.innerText || '').slice(0, 30)),
  });
})()
"""

user32 = ctypes.windll.user32


def vk_type_ascii(text: str) -> None:
    for ch in text:
        state = user32.VkKeyScanW(ord(ch))
        vk = state & 0xFF
        shift = bool((state >> 8) & 1)
        seq = []
        if shift:
            seq.append((0x10, False))
        seq.append((vk, False))
        seq.append((vk, True))
        if shift:
            seq.append((0x10, True))
        for v, up in seq:
            user32.keybd_event(v, 0, (2 if up else 0), 0)
            ctypes.windll.kernel32.Sleep(10)


async def main() -> None:
    wi.focus_window()
    await asyncio.sleep(0.5)
    c = await CdpClient.connect()
    try:
        print("[editors]", await c.js(JS_EDITORS))
        await c.js("document.querySelector('div[contenteditable=\"true\"]').focus()")
        await asyncio.sleep(0.3)
        print("[before]", await c.js(JS_ACTIVE))

        vk_type_ascii("HELLO")
        await asyncio.sleep(1.0)
        print("[after VK ascii]", await c.js(JS_ACTIVE))

        st = await c.state()
        print("[state] editor=", repr((st.get("editorText") or "")[:50]),
              "disabled=", st.get("sendDisabled"))
    finally:
        await c.close()


asyncio.run(main())
