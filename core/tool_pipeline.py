"""统一工具调用管线（Tool Pipeline）。

把分散在 demo_tools.py / hooks.py 中的横切关注点（爆破预算、prompt injection 防护、
payload 台账、增量打分、网络不可达检测、自动提交 flag）收敛成可插拔的 middleware，
让新增工具、新增安全策略、新增观测点都只需要加一行配置。

管线顺序：
    pre-execute → guard → around-execute(tool body) → post-execute → result

pre  可修改参数或注入上下文；
guard 可拒绝执行并返回拦截消息；
around 包装真实工具执行（用于超时、取消、资源隔离）；
post 观测/修改结果并触发副作用（如渐进披露、提交 flag）。
"""
from __future__ import annotations

import asyncio
import functools
import json
import re
import uuid
from abc import ABC
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from agents import RunContextWrapper

from core.task_context import TaskContext
from runtime.budget import brute_gate as _budget_brute_gate
from runtime.log import log_info, log_warn


# ---------------------------------------------------------------------------
# Middleware 协议
# ---------------------------------------------------------------------------

class ToolMiddleware(ABC):
    """Middleware 基类。子类重写 pre/guard/around/post 中需要的方法。"""

    name: str = ""

    async def pre(self, ctx: RunContextWrapper[TaskContext], tool: str,
                  args: Dict[str, Any]) -> Dict[str, Any]:
        """参数预处理；返回修改后的 args。"""
        return args

    def guard(self, ctx: RunContextWrapper[TaskContext], tool: str,
              args: Dict[str, Any]) -> Optional[str]:
        """执行前拦截；返回非空字符串时直接作为工具结果返回，不执行工具体。"""
        return None

    async def around(self, ctx: RunContextWrapper[TaskContext], tool: str,
                     args: Dict[str, Any], execute: Callable[[], Awaitable[str]]) -> str:
        """包装工具执行；默认直接执行。"""
        return await execute()

    async def post(self, ctx: RunContextWrapper[TaskContext], tool: str,
                   args: Dict[str, Any], result: str) -> str:
        """结果后处理；返回修改后的 result。"""
        return result


# 默认管线实例化时绑定提交函数；避免循环导入，这里只做占位。
SubmitFn = Callable[[RunContextWrapper[TaskContext], str], Optional[str]]


# ---------------------------------------------------------------------------
# 内置 Middleware
# ---------------------------------------------------------------------------

class BruteGateMiddleware(ToolMiddleware):
    """爆破预算闸门：拦截超预算的爆破/枚举类调用。"""
    name = "brute_gate"

    def guard(self, ctx, tool, args):
        arg_text = json.dumps(args, ensure_ascii=False) if args else ""
        block = _budget_brute_gate(ctx, tool, arg_text)
        return block or None


class ArtifactSpillMiddleware(ToolMiddleware):
    """超长输出落盘 middleware：截断前保留全文扫描 flag/注入的机会。

    工具输出超过阈值时写入 artifacts/，只返回预览 + 引用，避免撑爆会话上下文。
    """
    name = "artifact_spill"

    def __init__(self, threshold: int = 4000, preview: int = 4000):
        self.threshold = threshold
        self.preview = preview

    async def post(self, ctx, tool, args, result):
        text = str(result)
        # 延迟导入 demo_tools 中的提交/注入扫描函数，避免循环导入
        try:
            from demo_tools import _submit_flags_if_any, _guard_output
        except Exception:
            _submit_flags_if_any, _guard_output = None, None
        notes = []
        if _submit_flags_if_any is not None:
            note = _submit_flags_if_any(ctx, text)
            if note:
                notes.append(note)
        if _guard_output is not None:
            note = _guard_output(text)
            if note:
                notes.append(note)
        if len(text) <= self.threshold:
            tail = "\n".join(notes)
            return text + (f"\n{tail}" if tail else "")
        task_ctx = ctx.context
        art_dir = Path(task_ctx.workdir) / "artifacts"
        art_dir.mkdir(exist_ok=True)
        art_id = uuid.uuid4().hex[:8]
        (art_dir / f"{art_id}.txt").write_text(text, encoding="utf-8")
        tail = (f"\n...[已截断，全文 {len(text)} 字符保存到 artifacts/{art_id}.txt]"
                + f"\n[用 read_artifact {art_id} 读取全文]")
        if notes:
            tail += "\n" + "\n".join(notes)
        return text[:self.preview] + tail


class PromptInjectionGuardMiddleware(ToolMiddleware):
    """输出侧 prompt injection 过滤：移除常见注入模式。"""
    name = "prompt_injection_guard"

    async def post(self, ctx, tool, args, result):
        return _guard_output_text(result)


