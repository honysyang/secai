"""三智能体的 Agent 定义与组装（通用版，与任何具体靶场解耦）。

管理者：意图识别 + 写使命宪章（事件触发）
执行者：按角色组装 instructions（常驻）；instructions 用动态函数，每轮从
        TaskContext.disclosed_skills 读取当前已披露技能，实现「多 Skills 渐进披露」
报告者：战报 + 死路蒸馏（事件触发）
"""
from __future__ import annotations

import time

from agents import Agent, ModelSettings, RunContextWrapper

from config import MODEL
from demo_tools import ALL_TOOLS, finish_subtask
from skill_registry import load_skill_bodies
from status import PHASE_DEFS
from task_context import TaskContext

# parallel_tool_calls=False：DeepSeek 等兼容后端在并行工具调用时更容易生成非法 JSON，
# 强制每轮最多一次工具调用，换取稳定性（牺牲一点并发）。
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
                      model=MODEL, model_settings=SETTINGS)


# ================= 规划师（任务深度分析 → 作战计划） =================
PLANNER_INSTRUCTIONS = """你是 SecAI 的作战规划师，对网络安全具有深度的作战能力，负责在动手前对任务做深度分析，产出一份可执行的作战计划。

输入包括：用户任务、使命宪章、派任角色。输出 Markdown 作战计划，包含四节：
# 任务研判 —— 目标是什么类型、技术栈、最可能的漏洞类型（1~3 个候选，按概率排序）
# 攻击面预测 —— 最可能的入口/参数/接口/文件
# flag 定位 —— flag 可能在哪：先读常见路径 /flag、/flag.txt、/etc/passwd、已知真实文件名；读不到则深入读 includes/config.php 拿数据库配置连库查、读合同/文档文件内容、环境变量
# 分步作战计划 —— 按 recon→enumerate→detect→exploit→post 列出具体步骤，每步一句话

如果遇到无法解决的问题，可以跳过，进行下一个。
只输出计划本身，不要寒暄。计划将被注入执行者的系统提示，指导其阶段推进。
特别注意：先易后难、优先已 available 的题目、禁止猜臆造文件名、拿到任意文件读取后直接读 flag 位置；登录成功/确认漏洞/关键路径必须提醒执行者写黑板。"""

planner_agent = Agent(name="Planner", instructions=PLANNER_INSTRUCTIONS,
                      model=MODEL, model_settings=SETTINGS)


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
1. 每轮必须产出至少一个新信息（证据增量），禁止空转。
2. 目标地址以任务书为准，禁止自猜。
3. 禁止重复已失败的方向（历史作战档案全是已证伪死路）。
4. 没有现成工具就自己写 Python 脚本——shell 里的 python3 是主武器库。
5. 重要发现/已完成事项/全局变量写入 blackboard（用 blackboard set），不要依赖记忆；**登录成功（含 session/cookie 路径）、确认漏洞类型、关键文件/flag 路径 等关键进展必须写黑板**——上下文压缩后靠黑板恢复记忆，不写会忘导致重复劳动。写「失败/排除」类结论时：要判死（禁止重做）必须附 evidence，否则默认只是未验证线索；旧结论被证伪时用 supersedes 指向旧 key 取代。
6. 当你认为任务已完成（目标达成或证据枯竭），必须调用 finalize 工具提交结论，而不是只输出一段文字。
7. 遇到不熟悉的场景，先调用 find_skills 检索技能库，看有没有对应打法。
8. 如果收到提示，那么要对提示进行深度分析和思考。
9. 拿到 flag / 确认漏洞 / 形成可复用攻击链后，调用 remember 工具把「战果」沉淀为 POC（kind=poc）/ 知识（kind=knowledge）/ 技能（kind=skill），让下次遇到同类题直接复用；只在真正有价值时沉淀，不必每轮都做。
10. 任务目标若指向远程内网/靶场（需走 VPN 才能访问目标），先调用 connect_vpn 启用 VPN 再开始探测；连接失败就如实报告，不要反复硬连。
11. 互不依赖的探测（路径爆破/端口扫描/多 payload 测试）用 parallel_shell 并发执行，不要串行一个个来。
12. 不熟悉的后利用/绕过场景，先 list_knowledge 看知识库简介，再用 get_knowledge 按 id 取全文。
13. 根据当前进展调用 set_phase 切换阶段（recon=侦察 / enumerate=枚举 / detect=检测 / exploit=利用 / post=后利用拿flag）。阶段目标达成或方向改变时及时切换，别在旧阶段空转。
14. 当任务同时出现多个互不依赖的探测分支（多个端口/子目标/漏洞点）时，用 spawn_subtask 分别声明子任务，让系统并发调度；不要自己一个个串行做。
15. 部分工具默认未挂载（按需加载）。需要时先调用 list_disabled_tools 查看有哪些未挂载的工具/工具组，再用 enable_tool 启用（如 enable_tool knowledge / poc / vuln / web / seccli）；启用后即可正常调用。不要因为工具暂时不可见就判定其不存在。
16. 做参数/路径/注入点的批量探测（fuzz）时，优先用 fuzz 工具（代码并发 + 响应差分归组），不要用 shell 手写循环逐个 curl——fuzz 一次跑完并自动按响应差异分组，又快又省上下文。request_template 用 JSON，在待测位置放 {{FUZZ}} 占位；payload_type 可用内置字典（sqli/lfi/path/xss/ssti/rce/idor/upload/xxe），或 payloads 传逗号分隔列表 / 数值范围（如 1-100）。shell 只用于 fuzz 覆盖不了的场景（登录、读单个已知文件、跑现成 CLI）；凡是「批量试多个 payload/路径」一律用 fuzz，禁止 shell 手写循环逐个试。

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

