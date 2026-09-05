"""H3 增量上下文单测：charter/plan 版本化 + field_notes 仅首轮（R2 验收）。

验收口径：第 10 轮动态上下文注入量（估算 token）显著小于首轮，新增 token
占比 <40%。用大段 charter/plan/field_notes 放大差异；核心打法（CORE_SKILLS
常驻注入）是 H3 之外的固定开销，与增量改造无关，单测里 patch 为固定小段
以免淹没 charter/plan/notes 的减量信号。

运行：.venv/bin/python -m pytest tests/unit/test_dynamic_context_incremental.py -v
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from agents import RunContextWrapper

from core.agents_def import _build_dynamic_context
from core.task_context import TaskContext

# 中英混合粗估：2.5 字符 ≈ 1 token（与 core/context_manager.CHARS_PER_TOKEN 一致）
CHARS_PER_TOKEN = 2.5

_BIG_CHARTER = ("# 使命宪章\n- 目标：可验证的完成判据（一句话说死）\n- 关键原则："
                + "证据驱动不臆测；宁可判死不可空转；死路不重复。" * 120)
_BIG_PLAN = ("# 作战计划\n- 任务研判：目标类型、技术栈、最可能漏洞类型。"
             + "每个候选必须给出可验证假设与最小探测。" * 120)
_BIG_NOTES = ("# 历史作战档案\n- 已证伪方向：sqlmap 对 JSON API 需 --json 参数。"
              + "登录表单无注入点。" * 120)


def _est_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


class DynamicContextIncrementalTest(unittest.TestCase):
    def setUp(self) -> None:
        # 核心打法常驻注入是固定开销（与 H3 增量无关），固定为小段避免淹没信号
        p = patch("core.agents_def.load_skill_bodies",
                  return_value="（核心打法固定段）")
        p.start()
        self.addCleanup(p.stop)

    def _ctx(self) -> TaskContext:
        return TaskContext(workdir=Path("."))

    def _build(self, ctx: TaskContext, charter: str, plan: str,
               notes: str) -> str:
        return _build_dynamic_context(
            RunContextWrapper(context=ctx), charter, plan, notes)

    def test_round1_injects_full_charter_plan_notes(self):
        """首轮：charter/plan/field_notes 全量注入，版本号登记为 v1。"""
        ctx = self._ctx()
        text = self._build(ctx, _BIG_CHARTER, _BIG_PLAN, _BIG_NOTES)
        self.assertIn(_BIG_CHARTER[:80], text)
        self.assertIn(_BIG_PLAN[:80], text)
        self.assertIn(_BIG_NOTES[:80], text)
        self.assertEqual(ctx.injected_versions.get("charter"), 1)
        self.assertEqual(ctx.injected_versions.get("plan"), 1)
        self.assertTrue(ctx.field_notes_injected)

    def test_round10_injection_below_40_percent(self):
        """R2 验收：第 10 轮注入量（估算 token）≤ 首轮 40%。"""
        ctx = self._ctx()
        text1 = self._build(ctx, _BIG_CHARTER, _BIG_PLAN, _BIG_NOTES)
        est1 = _est_tokens(text1)
        # 第 2~10 轮：charter/plan/field_notes 参数不变（执行器每轮传同一现场）
        text10 = ""
        for _ in range(2, 11):
            text10 = self._build(ctx, _BIG_CHARTER, _BIG_PLAN, _BIG_NOTES)
        est10 = _est_tokens(text10)
        self.assertGreater(est1, est10)  # 首轮确实更大（含三大段全量）
        ratio = est10 / est1
        self.assertLess(ratio, 0.40,
                        f"第 10 轮/首轮 token 占比 {ratio:.2%} 应 <40%")

    def test_unchanged_plan_injects_one_line(self):
        """plan 未变更：只注入一行「作战计划 v1 无变更」，不含首轮全文。"""
        ctx = self._ctx()
        self._build(ctx, _BIG_CHARTER, _BIG_PLAN, _BIG_NOTES)
        text2 = self._build(ctx, _BIG_CHARTER, _BIG_PLAN, _BIG_NOTES)
        self.assertIn("# 作战计划 v1 无变更", text2)
        self.assertNotIn(_BIG_PLAN[:60], text2)
        # charter 同理只注入一行
        self.assertIn("# 使命宪章 v1 无变更", text2)
        self.assertNotIn(_BIG_CHARTER[:60], text2)

    def test_plan_change_reinjects_full_new_version(self):
        """plan 内容变化（replan 产出新计划）：全量注入新版本 v2。"""
        ctx = self._ctx()
        self._build(ctx, _BIG_CHARTER, _BIG_PLAN, _BIG_NOTES)
        new_plan = "# 作战计划 v2：换用 JWT 伪造路线直接打 /api/token。"
        text = self._build(ctx, _BIG_CHARTER, new_plan, _BIG_NOTES)
        self.assertIn(new_plan, text)
        self.assertEqual(ctx.injected_versions.get("plan"), 2)
        # 再次调用：v2 不再变化 → 一行占位
        text2 = self._build(ctx, _BIG_CHARTER, new_plan, _BIG_NOTES)
        self.assertIn("# 作战计划 v2 无变更", text2)
        self.assertNotIn(new_plan, text2)

    def test_field_notes_only_first_round(self):
        """field_notes 仅首轮注入：后续轮不再出现历史档案全文。"""
        ctx = self._ctx()
        self._build(ctx, _BIG_CHARTER, _BIG_PLAN, _BIG_NOTES)
        text2 = self._build(ctx, _BIG_CHARTER, _BIG_PLAN, _BIG_NOTES)
        self.assertNotIn("# 历史作战档案", text2)
        self.assertNotIn(_BIG_NOTES[:60], text2)

    def test_empty_charter_plan_fallback(self):
        """空 charter/plan：fallback 占位，不登记版本（避免把空串当 v1 内容）。"""
        ctx = self._ctx()
        text = self._build(ctx, "", "", "")
        self.assertIn("（无）", text)
        self.assertEqual(ctx.injected_versions.get("charter", 0), 0)
        self.assertFalse(ctx.field_notes_injected)


if __name__ == "__main__":
    unittest.main()
