"""FastAPI 入口：REST 快照 + WebSocket 增量。"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import bridge_watch, engine, handoff, planner, sim, store
from .probe import probe, probe_and_store
from .bus import BUS
from .sim import route_scores

ALLOWED_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:8787", "http://127.0.0.1:8787",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    BUS.warm()
    reclaimed = store.reclaim_running()
    if reclaimed:
        print(f"[startup] 回收上次中断的任务 {len(reclaimed)} 个: {reclaimed}")
    sim_task = asyncio.create_task(sim.run())
    watch_task = asyncio.create_task(bridge_watch.run())
    try:
        yield
    finally:
        for t in (sim_task, watch_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="AgentMesh", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"], allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "seq": store.last_seq(), "db": str(store.DB_PATH)}


@app.get("/api/snapshot")
def snapshot() -> dict:
    return store.snapshot()


# ---------------------------------------------------------------- 主脑计划

@app.get("/api/plan")
def get_plan() -> dict:
    p = store.plan()
    return {
        "plan": p if (p and p.get("status") == "draft") else None,
        "last": p if (p and p.get("status") != "draft") else None,
        "budget": planner.budget(),
        "agents": planner.assignable_agents(),
    }


@app.post("/api/plan/draft")
async def plan_draft(body: dict) -> dict:
    goal = (body or {}).get("goal") or ""
    try:
        p = await planner.draft(goal)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}"[:400])
    await BUS.publish("plan.drafted", "conductor", {
        "goal": p.get("goal"),
        "nodes": len(p.get("tasks") or []),
        "restated": p.get("goal_restated"),
    })
    return {"ok": True, "plan": p, "budget": planner.budget()}


@app.post("/api/plan/approve")
async def plan_approve() -> dict:
    try:
        p = await planner.approve()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "tasks": len(p.get("tasks") or [])}


@app.post("/api/plan/reject")
async def plan_reject(body: dict | None = None) -> dict:
    note = ((body or {}).get("note") or "").strip()[:400]
    try:
        planner.reject(note)
    except ValueError as e:
        raise HTTPException(409, str(e))
    await BUS.publish("plan.rejected", "user", {"note": note})
    return {"ok": True}


@app.patch("/api/plan/task/{key}")
async def plan_patch(key: str, body: dict) -> dict:
    """改单个草案节点：换执行者 / 删节点。批准前就能改，不用重生成。"""
    p = store.plan()
    if not p or p.get("status") != "draft":
        raise HTTPException(409, "没有待批准的草案")
    tasks = p.get("tasks") or []
    node = next((t for t in tasks if t["key"] == key), None)
    if not node:
        raise HTTPException(404, "节点不存在")

    body = body or {}
    if body.get("remove"):
        tasks = [t for t in tasks if t["key"] != key]
        for t in tasks:
            t["deps"] = [d for d in t["deps"] if d != key]
        p["tasks"] = tasks
    elif "assigned" in body:
        aid = body.get("assigned")
        allowed = {a["id"] for a in planner.assignable_agents()}
        node["assigned"] = aid if aid in allowed else None
        node["assigned_name"] = next(
            (a["name"] for a in planner.assignable_agents() if a["id"] == aid), None)
        node["real"] = any(a["id"] == aid and a["real"] for a in planner.assignable_agents())

    store.save_plan(p)
    return {"ok": True, "plan": store.plan()}


@app.get("/api/agents")
def agents() -> dict:
    return {"agents": store.agents()}


@app.get("/api/tasks/{tid}/routing")
def routing(tid: str) -> dict:
    t = store.task(tid)
    if not t:
        raise HTTPException(404, "task not found")
    return {"task_id": tid, "candidates": route_scores(t, store.agents())[:5]}


@app.post("/api/members")
async def join_member(body: dict) -> dict:
    aid = body.get("agent_id")
    if not aid or not store.set_member(aid, True):
        raise HTTPException(400, "agent not found")
    a = store.agent(aid) or {}
    await BUS.publish("member.joined", "user", {"agent_id": aid, "name": a.get("name")})
    return {"ok": True}


@app.delete("/api/members/{aid}")
async def leave_member(aid: str) -> dict:
    if not store.set_member(aid, False):
        raise HTTPException(400, "agent not found")
    a = store.agent(aid) or {}
    await BUS.publish("member.removed", "user", {"agent_id": aid, "name": a.get("name")})
    return {"ok": True}


VALID = {
    ("pending", "running"): "dispatch",
    ("blocked", "running"): "force-dispatch",
    ("running", "done"): "approve",
    ("running", "pending"): "cancel",
    ("done", "pending"): "reopen",
}


@app.post("/api/tasks/{tid}/dispatch")
async def dispatch(tid: str, body: dict | None = None) -> dict:
    t = store.task(tid)
    if not t:
        raise HTTPException(404, "task not found")
    body = body or {}
    aid = body.get("agent_id")
    force = bool(body.get("force"))
    if not aid:
        best = next((c for c in route_scores(t, store.agents()) if c["cap_match"] >= 0.35), None)
        if not best:
            raise HTTPException(409, "没有能力匹配的成员，请先在 Agent 池邀进，或强制派活")
        aid = best["agent_id"]
    a = store.agent(aid)
    if not a or not a["in_team"]:
        raise HTTPException(400, "该 agent 不在组内")
    # health 可能是过期的（WorkBuddy/豆包刚重启过），派活前实地探一次，
    # 别因为 15 秒的心跳窗口期把能跑的执行体误判成不可用。
    if (a.get("adapter") or "none") in engine.PROBEABLE and a.get("health") != "online":
        await probe_and_store(aid)
        a = store.agent(aid) or a
    if t["status"] not in ("pending", "blocked"):
        raise HTTPException(409, f"当前状态 {t['status']} 不可派活")
    if t["status"] == "blocked" and not force:
        raise HTTPException(409, "任务处于阻塞态，需要 force=true 才能强制派活")
    store.update_task(tid, status="running", assignee=aid, progress=0,
                      started_at=store.now_iso(), reason=None, output=None)
    await BUS.publish("task.dispatched", "user",
                      {"task_id": tid, "title": t["title"], "agent_id": aid,
                       "agent": a["name"], "force": force})
    if engine.has_real_runner(a):
        fresh = store.task(tid)
        if fresh:
            await engine.launch(fresh, a)
        return {"ok": True, "agent_id": aid}

    # 没有可用执行体：退回待办并说明原因。
    # 以前这里是静默跳过 launch，任务会永远停在 running、进度 0——
    # 看起来"派出去了"，其实谁也没动，比报错更难排查。
    why = f"{a['name']} 当前不可用：{a.get('evidence') or '未连接'}"
    store.update_task(tid, status="pending", progress=0, started_at=None, reason=why)
    await BUS.publish("task.idle", "scheduler",
                      {"task_id": tid, "title": t["title"], "agent": a["name"], "reason": why})
    return {"ok": False, "agent_id": aid, "degraded": True, "reason": why}


@app.post("/api/tasks/{tid}/cancel")
async def cancel(tid: str) -> dict:
    t = store.task(tid)
    if not t:
        raise HTTPException(404, "task not found")
    if t["status"] not in ("running", "reviewing"):
        raise HTTPException(409, "只有运行中或待验收的任务可以取消")
    if engine.is_running(tid):
        engine.cancel(tid)  # 由 engine 负责落库与事件
        return {"ok": True, "via": "engine"}
    store.update_task(tid, status="pending", progress=0, ended_at=store.now_iso())
    await BUS.publish("task.cancelled", "user", {"task_id": tid, "title": t["title"]})
    return {"ok": True}


@app.post("/api/tasks/{tid}/approve")
async def approve(tid: str) -> dict:
    t = store.task(tid)
    if not t:
        raise HTTPException(404, "task not found")
    if t["status"] not in ("running", "reviewing"):
        raise HTTPException(409, "只有运行中或待验收的任务可以验收")
    store.update_task(tid, status="done", progress=100, ended_at=store.now_iso())
    await BUS.publish("task.done", "user", {"task_id": tid, "title": t["title"], "by": "user"})
    made = await handoff.after_done(tid)
    return {"ok": True, "handoffs": len(made)}


@app.get("/api/tasks/{tid}/handoffs")
def task_handoffs(tid: str) -> dict:
    return {"handoffs": store.handoffs_for(to_task=tid)}


@app.post("/api/handoffs/{hid}/reject")
async def reject_handoff(hid: str, body: dict | None = None) -> dict:
    note = ((body or {}).get("note") or "").strip() or "未填写理由"
    try:
        return await handoff.reject(hid, note)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/demo/reset")
async def demo_reset() -> dict:
    store.reset_demo()
    await BUS.publish("workspace.created", "system", {"name": "演示数据已重置"})
    return {"ok": True}


@app.post("/api/workspace/auto-accept")
async def set_auto_accept(body: dict | None = None) -> dict:
    """开工后自动验收开关。默认关——真跑产出必须有人看过。"""
    on = bool((body or {}).get("on"))
    store.set_auto_accept(on)
    await BUS.publish("workspace.auto_accept", "user", {"on": on})
    return {"ok": True, "auto_accept": on}


@app.post("/api/plan/budget/reset")
async def reset_budget() -> dict:
    store.clear_llm_usage()
    await BUS.publish("budget.reset", "user", {})
    return {"ok": True, "budget": planner.budget()}


@app.post("/api/agents/{aid}/probe")
async def probe_agent(aid: str) -> dict:
    """现场探测一个成员能不能用，并把结果写回 health。

    具体判定逻辑在 app/probe.py —— 后台心跳和派活兜底用的是同一份，
    免得三处各写一遍、又各自跑偏。
    """
    a = store.agent(aid)
    if not a:
        raise HTTPException(404, "agent not found")

    r = await probe(aid)
    if not r:
        raise HTTPException(400, f"{a['name']} 没有可探测的执行体")
    health, ev = r
    store.set_agent_health(aid, health, ev)
    await BUS.publish("agent.probed", "user", {
        "agent_id": aid, "name": a["name"], "health": health, "evidence": ev,
    })
    return {"ok": True, "health": health, "evidence": ev}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    origin = ws.headers.get("origin", "")
    if origin and origin not in ALLOWED_ORIGINS and not origin.startswith("http://localhost"):
        await ws.close(code=1008)
        return
    await ws.accept()
    q = BUS.subscribe()
    try:
        await ws.send_text(json.dumps(
            {"v": 1, "type": "hello", "seq": store.last_seq()}, ensure_ascii=False))
        while True:
            getter = asyncio.create_task(q.get())
            recv = asyncio.create_task(ws.receive_text())
            done, pending = await asyncio.wait(
                {getter, recv}, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
                # 被取消的任务要把异常取走，否则 asyncio 会在退出时刷
                # "Task exception was never retrieved"（之前日志里就有一堆）
                p.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            if getter in done:
                await ws.send_text(json.dumps(getter.result(), ensure_ascii=False))
            if recv in done:
                try:
                    raw = recv.result()
                except Exception:
                    # 对端断开：必须跳出，否则下一轮 receive 会抛
                    # "Cannot call receive once a disconnect message has been received"
                    break
                try:
                    cmd = json.loads(raw)
                except Exception:
                    continue
                if cmd.get("type") == "ping":
                    await ws.send_text(json.dumps(
                        {"v": 1, "type": "pong", "seq": store.last_seq()}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        BUS.unsubscribe(q)
