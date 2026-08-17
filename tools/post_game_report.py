#!/usr/bin/env python3
"""赛后量化分析脚本。

用法：
    python3 tools/post_game_report.py [--workdir data/worker_generic] [--top-n 10]

输出：
- death_reason 分布（六种死法占比）
- 每类死因的平均耗时 / 回合数 / token
- 子任务利用率
- exploit 阶段是否调用过 payload 脚本库
- 推荐下一步优化方向
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional


def load_results(workdir: Path) -> List[dict]:
    """尝试读取 results.json；没有则从 events.jsonl 里重建。"""
    p = workdir / "results.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    events = workdir / "events.jsonl"
    if not events.exists():
        return []
    results = []
    for line in events.read_text(encoding="utf-8").strip().splitlines():
        try:
            e = json.loads(line)
            if e.get("event") == "task_finished":
                results.append({"code": e.get("code"), "outcome": e.get("outcome")})
        except Exception:
            continue
    return results


def load_death_reasons(workdir: Path) -> Dict[str, str]:
    """从 events.jsonl 里抓取 [death] 日志。"""
    reasons = {}
    events = workdir / "events.jsonl"
    if not events.exists():
        return reasons
    for line in events.read_text(encoding="utf-8").strip().splitlines():
        try:
            e = json.loads(line)
            msg = e.get("message", "")
            m = re.search(r"\[death\] 单题 (\S+) 终态=\S+ 死因=(\S+)", msg)
            if m:
                reasons[m.group(1)] = m.group(2)
        except Exception:
            continue
    return reasons


def load_tool_usage(workdir: Path) -> Counter:
    """统计所有工具调用次数。"""
    c = Counter()
    events = workdir / "events.jsonl"
    if not events.exists():
        return c
    for line in events.read_text(encoding="utf-8").strip().splitlines():
        try:
            e = json.loads(line)
            if e.get("event") == "tool_call":
                c[e.get("tool", "unknown")] += 1
        except Exception:
            continue
    return c


def load_payload_script_calls(workdir: Path) -> Counter:
    """统计 arsenal/payloads/*.py 被调用次数。"""
    c = Counter()
    events = workdir / "events.jsonl"
    if not events.exists():
        return c
    for line in events.read_text(encoding="utf-8").strip().splitlines():
        try:
            e = json.loads(line)
            msg = e.get("message", "")
            m = re.search(r"(arsenal/payloads/[\w_]+\.py)", msg)
            if m:
                c[m.group(1)] += 1
        except Exception:
            continue
    return c


def load_db_events(workdir: Path, limit: int = 5000):
    """从 SQLite 事件库读最近 N 条工具调用。"""
    db_path = workdir / "events.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT ts, level, source, message FROM events ORDER BY ts DESC LIMIT ?",
            (limit,))
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(description="赛后量化分析")
    parser.add_argument("--workdir", default="data/worker_generic", type=Path)
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()

    workdir = args.workdir
    results = load_results(workdir)
    reasons = load_death_reasons(workdir)
    tools = load_tool_usage(workdir)
    payloads = load_payload_script_calls(workdir)

    solved = sum(1 for r in results if r.get("outcome") == "solved")
    stuck = sum(1 for r in results if r.get("outcome") in ("stuck", "suspended"))
    total = len(results)

    print("===== SecAI 赛后量化报告 =====")
    print(f"工作目录：{workdir}")
    print(f"总题数：{total}  解决：{solved}  卡死：{stuck}  其他：{total - solved - stuck}")
    if total:
        print(f"解决率：{solved / total * 100:.1f}%  卡死率：{stuck / total * 100:.1f}%")

    print("\n--- 六种死法分布 ---")
    reason_counts = Counter(reasons.values())
    for reason, cnt in reason_counts.most_common():
        print(f"  {reason}: {cnt} 题 ({cnt / max(total, 1) * 100:.1f}%)")

    print("\n--- 工具调用 Top 10 ---")
    for tool, cnt in tools.most_common(args.top_n):
        print(f"  {tool}: {cnt}")

    print("\n--- payload 脚本库调用 ---")
    if payloads:
        for script, cnt in payloads.most_common():
            print(f"  {script}: {cnt}")
    else:
        print("  （未检测到 arsenal/payloads/ 脚本调用）")

    # 诊断建议
    print("\n--- 下轮优化建议 ---")
    top_reason = reason_counts.most_common(1)
    if top_reason:
        reason, cnt = top_reason[0]
        if reason == "evidence_exhausted_no_direction":
            print(f"· 有 {cnt} 题死于「证据枯竭无方向」，建议补强技能库 / 子任务分支类型覆盖 / 增加第一性原理提示。")
        elif reason == "wrong_submit_fuse":
            print(f"· 有 {cnt} 题死于「连续错交」，建议检查 flag 提交前验证 / 多面题 flag 计数逻辑。")
        elif reason == "wallclock_timeout":
            print(f"· 有 {cnt} 题死于「单题超时」，建议收紧墙钟预算或提升 exploit 阶段效率。")
        elif reason == "hint_stale":
            print(f"· 有 {cnt} 题死于「hint 后无转化」，建议加强 hint 方向锁 / 提示词闭环化。")
        elif reason == "empty_idle":
            print(f"· 有 {cnt} 题死于「空转无工具调用」，建议检查工具门控 / 系统提示是否过度限制。")
        else:
            print(f"· 主要死因 {reason} 出现 {cnt} 次，建议针对性分析。")
    if not payloads:
        print("· 未调用 payload 脚本库，说明漏洞利用仍靠临时拼凑；检查技能触发器 / 闭环指令是否生效。")


if __name__ == "__main__":
    main()
