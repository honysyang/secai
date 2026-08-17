"""三智能体的 Agent 定义与组装（通用版，与任何具体靶场解耦）。

管理者：意图识别 + 写使命宪章（事件触发）
执行者：按角色组装 instructions（常驻）；instructions 用动态函数，每轮从
        TaskContext.disclosed_skills 读取当前已披露技能，实现「多 Skills 渐进披露」
报告者：战报 + 死路蒸馏（事件触发）
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict, List

from agents import Agent, ModelSettings, RunContextWrapper

from adapters.config import MODEL, FAST_MODEL



from demo_tools import (ALL_TOOLS, finish_subtask, query_skills, list_knowledge,
                        get_knowledge, list_tools)
from arsenal.registries.skill_registry import load_skill_bodies
from runtime.status import PHASE_DEFS
from core.task_context import TaskContext

# 按角色拆分 ModelSettings：输出型 Agent 稳定低 temperature，探索型 Agent 略高；
# Strategist/Reporter 给更大 max_tokens 以输出完整宪章+计划/总结；
# Compactor 加量以保留详细约束与关键事实。
# Executor 在 exploit 阶段可开并行工具调用（V4 系对齐好，需环境变量显式启用）。
_EXECUTOR_PARALLEL = os.getenv("EXECUTOR_PARALLEL", "").lower() in ("1", "true", "yes")
STRATEGIST_SETTINGS = ModelSettings(temperature=0.15, max_tokens=8192, parallel_tool_calls=False)
EXECUTOR_SETTINGS = ModelSettings(temperature=0.3, max_tokens=4096, parallel_tool_calls=_EXECUTOR_PARALLEL)
REPORTER_SETTINGS = ModelSettings(temperature=0.2, max_tokens=8192, parallel_tool_calls=False)
COMPACTOR_SETTINGS = ModelSettings(temperature=0.1, max_tokens=4096, parallel_tool_calls=False)

# 保留兼容性兜底 SETTINGS
SETTINGS = ModelSettings(temperature=0.2, max_tokens=4096, parallel_tool_calls=False)


def intel_tools():
    """分析型智能体（Planner/Coach）的只读检索工具包：只查不改，不写黑板/状态。

    - query_skills / list_knowledge / get_knowledge / list_tools 均无副作用；
    - 执行者的 find_skills 会写 disclosed_skills（披露），这里不用它——分析型
      智能体不该写执行者的账本（权力分界铁约束）。
    """
    return [query_skills, list_knowledge, get_knowledge, list_tools]


# ================= 战略家（立法 + 深度分析 → 宪章 + 作战计划） =================
STRATEGIST_INSTRUCTIONS = """你是 SecAI 的战略家，负责在动手前一次性完成「立法 + 深度分析 + 制定作战计划」。

根据用户任务、目标信息、派任角色，输出 Markdown 文档，严格包含两大部分：

# 使命宪章
- 目标：可验证的完成判据（一句话说死）
- 关键原则：3~6 条（如：宁可判死不可空转；证据驱动不臆测；死路不重复）
- 约束：预算/时限/禁区/范围边界
- 终止判据：目标达成 + 证据枯竭（连续无信息增量/假设证伪）+ 资源时钟

# 作战计划
- 任务研判：目标类型、技术栈、最可能漏洞类型（1~3 个候选，按概率排序）。
  优先技术栈反推；每个候选必须给出「可验证假设」：即「假设存在 X 漏洞 → 用什么最小探测验证 → 预期正/负响应各是什么」。
- 攻击面预测：最可能的入口/参数/接口/文件（按可达性排序）
- flag 定位：flag 可能在哪（常见路径 / 数据库 / 环境变量 / 源码等）
- 分步计划：按 recon→enumerate→detect→exploit→post 列出具体步骤，每步必须含目标标识、唯一动作、成功标准
- 优先级与止损：先易后难、优先已 available 题目；拿到文件读取后直接读 flag 位置；关键路径必须提醒写黑板

纪律：
- 调用 query_skills / list_knowledge / get_knowledge / list_tools 等只读工具最多一次；
- 获取信息后必须立即产出宪章 + 计划，禁止反复调用同一工具空转；
- 只输出文档本身，不要寒暄。"""

strategist_agent = Agent(name="Strategist", instructions=STRATEGIST_INSTRUCTIONS,
                         tools=intel_tools(),
                         model=MODEL, model_settings=STRATEGIST_SETTINGS)


# 为保持旧代码/外部引用的兼容性，保留旧变量名（指向同一个 Agent 实例）
manager_agent = strategist_agent
planner_agent = strategist_agent


# ================= 执行者（静态系统提示 + 每轮动态上下文注入） =================
EXECUTOR_STATIC_INSTRUCTIONS = """你是 SecAI 的执行者，负责执行管理者立下的使命，产出可复用的证据与结论。

