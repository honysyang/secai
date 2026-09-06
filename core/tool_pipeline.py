"""统一工具调用管线（Tool Pipeline）。

把分散在工具层与 hooks 的横切关注点（爆破预算、prompt injection 防护、
payload 台账、增量打分、网络不可达检测）收敛成可插拔的 middleware，
让新增工具、新增安全策略、新增观测点都只需要加一行配置。

管线顺序：
    pre-execute → guard → around-execute(tool body) → post-execute → result

pre  可修改参数或注入上下文；
guard 可拒绝执行并返回拦截消息；
around 包装真实工具执行（用于超时、取消、资源隔离）；
post 观测/修改结果并触发副作用（如渐进披露）。
"""
from __future__ import annotations

import asyncio
import functools
import ipaddress
import json
import os
import re
import uuid
from abc import ABC
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Union
from urllib.parse import urlparse

from agents import RunContextWrapper

from core.task_context import TaskContext
from runtime.log import log_info, log_warn
from sandbox import SandboxBlockedError, SandboxUnavailableError
from sandbox import confine as _l5_confine
from sandbox.policy import Policy as SandboxPolicy
from tools.domains._base import _guard_output as _base_guard_output

# ================= 爆破预算 =================
BRUTEFORCE_MAX_CALLS = int(os.getenv("BRUTEFORCE_MAX_CALLS", "20"))  # 每会话爆破调用硬上限，0=关闭
_BRUTE_TOOLS = {"fuzz", "parallel_shell", "run_tool"}                # 一定是爆破/枚举的工具
_BRUTE_BINARIES = ("hydra", "ffuf", "dirsearch", "sqlmap", "john",   # shell 里的爆破二进制
                   "masscan", "nuclei", "gobuster", "wfuzz", "dirb")


def _is_brute_call(name: str, args: str = "") -> bool:
    """判断一次工具调用是否属于爆破/枚举类（成本治理用）。"""
    if name in _BRUTE_TOOLS:
        return True
    if name == "shell":
        low = (args or "").lower()
        return any(b in low for b in _BRUTE_BINARIES)
    return False


def brute_gate(ctx: RunContextWrapper, name: str, args: str = "") -> str:
    """爆破预算闸门：超限返回拦截消息（工具应直接返回它），未超限返回空串。

    返回空串 = 放行。拦截时计数已 +1，且不会实际执行该次爆破。
    """
    if BRUTEFORCE_MAX_CALLS <= 0:
        return ""
    if not _is_brute_call(name, args):
        return ""
    c = ctx.context
    c.bruteforce_calls += 1
    if c.bruteforce_calls <= BRUTEFORCE_MAX_CALLS:
        return ""
    return (f"[error] 爆破预算硬上限：本会话爆破/枚举类调用已达 {c.bruteforce_calls} 次"
            f"（上限 {BRUTEFORCE_MAX_CALLS}），本次调用未执行。禁止继续大规模爆破/枚举/"
            "字典攻击（含 shell 里的 hydra/ffuf/dirsearch/sqlmap 等）——转向已确认线索的"
            "定向验证，或换攻击面。")

# ---------------------------------------------------------------------------
# Middleware 协议
# ---------------------------------------------------------------------------

class ToolMiddleware(ABC):
    """Middleware 基类。子类重写 pre/guard/around/post 中需要的方法。"""

    name: str = ""

    async def pre(self, ctx: RunContextWrapper[TaskContext], tool: str,
                  args: dict[str, Any]) -> dict[str, Any]:
        """参数预处理；返回修改后的 args。"""
        return args

    def guard(self, ctx: RunContextWrapper[TaskContext], tool: str,
              args: dict[str, Any]) -> str | None:
        """执行前拦截；返回非空字符串时直接作为工具结果返回，不执行工具体。"""
        return None

    async def around(self, ctx: RunContextWrapper[TaskContext], tool: str,
                     args: dict[str, Any], execute: Callable[[], Awaitable[str]]) -> str:
        """包装工具执行；默认直接执行。"""
        return await execute()

    async def post(self, ctx: RunContextWrapper[TaskContext], tool: str,
                   args: dict[str, Any], result: str) -> str:
        """结果后处理；返回修改后的 result。"""
        return result


