"""sandbox —— L5 命令沙箱（fail-closed 门面）。

规格来源：SECAI-PT 终极执行计划 v4 第五部分 R3。
对外核心入口 confine(argv, policy, backend, workdir)，执行链上统一在
「工具执行前」做命令分级 + 沙箱化裁定：

    confine(argv, policy) → ConfineResult(argv=已包装的沙箱命令) / 抛异常

fail-closed 裁定表：
    tier=danger   + backend 不可用 → SandboxUnavailableError（禁止裸跑）
    tier=danger   + backend 可用   → SandboxBlockedError（拦截清单，永不执行）
    tier 越 Policy 档位           → SandboxBlockedError（策略拒绝）
    其余           + backend 不可用 → SandboxUnavailableError（禁止裸跑回退）
    其余           + backend 可用   → ConfineResult（写档按 policy 决定工作区可写）
"""
from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass

from sandbox.backend import ExecResult, SandboxBackend, SandboxUnavailableError
from sandbox.bubblewrap import BubblewrapSandbox
from sandbox.policy import (
    READ_ONLY,
    WORKSPACE_WRITE,
    CommandTier,
    Policy,
    SandboxBlockedError,
    classify_command,
    danger_hit_reason,
)

# backend 未显式传入时的默认后端（bwrap；binary 缺失时 available()=False → fail-closed）
DEFAULT_BACKEND: SandboxBackend = BubblewrapSandbox()


@dataclass
class ConfineResult:
    """confine 裁定结果：tier + 已包装 argv（可直接交给 backend.run/subprocess）。"""

    tier: CommandTier
    argv: list[str]
    writable: bool
    backend: str
    policy_level: str


def _coerce_policy(policy: Policy | str | None) -> Policy:
    if policy is None:
        return Policy()
    if isinstance(policy, str):
        return Policy(level=policy)  # type: ignore[arg-type]  # 非法档位 __post_init__ 抛 ValueError
    return policy


def _argv_str(argv: str | Sequence[str]) -> str:
    return argv if isinstance(argv, str) else shlex.join(str(c) for c in argv)


def confine(
    argv: str | Sequence[str],
    policy: Policy | str | None = None,
    *,
    backend: SandboxBackend | None = None,
    workdir: str | None = None,
) -> ConfineResult:
    """命令分级 + 沙箱裁定（fail-closed）。返回 ConfineResult 或抛异常。

    argv：整条命令文本（内部包成 ["bash","-c", text] 交给沙箱）或 argv 序列。
    backend：None → 用默认 bwrap 后端（未安装时按不可用处理，仍 fail-closed）。
    workdir：workspace-write 命令的宿主工作区路径（可写挂载源）。
    """
    p = _coerce_policy(policy)
    text = _argv_str(argv)
    tier = classify_command(text)
    backend = backend if backend is not None else DEFAULT_BACKEND
    has_backend = bool(backend) and backend.available()

    if tier == "danger":
        hit = danger_hit_reason(text) or "危险命令"
        if not has_backend:
            raise SandboxUnavailableError(
                f"危险命令「{text[:120]}」且沙箱后端不可用，fail-closed 拒绝裸跑（{hit}）")
        raise SandboxBlockedError(f"{hit}：命令「{text[:120]}」已被 L5 拦截，永不执行")

    if not has_backend:
        raise SandboxUnavailableError(
            f"沙箱后端不可用，命令「{text[:120]}」无法沙箱化，fail-closed 拒绝裸跑")

    if not p.allows_tier(tier):
        raise SandboxBlockedError(
            f"命令档位 {tier} 超出策略档位 {p.level} 允许范围（只读策略拒绝写型命令）")

    writable = tier == WORKSPACE_WRITE and p.level == WORKSPACE_WRITE
    cmd_argv = list(argv) if isinstance(argv, (list, tuple)) else ["bash", "-c", argv]
    wrapped = backend.build_argv(cmd_argv, writable=writable, workdir=workdir)
    return ConfineResult(
        tier=tier,
        argv=wrapped,
        writable=writable,
        backend=backend.name,
        policy_level=p.level,
    )


__all__ = [
    "ConfineResult",
    "DEFAULT_BACKEND",
    "ExecResult",
    "READ_ONLY",
    "SandboxBackend",
    "SandboxBlockedError",
    "SandboxUnavailableError",
    "WORKSPACE_WRITE",
    "BubblewrapSandbox",
    "Policy",
    "classify_command",
    "confine",
]
