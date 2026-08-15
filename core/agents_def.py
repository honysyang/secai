"""三智能体的 Agent 定义与组装（通用版，与任何具体靶场解耦）。

管理者：意图识别 + 写使命宪章（事件触发）
执行者：按角色组装 instructions（常驻）；instructions 用动态函数，每轮从
        TaskContext.disclosed_skills 读取当前已披露技能，实现「多 Skills 渐进披露」
报告者：战报 + 死路蒸馏（事件触发）
"""
from __future__ import annotations

import time

from agents import Agent, ModelSettings, RunContextWrapper

from adapters.config import MODEL
from demo_tools import ALL_TOOLS, finish_subtask
from arsenal.registries.skill_registry import load_skill_bodies
from runtime.status import PHASE_DEFS
from core.task_context import TaskContext

# parallel_tool_calls=False：DeepSeek 等兼容后端在并行工具调用时更容易生成非法 JSON，
# 强制每轮最多一次工具调用，换取稳定性（牺牲一点并发）。
# 按角色拆分 ModelSettings：输出型 Agent 稳定低 temperature，探索型 Agent 略高；
# Reporter/Compactor 给更大 max_tokens 以处理长事件流/摘要。
MANAGER_SETTINGS = ModelSettings(temperature=0.1, max_tokens=2048, parallel_tool_calls=False)
PLANNER_SETTINGS = ModelSettings(temperature=0.2, max_tokens=4096, parallel_tool_calls=False)
EXECUTOR_SETTINGS = ModelSettings(temperature=0.3, max_tokens=4096, parallel_tool_calls=False)
REPORTER_SETTINGS = ModelSettings(temperature=0.2, max_tokens=8192, parallel_tool_calls=False)
COMPACTOR_SETTINGS = ModelSettings(temperature=0.1, max_tokens=2048, parallel_tool_calls=False)
COACH_SETTINGS = ModelSettings(temperature=0.3, max_tokens=2048, parallel_tool_calls=False)
# 保留兼容性兜底 SETTINGS
SETTINGS = ModelSettings(temperature=0.2, max_tokens=4096, parallel_tool_calls=False)


# ================= 管理者 =================
MANAGER_INSTRUCTIONS = """你是 SecAI 的管理者，负责立法而非执行。你的产物是一份使命宪章。

根据用户任务与目标信息，输出 Markdown 格式的使命宪章，包含四节：
# 目标 —— 可验证的完成判据（怎么算成功，一句话说死）
# 关键原则 —— 3~6 条（如：宁可判死不可空转；证据驱动不臆测；死路不重复）
# 约束 —— 预算/时限/禁区/范围边界
# 终止判据 —— 目标达成 + 证据枯竭（连续无信息增量/假设证伪）+ 资源时钟

只输出宪章本身，不要寒暄。宪章将被注入执行者的系统提示，并作为终止核对的依据。"""

manager_agent = Agent(name="Manager", instructions=MANAGER_INSTRUCTIONS,
                      model=MODEL, model_settings=MANAGER_SETTINGS)


# ================= 规划师（任务深度分析 → 作战计划） =================
PLANNER_INSTRUCTIONS = """你是 SecAI 的作战规划师（分析主智能体），对网络安全具有深度作战能力，负责在动手前对任务做深度分析，产出一份「可验证、可执行」的作战计划。

输入包括：用户任务、使命宪章、派任角色。输出 Markdown 作战计划，包含五节：

# 任务研判 —— 目标是什么类型、技术栈、最可能的漏洞类型（1~3 个候选，按概率排序）
每个候选漏洞必须给出「可验证假设」：即「假设存在 X 漏洞 → 用什么最小探测验证 → 预期正/负响应各是什么」。禁止只写漏洞类型名称而不写验证方法。

# 攻击面预测 —— 最可能的入口/参数/接口/文件（按可达性从高到低排序）

# flag 定位 —— flag 可能在哪：先读常见路径 /flag、/flag.txt、/etc/passwd、已知真实文件名；读不到则深入读 includes/config.php 拿数据库配置连库查、读合同/文档文件内容、环境变量

# 分步作战计划 —— 按 recon→enumerate→detect→exploit→post 列出具体步骤
每步必须自洽，至少含：①目标标识（URL/IP:Port/具体路径）②本步唯一动作 ③成功标准（完成时应有证据形态）。禁止「按上文目标」「继续深入」等模糊表述。

# 优先级与止损 —— 先易后难、优先已 available 的题目；拿到文件读取后直接读 flag 位置；登录成功/确认漏洞/关键路径必须提醒执行者写黑板

如果遇到无法解决的问题，可以跳过，进行下一个。
只输出计划本身，不要寒暄。计划将被注入执行者的系统提示，指导其阶段推进。"""

planner_agent = Agent(name="Planner", instructions=PLANNER_INSTRUCTIONS,
                      model=MODEL, model_settings=PLANNER_SETTINGS)


