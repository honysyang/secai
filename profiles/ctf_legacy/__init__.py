"""ctf_legacy profile：TSecBench 跑分（CTF）链路的既有假设收敛包（R4 H12）。

原散布在 demo_tools→tools/domains/platform.py、bench_platform/platform_tools.py、
app/main.py 的 CTF 专属代码收口到本包：
- platform.py —— flag 机械提交铁律（_submit_flags_if_any）、通关机械复核（_is_completed）、
  finalize 终端动作与默认管线绑定（_late_bind_submit）；平台客户端一律经
  bench_platform.platform_client.get_platform_client() 单例获取，不再直接读 BENCHMARK_*。
- task.py —— 跑分默认任务书（prompts/tsec_task.txt 占位符替换）。
"""
from __future__ import annotations

from profiles.ctf_legacy.platform import (
    _is_completed,
    _late_bind_submit,
    _submit_flags_if_any,
    finalize,
)
from profiles.ctf_legacy.task import TSEC_TASK_FILE, build_default_task

__all__ = [
    "_is_completed",
    "_late_bind_submit",
    "_submit_flags_if_any",
    "finalize",
    "TSEC_TASK_FILE",
    "build_default_task",
]