# ---------------------------------------------------------------------------
# 内置 Middleware
# ---------------------------------------------------------------------------

class BruteGateMiddleware(ToolMiddleware):
    """爆破预算闸门：拦截超预算的爆破/枚举类调用。"""
    name = "brute_gate"

    def guard(self, ctx, tool, args):
        arg_text = json.dumps(args, ensure_ascii=False) if args else ""
        block = brute_gate(ctx, tool, arg_text)
        return block or None


class ArtifactSpillMiddleware(ToolMiddleware):
    """超长输出落盘 middleware：截断前保留全文扫描注入特征的机会。

    工具输出超过阈值时写入 artifacts/，只返回预览 + 引用，避免撑爆会话上下文。
    """
    name = "artifact_spill"

    def __init__(self, threshold: int = 4000, preview: int = 4000):
        self.threshold = threshold
        self.preview = preview

    async def post(self, ctx, tool, args, result):
        text = str(result)
        notes = []
        note = _base_guard_output(text)
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

    def __init__(self, middlewares: list[ToolMiddleware] | None = None):
        self.middlewares: list[ToolMiddleware] = list(middlewares or [])

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
                      args: dict[str, Any], body: Callable[[], Awaitable[str]]) -> str:
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
# R3 L5 防护中间件（L5 护栏层落地：pentest/scope + sandbox + pentest/approval）
# ---------------------------------------------------------------------------
# 执行链：ScopeCheck(纯函数) → sandbox.confine → (T3) ApprovalGate → 执行。
# 仅在 ctx.context.l5_guardrail（core.task_context.L5GuardrailConfig）配置时生效；
# 旧模式（无 l5_guardrail）三个中间件完全透传，保证存量行为零扰动。
# 拦截消息一律结构化 JSON，Agent 可直接解析。

# 会携带网络目标参数、需做越范围扫描的工具（其余工具无法判定目标 → 不误伤）
_SCOPE_TARGET_TOOLS = {"shell", "run_batch", "parallel_shell", "http_request"}
# 携带「命令文本」的工具（危险词表扫描 + 沙箱化对象）
_CMD_TEXT_TOOLS = {"shell", "run_batch", "parallel_shell"}
_CMD_FIELD = {"shell": "command", "run_batch": "script", "parallel_shell": "commands"}
# 沙箱 around 替换执行（真实 bwrap 包 shell）的工具
_SANDBOX_EXEC_TOOLS = {"shell"}

_IP_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"]+")
_HOSTPORT_RE = re.compile(r"(?<![\w.])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}):[0-9]{1,5}\b",
                          re.IGNORECASE)


def _l5_cfg(ctx) -> Any | None:
    """取运行上下文上的 L5 护栏配置（None=护栏关闭，旧模式透传）。"""
    inner = getattr(ctx, "context", None)
    return getattr(inner, "l5_guardrail", None) if inner is not None else None


def _scope_action(ctx, tool: str) -> str:
    phase = getattr(getattr(ctx, "context", None), "phase", "")
    return f"{phase}/{tool}" if phase else tool


def _extract_targets(tool: str, args: dict[str, Any]) -> list[str]:
    """从工具参数中提取网络目标候选（合法 IPv4/IPv6/URL host/裸 host:port）。

    只处理会直接触达网络的工具；提取不出候选返回空列表（调用方决定是否兜底绑定目标）。
    """
    if tool not in _SCOPE_TARGET_TOOLS:
        return []
    if tool == "http_request":
        text = str(args.get("url", ""))
    else:
        text = " ".join(str(args.get(f, "")) for f in _CMD_FIELD.values() if args.get(f))
    if not text:
        return []
    found: list[str] = []
    for m in _IP_RE.finditer(text):
        cand = m.group(0)
        try:
            ipaddress.ip_address(cand)
        except ValueError:
            continue
        if cand not in found:
            found.append(cand)
    for tok in _URL_RE.findall(text):
        try:
            host = urlparse(tok).hostname
        except ValueError:
            host = None
        if host and host not in found:
            found.append(host)
    for m in _HOSTPORT_RE.finditer(text):
        host = m.group(1)
        if host not in found:
            found.append(host)
    return found


