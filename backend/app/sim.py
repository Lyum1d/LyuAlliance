"""演示模拟器 + 能力路由。

作用：让 M1 的界面"活"起来——真实推进 running 任务的进度、完成后按能力分派下一个任务。
这不是伪造数据：所有变更都走 store 并写 event，前端看到的和真实执行时的事件流一致。
后续把这里换成真正的 Adapter 调用即可，事件协议不用改。
"""
from __future__ import annotations

import asyncio
import random

from . import engine, handoff, store
from .bus import BUS

TICK_S = 2.5
CAP_FLOOR = 0.35


def route_scores(task: dict, agents: list[dict]) -> list[dict]:
    """能力路由打分：0.5*能力 + 0.2*健康 + 0.15*空闲 + 0.1*成本 + 0.05*近期。

    返回按分数降序的候选列表，供前端"为什么选它"展示。
    """
    req = task.get("required_caps") or []
    out = []
    for a in agents:
        if not a["in_team"] or a["kind"] == "conductor" or a["role"] == "human":
            continue
        if a["health"] == "unavailable":
            # 已接入但不可用（无额度 / 未启用 / 服务没起）。
            # 不参与打分 = 永远派不到它 = 不会用模拟进度冒充它在干活。
            continue
        caps = a["caps"]
        if req:
            matched = [caps.get(c, 0.0) for c in req]
            cap_match = sum(matched) / len(matched)
        else:
            cap_match = max(caps.values()) if caps else 0.5
        health = {"online": 1.0, "degraded": 0.5}.get(a["health"], 0.0)
        load = 0.0  # 单任务串行，这里简化
        cost = {"free": 1.0, "paid": 0.5}.get(a["cost"], 0.5)
        score = 0.5 * cap_match + 0.2 * health + 0.15 * (1 - load) + 0.1 * cost + 0.05 * 1.0
        out.append({
            "agent_id": a["id"], "name": a["name"], "score": round(score, 3),
            "cap_match": round(cap_match, 2), "health": a["health"],
            "cost": a["cost"], "kind": a["kind"],
        })
    out.sort(key=lambda x: (-x["score"], x["kind"] != "native"))
    return out


def _deps_done(task_id: str, tasks: dict[str, dict]) -> bool:
    deps = store.depends_map().get(task_id, [])
    return all(tasks.get(d, {}).get("status") == "done" for d in deps)


async def _advance_running() -> None:
    tasks = {t["id"]: t for t in store.tasks()}
    for t in list(tasks.values()):
        if t["status"] != "running":
            continue
        if engine.is_running(t["id"]):
            continue  # 真跑的任务由 engine 推进，别用假进度覆盖
        p = min(100, t["progress"] + random.randint(1, 4))
        if p >= 100:
            store.update_task(t["id"], status="done", progress=100, ended_at=store.now_iso())
            await BUS.publish("task.done", f"agent:{t['assignee']}",
                              {"task_id": t["id"], "title": t["title"], "simulated": True})
            # 模拟完成也要交接：下游拿到的会是"没有真实产出"的上下文包，
            # 这样它不会以为自己有依据（比不给更安全）
            await handoff.after_done(t["id"])
        else:
            store.update_task(t["id"], progress=p)
            await BUS.publish("task.progress", f"agent:{t['assignee']}",
                              {"task_id": t["id"], "progress": p})


async def _dispatch_ready() -> None:
    tasks = {t["id"]: t for t in store.tasks()}
    agents = store.agents()
    by_id = {a["id"]: a for a in agents}
    running = [t for t in tasks.values() if t["status"] == "running"]
    if len(running) >= int(store.workspace().get("concurrency", 2)):
        return
    for t in tasks.values():
        if t["status"] != "pending":
            continue
        if not _deps_done(t["id"], tasks):
            continue
        cands = route_scores(t, agents)

        # 主脑计划里已经指派的节点，按指派走。
        # 为什么不让能力分否决它：主脑拆解时已权衡过，用户也在计划页批准了这份指派；
        # 若再用 0.35 的能力下限把它挡回去，任务会永远卡在 pending（踩过）。
        chosen = None
        pinned = t.get("assignee")
        if pinned:
            a = by_id.get(pinned)
            if (a and a["in_team"] and a["kind"] != "conductor"
                    and a["role"] != "human" and a["health"] != "unavailable"):
                sc = next((c for c in cands if c["agent_id"] == pinned), None)
                chosen = {
                    "agent_id": pinned, "name": a["name"],
                    "score": sc["score"] if sc else 0.0,
                    "cap_match": sc["cap_match"] if sc else 0.0,
                }

        if not chosen:
            best = next((c for c in cands if c["cap_match"] >= CAP_FLOOR), None)
            if not best:
                continue
            chosen = best

        store.update_task(t["id"], status="running", assignee=chosen["agent_id"],
                          started_at=store.now_iso(), reason=None)
        await BUS.publish("task.dispatched", "scheduler",
                          {"task_id": t["id"], "title": t["title"],
                           "agent_id": chosen["agent_id"], "agent": chosen["name"],
                           "score": chosen["score"], "scores": cands[:4],
                           "pinned": bool(pinned and chosen["agent_id"] == pinned)})
        # 有真实执行体的，直接交给 engine 去跑
        agent = by_id.get(chosen["agent_id"])
        if agent and engine.has_real_runner(agent):
            fresh = store.task(t["id"])
            if fresh:
                await engine.launch(fresh, agent)
        return


async def _reconcile() -> None:
    """对账：状态是 running、执行者有真实 adapter、但进程里没有 run 的，补启动。

    覆盖两种情况：① 重置演示数据后重新开跑 ② 后端重启后残留的 running 任务。
    """
    agents = {a["id"]: a for a in store.agents()}
    for t in store.tasks():
        if t["status"] != "running" or engine.is_running(t["id"]):
            continue
        a = agents.get(t["assignee"] or "")
        if a and engine.has_real_runner(a):
            store.update_task(t["id"], progress=0, output=None)
            await engine.launch(t, a)


async def run() -> None:
    await asyncio.sleep(1.2)
    while True:
        try:
            await _reconcile()
            await _advance_running()
            await _dispatch_ready()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 演示进程不能因为一次异常就死
            print(f"[sim] {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(TICK_S)
