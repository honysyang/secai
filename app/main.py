"""通用多智能体端到端 Demo：
管理者立法 → 角色派任 → 执行者执行（渐进披露 + 历史压缩 + 断点续跑）→ 报告者收尾。

不依赖任何靶场平台 / flag / 提交铁律，只跑通「多智能体 + 角色 + 多 Skills 渐进披露」主流程。

用法：
    python -m app.main "<任务描述>" [角色提示]
    python -m app.main                              # 使用默认本地侦察任务
    python -m app.main --resume                     # 从上次 checkpoint 续跑
"""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import requests

from agents import Runner
from agents.exceptions import MaxTurnsExceeded
from agents.memory import SQLiteSession

from core.agents_def import (strategist_agent, reporter_agent, build_executor,
                            EXECUTOR_DYNAMIC_PREFIX, _build_dynamic_context)
from runtime.budget import (HINT_BUDGET_RATIO, COST_LIMITS, SUSPEND_SECONDS,
                            should_pull_hint_by_budget)
from runtime.model_pool import ModelPool, is_model_failure, is_permanent_model_failure
from runtime.model_fallback import run_with_model_fallback
import runtime.stuck as stuck_mod
from runtime.stuck import StuckActionType, StuckDetector, compact_session
from core.charter import save_charter
from adapters.config import (BENCHMARK_BASE_URL, BENCHMARK_TOKEN,
                             BASE_URL, MODEL_NAME, API_KEY, VPN_CONFIG,
                             FAST_MODEL_NAME)

from core.context_manager import compact_if_needed
import adapters.db as db_mod
from demo_tools import build_default_tools
from core.events import BUS
from runtime.fork_analyst import fork_analyze, update_blackboard_with_fork
from core.hooks import EventStreamHooks, _ledger_failed_summary, _flush_emit_buffer
from runtime.reporting import (first_strike, write_cost_report,
                               export_trajectory, write_dashboard)

from bench_platform.platform_client import PlatformClient, TaskEnded, TaskNotFound, ContainerBusy
from arsenal.registries.role_registry import assign_role
from arsenal.registries.skill_registry import load_skills
from arsenal.registries import sec_tools
from bench_platform.scheduler import (select_challenge, decide_stuck_action,
                                      is_endgame, SINGLE_EMPTY_TURNS)
from solvecraft.solution_templates import append_solution_template, load_solution_hint
from runtime.status import set_status
from runtime.deadline import TASK_DEADLINE_TS, DEADLINE_SAFE_MARGIN
from runtime.log import log_info, log_warn, log_error
from core.task_context import TaskContext, SUBTASK_MAX_CONCURRENT

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

WORKDIR = DATA_DIR / "worker_generic"
SESSIONS_DIR = WORKDIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
_db_initialized = False

# 外层 Agent（Manager/Planner/Reporter/Coach）共享的模型灾备池，
# 在 run_task 入口初始化，避免与单题 executor 内部模型池冲突。
global_model_pool: Optional[ModelPool] = None


def _init_observability() -> None:
    """初始化 SQLite 落库 + 事件总线订阅（只初始化一次，防重复订阅）。

    hooks 发射的事件经 BUS 分发到 db 订阅者，与 events.jsonl 文件双写留痕。
    """
    global _db_initialized
    if _db_initialized:
        return
    db = db_mod.init_default()
    BUS.subscribe(db_mod.db_subscriber(db))
    _db_initialized = True
    log_info("可观测性初始化完成：SQLite 落库 + 事件总线订阅")

# 跑分任务模板已抽离到 prompts/tsec_task.txt（见下方 build_default_task）

#目标任务
TSEC_TASK_FILE = Path(__file__).parent.parent / "prompts" / "tsec_task.txt"


def build_default_task() -> str:
    """读跑分任务模板并替换占位符（模板独立在 prompts/tsec_task.txt）。"""
    from adapters.config import BENCHMARK_TOKEN, BENCHMARK_BASE_URL
    token = BENCHMARK_TOKEN or "（未配置 BENCHMARK_TOKEN）"
    base_url = BENCHMARK_BASE_URL or "（未配置 BENCHMARK_BASE_URL）"
    return (TSEC_TASK_FILE.read_text(encoding="utf-8")
            .replace("{BENCHMARK_TOKEN}", token)
            .replace("{BENCHMARK_BASE_URL}", base_url))

FIELD_NOTES_FILE = DATA_DIR / "field_notes.md"


def _load_field_notes(max_chars: int = 3000) -> str:
    """读取上次战报尾部（含「死路蒸馏」），作为执行者的历史作战档案注入。"""
    if not FIELD_NOTES_FILE.exists():
        return ""
    return FIELD_NOTES_FILE.read_text(encoding="utf-8")[-max_chars:]


def _append_mechanical_note(code: str, outcome: str, ctx) -> None:
    """题级机械沉淀（零 LLM）：战果 + 死路从黑板/提交记录直接提取。"""
    failed = [k for k, v in ctx.blackboard.items()
              if isinstance(v, dict) and v.get("status") == "failed"][:8]
    wins = [f"correct:{f}" for f in getattr(ctx, "correct_flags", [])][:8]
    disclosed = ",".join(getattr(ctx, "disclosed_skills", [])[:6])
    lines = [f"\n# {code} · {outcome} · {time.strftime('%m-%d %H:%M')}",
             f"- 战果: {', '.join(wins) or '无'}",
             f"- 死路: {', '.join(failed) or '无'}",
             f"- 披露技能: {disclosed}"]
    try:
        with FIELD_NOTES_FILE.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def load_notes_for(code: str, max_chars: int = 900) -> str:
    """按题检索档案：本题 + 同前缀题的历史段落，最近 3 段。"""
    if not FIELD_NOTES_FILE.exists():
        return ""
    text = FIELD_NOTES_FILE.read_text(encoding="utf-8")
    prefix = code.rsplit("-", 1)[0] if "-" in code else code
    hits = [sec[:max_chars] for sec in text.split("\n# ")
            if sec.startswith(code) or sec.startswith(prefix + "-")]
    return "\n---\n".join(hits[-3:])


SUBTASK_MAX_TURNS = 8  # 每个子任务最多 LLM 回合数（内部 ReAct，Agent 可 finalize 提前结束）
SUBTASK_TIMEOUT_SECONDS = 600  # 每个后台子任务总超时（10 分钟）
ZERO_GAIN_REPLAN_TURNS = 3  # 连续零信息增量轮数触发 fork_analyze（对齐指南：3 轮即破局分析）
REPLAN_MAX = 2           # 最多 replan 次数，防止无限重规划
COACH_AFTER_HINT_TURNS = 3  # hint 后仍零增益 3 轮触发软干预教练（每题目仅 1 次）
STRONG_MODEL_MAX_TURNS = 3  # 强模型（破局）每题目最多轮数，超限切回快模型
# 单题「自救+切换模型+hint+coach+replan」累计上限，按难度分档防死循环：
# 简单题快速放弃，hard 题给更多利用机会（链式利用需要更多轮次）。
MAX_STUCK_INTERVENTIONS = {
    "easy": 3,
    "medium": 5,
    "hard": 8,
}


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


