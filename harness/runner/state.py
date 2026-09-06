"""RunnerState：单题执行循环的显式状态。

pre/step/post 闭包间穿梭的 cell 变量（switched / outcome / death_reason /
intervention_count / hint_used / coach_used / turn_count / next_input 等）全部
提升为本数据类字段，ExecutorLoop 只持有 RunnerState + 注入依赖，即可用 fake
clock/scorer/model_pool 直驱单测。

骨架字段 phase/steps/budget/seq 对齐执行计划 v4 的 RunnerState 规格：
phase 标记 run() 当前所处阶段（pre → step → post），steps 记录已驱动轮数，
budget 为轮数硬顶（0 = 无硬顶，真实终止依赖 _pre/_post 熔断条件），
seq 为轮次/事件序号锚点（观测与回放用）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Phase = Literal["pre", "step", "post"]


@dataclass
class RunnerState:
    # ── 阶段机导航（骨架对齐） ──────────────────────────────────────────
    phase: Phase = "pre"       # run() 当前所处阶段：pre → step → post
    steps: int = 0             # 已驱动轮数（与 turn_count 同值，供 while 消费）
    budget: int = 0            # 轮数硬顶，0 = 无（真实熔断由 _pre/_post 完成）
    seq: int = 0               # 轮次/事件序号锚点（每完成一轮 step +1）

    # ── 闭包 cell 变量转字段（等价变换，零业务改动） ──────────────────
    switched: bool = False         # pre：token 到换脑档且已切换模型
    outcome: str = "stopped"       # 终态：solved / stuck / fatal
    death_reason: str = ""         # 六种死法终态标签（赛后分析口径）
    intervention_count: int = 0    # post：自救+切换+hint+coach+replan 累计干预
    hint_used: bool = False        # post：已看过平台 hint（每题一次）
    coach_used: bool = False       # post：教练软干预每题目仅一次
    turn_count: int = 0            # 主循环轮次计数（run() 每轮 +1 并同步 ctx.turn_count）
    next_input: str = ""           # 注入 Agent 的下一轮指令（setup 首设，post 改写）
