"""H7 破局链·惰性点 ±5 步回放单测（R2 验收：惰性点自动导出回放）。

覆盖：
1. write_stuck_replay 纯函数：事件窗口以锚点 seq 为圆心取 ±5 步；
2. ExecutorLoop._mark_stuck_anchor：零增益达阈值登记惰性点并立即落盘回放；
3. _finish 收尾补全：惰性点后实际发生的后 5 步纳入窗口（覆盖导出）。

运行：.venv/bin/python -m pytest tests/unit/runner/test_stuck_replay.py -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.task_context import TaskContext
from harness.runner.executor import ExecutorLoop
from harness.runner.state import RunnerState
from runtime.reporting import write_stuck_replay


class _FakeBus:
    """事件总线替身：可变事件列表（模拟触发后再发生 5 步事件）。"""

    def __init__(self, events=None):
        self.events = list(events or [])

    def history(self, task_id: str):
        return list(self.events)


def _events(n: int) -> list:
    return [{"seq": i, "ts": 1.0, "task_id": "t1", "kind": "tool",
             "data": {"n": i}} for i in range(1, n + 1)]


class _FakeModel:
    model = "main"


class _FakeExecutor:
    def __init__(self):
        self.model = _FakeModel()
        self.model_settings = type("S", (), {"parallel_tool_calls": False})()
        self.tools = []


class _FakePool:
    has_alternative = False

    @property
    def current(self):
        return type("E", (), {"name": "main", "model": _FakeModel()})()

    def next(self, **kw):
        return None

    def mark_failed(self, *a, **kw):
        pass

    def switch_to_role(self, role):
        return None


class _FakeSession:
    def close(self):
        pass


class StuckReplayWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.challenge_dir = self.tmp / "challenge"
        self.sessions_dir = self.tmp / "sessions"
        self.challenge_dir.mkdir(parents=True)
        self.sessions_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_write_stuck_replay_window_is_anchor_plus_minus_5(self):
        """窗口以锚点 seq 为圆心：seq=10 ±5 → 事件 6..15 共 10 条。"""
        evs = _events(20)
        anchor = {"turn": 7, "reason": "zero_gain_stall", "seq": 10}
        path = write_stuck_replay(self.challenge_dir, "t1", anchor, events=evs)
        self.assertIsNotNone(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0])
        self.assertEqual(meta["anchor_seq"], 10)
        self.assertEqual(meta["window_events"], 10)
        window = [json.loads(ln)["seq"] for ln in lines[1:]]
        self.assertEqual(window, list(range(6, 16)))   # 10±5

    def test_write_stuck_replay_early_window_truncated_at_head(self):
        """锚点靠近开头：窗口下界截到 0、上界截到事件末尾，不越界。"""
        evs = _events(6)
        anchor = {"turn": 2, "reason": "zero_gain_stall", "seq": 3}
        path = write_stuck_replay(self.challenge_dir, "t1", anchor, events=evs)
        lines = path.read_text(encoding="utf-8").splitlines()
        window = [json.loads(ln)["seq"] for ln in lines[1:]]
        # [max(0,3-5):3+5] = [0:8] → 实际只有 6 条（seq 1..6）
        self.assertEqual(window, list(range(1, 7)))

    def test_write_stuck_replay_no_events_returns_none(self):
        """无历史事件：不落盘返回 None。"""
        path = write_stuck_replay(self.challenge_dir, "t1",
                                  {"turn": 1, "reason": "x", "seq": 0},
                                  events=[])
        self.assertIsNone(path)

    def _loop(self, ctx: TaskContext) -> ExecutorLoop:
        return ExecutorLoop(
            state=RunnerState(next_input="开始。"), ctx=ctx,
            code="t1", desc="", brief="任务书", charter="宪章",
            task="测试", global_plan="", role={}, field_notes="",
            challenge_workdir=self.challenge_dir,
            sessions_dir=self.sessions_dir,
            session=_FakeSession(), executor=_FakeExecutor(),
            model_pool=_FakePool(),
            hooks=None, outer_hooks=None, difficulty="", db=None,
        )

    def test_mark_stuck_anchor_records_and_exports(self):
        """_mark_stuck_anchor：登记惰性点并立即落盘 ±5 步回放文件。"""
        fake_bus = _FakeBus(_events(20))
        ctx = TaskContext(workdir=self.challenge_dir)
        loop = self._loop(ctx)
        with patch("harness.runner.executor.BUS", fake_bus), \
             patch("runtime.reporting.BUS", fake_bus):
            loop.state.turn_count = 7
            loop._mark_stuck_anchor("zero_gain_stall")
        self.assertEqual(len(ctx.stuck_anchors), 1)
        self.assertEqual(ctx.stuck_anchors[0]["turn"], 7)
        self.assertEqual(ctx.stuck_anchors[0]["reason"], "zero_gain_stall")
        replay = self.challenge_dir / "replay_stuck_t1_turn7_zero_gain_stall.jsonl"
        self.assertTrue(replay.exists())
        meta = json.loads(replay.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(meta["anchor_seq"], 20)

    def test_mark_stuck_anchor_dedup_same_turn(self):
        """同一轮不重复登记惰性点（锚点去重）。"""
        fake_bus = _FakeBus(_events(10))
        ctx = TaskContext(workdir=self.challenge_dir)
        loop = self._loop(ctx)
        with patch("harness.runner.executor.BUS", fake_bus), \
             patch("runtime.reporting.BUS", fake_bus):
            loop.state.turn_count = 5
            loop._mark_stuck_anchor("zero_gain_stall")
            loop._mark_stuck_anchor("zero_gain_stall")
        self.assertEqual(len(ctx.stuck_anchors), 1)

    def test_finish_completes_window_with_followup_steps(self):
        """_finish 收尾覆盖导出：惰性点后 5 步实际事件纳入窗口。"""
        ctx = TaskContext(workdir=self.challenge_dir)
        # 触发前已有 20 条事件 → mark 时 anchor_seq=20（前 5 步窗口 16..20）
        fake_bus = _FakeBus(_events(20))
        loop = self._loop(ctx)
        with patch("harness.runner.executor.BUS", fake_bus), \
             patch("runtime.reporting.BUS", fake_bus):
            loop.state.turn_count = 7
            loop._mark_stuck_anchor("zero_gain_stall")
            # 惰性点后又发生 5 步（seq 21..25）→ 收尾补全
            fake_bus.events.extend({"seq": 20 + i, "ts": 1.0, "task_id": "t1",
                                    "kind": "tool", "data": {"n": 20 + i}}
                                   for i in range(1, 6))
            with patch("harness.runner.context.FIELD_NOTES_FILE",
                       self.tmp / "field_notes.md"), \
                 patch("harness.runner.executor.FIELD_NOTES_FILE",
                       self.tmp / "field_notes.md"):
                outcome = loop._finish()
        self.assertEqual(outcome, "stopped")
        replay = self.challenge_dir / "replay_stuck_t1_turn7_zero_gain_stall.jsonl"
        self.assertTrue(replay.exists())
        lines = replay.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0])
        # anchor_seq=20 → 补全后窗口为 seq 16..25（±5 共 10 条）
        self.assertEqual(meta["window_events"], 10)
        window = [json.loads(ln)["seq"] for ln in lines[1:]]
        self.assertEqual(window, list(range(16, 26)))


if __name__ == "__main__":
    unittest.main()
