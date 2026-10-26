"""只读探针：检查桌面 App 的 UIA 树是否可被程序读取。
不点击、不输入、不改任何状态。
用法: python probe_uia.py [进程名...]
"""
import sys
import psutil
import uiautomation as auto

TARGETS = sys.argv[1:] or ["Doubao", "DeepSeek Harness", "Trae", "WorkBuddy"]
MAX_DEPTH = 6
MAX_NODES = 4000


def pids_of(name: str) -> tuple[set[int], str]:
    pids, exe = set(), ""
    for p in psutil.process_iter(["name", "exe"]):
        try:
            if (p.info["name"] or "").lower().startswith(name.lower()):
                pids.add(p.pid)
                exe = exe or (p.info["exe"] or "")
        except Exception:
            pass
    return pids, exe


def walk(ctrl, depth, out, stats):
    if depth > MAX_DEPTH or stats["n"] >= MAX_NODES:
        return
    for ch in ctrl.GetChildren():
        stats["n"] += 1
        try:
            ctype = ch.ControlTypeName.replace("ControlType.", "")
            name = (ch.Name or "").strip()
            aid = ch.AutomationId or ""
        except Exception:
            continue
        stats["types"][ctype] = stats["types"].get(ctype, 0) + 1
        if name or aid:
            out.append(("  " * depth) + f"[{ctype}] {name[:60]!r} id={aid!r}")
        if ctype in ("EditControl", "DocumentControl") and name:
            stats["text_fields"].append(name[:120])
        if ctype == "ButtonControl" and name:
            stats["buttons"].append(name[:40])
        walk(ch, depth + 1, out, stats)


def main():
    auto.SetGlobalSearchTimeout(3)
    root = auto.GetRootControl()
    for target in TARGETS:
        pids, exe = pids_of(target)
        print(f"\n{'='*70}\n[{target}] 进程数={len(pids)} exe={exe}")
        if not pids:
            print("  未运行")
            continue
        found = False
        for w in root.GetChildren():
            try:
                if w.ProcessId not in pids:
                    continue
            except Exception:
                continue
            found = True
            print(f"  窗口: {w.Name!r} class={w.ClassName!r} pid={w.ProcessId}")
            out, stats = [], {"n": 0, "types": {}, "text_fields": [], "buttons": []}
            walk(w, 1, out, stats)
            print(f"  遍历节点={stats['n']}  控件类型分布={stats['types']}")
            print("  --- 可见控件(前 60) ---")
            for line in out[:60]:
                print("   " + line)
            if stats["text_fields"]:
                print(f"  >>> 可读文本框 {len(stats['text_fields'])} 个: {stats['text_fields'][:5]}")
            if stats["buttons"]:
                print(f"  >>> 按钮: {stats['buttons'][:12]}")
        if not found:
            print("  UIA 中找不到该进程的顶层窗口(可能最小化到托盘)")


if __name__ == "__main__":
    main()
