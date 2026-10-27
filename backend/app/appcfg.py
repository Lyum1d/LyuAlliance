"""主脑（云端模型）配置：数据库 > .env > 内置默认。

为什么要有这一层：用户要能在前端改 key / 模型，而不是改 .env 再重启。
所以运行时随时可改、立即生效；`.env` 保留为兜底——界面上没配过就还按老配置走。

安全边界：
- 存库时 key 用 DPAPI 加密（`secretbox`），绑定当前 Windows 账号
- **对外的任何接口只返回掩码**（`public()`），明文只在这个模块和 llm 客户端之间流转
"""
from __future__ import annotations

from . import secretbox, store
from .config import env, env_float

K_BASE = "conductor.base_url"
K_MODEL = "conductor.model"
K_KEY = "conductor.api_key"

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


def _db(key: str) -> str:
    v = store.config_pairs().get(key)
    return (v[0] if v else "") or ""


def effective() -> dict:
    """后端内部使用的明文配置。不要把这个 dict 直接返回给前端。"""
    base = _db(K_BASE) or env("CLOUD_BASE_URL", DEFAULT_BASE)
    model = _db(K_MODEL) or env("CONDUCTOR_MODEL", DEFAULT_MODEL)

    raw_key = _db(K_KEY)
    if raw_key:
        try:
            api_key = secretbox.decrypt(raw_key)
        except Exception:
            api_key = ""      # 解不开就当作没配（例如换了 Windows 账号 / 密文来自别的机器）
    else:
        api_key = env("CLOUD_API_KEY")

    return {
        "base_url": base,
        "model": model,
        "api_key": api_key,
        "budget_tokens": int(env_float("CONDUCTOR_BUDGET_TOKENS", 200_000)),
        "price_in": env_float("CLOUD_PRICE_IN", 0.5),
        "price_out": env_float("CLOUD_PRICE_OUT", 1.5),
    }


def public() -> dict:
    """给前端的视图：key 只给掩码，并标出每项的来源。"""
    c = effective()
    db_key = _db(K_KEY)
    return {
        "base_url": c["base_url"],
        "model": c["model"],
        "key_masked": secretbox.mask(c["api_key"]),
        "key_set": bool(c["api_key"]),
        "base_source": "db" if _db(K_BASE) else ".env",
        "model_source": "db" if _db(K_MODEL) else ".env",
        "key_source": "db" if db_key else ("env" if c["api_key"] else "none"),
        "encryption": secretbox.scheme(),
        "budget_tokens": c["budget_tokens"],
    }


def update(body: dict) -> dict:
    """改配置。

    - `api_key` 缺省 -> 不动；`""` -> 清除（回退到 .env）；其它 -> 加密后存
    - `base_url` / `model` 同理，空串表示清除
    """
    if "api_key" in body:
        v = str(body.get("api_key") or "").strip()
        if v:
            store.config_set(K_KEY, secretbox.encrypt(v), secret=True)
        else:
            store.config_del(K_KEY)
    for fld, store_key in (("base_url", K_BASE), ("model", K_MODEL)):
        if fld in body:
            v = str(body.get(fld) or "").strip()
            if v:
                store.config_set(store_key, v, secret=False)
            else:
                store.config_del(store_key)
    return public()
