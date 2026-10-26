"""CDP 只读探针：连上豆包的 DevTools，检查 DOM 里有没有可用的输入框与消息容器。
不点击、不输入、不改状态。
用法: python probe_cdp.py [--port 9333]
"""
import asyncio
import json
import sys

import httpx
import websockets

PORT = 9333
for i, a in enumerate(sys.argv):
    if a == "--port" and i + 1 < len(sys.argv):
        PORT = int(sys.argv[i + 1])

JS = r"""
(() => {
  const rect = e => { try { const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; } catch(_) { return null; } };
  const cls = e => (typeof e.className === 'string' ? e.className : '').slice(0, 60);
  const q = s => Array.from(document.querySelectorAll(s));
  const boxes = q('textarea, [contenteditable="true"], [role="textbox"]').map(e => ({
    tag: e.tagName, cls: cls(e), rect: rect(e), ph: e.getAttribute('placeholder') || e.getAttribute('data-placeholder') || ''
  }));
  const btns = q('button, [role="button"]').slice(0, 40).map(e => ({
    txt: (e.innerText || '').replace(/\s+/g, ' ').slice(0, 18),
    aria: e.getAttribute('aria-label') || '',
    cls: cls(e), rect: rect(e)
  })).filter(b => b.txt || b.aria);
  return JSON.stringify({
    title: document.title, url: location.href,
    bodyLen: (document.body ? document.body.innerText.length : 0),
    editors: boxes, buttons: btns,
    msgHints: {
      dataTestid: q('[data-testid]').slice(0, 15).map(e => e.getAttribute('data-testid')),
      markdownBlocks: q('.markdown, .md-body, [class*="markdown"]').length,
      msgClassLike: q('[class*="message"], [class*="msg"], [class*="bubble"]').length
    }
  });
})()
"""


async def dump_target(client: httpx.AsyncClient, t: dict) -> None:
    print(f"\n{'='*72}\n[{t.get('type')}] title={t.get('title')!r}\n  url={t.get('url')}")
    ws_url = t.get("webSocketDebuggerUrl")
    if not ws_url:
        print("  无 ws 调试地址，跳过")
        return
    try:
        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024, open_timeout=8) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                      "params": {"expression": JS, "returnByValue": True,
                                                 "awaitPromise": True}}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                if msg.get("id") != 1:
                    continue
                if "error" in msg:
                    print("  求值失败:", msg["error"])
                    return
                val = msg["result"]["result"].get("value")
                if not val:
                    print("  返回空:", str(msg["result"])[:200])
                    return
                d = json.loads(val)
                print(f"  document.title={d['title']!r}  可见文本长度={d['bodyLen']}")
                print(f"  输入类元素 {len(d['editors'])} 个:")
                for e in d["editors"][:6]:
                    print(f"    - <{e['tag']}> rect={e['rect']} ph={e['ph'][:30]!r} cls={e['cls']!r}")
                print(f"  按钮 {len(d['buttons'])} 个 (前 12):")
                for b in d["buttons"][:12]:
                    print(f"    - {b['txt']!r} aria={b['aria']!r} rect={b['rect']}")
                print(f"  消息线索: {d['msgHints']}")
                return
    except Exception as ex:
        print(f"  连接失败: {type(ex).__name__}: {ex}")


async def main():
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(f"http://127.0.0.1:{PORT}/json/list")
        targets = r.json()
    print(f"共 {len(targets)} 个 target")
    for t in targets:
        if t.get("type") in ("page", "webview", "other"):
            await dump_target(c, t)


if __name__ == "__main__":
    asyncio.run(main())
