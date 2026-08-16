"""任务上下文：执行现场 + 渐进披露 + 全局状态，与任何具体靶场解耦。

TaskContext 通过 Runner.run(context=...) 注入，被工具（demo_tools）、hooks、
context_manager、agents_def、main 共用。独立成模块以解耦依赖、避免循环导入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class TaskContext:
    """通用任务上下文：只承载「执行现场 + 渐进披露」，与任何具体靶场解耦。"""
    workdir: Path
    disclosed_skills: List[str] = field(default_factory=list)  # 已披露技能（去重有序）
    skill_events: List[str] = field(default_factory=list)      # 渐进披露审计
    notes: List[str] = field(default_factory=list)             # 通用发现/备注
    finalized: bool = False                                    # 执行者是否已调用 finalize 终端动作
    final_payload: Dict[str, Any] = field(default_factory=dict)  # finalize 提交的最终结论
    empty_turns: int = 0                                       # 连续无工具调用且未 finalize 的轮数（判停用）
    turn_tool_count: int = 0                                   # 本轮已调用的工具次数（判停器用，每轮开始时清零）
    compaction_summary: str = ""                               # 历史压缩摘要（超阈值时把旧历史压成摘要，注入系统提示）
    # ---- checkpoint 持久化所需的任务元信息（Agent 主动存档用） ----
    task: str = ""                                            # 任务书原文
    charter: str = ""                                         # 使命宪章
    role: Dict[str, Any] = field(default_factory=dict)        # 派任的角色定义
    turn_count: int = 0                                       # 当前轮次（主循环每轮同步）
    vpn_connected: bool = False                               # 是否已后台启用 VPN（connect_vpn 幂等用）
    blackboard: Dict[str, Any] = field(default_factory=dict)  # 全局黑板：已完成事项 / 全局变量（每条含 value/status/ts/verified/evidence/supersedes）
    token_usage: Dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "total": 0, "requests": 0})  # 累计 token 用量
    last_prompt_tokens: int = 0                                  # 最近一次 LLM 请求的真实 prompt_tokens（压缩观测用，SDK 返回的 input_tokens）
    bruteforce_calls: int = 0                                    # 本题爆破/枚举类调用计数（成本治理，超 BRUTEFORCE_MAX_CALLS 拦截）
    submitted: Set[str] = field(default_factory=set)             # 已提交过的 flag（去重，铁律提交用）
    correct_flags: List[str] = field(default_factory=list)       # 已确认 correct 的 flag（多 flag 题进度）
    seen_signatures: Set[str] = field(default_factory=set)       # 已见路径/指纹签名（信息增量去重用）
    subtasks: List[Dict[str, Any]] = field(default_factory=list)  # 子任务队列 [{id, desc, status, result}]，主循环并发调度
    todos: List[Dict[str, Any]] = field(default_factory=list)      # 待办清单 [{id, title, status, priority, created_at, done_at}]，执行者自我管理用
    enabled_tools: Optional[Set[str]] = None  # 工具按需加载：None=全部启用；否则只启用集合内的工具名（见 demo_tools.CORE_TOOL_NAMES）
    phase: str = "recon"                      # 当前阶段（recon/enumerate/detect/exploit/post），驱动 instructions 动态切换
    plan: str = ""                            # 作战计划（Planner 深度分析产出，注入执行者系统提示）
    boosted_roles: List[str] = field(default_factory=list)  # 已注入的阶段增强角色（证据触发，去重用）
    role_boost: str = ""                      # 当前注入的阶段增强打法（下一轮 instructions 追加）
    stuck_turns: int = 0                      # 当前阶段连续未切换的轮数（replan 触发用）
    replan_count: int = 0                     # 已执行 replan 次数（防止无限重规划）
    turn_gain: bool = False                   # 本轮是否产出正向信息增量（hooks 打分，main 每轮清零）
    zero_gain_turns: int = 0                  # 连续零信息增量轮数（判停/replan 共用，>0 的正向增量才清零）
    current_code: str = ""                    # 当前正在攻打的题目 unique_code（提交铁律机械提交用）
    fatal: str = ""                           # 致命错误标记（task_ended/task_not_found），主循环检测后终止
    turn_net_fail: bool = False               # 本轮是否命中网络不可达（hooks 检测，main 每轮清零）
    net_fail_turns: int = 0                   # 连续网络不可达轮数（≥2 快速换题，防 VPN 死磕）
    # ---- 单题墙上时钟 + 提交错误熔断（高分作战硬约束） ----
    challenge_start_ts: float = 0.0           # 本题开始攻击的 monotonic 时间戳
    wallclock_budget: int = 0                 # 本题墙上时间预算（秒），由主循环按 difficulty 设置
    wrong_submit_count: int = 0               # 本题累计错误提交次数（≥3 全错则标记 stuck）