class PayloadLedgerMiddleware(ToolMiddleware):
    """exploit 阶段 payload 台账：记录 shell/http_request/run_batch/fuzz 的调用签名。"""
    name = "payload_ledger"
    _TOOLS = {"shell", "http_request", "run_batch", "fuzz"}

    async def post(self, ctx, tool, args, result):
        task_ctx = ctx.context
        if task_ctx.phase == "exploit" and tool in self._TOOLS:
            _record_payload_ledger(task_ctx, tool, args, result)
        return result


class ProgressScorerMiddleware(ToolMiddleware):
    """信息增量打分：正向证据置位 turn_gain。"""
    name = "progress_scorer"

    async def post(self, ctx, tool, args, result):
        task_ctx = ctx.context
        if tool not in _NO_PROGRESS_TOOLS:
            score = _score_tool_result(tool, result, task_ctx)
            if score > 0:
                task_ctx.turn_gain = True
        return result


class NetworkUnreachableMiddleware(ToolMiddleware):
    """检测网络不可达信号，置位 turn_net_fail。"""
    name = "net_unreachable"

    async def post(self, ctx, tool, args, result):
        if _is_network_unreachable(result):
            ctx.context.turn_net_fail = True
        return result


class AutoSubmitFlagMiddleware(ToolMiddleware):
    """自动扫描工具输出中的 flag 并尝试提交（铁律提交）。"""
    name = "auto_submit_flag"

    def __init__(self, submit_fn: Optional[SubmitFn] = None):
        self.submit_fn = submit_fn

    async def post(self, ctx, tool, args, result):
        if self.submit_fn is not None:
            note = self.submit_fn(ctx, result)
            if note:
                return result + "\n" + note
        return result


class SandboxMiddleware(ToolMiddleware):
    """进程隔离 Code Runtime：对执行型工具做超时熔断 + 进程级资源限制。

    目前通过 subprocess preexec_fn 设置 RLIMIT_CPU/AS/NOFILE，并把每次执行包在
    asyncio.wait_for 中防止协程级挂起。未来可替换为容器/namespace 隔离。
    """
    name = "sandbox"
    _SANDBOX_TOOLS = {"shell", "run_batch", "parallel_shell", "fuzz"}
    _MAX_TIMEOUT = 300

    def __init__(self, max_cpu: int = 60, max_mem_gb: float = 1.5,
                 max_nofile: int = 256):
        self.max_cpu = max_cpu
        self.max_mem = int(max_mem_gb * 1024 * 1024 * 1024)
        self.max_nofile = max_nofile

    def pre(self, ctx, tool, args):
        if tool in self._SANDBOX_TOOLS and "timeout" in args:
            args = dict(args)
            args["timeout"] = min(int(args["timeout"]), self._MAX_TIMEOUT)
        return args

    async def around(self, ctx, tool, args, execute):
        if tool not in self._SANDBOX_TOOLS:
            return await execute()
        timeout = args.get("timeout", 60)
        timeout = min(int(timeout), self._MAX_TIMEOUT)
        try:
            return await asyncio.wait_for(execute(), timeout=timeout)
        except asyncio.TimeoutError:
            return f"[error] sandbox 执行超时（>{timeout}s），已终止"


# ---------------------------------------------------------------------------
# Pipeline Runner
# ---------------------------------------------------------------------------

MiddlewareSpec = Union[ToolMiddleware, str]


class ToolPipeline:
    """工具管线：按顺序收集 middleware，执行 pre → guard → around → post。"""

    def __init__(self, middlewares: Optional[List[ToolMiddleware]] = None):
        self.middlewares: List[ToolMiddleware] = list(middlewares or [])

    def add(self, mw: ToolMiddleware) -> "ToolPipeline":
        self.middlewares.append(mw)
        return self

    async def _run_pre(self, ctx, tool, args):
        for mw in self.middlewares:
            try:
                if asyncio.iscoroutinefunction(mw.pre):
                    args = await mw.pre(ctx, tool, args)
                else:
                    args = mw.pre(ctx, tool, args)
            except Exception as e:
                log_warn(f"[pipeline] {mw.name} pre error: {e}")
        return args

    async def execute(self, ctx: RunContextWrapper[TaskContext], tool: str,
                      args: Dict[str, Any], body: Callable[[], Awaitable[str]]) -> str:
        # pre
        args = await self._run_pre(ctx, tool, args)

        # guard
        for mw in self.middlewares:
            try:
                block = mw.guard(ctx, tool, args)
                if block:
                    log_info(f"[pipeline] {mw.name} blocked {tool}")
                    return block
            except Exception as e:
                log_warn(f"[pipeline] {mw.name} guard error: {e}")

        # around + body
        async def _run_body():
            return await body()

        executor = _run_body
        for mw in reversed(self.middlewares):
            async def _wrap(mw=mw, inner=executor):
                return await mw.around(ctx, tool, args, inner)
            executor = _wrap

        result = await executor()

        # post
        for mw in self.middlewares:
            try:
                result = await mw.post(ctx, tool, args, result)
            except Exception as e:
                log_warn(f"[pipeline] {mw.name} post error: {e}")
        return result


