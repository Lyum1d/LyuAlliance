"""找出 Slate 组件的 onChange prop，并尝试用它更新 WorkBuddy 的 state。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient  # noqa: E402

# 1) 在 fiber 链上找所有带 onChange 的 props，并看 value 形状
JS_FIND = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  const fk = Object.keys(ed).find(k => k.startsWith('__reactFiber$'));
  let f = ed[fk], depth = 0;
  const hits = [];
  while (f && depth < 60) {
    const p = f.memoizedProps;
    if (p && typeof p === 'object' && typeof p.onChange === 'function') {
      hits.push({
        d: depth,
        type: typeof f.type === 'function' ? (f.type.name || 'anon') : String(f.type).slice(0, 24),
        keys: Object.keys(p).slice(0, 14),
        valueType: Array.isArray(p.value) ? 'array(' + p.value.length + ')' : typeof p.value,
        valueSample: (() => { try { return JSON.stringify(p.value).slice(0, 200); } catch (e) { return 'circular'; } })(),
        onChangeLen: p.onChange.length,
      });
    }
    f = f.return; depth++;
  }
  return JSON.stringify(hits);
})()
"""


async def main() -> None:
    c = await CdpClient.connect()
    try:
        hits = json.loads(await c.js(JS_FIND) or "[]")
        print(json.dumps(hits, ensure_ascii=False, indent=1)[:3500])
    finally:
        await c.close()


asyncio.run(main())
