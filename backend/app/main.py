"""FastAPI 入口：REST 快照 + WebSocket 增量。"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import appcfg, billing, bridge_watch, engine, handoff, planner, sim, store
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
    billing_task = asyncio.create_task(billing.watch())
    try:
        yield
    finally:
        for t in (sim_task, watch_task, billing_task):
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
    except billing.InsufficientBalance as e:
        raise HTTPException(402, str(e))          # 402 Payment Required，语义刚好
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
    """改草案节点：增、删、改都走这里，改完立刻重算依赖 / 环 / 候选。

    - `{"add": {title, objective, stage, required_caps, acceptance}}` 新增节点（key 自动分配）
    - `{"remove": true}` 删除节点，同时把它从别人的 deps 里摘掉
    - 其余按白名单逐项更新：title / objective / stage / required_caps / acceptance / deps / assigned / why

    只认 draft 状态的草案——已经批准开工的图不能再改，那是另一回事。
    """
    p = store.plan()
    if not p or p.get("status") != "draft":
        raise HTTPException(409, "没有待批准的草案")
    body = body or {}
    tasks = p.get("tasks") or []

    if body.get("add"):
        if len(tasks) >= planner.MAX_NODES:
            raise HTTPException(409, f"节点数已达上限 {planner.MAX_NODES} 个")
        spec = body.get("add") or {}
        title = str(spec.get("title") or "").strip()[:24]
        if not title:
            raise HTTPException(400, "新节点需要标题")
        used = {t["key"] for t in tasks}
        i = 1
        while f"n{i}" in used:
            i += 1
        try:
            stage = int(spec.get("stage") or 3)
        except (TypeError, ValueError):
            stage = 3
        caps = [c for c in (spec.get("required_caps") or []) if c in planner.CAP_VOCAB_FOR_MODEL][:2]
        acc = [str(a).strip() for a in (spec.get("acceptance") or []) if str(a).strip()][:4]
        tasks.append({
            "key": f"n{i}",
            "title": title,
            "objective": str(spec.get("objective") or "").strip()[:200],
            "stage": min(5, max(1, stage)),
            "required_caps": caps or ["general"],
            "acceptance": acc or ["产出可被检查的交付物"],
            "deps": [],
            "assigned": None,
            "why": "手工新增",
        })
    else:
        node = next((t for t in tasks if t["key"] == key), None)
        if not node:
            raise HTTPException(404, "节点不存在")

        if body.get("remove"):
            tasks = [t for t in tasks if t["key"] != key]
            for t in tasks:
                t["deps"] = [d for d in (t.get("deps") or []) if d != key]
        else:
            if "title" in body:
                v = str(body.get("title") or "").strip()[:24]
                if not v:
                    raise HTTPException(400, "标题不能为空")
                node["title"] = v
            if "objective" in body:
                node["objective"] = str(body.get("objective") or "").strip()[:200]
            if "why" in body:
                node["why"] = str(body.get("why") or "").strip()[:120]
            if "stage" in body:
                try:
                    node["stage"] = min(5, max(1, int(body.get("stage") or 3)))
                except (TypeError, ValueError):
                    raise HTTPException(400, "阶段填 1~5 的整数")
            if "required_caps" in body:
                caps = [c for c in (body.get("required_caps") or [])
                        if c in planner.CAP_VOCAB_FOR_MODEL][:2]
                node["required_caps"] = caps or ["general"]
            if "acceptance" in body:
                acc = [str(a).strip() for a in (body.get("acceptance") or [])
                       if str(a).strip()][:4]
                node["acceptance"] = acc or ["产出可被检查的交付物"]
            if "deps" in body:
                # 先原样收下，合法性交给 revalidate 统一裁剪（含环检测）
                node["deps"] = [str(d) for d in (body.get("deps") or [])]
            if "assigned" in body:
                aid = body.get("assigned")
                allowed = {a["id"] for a in planner.assignable_agents()}
                node["assigned"] = aid if aid in allowed else None

    p["tasks"] = tasks
    planner.revalidate(p)
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


# 已开工的图：按任务状态限定哪些字段还能改。
# 原则是「已经发出去的东西不该被假装改掉」——
# running 的任务 prompt 已经交给执行体了，改标题/目标只是自欺；
# 但验收标准在 reviewing 之前都该能调，因为它决定的是"算不算过"。
TASK_EDITABLE: dict[str, set[str]] = {
    "pending": {"title", "objective", "acceptance", "required_caps", "assignee", "deps", "priority"},
    "blocked": {"title", "objective", "acceptance", "required_caps", "assignee", "deps", "priority"},
    "running": {"acceptance"},
    "reviewing": {"acceptance"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
}

TASK_FIELD_CN = {
    "title": "标题", "objective": "目标", "acceptance": "验收标准",
    "required_caps": "能力", "assignee": "执行者", "deps": "依赖", "priority": "优先级",
}


@app.patch("/api/tasks/{tid}")
async def task_edit(tid: str, body: dict) -> dict:
    """改已开工图里的单个任务。

    和草案编辑（`/api/plan/task/{key}`）是两回事：那个改的是尚未落地的草案 JSON，
    这个改的是真实任务行，改完立刻影响调度。
    """
    t = store.task(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    body = body or {}
    status = t["status"]
    allowed = TASK_EDITABLE.get(status, set())

    touched = [k for k in body if k in TASK_FIELD_CN]
    locked = [k for k in touched if k not in allowed]
    if locked:
        f = locked[0]
        hint = ""
        if status == "running":
            hint = "。它已经在跑了，prompt 早就发出去，改这些只是自欺"
        elif status in ("done", "failed"):
            hint = "。要重做请先「重新打开」这个任务"
        elif status == "reviewing":
            hint = "。它正等你验收，改内容请先退回"
        raise HTTPException(409, f"「{t['title']}」当前是 {status}，不能改{TASK_FIELD_CN[f]}{hint}")
    if not touched:
        raise HTTPException(400, "没有可改的字段")

    fields: dict[str, object] = {}
    if "title" in body:
        v = str(body.get("title") or "").strip()[:40]
        if not v:
            raise HTTPException(400, "标题不能为空")
        fields["title"] = v
    if "objective" in body:
        fields["objective"] = str(body.get("objective") or "").strip()[:400]
    if "acceptance" in body:
        acc = [str(a).strip() for a in (body.get("acceptance") or []) if str(a).strip()][:5]
        fields["acceptance"] = json.dumps(acc or ["产出可被检查的交付物"], ensure_ascii=False)
    if "required_caps" in body:
        caps = [c for c in (body.get("required_caps") or [])
                if c in planner.CAP_VOCAB_FOR_MODEL][:2]
        fields["required_caps"] = json.dumps(caps or ["general"], ensure_ascii=False)
    if "assignee" in body:
        aid = body.get("assignee")
        mate = {a["id"] for a in store.agents() if a["in_team"]}
        fields["assignee"] = aid if aid in mate else None
    if "priority" in body:
        try:
            fields["priority"] = max(0, min(9, int(body.get("priority") or 1)))
        except (TypeError, ValueError):
            raise HTTPException(400, "优先级要填 0~9 的整数")

    if fields:
        store.update_task(tid, **fields)

    if "deps" in body:
        raw_deps = body.get("deps") or []
        if tid in raw_deps:
            raise HTTPException(400, "任务不能依赖自己")
        ids = {x["id"] for x in store.tasks() if x["id"] != tid}
        deps = [d for d in dict.fromkeys(raw_deps) if d in ids]
        graph = {x["id"]: store.deps_of(x["id"]) for x in store.tasks()}
        graph[tid] = deps
        cyc = planner.cycle_in(graph)
        if cyc:
            raise HTTPException(409, f"这样改会形成循环依赖：{' → '.join(cyc)}，已拒绝")
        store.set_deps_of(tid, deps)

    await BUS.publish("task.edited", "user", {
        "task_id": tid,
        "title": str(fields.get("title") or t["title"]),
        "fields": sorted(set(fields) | ({"deps"} if "deps" in body else set())),
    })
    return {"ok": True, "task": store.task(tid)}


@app.delete("/api/tasks/{tid}")
async def task_remove(tid: str) -> dict:
    """从图里删掉一个任务。只允许删还没开跑的，在跑的请先取消。"""
    t = store.task(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    if t["status"] in ("running", "reviewing") or engine.is_running(tid):
        raise HTTPException(409, f"「{t['title']}」还在跑或待验收，先取消再删")
    downstream = [x["id"] for x in store.tasks() if tid in store.deps_of(x["id"])]
    store.drop_task(tid)
    await BUS.publish("task.removed", "user", {
        "task_id": tid, "title": t["title"], "downstream": downstream,
    })
    return {"ok": True, "freed": downstream}


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


@app.get("/api/billing")
async def billing_status(force: bool = False) -> dict:
    """账户体检：真实余额（供应商支持时）+ 本地 token 账本，给出 ok/warn/critical。

    `force=true` 跳过 60 秒缓存，点「刷新余额」时用。
    """
    return billing.status(force=force)


@app.get("/api/conductor")
async def conductor_get() -> dict:
    """主脑配置视图。**key 只返回掩码，任何情况下都不回原文。**"""
    return appcfg.public()


@app.put("/api/conductor")
async def conductor_put(body: dict) -> dict:
    """改主脑配置。key 缺省=不改，""=清除（回退 .env），其它=DPAPI 加密后入库。

    保存后立即生效：CloudLLM 每次实例化都重新读配置，不用重启后端。
    """
    before = appcfg.public()
    after = appcfg.update(body or {})
    await BUS.publish("conductor.config", "user", {
        "model": after["model"],
        "base_url": after["base_url"],
        "key_changed": before["key_masked"] != after["key_masked"],
        "encryption": after["encryption"],
    })
    return {"ok": True, **after}


@app.post("/api/conductor/test")
async def conductor_test() -> dict:
    """用当前配置发一个 4 token 的最小请求，验证 key / 模型 / base_url。"""
    from .llm import BudgetExceeded, CloudLLM

    try:
        r = await CloudLLM().probe()
    except BudgetExceeded as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    return {"ok": True, **r}


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
