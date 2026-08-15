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
# 收尾回捞衰减：进入收尾阶段（所有题都至少放弃过一次）后衰减更温和，
# 0.6^attempts 比 0.3^attempts 慢得多，让放弃过的题有机会被重新认真对待
ENDGAME_DECAY = 0.6

# 单题停滞机械前置（报告 P0-4「hint 第 8 轮机械前置」）
# 轮次预算按难度分级（对齐 SecAI/secai tsec_benchmark：easy≤12 / medium≤20 / hard≤25）。
# 针对 cloud 类题目（azure/s3/blob/sas/storage），因为结果通常二元、死磕收益低，
# hint/skip 阈值整体再提前一档。
_HINT_EARLY_TURNS = 2
_SKIP_EARLY_TURNS = 4
_HINT_DIFF_BUDGET = {
    "easy":   {"hint": 6,  "skip": 12},
    "medium": {"hint": 8,  "skip": 20},
    "hard":   {"hint": 10, "skip": 25},
}
_DEFAULT_HINT_BUDGET = {"hint": 8, "skip": 16}   # 难度未知时的兜底
SINGLE_EMPTY_TURNS = 6   # 单题连续 N 轮无工具调用 → 机械换题（空转也放弃，与难度无关）

# 触发“提前放弃”的题目指纹关键词（云存储 / SaaS / 二元结果类）
# 注意：不要加入过于宽泛的词（如 container），避免误触发普通 Web 题
_EARLY_HINT_KEYWORDS = ("azure", "azurite", "blob", "sas", "s3", "lambda",
                        "firebase", "supabase", "aws storage", "gcp", "google cloud")


def is_endgame(challenges: List[dict], attempts: Dict[str, int]) -> bool:
    """判断是否进入收尾回捞阶段：所有未完成题都至少被放弃过一次。

    此时没有「未尝试」的题可做，应降低衰减，回捞放弃过的题逐个再解决。
    """
    unfinished = [c for c in challenges if not c.get("is_completed")]
    if not unfinished:
        return False
    return all(attempts.get(c.get("unique_code", ""), 0) > 0 for c in unfinished)


def select_challenge(challenges: List[dict], attempts: Dict[str, int],
                     endgame: bool = False) -> Optional[dict]:
    """EV 选题：total_score × 难度系数 × 衰减^死路次数，跳过已完成题。

    endgame=True（收尾回捞阶段）时用更温和的 ENDGAME_DECAY，回捞放弃过的题。
    返回 EV 最高的未完成题目；全部完成返回 None。
    """
    base = ENDGAME_DECAY if endgame else DECAY
    best: Optional[dict] = None
    best_ev = -1.0
    for c in challenges:
        if c.get("is_completed"):
            continue
        code = c.get("unique_code", "")
        coef = DIFF_COEF.get(str(c.get("difficulty", "")).lower(), 1.0)
        ev = float(c.get("total_score", 0) or 0) * coef * (base ** attempts.get(code, 0))
        if ev > best_ev:
            best, best_ev = c, ev
    return best


def decide_stuck_action(zero_gain_turns: int, hint_used: bool,
                        difficulty: str = "", task_text: str = "") -> str:
    """单题停滞决策：'hint' 看提示 / 'skip' 换题 / 'continue' 继续。

    优先级：先看 hint（一次），看完仍无进展到更大阈值才换题。
    阈值按题目难度分级（easy/medium/hard），难题给更多轮次。
    若题目描述/指纹命中云存储等二元结果类关键词，hint/skip 阈值整体提前。
    """
    task_lower = str(task_text).lower()
    is_cloud_like = any(kw in task_lower for kw in _EARLY_HINT_KEYWORDS)

    budget = _HINT_DIFF_BUDGET.get(str(difficulty).lower(), _DEFAULT_HINT_BUDGET).copy()
    if is_cloud_like:
        budget["hint"] = max(2, budget["hint"] - _HINT_EARLY_TURNS)
        budget["skip"] = max(4, budget["skip"] - _SKIP_EARLY_TURNS)

    if zero_gain_turns >= budget["skip"]:
        return "skip"
    if zero_gain_turns >= budget["hint"] and not hint_used:
        return "hint"
    return "continue"
