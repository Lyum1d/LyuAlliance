"""测试：剪贴板粘贴中文 + 清空编辑器。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import wb_input as wi  # noqa: E402
from app.wb_bridge import CdpClient  # noqa: E402

JS_ACTIVE = r"""
(() => {
  const a = document.activeElement;
  const ed = document.querySelector('div[contenteditable="true"]');
  const btn = document.querySelector('.cr-send-button');
  return JSON.stringify({
    isEd: a && a.getAttribute('contenteditable') === 'true',
    activeText: a ? (a.innerText || '').slice(0, 80) : null,
    edText: ed ? (ed.innerText || '').slice(0, 80) : null,
    sendDisabled: btn ? (!!btn.disabled || btn.getAttribute('aria-disabled') === 'true') : null,
  });
})()
"""


async def main() -> None:
    wi.focus_window()
    await asyncio.sleep(0.5)
    c = await CdpClient.connect()
    try:
        await c.js("document.querySelector('div[contenteditable=\"true\"]').focus()")
        await asyncio.sleep(0.3)
        print("[0]", await c.js(JS_ACTIVE))

        print("[1] 清空")
        wi.clear_editor()
        await asyncio.sleep(0.5)
        print("   ", await c.js(JS_ACTIVE))

        print("[2] 写剪贴板 + Ctrl+V")
        ok = wi.set_clipboard("中文粘贴测试：这是桥接注入的文本，含标点、数字 123 和换行\n第二行")
        print("   clipboard set:", ok)
        wi.paste()
        await asyncio.sleep(1.2)
        print("   ", await c.js(JS_ACTIVE))
    finally:
        await c.close()


asyncio.run(main())
