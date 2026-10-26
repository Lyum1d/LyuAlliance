"""验证豆包「新工作任务」能否作为执行体被驱动。

流程：定位输入框 → 注入任务 → 发送 → 轮询工作台是否产出 → 抓文本。
只读失败即退出，不改任何设置。
"""
import asyncio
import json
import sys
import time

import httpx
import websockets

PORT = 9333
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "用一句话说明什么是 JWT，不超过 30 字。"

FIND = r"""
(() => {
  const eds = [...document.querySelectorAll('.tiptap.ProseMirror, [contenteditable="true"]')]
    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 100 && r.height > 0; });
  if (!eds.length) return JSON.stringify({ok:false, reason:'no-editor'});
  const e = eds[eds.length - 1];
  e.focus();
  return JSON.stringify({ok:true, cls:(typeof e.className==='string'?e.className:'').slice(0,40),
    rect:(()=>{const r=e.getBoundingClientRect();return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];})()});
})()
"""

STATE = r"""
(() => {
  const wb = document.querySelector('[data-testid="right-panel-workbench-layer"]');
  const sk = document.querySelector('[data-testid="office-loading-skeleton-docx"]');
  const stop = [...document.querySelectorAll('button,[role="button"]')]
    .find(b => /停止|中止|stop/i.test((b.innerText||'') + (b.getAttribute('aria-label')||'')));
  return JSON.stringify({
    wbLen: wb ? wb.innerText.length : -1,
    wbText: wb ? wb.innerText.replace(/\s+/g,' ').slice(-400) : '',
    skeleton: !!sk,
    generating: !!stop,
    bodyLen: document.body.innerText.length
  });
})()
"""


class Cdp:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), 30))
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")


async def main():
    ts = httpx.get(f"http://127.0.0.1:{PORT}/json/list", timeout=6).json()
    t = next((x for x in ts if x.get("url", "").startswith("doubao://doubao-chat/chat")
              and x.get("type") == "page"), None)
    if not t:
        print("找不到聊天页")
        return

    async with websockets.connect(t["webSocketDebuggerUrl"], max_size=16 << 20) as ws:
        cdp = Cdp(ws)
        await cdp.call("Runtime.enable")

        info = json.loads(await cdp.js(FIND) or "{}")
        print("[1] 输入框:", json.dumps(info, ensure_ascii=False))
        if not info.get("ok"):
            return

        await cdp.call("Input.insertText", text=PROMPT)
        await asyncio.sleep(0.8)
        typed = await cdp.js("document.querySelector('.tiptap.ProseMirror')?.innerText || ''")
        print("[2] 已写入:", repr(typed[:80]))

        before = json.loads(await cdp.js(STATE) or "{}")
        print("[3] 发送前:", {k: before.get(k) for k in ("wbLen", "skeleton", "generating", "bodyLen")})

        # 找发送按钮；找不到就 Enter
        clicked = await cdp.js(r"""
        (() => {
          const cand = [...document.querySelectorAll('button,[role="button"]')].map(b => {
            const r = b.getBoundingClientRect();
            return { b, a: b.getAttribute('aria-label')||'', t: (b.innerText||'').trim(),
                     x: r.x, y: r.y, w: r.width, h: r.height };
          }).filter(o => o.w > 0 && o.y > 780 && o.y < 900 && o.x > 1200);
          const hit = cand.find(o => /发送|send/i.test(o.a + o.t)) || cand[cand.length - 1];
          if (!hit) return '';
          hit.b.click();
          return (hit.a || hit.t || 'clicked').slice(0, 30);
        })()
        """)
        if not clicked:
            print("[4] 未找到发送按钮，改用 Enter")
            for ev in ("keyDown", "keyUp"):
                await cdp.call("Input.dispatchKeyEvent", type=ev, windowsVirtualKeyCode=13,
                               nativeVirtualKeyCode=13, key="Enter", code="Enter")
        else:
            print("[4] 点击发送:", clicked)

        print("[5] 等待工作台产出……")
        deadline = time.time() + 150
        last = ""
        quiet = 0
        while time.time() < deadline:
            await asyncio.sleep(3)
            st = json.loads(await cdp.js(STATE) or "{}")
            sig = f"{st.get('wbLen')}|{st.get('skeleton')}|{st.get('generating')}|{st.get('bodyLen')}"
            flag = "生成中" if st.get("generating") or st.get("skeleton") else "静止"
            print(f"    t+{int(time.time() - (deadline - 150))}s  wbLen={st.get('wbLen')}  {flag}")
            if sig == last:
                quiet += 1
            else:
                quiet = 0
                last = sig
            if quiet >= 3 and st.get("wbLen", 0) > before.get("wbLen", 0):
                break

        st = json.loads(await cdp.js(STATE) or "{}")
        print("\n[6] 工作台文本尾部：")
        print("-" * 60)
        print(st.get("wbText") or "(空)")
        print("-" * 60)
        print("wbLen:", st.get("wbLen"), "| 相对发送前增量:", (st.get("wbLen") or 0) - (before.get("wbLen") or 0))


if __name__ == "__main__":
    asyncio.run(main())