# 角色思维风格
{role_style}

# 工作纪律
1. 每轮必须产出至少一个新信息（证据增量），禁止空转与重复已失败方向。
2. 目标地址以任务书为准，禁止自猜；python3 脚本是主武器库。
3. 边渗透边记录（强制节奏）：每确认一条新认知（端口/版本/入口/认证态/漏洞点）
   立即写 blackboard 并附 evidence，不等会话结束或收尾，避免上下文压缩后丢细节；
   判死结论必须附证据，被证伪的旧结论用 supersedes 取代。
4. 批量探测（多 payload/路径/参数）一律用 fuzz / run_batch；互不依赖的动作用 parallel_shell；
   多个独立分支用 spawn_subtask。shell 只用于 fuzz 覆盖不了的场景。
5. 发现 flag 系统会机械代提交并回执：correct=true 且有剩余面数→继续找下一面；
   全部通关系统会自动结束本题。
6. 卡壳时：先 find_skills / list_knowledge 查打法；查不到现成打法也禁止停下，
   走第一性原理自己解决——技术栈反推 + 差异实验（distinguish/fuzz 找响应差异点）
   构造最小探测，直到拿到 flag 或凭证据判死；提示来了先深度分析再动手。
7. 拿到可复用攻击链后用 remember 沉淀 POC/知识/技能（只在真正有价值时）。
8. 阶段随进展用 set_phase 切换；任务完成或证据枯竭时调用 finalize 提交结论。
9. 确认漏洞/凭据/源码后立即沿最短路径拿 flag；系统注入的[闭环]指令优先级最高，按指令执行。

# 当前任务书
{brief}
"""


def _build_dynamic_context(ctx: RunContextWrapper[TaskContext], charter: str,
                           plan: str, field_notes: str, role_boost: str = "",
                           ledger_text: str = "") -> str:
    """组装每轮变化的动态上下文，作为 Runner.run input 的前置消息注入。

    静态系统提示只包含角色风格、工作纪律、任务书等不变内容；动态部分包含：
    当前阶段、宪章、作战计划、已解锁打法、历史档案、压缩摘要、黑板、阶段增强、
    exploit 阶段 payload 失败清单。
    这样同一个 Agent 实例可复用，SDK 不必每轮重建完整系统提示。
    """
    c = ctx.context
    playbooks_text = load_skill_bodies(c.disclosed_skills)
    phase = PHASE_DEFS.get(c.phase, PHASE_DEFS["recon"])
    parts = [
        ("# 当前阶段", f"""阶段：{c.phase}
目标：{phase['goal']}
当前焦点：{phase['focus']}
达成后切换：{phase.get('next', '')}
（目标达成后调用 set_phase 切到下一阶段；发现 flag 线索立即切 post；别在旧阶段空转）"""),
    ]
    # 破局指令：fork_analyst 一次性分析产出，优先级最高
    directive = ""
    nd = c.blackboard.get("next_directive")
    if isinstance(nd, dict) and nd.get("value"):
        directive = str(nd.get("value", "")).strip()
    if directive:
        parts.append(("# 破局指令（fork_analyst 给出，优先执行）", directive))
    parts.extend([
        ("# 使命宪章（管理者立法，必须遵守）", charter or "（无）"),
        ("# 作战计划（规划师深度分析，指导阶段推进）", plan or "（无：未规划）"),
        ("# 可用打法（随战况渐进披露，当前已解锁）",
         playbooks_text or "（暂无可用打法，先用通用侦察）"),
        ("# 历史作战档案", field_notes or "（无：首次执行）"),
        ("# 历史压缩摘要（超长对话压缩后保留的关键事实）", c.compaction_summary or "（无）"),
        ("# 全局黑板（已完成事项 / 全局变量，跨轮共享）", _format_blackboard(c.blackboard)),
    ])
    if c.plan_mode:
        parts.append(("# PLAN MODE 激活", """你当前处于 PLAN MODE。
