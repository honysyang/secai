"""H7 破局链·三闸门「前提证伪即级联回收」单测（R2 验收）。

黑板某 key 被证伪（status=failed 或带 supersedes 取代标记）时，
回收 depends_on 声明依赖该 key 的 pending/running 子任务。

运行：.venv/bin/python -m pytest tests/unit/runner/test_subtask_cascade.py -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.task_context import SubtaskBudget, TaskContext
from harness.runner.subtasks import (
    _cancel_subtask,
    _cascade_cancel_falsified,
)


def _ctx() -> TaskContext:
    return TaskContext(workdir=Path(tempfile.mkdtemp()))


def _sub(sid: str, status: str, depends_on: str = "") -> dict:
    return {
        "id": sid, "desc": f"子任务 {sid}", "objective": "验证 X 漏洞",
        "branch_type": "web", "depends_on": depends_on, "status": status,
        "result": "", "budget": SubtaskBudget(objective="验证 X 漏洞",
                                              max_turns=8),
    }


class SubtaskCascadeTest(unittest.TestCase):
    def test_failed_premise_cancels_running_dependent(self):
        """黑板 failed 前提 → running 依赖子任务被级联回收（job cancel + done）。"""
        ctx = _ctx()
        ctx.subtasks.append(_sub("s1", "running", depends_on="sqli_possible"))
        ctx.subtasks.append(_sub("s2", "running"))  # 无 depends_on，不受影响
        job1 = Mock()
        job1.done.return_value = False
        ctx.subtask_jobs["s1"] = job1
        ctx.blackboard["sqli_possible"] = {"value": "", "status": "failed",
                                           "verified": True}

        n = _cascade_cancel_falsified(ctx)

        self.assertEqual(n, 1)
        job1.cancel.assert_called_once()
        s1 = ctx.subtasks[0]
        self.assertEqual(s1["status"], "done")
        self.assertIn("级联回收", s1["result"]["summary"])
        self.assertTrue(s1["budget"].cancelled)
        self.assertEqual(s1["budget"].reason, "premise_falsified")
        # 无依赖子任务保持 running
        self.assertEqual(ctx.subtasks[1]["status"], "running")

    def test_supersedes_premise_also_cascades(self):
        """前提被 supersedes 取代（旧结论推翻）同样触发级联回收。"""
        ctx = _ctx()
        ctx.subtasks.append(_sub("s1", "pending", depends_on="sqli_old"))
        ctx.blackboard["sqli_old"] = {"value": "false", "status": "failed",
                                      "supersedes": "sqli_old", "verified": True}
        n = _cascade_cancel_falsified(ctx)
        self.assertEqual(n, 1)
        self.assertEqual(ctx.subtasks[0]["status"], "done")

    def test_multi_dep_any_falsified_cancels(self):
        """多前提（逗号分隔）：任一被证伪即回收。"""
        ctx = _ctx()
        ctx.subtasks.append(_sub("s1", "running",
                                 depends_on="port_open, sqli_possible"))
        job1 = Mock()
        job1.done.return_value = False
        ctx.subtask_jobs["s1"] = job1
        ctx.blackboard["port_open"] = {"value": "", "status": "failed"}
        n = _cascade_cancel_falsified(ctx)
        self.assertEqual(n, 1)
        self.assertEqual(ctx.subtasks[0]["status"], "done")

    def test_no_falsified_no_cancel(self):
        """黑板无证伪前提：任何子任务都不回收。"""
        ctx = _ctx()
        ctx.subtasks.append(_sub("s1", "running", depends_on="sqli_possible"))
        job1 = Mock()
        job1.done.return_value = False
        ctx.subtask_jobs["s1"] = job1
        ctx.blackboard["sqli_possible"] = {"value": "true", "status": "confirmed"}
        self.assertEqual(_cascade_cancel_falsified(ctx), 0)
        self.assertEqual(ctx.subtasks[0]["status"], "running")

    def test_falsified_key_without_dependency_untouched(self):
        """黑板有 failed 前提但无子任务依赖它：返回 0 不误杀。"""
        ctx = _ctx()
        ctx.subtasks.append(_sub("s1", "running"))
        ctx.blackboard["dead_path"] = {"value": "", "status": "failed"}
        self.assertEqual(_cascade_cancel_falsified(ctx), 0)
        self.assertEqual(ctx.subtasks[0]["status"], "running")

    def test_cancel_subtask_already_done_is_noop(self):
        """已结束子任务再次取消为空操作。"""
        ctx = _ctx()
        ctx.subtasks.append(_sub("s1", "done", depends_on="x"))
        ctx.subtasks[0]["result"] = {"summary": "已完成"}
        self.assertFalse(_cancel_subtask(ctx, "s1"))
        self.assertEqual(ctx.subtasks[0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
