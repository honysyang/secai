"""通用多智能体端到端 Demo：
管理者立法 → 角色派任 → 执行者执行（渐进披露 + 历史压缩 + 断点续跑）→ 报告者收尾。

不依赖任何靶场平台 / flag / 提交铁律，只跑通「多智能体 + 角色 + 多 Skills 渐进披露」主流程。

用法：
    python -m app.main "<任务描述>" [角色提示]
    python -m app.main                              # 使用默认本地侦察任务
    python -m app.main --resume                     # 从上次 checkpoint 续跑
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from pathlib import Path
from typing import Dict

from agents import Runner
from agents.exceptions import MaxTurnsExceeded
from agents.memory import SQLiteSession

from core.agents_def import (manager_agent, planner_agent, reporter_agent, coach_agent,
                            build_executor, build_subtask_executor)
from runtime.budget import (HINT_BUDGET_RATIO, COST_LIMITS, SUSPEND_SECONDS,
                            build_escalation_models, should_pull_hint_by_budget)
from core.charter import save_charter
from adapters.config import BENCHMARK_BASE_URL, BENCHMARK_TOKEN
from core.context_manager import compact_if_needed
import adapters.db as db_mod
from demo_tools import build_default_tools
from core.events import BUS
from core.hooks import EventStreamHooks
from platform.platform_client import PlatformClient, TaskEnded, TaskNotFound, ContainerBusy
from arsenal.registries.role_registry import assign_role
from platform.scheduler import select_challenge, decide_stuck_action, SINGLE_EMPTY_TURNS
from solvecraft.solution_templates import append_solution_template, load_solution_hint
from runtime.status import set_status
from runtime.stop_policy import TASK_DEADLINE_TS, DEADLINE_SAFE_MARGIN
from core.task_context import TaskContext

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

WORKDIR = DATA_DIR / "worker_generic"

_db_initialized = False


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

# 跑分任务模板已抽离到 prompts/tsec_task.txt（见下方 build_default_task）


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
ZERO_GAIN_REPLAN_TURNS = 5  # 连续零信息增量轮数触发 replan（替代旧「阶段停滞」判据）
REPLAN_MAX = 3           # 最多 replan 次数，防止无限重规划
COACH_AFTER_HINT_TURNS = 3  # hint 后仍零增益 3 轮触发软干预教练（每题目仅 1 次）


async def _run_subtasks(ctx, pending, workdir, brief) -> None:
    """并发调度 pending 子任务：每个子任务独立会话 + 独立 context，结果结构化回传。

    子任务用 finish_subtask 结束协议（summary/findings/flag），主 Agent 只拿到结构化结论，
    不接触子任务的海量工具输出（上下文隔离）。结果写回主黑板（subtask:<id>）。
    """
    sub_executor = build_subtask_executor(ctx.role, ctx.charter, brief,
                                         field_notes=_load_field_notes())

    async def _run_one(sub):
        sub["status"] = "running"
        # 独立 context：复制渐进披露技能，共享黑板/token 引用（结果与用量汇总回主 ctx）
        sub_ctx = TaskContext(
            workdir=workdir,
            disclosed_skills=list(ctx.disclosed_skills),
            task=ctx.task,
            charter=ctx.charter,
            role=ctx.role,
        )
        sub_ctx.blackboard = ctx.blackboard
        sub_ctx.token_usage = ctx.token_usage
        sub_ctx.enabled_tools = set(ctx.enabled_tools) if ctx.enabled_tools is not None else None
        sub_ctx.phase = ctx.phase
        sub_ctx.plan = ctx.plan
        sub_session = SQLiteSession(session_id=f"sub_{sub['id']}",
                                    db_path=str(workdir / f"sub_{sub['id']}.sqlite"))
        sub_hooks = EventStreamHooks(workdir, f"sub_{sub['id']}")
        try:
            await Runner.run(
                sub_executor,
                input=f"子任务：{sub['desc']}\n独立完成这个子任务，完成后调用 finish_subtask 提交结构化结论。",
                context=sub_ctx, hooks=sub_hooks, session=sub_session,
                max_turns=SUBTASK_MAX_TURNS)
            payload = sub_ctx.final_payload or {}
            if sub_ctx.finalized and payload.get("summary"):
                # 走了结束协议：结构化回传 summary/findings/flag
                sub["result"] = {
                    "summary": payload.get("summary", ""),
                    "findings": payload.get("findings", []),
                    "flag": payload.get("flag"),
                }
            else:
                # 未走结束协议：降级为未完成标记
                sub["result"] = {
                    "summary": "[未走结束协议] " + str(payload.get("summary", ""))[:200],
                    "findings": [], "flag": None,
                }
        except MaxTurnsExceeded:
            sub["result"] = {"summary": "[未走结束协议] 子任务达到回合上限",
                             "findings": [], "flag": None}
        except Exception as e:
            sub["result"] = {"summary": f"[子任务异常] {str(e)[:200]}",
                             "findings": [], "flag": None}
        finally:
            sub["status"] = "done"
            ctx.blackboard[f"subtask:{sub['id']}"] = {
                "value": json.dumps(sub["result"], ensure_ascii=False),
                "status": "done", "ts": int(time.time()),
                "verified": True,
            }

    await asyncio.gather(*(_run_one(s) for s in pending))


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
    """执行中计划修正：把黑板 + 近期事件流尾部 + 原计划交给 Planner，产出修正后的计划。"""
    events_text = (ctx.workdir / "events.jsonl").read_text(encoding="utf-8")[-4000:]
    blackboard_text = json.dumps(ctx.blackboard, ensure_ascii=False)[:2000]
    result = await Runner.run(
        planner_agent,
        input=(f"原任务：\n{task}\n\n使命宪章：\n{charter}\n\n"
               f"原作战计划：\n{ctx.plan}\n\n"
               f"当前黑板（已完成事项）：\n{blackboard_text}\n\n"
               f"近期执行情况（事件流尾部）：\n{events_text}\n\n"
               f"请指出原计划哪里判断错了，并给出修正后的作战计划。"),
        hooks=hooks)
    return str(result.final_output)


async def _coach(ctx, brief, hooks) -> str:
    """软干预教练：基于黑板 + 事件流尾部给 1~2 条具体可试方向（轻量，单轮）。

    与 _replan 分工：replan 产出完整作战计划（重），coach 只给方向建议（轻）。
    输入裁剪（黑板 2000 字符 + 事件流尾 4000 字符）控制 token 成本。
    """
    blackboard_text = json.dumps(ctx.blackboard, ensure_ascii=False)[:2000]
    try:
        events_text = (ctx.workdir / "events.jsonl").read_text(encoding="utf-8")[-4000:]
    except Exception:
        events_text = ""
    skills = ", ".join(ctx.disclosed_skills) or "无"
    result = await Runner.run(
        coach_agent,
        input=(f"题目：\n{brief}\n\n"
               f"已解锁技能：{skills}\n\n"
               f"当前黑板（已尝试/已完成）：\n{blackboard_text}\n\n"
               f"近期执行动作（事件流尾部）：\n{events_text}\n\n"
               f"请给出 1~2 条具体可执行的新方向。"),
        hooks=hooks)
    return str(result.final_output)


async def _run_single_challenge(code: str, desc: str, addrs: list, charter: str,
                                task: str, global_plan: str, hooks, workdir: Path,
                                client: PlatformClient, difficulty: str = "",
                                flag_total: int = 1, flag_done: int = 0) -> str:
    """对一道题执行完整渗透循环，返回 outcome：solved / stuck / fatal。

    单题独立 context + 独立 session；停滞时机械看 hint / 换题（调度器决策），
    选题/换题/看 hint 不由 LLM 自觉——这是报告 P0-4 的核心修复。
    """
    # 题级独立工作区：3 槽并发下每题独立 events/session/artifacts，避免交错
    challenge_workdir = workdir / f"worker_{code}"
    challenge_workdir.mkdir(parents=True, exist_ok=True)
    challenge_hooks = EventStreamHooks(challenge_workdir, code)

    role = assign_role(code, desc)  # 题级派任（P0-5：按 unique_code 前缀 + 描述）
    print(f"== 单题 {code}：派任 {role['role']} ==")
    ctx = TaskContext(workdir=challenge_workdir, disclosed_skills=list(role["playbooks"]),
                      task=task, charter=charter, role=role)
    ctx.blackboard = _load_blackboard(challenge_workdir)  # 回注上次尝试进度（挂起/重试）
    ctx.enabled_tools = build_default_tools()
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
    executor = build_executor(role, charter, brief,
                              field_notes=load_notes_for(code) or _load_field_notes())
    session = SQLiteSession(session_id=f"challenge_{code}",
                            db_path=str(workdir / f"challenge_{code}.sqlite"))

    turn_count = 0
    hint_used = False
    coach_used = False  # 软干预教练：每题目仅触发 1 次
    outcome = "stopped"
    # 成本治理：本尝试的 token/时钟起点 + 换脑/挂起档
    cost_limit = COST_LIMITS.get(str(difficulty).lower(), COST_LIMITS.get("medium", {}))
    switch_tokens = cost_limit.get("switch_tokens", 0)
    suspend_tokens = cost_limit.get("suspend_tokens", 0)
    cost_base_tokens = ctx.token_usage.get("total", 0)
    suspend_time_base = time.monotonic()
    switched = False
    escalation_llms = build_escalation_models()
    suspend_tokens_map = {d: v.get("suspend_tokens", 0)
                          for d, v in COST_LIMITS.items()}
    db = db_mod.get_db()
    if db is not None:
        db.task_started(code, desc)  # 登记题目生命周期（监控页任务列表/状态）
    next_input = f"开始攻击本题容器：{addrs}。先做信息收集，识别技术栈与入口。"
    try:
        while True:
            turn_count += 1
            # 成本治理：换脑档 + 挂起档（token / 时钟到档即停止本次尝试腾槽）
            used = ctx.token_usage.get("total", 0) - cost_base_tokens
            if switch_tokens and not switched and used >= switch_tokens and escalation_llms:
                new_model = random.choice(escalation_llms)
                old = getattr(executor.model, "model", "?")
                executor.model = new_model  # 无感知换脑：新模型以为轨迹全是自己的
                switched = True
                print(f"  [switch] 单题 {code} token {used} 到换脑档，"
                      f"{old} -> {getattr(new_model, 'model', '?')}")
            if suspend_tokens and used >= suspend_tokens:
                outcome = "suspended"
                break
            if SUSPEND_SECONDS and time.monotonic() - suspend_time_base >= SUSPEND_SECONDS:
                outcome = "suspended"
                break
            ctx.turn_count = turn_count
            ctx.turn_tool_count = 0
            ctx.turn_gain = False
            ctx.turn_net_fail = False  # 本轮网络不可达清零（hooks 命中时置位）
            prev_phase = ctx.phase
            try:
                await Runner.run(executor, input=next_input, context=ctx,
                                 hooks=challenge_hooks, session=session, max_turns=1)
            except MaxTurnsExceeded:
                pass

            if ctx.phase == prev_phase:
                ctx.stuck_turns += 1
            else:
                ctx.stuck_turns = 0

            if ctx.turn_gain:
                ctx.zero_gain_turns = 0
            else:
                ctx.zero_gain_turns += 1

            # 网络不可达累计：连续命中 → 快速换题（防 VPN 断开后死磕同一题）
            if ctx.turn_net_fail:
                ctx.net_fail_turns += 1
            else:
                ctx.net_fail_turns = 0

            # 致命错误 → 全局终止（任务结束 / token 无效）
            if ctx.fatal:
                outcome = "fatal"
                break

            # 网络不可达：连续 2 次连接失败/超时 → 判定本题不可达，机械换题
            if ctx.net_fail_turns >= 2:
                print(f"  [skip] 单题 {code} 连续 {ctx.net_fail_turns} 次网络不可达，机械换题")
                outcome = "stuck"
                break

            # 单题完成（Agent 主动 finalize）
            if ctx.finalized:
                outcome = "solved"
                break

            # 单题空转（连续无工具调用）→ 放弃换题
            if ctx.turn_tool_count == 0:
                ctx.empty_turns += 1
                if ctx.empty_turns >= SINGLE_EMPTY_TURNS:
                    print(f"  [skip] 单题 {code} 连续 {ctx.empty_turns} 轮空转，机械换题")
                    outcome = "stuck"
                    break
            else:
                ctx.empty_turns = 0

            # 单题停滞机械决策：先看 hint，看完仍无进展则换题
            action = decide_stuck_action(ctx.zero_gain_turns, hint_used, difficulty)
            # hint 预算：卡题（≥2 条失败路径）且 token 达挂起档比例时提前拉提示
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
                print(f"  [hint] 单题 {code} 停滞，机械看提示")
                next_input = f"本题卡住。系统已获取提示：\n{hint}\n\n请结合提示继续攻击本题。"
                continue
            if action == "skip":
                print(f"  [skip] 单题 {code} 已停滞 {ctx.zero_gain_turns} 轮，机械换题")
                outcome = "stuck"
                break

            # 软干预（教练式转向）：hint 后仍零增益，给一次具体方向建议再给机会
            if (hint_used and ctx.zero_gain_turns >= COACH_AFTER_HINT_TURNS
                    and not coach_used):
                coach_used = True
                advice = await _coach(ctx, brief, challenge_hooks)
                # 建议写进题级黑板（战术记忆，半持久纠偏；verified=False 表示待验证方向）
                ctx.blackboard["coach_advice"] = {
                    "value": advice,
                    "status": "done",
                    "ts": int(time.time()),
                    "verified": False,
                }
                print(f"  [coach] 单题 {code} hint 后仍停滞 {ctx.zero_gain_turns} 轮，教练给方向")
                next_input = (f"本题卡住。教练建议（可尝试的新方向）：\n{advice}\n\n"
                              f"请结合建议继续尝试，产出新证据。")
                continue

            # 单题 replan（停滞重新规划，仍针对本题）
            if ctx.zero_gain_turns >= ZERO_GAIN_REPLAN_TURNS and ctx.replan_count < REPLAN_MAX:
                ctx.plan = await _replan(ctx, brief, charter, role, hooks)
                ctx.replan_count += 1
                ctx.zero_gain_turns = 0
                next_input = "作战计划已更新，按新计划继续攻击本题。"
                continue

            # 子任务并发调度
            pending = [s for s in ctx.subtasks if s["status"] == "pending"]
            if pending:
                await _run_subtasks(ctx, pending, workdir, brief)

            # 历史压缩
            if await compact_if_needed(session, ctx):
                print("  [compact] 单题历史已压缩")

            next_input = "继续攻击本题：调用工具产出新证据增量，或调用 finalize 提交本题结论。"
    finally:
        # 清理单题 session 文件，避免堆积（保留 events/artifacts 作为证据）
        try:
            (challenge_workdir / "session.sqlite").unlink(missing_ok=True)
        except Exception:
            pass
    answer = ""
    if outcome == "solved" and ctx.final_payload:
        answer = str(ctx.final_payload.get("findings", ""))[:500]
    _append_mechanical_note(code, outcome, ctx)  # 题级机械沉淀（零 LLM，按题写档案）
    if outcome == "solved":
        append_solution_template(code, desc, ctx)  # 正向解法模板沉淀（同类题复用）
    if db is not None:
        db.task_finished(code, outcome, answer)  # 登记题目终态（监控页状态/结论）
    return outcome


async def run_task(task: str, role_hint: str = "", resume: bool = False) -> dict:
    _init_observability()  # 事件总线 → SQLite 落库（只初始化一次）
    workdir = WORKDIR
    workdir.mkdir(exist_ok=True)
    hooks = EventStreamHooks(workdir, "generic")

    # 清理旧 checkpoint / 事件流（调度器模式：题目进度在平台侧，本地不依赖续跑状态）
    for f in ("state.json", "session.sqlite"):
        (workdir / f).unlink(missing_ok=True)
    (workdir / "events.jsonl").write_text("", encoding="utf-8")

    # ① 管理者·立法（全局一次）
    print("== 管理者：写使命宪章 ==")
    set_status(workdir, "legislate", "running")
    charter_result = await Runner.run(manager_agent, input=f"用户任务：{task}",
                                      hooks=hooks)
    charter = str(charter_result.final_output)
    save_charter(DATA_DIR / "mission_charter.md", charter)
    set_status(workdir, "legislate", "finish")

    # 全局 fallback 角色（单题会按 unique_code 重新派任）
    base_role = assign_role(role_hint, task)
    print(f"== 全局角色：{base_role['role']} ==")

    # ② 规划师·全局计划（一次，单题复用 + 单题 brief 补充）
    print("== 规划师：任务深度分析，产出作战计划 ==")
    plan_result = await Runner.run(
        planner_agent,
        input=(f"用户任务：\n{task}\n\n使命宪章：\n{charter}\n\n"
               f"派任角色：{base_role['role']}\n\n请产出作战计划。"),
        hooks=hooks)
    global_plan = str(plan_result.final_output)

    # ③ 调度器主循环：自适应并发（持续 start 直到 container_busy，天然适配平台容器上限）
    client = PlatformClient(BENCHMARK_BASE_URL, BENCHMARK_TOKEN)
    attempts: Dict[str, int] = {}
    active: Dict[str, asyncio.Task] = {}  # code -> 单题 asyncio 任务
    MAX_SLOTS = 6  # 软上限保护（防平台异常无限 start；实际并发由 container_busy 反馈决定）
    results = []
    fatal_reason = ""

    # 启动前清理残留容器（上次运行残留的活跃容器，避免一上来就 max active）
    try:
        for c in client.list_challenges():
            if c.get("container_status") in ("available", "stopped", ""):
                client.close_challenge(c.get("unique_code"))
                print(f"  [cleanup] 清理残留容器 {c.get('unique_code')}")
    except Exception:
        pass

    async def _run_one(code: str, desc: str, addrs: list, difficulty: str,
                       chal: dict) -> str:
        """运行一道已 start 成功的题，返回 outcome。

        start 由主循环同步完成（以便立即感知 container_busy），本函数只负责跑题。
        """
        set_status(workdir, "execute", "running", code=code)
        outcome = await _run_single_challenge(
            code, desc, addrs, charter, task, global_plan, hooks, workdir,
            client, difficulty,
            flag_total=chal.get("flag_count") or 1,
            flag_done=chal.get("correct_flag_count") or 0)
        return outcome

    try:
        while True:
            # 全局 deadline 检查（比赛硬时限，含安全余量）
            if TASK_DEADLINE_TS:
                try:
                    if time.time() >= float(TASK_DEADLINE_TS) - DEADLINE_SAFE_MARGIN:
                        print("== deadline 到达，停止跑分 ==")
                        break
                except ValueError:
                    pass

            # 拉题目列表
            try:
                challenges = await asyncio.to_thread(client.list_challenges)
            except (TaskEnded, TaskNotFound) as e:
                fatal_reason = str(e)
                print(f"== 平台终止：{fatal_reason} ==")
                break

            # 自适应并发：持续 start 直到 container_busy（名额满）或没题或软上限
            while len(active) < MAX_SLOTS:
                # 排除已活跃的题，避免重复选题
                candidates = [c for c in challenges if c.get("unique_code") not in active]
                chal = select_challenge(candidates, attempts)
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
                except Exception as e:
                    attempts[code] = attempts.get(code, 0) + 1
                    print(f"  [start] 启动 {code} 失败：{str(e)[:200]}，跳过")
                    continue
                if not addrs:
                    attempts[code] = attempts.get(code, 0) + 1
                    continue
                # start 成功：真正占用容器，创建跑题任务
                t = asyncio.create_task(_run_one(code, desc, addrs, difficulty, chal))
                active[code] = t
                print(f"  [slot] 启动 {code}（活跃 {len(active)}/{MAX_SLOTS}）")

            if not active:
                print("== 全部题目已完成（无可选题目）==")
                break

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
                    print(f"  [error] 单题 {code} 异常：{str(e)[:200]}")
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
                    print(f"  [warn] 单题 {code} 容器关闭失败，名额可能泄漏")
                # attempts 只对真正跑过且未解的题降权（container_busy/start_failed 已在 start 阶段处理）
                if outcome in ("stuck", "suspended", "error"):
                    attempts[code] = attempts.get(code, 0) + 1
                results.append({"code": code, "outcome": outcome})
                print(f"== 单题 {code} 结果：{outcome} ==")
                if outcome == "fatal":
                    fatal_reason = "task_ended"
                    fatal_hit = True

            if fatal_hit:
                # 任一题致命错误：取消其余并发任务，终止战役
                for other in active.values():
                    other.cancel()
                break
    except KeyboardInterrupt:
        print("\n== 已中断 ==")
        set_status(workdir, "execute", "interrupted")
        for t in active.values():
            t.cancel()
        return {"status": "interrupted", "results": results}
    finally:
        # 清理所有仍活跃的容器，避免残留导致下次 max active
        for code in list(active.keys()):
            try:
                client.close_challenge(code)
            except Exception:
                pass

    # ④ 报告者·收尾
    set_status(workdir, "report", "running")
    events_text = (workdir / "events.jsonl").read_text(encoding="utf-8")[-6000:]
    summary = json.dumps(results, ensure_ascii=False)[:2000]
    report = await Runner.run(
        reporter_agent,
        input=(f"任务执行结束（{fatal_reason or '题目遍历完成'}）。"
               f"各题结果：{summary}\n\n事件流尾部：\n{events_text}"),
        hooks=hooks)
    report_text = str(report.final_output)
    (DATA_DIR / "field_notes.md").open("a", encoding="utf-8").write(
        f"\n\n# generic · {time.strftime('%Y-%m-%d %H:%M')}\n{report_text}\n")
    print("\n===== 战报 =====\n" + report_text)
    set_status(workdir, "report", "finish")

    print(f"\n== 跑分结果：{json.dumps(results, ensure_ascii=False)} ==")
    return {"status": "finished", "results": results, "report": report_text}


if __name__ == "__main__":
    resume = "--resume" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--resume"]
    task = args[0] if args else build_default_task()
    role_hint = args[1] if len(args) > 1 else ""
    out = asyncio.run(run_task(task, role_hint, resume=resume))
    print("\n最终状态：", json.dumps({k: v for k, v in out.items() if k != "report"},
                                  ensure_ascii=False))