本回合你只能输出/修正作战计划，禁止调用任何工具。
计划必须包含：假设、验证动作、预期结果、失败判据。
输出完成后系统会自动退出 PLAN MODE 并按新计划执行。"""))
    if role_boost:
        parts.append(("# 阶段增强（证据触发，随战况注入）", role_boost))
    if ledger_text:
        parts.append(("# exploit 差分基线", ledger_text))
    return "\n\n".join(f"{title}\n{body}" for title, body in parts)


EXECUTOR_DYNAMIC_PREFIX = "【动态上下文】\n"


def _format_blackboard(board: dict) -> str:
    """把黑板格式化成注入系统提示的精简摘要（只列 key/状态/验证标记/时间，value 仅 40 字符）。

    完整值由 Agent 按需用 blackboard get <key> 查询，避免把全部黑板值塞进每轮
    系统提示造成上下文膨胀。未验证条目会加「·未验证」标记，判死类条目展示证据。
    """
    if not board:
        return "（空）"
    lines = []
    for k, v in board.items():
        if isinstance(v, dict):
            value = str(v.get("value", ""))[:40]
            status = str(v.get("status", "")).strip()
            ts = v.get("ts", 0)
            when = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""
            verified = bool(v.get("verified", True))
            evidence = str(v.get("evidence", "")).strip()
            suffix = f" [{status}]" if status else ""
            if not verified:
                suffix += "·未验证"
            if evidence:
                suffix += f"（证据:{evidence[:30]}）"
            if when:
                suffix += f" ({when})"
            lines.append(f"- {k}: {value}{suffix}")
        else:
            # 兼容旧的纯字符串值
            lines.append(f"- {k}: {str(v)[:40]}")
    return "\n".join(lines) + "\n（黑板仅列摘要；取完整值用 blackboard get <key>）"


def _render_executor_instructions(ctx: RunContextWrapper[TaskContext],
                                  role: dict, brief: str) -> str:
    """渲染执行者静态系统提示（只包含角色风格、工作纪律、任务书）。"""
    return EXECUTOR_STATIC_INSTRUCTIONS.format(
        role_name=role["role"],
        role_style=role["style"],
        brief=brief,
    )


# ---------------------------------------------------------------------------
# Agent Preset 运行时组合：同一 build_executor 接口按场景动态叠加风格与工具
# ---------------------------------------------------------------------------
AGENT_PRESETS: Dict[str, Dict[str, Any]] = {
    "default": {
        "instructions_suffix": "",
        "extra_tools": [],
    },
    "recon_focused": {
        "instructions_suffix": (
            "\n# 侦察专精模式\n"
            "本阶段优先完成：服务识别、技术栈指纹、入口枚举、敏感路径发现。"
            "不要过早构造 exploit；所有发现必须写黑板并附证据。"),
        "extra_tools": [],
    },
    "exploit_focused": {
        "instructions_suffix": (
            "\n# 利用专精模式\n"
            "你已确认攻击面，本阶段只聚焦最小可利用链：构造稳定 PoC、验证 RCE/注入/越权、"
            "拿到 flag 后立即走 finalize。不要发散侦察。"),
        "extra_tools": [],
    },
    "analyst": {
        "instructions_suffix": (
            "\n# 分析型模式\n"
            "你只读不写：调用 query_skills / list_knowledge / get_knowledge / list_tools"
            "做情报分析，输出结论与下一步建议，禁止调用会改变状态或靶场的工具。"),
        "extra_tools": ["query_skills", "list_knowledge", "get_knowledge", "list_tools"],
    },
}


# 子任务结束协议：追加到执行者系统提示，要求结构化回传（而非只输出文字）
SUBTASK_ENDING = """

# 子任务输入前置条件（硬约束，先于执行）
你默认不拥有主 Agent 的完整上下文，仅以本次子任务描述为准。若描述缺少明确目标
（URL/IP:Port/具体路径）或范围边界、成功标准，禁止自行猜测目标或发起全量探索；
应直接调用 finish_subtask 返回「缺失信息清单」（目标、范围、成功标准、所需认证态），
等待主 Agent 补充后再执行。

