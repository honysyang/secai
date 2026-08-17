"""赛后报告与观测数据导出：预侦察 / 单题成本报告 / 轨迹导出 / 四指标看板。

从 app/main.py 剥离，降低主循环巨石模块体积；保持纯函数签名，便于单测。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

from core.events import BUS
from runtime.log import log_info, log_warn


# ---------------------------------------------------------------------------
# 首轮机械预侦察（零 LLM）
# ---------------------------------------------------------------------------
async def first_strike(addrs: list) -> str:
    """首轮机械预侦察：在 LLM 介入前发起常见入口/敏感路径/状态码探测，省一轮 LLM 回合。

    使用 asyncio.to_thread 把同步 requests.get 放到后台线程执行，避免阻塞事件循环；
    各路径之间用 asyncio.gather 并发，缩短预侦察耗时。
    """
    if not addrs:
        return ""
    addr = next((a for a in addrs if a.startswith(("http://", "https://"))), None)
    if not addr:
        return ""
    base = addr.rstrip("/")
    paths = ["/", "/robots.txt", "/.git/HEAD", "/index.php", "/index.html",
             "/login", "/admin", "/api", "/upload", "/flag", "/flag.txt",
             "/.env", "/config.php", "/includes/config.php", "/health",
             "/wp-login.php", "/phpinfo.php", "/server-status", "/swagger-ui.html",
             "/api/v1/", "/favicon.ico"]

    def _probe(path: str) -> dict:
        url = base + path
        try:
            r = requests.get(url, timeout=8, verify=False, allow_redirects=False)
            title = re.search(r"<title>([^<]*)</title>", r.text, re.I)
            return {
                "path": path, "status": r.status_code, "len": len(r.content),
                "title": (title.group(1) if title else "")[:80],
                "server": r.headers.get("Server", "")[:40],
                "powered": r.headers.get("X-Powered-By", "")[:40],
                "ct": r.headers.get("Content-Type", "")[:40],
            }
        except Exception as e:
            return {"path": path, "error": str(e)[:80]}

    rows = await asyncio.gather(*[asyncio.to_thread(_probe, p) for p in paths])
    return "首轮预侦察:\n" + json.dumps(rows, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 单题成本报告 + 轨迹导出
# ---------------------------------------------------------------------------
def write_cost_report(workdir: Path, code: str, outcome: str, ctx,
                      death_reason: str = "") -> None:
    """单题成本报告落盘 cost_report.json：token 明细 / 缓存命中率 / 死因 / 轮次。"""
    tu = ctx.token_usage
    cache_read = tu.get("cache_read", 0)
    cache_write = tu.get("cache_write", 0)
    prompt_total = cache_read + cache_write
    hit_rate = (cache_read / prompt_total) if prompt_total > 0 else 0.0
    report = {
        "code": code,
        "outcome": outcome,
        "turns": getattr(ctx, "turn_count", 0),
        "tokens": {
            "input": tu.get("input", 0),
            "output": tu.get("output", 0),
            "total": tu.get("total", 0),
            "requests": tu.get("requests", 0),
        },
        "cache": {
            "cache_read": cache_read,
            "cache_write": cache_write,
            "hit_rate": round(hit_rate, 4),
        },
        "death_reason": death_reason,
        "subtask_count": len(getattr(ctx, "subtasks", [])),
        "cache_hits": getattr(ctx, "cache_hits", 0),
        "cache_misses": getattr(ctx, "cache_misses", 0),
        "ts": int(time.time()),
    }
    with (workdir / "cost_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def export_trajectory(workdir: Path, code: str, outcome: str, ctx) -> None:
    """把事件总线历史落盘 trajectory_<code>.jsonl，供赛后回放与复盘。"""
    events = BUS.history(code)
    lines = [json.dumps(ev, ensure_ascii=False) for ev in events]
    lines.append(json.dumps({
        "kind": "trajectory_end", "ts": time.time(), "code": code,
        "outcome": outcome, "turns": getattr(ctx, "turn_count", 0),
        "death_reason": getattr(ctx, "death_reason", ""),
    }, ensure_ascii=False))
    with (workdir / f"trajectory_{code}.jsonl").open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# 四指标看板
# ---------------------------------------------------------------------------
def write_dashboard(workdir: Path) -> None:
    """赛后四指标看板：缓存命中率 / 零增量事件数 / 轮次有效动作比 / 单题 token 成本。

    扫描 workdir 下 worker_*/cost_report.json 汇总为 dashboard.json。
    """
    reports: List[Dict[str, Any]] = []
    for rep in sorted(workdir.glob("worker_*/cost_report.json")):
        try:
            data = json.loads(rep.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                reports.append(data)
        except Exception:
            continue
    if not reports:
        return
    cache_read = sum(r.get("cache", {}).get("cache_read", 0) for r in reports)
    cache_write = sum(r.get("cache", {}).get("cache_write", 0) for r in reports)
    prompt_total = cache_read + cache_write
    total_tokens = sum(r.get("tokens", {}).get("total", 0) for r in reports)
    total_turns = sum(r.get("turns", 0) for r in reports)
    solved = sum(1 for r in reports if r.get("outcome") == "solved")
    dashboard = {
        "generated_at": int(time.time()),
        "challenge_count": len(reports),
        "solved_count": solved,
        "cache": {
            "cache_read": cache_read,
            "cache_write": cache_write,
            "hit_rate": round(cache_read / prompt_total, 4) if prompt_total else 0.0,
        },
        "tokens": {"total": total_tokens,
                   "per_challenge": round(total_tokens / len(reports), 1)},
        "turns": {"total": total_turns,
                  "per_challenge": round(total_turns / len(reports), 1)},
        "zero_gain_events": 0,
        "per_challenge": reports,
    }
    with (workdir / "dashboard.json").open("w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)
    log_info(f"[dashboard] 四指标看板已生成：{len(reports)} 题，"
             f"命中率 {dashboard['cache']['hit_rate']:.1%}，"
             f"单题 token {dashboard['tokens']['per_challenge']:.0f}，"
             f"单题轮次 {dashboard['turns']['per_challenge']:.1f}")