def _cmd_text(tool: str, args: dict[str, Any]) -> str:
    return str(args.get(_CMD_FIELD[tool], "") or "") if tool in _CMD_FIELD else ""


def _block_message(error: str, tool: str, detail: str, **extra: Any) -> str:
    payload = {"ok": False, "error": error, "tool": tool, "detail": detail}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


class ScopeGuardMiddleware(ToolMiddleware):
    """越范围硬拦截：工具参数中出现不在授权范围的网络目标 → 结构化拦截，不执行。"""

    name = "l5_scope"

    def guard(self, ctx, tool, args):
        cfg = _l5_cfg(ctx)
        if cfg is None or getattr(cfg, "scope", None) is None:
            return None
        scope = cfg.scope
        targets = _extract_targets(tool, args or {})
        if not targets:
            bound = getattr(cfg, "target", "") or ""
            targets = [bound] if bound else []
        if not targets:
            return None  # 无目标可判定（本地命令等），交由沙箱/审批闸门
        for t in targets:
            r = scope.check(t, _scope_action(ctx, tool))
            if not r.allowed:
                log_warn(f"[l5_scope] 拦截 {tool} 越范围目标 {t}（{r.matched_rule}）：{r.reason}")
                return _block_message("scope_block", tool, r.reason,
                                      target=t, matched_rule=r.matched_rule)
        return None


class L5SandboxMiddleware(ToolMiddleware):
    """命令分级 + bwrap 沙箱（fail-closed）。

    guard：危险命令（rm -rf / 等）/ 超出 Policy 档位 / 无后端 → 结构化拦截（不执行）；
    around：shell 命令在有后端时真实 bwrap 执行；后端故障返回错误且绝不裸跑回退。
    """

    name = "l5_sandbox"

    def guard(self, ctx, tool, args):
        cfg = _l5_cfg(ctx)
        if cfg is None:
            return None
        if tool not in _CMD_TEXT_TOOLS:
            return None
        text = _cmd_text(tool, args or {})
        if not text.strip():
            return None
        policy = getattr(cfg, "policy", None) or SandboxPolicy()
        backend = getattr(cfg, "backend", None)
        # 护栏已开启但未显式配置沙箱后端 → fail-closed（执行型工具禁止裸跑）。
        # 不使用模块默认后端：around 只对显式 cfg.backend 做 bwrap 执行替换，
        # 若 guard 用默认后端放行而 around 不替换会造成「看似沙箱实则裸跑」的假象。
        if backend is None:
            log_warn(f"[l5_sandbox] {tool} 护栏模式未配置沙箱后端，fail-closed 拒绝裸跑")
            return _block_message(
                "sandbox_unavailable", tool,
                "护栏模式已开启但未配置沙箱后端（fail-closed）：执行型工具禁止裸跑")
        try:
            _l5_confine(text, policy, backend=backend, workdir=_workdir_str(ctx))
        except SandboxBlockedError as e:
            log_warn(f"[l5_sandbox] 拦截 {tool}：{e}")
            return _block_message("danger_block", tool, str(e))
        except SandboxUnavailableError as e:
            log_warn(f"[l5_sandbox] fail-closed 拦截 {tool}：{e}")
            return _block_message("sandbox_unavailable", tool, str(e))
        # 词表/后端都通过，但该工具尚未接入 bwrap 执行替换（仅 shell 已沙箱化）：
        # 护栏模式下拒绝其裸跑（fail-closed），防止脚本内容绕过词表后在宿主裸执行。
        if tool not in _SANDBOX_EXEC_TOOLS:
            log_warn(f"[l5_sandbox] {tool} 未接入沙箱化执行，护栏模式拒绝裸跑")
            return _block_message(
                "sandbox_unavailable", tool,
                "护栏模式下 run_batch/parallel_shell 尚未接入 bwrap 沙箱化执行，"
                "fail-closed 拒绝裸跑（脚本内容无法被命令词表覆盖）；请改用 shell 逐条执行，"
                "或由工具适配器（R4）在沙箱内运行")
        return None

    async def around(self, ctx, tool, args, execute):
        cfg = _l5_cfg(ctx)
        if (cfg is None or tool not in _SANDBOX_EXEC_TOOLS
                or getattr(cfg, "backend", None) is None):
            return await execute()
        text = (args or {}).get("command", "")
        if not text.strip():
            return await execute()
        policy = getattr(cfg, "policy", None) or SandboxPolicy()
        backend = cfg.backend
        try:
            res = _l5_confine(["bash", "-c", text], policy, backend=backend,
                              workdir=_workdir_str(ctx))
        except (SandboxBlockedError, SandboxUnavailableError) as e:
            return f"[error] L5 沙箱拒绝执行（未执行任何命令）：{e}"

        try:
            timeout = min(int((args or {}).get("timeout", 30) or 30), 120)
            er = await asyncio.to_thread(backend.run, res.argv, timeout=timeout,
                                         cwd=_workdir_str(ctx))
        except SandboxUnavailableError as e:
            return f"[error] L5 沙箱执行失败，fail-closed 未裸跑：{e}"
        return _format_sandbox_output(er)


