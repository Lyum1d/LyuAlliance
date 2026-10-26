"""空闲窗口下测原生输入（点击输入框 + 打字）。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import wb_input as wi  # noqa: E402
from app.wb_bridge import CdpClient  # noqa: E402

JS_BOX = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  const btn = document.querySelector('.cr-send-button');
  const r = ed.getBoundingClientRect();
  const cls = btn ? btn.className : '';
  return JSON.stringify({
    s: [window.screenX, window.screenY], dpr: window.devicePixelRatio,
    r: [r.x, r.y, r.width, r.height],
    sending: /--sending|--stop/.test(cls),
    editorText: (ed.innerText || '').slice(0, 40),
  });
})()
"""


async def main() -> None:
    wi.focus_window()
    await asyncio.sleep(0.5)
    c = await CdpClient.connect()
    try:
        box = json.loads(await c.js(JS_BOX) or "{}")
        print("box:", json.dumps(box, ensure_ascii=False))
        dpr = box["dpr"]
        sx, sy = box["s"]
        ex, ey, ew, eh = box["r"]
        px = int((sx + ex + ew * 0.3) * dpr)
        py = int((sy + ey + eh / 2) * dpr)
        wi.click(px, py)
        await asyncio.sleep(0.5)
        print(f"clicked ({px},{py})")

        n = wi.type_text("NATIVE2 abc")
        print("typed:", n)
        await asyncio.sleep(1.5)

        st = await c.state()
        print("editor:", repr((st.get("editorText") or "")[:60]))
        print("sending:", st.get("sending"), "disabled:", st.get("sendDisabled"))
        print("tail:", (st.get("sendCls") or "")[-40:])
    finally:
        await c.close()


asyncio.run(main())
