"""云端账户余额体检。

主脑能不能干活，取决于"还有没有钱"。这个信息有两个来源，两个都要看：

1. **供应商余额接口**（`GET /user/balance`，DeepSeek 有）——反映真实扣费，最准
2. **本地 token 账本**（`llm_usage` 表）——供应商不提供余额接口时兜底，也能防"预算失控"

设计上分三档：
- `ok`       正常
- `warn`     快不够了，照常干活但提示
- `critical` 已到止损线，**拦下新的拆解**（不是等调用失败了才说）

阈值走环境变量，默认余额 ¥5 提醒 / ¥1 拦下。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from . import appcfg, store
from .config import env_float


class InsufficientBalance(RuntimeError):
    """余额或预算已到止损线，不该再发起调用。"""


_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_TTL = 60.0          # 余额不要查太勤，60 秒缓存足够

DEFAULT_WARN = 5.0   # 元
DEFAULT_STOP = 1.0   # 元


def _thresholds() -> tuple[float, float]:
    warn = env_float("BALANCE_WARN_CNY", DEFAULT_WARN)
    stop = env_float("BALANCE_STOP_CNY", DEFAULT_STOP)
    return warn, max(0.0, stop)


def fetch_balance(force: bool = False) -> dict | None:
    """查真实余额。失败返回 None（可能是不支持该接口的供应商，或网络问题）。"""
    now = time.time()
    if not force and _CACHE["data"] is not None and now - _CACHE["at"] < _TTL:
        return _CACHE["data"]

    cfg = appcfg.effective()
    if not cfg["api_key"]:
        return None
    try:
        r = httpx.get(
            f"{cfg['base_url'].rstrip('/')}/user/balance",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            timeout=httpx.Timeout(8, connect=4),
            verify=False,        # 本机代理做 TLS 中间人，公共 CA 验不过
        )
        if r.status_code != 200:
            return None
        body = r.json()
    except Exception:
        return None

    infos = body.get("balance_infos") or []
    if not infos:
        return None
    info = next((x for x in infos if (x.get("currency") or "").upper() == "CNY"), infos[0])
    data = {
        "available": bool(body.get("is_available")),
        "balance": float(info.get("total_balance") or 0),
        "granted": float(info.get("granted_balance") or 0),
        "topped_up": float(info.get("topped_up_balance") or 0),
        "currency": info.get("currency") or "CNY",
        "at": now,
    }
    _CACHE.update(at=now, data=data)
    return data


def cost_per_run() -> float:
    """估一次拆解要花多少钱：用账本里的平均 token 数，没数据就按典型值猜。"""
    cfg = appcfg.effective()
    t = store.llm_usage_totals()
    calls = t["calls"] or 0
    if calls:
        avg_p = t["prompt_tokens"] / calls
        avg_c = t["completion_tokens"] / calls
    else:
        avg_p, avg_c = 700.0, 3000.0
    return (avg_p / 1e6) * cfg["price_in"] + (avg_c / 1e6) * cfg["price_out"]


def status(force: bool = False) -> dict:
    """综合余额与预算，给出可展示、可判断的体检结果。"""
    warn_below, stop_below = _thresholds()
    cfg = appcfg.effective()
    bal = fetch_balance(force)

    t = store.llm_usage_totals()
    used = (t["prompt_tokens"] or 0) + (t["completion_tokens"] or 0)
    limit = int(env_float("CONDUCTOR_BUDGET_TOKENS", 200_000)) or 200_000
    pct = round(used / limit * 100, 1)

    per = cost_per_run()
    est_runs = int(bal["balance"] / per) if (bal and per > 0) else None

    level = "ok"
    reasons: list[str] = []

    if bal is None:
        level = "unknown"
        reasons.append("查不到余额（供应商可能没有余额接口），只能按本地 token 账本估算")
    elif not bal["available"]:
        level = "critical"
        reasons.append("账户不可用：余额已耗尽或被停用")
    elif bal["balance"] <= stop_below:
        level = "critical"
        reasons.append(
            f"余额仅 ¥{bal['balance']:.2f}，已到止损线 ¥{stop_below:.0f}，新的拆解会被拦下"
        )
    elif bal["balance"] <= warn_below:
        level = "warn"
        reasons.append(f"余额 ¥{bal['balance']:.2f}，低于提醒线 ¥{warn_below:.0f}")

    if pct >= 95:
        level = "critical"
        reasons.append(f"本地 token 账本已用到 {pct}%（{used}/{limit}），触顶会拒绝调用")
    elif pct >= 80 and level == "ok":
        level = "warn"
        reasons.append(f"本地 token 账本已用到 {pct}%")

    if not reasons:
        msg = "余额正常"
        if bal:
            msg = f"余额 ¥{bal['balance']:.2f}（{bal['currency']}）"
            if est_runs is not None:
                msg += f"，按最近消耗约还能拆 {est_runs} 次"

    return {
        "level": level,
        "message": "；".join(reasons) if reasons else msg,
        "balance": bal["balance"] if bal else None,
        "currency": bal["currency"] if bal else None,
        "available": bal["available"] if bal else None,
        "balance_known": bal is not None,
        "warn_below": warn_below,
        "stop_below": stop_below,
        "est_runs": est_runs,
        "per_run_cost": round(per, 6),
        "budget_used": used,
        "budget_limit": limit,
        "budget_pct": pct,
        "model": cfg["model"],
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def guard() -> dict:
    """拆解前守一道。到止损线就抛，避免发起一次注定失败（或注定欠费）的调用。"""
    s = status()
    if s["level"] == "critical":
        raise InsufficientBalance(s["message"])
    return s


async def watch(interval: float = 300.0) -> None:
    """后台巡检：等级变化时发事件，界面上能看到余额什么时候开始告警。"""
    from .bus import BUS

    last_level = None
    await _sleep(3.0)
    while True:
        try:
            s = status(force=True)
            if last_level is not None and s["level"] != last_level:
                await BUS.publish("billing.changed", "scheduler", {
                    "level": s["level"],
                    "from": last_level,
                    "message": s["message"],
                    "balance": s["balance"],
                })
            last_level = s["level"]
        except Exception:
            pass
        await _sleep(interval)


async def _sleep(sec: float) -> None:
    import asyncio

    await asyncio.sleep(sec)
