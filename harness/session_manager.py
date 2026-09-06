"""SessionManager —— 多目标并行执行管理（R4 前半，L2 编排层落地）。

规格来源：SECAI-PT 终极执行计划 v4 第五部分 R4（harness/session_manager.py 多目标并行）
与设计裁决 #4「每个目标 = 一个独立 Session/执行循环，SessionManager 统一管理，
前端 mux WebSocket 同时监视」。

职责与语义：
- start_target()：为单个目标注册 session（默认自生成 session_id），为它创建**独立
  EventBus** 与**独立 SessionContext.state**（目标自己的黑板/上下文），随即 spawn
  asyncio.Task 并行驱动 runner —— 支持 ≥2 目标同时跑，各目标上下文不互扰；
- 事件总线隔离：默认每 session 一把全新 EventBus（seq/历史互不相干）；
  需要全量监视时可注入共享 bus_factory（事件仍按 session_id 键隔离）；
- list_sessions() / status()：编排面查询；stop()：幂等取消；broadcast()：向全部
  session 广播事件（MuxFrame host 通道数据源）；close()：收尾统一取消 running。

线程/循环约束：start_target 必须在运行中的事件循环里调用（内部 create_task）。
runner 签名：Callable[[SessionContext], Awaitable[Any]] —— 真实接入时在 runner 内
组装单目标执行循环（单测注入 fake runner，互不依赖）。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from core.events import EventBus

SessionStatus = Literal["running", "done", "stopped", "error"]

# 默认 bus 工厂：每个 session 独立事件总线（真隔离）；共享总线由调用方注入
def _fresh_bus() -> EventBus:
    return EventBus()


@dataclass
class SessionContext:
    """单个目标 session 的运行上下文：总线 + 独立 state（互扰隔离的最小边界）。"""

    session_id: str
    target_id: str
    bus: EventBus
    state: dict[str, Any] = field(default_factory=dict)  # 目标独立黑板/上下文（每 session 一份）
    meta: dict[str, Any] = field(default_factory=dict)   # 附加元信息（难度/授权书引用等）

    def emit(self, kind: str, **data: Any) -> dict:
        """向本 session 的隔离总线发一条事件（kind 对齐 MuxFrame session/event）。"""
        return self.bus.emit(self.session_id, kind, **data)


@dataclass
class SessionEntry:
    """一条已注册 session 的编排记录（含任务句柄与终态）。"""

    session_id: str
    target_id: str
    bus: EventBus
    context: SessionContext
    status: SessionStatus = "running"
    task: asyncio.Task | None = None
    result: Any = None
    error: str = ""
    stop_reason: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0

    def summary(self) -> dict[str, Any]:
        """编排面摘要（不含 result 大对象，防看板/列表被刷屏）。"""
        return {
            "session_id": self.session_id,
            "target_id": self.target_id,
            "status": self.status,
            "error": self.error,
            "stop_reason": self.stop_reason,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class SessionManager:
    """统一管理多个目标 session：注册 / 并行驱动 / 事件隔离 / 广播 / 查询 / 幂等停止。

    真实接入：runner 内组装单目标执行循环驱动单目标（如 pentest_target.run_pentest_target）；
    单测接入：fake runner（本类对 runner 零假设，只要求签名与返回 awaitable）。
    """

    def __init__(
        self,
        *,
        bus_factory: Callable[[], EventBus] | None = None,
        shared_bus: EventBus | None = None,
    ) -> None:
        # shared_bus 兼容旧总线场景：提供后 bus_factory 忽略，所有 session 共用
        # （隔离仍成立：事件按 session_id 键名分区，seq 各自计数）。
        if shared_bus is not None:
            self._bus_factory: Callable[[], EventBus] = lambda: shared_bus
        else:
            self._bus_factory = bus_factory or _fresh_bus
        self._sessions: dict[str, SessionEntry] = {}

    # ------------------------------------------------------------------
    # 注册与并行启动
    # ------------------------------------------------------------------
    def start_target(
        self,
        target: str,
        runner: Callable[[SessionContext], Awaitable[Any]],
        *,
        session_id: str | None = None,
        state: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """注册目标并立即 spawn 并行任务。返回 session_id。

        - target：目标标识（IP/域名/服务地址）；同一目标可开多个 session（不同轮次）。
        - runner：异步入口，接收 SessionContext，返回任意结果（终态存 entry.result）。
        - session_id：显式指定须唯一，重复抛 ValueError；缺省自生成。
        - state/meta：注入到该 session 独立上下文的初始内容。
        """
        if runner is None:
            raise TypeError("runner 不能为空")
        sid = session_id or uuid.uuid4().hex[:12]
        if sid in self._sessions:
            raise ValueError(f"session {sid} 已注册（每目标独立 session，勿重复 id）")
        bus = self._bus_factory()
        context = SessionContext(
            session_id=sid,
            target_id=target,
            bus=bus,
            state=dict(state or {}),
            meta=dict(meta or {}),
        )
        entry = SessionEntry(session_id=sid, target_id=target,
                             bus=bus, context=context, started_at=time.time())
        self._sessions[sid] = entry
        entry.task = asyncio.create_task(self._drive(entry, runner))
        return sid

    async def _drive(self, entry: SessionEntry,
                     runner: Callable[[SessionContext], Awaitable[Any]]) -> None:
        """包裹 runner：统一收口终态（done/stopped/error），异常不逃逸到调用方。"""
        try:
            entry.result = await runner(entry.context)
            entry.status = "done"
        except asyncio.CancelledError:
            entry.status = "stopped"  # 被 stop() 取消：不传播，避免 asyncio 告警
        except Exception as exc:  # runner 内部异常 → 结构化 error 终态
            entry.status = "error"
            entry.error = f"{type(exc).__name__}: {exc}"
            entry.result = None
        finally:
            entry.finished_at = time.time()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def _get(self, session_id: str) -> SessionEntry:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise KeyError(f"未知 session: {session_id}") from None

    @property
    def session_ids(self) -> list[str]:
        return list(self._sessions)

    @property
    def running_count(self) -> int:
        return sum(1 for e in self._sessions.values() if e.status == "running")

    def list_sessions(self) -> list[dict[str, Any]]:
        """全部 session 摘要（注册顺序）。"""
        return [e.summary() for e in self._sessions.values()]

    def status(self, session_id: str) -> dict[str, Any]:
        return self._get(session_id).summary()

    def get_bus(self, session_id: str) -> EventBus:
        return self._get(session_id).bus

    def get_context(self, session_id: str) -> SessionContext:
        return self._get(session_id).context

    def history(self, session_id: str) -> list[dict]:
        """该 session 隔离总线上的事件历史（副本）。"""
        return self._get(session_id).bus.history(session_id)

    # ------------------------------------------------------------------
    # 事件广播（host 通道：向全部已注册 session 下发系统事件）
    # ------------------------------------------------------------------
    def broadcast(self, kind: str, **data: Any) -> int:
        """向所有 session 的隔离总线广播一条事件。返回送达 session 数。"""
        count = 0
        for entry in self._sessions.values():
            try:
                entry.bus.emit(entry.session_id, kind, **data)
                count += 1
            except Exception:
                continue
        return count

    # ------------------------------------------------------------------
    # 停止 / 等待 / 收尾
    # ------------------------------------------------------------------
    async def stop(self, session_id: str, *, reason: str = "stopped") -> bool:
        """请求停止一个 session。幂等：已停止/已结束返回 False；未知 session 抛 KeyError。"""
        entry = self._get(session_id)
        task = entry.task
        if task is None or task.done():
            return False
        entry.status = "stopped"
        entry.stop_reason = reason
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return True

    async def join(self, session_id: str, *, timeout: float | None = None) -> Any:
        """等待某 session 结束并返回其 result（shield 保护，取消 join 不杀 session）。"""
        entry = self._get(session_id)
        task = entry.task
        if task is not None and not task.done():
            if timeout is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            else:
                await asyncio.shield(task)
        return entry.result

    async def close(self) -> None:
        """收尾：统一停止全部 running session（幂等，进程退出/测试 tearDown 用）。"""
        for session_id in list(self._sessions):
            try:
                await self.stop(session_id)
            except Exception:
                continue


__all__ = [
    "SessionContext",
    "SessionEntry",
    "SessionManager",
    "SessionStatus",
]
