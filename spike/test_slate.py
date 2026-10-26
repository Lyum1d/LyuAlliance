"""测试 Slate 编辑器可写性：写入测试文本 -> 回读 -> 清空。不点发送。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient  # noqa: E402

TEST = "AM-PROBE-9x7 这行是自动化探针写入的测试文本，不需要回答"

JS_FOCUS = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  if (!ed) return '';
  ed.focus();
  const r = ed.getBoundingClientRect();
  return JSON.stringify({cls: ed.className, rect: [r.x|0, r.y|0, r.width|0, r.height|0],
                         before: (ed.innerText || '').slice(0, 60)});
})()
"""

JS_READ = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  return JSON.stringify({text: ed ? (ed.innerText || '').slice(0, 200) : null,
                         html: ed ? ed.innerHTML.slice(0, 200) : null});
})()
"""

JS_CLEAR = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  if (!ed) return 'no editor';
  ed.focus();
  const sel = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(ed);
  sel.removeAllRanges();
  sel.addRange(range);
  const ok = document.execCommand('delete', false, null);
  return JSON.stringify({cleared: ok, after: (ed.innerText || '').slice(0, 80)});
})()
"""


async def main() -> None:
    cli = await CdpClient.connect()
    try:
        print("[0] focus:", await cli.js(JS_FOCUS))
        await cli.call("Input.insertText", text=TEST)
        await asyncio.sleep(0.5)
        print("[1] after insertText:", await cli.js(JS_READ))

        # 若 insertText 无效，退回 execCommand
        cur = json.loads(await cli.js(JS_READ) or "{}")
        if "AM-PROBE" not in (cur.get("text") or ""):
            print("[2] insertText 未生效，试 execCommand …")
            ok = await cli.js(
                "(() => { const ed=document.querySelector('div[contenteditable=\"true\"]');"
                "ed.focus(); return document.execCommand('insertText', false, %s); })()"
                % json.dumps(TEST, ensure_ascii=False)
            )
            await asyncio.sleep(0.5)
            print("    execCommand:", ok, "→", await cli.js(JS_READ))

        print("[3] clear:", await cli.js(JS_CLEAR))
        await asyncio.sleep(0.3)
        print("[4] final:", await cli.js(JS_READ))
    finally:
        await cli.close()


asyncio.run(main())
