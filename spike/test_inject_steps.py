"""分步调试注入：看每一步 editorText 的真实变化。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient, JS_FOCUS_CLEAR  # noqa: E402

TXT = "⟦WB:dbg⟧ 分步注入测试"


async def st(c, tag):
    s = await c.state()
    print(f"  {tag}: editor={ (s.get('editorText') or '')[:50]!r } "
          f"list={len(s.get('listText') or '')} sending={s.get('sending')}")
    return s


async def main() -> None:
    c = await CdpClient.connect()
    try:
        await st(c, "[0] 初始")

        print("[1] 直接 insertText（不 focus、不清空）")
        await c.call("Input.insertText", text=TXT)
        await asyncio.sleep(0.6)
        await st(c, "after insertText")

        print("[2] 显式 focus 后 insertText")
        r = await c.js(
            "(() => { const e=document.querySelector('div[contenteditable=\"true\"]');"
            "if(!e) return 'none'; e.focus();"
            "return JSON.stringify({active: document.activeElement === e,"
            " dt: e.getAttribute('data-slate-editor')}); })()"
        )
        print("   focus:", r)
        await c.call("Input.insertText", text=TXT + "B")
        await asyncio.sleep(0.6)
        await st(c, "after focus+insertText")

        print("[3] 试 execCommand insertText")
        r = await c.js(
            "(() => { const e=document.querySelector('div[contenteditable=\"true\"]');"
            "e.focus(); return document.execCommand('insertText', false, "
            + json.dumps(TXT + "C", ensure_ascii=False) + "); })()"
        )
        print("   execCommand:", r)
        await asyncio.sleep(0.6)
        await st(c, "after execCommand")

        print("[4] JS_FOCUS_CLEAR 清空")
        print("   ", await c.js(JS_FOCUS_CLEAR))
        await asyncio.sleep(0.5)
        await st(c, "after clear")
    finally:
        await c.close()


asyncio.run(main())
