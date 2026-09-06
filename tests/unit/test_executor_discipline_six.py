"""H6 单测：Executor 工作纪律 9→6 条语义去重收口（R2 验收：去重不删约束语义）。

从 EXECUTOR_STATIC_INSTRUCTIONS 提取「# 工作纪律」到「# 当前任务书」之间的
编号条目：必须恰好 1..6 条（无 7+），且原 9 条的关键约束词全部保留。

运行：.venv/bin/python -m pytest tests/unit/test_executor_discipline_six.py -v
"""
from __future__ import annotations

import re
import unittest

from core.agents_def import EXECUTOR_STATIC_INSTRUCTIONS


def _discipline_block() -> str:
    start = EXECUTOR_STATIC_INSTRUCTIONS.index("# 工作纪律")
    end = EXECUTOR_STATIC_INSTRUCTIONS.index("# 当前任务书")
    return EXECUTOR_STATIC_INSTRUCTIONS[start:end]


class ExecutorDisciplineSixTest(unittest.TestCase):
    def test_discipline_items_are_exactly_six(self):
        """工作纪律条目收口为 6 条：行首编号恰为 1..6，无 7+。"""
        block = _discipline_block()
        nums = [int(m) for m in re.findall(r"^\s*(\d+)\.\s", block, re.MULTILINE)]
        self.assertEqual(nums, [1, 2, 3, 4, 5, 6], f"纪律条目应 1..6，实际 {nums}")
        self.assertNotIn("\n7.", block)
        self.assertNotIn("\n8.", block)
        self.assertNotIn("\n9.", block)

    def test_all_constraint_semantics_preserved(self):
        """去重不删约束：原 9 条核心语义关键词全部保留在某一条内。"""
        block = _discipline_block()
        # 每条原纪律的指纹词（去重后必须仍能找到）
        required = [
            "证据增量",                    # 旧 1
            "空转",                        # 旧 1
            "find_skills",                 # 旧 6 卡壳查打法
            "第一性原理",                  # 旧 6 禁停
            "任务书",                      # 旧 2
            "python3",                     # 旧 2
            "blackboard",                  # 旧 3 边渗透边记录
            "supersedes",                  # 旧 3 判死取代
            "fuzz",                        # 旧 4 批量探测
            "run_batch",                   # 旧 4
            "parallel_shell",              # 旧 4
            "spawn_subtask",               # 旧 4
            "敏感凭据与闭环",             # 旧 5（9_6 起去平台提交语义，只保留黑板留证）
            "最短路径",                    # 旧 9 拿成果
            "[闭环]",                      # 旧 9 最高优先级
            "remember",                    # 旧 7 沉淀
            "set_phase",                   # 旧 8
            "finalize",                    # 旧 8
        ]
        missing = [k for k in required if k not in block]
        self.assertEqual(missing, [], f"去重丢失约束语义：{missing}")


if __name__ == "__main__":
    unittest.main()
