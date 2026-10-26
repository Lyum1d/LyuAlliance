"""WorkBuddy 桌面端桥接（CDP）。

为什么走这条：本机 CLI 存不住 OAuth 凭据（at-rest 加密缺 key），又拿不到 API Key，
所以只能像操作豆包那样，直接驱动桌面端这个 Electron 应用的界面。

和豆包桥接的差异（重要）：
- 豆包有独立「工作任务」页，不影响主聊天；WorkBuddy **没有隔离面**，驱动它 = 操作你正在用的窗口。
  所以每次派活前会先点「新建任务」开一个干净会话，绝不往你当前的对话里插消息。
- 代价：视图会切到新会话，你的原对话还在侧边栏，点回去即可。
- 同一时刻只允许一个任务驱动窗口（_WINDOW_LOCK），否则两个 agent 的输入会互相覆盖。

回读策略与豆包一致，但加了两层兜底：
- prompt 前置唯一标记 ⟦WB:<task_id>⟧，答案 = 标记之后的内容
- 标记被 UI 折叠/回收时，退化为「与发送前快照做 diff」
- 完成判定优先靠哨兵 ⟦END⟧（要求模型末行输出），其次靠 18s 静默

实测选择器（2026-10-26，WorkBuddy V5.9.2 / Electron 37 / Chrome 138）：
- 输入框   div[contenteditable="true"]._editable_1cxd2_1   （Slate，Input.insertText 可写）
- 发送按钮 button.cr-send-button（生成中带 --sending --stop，aria=停止）
- 消息区   .cr-message-list__content
- 新建任务 button[data-track-id="agent_new_task_button_clicked"]
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Callable

import httpx

from . import wb_input

DEFAULT_PORT = int(os.environ.get("WB_CDP_PORT") or 9444)
MARKER_PREFIX = "⟦WB:"
MARKER_SUFFIX = "⟧"

# 回答区的切分锚点。
#
# 这里踩过一个坑：最早是让模型在末尾输出 ⟦END⟧ 当哨兵，但哨兵的字符串
# 必须写在 prompt 里教它，于是「用户消息」里也出现了 ⟦END⟧，回读时
# 误判成已完成、把我们的 prompt 当成答案返回。
# 现在改成：prompt 末尾放一个独有的分隔符，分隔符之后就是回答区；
# 完成判定改用 UI 的生成状态（发送按钮 --sending），不依赖模型配合。
PROMPT_END = "⟪PROMPT-END⟫"
END_HINT = (
    "\n\n（自动化调用约定：请直接给出结果，不要反问、不要等确认。）"
    f"\n{PROMPT_END}"          # 必须是整段 prompt 的最后一个字符序列，后面不能再跟东西
)

# 一个窗口同一时刻只能跑一个任务
_WINDOW_LOCK = asyncio.Lock()


def make_marker(task_id: str) -> str:
    return f"{MARKER_PREFIX}{task_id}{MARKER_SUFFIX}"


# ---------- 页面内执行的 JS ----------

JS_STATE = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  const btn = document.querySelector('.cr-send-button');
  const list = document.querySelector('.cr-message-list__content')
            || document.querySelector('[class*="cr-message-list"]');
  const cls = btn ? (btn.className || '') : '';
  return JSON.stringify({
    hasEditor: !!ed,
    editorText: ed ? (ed.innerText || '') : null,
    sendCls: cls,
    sending: /--sending|--stop/.test(cls),
    sendDisabled: btn ? (!!btn.disabled || btn.getAttribute('aria-disabled') === 'true') : true,
    listText: list ? (list.innerText || '') : '',
    bodyLen: (document.body.innerText || '').length
  });
})()
"""

