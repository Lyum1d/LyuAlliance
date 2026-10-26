"""桥接成员的心跳探测。

解决的是「重启之后状态过期」这个问题：
WorkBuddy / 豆包被用户关掉、重开、或者换了个没带调试端口的方式启动，
agentmesh 这边如果只看数据库里的 health，就会一直显示旧状态——
要么以为能用（派活才发现连不上），要么以为不能用（明明已经可以了）。

所以后台按两档频率巡一遍：
- 快档（CDP 类，纯 HTTP 探测，几十毫秒）：15s
- 慢档（要 spawn 子进程的 CLI 类）：120s

只在状态真的变化时写库 + 发事件，避免把前端事件流刷满。
"""
from __future__ import annotations

import asyncio

from . import store
from .bus import BUS
from .probe import probe_and_store

FAST = {"wb-cdp", "doubao"}                 # 纯端口/HTTP，便宜
SLOW = {"workbuddy", "codex", "ollama"}     # 要起子进程，贵
FAST_INTERVAL = 15.0
SLOW_INTERVAL = 120.0


async def _sweep(adapters: set[str], tag: str) -> None:
    for a in store.agents():
        if (a.get("adapter") or "none") not in adapters:
            continue
        try:
            r = await probe_and_store(a["id"])
        except asyncio.CancelledError:
            raise
        except Exception:
            continue
        if r:
            health, ev = r
            await BUS.publish("agent.health", "watch", {
                "agent_id": a["id"], "name": a.get("name"),
                "health": health, "evidence": ev, "tier": tag,
            })


async def _loop(adapters: set[str], interval: float, tag: str) -> None:
    while True:
        await _sweep(adapters, tag)
        await asyncio.sleep(interval)


async def run() -> None:
    """lifespan 里挂两个后台协程。首轮稍等，让应用把端口起来。"""
    await asyncio.sleep(2.0)
    fast = asyncio.create_task(_loop(FAST, FAST_INTERVAL, "fast"))
    slow = asyncio.create_task(_loop(SLOW, SLOW_INTERVAL, "slow"))
    try:
        await asyncio.gather(fast, slow)
    finally:
        for t in (fast, slow):
            t.cancel()
