"""WorkBuddy（codebuddy CLI）适配器 —— 已接入，当前不可用（未登录）。

实测结论（2026-10-26）：
- 二进制：<WorkBuddy 安装目录>/resources/app.asar.unpacked/cli/bin/codebuddy
- 不加 `--port 0` 会 `EADDRINUSE 127.0.0.1:65314`（它自己要占一个端口，和桌面端冲突）
- 输出：`-p --output-format stream-json`，逐行 JSON
    {"type":"system","subtype":"init", ... "model":"fast-model"}
    {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
    {"type":"result","subtype":"success"|"error_during_execution","is_error":bool,
     "usage":{...},"total_cost_usd":0,"duration_ms":442}
- **必须登录**：未登录时返回 `Authentication required. Please use /login`，
  即使指定 `--model custom-local:qwen3.5:9b` 也一样——鉴权在模型选择之前。
  本机实测 `logged_in=false`，所以这个适配器目前不会被派活。

登录方式：终端跑 `codebuddy` 进去敲 `/login`，登录后到 Agent 池点「检测」翻成可用。
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import time
from typing import Callable

# WorkBuddy 安装根目录候选（按常见安装位置）
_INSTALL_GLOBS = [
    r"D:\chatAI\AI agent\WorkBuddy",
    r"C:\Program Files\WorkBuddy",
    r"C:\Program Files (x86)\WorkBuddy",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\WorkBuddy"),
    os.path.expandvars(r"%LOCALAPPDATA%\WorkBuddy"),
]

AUTH_HINT = "Authentication required"


def find_binary() -> str | None:
    env = os.environ.get("WB_CLI_PATH")
    if env and os.path.isfile(env):
        return env

    # 项目内自带的完整版优先：WorkBuddy 安装目录里那套是 lite 构建，
    # dist/ 只剩 codebuddy-lite-wb.mjs，**没有 TUI bundle**，一进交互界面就
    # 报 Cannot find module '../dist/codebuddy'（实测）。项目内这套是完整 npm 包。
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(here, "..", "..", "tools", "node_modules",
                         "@tencent-ai", "codebuddy-code", "bin", "codebuddy")
    local = os.path.normpath(local)
    if os.path.isfile(local):
        return local

    which = shutil.which("codebuddy") or shutil.which("cbc")
    if which:
        return which

    rel = os.path.join("resources", "app.asar.unpacked", "cli", "bin", "codebuddy")
    for root in _INSTALL_GLOBS:
        for cand in (os.path.join(root, rel), os.path.join(root, "WorkBuddy", rel)):
            if os.path.isfile(cand):
                return cand
    for drive in ("C:", "D:", "E:"):
        for hit in glob.glob(os.path.join(drive + os.sep, "*", "*", "WorkBuddy", rel)):
            if os.path.isfile(hit):
                return hit
    return None


def find_node() -> str | None:
    """Windows 上 codebuddy 是 `#!/usr/bin/env node` 脚本，必须用 node 启动。"""
    n = shutil.which("node")
    if n:
        return n
    for hit in sorted(glob.glob(os.path.expanduser(
            "~/.workbuddy/binaries/node/versions/*/node.exe")), reverse=True):
        return hit
    for drive in ("C:", "D:", "E:"):
        for hit in glob.glob(os.path.join(drive + os.sep, "*", "*", "WorkBuddy",
                                          "resources", "node", "node.exe")):
            return hit
    return None


class WorkBuddyDisabled(RuntimeError):
    pass


class WorkBuddyAdapter:
    def __init__(self, binary: str | None = None, model: str | None = None,
                 tools: str | None = None):
        self.binary = binary or find_binary()
        self.model = model or os.environ.get("WB_MODEL", "fast-model")
        # 默认禁用全部工具：让它是"写手"而不是"操作者"。
        # 想放开就设 WB_TOOLS=default 或具体工具名，风险自负。
        self.tools = tools if tools is not None else os.environ.get("WB_TOOLS", "")
        # API Key 模式：绕过 OAuth。
        # 本机实测：独立运行的 CLI 因 at-rest 加密缺 key，**存不住登录态**
        # （日志 hasSession=true hasAccessToken=false），所以 OAuth 走不通，
        # 只能用 CODEBUDDY_API_KEY。子进程继承本进程环境，设了就会自动生效。
        self.api_key = bool(os.environ.get("CODEBUDDY_API_KEY"))

    def _base_cmd(self) -> list[str]:
        """脚本用 node 启动，exe 直接跑。"""
        if not self.binary:
            raise RuntimeError("找不到 codebuddy 可执行文件（可用 WB_CLI_PATH 指定）")
        if self.binary.lower().endswith((".exe", ".cmd", ".bat")):
            return [self.binary]
        node = find_node()
        if not node:
            raise RuntimeError(
                "codebuddy 是 Node 脚本，需要 node 才能启动；"
                "PATH 里没有 node，可用 WB_NODE 指定"
            )
        return [os.environ.get("WB_NODE") or node, self.binary]

    # ---- 探测（未登录时 0.4s 返回、0 token） ----
    async def probe(self, timeout: float = 60.0) -> dict:
        if not self.binary:
            return {"installed": False, "logged_in": None, "error": "找不到 codebuddy 可执行文件",
                    "binary": None, "model": self.model}
        t0 = time.time()
        code, out, err = await self._run_raw("ping", timeout=timeout)
        ms = int((time.time() - t0) * 1000)
        blob = (out + err)
        if AUTH_HINT in blob:
            if self.api_key:
                return {"installed": True, "logged_in": False,
                        "error": "CODEBUDDY_API_KEY 无效或已过期（CLI 以 ApiKey 身份发了请求，被 401 拒绝）",
                        "binary": self.binary, "model": self.model, "ms": ms,
                        "mode": "api-key"}
            return {"installed": True, "logged_in": False,
                    "error": "未登录，且未配置 CODEBUDDY_API_KEY（本机 OAuth 存不住凭据，见 README）",
                    "binary": self.binary, "model": self.model, "ms": ms,
                    "mode": "oauth"}
        if code != 0:
            return {"installed": True, "logged_in": None, "error": blob[-200:] or f"退出码 {code}",
                    "binary": self.binary, "model": self.model, "ms": ms}
        return {"installed": True, "logged_in": True, "error": None,
                "binary": self.binary, "model": self.model, "ms": ms,
                "mode": "api-key" if self.api_key else "oauth"}

    async def health(self) -> bool:
        p = await self.probe()
        return bool(p.get("installed") and p.get("logged_in"))

    # ---- 真跑 ----
    async def run(
        self,
        prompt: str,
        on_content: Callable[[str], None],
        cwd: str | None = None,
        timeout: float = 600.0,
    ) -> dict:
        if not self.binary:
            raise RuntimeError("找不到 codebuddy 可执行文件（可用 WB_CLI_PATH 指定）")

        args = self._base_cmd() + ["-p", "--port", "0", "--output-format", "stream-json"]
        if self.tools:
            args += ["--tools", self.tools]
        else:
            args += ["--tools", ""]
        if self.model:
            args += ["--model", self.model]
        args.append(prompt)

        t0 = time.time()
        first_ms: int | None = None
        chars = 0
        usage: dict = {}
        is_error = False
        errors: list[str] = []

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
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
                et = ev.get("type")
                if et == "assistant":
                    for part in (ev.get("message") or {}).get("content") or []:
                        if part.get("type") == "text" and part.get("text"):
                            txt = part["text"]
                            if AUTH_HINT in txt:
                                raise WorkBuddyDisabled(
                                    "WorkBuddy CLI 未登录：在终端运行 codebuddy 后执行 /login，"
                                    "然后在 Agent 池点「检测」。"
                                )
                            if first_ms is None:
                                first_ms = int((time.time() - t0) * 1000)
                            chars += len(txt)
                            on_content(txt)
                elif et == "result":
                    usage = ev.get("usage") or {}
                    is_error = bool(ev.get("is_error"))
                    errors = ev.get("errors") or []
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        finally:
            if proc.returncode is None:
                proc.kill()

        if is_error:
            raise RuntimeError("; ".join(errors)[:300] or f"codebuddy 退出码 {proc.returncode}")
        return {
            "chars": chars,
            "first_token_ms": first_ms,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "mode": "codebuddy-stream-json",
        }

    async def _run_raw(self, prompt: str, timeout: float) -> tuple[int, str, str]:
        args = self._base_cmd() + ["-p", "--port", "0", "--output-format", "stream-json",
                                   "--tools", "", "--model", self.model, prompt]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "", "timeout"
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


if __name__ == "__main__":
    async def _main() -> None:
        a = WorkBuddyAdapter()
        print("binary:", a.binary)
        print("probe:", json.dumps(await a.probe(), ensure_ascii=False, indent=1))
        try:
            await a.run("ping", lambda s: None)
        except Exception as e:
            print("run 拒绝:", type(e).__name__, e)

    asyncio.run(_main())
