"""H4 看板 zero_gain_events 真实汇总单测（R2 验收：非硬编码 0）。

数据链：ExecutorLoop._post_step 累计 TaskContext.zero_gain_total →
write_cost_report 落盘 cost_report.json["zero_gain"]["total"] →
write_dashboard 聚合 worker_*/cost_report.json → dashboard.zero_gain_events。

运行：.venv/bin/python -m pytest tests/unit/test_dashboard_zero_gain.py -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.task_context import TaskContext
from runtime.reporting import write_cost_report, write_dashboard


def _ctx(workdir: Path, zero_gain_total: int, peak: int) -> TaskContext:
    ctx = TaskContext(workdir=workdir)
    ctx.zero_gain_total = zero_gain_total
    ctx.peak_zero_gain_streak = peak
    ctx.token_usage = {"input": 100, "output": 50, "total": 150,
                       "requests": 2, "cache_read": 80, "cache_write": 70}
    return ctx


class DashboardZeroGainTest(unittest.TestCase):
    def test_cost_report_records_zero_gain_stats(self):
        """每题 cost_report 落盘真实零增量统计（total + peak_streak）。"""
        with tempfile.TemporaryDirectory() as td:
            w = Path(td) / "worker_t1"
            w.mkdir(parents=True)
            write_cost_report(w, "t1", "stuck", _ctx(w, zero_gain_total=7,
                                                     peak=3),
                              death_reason="stuck_with_failed_paths")
            data = json.loads((w / "cost_report.json").read_text())
            self.assertEqual(data["zero_gain"]["total"], 7)
            self.assertEqual(data["zero_gain"]["peak_streak"], 3)

    def test_dashboard_zero_gain_events_is_real_aggregate(self):
        """看板 zero_gain_events = 各题真实 total 求和，非硬编码 0。"""
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            for code, total, peak in (("t1", 3, 3), ("t2", 5, 2), ("t3", 0, 0)):
                w = workdir / f"worker_{code}"
                w.mkdir()
                write_cost_report(w, code, "stuck", _ctx(w, total, peak))
            write_dashboard(workdir)
            data = json.loads((workdir / "dashboard.json").read_text())
            self.assertEqual(data["zero_gain_events"], 8)   # 3 + 5 + 0
            self.assertEqual(data["peak_zero_gain_streak"], 3)
            self.assertNotEqual(data["zero_gain_events"], 0)

    def test_dashboard_zero_gain_legacy_report_fallback_zero(self):
        """旧版 cost_report（无 zero_gain 字段）按 0 兜底，不抛错。"""
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            w = workdir / "worker_t1"
            w.mkdir()
            (w / "cost_report.json").write_text(json.dumps(
                {"code": "t1", "turns": 2, "outcome": "stuck"}), encoding="utf-8")
            write_dashboard(workdir)
            data = json.loads((workdir / "dashboard.json").read_text())
            self.assertEqual(data["zero_gain_events"], 0)
            self.assertEqual(data["peak_zero_gain_streak"], 0)


if __name__ == "__main__":
    unittest.main()
