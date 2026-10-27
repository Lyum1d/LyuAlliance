"""SQLite 存取层。

设计约束（来自方案文档）：
1. 单一事实源：所有状态只存在这里。
2. 唯一写入口：只有 service/sim 通过这些函数改状态，且每次改都写一条 event。
3. 可回放：events 表 append-only，seq 单调递增。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "agentmesh.db"

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace(
  id TEXT PRIMARY KEY, name TEXT, goal TEXT,
  concurrency INTEGER DEFAULT 2, mode TEXT DEFAULT 'manual',
  auto_accept INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS agent(
  id TEXT PRIMARY KEY, name TEXT, kind TEXT, role TEXT, model TEXT,
  caps TEXT, lane INTEGER, health TEXT, in_team INTEGER DEFAULT 0,
  cost TEXT, evidence TEXT, sort INTEGER, adapter TEXT DEFAULT 'none'
);
CREATE TABLE IF NOT EXISTS task(
  id TEXT PRIMARY KEY, ws_id TEXT, title TEXT, objective TEXT,
  status TEXT, assignee TEXT, stage INTEGER, progress INTEGER DEFAULT 0,
  priority INTEGER DEFAULT 1, required_caps TEXT, acceptance TEXT,
  reason TEXT, duration_ms INTEGER DEFAULT 0, started_at TEXT, ended_at TEXT,
  output TEXT, context TEXT
);
CREATE TABLE IF NOT EXISTS edge(
  from_id TEXT, to_id TEXT, kind TEXT DEFAULT 'hard',
  PRIMARY KEY(from_id, to_id)
);
CREATE TABLE IF NOT EXISTS event(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, ws_id TEXT,
  kind TEXT, actor TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_seq ON event(seq);
CREATE INDEX IF NOT EXISTS idx_task_status ON task(status);
CREATE TABLE IF NOT EXISTS plan(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ws_id TEXT, goal TEXT,
  status TEXT, payload TEXT, note TEXT,
  created_at TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, model TEXT, kind TEXT,
  prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage(ts);
CREATE TABLE IF NOT EXISTS handoff(
  id TEXT PRIMARY KEY, ws_id TEXT, from_task TEXT, to_task TEXT,
  from_agent TEXT, to_agent TEXT, status TEXT, packet TEXT, note TEXT,
  created_at TEXT, decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_handoff_to ON handoff(to_task);
CREATE TABLE IF NOT EXISTS app_config(
  key TEXT PRIMARY KEY, value TEXT,
  secret INTEGER DEFAULT 0,   -- 1 = value 是 secretbox 密文
  updated_at TEXT
);
"""

# ---------------------------------------------------------------- 种子数据

AGENTS = [
    dict(id="a_brain", name="主脑", kind="conductor", role="orchestrator",
         model="deepseek-flash", caps={"planning": 0.95, "review": 0.8}, lane=0,
         health="online", in_team=1, cost="paid", evidence="云端 API", sort=0, adapter="none"),
    dict(id="a_human", name="你", kind="human", role="human",
         model="", caps={"decide": 1.0}, lane=1,
         health="online", in_team=1, cost="free", evidence="人类成员", sort=1, adapter="none"),
    dict(id="a_qwen", name="qwen3.5:9b", kind="native", role="worker",
         model="qwen3.5:9b", caps={"long_context": 0.95, "general": 0.7, "code": 0.6}, lane=2,
         health="online", in_team=1, cost="free", evidence="11434 /api/tags", sort=2, adapter="ollama"),
    dict(id="a_wb", name="WorkBuddy 桌面端", kind="bridge", role="worker",
         model="WorkBuddy V5.9.2 · CDP", caps={"doc": 0.9, "slides": 0.85, "research": 0.75}, lane=3,
         health="unavailable", in_team=0, cost="paid",
         evidence="CDP :9444 · 未开启（需用 spike/wb_debug_launch.bat 带调试端口启动）",
         sort=3, adapter="wb-cdp"),
    dict(id="a_doubao", name="豆包", kind="bridge", role="worker",
         model="doubao-office-task", caps={"web_search": 0.95, "chinese_news": 0.9, "summarize": 0.85}, lane=4,
         health="online", in_team=1, cost="free", evidence="CDP :9333 · 工作任务", sort=4, adapter="doubao"),
    dict(id="a_dsh", name="DeepSeek Harness", kind="bridge", role="worker",
         model="", caps={"code_write": 0.9, "refactor": 0.85, "debug": 0.8}, lane=5,
         health="degraded", in_team=1, cost="free", evidence=":8326 需 key", sort=5, adapter="none"),
    dict(id="a_codex", name="Codex CLI", kind="native", role="worker",
         model="gpt-5.6-luna", caps={"code_write": 0.92, "shell": 0.85, "review": 0.85}, lane=6,
         health="unavailable", in_team=0, cost="paid",
         evidence="codex-cli 0.164.0 · 未登录/无额度，已接入未启用", sort=6, adapter="codex"),
    dict(id="a_r1", name="deepseek-r1:7b", kind="native", role="worker",
         model="deepseek-r1:7b", caps={"reasoning": 0.8, "math": 0.75, "code": 0.55}, lane=7,
         health="online", in_team=0, cost="free", evidence="11434 /api/tags", sort=7, adapter="ollama"),
]

