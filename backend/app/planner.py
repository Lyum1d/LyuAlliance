"""主脑 Conductor：把一句目标拆成可执行的 DAG 草案。

边界（很重要）：
- 它**只规划，不执行**。没有 run_shell / write_file 的能力，输出只有任务图。
- 拆出来的东西**不会自动开工**，必须用户点「批准并开工」才落成真实任务。
- 派活对象只能从"当前协作组里真正可派"的成员里选，不许瞎编 agent。

为什么要这么严：一个能直接派活的主脑，出错时你已经烧掉额度或改错文件了。
"""
from __future__ import annotations

import json
from typing import Any

from . import appcfg, billing, engine, store
from .bus import BUS
from .llm import CloudLLM
from .sim import route_scores

STAGES = ["澄清", "设计", "实现", "验证", "收尾"]
MAX_NODES = 14
MIN_NODES = 3

CAP_VOCAB = [
    "planning", "review", "decide", "long_context", "general", "code",
    "doc", "slides", "research", "web_search", "chinese_news", "summarize",
    "code_write", "refactor", "debug", "shell", "reasoning", "math", "deploy",
]
# 这两个能力只属于主脑与人类，不会出现在可派活的成员身上。
# 一旦被写进 required_caps，路由永远匹配不到人，节点会永久卡在 pending（实测踩过）。
RESERVED_CAPS = {"planning", "decide"}
CAP_VOCAB_FOR_MODEL = [c for c in CAP_VOCAB if c not in RESERVED_CAPS]

SYSTEM_TMPL = """你是 AgentMesh 的主脑（Conductor），负责把用户目标拆成可执行的任务 DAG。

铁律：
1. 你只做规划，不执行任何任务。
2. 只输出 JSON，不要解释文字，不要 markdown 代码块。
3. 每个节点必须是可验收的**动作**，不是"负责XX模块"这种空话。
4. {rule4}
5. 有依赖的写进 deps；无依赖的留空数组，让它们能并行。
6. required_caps 只能从给定词表里选，每节点 1~2 个。
7. assigned 只能从给定成员 id 里选；如果没人合适，填 null 并在 why 里说明。
8. acceptance 至少 1 条，要能被外部检查（"给出 3 条建议"可以，"做得好"不行）。"""

# medium 粒度拼出来与改造前的 SYSTEM 逐字一致，保证"没动过设置"时行为完全不变。
SYSTEM = SYSTEM_TMPL.format(
    rule4="节点数 5~12 个。能用 5 个解决就不要拆 12 个。")

SCHEMA_HINT = """输出 JSON 结构：
{
  "goal_restated": "一句话复述目标（不超过 40 字）",
  "tasks": [
    {
      "key": "n1",
      "title": "不超过 10 字",
      "objective": "一句话说明这一步要产出什么",
      "stage": 1,
      "required_caps": ["web_search"],
      "acceptance": ["可被检查的验收标准"],
      "deps": [],
      "assigned": "a_doubao",
      "why": "为什么派给它（不超过 30 字）"
    }
  ],
  "rationale": "整体拆解思路，2~3 句话",
  "risks": ["最可能在哪儿出问题，以及怎么应对"],
  "estimate": {"note": "大致工作量说明"}
}"""


def assignable_agents() -> list[dict]:
    """能真正接活的成员：在组内、非主脑非人类、健康不是 unavailable。"""
    out = []
    for a in store.agents():
        if not a["in_team"] or a["kind"] == "conductor" or a["role"] == "human":
            continue
        if a["health"] == "unavailable":
            continue
        out.append({
            "id": a["id"],
            "name": a["name"],
            "caps": a["caps"],
            "real": engine.has_real_runner(a),
            "health": a["health"],
        })
    return out


