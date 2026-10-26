"""聚焦探测 WorkBuddy 输入区附近的按钮与消息容器。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient  # noqa: E402

JS = r"""
(() => {
  const ed = [...document.querySelectorAll('[contenteditable="true"]')]
    .find(e => { const r = e.getBoundingClientRect(); return r.width > 100 && r.height > 0; });
  if (!ed) return JSON.stringify({err: 'no editor'});
  const er = ed.getBoundingClientRect();
  const out = {editor: {tag: ed.tagName, cls: ed.className,
                        rect: [er.x|0, er.y|0, er.width|0, er.height|0],
                        ce: ed.getAttribute('contenteditable'),
                        inner: (ed.innerHTML || '').slice(0, 200)}};

  // 输入框四周 160px 内的可点击元素
  const near = [];
  for (const e of document.querySelectorAll('button,[role="button"],svg,[class*="btn"],[class*="send"]')) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0 || r.width > 200 || r.height > 200) continue;
    if (r.top < er.top - 160 || r.top > er.bottom + 60) continue;
    if (r.left < er.left - 220 || r.left > er.right + 220) continue;
    near.push({
      tag: e.tagName,
      aria: e.getAttribute('aria-label') || '',
      title: e.getAttribute('title') || '',
      cls: (typeof e.className === 'string' ? e.className : (e.className.baseVal || '')).slice(0, 60),
      dt: e.getAttribute('data-testid') || '',
      dis: !!e.disabled,
      rect: [r.x|0, r.y|0, r.width|0, r.height|0],
      html: (e.outerHTML || '').slice(0, 160),
    });
  }
  out.near = near.slice(0, 25);

  // 消息滚动区
  const sc = [];
  for (const d of document.querySelectorAll('div,section,main')) {
    const s = getComputedStyle(d), r = d.getBoundingClientRect();
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && r.width > 400 && r.height > 200) {
      sc.push({cls: (typeof d.className === 'string' ? d.className : '').slice(0, 60),
               dt: d.getAttribute('data-testid') || '',
               rect: [r.x|0, r.y|0, r.width|0, r.height|0],
               sh: d.scrollHeight, ch: d.clientHeight,
               tail: (d.innerText || '').replace(/\s+/g, ' ').slice(-100)});
    }
  }
  // 只保留左侧边界 > 300 的（排除侧边栏）
  out.scrollers = sc.filter(o => o.rect[0] > 300).slice(0, 5);
  out.win = [window.innerWidth, window.innerHeight, window.devicePixelRatio];
  out.bodyText = (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 600);
  return JSON.stringify(out);
})()
"""


async def main() -> None:
    cli = await CdpClient.connect()
    try:
        print(await cli.js(JS))
    finally:
        await cli.close()


asyncio.run(main())
