"""卡壳治理：模型惰性 / 无进展时的切换模型或自救策略。

与 platform/scheduler.py 的机械决策互补：
- scheduler 决定「什么时候看 hint / 什么时候换题」；
- stuck.py 决定「同一题内，当 Agent 陷入惰性时，是换模型接管还是单模型自救换思路」。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from arsenal.registries.skill_registry import load_skills
from core.task_context import TaskContext


# 模型惰性检测阈值（可通过环境变量覆盖）
MODEL_SWITCH_TURNS = int(os.getenv("MODEL_SWITCH_TURNS", "6"))
MODEL_SELF_RESCUE_MAX = int(os.getenv("MODEL_SELF_RESCUE_MAX", "2"))


class StuckActionType(Enum):
    CONTINUE = "continue"           # 未达触发条件，继续正常循环
    SWITCH_MODEL = "switch_model"   # 多模型：切换到候选模型接管会话
    SELF_RESCUE = "self_rescue"     # 单模型：注入换思路提示 + 主动披露技能


@dataclass
class StuckAction:
    action: StuckActionType
    reason: str = ""
    extra_skills: List[str] = field(default_factory=list)
    next_input: str = ""
    reset_phase: bool = False


class StuckDetector:
    """检测单题执行是否陷入模型惰性，并决定下一步动作。

    触发阈值：连续 zero_gain 达到 MODEL_SWITCH_TURNS 时触发。
    策略：优先自救换思路；自救次数用尽后，若仍有候选模型，则切换模型接管。
    """

    def __init__(self, *, switch_turns: int = None, max_self_rescue: int = None):
        self.switch_turns = switch_turns if switch_turns is not None else MODEL_SWITCH_TURNS
        self.max_self_rescue = max_self_rescue if max_self_rescue is not None else MODEL_SELF_RESCUE_MAX
        self.self_rescue_count = 0
        self.switched = False  # 是否已尝试过模型切换

    def check(self, ctx: TaskContext, has_alternative: bool,
              current_model_name: str = "") -> StuckAction:
        """根据当前上下文判断是否应切换模型或自救。"""
        if ctx.zero_gain_turns < self.switch_turns:
            return StuckAction(action=StuckActionType.CONTINUE)

        # 优先自救：次数未超限时，不管是否有候选模型都先换思路
        if self.self_rescue_count < self.max_self_rescue:
            self.self_rescue_count += 1
            extra_skills = _pick_unseen_skills(ctx)
            for s in extra_skills:
                if s not in ctx.disclosed_skills:
                    ctx.disclosed_skills.append(s)
            # 阶段回退到 recon，让 Agent 重新评估全局
            old_phase = ctx.phase
            ctx.phase = "recon"
            return StuckAction(
                action=StuckActionType.SELF_RESCUE,
                reason=f"连续 {ctx.zero_gain_turns} 轮零增量，第 {self.self_rescue_count} 次自救换思路"
                        + ("（仍有候选模型，自救无效后将切换）" if has_alternative else "（仅有一个模型）"),
                extra_skills=extra_skills,
                reset_phase=True,
                next_input=_self_rescue_prompt(ctx, old_phase, extra_skills),
            )

        # 自救次数用尽后，若存在未尝试过的候选模型，则切换模型接管
        if has_alternative and not self.switched:
            self.switched = True
            return StuckAction(
                action=StuckActionType.SWITCH_MODEL,
                reason=f"连续 {ctx.zero_gain_turns} 轮零增量，自救 {self.max_self_rescue} 次无效，切换到候选模型接管",
            )

        # 自救用尽且已切过模型（或无候选）：返回 CONTINUE，让外层 scheduler 走 hint/skip
        suffix = ""
        if self.switched:
            suffix = "且已切换过模型"
        elif not has_alternative:
            suffix = "且无候选模型"
        return StuckAction(
            action=StuckActionType.CONTINUE,
            reason=f"连续 {ctx.zero_gain_turns} 轮零增量，自救次数用尽{suffix}，交由调度器决策",
        )


def _pick_unseen_skills(ctx: TaskContext, max_skills: int = 3) -> List[str]:
    """按当前阶段/角色主动挑选尚未披露的技能，用于单模型自救。"""
    skills = load_skills()
    phase = ctx.phase
    role_name = str(ctx.role.get("role", "")).lower()
    hints = [phase, role_name]
    hints.extend(ctx.task.lower().split())
    scored: List[tuple] = []
    for name, s in skills.items():
        if name in ctx.disclosed_skills:
            continue
        score = 0
        hay = " ".join([s.name, s.display_name, s.category, s.description,
                        " ".join(s.triggers)]).lower()
        for h in hints:
            if h and h in hay:
                score += 1
        # 优先选与当前阶段最相关的
        if phase and phase in hay:
            score += 2
        if score > 0:
            scored.append((score, name, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name, _ in scored[:max_skills]]


def _self_rescue_prompt(ctx: TaskContext, old_phase: str,
                        extra_skills: List[str]) -> str:
    """生成单模型自救时的 next_input。"""
    parts = [
        f"已连续 {ctx.zero_gain_turns} 轮没有产生新的关键证据，当前模型可能陷入循环。",
        "请立即换一种思路，不要重复已经尝试过且未成功的方法。",
    ]
    if extra_skills:
        parts.append(f"系统已为你解锁新打法参考：{', '.join(extra_skills)}。请优先尝试这些方向。")
    failed_paths = [
        k for k, v in ctx.blackboard.items()
        if isinstance(v, dict) and v.get("status") == "failed"
    ]
    if failed_paths:
        parts.append(f"已证伪的方向不要重复：{', '.join(failed_paths[:5])}。")
    parts.append(
        f"阶段已从 {old_phase} 重置为 recon，请重新做信息收集与攻击面识别，"
        "产出新的工具调用或新的发现后再继续。"
    )
    return "\n".join(parts)


def switch_model_prompt(ctx: TaskContext, old_model: str,
                        new_model: str) -> str:
    """生成多模型接管时的 next_input。"""
    return (
        f"新模型接管本题会话（{old_model} -> {new_model}）。"
        f"已连续 {ctx.zero_gain_turns} 轮没有产生新的关键证据，"
        "请回顾已有尝试和黑板记录，换种思路继续攻击本题容器，"
        "产出新的工具调用或新的发现。禁止重复已失败的路径。"
    )