async def draft(goal: str) -> dict:
    goal = (goal or "").strip()
    if len(goal) < 4:
        raise ValueError("目标太短，至少写清楚要做什么")

    agents = assignable_agents()
    if not agents:
        raise ValueError("协作组里没有可派活的成员，先去 Agent 池邀进至少一个")

    member_lines = []
    for a in agents:
        top = sorted(a["caps"].items(), key=lambda kv: -kv[1])[:4]
        caps = ", ".join(f"{k}:{v:.2f}" for k, v in top)
        flag = "真实执行" if a["real"] else "模拟推进"
        member_lines.append(f"- {a['id']}（{a['name']}，{flag}）：{caps}")

    user = f"""目标：{goal}

当前协作组成员（assigned 只能从这里选）：
{chr(10).join(member_lines)}

阶段编号：1=澄清 2=设计 3=实现 4=验证 5=收尾
能力词表（required_caps 只能从这里选，每节点 1~2 个）：
{', '.join(CAP_VOCAB_FOR_MODEL)}

{SCHEMA_HINT}"""

    # 拆解风格：粒度只改第 4 条铁律；额外要求编号续进铁律列表。
    # 早先把它当成一段"额外要求"追加在 system 末尾，模型会当软提示忽略（实测 0/13 节点遵守）；
    # 改成第 9 条、并点名"涉及验收的要写进 acceptance"后遵从度明显不同。
    gkey, gdef = appcfg.granularity()
    system = SYSTEM_TMPL.format(rule4=gdef["rule"])
    extra = appcfg.extra_prompt()
    if extra:
        system += ("\n9. 额外规则（与上面 8 条同等约束力，必须逐条落实；"
                   f"涉及验收标准的要直接写进对应节点的 acceptance）：\n{extra}")

    # 先看钱包。到止损线就抛 InsufficientBalance，别发起一次注定失败的调用——
    # 用户宁可提前被拦下，也不想等到报错才发现余额见底。
    wallet = billing.guard()

    llm = CloudLLM()
    # 输出预算得跟着粒度走：细粒度 + 额外规则会把 2600 撑爆，
    # 被截断的 JSON 解析失败（实测踩过两次：4200 仍不够 13 节点 + 每条回滚方案）。
    out_budget = {"coarse": 2000, "medium": 2600, "fine": 6000}.get(gkey, 2600)
    res = await llm.chat_json(system, user, max_tokens=out_budget, temperature=0.2)
    plan = _normalize(res["data"], agents)
    plan["goal"] = goal
    plan["cost"] = {
        "model": llm.model,
        "prompt_tokens": (res["usage"] or {}).get("prompt_tokens"),
        "completion_tokens": (res["usage"] or {}).get("completion_tokens"),
        "budget": res["budget"],
    }
    # 记下这次用的风格，方便前后对比（也便于判断效果变化是不是它引起的）
    plan["tuning"] = {"granularity": gkey, "extra_prompt": extra}
    plan["wallet"] = {"level": wallet["level"], "balance": wallet["balance"],
                      "message": wallet["message"]}
    store.save_plan(plan)
    return plan


def revalidate(plan: dict) -> dict:
    """草案被手工改过之后，重算一遍派生字段。

    `_normalize` 只在模型返回时跑一次；用户在界面上改标题/依赖/新增节点之后，
    依赖可能指向已删除的 key、也可能改出环，候选与「真实/模拟」标记也会过期。
    这条路径必须和 `_normalize` 用同一套规则，否则两处逻辑会慢慢漂移。
    """
    tasks = plan.get("tasks") or []
    key_set = {t["key"] for t in tasks}

    # 依赖只保留存在的 key，去重、不能自引
    for t in tasks:
        t["deps"] = [d for d in dict.fromkeys(t.get("deps") or [])
                     if d in key_set and d != t["key"]]

    # 环检测：手工连出来的环同样要断
    broken = _break_cycles({t["key"]: t for t in tasks})
    if broken:
        risks = plan.setdefault("risks", [])
        if isinstance(risks, list):
            risks.append(f"手工调整后出现循环依赖，已自动断开：{', '.join(broken)}")

    # 重算路由候选与派生标记（注意：不自动指派，尊重用户留空的选择）
    by_id = {a["id"]: a for a in assignable_agents()}
    for t in tasks:
        t["candidates"] = route_scores(
            {"required_caps": t.get("required_caps") or []}, store.agents())[:3]
        a = by_id.get(t.get("assigned"))
        t["assigned_name"] = a["name"] if a else None
        t["real"] = bool(a and a["real"])
    return plan