async def _run_subtasks(ctx, pending, challenge_workdir, brief, model=None, model_settings=None, model_pool=None) -> None:
    """后台并发调度 pending 子任务：立即创建 asyncio.Task，不阻塞主循环。

    子任务用 finish_subtask 结束协议（summary/findings/flag），主 Agent 只拿到结构化结论，
    不接触子任务的海量工具输出（上下文隔离）。结果写回主黑板（subtask:<id>）。
    调用方（主循环）负责通过 _reap_subtasks 非阻塞收割结果。

    并发配额：每题同时运行的子任务不超过 SUBTASK_MAX_CONCURRENT，超过时 pending
    子任务留到下一轮再启动，避免拖死 harness。

    三道闸门：
    - 明确目标：sub["objective"] 必填（spawn_subtask 已校验）
    - 独立预算：子任务携带独立 SubtaskBudget，token/turn/墙上时间任一耗尽即停
    - 回收机制：完成/超时/预算耗尽/父任务停止时统一回收
    """
    from core.task_context import SubtaskBudget

    running = sum(1 for j in ctx.subtask_jobs.values() if not j.done())
    slots = max(0, SUBTASK_MAX_CONCURRENT - running)
    if slots <= 0:
        return

    started = 0
    for sub in pending:
        if started >= slots:
            break
        if sub.get("status") != "pending":
            continue
        if sub.get("id") in ctx.subtask_jobs:
            continue
        # 第一道闸门：明确目标
        budget: SubtaskBudget = sub.get("budget")
        if budget is None or not budget.objective:
            sub["status"] = "rejected"
            sub["result"] = {"summary": "[子任务被拒绝] 缺少明确 objective", "findings": [], "flag": None}
            continue
        started += 1

        # 第二道闸门：独立预算缺省值
        if budget.max_tokens <= 0:
            # 未显式指定时给固定保守默认（约 3 万 token），避免无限燃烧
            budget.max_tokens = 30000
        budget.max_turns = min(budget.max_turns, SUBTASK_MAX_TURNS)

        sub_role = ctx.role
        if sub.get("branch_type"):
            try:
                sub_role = assign_role(sub.get("branch_type", ""), sub["desc"])
            except Exception:
                pass
        sub_executor = build_executor(
            sub_role, ctx.charter, brief,
            field_notes=_load_field_notes(),
            model=model, model_settings=model_settings,
            is_subtask=True)

        async def _run_one(sub=sub, sub_executor=sub_executor, sub_role=sub_role, budget=budget):
            sub["status"] = "running"
            # 独立 context：深拷贝避免主子任务状态污染
            sub_ctx = TaskContext(
                workdir=challenge_workdir,
                disclosed_skills=list(ctx.disclosed_skills),
                task=ctx.task,
                charter=ctx.charter,
                role=sub_role,
            )
            sub_ctx.current_code = ctx.current_code
            sub_ctx.submitted = set(ctx.submitted)
            sub_ctx.correct_flags = list(ctx.correct_flags)
            sub_ctx.blackboard = copy.deepcopy(ctx.blackboard)
            sub_ctx.token_usage = dict(ctx.token_usage)
            sub_ctx.enabled_tools = set(ctx.enabled_tools) if ctx.enabled_tools is not None else None
            sub_ctx.phase = ctx.phase
            sub_ctx.plan = ctx.plan
            sub_ctx.wallclock_budget = budget.timeout_seconds
            sub_session = SQLiteSession(session_id=f"sub_{sub['id']}",
                                        db_path=str(challenge_workdir / f"sub_{sub['id']}.sqlite"))
            sub_hooks = EventStreamHooks(challenge_workdir, f"sub_{sub['id']}")
            try:
                await asyncio.wait_for(
                    run_with_model_fallback(
                        sub_executor,
                        input=(f"子任务目标：{budget.objective}\n"
                               f"任务描述：{sub['desc']}\n"
                               f"独立完成这个子任务，完成后调用 finish_subtask 提交结构化结论。"),
                        context=sub_ctx, hooks=sub_hooks, session=sub_session,
                        max_turns=budget.max_turns,
                        model_pool=model_pool,
                        agent_name="Subtask"),
                    timeout=budget.timeout_seconds)
                payload = sub_ctx.final_payload or {}
                if sub_ctx.finalized and payload.get("summary"):
                    sub["result"] = {
                        "summary": payload.get("summary", ""),
                        "findings": payload.get("findings", []),
                        "flag": payload.get("flag"),
                    }
                    budget.reason = "completed"
                else:
                    sub["result"] = {
                        "summary": "[未走结束协议] " + str(payload.get("summary", ""))[:200],
                        "findings": [], "flag": None,
                    }
                    budget.reason = "unfinalized"
            except asyncio.TimeoutError:
                sub["result"] = {"summary": "[子任务超时] 达到总时间上限",
                                 "findings": [], "flag": None}
                budget.reason = "timeout"
            except MaxTurnsExceeded:
                sub["result"] = {"summary": "[未走结束协议] 子任务达到回合上限",
                                 "findings": [], "flag": None}
                budget.reason = "turn_budget"
            except asyncio.CancelledError:
                sub["result"] = {"summary": "[子任务取消] 被主循环取消",
                                 "findings": [], "flag": None}
                budget.reason = "cancelled"
                cancelled_flag = True
                raise
            except Exception as e:
                sub["result"] = {"summary": f"[子任务异常] {str(e)[:200]}",
                                 "findings": [], "flag": None}
                budget.reason = f"exception:{type(e).__name__}"
            finally:
                sub["status"] = "done"
                # 把子任务结果合并回主黑板，但不覆盖主任务已有的 verified 条目
                key = f"subtask:{sub['id']}"
                if key not in ctx.blackboard:
                    ctx.blackboard[key] = {
                        "value": json.dumps(sub["result"], ensure_ascii=False),
                        "status": "done", "ts": int(time.time()),
                        "verified": True,
                    }
                # 合并子任务 token 用量到父任务（原子累加）
                for k in ("input", "output", "total", "requests"):
                    ctx.token_usage[k] = ctx.token_usage.get(k, 0) + sub_ctx.token_usage.get(k, 0)
                # 合并 flag：如果子任务拿到 flag，主任务也获得
                if sub["result"].get("flag"):
                    ctx.correct_flags.append(sub["result"]["flag"])

        # 立即后台启动，不等主循环
        ctx.subtask_jobs[sub["id"]] = asyncio.create_task(_run_one())
        log_info(f"[subtask] 单题 {ctx.current_code} 启动后台子任务 {sub['id']} "
                 f"（{sub.get('branch_type') or sub_role['role']}）objective={budget.objective[:60]}")


def _reap_subtasks(ctx) -> str:
    """非阻塞收割已完成的子任务，返回结果摘要供主循环注入下一轮 input。"""
    notes = []
    for sid, job in list(ctx.subtask_jobs.items()):
        if not job.done():
            continue
        del ctx.subtask_jobs[sid]
        sub = next((s for s in ctx.subtasks if s.get("id") == sid), None)
        if sub is None:
            continue
        r = sub.get("result", {})
        flag = r.get("flag")
        line = (f"[分支回收] {sid}：{r.get('summary', '')[:150]}"
                + (f"｜flag={flag}" if flag else ""))
        notes.append(line)
    return "\n".join(notes)


async def _cancel_all_subtasks(ctx, reason: str = "parent_stop") -> None:
    """取消并等待所有后台子任务，防止主任务退出后子任务继续消耗资源。"""
    if not ctx.subtask_jobs:
        return
    tasks = list(ctx.subtask_jobs.values())
    for job in tasks:
        if not job.done():
            job.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # 记录被取消的子任务终态
    for sid, job in list(ctx.subtask_jobs.items()):
        sub = next((s for s in ctx.subtasks if s.get("id") == sid), None)
        budget = sub.get("budget") if sub else None
        if sub and not sub.get("result"):
            sub["status"] = "done"
            sub["result"] = {"summary": f"[子任务取消] {reason}",
                             "findings": [], "flag": None}
            if budget:
                budget.reason = reason
        if job.cancelled() and budget:
            budget.cancelled = True
    ctx.subtask_jobs.clear()
    log_info(f"[subtask] 单题 {ctx.current_code} 已取消 {len(tasks)} 个后台子任务：{reason}")


