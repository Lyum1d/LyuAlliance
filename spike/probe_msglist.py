"""探测消息列表 DOM 结构，区分 user / assistant 气泡。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient  # noqa: E402

JS = r"""
(() => {
  const list = document.querySelector('.cr-message-list__content')
            || document.querySelector('[class*="cr-message-list"]');
  if (!list) return JSON.stringify({err: 'no list'});
  const kids = [...list.children].map((e, i) => {
    const r = e.getBoundingClientRect();
    return {
      i,
      cls: (typeof e.className === 'string' ? e.className : '').slice(0, 90),
      dt: e.getAttribute('data-testid') || '',
      h: r.height|0,
      len: (e.innerText || '').length,
      head: (e.innerText || '').replace(/\s+/g, ' ').slice(0, 90),
    };
  });
  // 深一层看每个子元素里的 role / data 属性
  const roles = [];
  for (const e of list.querySelectorAll('[data-role],[role],[data-message-role],[data-author]')) {
    const r = e.getBoundingClientRect();
    roles.push({
      role: e.getAttribute('data-role') || e.getAttribute('role') || e.getAttribute('data-message-role') || e.getAttribute('data-author'),
      cls: (typeof e.className === 'string' ? e.className : '').slice(0, 60),
      len: (e.innerText || '').length,
      head: (e.innerText || '').replace(/\s+/g, ' ').slice(0, 60),
    });
  }
  return JSON.stringify({count: kids.length, kids, roles: roles.slice(0, 12)}, null, 0);
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
