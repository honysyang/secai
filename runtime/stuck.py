"""卡壳治理：模型惰性 / 无进展时的切换模型或自救策略。

与 bench_platform/scheduler.py 的机械决策互补：
- scheduler 决定「什么时候看 hint / 什么时候换题」；
- stuck.py 决定「同一题内，当 Agent 陷入惰性时，是换模型接管还是单模型自救换思路」。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from agents import Runner

from arsenal.registries.skill_registry import load_skills
from core.agents_def import compactor_agent
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
            # 阶段走向：已确认漏洞时保留 exploit 阶段，否则回退 recon 重新评估全局
            old_phase = ctx.phase
            keep_exploit = _has_confirmed_vuln(ctx)
            if keep_exploit:
                # 已有 confirmed 漏洞：不要回退 recon，停留在 exploit/post 继续换利用方法
                if ctx.phase in ("recon", "enumerate", "detect"):
                    ctx.phase = "exploit"
                reset_phase = False
            else:
                ctx.phase = "recon"
                reset_phase = True
            return StuckAction(
                action=StuckActionType.SELF_RESCUE,
                reason=f"连续 {ctx.zero_gain_turns} 轮零增量，第 {self.self_rescue_count} 次自救换思路"
                        + ("（仍有候选模型，自救无效后将切换）" if has_alternative else "（仅有一个模型）"),
                extra_skills=extra_skills,
                reset_phase=reset_phase,
                next_input=_self_rescue_prompt(ctx, old_phase, extra_skills, keep_exploit=keep_exploit),
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


# 确认过的漏洞类黑板 key（status=confirmed），自救时保留 exploit 阶段而非回退 recon
_CONFIRMED_VULN_KEYS = (
    "lfi_confirmed", "sqli_confirmed", "rce_confirmed", "ssrf_endpoint",
    "api_flag_endpoint", "upload_success", "deserialization_confirmed",
    "logic_flaw_hint", "vuln_confirmed",
)


def _has_confirmed_vuln(ctx: TaskContext) -> bool:
    """判断黑板是否已有 confirmed 漏洞，用于自救时决定阶段走向。"""
    for key in _CONFIRMED_VULN_KEYS:
        v = ctx.blackboard.get(key)
        if isinstance(v, dict) and v.get("status") == "confirmed":
            return True
    # 兼容旧写法：任意黑板项 value=True 且 status=confirmed 也算
    for v in ctx.blackboard.values():
        if isinstance(v, dict) and v.get("status") == "confirmed" and v.get("verified"):
            return True
    return False


# 自救选技能：攻击面/技术栈关键词 → 建议解锁的漏洞技能。
# 相比旧版「阶段+角色模糊匹配」，改为优先命中题目指纹里的攻击面，聚焦解题而非工具用法。
_VULN_HINTS = {
    # 技术栈指纹
    "flask": ["authentication_jwt", "broken_function_level_authorization", "ssti", "mass_assignment"],
    "werkzeug": ["authentication_jwt", "broken_function_level_authorization", "ssti"],
    "django": ["authentication_jwt", "broken_function_level_authorization", "idor", "mass_assignment"],
    "fastapi": ["authentication_jwt", "idor", "mass_assignment"],
    "php": ["sql_injection", "path_traversal_lfi_rfi", "insecure_file_uploads", "ssrf"],
    "java": ["insecure_deserialization", "path_traversal_lfi_rfi"],
    "node": ["prototype_pollution", "nosql_injection"],
    # 攻击面 / 功能关键词
    "ssrf": ["ssrf"],
    "proxy": ["ssrf"],
    "file://": ["path_traversal_lfi_rfi", "ssrf"],
    "weaver": ["ssrf", "sql_injection", "path_traversal_lfi_rfi"],
    "泛微": ["ssrf", "sql_injection", "path_traversal_lfi_rfi"],
    "admin": ["broken_function_level_authorization", "idor", "mass_assignment"],
    "管理员": ["broken_function_level_authorization", "idor"],
    "upload": ["insecure_file_uploads"],
    "上传": ["insecure_file_uploads"],
    "import": ["xxe", "insecure_file_uploads"],
    "导入": ["xxe", "insecure_file_uploads"],
    "login": ["authentication_jwt", "sql_injection"],
    "登录": ["authentication_jwt", "sql_injection"],
    "session": ["authentication_jwt", "broken_function_level_authorization"],
    "jwt": ["authentication_jwt"],
    "oauth": ["authentication_jwt"],
    "graphql": ["idor", "mass_assignment"],
    "price": ["business_logic", "race_conditions"],
    "优惠券": ["business_logic", "race_conditions"],
    "秒杀": ["business_logic", "race_conditions"],
    "文档": ["idor", "information_disclosure", "mass_assignment"],
    "机密": ["information_disclosure", "idor"],
    "命令执行": ["rce"],
    "反序列化": ["insecure_deserialization"],
    "deserialization": ["insecure_deserialization"],
    # 云存储 / Azure Blob / SAS 相关
    "azure": ["azure_blob_storage", "information_disclosure", "idor"],
    "azurite": ["azure_blob_storage", "information_disclosure"],
    "blob": ["azure_blob_storage", "information_disclosure", "idor"],
    "sas": ["azure_blob_storage", "information_disclosure", "idor"],
    "container": ["azure_blob_storage", "idor"],
    "storage": ["azure_blob_storage", "information_disclosure"],
    "devstoreaccount": ["azure_blob_storage"],
    "aws": ["aws", "information_disclosure"],
    "s3": ["aws", "information_disclosure"],
    "lambda": ["aws", "ssrf"],
    "firebase": ["firebase_firestore", "information_disclosure"],
    "supabase": ["supabase", "information_disclosure"],
}


def _fingerprint_text(ctx: TaskContext) -> str:
    """拼接题目指纹/技术栈线索（黑板 + 任务 + 计划），作为选技能匹配文本。"""
    parts = []
    for key in ("fingerprint", "target_fingerprint", "target"):
        v = ctx.blackboard.get(key)
        if isinstance(v, dict):
            val = str(v.get("value", "")).strip()
            if val:
                parts.append(val)
    parts.append(ctx.task)
    parts.append(ctx.plan)
    return " ".join(parts).lower()


def _pick_unseen_skills(ctx: TaskContext, max_skills: int = 3) -> List[str]:
    """自救时挑选尚未披露的技能。

    策略（聚焦解题，避免旧版只解锁工具用法）：
    1. 用题目指纹/任务/计划里的攻击面关键词精确命中漏洞技能（+4）；
    2. 漏洞类技能（vulnerabilities）兜底（+2）；
    3. 排除纯工具/扫描模式技能（tooling/scan_modes）。
    """
    skills = load_skills()
    text = _fingerprint_text(ctx)

    scores: Dict[str, int] = {}
    for kw, names in _VULN_HINTS.items():
        if kw in text:
            for n in names:
                if n in skills:
                    scores[n] = scores.get(n, 0) + 4

    for name, s in skills.items():
        if name in ctx.disclosed_skills:
            continue
        if s.category in ("tooling", "scan_modes"):
            continue
        sc = scores.get(name, 0)
        if s.category == "vulnerabilities":
            sc += 2
        hay = f"{s.name} {s.display_name} {s.category} {s.description} {' '.join(s.triggers)}".lower()
        if ctx.phase and ctx.phase in hay:
            sc += 1
        if sc > 0:
            scores[name] = sc

    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    return [name for name, _ in ranked[:max_skills]]


def _self_rescue_prompt(ctx: TaskContext, old_phase: str,
                        extra_skills: List[str], keep_exploit: bool = False) -> str:
    """生成单模型自救时的 next_input。"""
    bb_snapshot = json.dumps(
        {k: v for k, v in ctx.blackboard.items()
         if isinstance(v, dict) and v.get("status") in ("done", "failed")},
        ensure_ascii=False)[:1500]
    parts = [
        f"[已确认/已排除结论快照，禁止重复]\n{bb_snapshot}",
        f"已连续 {ctx.zero_gain_turns} 轮没有产生新的关键证据，当前模型可能陷入循环。",
    ]
    if keep_exploit:
        parts.append(
            "你已经确认过可利用漏洞（见黑板 confirmed 项）。请基于已确认漏洞，"
            "更换利用方式或 payload，不要重新做信息收集，也不要重复已失败的路径。"
        )
    else:
        parts.append(
            "请立即换一种思路，不要重复已经尝试过且未成功的方法。"
        )
    parts.append(
        "一次探测脚本应同时覆盖所有候选假设（例如：list 容器、list blob、常见 blob 名、直连后端等），禁止把同一思路改参数后重复写成 probe2/probe3。"
    )
    # 若此前已做过历史压缩，把摘要注入提示，让模型基于全局事实继续
    if ctx.compaction_summary:
        parts.append(f"历史压缩摘要：\n{ctx.compaction_summary}")
        parts.append("请基于以上摘要继续推进，不要重复摘要中已标记为失败或无进展的方向。")
    if extra_skills:
        parts.append(f"系统已为你解锁新打法参考：{', '.join(extra_skills)}。请优先尝试这些方向。")
    failed_paths = [
        k for k, v in ctx.blackboard.items()
        if isinstance(v, dict) and v.get("status") == "failed"
    ]
    if failed_paths:
        parts.append(f"已证伪的方向不要重复：{', '.join(failed_paths[:5])}。")
    if keep_exploit:
        parts.append(f"当前阶段保持 {ctx.phase}，请集中精力完成利用并提交 flag。")
    else:
        parts.append(
            f"阶段已从 {old_phase} 重置为 recon，请重新做信息收集与攻击面识别，"
            "产出新的工具调用或新的发现后再继续。"
        )
    return "\n".join(parts)


def _format_blackboard_summary(board: Dict[str, Any], max_chars: int = 1200) -> str:
    """把黑板内容整理成用于压缩的精简摘要。"""
    if not board:
        return "（空）"
    lines = []
    for k, v in board.items():
        if isinstance(v, dict):
            value = str(v.get("value", ""))[:120]
            status = str(v.get("status", "")).strip()
            evidence = str(v.get("evidence", "")).strip()
            parts = [f"{k}: {value}"]
            if status:
                parts.append(f"状态={status}")
            if evidence:
                parts.append(f"证据={evidence[:60]}")
            lines.append(" | ".join(parts))
        else:
            lines.append(f"{k}: {str(v)[:120]}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...（已截断）"
    return text


def _format_failed_actions(items: List[Any], max_chars: int = 4000) -> str:
    """从历史 items 中提取最近若干轮失败/无增量的动作文本。"""
    failed_lines = []
    for item in items[-40:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", ""))
        if role != "assistant":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            t = c.get("type")
            if t in ("function_call", "tool_call"):
                name = c.get("name") or ""
                args = c.get("arguments") or c.get("output") or ""
                failed_lines.append(f"工具:{name}({str(args)[:200]})")
    text = "\n".join(failed_lines[-20:])
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...（已截断）"
    return text or "（未提取到动作）"


async def compact_session(ctx: TaskContext, session, compactor_model=None) -> Optional[str]:
    """自救时对单题 session 进行历史压缩。

    使用 compactor_agent 对当前 session 历史进行摘要；摘要成功后清空 SQLiteSession
    中的历史消息（保留任务元信息在 ctx 中），并把摘要写入 ctx.compaction_summary。
    失败时返回 None，不抛出异常，避免中断外层主循环。
    """
    from agents.memory import SQLiteSession

    # 仅支持 SQLiteSession；其他 session 类型直接跳过
    if not isinstance(session, SQLiteSession):
        return None

    # 构造摘要输入：题目、角色、阶段、黑板关键内容、失败路径、已尝试动作
    try:
        items = await session.get_items()
    except Exception:
        return None

    failed_paths = [
        k for k, v in ctx.blackboard.items()
        if isinstance(v, dict) and v.get("status") == "failed"
    ]
    tried_major = _format_failed_actions(items)
    board_summary = _format_blackboard_summary(ctx.blackboard)

    compact_input = (
        f"题目：{ctx.task[:500]}\n\n"
        f"角色：{ctx.role.get('role', '未知')}\n"
        f"当前阶段：{ctx.phase}\n\n"
        f"黑板关键内容：\n{board_summary}\n\n"
        f"已证伪方向（不要重复）：\n{', '.join(failed_paths[:10]) or '（无）'}\n\n"
        f"最近失败路径上的主要动作：\n{tried_major}"
    )

    # 若有指定模型，克隆 compactor_agent 使用该模型；否则使用默认 MODEL
    agent = compactor_agent
    if compactor_model is not None:
        try:
            # dataclass 方式克隆，避免修改全局 agent 定义
            agent = compactor_agent.model_copy(update={"model": compactor_model})
        except Exception:
            # 部分 SDK 版本可能不支持 model_copy，回退到手动重建
            agent = compactor_agent.__class__(
                name=compactor_agent.name,
                instructions=compactor_agent.instructions,
                model=compactor_model,
                model_settings=compactor_agent.model_settings,
            )

    try:
        result = await Runner.run(
            agent,
            input=compact_input,
            context=ctx,
            session=session,
            max_turns=1,
        )
        summary = str(result.final_output).strip()
    except Exception:
        return None

    if not summary:
        return None

    # 摘要成功：清空 SQLiteSession 历史（保留 ctx 中的任务元信息），并写入摘要
    try:
        await session.clear_session()
    except Exception:
        # 即使清空失败也不抛异常，摘要仍可使用
        pass

    ctx.compaction_summary = summary
    return summary


def switch_model_prompt(ctx: TaskContext, old_model: str,
                        new_model: str) -> str:
    """生成多模型接管时的 next_input。"""
    return (
        f"新模型接管本题会话（{old_model} -> {new_model}）。"
        f"已连续 {ctx.zero_gain_turns} 轮没有产生新的关键证据，"
        "请回顾已有尝试和黑板记录，换种思路继续攻击本题容器，"
        "产出新的工具调用或新的发现。禁止重复已失败的路径。"
    )
