"""真实执行引擎：把任务真正交给 Agent 跑，边跑边把正文推给前端。

与 sim.py 的分工：
- 有 adapter 的 Agent → 这里真调用，产出写回 task.output
  - `ollama`  → 本地模型流式生成（增量 delta）
  - `doubao`  → 豆包「新工作任务」执行体，经 CDP 驱动（快照 snapshot）
- 没有 adapter 的（WorkBuddy / DSH / 主脑占位）→ 仍由 sim 模拟推进

为什么要区分 delta 和 snapshot：
Ollama 的流是单调追加的，增量拼接正确；
豆包生成初期会在气泡里塞「正在思考」占位符再被正文覆盖，增量拼接会把占位符留在结果里，
所以豆包侧每次给全量正文，前端整体替换。
"""
from __future__ import annotations

import asyncio
import time

from . import handoff, store
from .adapters import OllamaAdapter, build_prompt
from .bus import BUS
from .doubao import DoubaoBridge

RUNS: dict[str, asyncio.Task] = {}
_STOP = object()

REAL_ADAPTERS = {"doubao", "codex", "workbuddy", "wb-cdp"} | {"ollama"}

# 这些执行体的"能不能用"取决于外部进程/登录态，配置里读不出来，只能实地探测。
# dispatch 前和后台心跳都用它决定要不要先探一次。
PROBEABLE = {"wb-cdp", "doubao", "workbuddy", "codex", "ollama"}


def has_real_runner(agent: dict) -> bool:
    """有执行体 **且** 当前可用。

    health='unavailable' 的（未登录 / 无额度）不算——宁可退回模拟并标注，
    也不要让它假装在跑，或者派出一个必然失败的任务。
    """
    return ((agent.get("adapter") or "none") in REAL_ADAPTERS
            and agent.get("health") != "unavailable")


def is_running(tid: str) -> bool:
    return tid in RUNS


def cancel(tid: str) -> bool:
    t = RUNS.get(tid)
    if not t:
        return False
    t.cancel()
    return True


async def launch(task: dict, agent: dict) -> None:
    if task["id"] in RUNS:
        return
    RUNS[task["id"]] = asyncio.create_task(_run(task, agent))


