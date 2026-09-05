"""ExecutorLoop：单题执行循环的类化实现（原 app/main.py `_run_single_challenge` 等价变换）。

原实现把「pre/step/post」写成三个内嵌 async 闭包，通过 nonlocal 捕获
switched / outcome / death_reason / intervention_count / hint_used / coach_used /
turn_count / next_input 在闭包间穿梭，无法脱离 main 测试。本模块将其变换为：

- RunnerState（harness/runner/state.py）：全部 nonlocal cell 变量 → 数据类字段；
- ExecutorLoop：循环控制 + _pre_step/_step/_post_step 三个可直驱方法；
- run_single_challenge()：保留原 _run_single_challenge 的对外签名与 setup 流程
  （工作区/派任/黑板回注/工具裁剪/first_strike/缓存观测/executor 与 session 构建），
  组装依赖后交给 ExecutorLoop.run()。

等价变换红线：零业务改动。run() 的主循环、熔断、干预、子任务调度/收割、
清理与收尾报告流程与原 main.py 逐行对应。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from agents import RunContextWrapper, Runner
from agents.exceptions import MaxTurnsExceeded
from agents.memory import SQLiteSession

import adapters.db as db_mod
import runtime.stuck as stuck_mod
from adapters.config import FAST_MODEL_NAME
from arsenal.registries.role_registry import assign_role
from bench_platform.platform_client import PlatformClient
from bench_platform.scheduler import SINGLE_EMPTY_TURNS, decide_stuck_action
from core.agents_def import EXECUTOR_DYNAMIC_PREFIX, _build_dynamic_context, _prompt_hash, build_executor
from core.context_manager import compact_if_needed
from core.hooks import EventStreamHooks, _flush_emit_buffer, _ledger_failed_summary
from core.task_context import TaskContext
from demo_tools import build_default_tools
from harness.runner.context import (
    FIELD_NOTES_FILE,
    _append_mechanical_note,
    _coach,
    _load_blackboard,
    _load_field_notes,
    _merge_subtask_intel,
    _replan,
    load_notes_for,
)
from harness.runner.state import RunnerState
from harness.runner.subtasks import _cancel_all_subtasks, _reap_subtasks, _run_subtasks
from runtime.budget import (
    COST_LIMITS,
    HINT_BUDGET_RATIO,
    HINT_GRACE_TURNS,
    MAX_STUCK_INTERVENTIONS,
    SUSPEND_SECONDS,
    WALLCLOCK_BUDGET,
    should_pull_hint_by_budget,
)
from runtime.log import log_error, log_info, log_warn
from runtime.model_pool import ModelPool, is_model_failure, is_permanent_model_failure
from runtime.reporting import export_trajectory, first_strike, write_cost_report
from runtime.stuck import StuckActionType, StuckDetector, compact_session
from solvecraft.solution_templates import append_solution_template, load_solution_hint

ZERO_GAIN_REPLAN_TURNS = 3  # 连续零信息增量轮数触发 fork_analyze（对齐指南：3 轮即破局分析）
REPLAN_MAX = 1              # 破局链收敛为两级（R1）：只 fork_analyze 复盘一次，再 3 轮零增量即机械换题
COACH_AFTER_HINT_TURNS = 3  # hint 后仍零增益 3 轮触发软干预教练（默认关闭，R1 收敛）
# R1：coach / plan-mode 软干预默认关闭（代码保留，赛后用日志对比决定是否复活）
ENABLE_COACH = os.getenv("ENABLE_COACH", "false").lower() in ("1", "true", "yes")
ENABLE_PLAN_MODE = os.getenv("ENABLE_PLAN_MODE", "false").lower() in ("1", "true", "yes")
STRONG_MODEL_MAX_TURNS = 3  # 强模型（破局）每题目最多轮数，超限切回快模型
# 单题「自救+切换模型+hint+replan」累计干预上限见 runtime.budget.MAX_STUCK_INTERVENTIONS（B4 收口）


class _SystemClock:
    """默认时间源（真实运行）；测试可注入 fake clock 控制时间流逝。"""

    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()


def _max_interventions(difficulty: str) -> int:
    """按难度返回累计干预上限，未知难度默认 medium。"""
    return MAX_STUCK_INTERVENTIONS.get(str(difficulty).lower(),
                                       MAX_STUCK_INTERVENTIONS["medium"])


def _tool_groups_for(role_name: str, desc: str) -> tuple:
    """按题型返回初始工具组，减少无关工具干扰（配合 build_default_tools）。

    原则：核心工具常驻；平台编排/VPN 始终保留；二进制/协议/Pwn 题不挂 web 组
    （distinguish/web_search 对二进制帮助有限），其余题型挂 web 组做差分实验。
    """
    text = f"{role_name or ''} {desc or ''}".lower()
    groups = ["platform", "vpn", "seccli"]  # 平台编排 + VPN + 安全 CLI（run_tool）
    if any(k in text for k in ("二进制", "协议", "pwn", "reverse", "逆向", "f1", "f2")):
        return tuple(groups)  # 二进制/协议题：去掉 web 组，避免差分实验/联网干扰
    groups.append("web")       # Web/通用题：distinguish + web_search
    return tuple(groups)


class ExecutorLoop:
    """单题完整渗透循环：_pre_step → (_step)* → _post_step → 清理/收尾。

    依赖全部构造注入（state/clock/events/scorer/tools/model_pool/hooks/client），
    单测可用 fake 替身直驱 _pre_step/_step/_post_step 断言状态迁移与事件，
    无需真实 LLM/平台/时间。
    """

    def __init__(
        self,
        *,
        # 显式状态与执行现场
        state: RunnerState,
        ctx: TaskContext,
        code: str,
        desc: str,
        brief: str,
        charter: str,
        task: str,
        global_plan: str,
        role: dict,
        field_notes: str,
        challenge_workdir: Path,
        sessions_dir: Path,
        session,
        executor,
        # 编排依赖（注入替身即得可测回路）
        model_pool,
        client: PlatformClient,
        hooks,                  # 题级事件 hooks（事件发射/落盘出口）
        outer_hooks=None,       # 外层 generic hooks（fork_analyst 读事件历史用）
        difficulty: str = "",
        db=None,
        clock=None,             # 时间源：.monotonic()/.time()，默认标准库
        events=None,            # 事件总线（保留注入点；当前事件经 hooks 出口）
        scorer=None,            # 打分器（保留注入点；当前打分内嵌 hooks）
        tools=None,             # 工具集（保留注入点；当前工具挂 executor）
    ):
        self.state = state
        self.ctx = ctx
        self.code = code
        self.desc = desc
        self.brief = brief
        self.charter = charter
        self.task = task
        self.global_plan = global_plan
        self.role = role
        self.field_notes = field_notes
        self.challenge_workdir = challenge_workdir
        self.sessions_dir = sessions_dir
        self.session = session
        self.executor = executor
        self.model_pool = model_pool
        self.client = client
        self.hooks = hooks
        self.outer_hooks = outer_hooks
        self.difficulty = difficulty
        self.db = db

        # 注入依赖（带默认实现）
        self.clock = clock if clock is not None else _SystemClock()
        self.events = events          # 保留：事件总线（默认 None → 事件走 hooks）
        self.scorer = scorer          # 保留：打分器（默认 None → 打分走 hooks）
        self.tools = tools            # 保留：工具集（默认 None → 走 executor.tools）

        # 模型惰性检测器（多模型切换 / 单模型自救）
        self.stuck_detector = StuckDetector()

        # 成本治理：token/时钟起点 + 换脑/挂起档（COST_LIMITS 按难度分档，原 main setup 逻辑）
        cost_limit = COST_LIMITS.get(str(difficulty).lower(), COST_LIMITS.get("medium", {}))
        self.switch_tokens = cost_limit.get("switch_tokens", 0)
        self.suspend_tokens = cost_limit.get("suspend_tokens", 0)
        self.cost_base_tokens = ctx.token_usage.get("total", 0)
        self.suspend_time_base = self.clock.monotonic()
        self.suspend_tokens_map = {d: v.get("suspend_tokens", 0)
                                   for d, v in COST_LIMITS.items()}

    # ------------------------------------------------------------------
    # 主入口：复刻原 _run_single_challenge 的 while 主循环 + 清理 + 收尾
    # ------------------------------------------------------------------
    async def run(self) -> str:
        """执行完整单题循环，返回 outcome：solved / stuck / fatal。

        与原实现一致：try 内的 while 循环按 break 条件退出后，finally 统一回收
        子任务/冲刷事件/清理 session；随后做机械沉淀与报告收尾并返回 outcome。
        异常路径：while 内未捕获异常 → finally 清理后向外传播（与原 finally 语义一致）。
        """
        try:
            while True:
                self.state.phase = "pre"
                self.state.turn_count += 1
                self.ctx.turn_count = self.state.turn_count
                self.state.steps = self.state.turn_count
                should_break, dr = await self._pre_step()
                if should_break:
                    self.state.outcome = "stuck" if dr != "solved" else "solved"
                    self.state.death_reason = dr
                    break
                self.state.phase = "step"
                step_continue = await self._step()
                if not step_continue:
                    if self.state.death_reason == "model_exhausted":
                        break
                    continue  # 模型 fallback：同一输入下一轮重试
                self.state.phase = "post"
                self.state.seq += 1  # 完成一轮 step，轮次序号 +1
                should_break, self.state.next_input, self.state.death_reason = await self._post_step()
                if should_break:
                    if self.state.death_reason == "solved":
                        self.state.outcome = "solved"
                    else:
                        self.state.outcome = "stuck"
                    break
        finally:
            await self._cleanup()
        return self._finish()

    # ------------------------------------------------------------------
    # _pre_step：每轮 step 前检查硬性终止条件、重置 turn 状态、阶段/模型升级
    # ------------------------------------------------------------------
    async def _pre_step(self) -> tuple:
        """返回 (break_flag, death_reason)。break_flag=True 时外层应终止单题循环。"""
        # ── 静态 prompt 字节级断言（缓存防线关门） ─────────────────────
        # build_executor 时计算的 hash 必须全赛程不变；变了说明静态模板
        # 被每轮变量污染，前缀缓存已断，必须立刻暴露而不是默默烧钱。
        _src_now = getattr(self.executor, "static_prompt_src", None)
        _hash_expect = getattr(self.executor, "static_prompt_hash", None)
        if _src_now is not None and _hash_expect is not None:
            _hash_now = _prompt_hash(_src_now + "\n" + ",".join(
                sorted(getattr(t, "name", "") for t in self.executor.tools)))
            if _hash_now != _hash_expect:
                log_error(f"[cache-guard] 单题 {self.code} 静态 prompt hash 漂移："
                          f"{_hash_expect} -> {_hash_now}，前缀缓存已断！"
                          f"检查是否有人往静态模板拼了每轮变量。")
                self.ctx.cache_guard_violations = getattr(
                    self.ctx, "cache_guard_violations", 0) + 1
        # ── 断言结束 ─────────────────────────────────────────────────

        # 墙上时钟硬顶：单题超时强制 stuck，释放槽位
        elapsed = self.clock.monotonic() - self.ctx.challenge_start_ts
        if elapsed >= self.ctx.wallclock_budget:
            if self.ctx.zero_gain_turns < 5 and not getattr(self.ctx, "_wallclock_extended", False):
                self.ctx._wallclock_extended = True
                self.ctx.wallclock_budget += self.ctx.wallclock_budget // 2
                log_info(f"[extend] 单题 {self.code} 有进展，墙钟延长半档至 {self.ctx.wallclock_budget}s")
            else:
                log_warn(f"[skip] 单题 {self.code} 墙上时间 {elapsed:.0f}s 超过预算 {self.ctx.wallclock_budget}s，机械换题")
                return True, "wallclock_timeout"

        # 错误提交熔断
        if self.ctx.wrong_submit_count >= 6 and self.ctx.zero_gain_turns >= 3:
            log_warn(f"[skip] 单题 {self.code} 连续 {self.ctx.wrong_submit_count} 次错交且无新证据，机械换题")
            return True, "wrong_submit_fuse"

        # 成本治理：token / 时钟挂起档
        used = self.ctx.token_usage.get("total", 0) - self.cost_base_tokens
        if (self.switch_tokens and not self.state.switched and used >= self.switch_tokens
                and self.model_pool.has_alternative):
            entry = self.model_pool.next(reason="token_threshold")
            if entry is not None:
                old = getattr(self.executor.model, "model", "?")
                self.executor.model = entry.model
                self.state.switched = True
                log_warn(f"[switch] 单题 {self.code} token {used} 到换脑档，{old} -> {entry.name}")
        if self.suspend_tokens and used >= self.suspend_tokens:
            return True, "token_suspend"
        if SUSPEND_SECONDS and self.clock.monotonic() - self.suspend_time_base >= SUSPEND_SECONDS:
            return True, "time_suspend"

        # turn 状态清零
        self.ctx.turn_tool_count = 0
        self.ctx.turn_gain = False
        self.ctx.turn_net_fail = False

        # 攻坚换强脑：进入 exploit 阶段后切换到 strong 模型，并开启并行工具调用
        if (self.ctx.phase == "exploit" and not getattr(self.ctx, "_brain_upgraded", False)
                and self.model_pool is not None):
            strong_entry = self.model_pool.switch_to_role("strong")
            if strong_entry is not None:
                old = getattr(self.executor.model, "model", "?")
                self.executor.model = strong_entry.model
                self.executor.model_settings.parallel_tool_calls = True
                self.ctx._brain_upgraded = True
                self.ctx._on_strong_model = True
                self.ctx.strong_model_uses = 0
                log_warn(f"[brain-up] 单题 {self.code} 进入 exploit 阶段，"
                         f"{old} -> {strong_entry.name}，并行工具调用已开启")
        # 强模型轮数上限：超过 STRONG_MODEL_MAX_TURNS 轮后切回快模型
        if (getattr(self.ctx, "_on_strong_model", False) and self.model_pool is not None
                and self.ctx.strong_model_uses >= STRONG_MODEL_MAX_TURNS):
            fast_entry = self.model_pool.switch_to_role("fast")
            if fast_entry is not None:
                old = getattr(self.executor.model, "model", "?")
                self.executor.model = fast_entry.model
                self.ctx._on_strong_model = False
                log_warn(f"[brain-down] 单题 {self.code} 强模型已用 {self.ctx.strong_model_uses} 轮"
                         f"（上限 {STRONG_MODEL_MAX_TURNS}），{old} -> {fast_entry.name}")
        return False, ""

    # ------------------------------------------------------------------
    # _step：执行一次 Agent step（Runner.run 单轮），处理模型失败 fallback
    # ------------------------------------------------------------------
    async def _step(self) -> bool:
        """返回 False 表示需要 continue 外层循环（模型 fallback / 耗尽）。"""
        # 强模型轮数计数（含本轮）
        if getattr(self.ctx, "_on_strong_model", False):
            self.ctx.strong_model_uses += 1
        # R2：每轮合并子任务共享情报（运行期可见，不重复子任务已排除的方向）
        _merge_subtask_intel(self.ctx, self.challenge_workdir)
        ledger_text = _ledger_failed_summary(self.ctx) if self.ctx.phase == "exploit" else ""
        dynamic_ctx = _build_dynamic_context(
            RunContextWrapper(context=self.ctx), self.charter, self.ctx.plan or self.global_plan,
            self.field_notes, role_boost=getattr(self.ctx, "role_boost", ""),
            ledger_text=ledger_text)
        full_input = f"{EXECUTOR_DYNAMIC_PREFIX}{dynamic_ctx}\n\n{self.state.next_input}"
        try:
            await Runner.run(self.executor, input=full_input, context=self.ctx,
                             hooks=self.hooks, session=self.session, max_turns=1)
        except MaxTurnsExceeded:
            pass
        except Exception as exc:
            if is_model_failure(exc):
                current_name = getattr(self.executor.model, "model", "?")
                self.model_pool.mark_failed(current_name,
                                            permanent=is_permanent_model_failure(exc))
                entry = self.model_pool.next(current_name=current_name,
                                             reason=f"model_failure:{type(exc).__name__}")
                if entry is None:
                    log_error(f"[model-exhausted] 单题 {self.code} 所有模型均不可用：{exc}")
                    self.state.outcome = "stuck"
                    self.state.death_reason = "model_exhausted"
                    return False  # 外层 break
                self.executor.model = entry.model
                log_warn(f"[model-fallback] 单题 {self.code} {current_name} 失败，"
                         f"切换到 {entry.name} 继续同一会话：{str(exc)[:300]}")
                return False  # 同一输入重试，continue
            raise
        return True

    # ------------------------------------------------------------------
    # _post_step：每轮 step 后更新状态、触发干预、调度子任务/压缩/闭环
    # ------------------------------------------------------------------
    async def _post_step(self) -> tuple:
        """返回 (break_flag, next_input, death_reason)。"""
        if self.ctx.turn_gain:
            self.ctx.zero_gain_turns = 0
        else:
            self.ctx.zero_gain_turns += 1
            # H4：零增量轮真实统计（看板 zero_gain_events 数据源，经 cost_report 落盘）
            self.ctx.zero_gain_total += 1
            if self.ctx.zero_gain_turns > self.ctx.peak_zero_gain_streak:
                self.ctx.peak_zero_gain_streak = self.ctx.zero_gain_turns

        if self.ctx.turn_net_fail:
            self.ctx.net_fail_turns += 1
        else:
            self.ctx.net_fail_turns = 0

        # 致命错误 / 单题完成 / 空转 / 网络不可达
        if self.ctx.fatal:
            return True, "", "fatal_error"
        if self.ctx.finalized:
            return True, "", "solved"
        if self.ctx.turn_tool_count == 0:
            self.ctx.empty_turns += 1
            if self.ctx.empty_turns >= SINGLE_EMPTY_TURNS:
                log_warn(f"[skip] 单题 {self.code} 连续 {self.ctx.empty_turns} 轮空转，机械换题")
                return True, "", "empty_idle"
        else:
            self.ctx.empty_turns = 0
        if self.ctx.net_fail_turns >= 2:
            log_warn(f"[skip] 单题 {self.code} 连续 {self.ctx.net_fail_turns} 次网络不可达，机械换题")
            return True, "", "network_unreachable"

        # 模型惰性治理
        stuck_action = self.stuck_detector.check(
            self.ctx, self.model_pool.has_alternative,
            current_model_name=getattr(self.executor.model, "model", "?"))
        if stuck_action.action == StuckActionType.SWITCH_MODEL:
            current_name = getattr(self.executor.model, "model", "?")
            entry = self.model_pool.next(current_name=current_name,
                                         reason=f"stuck:{stuck_action.reason}")
            if entry is not None and entry.name != current_name:
                self.executor.model = entry.model
                log_warn(f"[model-switch] 单题 {self.code} {stuck_action.reason}，"
                         f"{current_name} -> {entry.name} 接管会话")
                self.ctx.zero_gain_turns = 0
                self.state.intervention_count += 1
                return False, stuck_mod.switch_model_prompt(self.ctx, current_name, entry.name), ""
        elif stuck_action.action == StuckActionType.SELF_RESCUE:
            log_warn(f"[self-rescue] 单题 {self.code} {stuck_action.reason}"
                     f"，解锁技能 {stuck_action.extra_skills}，阶段重置")
            summary = await compact_session(
                self.ctx, self.session, getattr(self.executor, "model", None),
                model_pool=self.model_pool)
            if summary:
                log_info(f"[self-rescue] 单题 {self.code} 历史压缩成功")
            else:
                log_warn(f"[self-rescue] 单题 {self.code} 历史压缩失败或跳过")
            self.ctx.zero_gain_turns = 0
            self.state.intervention_count += 1
            return False, stuck_action.next_input, ""

        # 单题停滞机械决策
        action = decide_stuck_action(
            self.ctx.zero_gain_turns, self.state.hint_used, self.difficulty,
            task_text=self.ctx.task + " " + json.dumps(self.ctx.blackboard, ensure_ascii=False))
        if action not in ("hint", "skip"):
            failed_paths = sum(
                1 for v in self.ctx.blackboard.values()
                if isinstance(v, dict) and v.get("status") == "failed")
            if should_pull_hint_by_budget(
                    self.ctx.token_usage.get("total", 0), failed_paths,
                    self.difficulty, self.state.hint_used, HINT_BUDGET_RATIO,
                    self.suspend_tokens_map):
                action = "hint"
        if action == "hint":
            try:
                hint = await asyncio.to_thread(self.client.get_hint, self.code)
            except Exception as e:
                hint = f"（获取提示失败：{str(e)[:120]}）"
            self.state.hint_used = True
            self.ctx.zero_gain_turns = 0
            self.ctx.hint_grace_active = True
            self.state.intervention_count += 1
            self.ctx.blackboard["hint_directive"] = {
                "value": hint, "status": "confirmed", "ts": int(time.time()),
                "verified": True, "evidence": "platform_hint",
            }
            log_info(f"  [hint] 单题 {self.code} 看提示（已写入 hint_directive）")
            return False, (
                f"【系统法令】平台提示已写入黑板 hint_directive，具有最高优先级。\n"
                f"原文：{hint}\n\n"
                f"接下来 {HINT_GRACE_TURNS} 轮你的每个动作必须直接验证该提示中的断言，"
                f"与提示无关的侦察/扫描将被系统判为零增量。"), ""

        if self.state.hint_used and self.ctx.zero_gain_turns >= HINT_GRACE_TURNS:
            log_warn(f"[hint-stale] 单题 {self.code} hint 后 {HINT_GRACE_TURNS} 轮无转化，机械换题")
            return True, "", "hint_stale"
        if action == "skip":
            failed_paths = sum(
                1 for v in self.ctx.blackboard.values()
                if isinstance(v, dict) and v.get("status") == "failed")
            if failed_paths == 0:
                log_warn(f"[skip] 单题 {self.code} 已停滞 {self.ctx.zero_gain_turns} 轮且无任何失败方向可探索，证据枯竭判死")
                return True, "", "evidence_exhausted_no_direction"
            else:
                log_warn(f"[skip] 单题 {self.code} 已停滞 {self.ctx.zero_gain_turns} 轮，机械换题")
                return True, "", "stuck_with_failed_paths"

        # 软干预教练（R1：默认关闭，代码保留；ENABLE_COACH=true 复活）
        if (ENABLE_COACH and self.state.hint_used
                and self.ctx.zero_gain_turns >= COACH_AFTER_HINT_TURNS
                and not self.state.coach_used):
            self.state.coach_used = True
            advice = await _coach(self.ctx, self.brief, self.hooks)
            self.ctx.blackboard["coach_advice"] = {
                "value": advice, "status": "done", "ts": int(time.time()),
                "verified": False,
            }
            log_info(f"[coach] 单题 {self.code} hint 后仍停滞 {self.ctx.zero_gain_turns} 轮，教练给方向")
            self.state.intervention_count += 1
            return False, (f"本题卡住。教练建议（可尝试的新方向）：\n{advice}\n\n"
                           f"请结合建议继续尝试，产出新证据。"), ""

        # 破局链两级熔断（R1）：3 轮零增量 → fork_analyze 复盘一次（写 next_directive）；
        # 复盘后 3 轮仍零增量 → 直接机械换题（不再 coach / plan-mode 兜底）
        if self.ctx.zero_gain_turns >= ZERO_GAIN_REPLAN_TURNS:
            if self.ctx.replan_count < REPLAN_MAX:
                self.ctx.plan = await _replan(self.ctx, self.brief, self.charter,
                                              self.role, self.outer_hooks)
                self.ctx.replan_count += 1
                self.ctx.zero_gain_turns = 0
                self.state.intervention_count += 1
                return False, "作战计划已更新，按新计划继续攻击本题。", ""
            log_warn(f"[skip] 单题 {self.code} fork_analyze 后 {ZERO_GAIN_REPLAN_TURNS} 轮仍零增量，机械换题")
            return True, "", "directive_no_progress"

        # 累计干预上限
        if self.state.intervention_count >= _max_interventions(self.difficulty):
            log_warn(f"[skip] 单题 {self.code} 累计干预 {self.state.intervention_count} 次"
                     f"（难度 {self.difficulty or 'unknown'} 上限 {_max_interventions(self.difficulty)}）仍无进展，机械换题")
            return True, "", "intervention_exhausted"

        # Plan Mode 触发/退出（R1：默认关闭，代码保留；ENABLE_PLAN_MODE=true 复活）
        if (ENABLE_PLAN_MODE and self.ctx.zero_gain_turns >= ZERO_GAIN_REPLAN_TURNS
                and self.ctx.plan_mode_history == 0
                and not self.ctx.plan_mode):
            self.ctx.plan_mode = True
            self.ctx.plan_mode_history = 0
            log_info(f"[plan-mode] 单题 {self.code} 进入 PLAN MODE，先输出可验证计划")
            self.ctx.zero_gain_turns = 0
            self.state.intervention_count += 1
            return False, "请基于当前已知事实，输出/修正本题的作战计划。禁止调用工具。", ""

        if ENABLE_PLAN_MODE and self.ctx.plan_mode:
            self.ctx.plan_mode = False
            self.ctx.plan_mode_history += 1
            log_info(f"[plan-mode] 单题 {self.code} 退出 PLAN MODE")
            return False, "PLAN MODE 已结束。请严格按照刚才的计划执行，继续攻击本题。", ""

        # 子任务调度与收割
        pending = [s for s in self.ctx.subtasks if s["status"] == "pending"]
        if pending:
            await _run_subtasks(self.ctx, pending, self.challenge_workdir, self.brief,
                                model=self.executor.model,
                                model_settings=self.executor.model_settings,
                                model_pool=self.model_pool)
        reap = _reap_subtasks(self.ctx)
        if reap:
            self.ctx.turn_gain = True
            return False, (f"【分支结果】以下后台子任务已返回，请立即处理：\n\n{reap}\n\n"
                           f"继续攻击本题：调用工具产出新证据增量，或调用 finalize 提交本题结论。"), ""

        # 历史压缩
        if await compact_if_needed(self.session, self.ctx, agent=self.executor):
            log_info("[compact] 单题历史已压缩")

        # 关键证据自动闭环
        close_notes = [n for n in self.ctx.notes
                       if n.startswith("[闭环]") or n.startswith("已确认")
                       or n.startswith("已发现")]
        if close_notes:
            self.ctx.notes = [n for n in self.ctx.notes if n not in close_notes]
            self.ctx.zero_gain_turns = 0
            return False, ("系统检测到可利用的关键证据，请立即按以下指令执行（不要继续侦察）：\n\n"
                           + "\n\n".join(close_notes)), ""

        return False, "继续攻击本题：调用工具产出新证据增量，或调用 finalize 提交本题结论。", ""

    # ------------------------------------------------------------------
    # _cleanup：第三道闸门统一回收（原 finally 段）
    # ------------------------------------------------------------------
    async def _cleanup(self) -> None:
        # 第三道闸门：统一回收所有后台子任务
        await _cancel_all_subtasks(self.ctx, reason="parent_finished")
        # 冲刷事件缓冲，保证 events.jsonl 完整落盘（证据留痕）
        try:
            _flush_emit_buffer(str(self.challenge_workdir / "events.jsonl"))
        except Exception as e:
            self.ctx.silent_failures += 1
            log_warn(f"[degraded] 单题 {self.code} 冲刷事件缓冲失败：{str(e)[:120]}")
        # 清理单题 session 文件，避免堆积（保留 events/artifacts 作为证据）
        try:
            (self.sessions_dir / f"challenge_{self.code}.sqlite").unlink(missing_ok=True)
        except Exception as e:
            self.ctx.silent_failures += 1
            log_warn(f"[degraded] 单题 {self.code} 清理 session 文件失败：{str(e)[:120]}")
        # 关闭 SQLiteSession（修补 6：连接/句柄生命周期闭环）
        try:
            self.session.close()
        except Exception as e:
            self.ctx.silent_failures += 1
            log_warn(f"[degraded] 单题 {self.code} 关闭 session 失败：{str(e)[:120]}")

    # ------------------------------------------------------------------
    # _finish：循环结束后的机械沉淀 + 报告收尾（原 finally 之后的段）
    # ------------------------------------------------------------------
    def _finish(self) -> str:
        answer = ""
        if self.state.outcome == "solved" and self.ctx.final_payload:
            answer = str(self.ctx.final_payload.get("findings", ""))[:500]
        _append_mechanical_note(self.code, self.state.outcome, self.ctx)  # 题级机械沉淀（零 LLM，按题写档案）
        # 写入缓存命中率到 field notes（赛后分析用）
        try:
            with FIELD_NOTES_FILE.open("a", encoding="utf-8") as f:
                f.write(f"- cache_hits={self.ctx.cache_hits} cache_misses={self.ctx.cache_misses}\n")
                f.write(f"- cache_guard_violations="
                        f"{getattr(self.ctx, 'cache_guard_violations', 0)}\n")
                _cr = self.ctx.token_usage.get("cache_read", 0)
                _cw = self.ctx.token_usage.get("cache_write", 0)
                _rate = _cr / (_cr + _cw) if (_cr + _cw) else 0
                f.write(f"- prefix_hit_rate={_rate:.1%} (read={_cr} write={_cw})\n")
                for note in self.ctx.cache_notes[-5:]:
                    f.write(f"  {note}\n")
        except Exception:
            pass
        if self.state.outcome == "solved":
            append_solution_template(self.code, self.desc, self.ctx)  # 正向解法模板沉淀（同类题复用）
        # 六种死法统一日志：便于赛后统计每种死因占比
        if self.state.death_reason:
            log_warn(f"[death] 单题 {self.code} 终态={self.state.outcome} 死因={self.state.death_reason} "
                     f"轮次={self.state.turn_count} token={self.ctx.token_usage.get('total', 0)}")
        if self.db is not None:
            self.db.task_finished(self.code, self.state.outcome, answer)  # 登记题目终态（监控页状态/结论）
        # 成本报告：单题 token 明细 + 缓存命中率 + 估算成本，供赛后复盘
        try:
            write_cost_report(self.challenge_workdir, self.code, self.state.outcome,
                              self.ctx, self.state.death_reason)
        except Exception:
            pass
        # 轨迹导出：事件总线全量落盘 trajectory_<code>.jsonl（append-only 回放用）
        try:
            export_trajectory(self.challenge_workdir, self.code, self.state.outcome, self.ctx)
        except Exception:
            pass
        return self.state.outcome


async def run_single_challenge(code: str, desc: str, addrs: list, charter: str,
                               task: str, global_plan: str, hooks, workdir: Path,
                               client: PlatformClient, difficulty: str = "",
                               flag_total: int = 1, flag_done: int = 0,
                               model_pool: ModelPool | None = None) -> str:
    """对一道题执行完整渗透循环，返回 outcome：solved / stuck / fatal（原 _run_single_challenge）。

    单题独立 context + 独立 session；停滞时机械看 hint / 换题（调度器决策），
    选题/换题/看 hint 不由 LLM 自觉——这是报告 P0-4 的核心修复。

    等价变换：setup 段原样保留，闭包状态机由 ExecutorLoop + RunnerState 承接。
    """
    # 题级独立工作区：3 槽并发下每题独立 events/session/artifacts，避免交错
    challenge_workdir = workdir / f"worker_{code}"
    challenge_workdir.mkdir(parents=True, exist_ok=True)
    challenge_hooks = EventStreamHooks(challenge_workdir, code)
    sessions_dir = workdir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    role = assign_role(code, desc)  # 题级派任（P0-5：按 unique_code 前缀 + 描述）
    log_info(f"== 单题 {code}：派任 {role['role']} ==")
    log_info(f"单题 {code} 目标：{desc.strip()[:150]}，flag 目标 {flag_total} 面（已拿 {flag_done}）")
    ctx = TaskContext(workdir=challenge_workdir, disclosed_skills=list(role["playbooks"]),
                      task=task, charter=charter, role=role)
    ctx.blackboard = _load_blackboard(challenge_workdir)  # 回注上次尝试进度（挂起/重试）
    # 按题型动态裁剪初始工具集：减少无关工具对 Agent 注意力的干扰
    ctx.enabled_tools = build_default_tools(groups=_tool_groups_for(role.get("role", ""), desc))
    # 调度器独占编排工具：单题循环里 Agent 不得自己选题/启动/关闭容器，避免破坏调度器追踪
    for t in ("check_vpn", "list_challenges", "start_challenge", "close_challenge"):
        ctx.enabled_tools.discard(t)
    ctx.current_code = code
    ctx.plan = global_plan
    sol_hint = load_solution_hint(code, desc)
    brief = (f"# 任务书\n{task}\n\n"
             f"# 当前题目（只打这道题）\n"
             f"- unique_code: {code}\n- 描述: {desc}\n- 容器地址: {addrs}\n"
             f"- flag 进度：已拿 {flag_done}/{flag_total} 面"
             f"（多 flag 题须逐面提交；系统提交回执会告知剩余面数）\n\n")
    if sol_hint:
        brief += (f"# 历史成功解法参考（同类题，可优先尝试）\n{sol_hint}\n\n")
    brief += ("选题/换题/看 hint 由系统调度负责，你只专注攻击本题容器；"
              "不要自己调用 list_challenges / start_challenge / close_challenge。")
    # 模型灾备池：执行者优先 FAST_MODEL（deepseek-v4-flash），glm 兜底。
    # 传入 model_pool 表示由外层统一分配（全局共享，避免每题重建）；
    # 未传入则兜底创建独立池（兼容单测/旧调用）。
    if model_pool is None:
        model_pool = ModelPool(preferred_name=FAST_MODEL_NAME)

    # 首轮机械预侦察：在 LLM 介入前先收集常见入口/敏感路径/状态码，省一轮 LLM 回合
    recon0 = ""
    try:
        recon0 = await first_strike(addrs)
        log_info(f"[first-strike] 单题 {code} 预侦察完成：{len(recon0)} 字符")
    except Exception as e:
        log_warn(f"[first-strike] 单题 {code} 预侦察失败：{str(e)[:120]}")

    # 缓存命中率观测：本题是否有现成打法/历史笔记可复用
    has_template = bool(sol_hint)
    has_notes = bool(load_notes_for(code))
    has_role_playbooks = bool(role.get("playbooks"))
    if has_template or has_notes or has_role_playbooks:
        ctx.cache_hits += 1
        ctx.cache_notes.append(
            f"hit: code={code} template={has_template} notes={has_notes} playbooks={has_role_playbooks}")
    else:
        ctx.cache_misses += 1
        ctx.cache_notes.append(f"miss: code={code} 无历史模板/笔记/角色打法")

    field_notes = load_notes_for(code) or _load_field_notes()
    # Agent Preset 运行时组合：按当前阶段选择默认 preset，强化阶段纪律
    initial_preset = "recon_focused" if ctx.phase == "recon" else "default"
    executor = build_executor(role, charter, brief,
                              field_notes=field_notes,
                              model=model_pool.current.model,
                              preset=initial_preset)
    log_info(f"单题 {code} 模型池：{model_pool}，起始模型 {model_pool.current.name}，preset={initial_preset}")
    session = SQLiteSession(session_id=f"challenge_{code}",
                            db_path=str(sessions_dir / f"challenge_{code}.sqlite"))

    db = db_mod.get_db()
    if db is not None:
        db.task_started(code, desc)  # 登记题目生命周期（监控页任务列表/状态）

    # 单题墙上时间预算：按难度分档硬顶（runtime.budget.WALLCLOCK_BUDGET，B4 收口）
    ctx.wallclock_budget = WALLCLOCK_BUDGET.get(str(difficulty).lower(), 15 * 60)
    ctx.challenge_start_ts = time.monotonic()
    ctx.wrong_submit_count = 0
    ctx.hint_grace_active = False

    next_input = f"开始攻击本题容器：{addrs}。"
    if recon0:
        next_input += f"\n\n系统已完成首轮机械预侦察，直接分析以下结果制定攻击路径：\n{recon0}"
    else:
        next_input += "先做信息收集，识别技术栈与入口。"

    # 闭包状态机 → RunnerState + ExecutorLoop（依赖全部构造注入）
    state = RunnerState(next_input=next_input)
    loop = ExecutorLoop(
        state=state, ctx=ctx,
        code=code, desc=desc, brief=brief, charter=charter, task=task,
        global_plan=global_plan, role=role, field_notes=field_notes,
        challenge_workdir=challenge_workdir, sessions_dir=sessions_dir,
        session=session, executor=executor,
        model_pool=model_pool, client=client, hooks=challenge_hooks,
        outer_hooks=hooks, difficulty=difficulty, db=db,
    )
    return await loop.run()