# 健康态由运行时探测决定的执行体（CDP 桥接 / 本地模型）。
# 不能在启动 seed 时按默认值强制覆盖，否则每次重启后端都会把已经连上的成员打回不可用。
LIVE_PROBE = {"wb-cdp", "doubao", "workbuddy", "ollama"}

TASKS = [    dict(id="t1", title="需求澄清", status="done", assignee="a_brain", stage=0,
         progress=100, acceptance=["明确 3 条功能边界"], duration_ms=80_000),
    dict(id="t2", title="技术选型", status="done", assignee="a_human", stage=0,
         progress=100, acceptance=["确定前后端框架"], duration_ms=242_000),
    dict(id="t3", title="数据建模", status="done", assignee="a_qwen", stage=1,
         progress=100, acceptance=["给出任务/事件表结构"], duration_ms=160_000),
    dict(id="t4", title="接口设计", status="done", assignee="a_brain", stage=1,
         progress=100, acceptance=["REST 与 WS 协议定稿"], duration_ms=190_000),
    dict(id="t5", title="写登录接口", status="running", assignee="a_qwen", stage=2,
         progress=62, required_caps=["code"],
         acceptance=["返回 JWT，有效期 2h", "密码错误返回 401 且不泄露用户是否存在", "连续 5 次失败锁定 10 分钟"]),
    dict(id="t6", title="页面骨架", status="running", assignee="a_wb", stage=2,
         progress=38, required_caps=["doc"],
         acceptance=["三栏布局可运行"]),
    dict(id="t7", title="检索最佳实践", status="running", assignee="a_doubao", stage=2,
         progress=22, required_caps=["web_search"],
         acceptance=["给出 3 条可落地建议"]),
    dict(id="t8", title="单元测试", status="blocked", assignee=None, stage=3,
         progress=0, required_caps=["code_write"],
         reason="能力不匹配：Codex 未在组内"),
    dict(id="t9", title="前后端联调", status="pending", assignee="a_human", stage=3,
         progress=0, acceptance=["登录链路跑通"]),
    dict(id="t10", title="部署脚本", status="pending", assignee="a_qwen", stage=4,
         progress=0, acceptance=["一条命令起服务"]),
]