# 子任务结束协议（必须遵守）
完成本子任务后，必须调用 finish_subtask 工具提交结构化结论（summary + findings + flag），
不要只输出一段文字。主 Agent 只看得到你提交的 summary/findings/flag，看不到你的过程，
所以 summary 必须自包含、写清结论；flag 没拿到就留空，禁止编造。"""


def build_executor(role: dict, charter: str, brief: str,
                   field_notes: str = "") -> Agent:
    """构建执行者。instructions 用动态函数：每轮读 ctx.context.disclosed_skills，
    这样 hooks.py 在运行时追加技能后，下一轮系统提示会自动带上新打法。"""
    def _instructions(ctx: RunContextWrapper[TaskContext], agent: Agent) -> str:
        return _render_executor_instructions(ctx, role, charter, brief, field_notes)

    return Agent(name=f"Executor[{role['role']}]", instructions=_instructions,
                 tools=ALL_TOOLS, model=MODEL, model_settings=SETTINGS)


def build_subtask_executor(role: dict, charter: str, brief: str,
                           field_notes: str = "") -> Agent:
    """构建子任务执行者：复用执行者模板 + finish_subtask 结束协议 + 专用结束工具。

    子任务用独立 session（上下文隔离），结果通过 finish_subtask 结构化回传，
    主 Agent 只拿到 summary/findings/flag，不接触子任务的海量工具输出。
    """
    def _instructions(ctx: RunContextWrapper[TaskContext], agent: Agent) -> str:
        return (_render_executor_instructions(ctx, role, charter, brief, field_notes)
                + SUBTASK_ENDING)

    return Agent(name=f"Subtask[{role['role']}]", instructions=_instructions,
                 tools=ALL_TOOLS + [finish_subtask], model=MODEL, model_settings=SETTINGS)


# ================= 报告者 =================
REPORTER_INSTRUCTIONS = """你是 SecAI 的报告者，负责把执行过程翻译成人能看懂的中文。
输入是一次执行的事件流（JSON 行）与最终状态。输出两部分：
## 战报 —— 结果（达成/判死/超时）、关键链（哪几步是转折点）、关键结论与证据
## 死路蒸馏 —— 已证伪方向清单（每条一行：方向 + 为什么死），供下次接力注入
只输出这两节。不评价、不抒情、不建议。"""

reporter_agent = Agent(name="Reporter", instructions=REPORTER_INSTRUCTIONS,
                       model=MODEL, model_settings=SETTINGS)


# ================= 历史压缩器（上下文超阈值时调用） =================
COMPACTOR_INSTRUCTIONS = """你是对话历史的压缩器。输入包含「更早的历史摘要」和「需要并入的新历史」两段。
把两者合并成一份精炼的中文摘要，控制在 500 字以内，必须保留：
- 已完成的动作与关键发现（证据）
- 当前进度与下一步方向
- 已证伪的死路（避免重试）
- 未决问题
只输出摘要本身，不要寒暄。"""

compactor_agent = Agent(name="Compactor", instructions=COMPACTOR_INSTRUCTIONS,
                        model=MODEL, model_settings=SETTINGS)
