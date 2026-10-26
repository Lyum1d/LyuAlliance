"""交接（Handoff）：把上游的"成果 + 已定决策 + 失败教训"打成结构化上下文包，交给下游。

为什么不直接把聊天记录转过去：
- 聊天记录里有大量过程噪声，模型读完抓不住重点
- 缺"已定决策"，接手的人会推翻重来（最常见的协作浪费）
- 缺"上次为什么失败"，会重复踩同一个坑

所以 ContextPacket 只装四样东西：
  goal / upstream artifacts / decisions / failures

诚实性约定：
- decisions 是**自动抽取 + 人工可改**的，抽取结果会明确标注来源，不当成事实
- 自动接收（模型无法表态）会记 auto=true，界面上说清楚，不假装对方"同意"了
"""
from __future__ import annotations

import re
from typing import Any

from . import store
from .bus import BUS

MAX_EXCERPT = 900
DECISION_HINTS = ("决策", "决定", "约定", "规范", "注意", "必须", "统一", "约束")
DECISION_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*(.{6,120})$")


def _excerpt(text: str, limit: int = MAX_EXCERPT) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + f"\n…（截断，原文 {len(t)} 字）"


def _pick_decisions(outputs: list[tuple[str, str]]) -> list[dict]:
    """从上游产物里挑"像决策"的行。

    这是启发式，不是事实。每条都带 source，界面上标注"自动抽取"。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for task_id, text in outputs:
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or len(line) < 6:
                continue
            if not any(h in line for h in DECISION_HINTS):
                continue
            m = DECISION_RE.match(line)
            if not m:
                continue
            body = m.group(1).strip()
            key = body[:40]
            if key in seen:
                continue
            seen.add(key)
            out.append({"text": body[:160], "source": task_id, "auto": True})
            if len(out) >= 8:
                return out
    return out


def _failures_of(task_id: str) -> list[dict]:
    out = []
    for e in store.events(limit=200):
        if e.get("kind") != "task.failed":
            continue
        p = e.get("payload") or {}
        if p.get("task_id") == task_id:
            out.append({"ts": e.get("ts"), "reason": (p.get("error") or "")[:200]})
    return out[-3:]


def build_packet(task: dict, upstream: list[dict], ws: dict) -> dict:
    """upstream = 该任务所有已完成的前置任务（dict 列表）"""
    arts = []
    pairs: list[tuple[str, str]] = []
    for u in upstream:
        text = u.get("output") or ""
        pairs.append((u["id"], text))
        arts.append({
            "task_id": u["id"],
            "title": u.get("title"),
            "agent": u.get("assignee"),
            "chars": len(text),
            "excerpt": _excerpt(text),
            # 没有真实产出（模拟推进）要标出来，别让下游以为拿到了东西
            "real": bool(text),
        })
    return {
        "version": 1,
        "goal": ws.get("goal") or ws.get("name") or "",
        "task": {
            "id": task["id"],
            "title": task["title"],
            "objective": task.get("objective") or "",
            "acceptance": task.get("acceptance") or [],
        },
        "upstream": arts,
        "decisions": _pick_decisions(pairs),
        "failures": _failures_of(task["id"]),
    }


def _children(tid: str) -> list[str]:
    return [b for a, b in store.edges() if a == tid]


def _deps_all_done(tid: str, tasks: dict[str, dict]) -> bool:
    deps = store.depends_map().get(tid, [])
    return bool(deps) and all(tasks.get(d, {}).get("status") == "done" for d in deps)


async def after_done(tid: str) -> list[dict]:
    """一个任务完成/验收后，把上下文交给所有"依赖已全部满足"的下游。

    自动接收的理由：下游是模型，没法真的"表态"。所以状态记 accepted + auto=true，
    界面上显示「自动接收（模型无法表态）」，需要人否决时点「退回」。
    """
    tasks = {t["id"]: t for t in store.tasks()}
    src = tasks.get(tid)
    if not src:
        return []
    ws = store.workspace()
    made: list[dict] = []

    for child_id in _children(tid):
        child = tasks.get(child_id)
        if not child or child["status"] in ("done", "running"):
            continue
        if not _deps_all_done(child_id, tasks):
            continue

        deps = store.depends_map().get(child_id, [])
        upstream = [tasks[d] for d in deps if d in tasks]
        packet = build_packet(child, upstream, ws)
        hid = f"h_{tid}__{child_id}"
        packet["handoff_id"] = hid
        h = {
            "id": hid,
            "from_task": tid,
            "to_task": child_id,
            "from_agent": src.get("assignee"),
            "to_agent": child.get("assignee"),
            "status": "accepted",
            "packet": packet,
            "note": "",
        }
        store.save_handoff(h)
        store.set_task_context(child_id, packet)
        made.append(h)
        await BUS.publish("handoff.made", "system", {
            "handoff_id": hid,
            "from_task": tid,
            "to_task": child_id,
            "from_agent": src.get("assignee"),
            "to_agent": child.get("assignee"),
            "artifacts": len(packet["upstream"]),
            "decisions": len(packet["decisions"]),
            "failures": len(packet["failures"]),
            "auto": True,
        })
    return made


async def reject(hid: str, note: str) -> dict:
    """把交接退回去：下游任务回到 pending，并清掉它的上下文。"""
    h = next((x for x in store.handoffs_for(limit=200) if x["id"] == hid), None)
    if not h:
        raise ValueError("交接记录不存在")
    store.set_handoff_status(hid, "rejected", note[:300])
    store.set_task_context(h["to_task"], None)
    store.update_task(h["to_task"], status="pending", progress=0, output=None,
                      ended_at=store.now_iso())
    await BUS.publish("handoff.rejected", "user", {
        "handoff_id": hid, "from_task": h["from_task"], "to_task": h["to_task"],
        "note": note[:200],
    })
    return {"ok": True}


def packet_to_prompt(packet: dict | None) -> str:
    """把上下文包压成给模型看的一段话。故意短：塞太多，9B 会抓不住重点。"""
    if not packet:
        return ""
    lines = ["【交接上下文】", f"项目目标：{packet.get('goal') or '-'}"]
    t = packet.get("task") or {}
    if t.get("acceptance"):
        lines.append("本步验收标准：\n" + "\n".join(f"- {a}" for a in t["acceptance"]))
    ups = packet.get("upstream") or []
    if ups:
        lines.append("上游已完成（可直接引用，不要重做）：")
        for u in ups:
            head = (u.get("excerpt") or "").replace("\n", " ")[:220]
            if not u.get("real"):
                lines.append(f"- 「{u.get('title')}」由 {u.get('agent') or '-'} 推进，"
                             f"**没有真实产出**，别当依据")
            else:
                lines.append(f"- 「{u.get('title')}」（{u.get('chars')} 字）：{head}")
    dec = packet.get("decisions") or []
    if dec:
        lines.append("已定决策（必须沿用，不要推翻）：\n"
                     + "\n".join(f"- {d['text']}" for d in dec))
    fail = packet.get("failures") or []
    if fail:
        lines.append("之前失败过（别重复同样做法）：\n"
                     + "\n".join(f"- {f['reason']}" for f in fail))
    lines.append("输出要求：接着上游的结论往下做，不要复述它们。")
    return "\n\n".join(lines)