# 子任务结束协议（必须遵守）
完成本子任务后，必须调用 finish_subtask 工具提交结构化结论（summary + findings + flag），
不要只输出一段文字。主 Agent 只看得到你提交的 summary/findings/flag，看不到你的过程，
所以 summary 必须自包含、写清结论；flag 没拿到就留空，禁止编造。"""


def _prompt_hash(text: str) -> str:
    """对静态 system prompt 源文本做 sha256，用于断言每轮字节级不变。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_executor(role: dict, charter: str, brief: str,
                   field_notes: str = "", model=None, model_settings=None,
                   is_subtask: bool = False,
                   preset: str = "default") -> Agent:
    """构建执行者。静态系统提示只含角色/纪律/任务书；动态上下文（阶段/计划/黑板/打法）
    每轮通过 Runner.run input 前置注入，实现动静分离，减少 SDK 每轮重建完整系统提示的开销。

    当 is_subtask=True 时追加子任务结束协议与 finish_subtask 工具，结果结构化回传，
    主 Agent 只拿到 summary/findings/flag，不接触子任务的海量工具输出。

    新增 preset 参数：运行时按场景组合 instructions 后缀与额外工具（如侦察/利用/分析师）。
    可通过 model/model_settings 注入模型池当前模型，支持灾备切换。
    默认使用 FAST_MODEL（deepseek-v4-flash），调用方传入 model 时覆盖。

    成本工程：静态指令 + 工具 schema 的 hash 存入 executor.static_prompt_hash，
    主循环每轮断言不变，确保 system prompt 字节级稳定，最大化 prompt cache 命中。
    """
    p = AGENT_PRESETS.get(preset, AGENT_PRESETS["default"])
    preset_suffix = p.get("instructions_suffix", "")
    subtask_suffix = ("\n\n" + SUBTASK_ENDING) if is_subtask else ""

    def _instructions(ctx: RunContextWrapper[TaskContext], agent: Agent) -> str:
        return _render_executor_instructions(ctx, role, brief) + preset_suffix + subtask_suffix

    # 静态指令 hash（不含每轮动态上下文），供主循环断言 system prompt 字节级稳定
    static_src = "\n".join([
        role.get("role", ""), role.get("style", ""), brief,
        preset_suffix, subtask_suffix,
    ])
    static_hash = _prompt_hash(static_src + "\n" +
                               ",".join(sorted(getattr(t, "name", "") for t in ALL_TOOLS)))

    extra_tools = []
    for tname in p.get("extra_tools", []):
        # 按名称从 ALL_TOOLS 查找对应 tool 对象
        for t in ALL_TOOLS:
            if getattr(t, "name", "") == tname:
                extra_tools.append(t)
                break
    tools = list(ALL_TOOLS) + extra_tools + ([finish_subtask] if is_subtask else [])
    name = f"Subtask[{role['role']}]" if is_subtask else f"Executor[{role['role']}]"
    if preset != "default":
        name += f"[{preset}]"
    executor = Agent(name=name, instructions=_instructions,
                     tools=tools, model=model or FAST_MODEL,
                     model_settings=model_settings or EXECUTOR_SETTINGS)
    # 挂静态 hash，供主循环每轮断言 system prompt 字节级稳定
    setattr(executor, "static_prompt_hash", static_hash)
    setattr(executor, "static_prompt_src", static_src)
    return executor


# 为保持旧代码/外部引用的兼容性，保留旧函数名（指向同一个函数）
build_subtask_executor = build_executor


# ================= 报告者 =================
REPORTER_INSTRUCTIONS = """你是 SecAI 的报告者，负责把执行过程翻译成人能看懂的中文。
输入是一次执行的事件流（JSON 行）与最终状态。输出两部分：
## 战报 —— 结果（达成/判死/超时）、关键链（哪几步是转折点）、关键结论与证据
## 死路蒸馏 —— 已证伪方向清单（每条一行：方向 + 为什么死），供下次接力注入
只输出这两节。不评价、不抒情、不建议。"""

reporter_agent = Agent(name="Reporter", instructions=REPORTER_INSTRUCTIONS,
                       model=MODEL, model_settings=REPORTER_SETTINGS)


# ================= 历史压缩器（上下文超阈值时调用） =================
COMPACTOR_INSTRUCTIONS = """你是对话历史的压缩器。输入包含「更早的历史摘要」和「需要并入的新历史」两段。
把两者合并成一份精炼的中文摘要，控制在 500 字以内，必须保留：
- 已完成的动作与关键发现（证据）
- 当前进度与下一步方向
- 已证伪的死路（避免重试）
- 未决问题
只输出摘要本身，不要寒暄。"""

compactor_agent = Agent(name="Compactor", instructions=COMPACTOR_INSTRUCTIONS,
                        model=MODEL, model_settings=COMPACTOR_SETTINGS)


# 卡壳教练已合并到 Strategist：当执行者卡壳时，由 _replan 顺带产出 1~2 条方向建议。
# 保留一个轻量提示函数，供 main.py 在不调用独立 Agent 时直接注入 next_input。
def coach_direction_prompt(blackboard_text: str, events_tail: str, skills_text: str) -> str:
    """不调用 LLM，直接返回硬提示模板，让执行者/Strategist 在 replan 时给出方向。"""
    return (
        f"当前黑板：\n{blackboard_text}\n\n"
        f"近期事件：\n{events_tail}\n\n"
        f"已解锁技能：{skills_text}\n\n"
        "请基于以上信息给出 1~2 条可验证的新方向（明确假设 + 最小验证动作 + 预期证据），"
        "优先使用已解锁技能或 payload 脚本库中的可执行脚本。禁止输出「继续尝试」等空话。"
    )
