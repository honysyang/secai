"""L5 防护链集成测试 —— ScopeCheck → sandbox.confine → (T3) ApprovalGate → 执行。

在自定义 ToolPipeline 上按 DEFAULT_PIPELINE 的挂载顺序（l5_scope → l5_sandbox → l5_approval）
用最小 body 替身验证：越范围硬拦截 / 无后端 fail-closed 不裸跑 / danger 词表拦截 /
T3 无审批暂停（body 与后端都未被调用）/ 审批后放行并走沙箱执行 / 未接入工具拒绝裸跑 /
无 l5 配置完全透传（存量零扰动）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.events import EventBus
from core.task_context import L5GuardrailConfig, TaskContext
from core.tool_pipeline import (
    ApprovalGateMiddleware,
    L5SandboxMiddleware,
    ScopeGuardMiddleware,
    ToolPipeline,
)
from pentest.approval import ApprovalGate
from pentest.scope import ScopeConstraint
from sandbox import ExecResult
from sandbox.policy import Policy


class _FakeBackend:
    """可用假后端：记录 run 调用，返回固定输出（不真正进 bwrap）。"""

    name = "fake"

    def __init__(self):
        self.run_calls: list[list[str]] = []

    def available(self) -> bool:
        return True

    def build_argv(self, argv, *, writable, workdir=None):
        return ["fake-bwrap", *list(argv)]

    def run(self, argv, *, timeout=None, cwd=None):
        self.run_calls.append(list(argv))
        return ExecResult(rc=0, stdout="fake-ok")


def make_pipeline() -> ToolPipeline:
    return ToolPipeline([
        ScopeGuardMiddleware(),
        L5SandboxMiddleware(),
        ApprovalGateMiddleware(),
    ])


def make_ctx(tmp_path, **cfg: object) -> SimpleNamespace:
    l5 = L5GuardrailConfig(**cfg)
    return SimpleNamespace(context=TaskContext(workdir=tmp_path, l5_guardrail=l5))


def make_ctx_plain(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(context=TaskContext(workdir=tmp_path))  # 无 l5 配置


def run(pipeline: ToolPipeline, ctx, tool: str, args: dict, body) -> str:
    async def _main() -> str:
        async def _body() -> str:
            body.append(tool)
            return "BODY-RAN"

        return await pipeline.execute(ctx, tool, args, _body)

    return asyncio_run(_main())


def asyncio_run(coro) -> str:
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1) ScopeCheck：越范围硬拦截
# ---------------------------------------------------------------------------

def test_out_of_scope_target_hard_blocked(tmp_path) -> None:
    scope = ScopeConstraint(allowed_targets=["10.0.0.0/24", "*.corp.example.com"])
    ctx = make_ctx(tmp_path, task_id="t1", scope=scope, target="10.0.0.5")
    body: list[str] = []
    out = run(make_pipeline(), ctx, "shell",
              {"command": "ping -c1 172.16.0.5", "timeout": 5}, body)
    msg = json.loads(out)
    assert msg["error"] == "scope_block"
    assert msg["target"] == "172.16.0.5"
    assert body == []  # 未执行


def test_in_scope_target_passes_scope_and_runs_in_sandbox(tmp_path) -> None:
    scope = ScopeConstraint(allowed_targets=["10.0.0.0/24"])
    backend = _FakeBackend()
    ctx = make_ctx(tmp_path, task_id="t1", scope=scope, backend=backend)
    body: list[str] = []
    out = run(make_pipeline(), ctx, "shell",
              {"command": "nmap -sV 10.0.0.5", "timeout": 5}, body)
    # 范围放行 → 沙箱执行（shell 已替换为 fake 后端运行，body 不执行）
    assert "fake-ok" in out and backend.run_calls


def test_domain_wildcard_in_scope_allowed(tmp_path) -> None:
    scope = ScopeConstraint(allowed_targets=["*.corp.example.com"])
    backend = _FakeBackend()
    ctx = make_ctx(tmp_path, scope=scope, backend=backend)
    body: list[str] = []
    out = run(make_pipeline(), ctx, "shell",
              {"command": "curl http://api.corp.example.com:8080/health", "timeout": 5}, body)
    assert "fake-ok" in out


# ---------------------------------------------------------------------------
# 2) sandbox.confine：fail-closed + danger 拦截
# ---------------------------------------------------------------------------

def test_no_backend_fail_closed_no_bare_run(tmp_path) -> None:
    """护栏开启但未配置沙箱后端 → 拒绝执行（不裸跑），body 不被调用。"""
    ctx = make_ctx(tmp_path, task_id="t1", backend=None)
    body: list[str] = []
    out = run(make_pipeline(), ctx, "shell", {"command": "whoami", "timeout": 5}, body)
    msg = json.loads(out)
    assert msg["error"] == "sandbox_unavailable"
    assert body == []


def test_danger_command_blocked_by_wordlist(tmp_path) -> None:
    ctx = make_ctx(tmp_path, task_id="t1", backend=_FakeBackend())
    body: list[str] = []
    out = run(make_pipeline(), ctx, "shell", {"command": "rm -rf /"}, body)
    msg = json.loads(out)
    assert msg["error"] == "danger_block"
    assert body == []  # rm -rf / 永不执行


def test_write_tier_blocked_by_read_only_policy(tmp_path) -> None:
    ctx = make_ctx(tmp_path, task_id="t1", backend=_FakeBackend(),
                   policy=Policy(level="read-only"))
    body: list[str] = []
    out = run(make_pipeline(), ctx, "shell", {"command": "touch /tmp/x.txt"}, body)
    msg = json.loads(out)
    assert msg["error"] == "danger_block"
    assert body == []


def test_run_batch_not_sandboxed_yet_rejected_in_guard_mode(tmp_path) -> None:
    """护栏模式下未接入 bwrap 执行替换的工具拒绝裸跑（脚本内容词表无法覆盖）。"""
    ctx = make_ctx(tmp_path, task_id="t1", backend=_FakeBackend())
    body: list[str] = []
    out = run(make_pipeline(), ctx, "run_batch",
              {"script": "print('hi')", "timeout": 5}, body)
    msg = json.loads(out)
    assert msg["error"] == "sandbox_unavailable"
    assert "run_batch" in msg["detail"]
    assert body == []


# ---------------------------------------------------------------------------
# 3) ApprovalGate：T3 无审批暂停 → grant 后放行并沙箱执行
# ---------------------------------------------------------------------------

def test_t3_pending_blocks_then_grant_allows(tmp_path) -> None:
    bus = EventBus()
    gate = ApprovalGate(bus=bus)
    backend = _FakeBackend()
    ctx = make_ctx(tmp_path, task_id="eng-1", backend=backend, approval=gate)
    body: list[str] = []

    args = {"command": "whoami", "timeout": 5}
    out1 = run(make_pipeline(), ctx, "shell", args, body)
    msg1 = json.loads(out1)
    assert msg1["approval_status"] == "pending"  # T3 无审批暂停
    assert body == [] and backend.run_calls == []  # 既没跑 body 也没跑沙箱

    # 人工 grant 后同签名重试 → 放行 → around 用 fake 后端沙箱执行（body 不执行）
    assert gate.pending_count() == 1
    rid = json.loads(out1)["request_id"]
    gate.grant(rid, "grant", approver="admin", note="同意")
    out2 = run(make_pipeline(), ctx, "shell", args, body)
    assert body == []  # shell 已被沙箱执行替换，不走原 body
    assert len(backend.run_calls) == 1
    assert out2.startswith("rc=0") and "fake-ok" in out2


def test_t3_denied_gives_structured_rejection(tmp_path) -> None:
    bus = EventBus()
    gate = ApprovalGate(bus=bus)
    ctx = make_ctx(tmp_path, task_id="eng-1", backend=_FakeBackend(), approval=gate)
    args = {"command": "hydra -l admin 10.0.0.5", "timeout": 5}
    out1 = run(make_pipeline(), ctx, "shell", args, [])
    rid = json.loads(out1)["request_id"]
    gate.grant(rid, "deny", approver="admin", note="爆破不允许")
    body: list[str] = []
    out2 = run(make_pipeline(), ctx, "shell", args, body)
    msg2 = json.loads(out2)
    assert msg2["ok"] is False and msg2["approval_status"] == "denied"
    assert "拒绝" in msg2["reason"]
    assert body == []


def test_approval_records_visible_on_bus_after_pipeline(tmp_path) -> None:
    """整链调用后审批记录可从事件总线查到（approval/requested → resolved）。"""
    bus = EventBus()
    gate = ApprovalGate(bus=bus)
    ctx = make_ctx(tmp_path, task_id="eng-1", backend=_FakeBackend(), approval=gate)
    out = run(make_pipeline(), ctx, "shell", {"command": "whoami"}, [])
    rid = json.loads(out)["request_id"]
    gate.grant(rid, "grant")
    run(make_pipeline(), ctx, "shell", {"command": "whoami"}, [])
    kinds = [e["kind"] for e in bus.history("eng-1")]
    assert kinds.count("approval/requested") == 1  # 首次 pending 请求
    assert kinds.count("approval/resolved") == 2  # 人工 grant + 缓存命中 resolved


# ---------------------------------------------------------------------------
# 4) 无 l5 配置完全透传（存量零扰动）
# ---------------------------------------------------------------------------

def test_legacy_mode_without_l5_passthrough(tmp_path) -> None:
    ctx = make_ctx_plain(tmp_path)
    body: list[str] = []
    out = run(make_pipeline(), ctx, "shell", {"command": "whoami", "timeout": 5}, body)
    assert out == "BODY-RAN"  # 旧模式照常执行，中间件透传
