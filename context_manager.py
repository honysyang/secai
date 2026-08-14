"""上下文生命周期管理：历史压缩 + 断点续跑。

压缩：会话 L2 层 token 量超过阈值时，把旧历史用 compactor agent 压成摘要（保留最近若干条），
    摘要写入 TaskContext.compaction_summary，由执行者动态 instructions 每轮注入。
断点：把 TaskContext + 任务元信息持久化到 state.json，配合文件型 SQLiteSession
     （session.sqlite）实现中断后 --resume 续跑。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from agents import Runner

from agents_def import compactor_agent
from task_context import TaskContext

# 会话 L2 层 token 量超过此值触发压缩（不再是 item 条数）
COMPACT_TOKEN_THRESHOLD = 30000
# token 估算系数：混合中英文约 2.5 字符 ≈ 1 token（偏保守，宁可早压）
CHARS_PER_TOKEN = 2.5
# 压缩时保留的最近 item 数（约 4~6 轮对话）
COMPACT_KEEP = 8
# 送给摘要器的旧历史文本上限（字符）
SUMMARY_INPUT_CHARS = 6000
# 压缩摘要本身的字符上限，防止摘要二次膨胀（约 500~700 token）
COMPACTION_SUMMARY_CHARS = 2000

STATE_FILE = "state.json"
SESSION_FILE = "session.sqlite"


def _is_boundary(item: Any) -> bool:
    """可作为安全切分点的消息：user 或 assistant（回合边界）。

    function_call / function_call_output 的 role 为 None，不是边界；从它们中间
    切断会拆散「tool_calls → tool 结果」的配对，导致 Chat Completions 400。
    """
    return isinstance(item, dict) and item.get("role") in ("user", "assistant")


def _split_for_compact(items: List[Any], keep: int) -> tuple[List[Any], List[Any]]:
    """按数量切 old/recent，但把 recent 起点回退到完整回合边界。

    Chat Completions 要求 role=tool 消息必须紧跟其 tool_calls；若 recent 第一条
    落在工具调用组中间（function_call / function_call_output），则把起点向前回退
    到最近的 user/assistant 消息，保证工具调用组不被拆散。
    """
    if len(items) <= keep:
        return [], list(items)
    old = list(items[:-keep])
    recent = list(items[-keep:])
    while recent and not _is_boundary(recent[0]) and old:
        recent.insert(0, old.pop())
    return old, recent


def _item_text(item: Any) -> str:
    """把原始 ResponseInputItem 抽成可读文本，供摘要器理解。"""
    if isinstance(item, dict):
        role = str(item.get("role", ""))
        content = item.get("content")
        if isinstance(content, str):
            return f"[{role}] {content}"
        if isinstance(content, list):
            parts: List[str] = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                t = c.get("type")
                if t in ("output_text", "input_text", "text"):
                    txt = c.get("text") or c.get("content") or ""
                    if txt:
                        parts.append(str(txt))
                elif t in ("function_call", "function_call_output", "tool_call"):
                    name = c.get("name") or ""
                    args = c.get("arguments") or c.get("output") or ""
                    parts.append(f"工具:{name}({str(args)[:200]})")
            return f"[{role}] " + " ".join(p for p in parts if p)
    return str(item)[:400]


def _estimate_tokens(items: List[Any]) -> int:
    """估算会话 item 的 token 量，用于压缩触发判断。

    用 JSON 序列化还原 item 的完整内容（含 tool 参数与结果），再除以
    CHARS_PER_TOKEN。混合中英文经验值偏保守，避免低估导致压缩永远不触发。
    """
    total = 0
    for item in items:
        try:
            text = json.dumps(item, ensure_ascii=False, default=str)
        except Exception:
            text = str(item)
        total += len(text)
    return int(total / CHARS_PER_TOKEN) + 1


def _emit_compact(ctx: TaskContext, estimate_before: int,
                  items_before: int, items_after: int) -> None:
    """把压缩事件写入 events.jsonl，供前端实时流展示。"""
    entry = {"kind": "compact", "ts": round(time.time(), 1),
             "estimate_before": estimate_before, "items_before": items_before,
             "items_after": items_after}
    try:
        with open(ctx.workdir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _emit_token_estimate(ctx: TaskContext, estimate: int) -> None:
    """把当前会话 token 估算写入事件流，供前端显示距离压缩阈值还有多远。"""
    entry = {"kind": "token_estimate", "ts": round(time.time(), 1),
             "estimate": estimate, "threshold": COMPACT_TOKEN_THRESHOLD}
    try:
        with open(ctx.workdir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


async def _summarize(old_text: str, prev_summary: str) -> str:
    prompt = (f"更早的历史摘要（可能为空）：\n{prev_summary or '（无）'}\n\n"
              f"需要并入的新历史：\n{old_text}")
    result = await Runner.run(compactor_agent, input=prompt)
    return str(result.final_output)


async def compact_if_needed(session, ctx: TaskContext) -> bool:
    """会话过大时压缩：旧历史→摘要（写 ctx.compaction_summary），保留最近若干条。"""
    items = await session.get_items()
    estimate = _estimate_tokens(items)
    _emit_token_estimate(ctx, estimate)
    if estimate <= COMPACT_TOKEN_THRESHOLD:
        return False

    old, recent = _split_for_compact(items, COMPACT_KEEP)
    old_text = "\n".join(_item_text(i) for i in old)[-SUMMARY_INPUT_CHARS:]
    summary = await _summarize(old_text, ctx.compaction_summary)
    if len(summary) > COMPACTION_SUMMARY_CHARS:
        summary = summary[:COMPACTION_SUMMARY_CHARS]

    # 清空会话，只保留最近若干条；摘要走系统提示，不占 session item
    await session.clear_session()
    await session.add_items(recent)
    ctx.compaction_summary = summary
    _emit_compact(ctx, estimate_before=estimate, items_before=len(items),
                  items_after=len(recent))
    return True


# ================= 断点续跑 =================
def save_state(workdir: Path, ctx: TaskContext, turn_count: int,
               task: str, charter: str, role: dict) -> None:
    state = {
        "task": task,
        "charter": charter,
        "role": role,
        "disclosed_skills": ctx.disclosed_skills,
        "notes": ctx.notes,
        "finalized": ctx.finalized,
        "final_payload": ctx.final_payload,
        "turn_count": turn_count,
        "empty_turns": ctx.empty_turns,
        "compaction_summary": ctx.compaction_summary,
        "vpn_connected": ctx.vpn_connected,
        "blackboard": ctx.blackboard,
        "token_usage": ctx.token_usage,
        "subtasks": ctx.subtasks,
        "enabled_tools": sorted(ctx.enabled_tools) if ctx.enabled_tools is not None else None,
        "phase": ctx.phase,
        "plan": ctx.plan,
        "stuck_turns": ctx.stuck_turns,
        "replan_count": ctx.replan_count,
        "zero_gain_turns": ctx.zero_gain_turns,
        "current_code": ctx.current_code,
        "fatal": ctx.fatal,
    }
    (workdir / STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state(workdir: Path) -> Dict[str, Any] | None:
    p = workdir / STATE_FILE
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def has_checkpoint(workdir: Path) -> bool:
    return (workdir / STATE_FILE).exists() and (workdir / SESSION_FILE).exists()


def build_ctx_from_state(workdir: Path, state: Dict[str, Any]) -> TaskContext:
    return TaskContext(
        workdir=workdir,
        disclosed_skills=state.get("disclosed_skills", []),
        notes=state.get("notes", []),
        finalized=state.get("finalized", False),
        final_payload=state.get("final_payload", {}),
        empty_turns=state.get("empty_turns", 0),
        compaction_summary=state.get("compaction_summary", ""),
        task=state.get("task", ""),
        charter=state.get("charter", ""),
        role=state.get("role", {}),
        turn_count=state.get("turn_count", 0),
        vpn_connected=state.get("vpn_connected", False),
        blackboard=state.get("blackboard", {}),
        token_usage=state.get("token_usage", {"input": 0, "output": 0, "total": 0, "requests": 0}),
        subtasks=state.get("subtasks", []),
        enabled_tools=(set(state["enabled_tools"])
                       if state.get("enabled_tools") is not None else None),
        phase=state.get("phase", "recon"),
        plan=state.get("plan", ""),
        stuck_turns=state.get("stuck_turns", 0),
        replan_count=state.get("replan_count", 0),
        zero_gain_turns=state.get("zero_gain_turns", 0),
        current_code=state.get("current_code", ""),
        fatal=state.get("fatal", ""),
    )