def _normalize(raw: dict, agents: list[dict]) -> dict:
    """把模型输出裁剪成可信结构：剔除非法节点、修 dep、补路由理由。"""
    if not isinstance(raw, dict):
        raise RuntimeError("模型输出不是对象")
    tasks_in = raw.get("tasks")
    if not isinstance(tasks_in, list) or not tasks_in:
        raise RuntimeError("模型没有给出 tasks")

    agent_ids = {a["id"] for a in agents}
    by_id = {a["id"]: a for a in agents}

    kept: list[dict] = []
    seen: set[str] = set()
    for i, t in enumerate(tasks_in):
        if not isinstance(t, dict):
            continue
        key = str(t.get("key") or f"n{i + 1}").strip()
        if not key or key in seen:
            key = f"n{i + 1}"
        title = str(t.get("title") or "").strip()[:24]
        if not title:
            continue
        seen.add(key)
        stage = t.get("stage")
        try:
            stage = int(stage)
        except (TypeError, ValueError):
            stage = 3
        stage = min(5, max(1, stage))
        caps = [c for c in (t.get("required_caps") or [])
                if c in CAP_VOCAB and c not in RESERVED_CAPS][:2]
        if not caps:
            caps = ["general"]
        acc = [str(x).strip() for x in (t.get("acceptance") or []) if str(x).strip()][:4]
        if not acc:
            acc = ["产出可被检查的交付物"]
        assigned = t.get("assigned")
        if assigned not in agent_ids:
            assigned = None
        kept.append({
            "key": key,
            "title": title,
            "objective": str(t.get("objective") or "").strip()[:200],
            "stage": stage,
            "required_caps": caps,
            "acceptance": acc,
            "deps": [],
            "assigned": assigned,
            "why": str(t.get("why") or "").strip()[:120],
        })
        if len(kept) >= MAX_NODES:
            break

    if len(kept) < MIN_NODES:
        raise RuntimeError(f"模型只给出了 {len(kept)} 个有效节点，太少（至少要 {MIN_NODES} 个）")

    # 修 deps：只保留存在的 key，且不能自引
    key_set = {k["key"] for k in kept}
    raw_by_key = {
        str(t.get("key") or ""): t for t in tasks_in if isinstance(t, dict)
    }
    for node in kept:
        src = raw_by_key.get(node["key"]) or {}
        deps = [d for d in (src.get("deps") or []) if d in key_set and d != node["key"]]
        node["deps"] = list(dict.fromkeys(deps))

    # 环检测：有环就打断它（宁可让两个节点并列，也不要互相死等）
    node_by_key = {k["key"]: k for k in kept}
    broken = _break_cycles(node_by_key)
    if broken:
        raw.setdefault("risks", [])
        if isinstance(raw.get("risks"), list):
            raw["risks"].append(f"草案里存在循环依赖，已自动断开：{', '.join(broken)}")

    # 为每个节点补路由建议（用真实打分，而不是信模型说的）
    for node in kept:
        cands = route_scores({"required_caps": node["required_caps"]}, store.agents())
        node["candidates"] = cands[:3]
        if node["assigned"] is None and cands:
            best = cands[0]
            if best["cap_match"] >= 0.35:
                node["assigned"] = best["agent_id"]
                node["why"] = node["why"] or f"能力匹配 {best['cap_match']:.2f}，当前最合适"
        if node["assigned"]:
            a = by_id.get(node["assigned"])
            node["assigned_name"] = a["name"] if a else node["assigned"]
            node["real"] = bool(a and a["real"])
        else:
            node["assigned_name"] = None
            node["real"] = False

    return {
        "goal_restated": str(raw.get("goal_restated") or "")[:120],
        "tasks": kept,
        "rationale": str(raw.get("rationale") or "")[:600],
        "risks": [str(r)[:200] for r in (raw.get("risks") or [])][:5],
        "estimate": raw.get("estimate") if isinstance(raw.get("estimate"), dict) else {},
        "status": "draft",
    }


def _find_cycle(nodes: dict[str, dict]) -> list[str]:
    """DFS 找一条环，返回环上的 key 列表；无环返回空。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in nodes}
    path: list[str] = []

    def dfs(k: str) -> list[str] | None:
        color[k] = GRAY
        path.append(k)
        for d in nodes[k]["deps"]:
            if d not in nodes:
                continue
            if color[d] == GRAY:
                return path[path.index(d):]
            if color[d] == WHITE:
                found = dfs(d)
                if found:
                    return found
        path.pop()
        color[k] = BLACK
        return None

    for k in nodes:
        if color[k] == WHITE:
            found = dfs(k)
            if found:
                return found
    return []


def _break_cycles(nodes: dict[str, dict]) -> list[str]:
    """反复找环，每次清空环上第一个节点的 deps，直到无环。"""
    broken: list[str] = []
    for _ in range(30):
        cyc = _find_cycle(nodes)
        if not cyc:
            return broken
        victim = cyc[0]
        nodes[victim]["deps"] = []
        broken.append(victim)
    return broken


def _dep_closed(plan: dict) -> bool:
    keys = {t["key"] for t in plan["tasks"]}
    return all(d in keys for t in plan["tasks"] for d in t["deps"])


async def approve() -> dict:
    """把当前草案落成真实任务图。会**替换**现有任务（草案代表一个新项目）。"""
    plan = store.plan()
    if not plan or plan.get("status") != "draft":
        raise ValueError("没有待批准的草案")
    if not _dep_closed(plan):
        raise ValueError("草案里的依赖指向了不存在的节点，拒绝开工")

    for t in store.tasks():
        if t["status"] == "running":
            engine.cancel(t["id"])

    info = store.replace_graph(plan)
    store.set_plan_status("approved")
    await BUS.publish("plan.approved", "conductor", {
        "goal": plan.get("goal"),
        "tasks": info["tasks"],
        "edges": info["edges"],
        "nodes": [{"key": t["key"], "title": t["title"],
                   "assigned": t.get("assigned_name"), "real": t.get("real")}
                  for t in plan["tasks"]],
    })
    return plan


def reject(note: str) -> dict:
    plan = store.plan()
    if not plan:
        raise ValueError("没有草案可驳回")
    store.set_plan_status("rejected", note)
    return {"ok": True}


def budget() -> dict:
    return CloudLLM().budget()


def cycle_in(deps_map: dict[str, list[str]]) -> list[str]:
    """给定 {id: [它依赖的 id…]}，返回环上的点；无环返回空。

    直接复用 `_find_cycle`，不在这里另写一套图论逻辑——
    草案阶段和已开工阶段对"环"的定义必须一样。
    """
    return _find_cycle({k: {"deps": v} for k, v in deps_map.items()})
