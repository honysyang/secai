"""核心逻辑最小测试集（内置 unittest，无第三方依赖）。

覆盖本批修复的关键纯函数，防止回归：
- core.memory：黑板快照 / MemoryManager 读写
- runtime.fork_analyst：破局指令质量门
- core.tool_pipeline：增量打分 / 网络不可达（收敛到 hooks 后行为不变）
- runtime.reporting：成本报告 / 轨迹 / 看板落盘

运行：.venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from core.task_context import TaskContext, SubtaskBudget


class TestMemory(unittest.TestCase):
    def setUp(self) -> None:
        from core.memory import MemoryManager, render_blackboard_snapshot
        self.ctx = TaskContext(workdir=Path("."))
        self.ctx.blackboard["lfi_confirmed"] = {
            "value": "true", "status": "confirmed", "ts": 0, "verified": True}
        self.mm = MemoryManager(self.ctx)
        self.snapshot_fn = render_blackboard_snapshot

    def test_snapshot_keeps_confirmed(self):
        snap = self.snapshot_fn(self.ctx)
        self.assertIn("lfi_confirmed", snap)

    def test_snapshot_skips_unverified(self):
        self.ctx.blackboard["junk"] = {"value": "x", "status": "confirmed",
                                       "ts": 0, "verified": False}
        snap = self.snapshot_fn(self.ctx)
        self.assertNotIn("junk", snap)

    def test_set_get_blackboard(self):
        self.mm.set_blackboard("rce_confirmed", "whoami=root", evidence="cmdi")
        self.assertEqual(self.mm.get_blackboard("rce_confirmed"), "whoami=root")
        self.assertEqual(self.ctx.blackboard["rce_confirmed"]["evidence"], "cmdi")

    def test_mark_dead_end(self):
        self.mm.mark_dead_end("path_a", "404 all")
        self.assertEqual(self.ctx.blackboard["path_a"]["status"], "failed")

    def test_confirm_only(self):
        self.ctx.blackboard["failed_x"] = {"value": "y", "status": "failed",
                                           "ts": 0, "verified": True}
        confirmed = self.mm.confirmed_blackboard()
        self.assertIn("lfi_confirmed", confirmed)
        self.assertNotIn("failed_x", confirmed)

    def test_subtask_budget(self):
        b = SubtaskBudget(objective="验证SQLi", max_turns=8)
        self.assertTrue(b.objective)
        self.assertEqual(b.max_turns, 8)


class TestForkAnalystQualityGate(unittest.TestCase):
    def test_short_directive_degrades(self):
        from runtime.fork_analyst import update_blackboard_with_fork
        bb = {}
        directive = update_blackboard_with_fork(bb, {"next_directive": "继续", "directions": []})
        # 过短 → 降级为通用指令
        self.assertNotEqual(directive, "继续")
        self.assertGreater(len(directive), 12)

    def test_action_directive_passes(self):
        from runtime.fork_analyst import update_blackboard_with_fork
        bb = {}
        directive = update_blackboard_with_fork(
            bb, {"next_directive": "用 curl 访问 /admin/api/flag 验证端点", "directions": []})
        self.assertIn("curl", directive.lower())
        self.assertIn("next_directive", bb)


class TestToolPipeline(unittest.TestCase):
    def test_score_flag_is_positive(self):
        from core.tool_pipeline import _score_tool_result
        ctx = TaskContext(workdir=Path("."))
        self.assertEqual(_score_tool_result("shell", "found flag{abc123}", ctx), 1)

    def test_score_noise_is_zero(self):
        from core.tool_pipeline import _score_tool_result
        ctx = TaskContext(workdir=Path("."))
        self.assertEqual(_score_tool_result("think", "思考中，无输出", ctx), 0)

    def test_network_unreachable(self):
        from core.tool_pipeline import _is_network_unreachable
        self.assertTrue(_is_network_unreachable("curl: connection refused"))
        self.assertFalse(_is_network_unreachable("HTTP 200 OK"))

    def test_ledger_signature_normalizes_noise(self):
        from core.tool_pipeline import _ledger_signature
        sig1 = _ledger_signature("shell", {"cmd": "curl -H 'X-T: 1234567890abcdef' /flag"})
        sig2 = _ledger_signature("shell", {"cmd": "curl -H 'X-T: 2234567890abcdef' /flag"})
        self.assertEqual(sig1, sig2)  # 随机 hex 归一化


class TestReporting(unittest.TestCase):
    def test_write_cost_report(self):
        from runtime.reporting import write_cost_report
        from core.task_context import TaskContext
        with __import__("tempfile").TemporaryDirectory() as td:
            workdir = Path(td)
            ctx = TaskContext(workdir=workdir)
            ctx.token_usage = {"input": 100, "output": 50, "total": 150,
                               "requests": 2, "cache_read": 80, "cache_write": 70}
            write_cost_report(workdir, "t1", "solved", ctx, death_reason="solved")
            data = json.loads((workdir / "cost_report.json").read_text())
            self.assertEqual(data["code"], "t1")
            self.assertAlmostEqual(data["cache"]["hit_rate"], 80 / 150, places=4)
            self.assertEqual(data["death_reason"], "solved")

    def test_write_dashboard(self):
        from runtime.reporting import write_cost_report, write_dashboard
        from core.task_context import TaskContext
        with __import__("tempfile").TemporaryDirectory() as td:
            workdir = Path(td)
            w = workdir / "worker_t1"
            w.mkdir()
            ctx = TaskContext(workdir=w)
            ctx.token_usage = {"input": 100, "output": 50, "total": 150,
                               "requests": 2, "cache_read": 80, "cache_write": 70}
            write_cost_report(w, "t1", "solved", ctx, death_reason="solved")
            write_dashboard(workdir)
            data = json.loads((workdir / "dashboard.json").read_text())
            self.assertEqual(data["challenge_count"], 1)
            self.assertEqual(data["solved_count"], 1)
            self.assertAlmostEqual(data["cache"]["hit_rate"], 80 / 150, places=4)


if __name__ == "__main__":
    unittest.main()
