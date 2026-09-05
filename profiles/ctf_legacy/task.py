"""CTF Legacy 默认任务书（R4 H12 收敛）。

原 app.main.build_default_task / TSEC_TASK_FILE 属 TSecBench 跑分 profile 专属：
读 prompts/tsec_task.txt 模板并把平台凭证占位符替换为实际值。随 CTF 假设收敛到
profiles/ctf_legacy/ 后，app/main.py 只做调度，不再直接读平台模板与凭证常量。
"""
from __future__ import annotations

from pathlib import Path

from adapters.config import BENCHMARK_BASE_URL, BENCHMARK_TOKEN

_PROFILE_DIR = Path(__file__).resolve().parent  # <repo>/profiles/ctf_legacy
TSEC_TASK_FILE = _PROFILE_DIR.parent.parent / "prompts" / "tsec_task.txt"


def build_default_task() -> str:
    """读跑分任务模板并替换占位符（模板独立在 prompts/tsec_task.txt）。"""
    token = BENCHMARK_TOKEN or "（未配置 BENCHMARK_TOKEN）"
    base_url = BENCHMARK_BASE_URL or "（未配置 BENCHMARK_BASE_URL）"
    return (TSEC_TASK_FILE.read_text(encoding="utf-8")
            .replace("{BENCHMARK_TOKEN}", token)
            .replace("{BENCHMARK_BASE_URL}", base_url))
