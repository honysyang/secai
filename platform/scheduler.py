"""调度器：跑分编排的纯函数层（零 LLM）。

选题 / 换题 / 看 hint / 容器 SOP 全部代码机械决策，不靠 LLM 自觉——这是诊断报告
P0-4 的落地，解决「Agent 卡在一道题不会换题 / 不会看提示」的结构性问题。

设计原则：
- 选题用 EV（期望价值），死路降权（0.3^attempts），不会反复撞同一道硬题；
- 看 hint 是机械前置（单题连续 N 轮零增量），不是 LLM 权衡；
- 换题也是机械决策（看 hint 后仍零增量 N 轮），不是 LLM 自觉。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 难度系数：易题优先（先拿能拿的分），难题降权
DIFF_COEF = {"easy": 1.3, "medium": 1.0, "hard": 0.7}
# 死路衰减：同一题每放弃一次，EV 乘 0.3，避免反复撞硬题
DECAY = 0.3

# 单题停滞机械前置（报告 P0-4「hint 第 8 轮机械前置」）
# 轮次预算按难度分级（对齐 SecAI/secai tsec_benchmark：easy≤12 / medium≤20 / hard≤25）：
# 难题给更多轮次，简单题卡住就尽早换（easy 单位时间得分率最高）。
_DIFF_BUDGET = {
    "easy":   {"hint": 6,  "skip": 12},
    "medium": {"hint": 8,  "skip": 20},
    "hard":   {"hint": 10, "skip": 25},
}
_DEFAULT_BUDGET = {"hint": 8, "skip": 16}   # 难度未知时的兜底
SINGLE_EMPTY_TURNS = 6   # 单题连续 N 轮无工具调用 → 机械换题（空转也放弃，与难度无关）


def select_challenge(challenges: List[dict], attempts: Dict[str, int]) -> Optional[dict]:
    """EV 选题：total_score × 难度系数 × 0.3^死路次数，跳过已完成题。

    返回 EV 最高的未完成题目；全部完成返回 None。
    """
    best: Optional[dict] = None
    best_ev = -1.0
    for c in challenges:
        if c.get("is_completed"):
            continue
        code = c.get("unique_code", "")
        coef = DIFF_COEF.get(str(c.get("difficulty", "")).lower(), 1.0)
        ev = float(c.get("total_score", 0) or 0) * coef * (DECAY ** attempts.get(code, 0))
        if ev > best_ev:
            best, best_ev = c, ev
    return best


def decide_stuck_action(zero_gain_turns: int, hint_used: bool,
                        difficulty: str = "") -> str:
    """单题停滞决策：'hint' 看提示 / 'skip' 换题 / 'continue' 继续。

    优先级：先看 hint（一次），看完仍无进展到更大阈值才换题。
    阈值按题目难度分级（easy/medium/hard），难题给更多轮次。
    """
    budget = _DIFF_BUDGET.get(str(difficulty).lower(), _DEFAULT_BUDGET)
    if zero_gain_turns >= budget["skip"]:
        return "skip"
    if zero_gain_turns >= budget["hint"] and not hint_used:
        return "hint"
    return "continue"
