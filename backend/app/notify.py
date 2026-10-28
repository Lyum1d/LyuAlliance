"""余额告警的邮件通知，走 WorkBuddy 内置的 agent-mail（MCP over HTTP）。

为什么不用 SMTP：那要用户去邮箱后台申请授权码、填服务器和端口、分 SSL/TLS，
配置成本比这个功能本身还高。agent-mail 的端点和令牌已经在环境变量
`CODEBUDDY_MCP_CONFIG` 里，零配置就能发。

代价要写明白：**那个端口是 WorkBuddy 每次会话动态分配的**（今天 49991，明天可能换）。
后端启动时读一次，WorkBuddy 重启后旧端口失效 → 发不出去。
所以失败必须明确报出来，不能静默当成功，否则"以为通知了其实没发"更危险。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from . import appcfg

PROTO = "2025-03-26"


class NotifyUnavailable(RuntimeError):
    """发不出去，且不是用户配置问题（端点缺失 / 端口失效 / 服务拒绝）。"""


def endpoint() -> tuple[str, dict[str, str]] | None:
    raw = os.environ.get("CODEBUDDY_MCP_CONFIG")
    if not raw:
        return None
    try:
        cfg = json.loads(raw)
        am = (cfg.get("mcpServers") or {}).get("agent-mail") or {}
        url = am.get("url")
        if not url:
            return None
        headers = dict(am.get("headers") or {})
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json, text/event-stream"
        return url, headers
    except Exception:
        return None


def _rpc(url: str, headers: dict, method: str, params: Any = None, rid: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    r = httpx.post(url, headers=headers, json=body, timeout=httpx.Timeout(20, connect=5))
    text = r.text.strip()
    # agent-mail 的 send 会返回 202 + 空 body（接受即返回，不等投递结果）。
    # 早期这里强制走 r.json()，202 直接炸成"返回了非 JSON"，看起来像发失败，其实发出去了。
    if not text:
        return {"_http": r.status_code}
    try:
        return r.json()
    except Exception:
        raise NotifyUnavailable(
            f"agent-mail 返回了非 JSON（HTTP {r.status_code}）：{text[:160]}")


def _dig(obj: Any, key: str) -> Any:
    """在嵌套 dict/list 里找第一个 key。agent-mail 的返回层层包着 data.data，写死路径太脆。"""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            got = _dig(v, key)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _dig(v, key)
            if got is not None:
                return got
    return None


_ID: dict = {"at": 0.0, "v": None}


def identity(force: bool = False) -> str | None:
    """这个智能体邮箱自己的地址，用于界面上显示「从哪发出」。

    每次都要跑一遍 MCP 握手，所以缓存 5 分钟——地址本身不会变。
    """
    if not force and _ID["v"] and time.time() - _ID["at"] < 300:
        return _ID["v"]
    ep = endpoint()
    if not ep:
        return None
    url, h = ep
    try:
        _rpc(url, h, "initialize", {
            "protocolVersion": PROTO, "capabilities": {},
            "clientInfo": {"name": "agentmesh", "version": "0.1"}})
        d = _rpc(url, h, "tools/call", {"name": "GetMe", "arguments": {}}, 2)
        text = _dig(d, "text")
        if isinstance(text, str):
            data = json.loads(text)
            aliases = _dig(data, "aliases") or []
            found = None
            for a in aliases:
                if isinstance(a, dict) and a.get("is_primary"):
                    found = a.get("email")
                    break
            if not found and aliases and isinstance(aliases[0], dict):
                found = aliases[0].get("email")
            if found:
                _ID.update(at=time.time(), v=found)
            return found
    except Exception:
        return None
    return None


def send(subject: str, body: str, to: str | None = None) -> dict:
    """发一封纯文本邮件。收件人默认取配置里的。"""
    dest = (to or appcfg.mail_to()).strip()
    if not dest:
        raise NotifyUnavailable("还没填收件人邮箱")
    ep = endpoint()
    if not ep:
        raise NotifyUnavailable("读不到 agent-mail 端点（CODEBUDDY_MCP_CONFIG 缺失）")
    url, h = ep

    _rpc(url, h, "initialize", {
        "protocolVersion": PROTO, "capabilities": {},
        "clientInfo": {"name": "agentmesh", "version": "0.1"}})
    _rpc(url, h, "notifications/initialized", None, 2)
    d = _rpc(url, h, "tools/call", {
        "name": "SendMessage",
        "arguments": {
            "to": [{"email": dest}],
            "subject": subject[:200],
            "body": body,
            "body_format": "PLAIN",
            # 用户已在界面上明确打开告警开关，这里不再逐封弹确认
            "skip_confirmation": True,
        },
    }, 3)

    if "_http" in d:
        if d["_http"] in (200, 202):
            return {"ok": True, "to": dest,
                    "detail": f"已提交（HTTP {d['_http']}，服务端异步投递）"}
        raise NotifyUnavailable(f"agent-mail 返回 HTTP {d['_http']}")

    err = d.get("error")
    if err:
        raise NotifyUnavailable(f"agent-mail 拒绝：{err.get('message') or err}")
    res = d.get("result") or {}
    if res.get("isError"):
        msg = _dig(res, "text") or str(res)[:160]
        raise NotifyUnavailable(f"发送失败：{msg}")
    return {"ok": True, "to": dest, "detail": str(_dig(res, "text") or "")[:200]}
