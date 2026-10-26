"""无损检测：发 Tab 键，看 WorkBuddy 的焦点是否移动。"""
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
  return JSON.stringify({
    tag: a ? a.tagName : null,
    cls: a && typeof a.className === 'string' ? a.className.slice(0, 60) : null,
    aria: a ? (a.getAttribute('aria-label') || '') : null,
    txt: a ? (a.innerText || '').replace(/\s+/g, ' ').slice(0, 30) : null,
    isEditor: a ? a.getAttribute('contenteditable') === 'true' : null,
  });
})()
"""


async def main() -> None:
    wi.focus_window()
    await asyncio.sleep(0.6)
    c = await CdpClient.connect()
    try:
        before = json.loads(await c.js(JS_ACTIVE) or "{}")
        print("before:", json.dumps(before, ensure_ascii=False))

        wi.key_combo(0x09)          # Tab
        await asyncio.sleep(0.7)
        after1 = json.loads(await c.js(JS_ACTIVE) or "{}")
        print("after Tab:", json.dumps(after1, ensure_ascii=False))
        changed = (before.get("tag"), before.get("cls")) != (after1.get("tag"), after1.get("cls"))
        print("==> 焦点是否移动:", changed)

        # 还原焦点到输入框
        await c.js("document.querySelector('div[contenteditable=\"true\"]').focus()")
        await asyncio.sleep(0.3)
        print("restored:", json.loads(await c.js(JS_ACTIVE) or "{}").get("isEditor"))
    finally:
        await c.close()


asyncio.run(main())
