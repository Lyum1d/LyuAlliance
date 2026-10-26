"""用 Slate editor 实例直接注入文本，验证按钮是否启用。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient  # noqa: E402

TXT = "⟦WB:dbg3⟧ Slate 实例注入测试"

JS_INJECT = r"""
(() => {
  const txt = %s;
  const ed = document.querySelector('div[contenteditable="true"]');
  if (!ed) return JSON.stringify({r: 'no-dom'});
  const fk = Object.keys(ed).find(k => k.startsWith('__reactFiber$'));
  if (!fk) return JSON.stringify({r: 'no-fiber'});
  let f = ed[fk], editor = null, depth = 0;
  while (f && depth < 40) {
    const p = f.memoizedProps || {};
    if (p.editor && typeof p.editor.insertText === 'function') { editor = p.editor; break; }
    f = f.return; depth++;
  }
  if (!editor) return JSON.stringify({r: 'no-editor'});
  try {
    const start = editor.start([]);
    const end = editor.end([]);
    editor.selection = {anchor: start, focus: end};
    if (JSON.stringify(start) !== JSON.stringify(end)) editor.deleteFragment();
    const s2 = editor.start([]);
    editor.selection = {anchor: s2, focus: s2};
    editor.insertText(txt);
  } catch (e) {
    return JSON.stringify({r: 'err', msg: String(e).slice(0, 160)});
  }
  return JSON.stringify({r: 'ok', depth: depth,
    domText: (ed.innerText || '').slice(0, 80),
    sel: editor.selection ? JSON.stringify(editor.selection) : null});
})()
""" % json.dumps(TXT, ensure_ascii=False)


async def main() -> None:
    c = await CdpClient.connect()
    try:
        print("inject:", await c.js(JS_INJECT))
        await asyncio.sleep(1.2)
        s = await c.state()
        print("editor:", repr((s.get("editorText") or "")[:60]))
        print("disabled:", s.get("sendDisabled"), "| cls:", (s.get("sendCls") or "")[-45:])
    finally:
        await c.close()


asyncio.run(main())
