"""Codex CLI 适配器 —— 已接入，但默认不启用。

为什么默认关闭：
用户当前 Codex 代理无可用额度，真实调用会直接失败，且可能产生计费。
所以这里做的是「登记 + 零成本探测」，不会发出任何一次真实推理请求。

设计要点：
- `enabled` 由环境变量 `CODEX_ENABLED` 控制，默认 false
- `probe()` 只跑 `codex --version` / `codex login status`，两者都不消耗额度
- `run()` 即使被调用，在 enabled=False 时立刻抛 CodexDisabled，不发子进程
- 真要启用时，把 `CODEX_ENABLED=true` 写进 .env 并重启后端即可，代码无需改动

已验证程度（诚实标注）：
- ✅ CLI 存在：`codex-cli 0.164.0`
- ✅ 非交互入口：`codex exec --json` 输出 JSONL 事件流（--help 实测）
- ⚠️ 端到端**未验证**：无额度，从未真实跑通一次任务。启用后需要先验一次再信任。
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import time
from typing import Callable


class CodexDisabled(RuntimeError):
    """Codex 已登记但未启用。"""


def _candidates() -> list[str]:
    out: list[str] = []
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    out += glob.glob(os.path.join(local, "OpenAI", "Codex", "bin", "*", "codex.exe"))
    which = shutil.which("codex")
    if which:
        out.append(which)
    return out


def find_binary() -> str | None:
    return next((p for p in _candidates() if os.path.isfile(p)), None)


class CodexAdapter:
    def __init__(self, binary: str | None = None, enabled: bool | None = None):
        self.binary = binary or find_binary()
        if enabled is None:
            enabled = (os.environ.get("CODEX_ENABLED", "false").lower() in ("1", "true", "yes"))
        self.enabled = enabled

    # ---- 零成本探测 ----
    async def probe(self) -> dict:
        """不发推理请求，只问二进制和登录态。"""
        if not self.binary:
            return {"installed": False, "version": None, "logged_in": None,
                    "enabled": self.enabled, "binary": None}
        version = await self._sh([self.binary, "--version"])
        status = await self._sh([self.binary, "login", "status"])
        logged = None
        low = (status or "").lower()
        if "not logged" in low or "未登录" in low:
            logged = False
        elif "logged in" in low or "@" in (status or ""):
            logged = True
        return {
            "installed": True,
            "version": (version or "").strip().splitlines()[0] if version else None,
            "logged_in": logged,
            "enabled": self.enabled,
            "binary": self.binary,
        }

    async def health(self) -> bool:
        """健康 = 装着 + 已启用。没启用就不算「可用」，避免被路由选中。"""
        if not self.enabled:
            return False
        p = await self.probe()
        return bool(p["installed"])

    # ---- 真实执行（未启用时立刻拒绝，不起子进程） ----
    async def run(
        self,
        prompt: str,
        on_content: Callable[[str], None],
        cwd: str | None = None,
        timeout: float = 600.0,
    ) -> dict:
        if not self.enabled:
            raise CodexDisabled(
                "Codex 已接入但未启用（CODEX_ENABLED=false）。"
                "当前代理无可用额度，调用会失败；如已恢复额度，"
                "请在 backend/.env 里设 CODEX_ENABLED=true 并重启后端。"
            )
        if not self.binary:
            raise RuntimeError("找不到 codex 可执行文件")

        args = [self.binary, "exec", "--json"]
        if cwd:
            args += ["-C", cwd]
        args.append(prompt)

        t0 = time.time()
        chars = 0
        first_ms: int | None = None
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = _extract_text(ev)
                if text:
                    if first_ms is None:
                        first_ms = int((time.time() - t0) * 1000)
                    chars += len(text)
                    on_content(text)
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        finally:
            if proc.returncode is None:
                proc.kill()
        if proc.returncode not in (0, None):
            err = (await proc.stderr.read()).decode("utf-8", "replace")[:300] if proc.stderr else ""
            raise RuntimeError(f"codex exec 退出码 {proc.returncode}: {err}")
        return {
            "chars": chars,
            "first_token_ms": first_ms,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "mode": "codex-exec-json",
        }

    @staticmethod
    async def _sh(cmd: list[str], timeout: float = 10.0) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return out.decode("utf-8", "replace")
        except Exception:
            return ""


def _extract_text(ev: dict) -> str:
    """从 codex --json 的事件里抠出正文增量。

    已知形状（据 --help 与社区用法，**未经真实运行验证**）：
      {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
    这里做宽容匹配：凡是带 text/content 的字符串字段都尝试取一次。
    """
    if not isinstance(ev, dict):
        return ""
    item = ev.get("item") if isinstance(ev.get("item"), dict) else None
    if item and isinstance(item.get("text"), str):
        return item["text"]
    for k in ("text", "delta", "content"):
        v = ev.get(k)
        if isinstance(v, str):
            return v
    return ""


if __name__ == "__main__":
    async def _main() -> None:
        a = CodexAdapter()
        print("probe:", json.dumps(await a.probe(), ensure_ascii=False, indent=1))
        print("health:", await a.health())
        try:
            await a.run("ping", lambda s: None)
        except CodexDisabled as e:
            print("run 拒绝:", e)

    asyncio.run(_main())
