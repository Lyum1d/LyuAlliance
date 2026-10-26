"""CDP 探针 2：定位发送按钮、消息容器、以及"是否正在生成"的可判定信号。
只读，不点击不输入。
"""
import asyncio
import json

import httpx
import websockets

PORT = 9333
CHAT_URL = "doubao://doubao-chat/chat"

JS = r"""
(() => {
  const out = {};
  const R = e => { const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const C = e => (typeof e.className === 'string' ? e.className : '').slice(0, 70);

  const ed = document.querySelector('.tiptap.ProseMirror');
  out.editor = ed ? { rect: R(ed), html: ed.innerHTML.slice(0, 120), editable: ed.getAttribute('contenteditable') } : null;
  if (ed) {
    const chain = [];
    let p = ed.parentElement;
    for (let i = 0; i < 4 && p; i++) { chain.push(p.tagName + '.' + C(p)); p = p.parentElement; }
    out.editorParents = chain;
  }

  out.candidateButtons = Array.from(document.querySelectorAll('button,[role="button"]'))
    .map(b => {
      const r = b.getBoundingClientRect();
      return { t: (b.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 14),
               a: b.getAttribute('aria-label') || '', x: Math.round(r.x), y: Math.round(r.y),
               w: Math.round(r.width), h: Math.round(r.height),
               cls: C(b), dt: b.getAttribute('data-testid') || '' };
    })
    .filter(o => o.w > 0 && (/发送|停止|send|stop/i.test(o.t + o.a) || (o.y > 740 && o.y < 1000)));

  out.testids = [...new Set(Array.from(document.querySelectorAll('[data-testid]')).map(e => e.getAttribute('data-testid')))];

  const scrollers = Array.from(document.querySelectorAll('div')).filter(d => {
    const s = getComputedStyle(d);
    return (s.overflowY === 'auto' || s.overflowY === 'scroll') && d.scrollHeight > d.clientHeight + 40;
  });
  out.scrollers = scrollers.slice(0, 3).map(d => ({ cls: C(d), kids: d.children.length, sh: d.scrollHeight, ch: d.clientHeight }));
  if (scrollers.length) {
    const sc = scrollers[0];
    out.msgNodes = Array.from(sc.children).slice(-4).map(c => ({
      tag: c.tagName, cls: C(c), dt: c.getAttribute('data-testid') || '',
      txt: (c.innerText || '').replace(/\s+/g, ' ').slice(0, 80)
    }));
  }

  out.stopHints = Array.from(document.querySelectorAll('[class*="stop"],[class*="loading"],[class*="generating"],[class*="typing"]'))
    .slice(0, 8).map(e => ({ tag: e.tagName, cls: C(e) }));
  return JSON.stringify(out, null, 1);
})()
"""


async def main():
    async with httpx.AsyncClient(timeout=8) as c:
        targets = (await c.get(f"http://127.0.0.1:{PORT}/json/list")).json()
    t = next((x for x in targets if x.get("url") == CHAT_URL and x.get("webSocketDebuggerUrl")), None)
    if not t:
        print("未找到聊天页 target，现有：")
        for x in targets:
            print("  ", x.get("type"), x.get("url"))
        return
    print(f"目标: {t['title']!r} {t['url']}")
    async with websockets.connect(t["webSocketDebuggerUrl"], max_size=8 << 20, open_timeout=8) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": JS, "returnByValue": True}}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if msg.get("id") != 1:
                continue
            if "error" in msg:
                print("求值失败:", msg["error"])
                return
            print(msg["result"]["result"].get("value", "(空)"))
            return


if __name__ == "__main__":
    asyncio.run(main())