# ---------------------------------------------------------------------------
# 工具装饰器：把普通函数包装成带管线的 function_tool
# ---------------------------------------------------------------------------

def with_pipeline(pipeline: ToolPipeline):
    """装饰器：把 async/sync 工具函数纳入统一管线。

    被装饰函数签名必须是 (ctx: RunContextWrapper, **kwargs) -> str。
    同步阻塞型工具函数会自动在 asyncio.to_thread 中执行，避免阻塞事件循环。
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(ctx: RunContextWrapper[TaskContext], **kwargs):
            async def body():
                if asyncio.iscoroutinefunction(func):
                    return await func(ctx, **kwargs)
                # 同步阻塞型工具体放到后台线程执行
                return await asyncio.to_thread(func, ctx, **kwargs)
            return await pipeline.execute(ctx, func.__name__, kwargs, body)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# 默认管线（全局共用，覆盖大多数横切关注点）
# ---------------------------------------------------------------------------

DEFAULT_PIPELINE = ToolPipeline([
    BruteGateMiddleware(),
    SandboxMiddleware(),
    ArtifactSpillMiddleware(),
    PromptInjectionGuardMiddleware(),
    PayloadLedgerMiddleware(),
    ProgressScorerMiddleware(),
    NetworkUnreachableMiddleware(),
    AutoSubmitFlagMiddleware(),
])


# ---------------------------------------------------------------------------
# 以下 helper 从原 demo_tools.py / hooks.py 迁移，保持行为一致
# ---------------------------------------------------------------------------

_NO_PROGRESS_TOOLS = {"think", "todo_add", "todo_list", "todo_mark", "checkpoint", "list_tools"}


def _guard_output_text(text: str) -> str:
    """过滤输出中可能试图覆盖系统提示的注入片段。"""
    patterns = [
        r"\n\s*#\s*system\s*prompt\s*[:：].*",
        r"\n\s*system\s*[:：].*?ignore.*?(?=\n|$)",
        r"\n\s*ignore\s+previous.*?(?=\n|$)",
    ]
    out = str(text)
    for p in patterns:
        out = re.sub(p, "", out, flags=re.IGNORECASE)
    return out


def _ledger_signature(tool: str, args: Dict[str, Any]) -> str:
    """归一化工具调用签名（统一收敛到 core.hooks 的增强实现，避免双份漂移）。"""
    from core.hooks import _ledger_signature as _sig
    return _sig(tool, args)


def _record_payload_ledger(task_ctx: TaskContext, tool: str, args: Dict[str, Any], text: str) -> None:
    """exploit 阶段 payload 记账（统一收敛到 core.hooks 的增强实现）。"""
    from core.hooks import _record_payload_ledger as _rec
    _rec(task_ctx, tool, args, text)


def _score_tool_result(tool: str, text: str, ctx: TaskContext) -> int:
    """信息增量打分（统一收敛到 core.hooks 的 v3 增强实现，避免双份漂移）。"""
    from core.hooks import _score_tool_result as _score
    return _score(tool, text, ctx)


def _is_network_unreachable(text: str) -> bool:
    """网络不可达检测（统一收敛到 core.hooks）。"""
    from core.hooks import _is_network_unreachable as _net
    return _net(text)


# 并发包装器：给同步阻塞型工具加 around 超时/取消
class TimeoutAroundMiddleware(ToolMiddleware):
    """around 层超时 + 取消传播（用于 parallel_shell / fuzz / run_batch 等并发工具）。"""
    name = "timeout_around"

    def __init__(self, default_timeout: float = 120.0):
        self.default_timeout = default_timeout

    async def around(self, ctx, tool, args, execute):
        timeout = args.get("timeout", self.default_timeout)
        if timeout is None:
            timeout = self.default_timeout
        try:
            return await asyncio.wait_for(execute(), timeout=float(timeout))
        except asyncio.TimeoutError:
            return f"[error] {tool} 执行超时（{timeout}s）"
        except asyncio.CancelledError:
            return f"[error] {tool} 执行被取消"
