"""探测 2：发送按钮状态机 + 消息容器 + Slate 可写性（只写不发送）。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient  # noqa: E402

JS_STATE = r"""
(() => {
  const btn = document.querySelector('.cr-send-button');
  const tb = document.querySelector('.cr-input-toolbar__send');
  const ed = document.querySelector('div[contenteditable="true"]');
  // 找主消息区：左侧 > 300、宽度 > 500、有大量文本的元素
  let best = null;
  for (const d of document.querySelectorAll('div,main,section')) {
    const r = d.getBoundingClientRect();
    if (r.left < 300 || r.width < 500 || r.height < 300) continue;
    const t = (d.innerText || '');
    if (!best || t.length > (best.innerText || '').length) best = d;
  }
  return JSON.stringify({
    sendBtn: btn ? {cls: btn.className, aria: btn.getAttribute('aria-label'),
                    dis: btn.disabled, html: btn.outerHTML.slice(0, 260)} : null,
    toolbar: tb ? tb.className + ' | ' + tb.innerText.trim().slice(0, 40) : null,
    editorCls: ed ? ed.className : null,
    editorText: ed ? JSON.stringify(ed.innerText).slice(0, 80) : null,
    main: best ? {cls: (typeof best.className === 'string' ? best.className : '').slice(0, 70),
                  len: (best.innerText || '').length,
                  rect: [best.getBoundingClientRect().x|0, best.getBoundingClientRect().y|0,
                         best.getBoundingClientRect().width|0, best.getBoundingClientRect().height|0],
                  head: (best.innerText || '').replace(/\s+/g, ' ').slice(0, 120),
                  tail: (best.innerText || '').replace(/\s+/g, ' ').slice(-260)} : null,
    bodyLen: document.body.innerText.length,
  });
})()
"""


async def main() -> None:
    cli = await CdpClient.connect()
    try:
        d = json.loads(await cli.js(JS_STATE) or "{}")
        print(json.dumps(d, ensure_ascii=False, indent=1))
    finally:
        await cli.close()


asyncio.run(main())