EDGES = [
    ("t1", "t2"), ("t1", "t3"), ("t2", "t4"), ("t3", "t4"),
    ("t4", "t5"), ("t4", "t6"), ("t4", "t7"),
    ("t5", "t8"), ("t6", "t8"), ("t7", "t9"), ("t8", "t9"), ("t9", "t10"),
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def now_iso() -> str:
    return _now()


def _ensure_col(c: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db(reset: bool = False) -> None:
    with _lock:
        c = conn()
        c.executescript(SCHEMA)
        # 老库补列（v0.1 → v0.2）
        _ensure_col(c, "agent", "adapter", "TEXT DEFAULT 'none'")
        _ensure_col(c, "task", "output", "TEXT")
        _ensure_col(c, "workspace", "auto_accept", "INTEGER DEFAULT 0")
        _ensure_col(c, "task", "context", "TEXT")
        if reset:
            for t in ("workspace", "agent", "task", "edge", "event"):
                c.execute(f"DELETE FROM {t}")
        if c.execute("SELECT COUNT(*) FROM workspace").fetchone()[0] == 0:
            c.execute(
                "INSERT INTO workspace(id,name,goal,concurrency,mode,created_at) VALUES(?,?,?,?,?,?)",
                ("ws_main", "登录待办应用",
                 "做一个带登录的待办应用，前端 React，后端 FastAPI，能本地跑起来",
                 2, "manual", _now()))
        if c.execute("SELECT COUNT(*) FROM agent").fetchone()[0] == 0:
            for a in AGENTS:
                c.execute(
                    "INSERT INTO agent(id,name,kind,role,model,caps,lane,health,in_team,cost,evidence,sort,adapter)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (a["id"], a["name"], a["kind"], a["role"], a["model"],
                     json.dumps(a["caps"], ensure_ascii=False), a["lane"], a["health"],
                     a["in_team"], a["cost"], a["evidence"], a["sort"], a["adapter"]))
        else:
            # 已有库：同步「描述性元数据」和 adapter（不覆盖 in_team 等用户改过的状态）。
            for a in AGENTS:
                c.execute(
                    "UPDATE agent SET adapter=?, evidence=?, model=? WHERE id=?",
                    (a["adapter"], a["evidence"], a["model"], a["id"]),
                )
                # 只有「配置层面就永久不可用」的才强制翻成 unavailable
                # （如 codex 没额度、DSH 需 key）。
                #
                # 桥接类（CDP 端口 / 本地 Ollama）的健康态完全取决于外部进程此刻
                # 有没有带着调试端口在跑，必须留给运行时探测。以前这里对所有
                # unavailable 都强制覆盖、还顺手 in_team=0，结果是：重启一次后端，
                # 已经连上的 WorkBuddy 就被打回不可用并踢出团队，得手动重邀。
                if a["health"] == "unavailable" and a["adapter"] not in LIVE_PROBE:
                    c.execute("UPDATE agent SET health='unavailable' WHERE id=?", (a["id"],))
        if c.execute("SELECT COUNT(*) FROM task").fetchone()[0] == 0:
            _seed_tasks(c)
        if c.execute("SELECT COUNT(*) FROM edge").fetchone()[0] == 0:
            for f, t in EDGES:
                c.execute("INSERT INTO edge(from_id,to_id) VALUES(?,?)", (f, t))
        if c.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 0:
            c.execute("INSERT INTO event(ts,ws_id,kind,actor,payload) VALUES(?,?,?,?,?)",
                      (_now(), "ws_main", "workspace.created", "system",
                       json.dumps({"name": "登录待办应用"}, ensure_ascii=False)))
        c.commit()


def _seed_tasks(c: sqlite3.Connection) -> None:
    for t in TASKS:
        c.execute(
            "INSERT INTO task(id,ws_id,title,objective,status,assignee,stage,progress,"
            "priority,required_caps,acceptance,reason,duration_ms,started_at,ended_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t["id"], "ws_main", t["title"], t.get("objective", ""), t["status"],
             t.get("assignee"), t["stage"], t["progress"], t.get("priority", 1),
             json.dumps(t.get("required_caps", []), ensure_ascii=False),
             json.dumps(t.get("acceptance", []), ensure_ascii=False),
             t.get("reason"), t.get("duration_ms", 0),
             _now() if t["status"] == "running" else None,
             _now() if t["status"] == "done" else None))


def reset_demo() -> None:
    """把任务表复位到演示初始状态（用于反复演示，不动 agent/edge）。"""
    with _lock:
        c = conn()
        c.execute("DELETE FROM task")
        _seed_tasks(c)
        c.commit()


# ---------------------------------------------------------------- 读