JS_FOCUS_CLEAR = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  if (!ed) return 'no-editor';
  ed.focus();
  const sel = window.getSelection();
  const r = document.createRange();
  r.selectNodeContents(ed);
  sel.removeAllRanges();
  sel.addRange(r);
  document.execCommand('delete', false, null);
  return 'ok';
})()
"""

JS_NEW_CHAT = r"""
(() => {
  const b = document.querySelector('button[data-track-id="agent_new_task_button_clicked"]');
  if (!b) return 'no-button';
  b.click();
  return 'clicked';
})()
"""

JS_CLICK_SEND = r"""
(() => {
  const b = document.querySelector('.cr-send-button');
  if (!b) return JSON.stringify({r: 'no-button'});
  const c = b.className || '';
  if (/--sending|--stop/.test(c)) return JSON.stringify({r: 'busy'});
  if (b.disabled || b.getAttribute('aria-disabled') === 'true')
    return JSON.stringify({r: 'disabled'});
  b.click();
  return JSON.stringify({r: 'clicked', aria: b.getAttribute('aria-label') || ''});
})()
"""


def js_insert(text: str) -> str:
    """页面内注入文本。

    为什么不用 CDP 的 Input.insertText：它要求窗口持有系统焦点，
    而本机常态是用户在别的窗口/终端里，此时它静默无效（实测 editor 内容不变）。
    execCommand 在页面内执行，不依赖系统焦点，对 Slate 有效。
    """
    return (
        "(() => { const e = document.querySelector('div[contenteditable=\"true\"]');"
        "if (!e) return 'no-editor'; e.focus();"
        f"const ok = document.execCommand('insertText', false, {json.dumps(text, ensure_ascii=False)});"
        "return ok ? 'ok' : 'fail'; })()"
    )

JS_BOX = r"""
(() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  if (!ed) return JSON.stringify({err: 'no-editor'});
  const btn = document.querySelector('.cr-send-button');
  const er = ed.getBoundingClientRect();
  const br = btn ? btn.getBoundingClientRect() : null;
  return JSON.stringify({
    screen: [window.screenX, window.screenY],
    dpr: window.devicePixelRatio,
    editor: [er.x, er.y, er.width, er.height],
    send: br ? [br.x, br.y, br.width, br.height] : null
  });
})()
"""

JS_PROBE = r"""
(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 60 && r.height > 0; };
  const eds = [...document.querySelectorAll('textarea,[contenteditable="true"],.ProseMirror,.tiptap')]
    .filter(vis).map(e => ({
      tag: e.tagName, cls: (typeof e.className === 'string' ? e.className : '').slice(0, 50),
      dt: e.getAttribute('data-testid') || '', ph: e.getAttribute('placeholder') || '',
      rect: (() => { const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })()
    }));
  const btn = document.querySelector('.cr-send-button');
  const list = document.querySelector('.cr-message-list__content');
  const scrollers = [];
  for (const d of document.querySelectorAll('div,section,main')) {
    const s = getComputedStyle(d), r = d.getBoundingClientRect();
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && r.width > 400 && r.height > 200 && r.left > 300) {
      scrollers.push({ cls: (typeof d.className === 'string' ? d.className : '').slice(0, 50),
                       rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                       sh: d.scrollHeight, ch: d.clientHeight });
    }
  }
  return JSON.stringify({
    url: location.href.slice(0, 100), title: document.title,
    win: [window.innerWidth, window.innerHeight],
    editors: eds.slice(0, 4),
    send: btn ? {cls: btn.className, aria: btn.getAttribute('aria-label')} : null,
    listLen: list ? (list.innerText || '').length : -1,
    scrollers: scrollers.slice(0, 3),
    testids: [...new Set([...document.querySelectorAll('[data-testid],[data-track-id]')]
      .map(e => e.getAttribute('data-testid') || e.getAttribute('data-track-id')))]
      .filter(s => /chat|msg|message|input|send|new_task/i.test(s)).slice(0, 20)
  });
})()
"""


_TS_RE = re.compile(r"^\d{1,2}:\d{2}$")
_DONE_RE = re.compile(r"^已(完成|处理)\s*[\d.]+\s*s$")
_FINISHED_UI_RE = re.compile(r"已完成\s*[\d.]+\s*s")
_STATUS_WORDS = {"WorkBuddy", "你", "准备中", "等待模型响应", "生成回复中",
                 "继续处理任务过程", "思考中", "处理中", "深度思考中",
                 "正在思考", "正在生成", "执行中"}


def _clean_answer(raw: str) -> str:
    """把回答区开头的「时间戳 / WorkBuddy / 已处理 Ns / 准备中…」和结尾的用量统计剥掉。"""
    lines = raw.strip("\n").split("\n")
    while lines:
        t = lines[0].strip()
        if (not t) or _TS_RE.match(t) or _DONE_RE.match(t) or t in _STATUS_WORDS:
            lines.pop(0)
        else:
            break
    out = "\n".join(lines)
    cut = out.find("\n共消耗")
    if cut >= 0:
        out = out[:cut]
    return out.strip()


def js_read(prompt_end: str) -> str:
    """切出分隔符之后的回答区。"""
    m = json.dumps(prompt_end, ensure_ascii=False)
    return r"""