def _workdir_str(ctx) -> str | None:
    inner = getattr(ctx, "context", None)
    wd = getattr(inner, "workdir", None) if inner is not None else None
    return str(wd) if wd is not None else None


def _format_sandbox_output(er, preview: int = 4000) -> str:
    """与 shell 工具本体一致的输出格式（沙箱化执行后拼装）。"""
    out = f"rc={er.rc}\nstdout:\n{er.stdout[:preview]}\nstderr:\n{er.stderr[:1000]}"
    if er.timed_out:
        out += "\n[error] L5 沙箱命令超时，已终止"
    return out


class ApprovalGateMiddleware(ToolMiddleware):
    """T3 人工审批门：tier 需审批的工具无人工放行 → 结构化暂停/拒绝，不执行。"""

    name = "l5_approval"

    def guard(self, ctx, tool, args):
        cfg = _l5_cfg(ctx)
        if cfg is None or getattr(cfg, "approval", None) is None:
            return None
        gate = cfg.approval
        session_id = (getattr(cfg, "task_id", "") or ""
                      or getattr(getattr(ctx, "context", None), "task", "")[:40] or "l5")
        result = gate.request(session_id, tool, dict(args or {}))
        if result.ok:
            return None
        log_warn(f"[l5_approval] {tool} 未获审批（{result.status}），暂停执行")
        return result.as_message()


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
])
# R3：L5 护栏中间件挂入默认管线（无 l5_guardrail 配置时透传，存量零扰动）
DEFAULT_PIPELINE.add(ScopeGuardMiddleware())
DEFAULT_PIPELINE.add(L5SandboxMiddleware())
DEFAULT_PIPELINE.add(ApprovalGateMiddleware())


# ---------------------------------------------------------------------------
# 以下 helper 统一收敛到 core.hooks 的增强实现，避免双份漂移
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


def _ledger_signature(tool: str, args: dict[str, Any]) -> str:
    """归一化工具调用签名（统一收敛到 core.hooks 的增强实现，避免双份漂移）。"""
    from core.hooks import _ledger_signature as _sig
    return _sig(tool, args)


def _record_payload_ledger(task_ctx: TaskContext, tool: str, args: dict[str, Any], text: str) -> None:
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
