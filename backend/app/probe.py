"""统一的能力探测：把「这个执行体现在到底能不能用」收敛到一处。

为什么单独拎出来：WorkBuddy / 豆包的可用性不取决于配置，而取决于
**外部进程此刻是不是带着调试端口在跑**。这个状态在 agentmesh 运行期间会变
（用户关了应用、重启了应用），所以需要三处共用同一份判定：

1. `POST /api/agents/{aid}/probe` —— 用户手点
2. `bridge_watch` 后台心跳 —— 应用重开/关闭时自动跟随
3. `engine` 派活前兜底 —— 状态过期时先探一次再决定，不盲目退回模拟

探测分两段：先看端口通不通（进程死没死），再问接口能不能用（页面结构对不对）。
两段的失败原因完全不同，evidence 里要分开写，否则用户不知道该重启哪个东西。
"""
from __future__ import annotations

from . import store


def _win_alive(title: str) -> bool | None:
    """目标应用的窗口还在不在。返回 None 表示平台不支持判断。"""
    try:
        import ctypes

        return bool(ctypes.windll.user32.FindWindowW(None, title))
    except Exception:
        return None


async def probe(aid: str) -> tuple[str, str] | None:
    """返回 (health, evidence)。没有可探测执行体时返回 None。"""
    a = store.agent(aid)
    if not a:
        return None
    adapter = a.get("adapter") or "none"

    if adapter == "wb-cdp":
        return await _probe_wb_cdp()

    if adapter == "doubao":
        from .doubao import DoubaoBridge

        ok = await DoubaoBridge().health()
        if ok:
            return "online", "CDP :9333 · 工作任务"
        alive = _win_alive("豆包")
        if alive is False:
            return "unavailable", "豆包未运行"
        return "unavailable", "CDP :9333 未开启（需带调试端口重启豆包）"

    if adapter == "workbuddy":
        from .wb import WorkBuddyAdapter

        info = await WorkBuddyAdapter().probe()
        if not info["installed"]:
            return "unavailable", "未找到 codebuddy 可执行文件"
        if info["logged_in"]:
            return "online", f"codebuddy · {info.get('mode') or 'oauth'} · {info['model']}"
        return "unavailable", f"codebuddy · {info.get('error') or '未登录'}"

    if adapter == "codex":
        from .codex import CodexAdapter

        info = await CodexAdapter().probe()
        if not info["enabled"]:
            return "unavailable", f"{info['version'] or 'codex'} · 未启用（CODEX_ENABLED=false）"
        if info["installed"] and info["logged_in"]:
            return "online", f"{info['version']} · 已登录"
        return "unavailable", f"{info['version'] or 'codex'} · 未登录/无额度"

    if adapter == "ollama":
        from .adapters import OllamaAdapter

        ok = await OllamaAdapter().health()
        return ("online", "11434 /api/tags") if ok else ("unavailable", "Ollama 未响应")

    return None


async def _probe_wb_cdp() -> tuple[str, str]:
    from .wb_bridge import DEFAULT_PORT, WorkBuddyBridge

    br = WorkBuddyBridge()
    if not await br.health():
        alive = _win_alive("WorkBuddy")
        if alive is False:
            return "unavailable", "WorkBuddy 未运行（需用 spike/wb_debug_launch.bat 启动）"
        return "unavailable", (
            f"WorkBuddy 在运行但调试端口 :{DEFAULT_PORT} 没开"
            "（需用 spike/wb_debug_launch.bat 重启桌面端）"
        )
    try:
        dom = await br.probe_dom()
    except Exception as e:
        return "degraded", f"CDP :{DEFAULT_PORT} 已开但读取失败：{type(e).__name__}"
    title = (dom.get("title") or "")[:24]
    n = len(dom.get("editors") or [])
    return ("online" if n else "degraded",
            f"CDP :{DEFAULT_PORT} · {title or '页面已连'} · 输入框 {n} 个")


async def probe_and_store(aid: str) -> tuple[str, str] | None:
    """探测并写回 health；只有状态真的变了才返回结果（供心跳判断要不要发事件）。"""
    a = store.agent(aid)
    if not a:
        return None
    r = await probe(aid)
    if not r:
        return None
    health, ev = r
    if health == a.get("health") and ev == a.get("evidence"):
        return None
    store.set_agent_health(aid, health, ev)
    return r
