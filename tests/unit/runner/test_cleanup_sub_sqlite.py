"""H5 单测：session 收尾物理清理 sub_*.sqlite 残留（句柄安全）。

覆盖：_cleanup 对父 session 文件、后台子任务 session 句柄与 sub_*.sqlite
物理文件的三段清理；残留文件删除、句柄兜底 close、登记表清空。

运行：.venv/bin/python -m pytest tests/unit/runner/test_cleanup_sub_sqlite.py -v
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from core.task_context import TaskContext
from harness.runner.executor import ExecutorLoop
from harness.runner.state import RunnerState


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
    """父 session 替身：记录 close 调用。"""

    def __init__(self):
        self.closed = False

    async def get_items(self):
        return []

    def close(self):
        self.closed = True


class _DummySubSession:
    """残留子任务 session 替身：记录 close 调用。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class CleanupSubSqliteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.challenge_dir = self.tmp / "challenge"
        self.sessions_dir = self.tmp / "sessions"
        self.challenge_dir.mkdir(parents=True)
        self.sessions_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _loop(self, ctx: TaskContext) -> ExecutorLoop:
        return ExecutorLoop(
            state=RunnerState(next_input="开始。"),
            ctx=ctx, code="t1", desc="", brief="任务书", charter="宪章",
            task="测试", global_plan="", role={}, field_notes="",
            challenge_workdir=self.challenge_dir,
            sessions_dir=self.sessions_dir,
            session=_FakeSession(), executor=_FakeExecutor(),
            model_pool=_FakePool(),
            hooks=None, outer_hooks=None, difficulty="", db=None,
        )

    def test_cleanup_removes_sub_sqlite_files_and_closes_handles(self):
        """_cleanup 物理删除 sub_*.sqlite 残留 + 兜底 close 登记的句柄。"""
        ctx = TaskContext(workdir=self.challenge_dir)
        # 残留文件（正常完成的子任务已 close 但文件仍在；取消路径文件残留）
        for name in ("sub_aaa111.sqlite", "sub_bbb222.sqlite"):
            (self.challenge_dir / name).write_text("")
        # 父 session 文件
        (self.sessions_dir / "challenge_t1.sqlite").write_text("")
        # 登记表中仍打开的句柄（模拟 finally 未 pop 的异常残留）
        s1, s2 = _DummySubSession(), _DummySubSession()
        ctx.open_sub_sessions["aaa111"] = s1
        ctx.open_sub_sessions["bbb222"] = s2

        asyncio.run(self._loop(ctx)._cleanup())

        # 物理文件全删
        self.assertFalse((self.challenge_dir / "sub_aaa111.sqlite").exists())
        self.assertFalse((self.challenge_dir / "sub_bbb222.sqlite").exists())
        self.assertFalse((self.sessions_dir / "challenge_t1.sqlite").exists())
        # 句柄兜底 close + 登记表清空
        self.assertTrue(s1.closed)
        self.assertTrue(s2.closed)
        self.assertEqual(ctx.open_sub_sessions, {})

    def test_cleanup_keeps_evidence_files(self):
        """清理只删 session 类文件，不动 events.jsonl / cost_report.json 等证据。"""
        ctx = TaskContext(workdir=self.challenge_dir)
        (self.challenge_dir / "sub_x.sqlite").write_text("")
        (self.challenge_dir / "events.jsonl").write_text("{}\n")
        (self.challenge_dir / "cost_report.json").write_text("{}")
        (self.sessions_dir / "challenge_t1.sqlite").write_text("")

        asyncio.run(self._loop(ctx)._cleanup())

        self.assertFalse((self.challenge_dir / "sub_x.sqlite").exists())
        self.assertTrue((self.challenge_dir / "events.jsonl").exists())
        self.assertTrue((self.challenge_dir / "cost_report.json").exists())

    def test_cleanup_no_sub_files_is_noop(self):
        """无 sub_*.sqlite / 无登记句柄：清理为空操作不抛错。"""
        ctx = TaskContext(workdir=self.challenge_dir)
        loop = self._loop(ctx)
        asyncio.run(loop._cleanup())  # 不应抛异常
        self.assertEqual(ctx.silent_failures, 0)


if __name__ == "__main__":
    unittest.main()
