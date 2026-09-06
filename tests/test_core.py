"""核心逻辑最小测试集（内置 unittest，无第三方依赖）。

覆盖本批修复的关键纯函数，防止回归：
- core.memory：黑板快照 / MemoryManager 读写
- core.tool_pipeline：增量打分 / 网络不可达（收敛到 hooks 后行为不变）

运行：.venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

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


class TestScoring(unittest.TestCase):
    """信息增量打分回归（D1/D2 验收）：hint 锁铁证前置 + 命中方向解锁 + 弱词收紧。

    从 hooks 独立 import 打分纯函数，可直接对历史工具输出回放。
    """

    def _ctx(self, hint_directive: str = "", lock: bool = False):
        from core.task_context import TaskContext
        ctx = TaskContext(workdir=Path("."))
        if hint_directive:
            ctx.blackboard["hint_directive"] = {"value": hint_directive}
        if lock:
            setattr(ctx, "hint_grace_active", True)
        return ctx

    def test_hint_lock_unlocks_on_direction_hit(self):
        # D1：锁定期内命中 hint 方向 → +1 且解锁
        from core.hooks import _score_tool_result
        ctx = self._ctx(hint_directive="尝试 JWT 伪造，检查 /api/token", lock=True)
        self.assertEqual(
            _score_tool_result("shell", "POST /api/token 返回 jwt 签名错误", ctx), 1)
        self.assertFalse(getattr(ctx, "hint_grace_active", False))  # 已转化，解锁

    def test_flag_ironclad_beats_hint_lock(self):
        # D1：铁证（flag{）在锁定期内也必须 +1（输出不含 hint 词）
        from core.hooks import _score_tool_result
        ctx = self._ctx(hint_directive="检查 /etc/passwd", lock=True)
        self.assertEqual(
            _score_tool_result("shell", "读取到 flag{test_flag_123} 提交", ctx), 1)

    def test_hint_lock_blocks_unrelated(self):
        from core.hooks import _score_tool_result
        ctx = self._ctx(hint_directive="尝试 JWT 伪造", lock=True)
        self.assertEqual(
            _score_tool_result("shell", "扫描了 100 个端口，全部关闭", ctx), 0)

    def test_session_eq_no_longer_ironclad(self):
        # D2：宽词 "session=" 不再是铁证
        from core.hooks import _score_tool_result
        ctx = self._ctx()
        self.assertEqual(
            _score_tool_result("http_request", "Set-Cookie: session=abc123", ctx), 0)

    def test_weak_hint_needs_error_context(self):
        # D2：弱词 admin/root 单独出现不算进展，与 error 同现才算
        from core.hooks import _score_tool_result
        ctx = self._ctx()
        self.assertEqual(
            _score_tool_result("shell", "<title>Admin Dashboard</title> 正常页面", ctx), 0)
        self.assertEqual(
            _score_tool_result("shell", "Error: Access denied for user 'admin'", ctx), 1)

    def test_strong_hint_counts(self):
        # D2：强信号（mysql 报错/SQLi 特征）命中即算增量
        from core.hooks import _score_tool_result
        ctx = self._ctx()
        self.assertEqual(
            _score_tool_result("shell", "MySQL syntax error near '1'", ctx), 1)




if __name__ == "__main__":
    unittest.main()
