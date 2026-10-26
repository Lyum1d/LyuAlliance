"""Agent 适配器：把统一的任务请求翻译成各家接口。

v0.2 只实现 Ollama；Codex / OpenAI 兼容 / 豆包 CDP 留同签名接口，逐步补。
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Callable

import httpx

OLLAMA_BASE = "http://127.0.0.1:11434"
NUM_CTX = 8192
NUM_PREDICT = 600


def build_prompt(task: dict, workspace: dict) -> str:
    """把任务压成一段 prompt。刻意短——9B 模型对长 prompt 的遵循度会掉。"""
    parts = [
        "你是多 Agent 协作团队里的一名执行者，只负责完成下面这一个任务。",
        f"项目目标：{workspace.get('goal') or workspace.get('name') or ''}",
        f"任务名称：{task['title']}",
    ]
    if task.get("objective"):
        parts.append(f"任务说明：{task['objective']}")
    acc = task.get("acceptance") or []
    if acc:
        parts.append("验收标准：\n" + "\n".join(f"- {a}" for a in acc))

    # 交接上下文：上游成果 / 已定决策 / 失败教训
    ctx = task.get("context")
    if ctx:
        from .handoff import packet_to_prompt

        block = packet_to_prompt(ctx)
        if block:
            parts.append(block)

    parts.append(
        "输出要求：直接给出可交付的结果，不要复述任务背景，不要寒暄。"
        "中文作答，控制在 400 字以内；如果产出是代码，就只给代码加必要注释。"
    )
    return "\n\n".join(parts)


class OllamaAdapter:
    """本地 Ollama。关键点：qwen3.5 默认开 thinking，会把 token 预算全烧在思考上，
    导致 content 为空。所以这里默认 think=False。"""

    def __init__(self, base: str = OLLAMA_BASE, think: bool = False):
        self.base = base.rstrip("/")
        self.think = think

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self.base}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def run(
        self,
        model: str,
        prompt: str,
        on_content: Callable[[str], None],
        on_thinking: Callable[[str], None] | None = None,
        timeout: float = 300.0,
    ) -> dict:
        """流式执行，返回统计信息。"""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "think": self.think,
            "options": {
                "num_predict": NUM_PREDICT,
                "temperature": 0.3,
                "num_ctx": NUM_CTX,
            },
        }
        eval_count = 0
        first_ms: int | None = None
        import time

        t0 = time.time()
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5)) as c:
            async with c.stream("POST", f"{self.base}/api/chat", json=payload) as r:
                if r.status_code != 200:
                    body = (await r.aread())[:300].decode("utf-8", "replace")
                    raise RuntimeError(f"Ollama {r.status_code}: {body}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = obj.get("message") or {}
                    ch = msg.get("content") or ""
                    th = msg.get("thinking") or ""
                    if th and on_thinking:
                        on_thinking(th)
                    if ch:
                        if first_ms is None:
                            first_ms = int((time.time() - t0) * 1000)
                        on_content(ch)
                    if obj.get("done"):
                        eval_count = int(obj.get("eval_count") or 0)
                        break
        return {
            "eval_count": eval_count,
            "first_token_ms": first_ms,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }


async def stream_ollama(model: str, prompt: str) -> AsyncIterator[str]:
    """给不需要回调的调用方用的极简生成器。"""
    a = OllamaAdapter()
    q: list[str] = []
    stats = await a.run(model, prompt, lambda s: q.append(s))
    for s in q:
        yield s
    _ = stats
