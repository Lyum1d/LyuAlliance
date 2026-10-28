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
K_GRAN = "conductor.granularity"
K_EXTRA = "conductor.extra_prompt"
K_MAIL_TO = "notify.email_to"
K_MAIL_ON = "notify.email_enabled"

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"

# 拆解粒度。默认 medium 的取值范围和措辞与改造前完全一致，
# 所以「没动过这个设置」时，拆出来的东西和以前一模一样。
GRANULARITY: dict[str, dict] = {
    "coarse": {
        "lo": 3, "hi": 5, "label": "粗",
        "hint": "只抓主干，能合并的合并",
        "rule": "节点数 3~5 个。只抓主干，能合并的合并，宁可少不要多。",
    },
    "medium": {
        "lo": 5, "hi": 12, "label": "中",
        "hint": "默认，与原行为一致",
        # 这一句必须与改造前 SYSTEM 里的第 4 条逐字相同
        "rule": "节点数 5~12 个。能用 5 个解决就不要拆 12 个。",
    },
    "fine": {
        "lo": 8, "hi": 14, "label": "细",
        "hint": "每个可独立验收的动作单独成节点",
        "rule": "节点数 8~14 个。每个可独立验收的动作单独成节点，便于并行和定点返工。",
    },
}
DEFAULT_GRAN = "medium"
EXTRA_LIMIT = 800


def granularity() -> tuple[str, dict]:
    """当前粒度：返回 (key, {lo,hi,label,hint})。库里存了非法值就回默认。"""
    g = _db(K_GRAN)
    if g not in GRANULARITY:
        g = DEFAULT_GRAN
    return g, GRANULARITY[g]


def extra_prompt() -> str:
    return _db(K_EXTRA)[:EXTRA_LIMIT]


def mail_to() -> str:
    """余额告警的收件人。空表示没配。"""
    return _db(K_MAIL_TO).strip()[:120]


def mail_enabled() -> bool:
    """默认关：发邮件是外部动作，必须用户明确打开。"""
    return _db(K_MAIL_ON) == "1"


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
        "granularity": granularity()[0],
        "extra_prompt": extra_prompt(),
    }


def public() -> dict:
    """给前端的视图：key 只给掩码，并标出每项的来源。"""
    c = effective()
    db_key = _db(K_KEY)
    g, gdef = granularity()
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
        "granularity": g,
        "granularity_label": gdef["label"],
        "granularity_hint": gdef["hint"],
        "extra_prompt": c["extra_prompt"],
        "granularity_source": "db" if _db(K_GRAN) else "默认",
        "notify_email": mail_to(),
        "notify_enabled": mail_enabled(),
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
    if "granularity" in body:
        v = str(body.get("granularity") or "").strip()
        if v in GRANULARITY:
            store.config_set(K_GRAN, v)
        else:
            store.config_del(K_GRAN)      # 空值或非法值都视为「回默认」
    if "notify_email" in body:
        v = str(body.get("notify_email") or "").strip()[:120]
        if v:
            store.config_set(K_MAIL_TO, v)
        else:
            store.config_del(K_MAIL_TO)
    if "notify_enabled" in body:
        if body.get("notify_enabled"):
            store.config_set(K_MAIL_ON, "1")
        else:
            store.config_del(K_MAIL_ON)
    if "extra_prompt" in body:
        v = str(body.get("extra_prompt") or "").strip()[:EXTRA_LIMIT]
        if v:
            store.config_set(K_EXTRA, v)
        else:
            store.config_del(K_EXTRA)
    return public()
