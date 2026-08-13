"""判停器：由代码决定「当前是否应该停止」（对标 AutoCVE 的 stop_hooks / token_budget）。

不依赖固定回合数作为主判据，而是综合：
  1. 终端动作 —— 执行者主动调用 finalize 工具提交结论；
  2. 空转/假完成检测 —— 连续 N 轮既没调工具也没 finalize；
  3. 字符预算 —— 累计事件流字节数超限；
  4. 回合兜底 —— MAX_TURNS 仅作最后防线。

返回 dict：{"stop": bool, "reason": str, "nudge": str(可选)}。
"""
from __future__ import annotations

from typing import Any

MAX_TURNS = 0        # 兜底：最多 LLM 回合数，如果等于0 ，则没有回合限制
CHAR_BUDGET = 0  # 兜底：累计事件流字节预算（跑分任务要读大量响应，30KB 太小）
EMPTY_TURN_LIMIT = 5  # 连续 N 轮无工具调用且未 finalize → 判停（0 表示不因空转判停）

EMPTY_TURN_NUDGE = (
    "你上一轮既没有调用任何工具，也没有调用 finalize 提交结论。"
    "如果还需要继续调查，请立即调用工具（shell/http_request/distinguish/web_search）；"
    "如果任务已经完成，请调用 finalize 提交最终结论。不要只描述计划而不执行。"
)


def should_stop(ctx: Any, turn_count: int, total_chars: int) -> dict:
    # 1) 终端动作：执行者已调用 finalize
    if ctx.finalized:
        return {"stop": True, "reason": "finalized"}

    # 2) 回合兜底（0 表示不限制）
    if MAX_TURNS > 0 and turn_count >= MAX_TURNS:
        return {"stop": True, "reason": "max_turns_backstop"}

    # 3) 字符预算兜底（0 表示不限制）
    if CHAR_BUDGET > 0 and total_chars >= CHAR_BUDGET:
        return {"stop": True, "reason": "char_budget_exhausted"}

    # 4) 空转/假完成检测：本轮没有任何工具调用
    if ctx.turn_tool_count == 0:
        ctx.empty_turns += 1
        if EMPTY_TURN_LIMIT > 0 and ctx.empty_turns >= EMPTY_TURN_LIMIT:
            return {"stop": True, "reason": "empty_turn_limit"}
        return {"stop": False, "nudge": EMPTY_TURN_NUDGE}

    # 5) 本轮有工具调用 → 继续
    ctx.empty_turns = 0
    return {"stop": False}
