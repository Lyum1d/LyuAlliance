"""S0 端到端验证：通过 CDP 向豆包发一句话，并把回复抓回来。

用法:
    python test_send.py              # 真实发送
    python test_send.py --dry        # 只填入输入框，不发送
    python test_send.py --text "..." # 自定义内容
"""
import asyncio
import json
import sys
import time

import httpx
import websockets

PORT = 9333
CHAT_URL = "doubao://doubao-chat/chat"
EDITOR = '[data-testid="chat_input_input"]'

TEXT = "请只回复两个字：收到"
DRY = False
for i, a in enumerate(sys.argv):
    if a == "--dry":
        DRY = True
    if a == "--text" and i + 1 < len(sys.argv):
        TEXT = sys.argv[i + 1]


class Cdp:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")


BODY_TMPL = r"""(() => {
  const el = document.querySelector('[data-testid="chat_list_wrapper"]') || document.body;
  return JSON.stringify({ len: el.innerText.length, tail: el.innerText.slice(-1200) });
})()"""

FIND_SEND = r"""(() => {
  const btns = Array.from(document.querySelectorAll('button,[role="button"]'));
  const cand = btns.map(b => {
    const r = b.getBoundingClientRect();
    return { el: b, a: b.getAttribute('aria-label') || '', t: (b.innerText||'').trim(),
             x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width) };
  }).filter(o => o.w > 0 && o.x > 1400 && o.y > 800 && o.y < 920);
  const hit = cand.find(o => /发送|send/i.test(o.a + o.t)) || cand[cand.length - 1];
  if (!hit) return '';
  hit.el.click();
  return hit.a || hit.t || 'clicked';
})()"""


async def read_state(cdp):
    v = await cdp.js(BODY_TMPL)
    return json.loads(v) if v else {"len": 0, "tail": ""}


async def main():
    async with httpx.AsyncClient(timeout=8) as c:
        targets = (await c.get(f"http://127.0.0.1:{PORT}/json/list")).json()
    t = next((x for x in targets if x.get("url") == CHAT_URL), None)
    if not t:
        print("找不到聊天页，豆包是否带 --remote-debugging-port 启动？")
        return

    async with websockets.connect(t["webSocketDebuggerUrl"], max_size=16 << 20, open_timeout=10) as ws:
        cdp = Cdp(ws)
        await cdp.call("Runtime.enable")

        before = await read_state(cdp)
        print(f"[1] 发送前消息区文本长度 = {before['len']}")

        ok = await cdp.js(f"""(() => {{
            const ed = document.querySelector('{EDITOR}');
            if (!ed) return 'no-editor';
            ed.focus();
            return 'focused';
        }})()""")
        print(f"[2] 聚焦输入框: {ok}")
        if ok != "focused":
            return

        await cdp.call("Input.insertText", text=TEXT)
        await asyncio.sleep(0.6)
        filled = await cdp.js(f"""document.querySelector('{EDITOR}').innerText""")
        print(f"[3] 输入框内容: {filled!r}")

        if DRY:
            print("[dry] 已填入但未发送。")
            return

        clicked = await cdp.js(FIND_SEND)
        if clicked:
            print(f"[4] 点击发送按钮: {clicked!r}")
        else:
            print("[4] 未找到发送按钮，改用 Enter 键")
            for ev in ("keyDown", "keyUp"):
                await cdp.call("Input.dispatchKeyEvent", type=ev, windowsVirtualKeyCode=13,
                               nativeVirtualKeyCode=13, key="Enter", code="Enter")
        # 清空残留（若 Enter 未触发发送）
        await asyncio.sleep(1.0)

        print("[5] 等待回复……")
        deadline = time.time() + 90
        last_len, stable_since = before["len"], None
        tail = ""
        while time.time() < deadline:
            await asyncio.sleep(1.5)
            st = await read_state(cdp)
            if st["len"] != last_len:
                last_len, stable_since = st["len"], time.time()
                print(f"    + 文本增长 → {last_len}")
            else:
                if stable_since and time.time() - stable_since > 4:
                    tail = st["tail"]
                    break
        if not tail:
            st = await read_state(cdp)
            tail = st["tail"]
        print("\n[6] 抓到的尾部文本：")
        print("-" * 60)
        print(tail[-800:])
        print("-" * 60)
        print(f"判定: {'成功拿到增量' if last_len > before['len'] + 1 else '未检测到新增文本'}")


if __name__ == "__main__":
    asyncio.run(main())
