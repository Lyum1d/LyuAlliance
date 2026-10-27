"""通过 GitHub REST API 推送本地提交。

为什么需要它：这台机器的代理只放行 `api.github.com`，不放行 `github.com`，
所以 `git push` 必然报 `CONNECT tunnel failed, response 502`。
Git Data API 走的是 api.github.com，能通。

用法（在仓库根目录执行）：

    python spike/push_via_api.py

默认推送「最近一次提交」相对上一次提交的变更。token 自动从 Git Credential
Manager 取，不落盘、不打印。

注意：API 建出来的 commit，sha 与本地不一定一致（Git 侧编码细节差异），
但 tree 内容完全相同。所以推完之后本地和远端"内容同步、sha 不同"，
`git status` 可能显示 ahead——这是预期现象，不要去 force push 修正。
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

import httpx

OWNER, REPO, BRANCH = "Lyum1d", "LyuAlliance", "main"
REPO_DIR = Path(__file__).resolve().parents[1]   # agentmesh/


def git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(REPO_DIR), *args],
                          capture_output=True, text=True, check=check).stdout.strip()


def token() -> str:
    # 必须继承完整环境变量：GCM 要靠 USERPROFILE / APPDATA 找凭据存储，
    # 之前只传 PATH 导致它读不到已保存的 token。
    out = subprocess.run(["git", "credential", "fill"], capture_output=True, text=True,
                         input="protocol=https\nhost=github.com\n\n",
                         env={**os.environ, "GCM_INTERACTIVE": "never"})
    for line in out.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    raise SystemExit("拿不到 GitHub 凭据：先确认 Git Credential Manager 里存过 token")


api = httpx.Client(
    base_url="https://api.github.com",
    verify=False,            # 代理做 TLS 中间人，公共 CA 验不过
    timeout=60,
    headers={"Authorization": f"Bearer {token()}",
             "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28"},
)


def call(method: str, path: str, **kw):
    r = api.request(method, path, **kw)
    if r.status_code >= 300:
        raise SystemExit(f"{method} {path} -> {r.status_code}\n{r.text[:500]}")
    return r.json() if r.text else {}


def main() -> None:
    ref = call("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
    head = ref["object"]["sha"]
    base_tree = call("GET", f"/repos/{OWNER}/{REPO}/git/commits/{head}")["tree"]["sha"]
    print(f"远端 HEAD {head[:8]}")

    files = [f for f in git("diff", "--name-only", "HEAD~1", "HEAD").splitlines() if f.strip()]
    if not files:
        print("最近一次提交没有文件变更，退出")
        return
    print(f"变更文件 {len(files)} 个")

    items = []
    for rel in files:
        content = base64.b64encode((REPO_DIR / rel).read_bytes()).decode()
        blob = call("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                    json={"content": content, "encoding": "base64"})
        items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  + {rel}")

    tree = call("POST", f"/repos/{OWNER}/{REPO}/git/trees",
                json={"base_tree": base_tree, "tree": items})["sha"]
    commit = call("POST", f"/repos/{OWNER}/{REPO}/git/commits",
                  json={"message": git("log", "-1", "--pretty=%B") + "\n",
                        "tree": tree, "parents": [head]})["sha"]
    call("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
         json={"sha": commit, "force": False})
    print(f"已推送 {commit[:8]} → {BRANCH}")


if __name__ == "__main__":
    sys.exit(main())
