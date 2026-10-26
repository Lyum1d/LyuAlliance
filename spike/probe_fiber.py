"""探测 React fiber，找 Slate editor 实例 / onChange。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient  # noqa: E402

JS = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  if (!ed) return JSON.stringify({err: 'no editor'});
  const fk = Object.keys(ed).find(k => k.startsWith('__reactFiber$'));
  const pk = Object.keys(ed).find(k => k.startsWith('__reactProps$'));
  const props = pk ? ed[pk] : null;
  const out = {
    fiberKey: fk || null,
    propsKey: pk || null,
    propsNames: props ? Object.keys(props).slice(0, 20) : [],
  };
  // 沿 fiber 向上找 editor 实例或 value/onChange
  let f = fk ? ed[fk] : null;
  const seen = [];
  let editor = null, valueOnChange = null;
  let depth = 0;
  while (f && depth < 40) {
    const p = f.memoizedProps || {};
    const names = Object.keys(p);
    if (names.length) {
      seen.push({d: depth, type: typeof f.type === 'function' ? (f.type.name || 'anon') : String(f.type).slice(0, 20),
                 names: names.slice(0, 12)});
    }
    if (!editor && p.editor && typeof p.editor.insertText === 'function') editor = p.editor;
    if (!valueOnChange && Array.isArray(p.value) && typeof p.onChange === 'function')
      valueOnChange = {value: p.value, hasOnChange: true};
    f = f.return; depth++;
  }
  out.chain = seen.slice(0, 14);
  if (editor) {
    out.editor = {
      keys: Object.keys(editor).slice(0, 25),
      methods: Object.keys(editor).filter(k => typeof editor[k] === 'function').slice(0, 30),
      selection: editor.selection ? JSON.stringify(editor.selection).slice(0, 120) : null,
      hasIsEditor: !!editor.isEditor,
    };
  } else out.editor = null;
  out.valueOnChange = valueOnChange ? {has: true, len: valueOnChange.value.length,
                                       sample: JSON.stringify(valueOnChange.value).slice(0, 160)} : null;
  return JSON.stringify(out);
})()
"""


async def main() -> None:
    c = await CdpClient.connect()
    try:
        d = json.loads(await c.js(JS) or "{}")
        print(json.dumps(d, ensure_ascii=False, indent=1)[:4000])
    finally:
        await c.close()


asyncio.run(main())