(() => {
  const sep = %s;
  const el = document.querySelector('.cr-message-list__content')
          || document.querySelector('[class*="cr-message-list"]');
  const t = (el ? el.innerText : document.body.innerText) || '';
  const i = t.indexOf(sep);
  if (i < 0) return JSON.stringify({found: false, ans: ''});
  return JSON.stringify({found: true, ans: t.slice(i + sep.length)});
})()
""" % m


class CdpClient:
    def __init__(self, ws):
        self._ws = ws
        self._n = 0

    @staticmethod
    async def connect(port: int = DEFAULT_PORT, timeout: float = 6.0) -> "CdpClient":
        import websockets

        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            r = await c.get(f"http://127.0.0.1:{port}/json/list")
            r.raise_for_status()
            targets = r.json()
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise RuntimeError("调试端口通了，但没有 page 目标（应用可能还没加载完）")
        page = next((t for t in pages if t.get("title") not in ("", "about:blank")
                     and not t.get("url", "").startswith("devtools://")), pages[0])
        ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=32 << 20)
        cli = CdpClient(ws)
        await cli.call("Runtime.enable")
        return cli

    async def call(self, method: str, **params) -> dict:
        self._n += 1
        mid = self._n
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), 40)
            m = json.loads(raw)
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"CDP {method}: {m['error']}")
                return m.get("result", {})

    async def js(self, expr: str) -> Any:
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")

    async def state(self) -> dict:
        try:
            return json.loads(await self.js(JS_STATE) or "{}")
        except Exception:
            return {}

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass


class WorkBuddyBridge:
    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as c:
                r = await c.get(f"http://127.0.0.1:{self.port}/json/version")
                return r.status_code == 200
        except Exception:
            return False

    async def probe_dom(self) -> dict:
        cli = await CdpClient.connect(self.port)
        try:
            return json.loads(await cli.js(JS_PROBE) or "{}")
        finally:
            await cli.close()

    # ---------- 内部 ----------

    async def _center(self, cli: CdpClient, which: str = "editor") -> tuple[int, int]:
        """取控件中心的物理像素坐标（SendInput 用物理像素，CSS px 要乘 dpr）。"""
        obj = json.loads(await cli.js(JS_BOX) or "{}")
        if obj.get("err") or not obj.get(which):
            raise RuntimeError(f"取不到 {which} 的屏幕坐标（{obj.get('err') or '元素缺失'}）")
        sx, sy = obj["screen"]
        x, y, w, h = obj[which]
        dpr = obj["dpr"]
        return int(round((sx + x + w / 2) * dpr)), int(round((sy + y + h / 2) * dpr))

    async def _wait_idle(self, cli: CdpClient, timeout: float = 40.0) -> bool:
        """等窗口不处于生成状态，避免打断用户正在跑的对话。"""
        t0 = time.time()
        calm = 0
        while time.time() - t0 < timeout:
            st = await cli.state()
            if not st.get("sending"):
                calm += 1
                if calm >= 2:
                    return True
            else:
                calm = 0
            await asyncio.sleep(1.0)
        return False

    async def _new_chat(self, cli: CdpClient, timeout: float = 15.0) -> None:
        r = await cli.js(JS_NEW_CHAT)
        if r != "clicked":
            raise RuntimeError("找不到「新建任务」按钮（界面可能已改版，先跑 probe）")
        t0 = time.time()
        while time.time() - t0 < timeout:
            await asyncio.sleep(0.8)
            st = await cli.state()
            if st.get("hasEditor") and not (st.get("editorText") or "").strip("\n ﻿"):
                return
        # 不算致命：后面还有清空动作兜底

    # ---------- 主入口 ----------

    async def run(
        self,
        prompt: str,
        task_id: str,
        on_snapshot: Callable[[str], None] | None = None,
        timeout: float = 420.0,
        fresh: bool = True,
        dry_run: bool = False,
    ) -> dict:
        marker = make_marker(task_id)
        full_prompt = f"{marker} {prompt}{END_HINT}"
        t0 = time.time()
        first_ms: int | None = None

        async with _WINDOW_LOCK:
            cli = await CdpClient.connect(self.port)
            try:
                st = await cli.state()
                if not st.get("hasEditor"):
                    raise RuntimeError("找不到输入框（WorkBuddy 可能停在了非对话页）")

                # 1) 只有在「往当前会话里发」时才需要等窗口空闲。
                #    注意一个死结：驱动 WorkBuddy 的 agent 本身就跑在 WorkBuddy 里，
                #    它执行工具调用期间窗口恒为 sending —— 所以 fresh=True 绝不能等，
                #    否则永远等不到。新建任务不会中断后台正在跑的生成，是安全的。
                if not fresh and not await self._wait_idle(cli, 40):
                    raise RuntimeError(
                        "WorkBuddy 窗口已连续生成超过 40s，为避免打断你正在进行的对话，本次放弃"
                    )

                # 2) 开干净会话，避免污染用户当前对话。
                #    若当前已经是空白新会话，再点一次会触发重渲染、把注入的文本冲掉，所以先判断。
                if fresh and not ((st.get("listText") or "").strip() == ""
                                  and (st.get("editorText") or "").strip("\n ﻿") == ""):
                    await self._new_chat(cli)
                    await asyncio.sleep(0.8)

                # 3) 用 OS 级真实输入注入文本。
                #
                # 为什么不用 CDP 的 Input 域：这个构建把它彻底禁掉了 ——
                # Input.insertText / dispatchKeyEvent 全都静默无效（窗口在前台也没用）。
                # 页面内 execCommand / Slate editor.insertText 能改内部状态，
                # 但 WorkBuddy 的「有没有内容」是另一层 state，按钮始终 disabled。
                # 唯一能穿透的是 SendInput 发真实键盘事件：Tab 能移动焦点、
                # VK 码能输入、Ctrl+V 能粘贴中文，按钮随之正常 enable。
                if not await asyncio.to_thread(wb_input.focus_window):
                    raise RuntimeError("找不到标题为 WorkBuddy 的窗口（应用是否已启动？）")
                await asyncio.sleep(0.35)

                got = ""
                for attempt in range(2):
                    ex, ey = await self._center(cli, "editor")
                    await asyncio.to_thread(wb_input.click, ex, ey)
                    await asyncio.sleep(0.3)
                    await asyncio.to_thread(wb_input.clear_editor)
                    await asyncio.sleep(0.3)
                    if not await asyncio.to_thread(wb_input.set_clipboard, full_prompt):
                        raise RuntimeError("写入系统剪贴板失败（剪贴板可能被其他程序占用）")
                    await asyncio.to_thread(wb_input.paste)
                    await asyncio.sleep(0.9)
                    st = await cli.state()
                    got = (st.get("editorText") or "")
                    if marker in got:
                        break
                if marker not in got:
                    raise RuntimeError(
                        f"注入失败（重试 2 次）：输入框里没有标记（当前内容 {got[:40]!r}）"
                    )

                if dry_run:
                    await asyncio.to_thread(wb_input.clear_editor)
                    return {"dry_run": True, "injected": len(full_prompt),
                            "chars": 0, "mode": "workbuddy-cdp"}

                # 4) 记快照 + 原生点击发送
                baseline = st.get("listText") or ""
                if st.get("sendDisabled"):
                    raise RuntimeError("发送按钮仍是禁用态：内容可能没被识别，已放弃")
                sx, sy = await self._center(cli, "send")
                await asyncio.to_thread(wb_input.click, sx, sy)
                await asyncio.sleep(1.6)

                # 校验真的发出去了，没反应就补一次
                chk = await cli.state()
                if not chk.get("sending") and (chk.get("listText") or "") == baseline:
                    await asyncio.to_thread(wb_input.click, sx, sy)
                    await asyncio.sleep(1.6)

                # 5) 轮询回读：完成判定看 UI 的「已完成 Ns」，不看按钮闪烁
                answer = ""
                stable = 0
                found_sep = False
                deadline = t0 + timeout
                while time.time() < deadline:
                    await asyncio.sleep(1.2)
                    raw = await cli.js(js_read(PROMPT_END))
                    obj = json.loads(raw or "{}")
                    found_sep = bool(obj.get("found"))
                    blob = obj.get("ans") or ""
                    finished_ui = bool(_FINISHED_UI_RE.search(blob))
                    text = _clean_answer(blob) if found_sep else ""
                    if text != answer:
                        if first_ms is None and text:
                            first_ms = int((time.time() - t0) * 1000)
                        answer = text
                        stable = 0
                        if on_snapshot:
                            on_snapshot(text)
                    else:
                        stable += 1
                    # UI 已经打出「已完成」且确实有正文 → 收工
                    if finished_ui and answer:
                        break
                    if answer and stable >= 15:      # 18s 静默兜底
                        break

                if not answer.strip():
                    raise RuntimeError(
                        "WorkBuddy 在 %ds 内没有产出正文（分隔符命中=%s）"
                        % (int(timeout), found_sep)
                    )
                return {"chars": len(answer), "first_token_ms": first_ms,
                        "elapsed_ms": int((time.time() - t0) * 1000),
                        "mode": "workbuddy-cdp", "read_mode": "separator"}
            finally:
                await cli.close()


if __name__ == "__main__":
    import sys

    async def _main() -> None:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
        b = WorkBuddyBridge(port=port)
        ok = await b.health()
        print(f"[0] CDP :{port} health={ok}")
        if not ok:
            print("    先用 spike/wb_debug_launch.bat 带调试端口重启 WorkBuddy")
            return
        info = await b.probe_dom()
        print(json.dumps(info, ensure_ascii=False, indent=1)[:2600])

    asyncio.run(_main())