def _load_blackboard(workdir: Path) -> dict:
    """从 workdir/blackboard.json 加载黑板（存在则返回，否则空 dict）。

    挂起/重试同一题时回注上次进度，避免重复已做/已排除的结论。
    """
    p = workdir / "blackboard.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def _replan(ctx, task: str, charter: str, role: dict, hooks) -> str:
    """执行中计划修正：调用 fork_analyst 做一次性强模型分析，结果只写 next_directive。

    不再调用常驻 Strategist；fork_analyst 只读取近期轨迹与黑板，产出破局建议。
    """
    events = BUS.history(hooks.task_id)
    role_brief = (
        f"任务：{task}\n"
        f"角色：{role.get('role', 'unknown')}\n"
        f"宪章：{charter[:500]}"
    )
    pool = global_model_pool
    try:
        strong = pool.switch_to_role("strong") if pool else None
        model = strong.model if strong else None
        result = await fork_analyze(
            events=events,
            blackboard=ctx.blackboard,
            role_brief=role_brief,
            model=model,
            model_pool=pool,
        )
        directive = update_blackboard_with_fork(ctx.blackboard, result)
        log_info(f"[fork-analyst] 单题 {hooks.task_id} 产出 next_directive：{directive[:80]}")
        return directive
    except Exception as e:
        log_warn(f"[fork-analyst] 调用失败：{e}；回退到静态提示")
        return "继续探索新的可验证方向，优先使用已解锁技能做最小验证。"


async def _coach(ctx, brief, hooks) -> str:
    """软干预教练：直接返回 blackboard 中 fork_analyst 的 next_directive，避免常驻 Coach Agent。"""
    fork_entry = ctx.blackboard.get("next_directive")
    if fork_entry and isinstance(fork_entry, dict) and fork_entry.get("value"):
        return str(fork_entry["value"])
    return "当前无明确教练建议。请基于黑板事实，选择一个未验证方向做最小动作并产出证据。"


