"""在伪终端里驱动 codebuddy 的 /login，把授权链接捞出来。

为什么要伪终端：`/login` 是 TUI 里的斜杠命令，打印模式（-p）不执行斜杠命令，
直接返回 "Authentication required"。Windows 没有 pty 模块，所以用 pywinpty 起一个 ConPTY。
"""
from __future__ import annotations

import re
import sys
import time

from winpty import PtyProcess

CLI = r"D:\chatAI\AI agent\WorkBuddy\resources\app.asar.unpacked\cli\bin\codebuddy"
NODE = r"C:\Users\Lianaxber\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|[\x00-\x08\x0b\x0c\x0e-\x1f]")
URL = re.compile(r"https?://[^\s\"'<>)\]]+")

WAIT_LOGIN = float(sys.argv[1]) if len(sys.argv) > 1 else 70.0


def clean(s: str) -> str:
    return ANSI.sub("", s)


def main() -> None:
    proc = PtyProcess.spawn([NODE, CLI, "--port", "0"], dimensions=(40, 120))
    seen: list[str] = []
    t0 = time.time()
    sent_login = False
    next_send = t0 + 12

    try:
        while time.time() - t0 < WAIT_LOGIN:
            try:
                chunk = proc.read(4096)
            except EOFError:
                break
            except Exception:
                break
            if not chunk:
                time.sleep(0.3)
                continue
            text = clean(chunk)
            sys.stdout.write(text)
            sys.stdout.flush()
            for u in URL.findall(text):
                if u not in seen:
                    seen.append(u)
                    print(f"\n[URL] {u}\n", flush=True)

            # TUI 起来之后再发 /login
            if not sent_login and time.time() >= next_send:
                proc.write("/login\r")
                sent_login = True
                print("\n[已发送 /login]\n", flush=True)
    finally:
        try:
            proc.terminate(force=True)
        except Exception:
            pass

    print("\n" + "=" * 60)
    if seen:
        print("捕获到链接：")
        for u in seen:
            print("  " + u)
    else:
        print("没有捕获到链接（TUI 可能没渲染出来，或登录流程需要图形界面）")


if __name__ == "__main__":
    main()
