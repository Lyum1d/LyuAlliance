"""豆包桥接适配器：通过 CDP 驱动豆包桌面端的「新工作任务」执行体。

为什么走 CDP 而不是键鼠：
- 键鼠会抢用户焦点、失败率高，且无法判断"生成是否结束"
- CDP 可以在后台精确注入文本、判断完成态、按消息行切片回读

前置条件：豆包必须以 `--remote-debugging-port=9333` 启动
（spike/launch_doubao_debug.cmd，且必须用 explorer 托管，否则会被工具会话回收）。

回读策略（实测得出，不靠猜）：
- 消息行锚点是 `[data-testid="message_content"]`，class 是哈希值会变，testid 稳定
- 用户气泡 class 含 `justify-end`，助手气泡是 `grid`
- prompt 前置唯一标记 ⟦AM:<task_id>⟧，答案 = 含标记那一行之后的所有消息行
- 不要用「工作台层 right-panel-workbench-layer」回读正文，那里是产物文件区；
  也不要用「输入框的祖先面板」，会把侧栏模式菜单一起抓进来（踩过）
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

import httpx

DEFAULT_PORT = 9333
MARKER_PREFIX = "⟦AM:"
MARKER_SUFFIX = "⟧"
# 完成哨兵：豆包的工作任务模式没有稳定的「停止」控件可供探测
#（实测生成期间按钮列表里既无 aria-label 也无 innerText 命中 停止/中止），
# 所以改为在指令里要求它最后单独一行输出哨兵，见到即判定完成。
# 这比猜 UI 状态可靠得多，且豆包改版也不会失效。
END_MARK = "⟦END⟧"
END_HINT = (
    f"\n\n（系统约定：在你最终回复的最后单独一行输出 {END_MARK} 作为结束标记，"
    "不要解释它，不要加引号。）"
)


def make_marker(task_id: str) -> str:
    return f"{MARKER_PREFIX}{task_id}{MARKER_SUFFIX}"


# ---------- 注入页面执行的 JS ----------

def js_state(marker: str) -> str:
    """读取状态：只回传标记之后的正文，避免整页 innerText 过重。"""
    m = json.dumps(marker, ensure_ascii=False)
    return r"""