async def _run_single_challenge(code: str, desc: str, addrs: list, charter: str,
                                task: str, global_plan: str, hooks, workdir: Path,
                                client: PlatformClient, difficulty: str = "",
                                flag_total: int = 1, flag_done: int = 0,
                                model_pool: Optional[ModelPool] = None) -> str:
    """对一道题执行完整渗透循环，返回 outcome：solved / stuck / fatal。

    单题独立 context + 独立 session；停滞时机械看 hint / 换题（调度器决策），
    选题/换题/看 hint 不由 LLM 自觉——这是报告 P0-4 的核心修复。
    """
    # 题级独立工作区：3 槽并发下每题独立 events/session/artifacts，避免交错
    challenge_workdir = workdir / f"worker_{code}"
    challenge_workdir.mkdir(parents=True, exist_ok=True)
    challenge_hooks = EventStreamHooks(challenge_workdir, code)

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
                            db_path=str(SESSIONS_DIR / f"challenge_{code}.sqlite"))

    turn_count = 0
    hint_used = False
    coach_used = False  # 软干预教练：每题目仅触发 1 次
    intervention_count = 0  # 累计干预次数：自救+切换模型+hint+coach+replan
    stuck_detector = StuckDetector()  # 模型惰性检测器（多模型切换 / 单模型自救）
    outcome = "stopped"
    death_reason = ""  # 六种死法终态标签，用于赛后分析
    # 成本治理：本尝试的 token/时钟起点 + 换脑/挂起档
    cost_limit = COST_LIMITS.get(str(difficulty).lower(), COST_LIMITS.get("medium", {}))
    switch_tokens = cost_limit.get("switch_tokens", 0)
    suspend_tokens = cost_limit.get("suspend_tokens", 0)
    cost_base_tokens = ctx.token_usage.get("total", 0)
    suspend_time_base = time.monotonic()
    switched = False
    suspend_tokens_map = {d: v.get("suspend_tokens", 0)
                          for d, v in COST_LIMITS.items()}
    db = db_mod.get_db()
    if db is not None:
        db.task_started(code, desc)  # 登记题目生命周期（监控页任务列表/状态）

    # 单题墙上时间预算：按难度分档硬顶（高分作战硬约束）
    _WALLCLOCK_BUDGET = {
        "easy": 10 * 60,    # 10 分钟
        "medium": 15 * 60,  # 15 分钟
        "hard": 25 * 60,    # 25 分钟
    }
    ctx.wallclock_budget = _WALLCLOCK_BUDGET.get(str(difficulty).lower(), 15 * 60)
    ctx.challenge_start_ts = time.monotonic()
    ctx.wrong_submit_count = 0
    ctx.hint_grace_active = False
    HINT_GRACE_TURNS = 5

    next_input = f"开始攻击本题容器：{addrs}。"
    if recon0:
        next_input += f"\n\n系统已完成首轮机械预侦察，直接分析以下结果制定攻击路径：\n{recon0}"
    else:
        next_input += "先做信息收集，识别技术栈与入口。"
    try:
        async def _pre_step() -> tuple:
            """每轮 step 前：检查硬性终止条件、重置 turn 状态、阶段/模型升级。

            返回 (break_flag, death_reason)。break_flag=True 时外层应终止单题循环。
            """
            nonlocal switched
            # 墙上时钟硬顶：单题超时强制 stuck，释放槽位
            elapsed = time.monotonic() - ctx.challenge_start_ts
            if elapsed >= ctx.wallclock_budget:
                if ctx.zero_gain_turns < 5 and not getattr(ctx, "_wallclock_extended", False):
                    ctx._wallclock_extended = True
                    ctx.wallclock_budget += ctx.wallclock_budget // 2
                    log_info(f"[extend] 单题 {code} 有进展，墙钟延长半档至 {ctx.wallclock_budget}s")
                else:
                    log_warn(f"[skip] 单题 {code} 墙上时间 {elapsed:.0f}s 超过预算 {ctx.wallclock_budget}s，机械换题")
                    return True, "wallclock_timeout"

            # 错误提交熔断
            if ctx.wrong_submit_count >= 6 and ctx.zero_gain_turns >= 3:
                log_warn(f"[skip] 单题 {code} 连续 {ctx.wrong_submit_count} 次错交且无新证据，机械换题")
                return True, "wrong_submit_fuse"

            # 成本治理：token / 时钟挂起档
            used = ctx.token_usage.get("total", 0) - cost_base_tokens
            if (switch_tokens and not switched and used >= switch_tokens
                    and model_pool.has_alternative):
                entry = model_pool.next(reason="token_threshold")
                if entry is not None:
                    old = getattr(executor.model, "model", "?")
                    executor.model = entry.model
                    switched = True
                    log_warn(f"[switch] 单题 {code} token {used} 到换脑档，{old} -> {entry.name}")
            if suspend_tokens and used >= suspend_tokens:
                return True, "token_suspend"
            if SUSPEND_SECONDS and time.monotonic() - suspend_time_base >= SUSPEND_SECONDS:
                return True, "time_suspend"

            # turn 状态清零
            ctx.turn_tool_count = 0
            ctx.turn_gain = False
            ctx.turn_net_fail = False

            # 攻坚换强脑：进入 exploit 阶段后切换到 strong 模型，并开启并行工具调用
            if (ctx.phase == "exploit" and not getattr(ctx, "_brain_upgraded", False)
                    and model_pool is not None):
                strong_entry = model_pool.switch_to_role("strong")
                if strong_entry is not None:
                    old = getattr(executor.model, "model", "?")
                    executor.model = strong_entry.model
                    executor.model_settings.parallel_tool_calls = True
                    ctx._brain_upgraded = True
                    ctx._on_strong_model = True
                    ctx.strong_model_uses = 0
                    log_warn(f"[brain-up] 单题 {code} 进入 exploit 阶段，"
                             f"{old} -> {strong_entry.name}，并行工具调用已开启")
            # 强模型轮数上限：超过 STRONG_MODEL_MAX_TURNS 轮后切回快模型
            if getattr(ctx, "_on_strong_model", False) and model_pool is not None:
                if ctx.strong_model_uses >= STRONG_MODEL_MAX_TURNS:
                    fast_entry = model_pool.switch_to_role("fast")
                    if fast_entry is not None:
                        old = getattr(executor.model, "model", "?")
                        executor.model = fast_entry.model
                        ctx._on_strong_model = False
                        log_warn(f"[brain-down] 单题 {code} 强模型已用 {ctx.strong_model_uses} 轮"
                                 f"（上限 {STRONG_MODEL_MAX_TURNS}），{old} -> {fast_entry.name}")
            return False, ""

        async def _step() -> bool:
            """执行一次 Agent step（Runner.run 单轮）。

            处理模型失败 fallback；返回 False 表示需要 continue 外层循环。
            """
            # 强模型轮数计数（含本轮）
            if getattr(ctx, "_on_strong_model", False):
                ctx.strong_model_uses += 1
            ledger_text = _ledger_failed_summary(ctx) if ctx.phase == "exploit" else ""
            dynamic_ctx = _build_dynamic_context(
                RunContextWrapper(context=ctx), charter, ctx.plan or global_plan,
                field_notes, role_boost=getattr(ctx, "role_boost", ""),
                ledger_text=ledger_text)
            full_input = f"{EXECUTOR_DYNAMIC_PREFIX}{dynamic_ctx}\n\n{next_input}"
            try:
                await Runner.run(executor, input=full_input, context=ctx,
                                 hooks=challenge_hooks, session=session, max_turns=1)
            except MaxTurnsExceeded:
                pass
            except Exception as exc:
                if is_model_failure(exc):
                    current_name = getattr(executor.model, "model", "?")
                    model_pool.mark_failed(current_name,
                                           permanent=is_permanent_model_failure(exc))
                    entry = model_pool.next(current_name=current_name,
                                            reason=f"model_failure:{type(exc).__name__}")
                    if entry is None:
                        log_error(f"[model-exhausted] 单题 {code} 所有模型均不可用：{exc}")
                        nonlocal outcome, death_reason
                        outcome = "stuck"
                        death_reason = "model_exhausted"
                        return False  # 外层 break
                    executor.model = entry.model
                    log_warn(f"[model-fallback] 单题 {code} {current_name} 失败，"
                             f"切换到 {entry.name} 继续同一会话：{str(exc)[:300]}")
                    return False  # 同一输入重试，continue
                raise
            return True

        async def _post_step() -> tuple:
            """每轮 step 后：更新状态、触发干预、调度子任务/压缩/闭环。

            返回 (break_flag, next_input, death_reason)。
            """
            nonlocal next_input, intervention_count, hint_used, coach_used
            prev_phase = getattr(ctx, "_prev_phase", ctx.phase)
            if ctx.phase == prev_phase:
                ctx.stuck_turns += 1
            else:
                ctx.stuck_turns = 0
            ctx._prev_phase = ctx.phase

            if ctx.turn_gain:
                ctx.zero_gain_turns = 0
            else:
                ctx.zero_gain_turns += 1

            if ctx.turn_net_fail:
                ctx.net_fail_turns += 1
            else:
                ctx.net_fail_turns = 0

            # 致命错误 / 单题完成 / 空转 / 网络不可达
            if ctx.fatal:
                return True, "", "fatal_error"
            if ctx.finalized:
                return True, "", "solved"
            if ctx.turn_tool_count == 0:
                ctx.empty_turns += 1
                if ctx.empty_turns >= SINGLE_EMPTY_TURNS:
                    log_warn(f"[skip] 单题 {code} 连续 {ctx.empty_turns} 轮空转，机械换题")
                    return True, "", "empty_idle"
            else:
                ctx.empty_turns = 0
            if ctx.net_fail_turns >= 2:
                log_warn(f"[skip] 单题 {code} 连续 {ctx.net_fail_turns} 次网络不可达，机械换题")
                return True, "", "network_unreachable"

            # 模型惰性治理
            stuck_action = stuck_detector.check(
                ctx, model_pool.has_alternative,
                current_model_name=getattr(executor.model, "model", "?"))
            if stuck_action.action == StuckActionType.SWITCH_MODEL:
                current_name = getattr(executor.model, "model", "?")
                entry = model_pool.next(current_name=current_name,
                                        reason=f"stuck:{stuck_action.reason}")
                if entry is not None and entry.name != current_name:
                    executor.model = entry.model
                    log_warn(f"[model-switch] 单题 {code} {stuck_action.reason}，"
                             f"{current_name} -> {entry.name} 接管会话")
                    ctx.zero_gain_turns = 0
                    intervention_count += 1
                    return False, stuck_mod.switch_model_prompt(ctx, current_name, entry.name), ""
            elif stuck_action.action == StuckActionType.SELF_RESCUE:
                log_warn(f"[self-rescue] 单题 {code} {stuck_action.reason}"
                         f"，解锁技能 {stuck_action.extra_skills}，阶段重置")
                summary = await compact_session(
                    ctx, session, getattr(executor, "model", None),
                    model_pool=model_pool)
                if summary:
                    log_info(f"[self-rescue] 单题 {code} 历史压缩成功")
                else:
                    log_warn(f"[self-rescue] 单题 {code} 历史压缩失败或跳过")
                ctx.zero_gain_turns = 0
                intervention_count += 1
                return False, stuck_action.next_input, ""

            # 单题停滞机械决策
            action = decide_stuck_action(
                ctx.zero_gain_turns, hint_used, difficulty,
                task_text=ctx.task + " " + json.dumps(ctx.blackboard, ensure_ascii=False))
            if action not in ("hint", "skip"):
                failed_paths = sum(
                    1 for v in ctx.blackboard.values()
                    if isinstance(v, dict) and v.get("status") == "failed")
                if should_pull_hint_by_budget(
                        ctx.token_usage.get("total", 0), failed_paths,
                        difficulty, hint_used, HINT_BUDGET_RATIO,
                        suspend_tokens_map):
                    action = "hint"
            if action == "hint":
                try:
                    hint = await asyncio.to_thread(client.get_hint, code)
                except Exception as e:
                    hint = f"（获取提示失败：{str(e)[:120]}）"
                hint_used = True
                ctx.zero_gain_turns = 0
                ctx.hint_grace_active = True
                intervention_count += 1
                ctx.blackboard["hint_directive"] = {
                    "value": hint, "status": "confirmed", "ts": int(time.time()),
                    "verified": True, "evidence": "platform_hint",
                }
                log_info(f"  [hint] 单题 {code} 看提示（已写入 hint_directive）")
                return False, (
                    f"【系统法令】平台提示已写入黑板 hint_directive，具有最高优先级。\n"
                    f"原文：{hint}\n\n"
                    f"接下来 {HINT_GRACE_TURNS} 轮你的每个动作必须直接验证该提示中的断言，"
                    f"与提示无关的侦察/扫描将被系统判为零增量。"), ""

            if hint_used and ctx.zero_gain_turns >= HINT_GRACE_TURNS:
                log_warn(f"[hint-stale] 单题 {code} hint 后 {HINT_GRACE_TURNS} 轮无转化，机械换题")
                return True, "", "hint_stale"
            if action == "skip":
                failed_paths = sum(
                    1 for v in ctx.blackboard.values()
                    if isinstance(v, dict) and v.get("status") == "failed")
                if failed_paths == 0:
                    log_warn(f"[skip] 单题 {code} 已停滞 {ctx.zero_gain_turns} 轮且无任何失败方向可探索，证据枯竭判死")
                    return True, "", "evidence_exhausted_no_direction"
                else:
                    log_warn(f"[skip] 单题 {code} 已停滞 {ctx.zero_gain_turns} 轮，机械换题")
                    return True, "", "stuck_with_failed_paths"

            # 软干预教练
            if (hint_used and ctx.zero_gain_turns >= COACH_AFTER_HINT_TURNS
                    and not coach_used):
                coach_used = True
                advice = await _coach(ctx, brief, challenge_hooks)
                ctx.blackboard["coach_advice"] = {
                    "value": advice, "status": "done", "ts": int(time.time()),
                    "verified": False,
                }
                log_info(f"[coach] 单题 {code} hint 后仍停滞 {ctx.zero_gain_turns} 轮，教练给方向")
                intervention_count += 1
                return False, (f"本题卡住。教练建议（可尝试的新方向）：\n{advice}\n\n"
                               f"请结合建议继续尝试，产出新证据。"), ""

            # replan
            if ctx.zero_gain_turns >= ZERO_GAIN_REPLAN_TURNS and ctx.replan_count < REPLAN_MAX:
                ctx.plan = await _replan(ctx, brief, charter, role, hooks)
                ctx.replan_count += 1
                ctx.zero_gain_turns = 0
                intervention_count += 1
                return False, "作战计划已更新，按新计划继续攻击本题。", ""

            # 累计干预上限
            if intervention_count >= _max_interventions(difficulty):
                log_warn(f"[skip] 单题 {code} 累计干预 {intervention_count} 次"
                         f"（难度 {difficulty or 'unknown'} 上限 {_max_interventions(difficulty)}）仍无进展，机械换题")
                return True, "", "intervention_exhausted"

            # Plan Mode 触发
            if (ctx.zero_gain_turns >= ZERO_GAIN_REPLAN_TURNS
                    and ctx.plan_mode_history == 0
                    and not ctx.plan_mode):
                ctx.plan_mode = True
                ctx.plan_mode_history = 0
                log_info(f"[plan-mode] 单题 {code} 进入 PLAN MODE，先输出可验证计划")
                ctx.zero_gain_turns = 0
                intervention_count += 1
                return False, "请基于当前已知事实，输出/修正本题的作战计划。禁止调用工具。", ""

            # Plan Mode 退出
            if ctx.plan_mode:
                ctx.plan_mode = False
                ctx.plan_mode_history += 1
                log_info(f"[plan-mode] 单题 {code} 退出 PLAN MODE")
                return False, "PLAN MODE 已结束。请严格按照刚才的计划执行，继续攻击本题。", ""

            # 子任务调度与收割
            pending = [s for s in ctx.subtasks if s["status"] == "pending"]
            if pending:
                await _run_subtasks(ctx, pending, challenge_workdir, brief,
                                    model=executor.model,
                                    model_settings=executor.model_settings,
                                    model_pool=model_pool)
            reap = _reap_subtasks(ctx)
            if reap:
                ctx.turn_gain = True
                return False, (f"【分支结果】以下后台子任务已返回，请立即处理：\n\n{reap}\n\n"
                               f"继续攻击本题：调用工具产出新证据增量，或调用 finalize 提交本题结论。"), ""

            # 历史压缩
            if await compact_if_needed(session, ctx, agent=executor):
                log_info("[compact] 单题历史已压缩")

            # 关键证据自动闭环
            close_notes = [n for n in ctx.notes
                           if n.startswith("[闭环]") or n.startswith("已确认")
                           or n.startswith("已发现")]
            if close_notes:
                ctx.notes = [n for n in ctx.notes if n not in close_notes]
                ctx.zero_gain_turns = 0
                return False, ("系统检测到可利用的关键证据，请立即按以下指令执行（不要继续侦察）：\n\n"
                               + "\n\n".join(close_notes)), ""

            return False, "继续攻击本题：调用工具产出新证据增量，或调用 finalize 提交本题结论。", ""

        while True:
            turn_count += 1
            ctx.turn_count = turn_count
            ctx._prev_phase = ctx.phase
            should_break, dr = await _pre_step()
            if should_break:
                outcome = "stuck" if dr != "solved" else "solved"
                death_reason = dr
                break
            step_continue = await _step()
            if not step_continue:
                if death_reason == "model_exhausted":
                    break
                continue
            should_break, next_input, death_reason = await _post_step()
            if should_break:
                if death_reason == "solved":
                    outcome = "solved"
                else:
                    outcome = "stuck"
                break
    finally:
        # 第三道闸门：统一回收所有后台子任务
        await _cancel_all_subtasks(ctx, reason="parent_finished")
        # 冲刷事件缓冲，保证 events.jsonl 完整落盘（证据留痕）
        try:
            _flush_emit_buffer(str(challenge_workdir / "events.jsonl"))
        except Exception:
            pass
        # 清理单题 session 文件，避免堆积（保留 events/artifacts 作为证据）
        try:
            (SESSIONS_DIR / f"challenge_{code}.sqlite").unlink(missing_ok=True)
        except Exception:
            pass
    answer = ""
    if outcome == "solved" and ctx.final_payload:
        answer = str(ctx.final_payload.get("findings", ""))[:500]
    _append_mechanical_note(code, outcome, ctx)  # 题级机械沉淀（零 LLM，按题写档案）
    # 写入缓存命中率到 field notes（赛后分析用）
    try:
        with FIELD_NOTES_FILE.open("a", encoding="utf-8") as f:
            f.write(f"- cache_hits={ctx.cache_hits} cache_misses={ctx.cache_misses}\n")
            for note in ctx.cache_notes[-5:]:
                f.write(f"  {note}\n")
    except Exception:
        pass
    if outcome == "solved":
        append_solution_template(code, desc, ctx)  # 正向解法模板沉淀（同类题复用）
    # 六种死法统一日志：便于赛后统计每种死因占比
    if death_reason:
        log_warn(f"[death] 单题 {code} 终态={outcome} 死因={death_reason} "
                 f"轮次={turn_count} token={ctx.token_usage.get('total', 0)}")
    if db is not None:
        db.task_finished(code, outcome, answer)  # 登记题目终态（监控页状态/结论）
    # 成本报告：单题 token 明细 + 缓存命中率 + 估算成本，供赛后复盘
    try:
        write_cost_report(challenge_workdir, code, outcome, ctx, death_reason)
    except Exception:
        pass
    # 轨迹导出：事件总线全量落盘 trajectory_<code>.jsonl（append-only 回放用）
    try:
        export_trajectory(challenge_workdir, code, outcome, ctx)
    except Exception:
        pass
    return outcome


