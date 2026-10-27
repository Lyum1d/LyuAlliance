"""云端 LLM 客户端（OpenAI 兼容），给主脑做拆解用。

只用一个方法 `chat_json`：要求模型返回严格 JSON，失败则从文本里抠 JSON 块。
带预算闸：单次调用有 max_tokens 上限，累计用量超 `CONDUCTOR_BUDGET_TOKENS` 直接拒绝，
不静默烧钱。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from . import appcfg, store
from .config import env_bool


class BudgetExceeded(RuntimeError):
    pass


class CloudLLM:
    def __init__(self) -> None:
        # 每次实例化都重新读配置：界面上改完 key/模型保存后，下一次拆解就用新的，
        # 不需要重启后端。配置来源见 appcfg（库 > .env > 默认）。
        c = appcfg.effective()
        self.base = c["base_url"].rstrip("/")
        self.key = c["api_key"]
        self.model = c["model"]
        self.hard_limit = c["budget_tokens"]
        self.price_in = c["price_in"]    # 元 / 百万 token（估算用）
        self.price_out = c["price_out"]

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def budget(self) -> dict:
        t = store.llm_usage_totals()
        used = t["prompt_tokens"] + t["completion_tokens"]
        cost = (t["prompt_tokens"] / 1e6) * self.price_in + (
            t["completion_tokens"] / 1e6
        ) * self.price_out
        return {
            "model": self.model,
            "calls": t["calls"],
            "prompt_tokens": t["prompt_tokens"],
            "completion_tokens": t["completion_tokens"],
            "used_tokens": used,
            "limit_tokens": self.hard_limit,
            "est_cost_cny": round(cost, 4),
            "since": t["first_ts"],
            "price_note": f"估算按 ¥{self.price_in}/M 入、¥{self.price_out}/M 出；实际以账单为准",
        }

    async def probe(self) -> dict:
        """最小请求验证连通性：key 对不对、模型名存不存在、base_url 通不通。

        只发 4 个 token，成本可忽略；仍然记进账本，免得配置错时反复点测试悄悄烧钱。
        """
        if not self.key:
            raise BudgetExceeded("未配置 API Key，无法测试")
        t0 = time.time()
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8)) as c:
            r = await c.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                json={"model": self.model,
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 4},
            )
        ms = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:300]}")
        body = r.json()
        u = body.get("usage") or {}
        store.add_llm_usage(self.model, "conductor.probe",
                            int(u.get("prompt_tokens") or 0),
                            int(u.get("completion_tokens") or 0))
        choice = (body.get("choices") or [{}])[0]
        return {
            "model": body.get("model") or self.model,
            "latency_ms": ms,
            "reply": ((choice.get("message") or {}).get("content") or "")[:40],
        }

    async def chat_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 2400,
        temperature: float = 0.2,
    ) -> dict:
        if not self.configured:
            raise BudgetExceeded("未配置 CLOUD_API_KEY，主脑无法调用云端模型")
        used = self.budget()["used_tokens"]
        if used >= self.hard_limit:
            raise BudgetExceeded(
                f"已达 token 上限（累计 {used}/{self.hard_limit}，跨重启累计），"
                "为免意外开销已停止调用。确认要继续就调高 CONDUCTOR_BUDGET_TOKENS，"
                "或用 /api/plan/budget/reset 清空账本。"
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if env_bool("CLOUD_JSON_MODE", True):
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as c:
            r = await c.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                json=payload,
            )
        if r.status_code != 200:
            raise RuntimeError(f"云端 {r.status_code}: {r.text[:300]}")

        body = r.json()
        u = body.get("usage") or {}
        pt = int(u.get("prompt_tokens") or 0)
        ct = int(u.get("completion_tokens") or 0)
        # 落账本：跨重启累计，避免"重启即归零"导致预算形同虚设
        store.add_llm_usage(self.model, "conductor.draft", pt, ct)

        text = (body["choices"][0]["message"]["content"] or "").strip()
        return {
            "data": _parse_json(text),
            "text": text,
            "usage": u,
            "budget": self.budget(),
        }


def _parse_json(text: str) -> dict:
    """模型有时会裹上 ```json，先直解析再兜底抠块。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass
    truncated = not text.rstrip().endswith("}")
    raise RuntimeError(
        "模型返回的不是合法 JSON"
        + ("（输出疑似被 max_tokens 截断，把拆解粒度调粗一点，或减少额外要求）"
           if truncated else "（格式不符）")
        + f"：{text[:200]}"
    )
