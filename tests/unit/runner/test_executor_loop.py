"""ExecutorLoop 直驱单测（SECAI-PT R1 验收：≥8 条，fake 依赖注入）。

用 fake state/clock/scorer/events/model_pool/executor/hooks/session/client 构造
ExecutorLoop，不触 LLM/平台/真实文件系统（field_notes 落盘 patch 到临时目录），
直驱 _pre_step/_step/_post_step 与 run() 完成 pre/step/post 一轮，
断言 RunnerState 状态迁移与事件（进程级 BUS + hooks）输出。

运行：.venv/bin/python -m pytest tests/unit/runner/test_executor_loop.py -v
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.events import BUS
from core.task_context import TaskContext
from harness.runner.executor import ExecutorLoop
from harness.runner.state import RunnerState


# ---------------------------------------------------------------------------
# fake 替身
# ---------------------------------------------------------------------------
class FakeModel:
    def __init__(self, name: str):
        self.model = name


class FakeEntry:
    def __init__(self, name: str):
        self.name = name
        self.model = FakeModel(name)


class FakeModelPool:
    """最小模型池替身：记录 next/mark_failed/switch_to_role 调用，行为可配。"""

    def __init__(self, has_alternative: bool = True, next_entry="auto"):
        self.has_alternative = has_alternative
        self._next = None if next_entry == "none" else FakeEntry(next_entry)
        self.next_calls = []
        self.mark_failed_calls = []
        self.switch_calls = []

    @property
    def current(self) -> FakeEntry:
        return FakeEntry("main")

    def next(self, **kwargs):
        self.next_calls.append(kwargs)
        return self._next

    def mark_failed(self, name: str, permanent: bool = False) -> None:
        self.mark_failed_calls.append((name, permanent))

    def switch_to_role(self, role: str):
        self.switch_calls.append(role)
        return FakeEntry(role)


class FakeClock:
    """可控时间源：monotonic()/time() 返回固定 now（测试可拨动）。"""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now


class FakeScorer:
    """打分器替身：记录打分调用（当前打分内嵌 hooks，此处验证注入与调用）。"""

    def __init__(self):
        self.calls = []

    def score(self, tool: str, output: str) -> int:
        self.calls.append((tool, output))
        return 1


class FakeExecutor:
    """Agent 替身：只有循环读取的 model/model_settings/tools；无静态 hash（跳过断言）。"""

    def __init__(self):
        self.model = FakeModel("main")
        self.model_settings = SimpleNamespace(parallel_tool_calls=False)
        self.tools = []


class FakeHooks:
    """题级 hooks 替身：记录事件（真实 EventStreamHooks 的事件出口）。"""

    def __init__(self):
        self.task_id = "t1"
        self.events = []

    def emit(self, kind: str, **data) -> None:
        self.events.append({"kind": kind, "data": data})
        BUS.emit(self.task_id, kind, **data)


class FakeSession:
    def __init__(self):
        self.closed = False

    async def get_items(self):
        """会话历史（空）：compact_if_needed 阈值估算用。"""
        return []

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self):
        self.hint_calls = []

    def get_hint(self, code: str) -> str:
        self.hint_calls.append(code)
        return f"hint-for-{code}"


# ---------------------------------------------------------------------------
# 构造 helper
# ---------------------------------------------------------------------------
def _make_ctx(workdir: Path, clock_now: float = 1000.0) -> TaskContext:
    ctx = TaskContext(workdir=workdir / "challenge")
    ctx.wallclock_budget = 600
    ctx.challenge_start_ts = clock_now
    ctx.current_code = "t1"
    return ctx


class ExecutorLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        (self.tmp / "challenge").mkdir(parents=True, exist_ok=True)
        (self.tmp / "sessions").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._td.cleanup()

    # -- helpers ------------------------------------------------------------
    def _loop(self, ctx=None, state=None, *, clock=None, pool=None, executor=None,
              hooks=None, session=None, client=None, events=None, scorer=None,
              tools=None, difficulty: str = "", code: str = "t1", **kw) -> ExecutorLoop:
        clock = clock or FakeClock(1000.0)
        ctx = ctx or _make_ctx(self.tmp, clock_now=clock.monotonic())
        return ExecutorLoop(
            state=state or RunnerState(next_input="开始攻击本题容器。"),
            ctx=ctx, code=code, desc="", brief="任务书", charter="宪章",
            task="测试任务", global_plan="", role={}, field_notes="",
            challenge_workdir=self.tmp / "challenge",
            sessions_dir=self.tmp / "sessions",
            session=session or FakeSession(), executor=executor or FakeExecutor(),
            model_pool=pool or FakeModelPool(), client=client or FakeClient(),
            hooks=hooks or FakeHooks(), outer_hooks=FakeHooks(),
            difficulty=difficulty, db=None,
            clock=clock, events=events, scorer=scorer, tools=tools, **kw)

    def _field_notes(self, stack: ExitStack) -> Path:
        """把 field_notes 写盘路径重定向到临时目录（防污染真实 data/）。"""
        fn = self.tmp / "field_notes.md"
        stack.enter_context(patch("harness.runner.context.FIELD_NOTES_FILE", fn))
        stack.enter_context(patch("harness.runner.executor.FIELD_NOTES_FILE", fn))
        return fn

    def _run_async(self, coro):
        return asyncio.run(coro)

    # -- 用例 ---------------------------------------------------------------
    def test_constructor_injects_fake_dependencies(self):
        """注入语义：state/clock/events/scorer/tools 全部落到实例字段。"""
        state = RunnerState()
        clock = FakeClock()
        bus = FakeHooks()
        scorer = FakeScorer()
        tools = {"shell"}
        loop = self._loop(state=state, clock=clock, events=bus,
                          scorer=scorer, tools=tools)
        self.assertIs(loop.state, state)
        self.assertIs(loop.clock, clock)
        self.assertIs(loop.events, bus)
        self.assertIs(loop.scorer, scorer)
        self.assertIs(loop.tools, tools)
        # 成本档按难度分档（默认 medium）
        self.assertEqual(loop.switch_tokens, 1_000_000)
        self.assertEqual(loop.suspend_tokens, 2_000_000)
        self.assertIn("easy", loop.suspend_tokens_map)

    def test_run_solved_one_round_state_transition_and_events(self):
        """run() 完成 pre/step/post 一轮（solved）：状态迁移 + BUS 事件 + 清理闭环。"""
        ctx = _make_ctx(self.tmp)
        hooks = FakeHooks()
        session = FakeSession()

        async def _fake_run(*args, **kwargs):
            ctx.finalized = True          # Agent 调用 finalize → 单题 solved
            ctx.turn_gain = True          # 本轮产出正向信息增量
            hooks.emit("agent_end", agent="Executor")  # 一轮真实事件
            return SimpleNamespace(final_output="ok")

        state = RunnerState(next_input="开始攻击本题容器。")
        recorder = []
        with ExitStack() as stack:
            fn = self._field_notes(stack)
            stack.enter_context(patch("harness.runner.executor.Runner.run",
                                      new=AsyncMock(side_effect=_fake_run)))
            unsub = BUS.subscribe(recorder.append)
            try:
                outcome = self._run_async(
                    self._loop(ctx=ctx, state=state, hooks=hooks,
                               session=session).run())
            finally:
                unsub()
        self.assertEqual(outcome, "solved")
        # 状态迁移：一轮 pre/step/post 完成，轮次/序号 +1
        self.assertEqual(state.phase, "post")
        self.assertEqual(state.turn_count, 1)
        self.assertEqual(state.steps, 1)
        self.assertEqual(state.seq, 1)
        self.assertEqual(state.outcome, "solved")
        self.assertEqual(state.death_reason, "solved")
        self.assertEqual(ctx.turn_count, 1)
        self.assertEqual(ctx.zero_gain_turns, 0)  # turn_gain → 零增益计数清零
        # 事件：hooks 出口 + 进程级 BUS 均可观测
        self.assertEqual(len(hooks.events), 1)
        self.assertEqual(hooks.events[0]["kind"], "agent_end")
        self.assertTrue(any(ev["kind"] == "agent_end" for ev in recorder))
        # 清理闭环：session 关闭、成本报告落盘
        self.assertTrue(session.closed)
        self.assertTrue((self.tmp / "challenge" / "cost_report.json").exists())
        self.assertTrue(fn.exists())

    def test_pre_step_resets_turn_state(self):
        """_pre_step：turn 状态清零并返回 (False, '')，无终止条件命中。"""
        ctx = _make_ctx(self.tmp)
        ctx.turn_tool_count = 3
        ctx.turn_gain = True
        ctx.turn_net_fail = True
        loop = self._loop(ctx=ctx)
        br, dr = self._run_async(loop._pre_step())
        self.assertEqual((br, dr), (False, ""))
        self.assertEqual(ctx.turn_tool_count, 0)
        self.assertFalse(ctx.turn_gain)
        self.assertFalse(ctx.turn_net_fail)

    def test_pre_step_wallclock_extension_then_timeout(self):
        """_pre_step：墙钟超预算先延长半档一次，再超则机械换题 wallclock_timeout。"""
        ctx = _make_ctx(self.tmp, clock_now=1000.0)
        clock = FakeClock(now=2000.0)  # 已超 600s 预算
        loop = self._loop(ctx=ctx, clock=clock)
        br, dr = self._run_async(loop._pre_step())
        self.assertEqual((br, dr), (False, ""))           # 首次：有进展 → 延长半档
        self.assertTrue(getattr(ctx, "_wallclock_extended", False))
        self.assertEqual(ctx.wallclock_budget, 900)
        br2, dr2 = self._run_async(loop._pre_step())
        self.assertEqual((br2, dr2), (True, "wallclock_timeout"))  # 二次：机械换题

    def test_pre_step_token_switch_then_suspend(self):
        """_pre_step：token 到换脑档切模型（switched=True），再达挂起档 token_suspend。"""
        ctx = _make_ctx(self.tmp)
        pool = FakeModelPool(has_alternative=True, next_entry="alt")
        loop = self._loop(ctx=ctx, pool=pool)
        # init 时 cost_base=0；模拟 hooks 已累计 token 到挂起档之上
        ctx.token_usage["total"] = 2_500_000
        br, dr = self._run_async(loop._pre_step())
        self.assertEqual(dr, "token_suspend")
        self.assertTrue(br)
        self.assertTrue(loop.state.switched)
        self.assertEqual(loop.executor.model.model, "alt")
        self.assertEqual(pool.next_calls[0]["reason"], "token_threshold")

    def test_pre_step_brain_upgrade_on_exploit(self):
        """_pre_step：进入 exploit 阶段切换 strong 模型并开并行工具调用。"""
        ctx = _make_ctx(self.tmp)
        ctx.phase = "exploit"
        executor = FakeExecutor()
        loop = self._loop(ctx=ctx, executor=executor)
        br, _ = self._run_async(loop._pre_step())
        self.assertFalse(br)
        self.assertTrue(getattr(ctx, "_brain_upgraded", False))
        self.assertTrue(getattr(ctx, "_on_strong_model", False))
        self.assertTrue(executor.model_settings.parallel_tool_calls)
        self.assertEqual(executor.model.model, "strong")

    def test_step_model_failure_fallback(self):
        """_step：模型服务失败 → mark_failed + next 换模型 → 返回 False（同轮重试）。"""
        ctx = _make_ctx(self.tmp)
        pool = FakeModelPool(next_entry="alt")
        executor = FakeExecutor()
        exc = type("FakeAPIError", (Exception,), {})("rate limited")
        exc.status_code = 429
        loop = self._loop(ctx=ctx, pool=pool, executor=executor)
        with patch("harness.runner.executor.Runner.run",
                   new=AsyncMock(side_effect=exc)):
            cont = self._run_async(loop._step())
        self.assertFalse(cont)                      # 外层 continue 重试
        self.assertEqual(loop.executor.model.model, "alt")  # 已切换到灾备模型
        self.assertEqual(pool.mark_failed_calls, [("main", False)])
        self.assertTrue(pool.next_calls[0]["reason"].startswith("model_failure:"))
        self.assertEqual(loop.state.outcome, "stopped")  # 非耗尽，终态未变

    def test_step_model_exhausted_marks_stuck(self):
        """_step：模型全不可用 → outcome=stuck / death_reason=model_exhausted。"""
        ctx = _make_ctx(self.tmp)
        pool = FakeModelPool(next_entry="none")
        exc = type("FakeAPIError", (Exception,), {})("no quota")
        exc.status_code = 429
        loop = self._loop(ctx=ctx, pool=pool)
        with patch("harness.runner.executor.Runner.run",
                   new=AsyncMock(side_effect=exc)):
            cont = self._run_async(loop._step())
        self.assertFalse(cont)
        self.assertEqual(loop.state.outcome, "stuck")
        self.assertEqual(loop.state.death_reason, "model_exhausted")

    def test_post_step_tracks_zero_gain_and_continues(self):
        """_post_step：零增量 +1 且未触发任何熔断 → 返回继续指令。"""
        ctx = _make_ctx(self.tmp)
        loop = self._loop(ctx=ctx)
        br, ni, dr = self._run_async(loop._post_step())
        self.assertEqual((br, dr), (False, ""))
        self.assertEqual(ctx.zero_gain_turns, 1)
        self.assertIn("继续攻击本题", ni)

    def test_post_step_empty_idle_fuse(self):
        """_post_step：连续空转达 SINGLE_EMPTY_TURNS → empty_idle 机械换题。"""
        ctx = _make_ctx(self.tmp)
        ctx.empty_turns = 3  # 已空转 3 轮，本轮仍无工具调用
        loop = self._loop(ctx=ctx)
        br, _, dr = self._run_async(loop._post_step())
        self.assertTrue(br)
        self.assertEqual(dr, "empty_idle")
        self.assertEqual(ctx.empty_turns, 4)

    def test_post_step_finalized_returns_solved(self):
        """_post_step：ctx.finalized（Agent 走 finalize）→ (True, '', 'solved')。"""
        ctx = _make_ctx(self.tmp)
        ctx.finalized = True
        loop = self._loop(ctx=ctx)
        br, _, dr = self._run_async(loop._post_step())
        self.assertEqual((br, dr), (True, "solved"))

    def test_post_step_replan_once_then_directive_no_progress(self):
        """_post_step：3 轮零增量触发 fork_analyze 复盘一次；复盘后再停滞直接换题。"""
        ctx = _make_ctx(self.tmp)
        ctx.zero_gain_turns = 3
        loop = self._loop(ctx=ctx)
        with patch("harness.runner.executor._replan",
                   new=AsyncMock(return_value="next_directive")):
            br, ni, dr = self._run_async(loop._post_step())
        self.assertEqual((br, dr), (False, ""))
        self.assertEqual(ni, "作战计划已更新，按新计划继续攻击本题。")
        self.assertEqual(ctx.replan_count, 1)
        self.assertEqual(ctx.plan, "next_directive")
        self.assertEqual(ctx.zero_gain_turns, 0)
        self.assertEqual(loop.state.intervention_count, 1)
        # 复盘后再次 3 轮零增量：不再复盘，直接机械换题
        ctx.zero_gain_turns = 3
        br2, _, dr2 = self._run_async(loop._post_step())
        self.assertTrue(br2)
        self.assertEqual(dr2, "directive_no_progress")

    def test_post_step_reaps_subtask_results(self):
        """_post_step：后台子任务完成被非阻塞收割 → 分支结果注入下一轮。"""
        ctx = _make_ctx(self.tmp)
        ctx.subtasks.append({"id": "s1", "status": "running",
                             "result": {"summary": "验证完成：端点可达",
                                        "flag": None}})

        async def _scenario(loop):
            async def _noop():
                return None
            job = asyncio.create_task(_noop())
            ctx.subtask_jobs["s1"] = job
            await asyncio.sleep(0)  # 让子任务 job 结束
            return await loop._post_step()

        loop = self._loop(ctx=ctx)
        br, ni, dr = self._run_async(_scenario(loop))
        self.assertEqual((br, dr), (False, ""))
        self.assertIn("【分支结果】", ni)
        self.assertIn("s1", ni)
        self.assertTrue(ctx.turn_gain)          # 分支结果视为正向增量
        self.assertNotIn("s1", ctx.subtask_jobs)  # 已从收割队列移除


if __name__ == "__main__":
    unittest.main()