async def run_task(task: str, role_hint: str = "", resume: bool = False) -> dict:
    _init_observability()  # 事件总线 → SQLite 落库（只初始化一次）
    log_info("===== 跑分任务开始 =====")
    log_info(
        f"启动配置：模型 {MODEL_NAME}，网关 {BASE_URL}，"
        f"API_KEY {'已配置' if API_KEY else '未配置'}，"
        f"平台 {'已配置' if BENCHMARK_BASE_URL and BENCHMARK_TOKEN else '未配置'}，"
        f"VPN {'已配置' if VPN_CONFIG else '未配置'}，resume={resume}"
    )
    # 任务与系统能力摘要：启动期一眼确认「任务是什么、加载了什么、具备哪些能力」
    log_info(f"任务摘要：{task.strip()[:200]}")
    skills = load_skills()
    log_info(f"技能库加载：{len(skills)} 个技能（{', '.join(sorted(skills))[:300]}）")
    available_tools = sec_tools.available_tools()
    log_info(f"本机安全工具：{len(available_tools)} 个可用（{', '.join(sorted(available_tools))[:300]}）")
    workdir = WORKDIR
    workdir.mkdir(exist_ok=True)
    hooks = EventStreamHooks(workdir, "generic")

    # 外层 Agent 全局模型池（与单题 executor 内部模型池隔离，互不污染）；
    # 主模型 glm 优先，deepseek（flash/pro）仅作灾备兜底。
    global global_model_pool
    global_model_pool = ModelPool()

    log_info(f"== 模型池就绪：{global_model_pool} ==")
    # 同步更新外层 Agent 默认模型为当前模型池入口，避免首次调用仍用旧默认
    if global_model_pool is not None:
        strategist_agent.model = global_model_pool.current.model
        reporter_agent.model = global_model_pool.current.model

    # 清理旧 checkpoint / 事件流（调度器模式：题目进度在平台侧，本地不依赖续跑状态）
    for f in ("state.json", "session.sqlite"):
        (workdir / f).unlink(missing_ok=True)
    (workdir / "events.jsonl").write_text("", encoding="utf-8")

    # 全局 fallback 角色提示（单题会按 unique_code 重新派任，这里只做参考）
    base_role = assign_role(role_hint, task)
    log_info(f"== 全局角色提示：{base_role['role']} ==")

    # ① 战略家·立法与规划（全局一次；合并管理者 + 规划师，减少一轮 LLM 调用）
    # 立法幂等：同任务已有缓存宪章/计划则复用，避免重跑强模型（resume/重启场景）
    log_info("== 战略家：写使命宪章与作战计划 ==")
    set_status(workdir, "legislate", "running")
    _task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()[:16]
    _charter_cache = DATA_DIR / "mission_charter.md"
    _charter_meta = DATA_DIR / "mission_charter.meta.json"
    charter = ""
    global_plan = ""
    try:
        if _charter_cache.exists() and _charter_meta.exists():
            meta = json.loads(_charter_meta.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("task_hash") == _task_hash:
                charter = _charter_cache.read_text(encoding="utf-8")
                global_plan = str(meta.get("plan", ""))
                log_info("== 战略家：复用已缓存宪章/计划（同任务幂等）==")
    except Exception as e:
        log_warn(f"[legislate] 读取宪章缓存失败，重新立法：{str(e)[:120]}")
        charter = ""
        global_plan = ""

    if not charter:
        combined_doc = ""
        for attempt in range(2):
            try:
                combined_result = await run_with_model_fallback(
                    strategist_agent,
                    input=(f"用户任务：\n{task}\n\n"
                           f"角色提示：{base_role['role']}\n"
                           f"角色风格：{base_role.get('style', '')[:200]}\n\n"
                           f"请一次性输出使命宪章和作战计划。"),
                    hooks=hooks,
                    model_pool=global_model_pool,
                    agent_name="Strategist")
                combined_doc = str(combined_result.final_output)
                break
            except Exception as e:
                log_warn(f"[retry] 战略家立法/规划失败（{attempt + 1}/2）：{str(e)[:200]}")
                if attempt == 1:
                    log_error(f"== 战略家立法/规划失败：{str(e)[:200]}，无法继续 ==")
                    set_status(workdir, "legislate", "error")
                    return {"status": "error", "reason": f"strategist_failed: {type(e).__name__}",
                            "results": [], "report": ""}
                await asyncio.sleep(3)
        # 简单拆分：宪章取「# 使命宪章」到「# 作战计划」之间的内容；计划取剩余部分
        charter_part = combined_doc
        plan_part = ""
        if "# 作战计划" in combined_doc:
            idx = combined_doc.index("# 作战计划")
            charter_part = combined_doc[:idx]
            plan_part = combined_doc[idx:]
        charter = charter_part.strip()
        global_plan = plan_part.strip() or charter
        try:
            save_charter(DATA_DIR / "mission_charter.md", charter)
            (_charter_meta).write_text(
                json.dumps({"task_hash": _task_hash, "plan": global_plan},
                           ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log_warn(f"[legislate] 保存宪章缓存失败：{str(e)[:120]}")
    set_status(workdir, "legislate", "finish")

    # ③ 调度器主循环：自适应并发（持续 start 直到 container_busy，天然适配平台容器上限）
    client = PlatformClient(BENCHMARK_BASE_URL, BENCHMARK_TOKEN)
    # 执行者共享模型池：FAST_MODEL（deepseek-v4-flash）优先，glm 兜底；
    # 全局共享一份，避免每题新建池导致灾备状态丢失。
    fast_pool = ModelPool(preferred_name=FAST_MODEL_NAME)
    attempts: Dict[str, int] = {}
    active: Dict[str, asyncio.Task] = {}  # code -> 单题 asyncio 任务
    MAX_SLOTS = int(os.getenv("PLATFORM_MAX_ACTIVE", "3"))  # 软上限保护（默认 3，适配平台活跃容器上限）
    results = []
    fatal_reason = ""
    list_fail_streak = 0  # 拉题目列表连续失败计数（网络抖动重试，超限停止，不崩溃退出）
    LIST_RETRY_MAX = int(os.getenv("LIST_RETRY_MAX", "10"))  # 连续失败上限
    slot_wait = 0  # 连续「有未完成题但拿不到容器名额」轮数（残留容器清理/判停用）
    leak_streak = 0  # 平台侧残留容器连续出现轮数
    close_pending: set = set()  # close 失败重试队列

    # 启动前不清理残留容器：依赖平台 max_active 自然淘汰，避免启动阶段
    # 浪费大量时间逐个关闭容器（参考日志 secai-20260814.log #L26-35）。
    # 若后续出现一上来就 container_busy 导致无法 start 新题的情况，
    # 可在此加一段异步批量 close 可用/停止状态容器的逻辑。
    pass

    async def _run_one(code: str, desc: str, addrs: list, difficulty: str,
                       chal: dict, model_pool: ModelPool) -> str:
        """运行一道已 start 成功的题，返回 outcome。

        start 由主循环同步完成（以便立即感知 container_busy），本函数只负责跑题。
        """
        set_status(workdir, "execute", "running", code=code)
        outcome = await _run_single_challenge(
            code, desc, addrs, charter, task, global_plan, hooks, workdir,
            client, difficulty,
            flag_total=chal.get("flag_count") or 1,
            flag_done=chal.get("correct_flag_count") or 0,
            model_pool=model_pool)
        return outcome


async def _endgame_sweep(client: PlatformClient, model_pool: ModelPool,
                         active: Dict[str, asyncio.Task], attempts: Dict[str, int],
                         results: List[dict], charter: str, task: str,
                         global_plan: str, hooks, workdir: Path,
                         max_retries: int = 2) -> None:
    """终局重扫：主循环退出后再次拉题目列表，尝试未完成的题或重试 stuck 题。

    - 只有在 `TASK_DEADLINE_TS` 配置且剩余时间 >= DEADLINE_SAFE_MARGIN + 60s 时才执行；
    - 优先选「未完成且未做过」的题，其次选「之前 stuck/outcome 为 stuck 或 suspended」的题；
    - 每道题最多重试 max_retries 次，避免无限循环；
    - 全程同步 start 并等单题跑完，不额外占用并发槽位（重扫阶段保守策略）。
    """
    try:
        ts = float(TASK_DEADLINE_TS)
    except ValueError:
        return
    if time.time() >= ts - DEADLINE_SAFE_MARGIN - 60:
        log_info("[endgame] 剩余时间不足，跳过终局重扫")
        return

    log_info("[endgame] 开始终局重扫：拉取题目列表检查是否漏题...")
    try:
        challenges = await asyncio.to_thread(client.list_challenges)
    except Exception as e:
        log_warn(f"[endgame] 拉取题目列表失败：{str(e)[:200]}")
        return
    if not isinstance(challenges, list) or not challenges:
        return

    # 已收录结果
    done_codes = {r["code"] for r in results}
    # 平台侧未完成的题
    not_completed = [c for c in challenges if not c.get("is_completed")
                     and c.get("unique_code")]
    # 优先顺序：未做过的 > stuck 过的 > 其他
    def _score(c):
        code = c.get("unique_code")
        if code not in done_codes:
            return 0
        r = next((x for x in results if x["code"] == code), None)
        if r and r.get("outcome") in ("stuck", "suspended"):
            return 1
        return 2

    not_completed.sort(key=_score)

    for chal in not_completed:
        code = chal.get("unique_code", "")
        if not code:
            continue
        if time.time() >= ts - DEADLINE_SAFE_MARGIN - 30:
            log_info("[endgame] 剩余时间不足，停止重扫")
            break
        # 超过重试上限不再碰
        if attempts.get(code, 0) >= max_retries:
            continue
        desc = chal.get("description", "") or ""
        difficulty = chal.get("difficulty", "")
        log_info(f"[endgame] 重扫 {code}（已尝试 {attempts.get(code, 0)} 次）")
        try:
            addrs = await asyncio.to_thread(client.start_challenge, code)
        except Exception as e:
            log_warn(f"[endgame] start {code} 失败：{str(e)[:200]}")
            continue
        if not addrs:
            attempts[code] = attempts.get(code, 0) + 1
            continue
        try:
            outcome = await _run_single_challenge(
                code, desc, addrs, charter, task, global_plan, hooks, workdir,
                client, difficulty,
                flag_total=chal.get("flag_count") or 1,
                flag_done=chal.get("correct_flag_count") or 0,
                model_pool=model_pool)
            results.append({"code": code, "outcome": outcome})
            if outcome in ("stuck", "suspended", "error"):
                attempts[code] = attempts.get(code, 0) + 1
            log_info(f"[endgame] 单题 {code} 结果：{outcome}")
        except Exception as e:
            log_error(f"[endgame] 单题 {code} 异常：{str(e)[:200]}")
            results.append({"code": code, "outcome": "error"})
            attempts[code] = attempts.get(code, 0) + 1
        finally:
            # 尽力关闭容器释放名额
            for _ in range(3):
                try:
                    if await asyncio.to_thread(client.close_challenge, code):
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)

    try:
        challenges2 = await asyncio.to_thread(client.list_challenges)
        still_unfinished = [c.get("unique_code") for c in (challenges2 or [])
                            if not c.get("is_completed")]
        log_info(f"[endgame] 重扫结束，仍显示未完成：{still_unfinished[:20]}")
    except Exception:
        pass


    try:
        while True:
            # 全局 deadline 检查（比赛硬时限，含安全余量）
            if TASK_DEADLINE_TS:
                try:
                    if time.time() >= float(TASK_DEADLINE_TS) - DEADLINE_SAFE_MARGIN:
                        log_warn("== deadline 到达，停止跑分 ==")
                        break
                except ValueError:
                    pass

            # 拉题目列表（网络抖动/5xx/空列表重试，不因偶发异常崩溃退出）
            try:
                challenges = await asyncio.to_thread(client.list_challenges)
                if not isinstance(challenges, list) or not challenges:
                    # 空列表不是「全部完成」，而是异常（任务未开始/已结束/被清空），
                    # 必须重试并最终报错，避免静默误判为全部完成而提前退出、漏题。
                    raise ValueError("题目列表为空")
                list_fail_streak = 0
            except (TaskEnded, TaskNotFound) as e:
                fatal_reason = str(e)
                log_warn(f"== 平台终止：{fatal_reason} ==")
                break
            except Exception as e:
                list_fail_streak += 1
                log_warn(f"[retry] 拉取题目列表失败({list_fail_streak})：{str(e)[:200]}")
                if list_fail_streak >= LIST_RETRY_MAX:
                    fatal_reason = f"list_challenges 连续失败 {list_fail_streak} 次：{str(e)[:200]}"
                    break
                await asyncio.sleep(2)
                continue

            # 状态对齐：平台侧 running 但本地未记录 = 泄漏槽位
            leaked = [c.get("unique_code") for c in challenges
                      if c.get("container_status") == "running"
                      and c.get("unique_code") not in active]
            if leaked:
                leak_streak += 1
                log_warn(f"[leak] 平台侧残留容器 {leaked}，第 {leak_streak} 轮")
                if leak_streak >= 3:
                    for lc in leaked:
                        try:
                            await asyncio.to_thread(client.close_challenge, lc)
                            log_warn(f"[leak] 已机械关闭残留容器 {lc}")
                        except Exception:
                            pass
                    leak_streak = 0
            else:
                leak_streak = 0

            # close 失败重试队列：每轮尝试关闭之前没关掉的容器
            for cc in list(close_pending):
                try:
                    if await asyncio.to_thread(client.close_challenge, cc):
                        close_pending.discard(cc)
                        log_info(f"[close-retry] {cc} 已关闭，槽位回收")
                except Exception:
                    pass

            # 自适应并发：持续 start 直到 container_busy（名额满）或没题或软上限
            while len(active) < MAX_SLOTS:
                # 排除已活跃的题，避免重复选题
                candidates = [c for c in challenges if c.get("unique_code") not in active]
                # 收尾回捞：所有未完成题都至少放弃过一次时，降低衰减逐个回捞
                endgame = is_endgame(challenges, attempts)
                chal = select_challenge(candidates, attempts, endgame=endgame)
                if chal is None:
                    break
                code = chal.get("unique_code", "")
                desc = chal.get("description", "") or ""
                difficulty = chal.get("difficulty", "")
                # 同步 start：立即感知 container_busy，被拒就停止派发（等活跃题 close 释放）
                try:
                    addrs = await asyncio.to_thread(client.start_challenge, code)
                except ContainerBusy:
                    break
                except (TaskEnded, TaskNotFound) as e:
                    # 平台任务结束/token 无效：全局终止信号，不能当普通启动失败跳过
                    fatal_reason = str(e)
                    log_warn(f"== 平台终止：{fatal_reason} ==")
                    break
                except Exception as e:
                    attempts[code] = attempts.get(code, 0) + 1
                    log_warn(f"[start] 启动 {code} 失败：{str(e)[:200]}，跳过")
                    continue
                if not addrs:
                    attempts[code] = attempts.get(code, 0) + 1
                    continue
                # start 成功：真正占用容器，创建跑题任务
                t = asyncio.create_task(_run_one(code, desc, addrs, difficulty, chal, fast_pool))
                active[code] = t
                log_info(f"[slot] 启动 {code}（活跃 {len(active)}/{MAX_SLOTS}）")

            if fatal_reason:
                # start 阶段遇到 TaskEnded/TaskNotFound：取消并发题，终止整个跑分
                for other in active.values():
                    other.cancel()
                if active:
                    await asyncio.gather(*active.values(), return_exceptions=True)
                break

            if not active:
                # 区分「全部完成」与「还有题但拿不到名额」：后者是残留容器占位，
                # 不能误报为全部完成（否则提前退出、漏题）。
                unfinished = [c for c in challenges if not c.get("is_completed")]
                if not unfinished:
                    log_info("== 全部题目已完成 ==")
                    break
                slot_wait += 1
                if slot_wait == 1:
                    log_warn("[slot] 无可用槽位但仍有未完成题，批量清理非 running 残留容器")
                    for c in unfinished:
                        if c.get("container_status") in ("available", "stopped", ""):
                            try:
                                await asyncio.to_thread(client.close_challenge, c.get("unique_code"))
                            except Exception:
                                pass
                if slot_wait >= 10:
                    fatal_reason = "连续 10 轮拿不到容器名额（疑似平台侧残留/泄漏）"
                    break
                await asyncio.sleep(5)
                continue

            slot_wait = 0  # 成功拿到槽位，重置无槽位计数

            # 等待任一单题完成
            done, _ = await asyncio.wait(
                list(active.values()), return_when=asyncio.FIRST_COMPLETED)

            # 处理完成的单题
            fatal_hit = False
            for t in done:
                code = next(c for c, task in active.items() if task is t)
                del active[code]
                try:
                    outcome = t.result()  # _run_one 直接返回 outcome
                except Exception as e:
                    outcome = "error"
                    log_error(f"[error] 单题 {code} 异常：{str(e)[:200]}")
                # 关闭容器释放名额：检查返回值，失败重试（close 静默失败是 container_busy 灾难根因）
                closed = False
                for _ in range(3):
                    try:
                        closed = await asyncio.to_thread(client.close_challenge, code)
                    except Exception:
                        closed = False
                    if closed:
                        break
                    await asyncio.sleep(1)
                if not closed:
                    log_warn(f"[warn] 单题 {code} 容器关闭失败，进入重试队列")
                    close_pending.add(code)
                else:
                    close_pending.discard(code)
                # attempts 只对真正跑过且未解的题降权（container_busy/start_failed 已在 start 阶段处理）
                if outcome in ("stuck", "suspended", "error"):
                    attempts[code] = attempts.get(code, 0) + 1
                results.append({"code": code, "outcome": outcome})
                log_info(f"== 单题 {code} 结果：{outcome} ==")
                if outcome == "fatal":
                    fatal_reason = "task_ended"
                    fatal_hit = True

            if fatal_hit:
                # 任一题致命错误：取消其余并发任务，终止战役
                # 必须 await 取消完成，确保各任务的 finally 能正常关闭容器
                for other in active.values():
                    other.cancel()
                if active:
                    await asyncio.gather(*active.values(), return_exceptions=True)
                break
    except KeyboardInterrupt:
        log_warn("== 已中断 ==")
        set_status(workdir, "execute", "interrupted")
        for t in active.values():
            t.cancel()
        return {"status": "interrupted", "results": results}
    finally:
        # 清理所有仍活跃的容器，避免残留导致下次 max active
        for code in list(active.keys()):
            try:
                await asyncio.to_thread(client.close_challenge, code)
            except Exception:
                pass
        # finally 兜底：重试队列里仍未关闭的容器也尝试关闭
        for code in list(close_pending):
            try:
                await asyncio.to_thread(client.close_challenge, code)
            except Exception:
                pass

    # ④ 终局重扫：主循环退出后再次拉列表，尝试未完成 / stuck 题，防止提前退出漏题
    if TASK_DEADLINE_TS:
        try:
            await _endgame_sweep(
                client, fast_pool, active, attempts, results, charter, task,
                global_plan, hooks, workdir, max_retries=2)
        except Exception as e:
            log_warn(f"[endgame] 终局重扫异常：{str(e)[:200]}")

    # ⑤ 报告者·收尾（后台异步生成，不阻塞主进程结束；5 秒内能完成则直接用）
    set_status(workdir, "report", "running")

    async def _generate_report() -> str:
        try:
            events_text = (workdir / "events.jsonl").read_text(encoding="utf-8")[-3000:]
        except Exception:
            events_text = ""
        summary = json.dumps(results, ensure_ascii=False)[:1000]
        try:
            report = await run_with_model_fallback(
                reporter_agent,
                input=(f"任务执行结束（{fatal_reason or '题目遍历完成'}）。"
                       f"各题结果：{summary}\n\n事件流尾部：\n{events_text}"),
                hooks=hooks,
                model_pool=global_model_pool,
                agent_name="Reporter")
            return str(report.final_output)
        except Exception as e:
            log_error(f"== 报告生成失败：{str(e)[:200]}，降级为无战报 ==")
            return f"（战报生成失败：{str(e)[:200]}）"

    report_task = asyncio.create_task(_generate_report())
    try:
        report_text = await asyncio.wait_for(report_task, timeout=5.0)
    except asyncio.TimeoutError:
        report_text = "（战报后台生成中，请查看 data/field_notes.md）"
        log_info("[report] 战报后台生成中，未阻塞主进程结束")

    # 无论是否超时，确保战报最终写入 field_notes（join 等待，避免进程退出时战报丢失）
    async def _persist_report():
        try:
            final = await asyncio.wait_for(asyncio.shield(report_task), timeout=30.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            final = "（战报生成超时，未写入）"
        try:
            with (DATA_DIR / "field_notes.md").open("a", encoding="utf-8") as f:
                f.write(f"\n\n# generic · {time.strftime('%Y-%m-%d %H:%M')}\n{final}\n")
        except Exception as e:
            log_warn(f"[report] 写入 field_notes 失败：{str(e)[:120]}")
        print("\n===== 战报 =====\n" + final)
        set_status(workdir, "report", "finish")

    try:
        await asyncio.wait_for(_persist_report(), timeout=35.0)
    except asyncio.TimeoutError:
        log_warn("[report] 战报写入超时，跳过（不影响主流程）")

    # 四指标看板：汇总所有 worker_*/cost_report.json → dashboard.json
    try:
        write_dashboard(workdir)
    except Exception:
        pass

    log_info(f"== 跑分结果：{json.dumps(results, ensure_ascii=False)} ==")
    return {"status": "finished", "results": results, "report": report_text}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    resume = "--resume" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--resume"]
    task = args[0] if args else build_default_task()
    role_hint = args[1] if len(args) > 1 else ""

    # 比赛连续性保险：未预期异常退出后自动重启（指数退避），避免单点崩溃导致全程退出。
    # 注意：平台 TaskEnded / TaskNotFound 已在 run_task 内部正常终止，不会触发此重启；
    # 只有真正未捕获异常（模型/网络/工具/进程异常）才重启。
    SECAI_MAX_RESTARTS = int(os.getenv("SECAI_MAX_RESTARTS", "5"))
    SECAI_RESTART_DELAY = float(os.getenv("SECAI_RESTART_DELAY", "2"))
    out = None
    for restart in range(SECAI_MAX_RESTARTS + 1):
        try:
            out = asyncio.run(run_task(task, role_hint, resume=resume))
            break  # 正常结束
        except KeyboardInterrupt:
            log_warn("== 已中断 ==")
            sys.exit(130)
        except Exception as e:
            if restart >= SECAI_MAX_RESTARTS:
                log_error(f"== 已达最大重启次数 {SECAI_MAX_RESTARTS}，终止 ==")
                sys.exit(1)
            delay = SECAI_RESTART_DELAY * (2 ** restart)
            log_error(f"== 未预期异常退出（{restart + 1}/{SECAI_MAX_RESTARTS}）："
                      f"{type(e).__name__}: {str(e)[:300]}；{delay:.1f}s 后重启 ==")
            time.sleep(delay)
            # 保留 resume 参数，让重启后尽可能复用现场（field_notes / 平台状态）
            resume = True

    if out is not None:
        log_info(f"最终状态：{json.dumps({k: v for k, v in out.items() if k != 'report'}, ensure_ascii=False)}")
