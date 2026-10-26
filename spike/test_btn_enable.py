"""诊断：execCommand 注入后，Slate 状态与发送按钮启用条件。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.wb_bridge import CdpClient, js_insert, JS_FOCUS_CLEAR  # noqa: E402

TXT = "⟦WB:dbg2⟧ 按钮启用诊断测试"

JS_DIAG = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  const btn = document.querySelector('.cr-send-button');
  return JSON.stringify({
    editorText: ed ? (ed.innerText || '') : null,
    editorHTML: ed ? ed.innerHTML.slice(0, 220) : null,
    btnCls: btn ? btn.className : null,
    btnAriaDis: btn ? btn.getAttribute('aria-disabled') : null,
    btnDisabledProp: btn ? btn.disabled : null,
    activeIsEditor: document.activeElement === ed,
    activeTag: document.activeElement ? document.activeElement.tagName + '.' +
               (typeof document.activeElement.className === 'string' ? document.activeElement.className.slice(0,40) : '') : null,
    hasFocus: document.hasFocus(),
  });
})()
"""


async def show(c, tag):
    d = json.loads(await c.js(JS_DIAG) or "{}")
    print(f"[{tag}]")
    print(f"   text={d.get('editorText')!r}")
    print(f"   btnCls={d.get('btnCls')}")
    print(f"   ariaDisabled={d.get('btnAriaDis')} propDisabled={d.get('btnDisabledProp')}")
    print(f"   activeIsEditor={d.get('activeIsEditor')} active={d.get('activeTag')} docFocus={d.get('hasFocus')}")
    return d


async def main() -> None:
    c = await CdpClient.connect()
    try:
        await c.js(JS_FOCUS_CLEAR)
        await asyncio.sleep(0.3)
        await show(c, "0 清空后")

        print("\n[1] execCommand insertText")
        print("   ret =", await c.js(js_insert(TXT)))
        await asyncio.sleep(0.8)
        await show(c, "1 execCommand 后")

        print("\n[2] 补发 beforeinput / input 事件")
        await c.js(r"""
        (() => {
          const ed = document.querySelector('div[contenteditable="true"]');
          const t = ed.innerText;
          try { ed.dispatchEvent(new InputEvent('beforeinput', {bubbles: true, cancelable: true,
                    inputType: 'insertText', data: t})); } catch(e) {}
          try { ed.dispatchEvent(new InputEvent('input', {bubbles: true,
                    inputType: 'insertText', data: t})); } catch(e) {}
          return 'sent';
        })()
        """)
        await asyncio.sleep(0.8)
        await show(c, "2 补事件后")

        print("\n[3] CDP Input.insertText 追加一个字符（测焦点前提）")
        await c.call("Input.insertText", text="X")
        await asyncio.sleep(0.6)
        await show(c, "3 CDP insertText 后")

        print("\n[4] 逐键模拟：空格 + 退格")
        for k, code in ((" ", 32), ("Backspace", 8)):
            for ev in ("keyDown", "keyUp"):
                await c.call("Input.dispatchKeyEvent", type=ev, key=k,
                             windowsVirtualKeyCode=code, nativeVirtualKeyCode=code)
            await asyncio.sleep(0.3)
        await show(c, "4 按键后")
    finally:
        await c.close()


asyncio.run(main())
