"""任务上下文：执行现场 + 渐进披露 + 全局状态，与任何具体靶场解耦。

TaskContext 通过 Runner.run(context=...) 注入，被工具、hooks、
context_manager、agents_def 共用。独立成模块以解耦依赖、避免循环导入。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SubtaskBudget:
    """子任务独立预算与回收闸门。"""
    objective: str = ""                        # 明确目标（必填）
    max_tokens: int = 0                        # token 预算上限，0=不限制
    max_turns: int = 8                         # 回合预算上限
    timeout_seconds: float = 600.0             # 墙上时间预算
    used_tokens: int = 0                       # 已用 token（由主循环/ hooks 累计）
    used_turns: int = 0                        # 已用回合
    cancelled: bool = False                    # 是否已被取消
    reason: str = ""                           # 终态原因（completed / timeout / budget / cancelled / parent_stop）


@dataclass
class L5GuardrailConfig:
    """R3 L5 护栏运行配置（挂到 TaskContext.l5_guardrail；None = 护栏关闭，旧模式透传）。

    字段均为鸭子类型（避免 core → pentest/sandbox 的静态依赖）：
    - scope: pentest.scope.ScopeConstraint | None   —— 授权范围守卫（越范围硬拦截）
    - policy: sandbox.policy.Policy | None          —— 命令分级策略（默认 workspace-write）
    - backend: sandbox.SandboxBackend | None        —— 沙箱后端（None = 按 fail-closed 处理）
    - approval: pentest.approval.ApprovalGate | None —— T3 人工审批门
    对应执行链：ScopeCheck → sandbox.confine → (T3) ApprovalGate → 执行。
    """
    task_id: str = ""                # 审计维度（事件总线 task_id / engagement）
    scope: Any = None
    target: str = ""                 # 会话绑定目标（参数无可解析目标时的兜底检查）
    policy: Any = None
    backend: Any = None
    approval: Any = None


@dataclass
class TaskContext:
    """通用任务上下文：只承载「执行现场 + 渐进披露」，与任何具体靶场解耦。"""
    workdir: Path
    disclosed_skills: list[str] = field(default_factory=list)  # 已披露技能（去重有序）
    skill_events: list[str] = field(default_factory=list)      # 渐进披露审计
    notes: list[str] = field(default_factory=list)             # 通用发现/备注
    finalized: bool = False                                    # 执行者是否已调用 finalize 终端动作
    final_payload: dict[str, Any] = field(default_factory=dict)  # finalize 提交的最终结论
    empty_turns: int = 0                                       # 连续无工具调用且未 finalize 的轮数（判停用）
    turn_tool_count: int = 0                                   # 本轮已调用的工具次数（判停器用，每轮开始时清零）
    compaction_summary: str = ""                               # 历史压缩摘要（超阈值时把旧历史压成摘要，注入系统提示）
    # ---- checkpoint 持久化所需的任务元信息（Agent 主动存档用） ----
    task: str = ""                                            # 任务书原文
    charter: str = ""                                         # 使命宪章
    role: dict[str, Any] = field(default_factory=dict)        # 派任的角色定义
    turn_count: int = 0                                       # 当前轮次（主循环每轮同步）
    vpn_connected: bool = False                               # 是否已后台启用 VPN（connect_vpn 幂等用）
    blackboard: dict[str, Any] = field(default_factory=dict)  # 全局黑板：已完成事项 / 全局变量（每条含 value/status/ts/verified/evidence/supersedes）
    token_usage: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "total": 0, "requests": 0})  # 累计 token 用量
    last_prompt_tokens: int = 0                                  # 最近一次 LLM 请求的真实 prompt_tokens（压缩观测用，SDK 返回的 input_tokens）
    bruteforce_calls: int = 0                                    # 爆破/枚举类调用计数（成本治理，超 BRUTEFORCE_MAX_CALLS 拦截）
    seen_signatures: set[str] = field(default_factory=set)       # 已见路径/指纹签名（信息增量去重用）
    subtasks: list[dict[str, Any]] = field(default_factory=list)  # 子任务队列 [{id, desc, branch_type, status, result}]，主循环并发调度
    subtask_jobs: dict[str, asyncio.Task] = field(default_factory=dict)  # 已后台化的子任务 id -> asyncio.Task
    todos: list[dict[str, Any]] = field(default_factory=list)      # 待办清单 [{id, title, status, priority, created_at, done_at}]，执行者自我管理用
    enabled_tools: set[str] | None = None  # 工具按需加载：None=全部启用；否则只启用集合内的工具名
    phase: str = "recon"                      # 当前阶段（recon/enumerate/detect/exploit/post），驱动 instructions 动态切换
    plan: str = ""                            # 作战计划（Planner 深度分析产出，注入执行者系统提示）
    boosted_roles: list[str] = field(default_factory=list)  # 已注入的阶段增强角色（证据触发，去重用）
    role_boost: str = ""                      # 当前注入的阶段增强打法（下一轮 instructions 追加）
    replan_count: int = 0                     # 已执行 replan 次数（防止无限重规划）
    turn_gain: bool = False                   # 本轮是否产出正向信息增量（hooks 打分，主循环每轮清零）
    zero_gain_turns: int = 0                  # 连续零信息增量轮数（判停/replan 共用，>0 的正向增量才清零）
    fatal: str = ""                           # 致命错误标记，主循环检测后终止
    turn_net_fail: bool = False               # 本轮是否命中网络不可达（hooks 检测，主循环每轮清零）
    net_fail_turns: int = 0                   # 连续网络不可达轮数（≥2 快速换目标，防 VPN 死磕）
    # ---- Plan Mode 二态开关（进入时只输出/更新计划，不执行工具） ----
    plan_mode: bool = False
    # ---- 动态上下文增量注入状态（charter/plan 版本化 + field_notes 仅首轮） ----
    injected_signatures: dict[str, str] = field(default_factory=dict)  # 已全量注入的内容签名（charter/plan → sha256[:16]）
    injected_versions: dict[str, int] = field(default_factory=dict)    # 已注入的版本号（每次内容变化 +1）
    field_notes_injected: bool = False                                 # 历史作战档案是否已注入（仅首轮注入）
    # ---- exploit 阶段 payload 台账（差分基线纪律，避免重复同一失败变体） ----
    payload_ledger: list[dict[str, Any]] = field(default_factory=list)  # [{target, signature, hit, count, last_text_hash}]
    # ---- 强弱模型分工：强模型（破局）每任务最多 STRONG_MODEL_MAX_TURNS 轮 ----
    strong_model_uses: int = 0            # 已用强模型轮数（主循环计数，超限切回快模型）
    _on_strong_model: bool = False        # 当前是否处于强模型接管状态
    # ---- R3 L5：护栏运行配置（None = 关闭；设置后 tool_pipeline 的 L5 中间件生效） ----
    l5_guardrail: Any = None       # L5GuardrailConfig | None


# 模块级常量：每任务同时运行的后台子任务上限（避免无界增长拖死 harness）
SUBTASK_MAX_CONCURRENT = 2
