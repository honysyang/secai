"""后台子任务三闸门调度（原 app/main.py 子任务函数按职责搬离）。

- _run_subtasks：并发启动 pending 子任务（asyncio.Task 后台化，不阻塞主循环），
  每子任务携带独立 SubtaskBudget（token/turn/墙上时间任一耗尽即停）；
- _reap_subtasks：非阻塞收割已完成子任务，返回结果摘要供主循环注入下一轮 input；
- _cancel_all_subtasks：取消并等待所有后台子任务（第三道闸门，父任务收尾统一回收）。

等价变换搬移：逻辑与 app.main 时代完全一致，只把模块级常量一并带至本文件。
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path

from agents import MaxTurnsExceeded
from agents.memory import SQLiteSession

from arsenal.registries.role_registry import assign_role
from core.agents_def import build_executor
from core.hooks import EventStreamHooks
from core.task_context import SUBTASK_MAX_CONCURRENT, SubtaskBudget, TaskContext
from harness.runner.context import _load_field_notes
from runtime.log import log_info, log_warn
from runtime.model_fallback import run_with_model_fallback

SUBTASK_MAX_TURNS = 8  # 每个子任务最多 LLM 回合数（内部 ReAct，Agent 可 finalize 提前结束）
SUBTASK_TIMEOUT_SECONDS = 600  # 每个后台子任务总超时（10 分钟）


async def _run_subtasks(ctx, pending, challenge_workdir: Path, brief: str,
                        model=None, model_settings=None, model_pool=None) -> None:
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
            # R2：子任务标记 + 启动快照 keys——set 新 key（不在快照）时共享情报给主线
            sub_ctx.is_subtask = True
            sub_ctx._snapshot_keys = set(ctx.blackboard.keys())
            sub_ctx.token_usage = dict(ctx.token_usage)
            sub_ctx.enabled_tools = set(ctx.enabled_tools) if ctx.enabled_tools is not None else None
            sub_ctx.phase = "recon"  # 子任务从 recon 起跑（R3：避免继承父阶段语义错位）
            sub_ctx.plan = ctx.plan
            sub_ctx.wallclock_budget = budget.timeout_seconds
            sub_session = SQLiteSession(session_id=f"sub_{sub['id']}",
                                        db_path=str(challenge_workdir / f"sub_{sub['id']}.sqlite"))
            # H5：句柄登记到父 ctx，收尾统一 close 后再物理删 sub_*.sqlite（防残留句柄）
            ctx.open_sub_sessions[sub["id"]] = sub_session
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
                # R4：子任务 flag 不 append 进父 correct_flags——flag 计数以平台
                # correct_flag_count 为唯一真相源（_is_completed 已按平台复核），
                # 避免双轨记账与平台不一致；子任务 flag 仍可见于黑板 subtask:<id>。
                # 关闭子任务 session（修补 6：连接/句柄生命周期闭环）
                try:
                    sub_session.close()
                except Exception as e:
                    log_warn(f"[degraded] 子任务 {sub['id']} 关闭 session 失败：{str(e)[:120]}")
                finally:
                    # H5：句柄已关 → 从登记表移除（未移除的由父收尾兜底 close + 删文件）
                    ctx.open_sub_sessions.pop(sub["id"], None)

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
    await asyncio.gather(*tasks, return_exceptions=True)
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