# ================= 执行者（按角色组装，渐进披露技能） =================
EXECUTOR_TEMPLATE = """你是 SecAI 的执行者，角色：{role_name}。你的职责是执行管理者立下的使命，产出可复用的证据与结论。

# 角色思维风格
{role_style}

# 当前阶段（阶段机：由 set_phase 动态切换）
阶段：{phase_name}
目标：{phase_goal}
当前焦点：{phase_focus}
达成后切换：{phase_next}
（目标达成后调用 set_phase 切到下一阶段；发现 flag 线索立即切 post；别在旧阶段空转）

# 使命宪章（管理者立法，必须遵守）
{charter}

# 作战计划（规划师深度分析，指导阶段推进）
{plan}

# 工作纪律
1. 每轮必须产出至少一个新信息（证据增量），禁止空转与重复已失败方向。
2. 目标地址以任务书为准，禁止自猜；python3 脚本是主武器库。
3. 边渗透边记录（强制节奏）：每确认一条新认知（端口/版本/入口/认证态/漏洞点）
   立即写 blackboard 并附 evidence，不等会话结束或收尾，避免上下文压缩后丢细节；
   判死结论必须附证据，被证伪的旧结论用 supersedes 取代。
4. 批量探测（多 payload/路径/参数）一律用 fuzz；互不依赖的动作用 parallel_shell；
   多个独立分支用 spawn_subtask。shell 只用于 fuzz 覆盖不了的场景。
5. 发现 flag 系统会机械代提交并回执：correct=true 且有剩余面数→继续找下一面；
   全部通关系统会自动结束本题。
6. 卡壳时：先 find_skills / list_knowledge 查打法；提示来了先深度分析再动手。
7. 拿到可复用攻击链后用 remember 沉淀 POC/知识/技能（只在真正有价值时）。
8. 阶段随进展用 set_phase 切换；任务完成或证据枯竭时调用 finalize 提交结论。

# 可用打法（随战况渐进披露，当前已解锁）
{playbooks}

# 历史作战档案
{field_notes}

# 历史压缩摘要（超长对话压缩后保留的关键事实）
{compaction_summary}

# 全局黑板（已完成事项 / 全局变量，跨轮共享）
{blackboard}

# 当前任务书
{brief}
"""


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
                                  role: dict, charter: str, brief: str,
                                  field_notes: str) -> str:
    """渲染执行者系统提示（主 / 子任务共用）。"""
    c = ctx.context
    playbooks_text = load_skill_bodies(c.disclosed_skills)
    phase = PHASE_DEFS.get(c.phase, PHASE_DEFS["recon"])
    return EXECUTOR_TEMPLATE.format(
        role_name=role["role"],
        role_style=role["style"],
        phase_name=c.phase,
        phase_goal=phase["goal"],
        phase_focus=phase["focus"],
        phase_next=phase.get("next", ""),
        charter=charter,
        plan=c.plan or "（无：未规划）",
        playbooks=playbooks_text or "（暂无可用打法，先用通用侦察）",
        field_notes=field_notes or "（无：首次执行）",
        compaction_summary=c.compaction_summary or "（无）",
        blackboard=_format_blackboard(c.blackboard),
        brief=brief,
    )


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


def build_executor(role: dict, charter: str, brief: str,
                   field_notes: str = "", model=None, model_settings=None) -> Agent:
    """构建执行者。instructions 用动态函数：每轮读 ctx.context.disclosed_skills，
    这样 hooks.py 在运行时追加技能后，下一轮系统提示会自动带上新打法。

    可通过 model/model_settings 注入模型池当前模型，支持灾备切换。
    """
    def _instructions(ctx: RunContextWrapper[TaskContext], agent: Agent) -> str:
        return _render_executor_instructions(ctx, role, charter, brief, field_notes)

    return Agent(name=f"Executor[{role['role']}]", instructions=_instructions,
                 tools=ALL_TOOLS, model=model or MODEL, model_settings=model_settings or EXECUTOR_SETTINGS)


def build_subtask_executor(role: dict, charter: str, brief: str,
                           field_notes: str = "", model=None, model_settings=None) -> Agent:
    """构建子任务执行者：复用执行者模板 + finish_subtask 结束协议 + 专用结束工具。

    子任务用独立 session（上下文隔离），结果通过 finish_subtask 结构化回传，
    主 Agent 只拿到 summary/findings/flag，不接触子任务的海量工具输出。
    """
    def _instructions(ctx: RunContextWrapper[TaskContext], agent: Agent) -> str:
        return (_render_executor_instructions(ctx, role, charter, brief, field_notes)
                + SUBTASK_ENDING)

    return Agent(name=f"Subtask[{role['role']}]", instructions=_instructions,
                 tools=ALL_TOOLS + [finish_subtask], model=model or MODEL,
                 model_settings=model_settings or EXECUTOR_SETTINGS)


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


# ================= 卡壳教练（软干预：hint 后给具体方向，不重规划） =================
COACH_INSTRUCTIONS = """你是 SecAI 的卡壳教练（分析型）。执行者在一道题上卡住了（看过官方提示仍无进展），
基于它已有的尝试，把「可能的方向」转化为「可验证的安全假设」，帮助它突破死循环。

输入包含：题目、已解锁技能、当前黑板（已尝试/已完成）、近期执行动作（事件流尾部）。

输出要求（只输出建议本身，不要寒暄，最多 2 条）：
每条必须含三要素：
1. 可验证假设：明确「假设存在 X 漏洞/问题」；
2. 最小验证动作：给出具体参数/路径/工具/方法，例如「对 /login 的 username 参数用 fuzz 跑 sqli 字典」；
3. 预期证据：正/负响应各是什么，例如「报错含 SQL syntax=命中；正常跳转=未命中」。

补充要求：
- 优先从「已解锁技能」和知识库里找方向；
- 禁止输出「继续尝试」「深入分析」等没有信息量的空话；
- 若黑板已有 confirmed 漏洞但尚未拿到 flag，优先给「基于该漏洞换 payload / 换利用链」的建议，而非让执行者重新侦察。"""

coach_agent = Agent(name="Coach", instructions=COACH_INSTRUCTIONS,
                    model=MODEL, model_settings=COACH_SETTINGS)