async def _run(task: dict, agent: dict) -> None:
    tid = task["id"]
    aid = agent["id"]
    actor = f"agent:{aid}"
    kind = agent.get("adapter") or "none"
    prompt = build_prompt(task, store.workspace())

    q: asyncio.Queue = asyncio.Queue()
    buf: list[str] = []

    async def pump() -> None:
        n = 0
        while True:
            item = await q.get()
            if item is _STOP:
                return
            mode, chunk = item  # ("delta"|"snapshot", str)
            if mode == "snapshot":
                buf.clear()
                buf.append(chunk)
            else:
                buf.append(chunk)
            n += 1
            await BUS.publish(
                "task.token", actor,
                {"task_id": tid, "delta": chunk, "snapshot": mode == "snapshot"},
                persist=False,
            )
            # 豆包是整段回吐，节奏粗；Ollama 是逐 token，节奏细
            step = 6 if mode == "snapshot" else 10
            if n % step == 0:
                p = min(96, 8 + n * (4 if mode == "snapshot" else 1))
                store.update_task(tid, progress=p, output="".join(buf)[-12000:])
                await BUS.publish("task.progress", actor,
                                  {"task_id": tid, "progress": p}, persist=False)

    t0 = time.time()
    pump_task = asyncio.create_task(pump())

    try:
        await BUS.publish("run.started", actor, {
            "task_id": tid, "title": task["title"],
            "agent_id": aid, "agent": agent["name"], "model": agent["model"],
            "adapter": kind, "prompt_chars": len(prompt),
        })

        if kind == "ollama":
            ad = OllamaAdapter()
            stats = await ad.run(
                agent["model"], prompt,
                on_content=lambda s: q.put_nowait(("delta", s)),
                on_thinking=lambda s: None,
            )
        elif kind == "doubao":
            # 安全闸：豆包「工作任务」模式对本地电脑默认是「全部允许」，
            # 会真的执行命令、创建文件、装依赖（实测它自己跑过 node -v / npm install）。
            # 所以只允许它做检索/总结类，代码与命令类一律不给，哪怕被强制派活。
            risky = {"code_write", "shell", "deploy"}
            hit = risky & set(task.get("required_caps") or [])
            if hit:
                raise RuntimeError(
                    f"豆包工作任务模式对本地机器为「全部允许」，拒绝承接 {sorted(hit)} 类任务；"
                    "请改派给 Codex / qwen，或先把豆包的本地权限调成「每次询问」"
                )
            br = DoubaoBridge()
            if not await br.health():
                raise RuntimeError(
                    "豆包 CDP 端口 9333 未开启：需以 --remote-debugging-port=9333 重启豆包"
                    "（见 spike/launch_doubao_debug.cmd）"
                )
            stats = await br.run(
                prompt, task_id=tid,
                on_snapshot=lambda s: q.put_nowait(("snapshot", s)),
            )
        elif kind == "codex":
            from .codex import CodexAdapter

            ca = CodexAdapter()
            if not ca.enabled:
                info = await ca.probe()
                raise RuntimeError(
                    f"Codex 已接入但未启用（{('已安装 ' + str(info['version'])) if info['installed'] else '未找到可执行文件'}，"
                    f"登录态={'已登录' if info['logged_in'] else '未登录'}）。"
                    "当前无可用额度，不会真实调用；如已恢复，请在 backend/.env 设 CODEX_ENABLED=true 并重启后端。"
                )
            stats = await ca.run(prompt, on_content=lambda s: q.put_nowait(("delta", s)))
        elif kind == "wb-cdp":
            from .wb_bridge import WorkBuddyBridge

            br = WorkBuddyBridge()
            if not await br.health():
                raise RuntimeError(
                    "WorkBuddy 调试端口 9444 未开启：需用 spike/wb_debug_launch.bat "
                    "带 --remote-debugging-port=9444 重启桌面端"
                )
            stats = await br.run(
                prompt, task_id=tid,
                on_snapshot=lambda s: q.put_nowait(("snapshot", s)),
            )
        elif kind == "workbuddy":
            from .wb import WorkBuddyAdapter

            wb = WorkBuddyAdapter()
            stats = await wb.run(prompt, on_content=lambda s: q.put_nowait(("delta", s)))
        else:
            raise RuntimeError(f"未知 adapter: {kind}")

        q.put_nowait(_STOP)
        await pump_task

        text = "".join(buf).strip()
        if not text:
            raise RuntimeError("执行体没有输出正文")

        auto = store.auto_accept()
        store.update_task(
            tid, status="done" if auto else "reviewing", progress=100, output=text,
            duration_ms=int((time.time() - t0) * 1000), ended_at=store.now_iso(),
        )
        await BUS.publish("task.review", actor, {
            "task_id": tid, "title": task["title"], "agent": agent["name"],
            "adapter": kind, "auto": auto,
            "tokens": stats.get("eval_count"),
            "chars": len(text),
            "first_token_ms": stats.get("first_token_ms"),
            "elapsed_ms": stats.get("elapsed_ms"),
        })
        if auto:
            # 自动验收开着：不再等人点，直接放行下游
            await BUS.publish("task.auto_accepted", "scheduler", {
                "task_id": tid, "title": task["title"], "agent": agent["name"],
            })
            await BUS.publish("task.done", "auto", {
                "task_id": tid, "title": task["title"], "by": "auto",
            })
            await handoff.after_done(tid)
    except asyncio.CancelledError:
        q.put_nowait(_STOP)
        try:
            await pump_task
        except Exception:
            pass
        store.update_task(tid, status="pending", progress=0, ended_at=store.now_iso())
        await BUS.publish("task.cancelled", "user", {"task_id": tid, "title": task["title"]})
        raise
    except Exception as e:
        q.put_nowait(_STOP)
        try:
            await pump_task
        except Exception:
            pass
        msg = f"{type(e).__name__}: {e}"[:200]
        store.update_task(tid, status="failed", reason=msg, ended_at=store.now_iso())
        await BUS.publish("task.failed", actor, {
            "task_id": tid, "title": task["title"], "error": msg,
        })
    finally:
        RUNS.pop(tid, None)
