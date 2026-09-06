"""SessionManager 多目标并行单测（R4 验收：≥2 并行执行循环的编排底座）。

用 fake runner（零 LLM/平台依赖）直驱 SessionManager，验证：
- 两个目标 session 并发执行且上下文（state/bus）互不污染；
- 事件按 session 隔离（独立 seq / 独立历史）；
- stop 幂等；list/status 编排面查询；broadcast 广播；runner 异常结构化收口。

运行：.venv/bin/python -m pytest tests/unit/test_session_manager.py -v
"""
from __future__ import annotations

import asyncio
import unittest

from harness.session_manager import SessionManager


class SessionManagerTest(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_two_targets_run_concurrently_and_contexts_isolated(self):
        """两个并行任务同时跑互不干扰：A 等 B 开跑的栅栏证明并发，state 各自独立。"""

        async def scenario():
            mgr = SessionManager()
            b_started = asyncio.Event()
            a_passed = {}

            async def runner_a(ctx):
                ctx.state["probe"] = "only-a"
                # 若管理器串行 await（不 create_task），B 永远不会开跑 → 2s 超时转 error
                await asyncio.wait_for(b_started.wait(), timeout=2.0)
                a_passed["ok"] = True
                ctx.state["done"] = True
                return "A-ok"

            async def runner_b(ctx):
                ctx.state["probe"] = "only-b"
                b_started.set()
                await asyncio.sleep(0.05)
                return "B-ok"

            sid_a = mgr.start_target("10.0.0.1", runner_a)
            sid_b = mgr.start_target("10.0.0.2", runner_b)
            result_a = await mgr.join(sid_a)
            result_b = await mgr.join(sid_b)

            self.assertEqual(result_a, "A-ok")
            self.assertEqual(result_b, "B-ok")
            self.assertEqual(a_passed["ok"], True)  # 并发栅栏通过（B 在 A 等待期间开跑）
            self.assertEqual(mgr.status(sid_a)["status"], "done")
            self.assertEqual(mgr.status(sid_b)["status"], "done")
            # 上下文独立：每 session 只见自己的 state，bus 也是两把
            ctx_a, ctx_b = mgr.get_context(sid_a), mgr.get_context(sid_b)
            self.assertEqual(ctx_a.state, {"probe": "only-a", "done": True})
            self.assertEqual(ctx_b.state, {"probe": "only-b"})
            self.assertIsNot(ctx_a.state, ctx_b.state)
            self.assertIsNot(ctx_a.bus, ctx_b.bus)

        self._run(scenario())

    def test_events_isolated_per_session_bus(self):
        """事件按 session 隔离：独立总线独立 seq，A/B 事件互不可见。"""

        async def scenario():
            mgr = SessionManager()

            def make_runner(tool: str):
                async def _runner(ctx):
                    ctx.emit("agent/tool", tool=tool)
                    await asyncio.sleep(0.01)
                    ctx.emit("agent/tool", tool=tool)
                    return tool
                return _runner

            sid_a = mgr.start_target("tgt-a", make_runner("nmap"))
            sid_b = mgr.start_target("tgt-b", make_runner("curl"))
            await mgr.join(sid_a)
            await mgr.join(sid_b)

            hist_a, hist_b = mgr.history(sid_a), mgr.history(sid_b)
            self.assertEqual([e["seq"] for e in hist_a], [1, 2])
            self.assertEqual([e["seq"] for e in hist_b], [1, 2])  # 独立 seq，不从别处续号
            self.assertTrue(all(e["task_id"] == sid_a for e in hist_a))
            self.assertTrue(all(e["task_id"] == sid_b for e in hist_b))
            self.assertEqual([e["data"]["tool"] for e in hist_a], ["nmap", "nmap"])
            self.assertEqual([e["data"]["tool"] for e in hist_b], ["curl", "curl"])

        self._run(scenario())

    def test_stop_is_idempotent(self):
        """stop 幂等：首次取消 running → True，再次/三次 no-op → False，未知 session 抛 KeyError。"""

        async def scenario():
            mgr = SessionManager()

            async def forever(ctx):
                await asyncio.Event().wait()  # 永不结束，直到被取消
                return "never"

            sid = mgr.start_target("10.0.0.9", forever)
            await asyncio.sleep(0.05)
            self.assertEqual(mgr.status(sid)["status"], "running")

            self.assertTrue(await mgr.stop(sid, reason="manual"))
            self.assertEqual(mgr.status(sid)["status"], "stopped")
            self.assertFalse(await mgr.stop(sid))  # 第二次幂等 no-op
            self.assertFalse(await mgr.stop(sid))  # 第三次同样 no-op
            with self.assertRaises(KeyError):
                await mgr.stop("ghost-session")

        self._run(scenario())

    def test_start_registers_unique_sessions_and_list_status(self):
        """注册/查询：session_id 唯一、list/status/running_count 正确、重复 id 拒绝。"""

        async def scenario():
            mgr = SessionManager()

            async def quick(ctx):
                await asyncio.sleep(0.01)
                return ctx.target_id

            sid_auto = mgr.start_target("t1", quick)
            sid_fixed = mgr.start_target("t2", quick, session_id="fixed-2")
            self.assertNotEqual(sid_auto, sid_fixed)
            # 未让出事件循环前两任务均 running
            self.assertEqual(mgr.running_count, 2)
            self.assertEqual(mgr.status(sid_fixed)["target_id"], "t2")

            self.assertEqual(await mgr.join(sid_auto), "t1")
            self.assertEqual(await mgr.join(sid_fixed), "t2")
            self.assertEqual(mgr.status(sid_auto)["status"], "done")
            self.assertEqual(mgr.running_count, 0)

            listed = {s["session_id"]: s for s in mgr.list_sessions()}
            self.assertEqual(set(listed), {sid_auto, sid_fixed})
            self.assertEqual(listed[sid_auto]["status"], "done")

            with self.assertRaises(ValueError):  # 显式重复 id 拒绝
                mgr.start_target("dup", quick, session_id="fixed-2")
            with self.assertRaises(KeyError):    # 未知 session 查询拒绝
                mgr.status("ghost")

        self._run(scenario())

    def test_broadcast_fans_out_to_all_sessions(self):
        """broadcast：一条系统事件送达全部 session 总线（host 通道数据源）。"""

        async def scenario():
            mgr = SessionManager()

            async def quick(ctx):
                await asyncio.sleep(0.01)

            sid_a = mgr.start_target("a", quick)
            sid_b = mgr.start_target("b", quick)
            self.assertEqual(mgr.broadcast("system/ping", reason="engagement changed"), 2)
            for sid in (sid_a, sid_b):
                hist = mgr.history(sid)
                self.assertEqual(hist[-1]["kind"], "system/ping")
                self.assertEqual(hist[-1]["data"]["reason"], "engagement changed")
            await mgr.join(sid_a)
            await mgr.join(sid_b)
            # session 结束后总线保留，广播仍可达
            self.assertEqual(mgr.broadcast("system/bye"), 2)

        self._run(scenario())

    def test_runner_exception_becomes_error_status(self):
        """runner 异常结构化收口：status=error + error 文案，不影响其他 session。"""

        async def scenario():
            mgr = SessionManager()

            async def boom(ctx):
                await asyncio.sleep(0)
                raise RuntimeError("engine blew up")

            async def ok(ctx):
                await asyncio.sleep(0)
                return {"flag": "CTF{ok}"}

            sid_err = mgr.start_target("x", boom)
            sid_ok = mgr.start_target("y", ok)
            self.assertIsNone(await mgr.join(sid_err))
            self.assertEqual(mgr.status(sid_err)["status"], "error")
            self.assertIn("engine blew up", mgr.status(sid_err)["error"])
            # 相邻 session 不受异常影响
            self.assertEqual(await mgr.join(sid_ok), {"flag": "CTF{ok}"})
            self.assertEqual(mgr.status(sid_ok)["status"], "done")

        self._run(scenario())


if __name__ == "__main__":
    unittest.main()