(() => {
  const marker = %s;
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const rows = [...document.querySelectorAll('[data-testid="message_content"]')];
  let idx = -1;
  rows.forEach((e, i) => { if ((e.innerText || '').includes(marker)) idx = i; });
  const ans = idx >= 0
    ? rows.slice(idx + 1).map(e => (e.innerText || '').trim()).filter(Boolean).join('\n\n')
    : '';
  // 只有"可见且在文档流里"的停止按钮才算生成中。
  // 不能把 office-loading-skeleton-docx 计入：纯文本回答时它永不消失，
  // 会把每一轮都判成生成中（实测导致 180s 空等到超时）。
  const stop = [...document.querySelectorAll('button,[role="button"]')].filter(b => {
    const r = b.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0 || b.offsetParent === null) return false;
    const s = getComputedStyle(b);
    if (s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) return false;
    return /停止|中止/.test((b.innerText || '') + (b.getAttribute('aria-label') || ''));
  })[0];
  const wb = document.querySelector('[data-testid="right-panel-workbench-layer"]');
  return JSON.stringify({
    ans: ans,
    idx: idx,
    rows: rows.length,
    generating: !!stop,
    skeleton: !!document.querySelector('[data-testid="office-loading-skeleton-docx"]'),
    guidance: !!document.querySelector('[data-testid="flow_chat_guidance_page"]'),
    officeMode: !!wb,
    workbenchLen: wb ? wb.innerText.length : -1
  });
})()
""" % m


JS_FOCUS_EDITOR = r"""
(() => {
  const eds = [...document.querySelectorAll('.tiptap.ProseMirror,[contenteditable="true"]')]
    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 100 && r.height > 0; });
  if (!eds.length) return '';
  const e = eds[eds.length - 1];
  e.focus();
  const r = e.getBoundingClientRect();
  return JSON.stringify({rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]});
})()
"""

JS_EDITOR_TEXT = r"""
(() => {
  const e = document.querySelector('.tiptap.ProseMirror');
  return e ? (e.innerText || '') : '';
})()
"""

JS_CLICK_SEND = r"""
(() => {
  const cand = [...document.querySelectorAll('button,[role="button"]')]
    .map(b => { const r = b.getBoundingClientRect();
      return { b, a: b.getAttribute('aria-label') || '', t: (b.innerText || '').trim(),
               x: r.x, y: r.y, w: r.width, h: r.height }; })
    .filter(o => o.w > 0 && o.h > 0);
  let hit = cand.find(o => /发送|send/i.test(o.a + o.t));
  if (!hit) {
    hit = cand.filter(o => o.x > window.innerWidth - 180 &&
                           o.y > window.innerHeight - 220 &&
                           o.w < 80 && o.h < 80).pop();
  }
  if (!hit) return '';
  hit.b.click();
  return (hit.a || hit.t || 'clicked').slice(0, 24);
})()
"""

JS_CLICK_NEW_TASK = r"""
(() => {
  const b = document.querySelector('[data-testid="create_office_task_button"] [role="button"]')
         || document.querySelector('[data-testid="create_office_task_button"]');
  if (!b) return '';
  b.click();
  return 'clicked';
})()
"""


_STATUS_PLACEHOLDERS = ("正在思考", "思考中", "正在生成", "生成中")
_HEADER_RE = __import__("re").compile(r"^已处理\s*[\d\s分秒小时]+")


def _clean(text: str) -> str:
    """去掉生成初期的状态占位符、耗时表头和哨兵符。"""
    s = text.strip()
    changed = True
    while changed:
        changed = False
        for p in _STATUS_PLACEHOLDERS:
            if s.startswith(p):
                s = s[len(p):].lstrip()
                changed = True
    s = _HEADER_RE.sub("", s).strip()
    if END_MARK in s:
        s = s.split(END_MARK)[0].rstrip()
    return s


class CdpClient:
    """极简 CDP 客户端：只用到 Runtime.evaluate 和 Input.*。"""

    def __init__(self, ws):
        self._ws = ws
        self._n = 0

    @staticmethod
    async def connect(port: int = DEFAULT_PORT, timeout: float = 6.0) -> "CdpClient":
        import websockets

        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(f"http://127.0.0.1:{port}/json/list")
            r.raise_for_status()
            targets = r.json()
        page = next(
            (t for t in targets
             if t.get("type") == "page" and t.get("url", "").startswith("doubao://doubao-chat")),
            None,
        )
        if not page:
            raise RuntimeError("找不到豆包聊天页（豆包未启动，或不在聊天界面）")
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
            # 其它 id 的消息（事件）直接丢弃

    async def js(self, expr: str) -> Any:
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass


class DoubaoBridge:
    """把豆包「新工作任务」当作一个可派活的执行体。"""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"http://127.0.0.1:{self.port}/json/version")
                return r.status_code == 200
        except Exception:
            return False

    async def run(
        self,
        prompt: str,
        task_id: str,
        on_snapshot: Callable[[str], None] | None = None,
        timeout: float = 300.0,
    ) -> dict:
        marker = make_marker(task_id)
        full_prompt = f"{marker} {prompt}{END_HINT}"

        t0 = time.time()
        first_ms: int | None = None
        cli = await CdpClient.connect(self.port)
        try:
            st = await self._state(cli, marker)
            if not st.get("officeMode"):
                raise RuntimeError("豆包不在工作任务模式，无法作为执行体驱动")

            # 已有历史对话时先开一个干净的新工作任务，避免上下文串味
            if not st.get("guidance"):
                await cli.js(JS_CLICK_NEW_TASK)
                await asyncio.sleep(2.5)
                st = await self._state(cli, marker)

            # 注入
            focus = await cli.js(JS_FOCUS_EDITOR)
            if not focus:
                raise RuntimeError("找不到输入框（豆包界面结构可能已变化）")
            await cli.call("Input.insertText", text=full_prompt)
            await asyncio.sleep(0.6)
            typed = (await cli.js(JS_EDITOR_TEXT) or "").strip()
            if not typed:
                raise RuntimeError("输入框注入失败：写入后仍为空")

            # 发送
            clicked = await cli.js(JS_CLICK_SEND)
            if not clicked:
                for ev in ("keyDown", "keyUp"):
                    await cli.call("Input.dispatchKeyEvent", type=ev,
                                   windowsVirtualKeyCode=13, nativeVirtualKeyCode=13,
                                   key="Enter", code="Enter")
            await asyncio.sleep(1.2)

            # 轮询：按"快照"而不是"增量"累积。
            # 豆包生成初期会在气泡里先塞一个「正在思考」占位符，随后被正文覆盖，
            # 用长度增量拼接会把占位符永久留在结果里（踩过）。
            answer = ""
            stable = 0
            deadline = t0 + timeout
            while time.time() < deadline:
                await asyncio.sleep(1.5)
                st = await self._state(cli, marker)
                raw = st.get("ans") or ""
                done = END_MARK in raw
                cur = _clean(raw)
                if cur != answer:
                    if first_ms is None and cur:
                        first_ms = int((time.time() - t0) * 1000)
                    answer = cur
                    stable = 0
                    if on_snapshot:
                        on_snapshot(cur)
                else:
                    stable += 1
                if answer and done:
                    break
                # 兜底：18 秒完全没有变化就收工（豆包漏掉哨兵时不会无限等）
                if answer and stable >= 12:
                    break

            if not answer.strip():
                raise RuntimeError(
                    f"豆包在 {int(timeout)}s 内没有产出正文"
                    f"（消息行 {st.get('rows')} 条, 标记命中行 {st.get('idx')}, "
                    f"generating={st.get('generating')}）"
                )

            return {
                "chars": len(answer),
                "first_token_ms": first_ms,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "mode": "doubao-office-task",
            }
        finally:
            await cli.close()

    async def _state(self, cli: CdpClient, marker: str) -> dict:
        raw = await cli.js(js_state(marker))
        return json.loads(raw or "{}")


# ---------- 命令行探针：不启服务也能验证通道 ----------
if __name__ == "__main__":
    import sys

    async def _main() -> None:
        prompt = sys.argv[1] if len(sys.argv) > 1 else "用一句话说明什么是 JWT，不超过 30 字。"
        b = DoubaoBridge()
        ok = await b.health()
        print(f"[0] CDP :{b.port} health={ok}", flush=True)
        if not ok:
            print("    需要先以调试端口重启豆包：spike/launch_doubao_debug.cmd")
            return

        chunks: list[str] = []

        def on_snapshot(s: str) -> None:
            chunks.clear()
            chunks.append(s)
            print(f"    ~snapshot {len(s)}字: {s[-40:].replace(chr(10), ' ')}", flush=True)

        stats = await b.run(prompt, task_id="probe", on_snapshot=on_snapshot, timeout=180)
        print("\n[1] 统计:", json.dumps(stats, ensure_ascii=False))
        print("[2] 正文:")
        print("-" * 60)
        print("".join(chunks))
        print("-" * 60)

    asyncio.run(_main())