def _row_to_agent(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["caps"] = json.loads(d["caps"] or "{}")
    d["in_team"] = bool(d["in_team"])
    return d


def _row_to_task(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["required_caps"] = json.loads(d["required_caps"] or "[]")
    d["acceptance"] = json.loads(d["acceptance"] or "[]")
    # context 是交接包 JSON，解析成对象给前端直接用
    raw = d.get("context")
    if raw:
        try:
            d["context"] = json.loads(raw)
        except (TypeError, ValueError):
            d["context"] = None
    else:
        d["context"] = None
    return d


def agents() -> list[dict]:
    with _lock:
        rs = conn().execute("SELECT * FROM agent ORDER BY sort").fetchall()
    return [_row_to_agent(r) for r in rs]


def agent(aid: str) -> dict | None:
    with _lock:
        r = conn().execute("SELECT * FROM agent WHERE id=?", (aid,)).fetchone()
    return _row_to_agent(r) if r else None


def tasks() -> list[dict]:
    with _lock:
        rs = conn().execute("SELECT * FROM task ORDER BY rowid").fetchall()
    return [_row_to_task(r) for r in rs]


def task(tid: str) -> dict | None:
    with _lock:
        r = conn().execute("SELECT * FROM task WHERE id=?", (tid,)).fetchone()
    return _row_to_task(r) if r else None


def edges() -> list[list[str]]:
    with _lock:
        rs = conn().execute("SELECT from_id,to_id FROM edge").fetchall()
    return [[r["from_id"], r["to_id"]] for r in rs]


def depends_map() -> dict[str, list[str]]:
    m: dict[str, list[str]] = {}
    for f, t in edges():
        m.setdefault(t, []).append(f)
    return m


def workspace() -> dict:
    with _lock:
        r = conn().execute("SELECT * FROM workspace WHERE id='ws_main'").fetchone()
    return dict(r) if r else {}


def events(limit: int = 60, since: int = 0) -> list[dict]:
    with _lock:
        if since:
            rs = conn().execute(
                "SELECT * FROM event WHERE seq>? ORDER BY seq DESC LIMIT ?", (since, limit)).fetchall()
        else:
            rs = conn().execute(
                "SELECT * FROM event ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rs:
        d = dict(r)
        d["payload"] = json.loads(d["payload"] or "{}")
        out.append(d)
    return list(reversed(out))


def last_seq() -> int:
    with _lock:
        r = conn().execute("SELECT COALESCE(MAX(seq),0) FROM event").fetchone()
    return int(r[0])


def snapshot() -> dict:
    ws = workspace()
    ags = agents()
    tks = tasks()
    return {
        "workspace": ws,
        "agents": ags,
        "tasks": tks,
        "edges": edges(),
        "events": events(40),
        "seq": last_seq(),
    }


# ---------------------------------------------------------------- 写

def append_event(kind: str, actor: str, payload: dict) -> int:
    with _lock:
        c = conn()
        cur = c.execute(
            "INSERT INTO event(ts,ws_id,kind,actor,payload) VALUES(?,?,?,?,?)",
            (_now(), "ws_main", kind, actor, json.dumps(payload, ensure_ascii=False)))
        c.commit()
        return int(cur.lastrowid)


def update_task(tid: str, **fields: Any) -> None:
    if not fields:
        return
    keys = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        c = conn()
        c.execute(f"UPDATE task SET {keys} WHERE id=?", (*fields.values(), tid))
        c.commit()


def set_member(aid: str, in_team: bool) -> bool:
    a = agent(aid)
    if not a:
        return False
    with _lock:
        c = conn()
        c.execute("UPDATE agent SET in_team=? WHERE id=?", (1 if in_team else 0, aid))
        c.commit()
    return True


def all_events_count() -> int:
    with _lock:
        return int(conn().execute("SELECT COUNT(*) FROM event").fetchone()[0])


# ---------------------------------------------------------------- 主脑计划草案

def save_plan(plan: dict) -> int:
    """存一份草案。同时只留一份 draft——旧草案自动作废，避免"批准了哪份"说不清。"""
    with _lock:
        c = conn()
        c.execute("UPDATE plan SET status='superseded' WHERE status='draft'")
        cur = c.execute(
            "INSERT INTO plan(ws_id,goal,status,payload,note,created_at)"
            " VALUES(?,?,?,?,?,?)",
            ("ws_main", plan.get("goal", ""), "draft",
             json.dumps(plan, ensure_ascii=False), "", _now()),
        )
        c.commit()
        return int(cur.lastrowid or 0)


def plan() -> dict | None:
    """取最近一份草案（draft 优先）。"""
    with _lock:
        r = conn().execute(
            "SELECT * FROM plan ORDER BY (status='draft') DESC, id DESC LIMIT 1"
        ).fetchone()
    if not r:
        return None
    p = json.loads(r["payload"])
    p["status"] = r["status"]
    p["note"] = r["note"]
    p["created_at"] = r["created_at"]
    return p


def set_plan_status(status: str, note: str = "") -> None:
    with _lock:
        c = conn()
        c.execute(
            "UPDATE plan SET status=?, note=?, decided_at=?"
            " WHERE id=(SELECT id FROM plan ORDER BY id DESC LIMIT 1)",
            (status, note, _now()),
        )
        c.commit()


def replace_graph(plan_data: dict) -> dict:
    """用草案替换整个任务图 —— 草案代表一个新项目，不是往旧图里加节点。

    真跑中的任务由调用方先 cancel，这里只管落库。
    """
    with _lock:
        c = conn()
        c.execute("DELETE FROM edge")
        c.execute("DELETE FROM task")
        c.execute("DELETE FROM handoff")   # 旧图的交接记录没有意义了
        idmap: dict[str, str] = {}
        for t in plan_data["tasks"]:
            tid = f"p_{t['key']}"
            idmap[t["key"]] = tid
            c.execute(
                "INSERT INTO task(id,ws_id,title,objective,status,assignee,stage,"
                "progress,priority,required_caps,acceptance,reason,duration_ms,"
                "started_at,ended_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, "ws_main", t["title"], t.get("objective", ""), "pending",
                 t.get("assigned"), int(t.get("stage") or 3), 0, 1,
                 json.dumps(t.get("required_caps") or [], ensure_ascii=False),
                 json.dumps(t.get("acceptance") or [], ensure_ascii=False),
                 None, 0, None, None),
            )
        n_edges = 0
        for t in plan_data["tasks"]:
            for d in t.get("deps") or []:
                if d in idmap:
                    c.execute("INSERT OR IGNORE INTO edge(from_id,to_id) VALUES(?,?)",
                              (idmap[d], idmap[t["key"]]))
                    n_edges += 1
        c.commit()
    return {"tasks": len(idmap), "edges": n_edges}


# ---------------------------------------------------------------- 工作区设置

def set_auto_accept(on: bool) -> bool:
    with _lock:
        c = conn()
        c.execute("UPDATE workspace SET auto_accept=? WHERE id='ws_main'", (1 if on else 0,))
        c.commit()
    return on


def auto_accept() -> bool:
    return bool(workspace().get("auto_accept") or 0)


# ---------------------------------------------------------------- LLM 用量账本

_USAGE_DDL = (
    "CREATE TABLE IF NOT EXISTS llm_usage("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, model TEXT, kind TEXT,"
    "prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0)"
)


def _ensure_usage_table(c: sqlite3.Connection) -> None:
    """允许在 init_db 之前调用（例如模块级预热），不至于炸在 'no such table'。"""
    c.execute(_USAGE_DDL)


def add_llm_usage(model: str, kind: str, prompt_tokens: int, completion_tokens: int) -> None:
    with _lock:
        c = conn()
        _ensure_usage_table(c)
        c.execute(
            "INSERT INTO llm_usage(ts,model,kind,prompt_tokens,completion_tokens)"
            " VALUES(?,?,?,?,?)",
            (_now(), model, kind, int(prompt_tokens or 0), int(completion_tokens or 0)),
        )
        c.commit()


def llm_usage_totals() -> dict:
    with _lock:
        c = conn()
        _ensure_usage_table(c)
        r = c.execute(
            "SELECT COUNT(*) AS calls,"
            " COALESCE(SUM(prompt_tokens),0) AS p,"
            " COALESCE(SUM(completion_tokens),0) AS c,"
            " MIN(ts) AS first_ts"
            " FROM llm_usage"
        ).fetchone()
    return {
        "calls": int(r["calls"] or 0),
        "prompt_tokens": int(r["p"] or 0),
        "completion_tokens": int(r["c"] or 0),
        "first_ts": r["first_ts"],
    }


def clear_llm_usage() -> None:
    with _lock:
        c = conn()
        c.execute("DELETE FROM llm_usage")
        c.commit()


def set_agent_health(aid: str, health: str, evidence: str | None = None) -> None:
    with _lock:
        c = conn()
        if evidence is None:
            c.execute("UPDATE agent SET health=? WHERE id=?", (health, aid))
        else:
            c.execute("UPDATE agent SET health=?, evidence=? WHERE id=?", (health, evidence, aid))
        c.commit()


def reclaim_running(reason: str = "上次后端退出时被中断") -> list[str]:
    """回收僵尸任务。

    RUNS 是进程内字典，后端一重启就没了；但库里那些 status='running' 不会自己变，
    会永远卡在进度 0、谁也没在跑。启动时统一退回待办并写明原因，让调度能重新接。
    """
    with _lock:
        c = conn()
        rows = [r[0] for r in c.execute("SELECT id FROM task WHERE status='running'").fetchall()]
        if rows:
            c.execute(
                "UPDATE task SET status='pending', progress=0, started_at=NULL, reason=? "
                "WHERE status='running'",
                (reason,),
            )
            c.commit()
        return rows


# ---------------------------------------------------------------- 交接（handoff）

def save_handoff(h: dict) -> None:
    with _lock:
        c = conn()
        c.execute(
            "INSERT OR REPLACE INTO handoff(id,ws_id,from_task,to_task,from_agent,"
            "to_agent,status,packet,note,created_at,decided_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (h["id"], "ws_main", h.get("from_task"), h.get("to_task"),
             h.get("from_agent"), h.get("to_agent"), h.get("status", "proposed"),
             json.dumps(h.get("packet") or {}, ensure_ascii=False),
             h.get("note") or "", h.get("created_at") or _now(), h.get("decided_at")),
        )
        c.commit()


def handoffs_for(to_task: str | None = None, limit: int = 60) -> list[dict]:
    with _lock:
        c = conn()
        if to_task:
            rows = c.execute(
                "SELECT * FROM handoff WHERE to_task=? ORDER BY created_at DESC LIMIT ?",
                (to_task, limit)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM handoff ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["packet"] = json.loads(d["packet"] or "{}")
        except ValueError:
            d["packet"] = {}
        out.append(d)
    return out


def set_handoff_status(hid: str, status: str, note: str = "") -> bool:
    with _lock:
        c = conn()
        cur = c.execute(
            "UPDATE handoff SET status=?, note=?, decided_at=? WHERE id=?",
            (status, note, _now(), hid))
        c.commit()
    return cur.rowcount > 0


def set_task_context(tid: str, payload: dict | None) -> None:
    with _lock:
        c = conn()
        c.execute("UPDATE task SET context=? WHERE id=?",
                  (json.dumps(payload, ensure_ascii=False) if payload else None, tid))
        c.commit()


# ---------------------------------------------------------------- 应用配置（主脑等）

def config_pairs() -> dict[str, tuple[str, bool]]:
    """{key: (value, secret)}。secret=1 表示 value 是密文。"""
    with _lock:
        c = conn()
        rows = c.execute("SELECT key, value, secret FROM app_config").fetchall()
    return {r[0]: (r[1], bool(r[2])) for r in rows}


def config_set(key: str, value: str, secret: bool = False) -> None:
    with _lock:
        c = conn()
        c.execute(
            "INSERT INTO app_config(key, value, secret, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "secret=excluded.secret, updated_at=excluded.updated_at",
            (key, value, 1 if secret else 0, now_iso()),
        )
        c.commit()


def config_del(key: str) -> None:
    with _lock:
        c = conn()
        c.execute("DELETE FROM app_config WHERE key=?", (key,))
        c.commit()


# ---------------------------------------------------------------- 单任务增量编辑

def deps_of(task_id: str) -> list[str]:
    """这个任务依赖谁（edge 的 from 是依赖方，to 是它自己）。"""
    with _lock:
        rows = conn().execute("SELECT from_id FROM edge WHERE to_id=?", (task_id,)).fetchall()
    return [r[0] for r in rows]


def set_deps_of(task_id: str, deps: list[str]) -> None:
    with _lock:
        c = conn()
        c.execute("DELETE FROM edge WHERE to_id=?", (task_id,))
        for d in deps:
            c.execute("INSERT OR IGNORE INTO edge(from_id,to_id) VALUES(?,?)", (d, task_id))
        c.commit()


def drop_task(task_id: str) -> bool:
    """删任务并清掉所有相关边与交接记录，返回是否真的删掉了。"""
    with _lock:
        c = conn()
        cur = c.execute("DELETE FROM task WHERE id=?", (task_id,))
        c.execute("DELETE FROM edge WHERE from_id=? OR to_id=?", (task_id, task_id))
        c.execute("DELETE FROM handoff WHERE from_task=? OR to_task=?", (task_id, task_id))
        c.commit()
        return cur.rowcount > 0
