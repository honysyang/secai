"""轨迹分叉分析：一次性强模型调用，替代常驻 Strategist/Coach。

设计原则：
- 只在主循环检测到连续零增量（zero_gain_turns >= 3）时触发；
- 读取最近 N 轮轨迹摘要（而非完整海联 raw output），生成结构化破局建议；
- 结果只写入 blackboard['next_directive']，由主循环在下一轮喂给 Executor；
- 一次性调用，不保留状态，每题最多触发 FORK_ANALYZE_MAX 次。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agents import Agent, Runner

from core.agents_def import EXECUTOR_SETTINGS
from runtime.log import log_info, log_warn
from runtime.model_fallback import run_with_model_fallback


FORK_ANALYZE_MAX = 3


FORK_ANALYST_SYSTEM = """你是一名冷静的 CTF 复盘分析师，只阅读轨迹与黑板，不调用任何工具。
你的任务是：找出当前攻击路径中最可能产生新证据的 1~3 个方向，并给出最小可验证动作。

输出格式（严格 JSON，不要 markdown 代码块）：
{
  "diagnosis": "简短诊断：为什么最近几轮没有增量（原因不超过 2 条）",
  "directions": [
    {
      "name": "方向名",
      "assumption": "这个方向依赖的核心假设",
      "action": "最小验证动作（具体到命令/路径/payload）",
      "expected": "如果假设成立，预期看到的具体证据",
      "fallback": "如果验证失败，下一步该排除什么"
    }
  ],
  "next_directive": "一段可直接发给执行 Agent 的自然语言指令，明确下一步做什么、不要做什么"
}

约束：
- 不要泛泛而谈，action 必须具体到可执行的 shell 命令或 HTTP 请求；
- 优先使用黑板上已确认的事实和已解锁的技能；
- 不要重复最近已经失败的路径；
- 不要编造 flag 或假设不存在的服务；
- 只输出 JSON，不要解释。"""


def _format_tail(events: List[dict], blackboard: Dict[str, Any], max_events: int = 12) -> str:
    """把最近事件和黑板上关键事实格式化为 fork_analyst 的输入。"""
    lines = ["# 当前黑板（已验证事实/死路/flag/hint）", json.dumps(blackboard, ensure_ascii=False, indent=2)[:2000]]
    if events:
        lines.append("\n# 最近事件流尾部（从新到旧）：")
        for ev in reversed(events[-max_events:]):
            kind = ev.get("kind", "unknown")
            data = ev.get("data", {})
            if kind == "tool_call":
                tool = data.get("tool", "?")
                args = data.get("args", {})
                lines.append(f"- call {tool}({json.dumps(args, ensure_ascii=False)[:200]})")
            elif kind == "tool_output":
                out = str(data.get("output", ""))[:200]
                lines.append(f"- output: {out}")
            elif kind == "llm":
                lines.append(f"- llm: {str(data.get('text', ''))[:200]}")
            else:
                lines.append(f"- {kind}: {json.dumps(data, ensure_ascii=False)[:200]}")
    return "\n".join(lines)


async def fork_analyze(
    events: List[dict],
    blackboard: Dict[str, Any],
    role_brief: str = "",
    model=None,
    model_pool=None,
    max_events: int = 12,
) -> Dict[str, Any]:
    """对最近轨迹做一次性强模型分析，返回结构化建议。

    Args:
        events: 最近事件流（从 BUS.history 或 hooks 导出）
        blackboard: 当前黑板字典
        role_brief: 当前角色/题型的简要提示，用于对齐分析方向
        model: 指定强模型；None 时使用 model_pool 中的 strong 角色模型
        model_pool: 模型池，用于失败 fallback

    Returns:
        dict，至少包含 next_directive 字段
    """
    analyst = Agent(
        name="ForkAnalyst",
        instructions=FORK_ANALYST_SYSTEM,
        model=model,
        model_settings=EXECUTOR_SETTINGS,
    )

    tail = _format_tail(events, blackboard, max_events=max_events)
    prompt = (
        f"{role_brief}\n\n"
        f"最近连续多轮没有产生新证据。请基于以下轨迹与黑板做分叉分析。\n\n"
        f"{tail}"
    )

    try:
        if model_pool is not None:
            result = await run_with_model_fallback(
                analyst, input=prompt, model_pool=model_pool,
                agent_name="ForkAnalyst", max_rounds=3, max_turns=1,
            )
        else:
            result = await Runner.run(analyst, input=prompt, max_turns=1)
        text = str(result.final_output or "").strip()
        # 兼容 LLM 偶尔包裹的 markdown 代码块
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("fork_analyze 输出不是 JSON 对象")
        parsed.setdefault("next_directive", "继续尝试新的验证方向，产出具体证据。")
        log_info(f"[fork-analyst] 分析完成：{parsed.get('diagnosis', '')[:80]}")
        return parsed
    except json.JSONDecodeError as e:
        log_warn(f"[fork-analyst] 输出不是合法 JSON：{e}；降级为文本指令")
        # 降级：直接把 LLM 输出文本作为 next_directive
        return {
            "diagnosis": "输出解析失败",
            "directions": [],
            "next_directive": text if "text" in dir() else "继续探索新方向，优先验证未尝试的入口。",
        }
    except Exception as e:
        log_warn(f"[fork-analyst] 调用失败：{e}")
        return {
            "diagnosis": f"调用失败：{type(e).__name__}",
            "directions": [],
            "next_directive": "继续探索新方向，优先使用已解锁技能做最小验证。",
        }


def update_blackboard_with_fork(blackboard: Dict[str, Any], result: Dict[str, Any]) -> str:
    """把 fork_analyze 结果写入 blackboard，返回 next_directive 文本。"""
    directive = str(result.get("next_directive", "")).strip()
    if not directive:
        directive = "继续探索新方向，优先验证未尝试的入口。"
    blackboard["next_directive"] = {
        "value": directive,
        "status": "confirmed",
        "ts": __import__("time").time(),
        "verified": False,
        "fork": {
            "diagnosis": result.get("diagnosis", ""),
            "directions": result.get("directions", []),
        },
    }
    return directive
