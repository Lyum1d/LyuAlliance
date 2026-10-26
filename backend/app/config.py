"""零依赖配置加载：读 backend/.env。

不用 python-dotenv，是因为单机项目没必要多一个依赖；解析规则也只需要
`KEY=VALUE` 这一种（够覆盖现有 .env）。首次调用会把值写进 os.environ，
已存在的真实环境变量优先（便于临时覆盖，比如 CODEX_ENABLED=true uvicorn ...）。
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_loaded = False


def load_dotenv(path: Path | None = None) -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    p = path or _ENV_PATH
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def env(key: str, default: str = "") -> str:
    load_dotenv()
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


def env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(env(key, str(default)))
    except ValueError:
        return default


load_dotenv()
