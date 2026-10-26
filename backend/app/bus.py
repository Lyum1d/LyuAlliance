"""进程内事件总线：单 worker 下用 asyncio.Queue 广播，同时落库。

两类事件：
- persist=True  → 写 `event` 表，带全局单调 seq，可回放/断线续传
- persist=False → 只是 UI 流式增量（token），不落库，避免把 events 表撑爆

为什么必须单 worker：多进程会让 Queue 分裂，部分浏览器收不到事件。
"""
from __future__ import annotations

import asyncio
from typing import Any

from . import store


class Bus:
    def __init__(self) -> None:
        self.subs: set[asyncio.Queue] = set()
        self._seq = 0

    def warm(self) -> None:
        self._seq = store.last_seq()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)

    async def publish(
        self, kind: str, actor: str, payload: dict[str, Any], persist: bool = True
    ) -> int:
        if persist:
            seq = store.append_event(kind, actor, payload)
            self._seq = seq
        else:
            seq = self._seq
        msg = {
            "v": 1, "type": "event", "seq": seq, "ts": store.now_iso(),
            "kind": kind, "actor": actor, "payload": payload,
            "persist": persist,
        }
        for q in list(self.subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
        return seq


BUS = Bus()
