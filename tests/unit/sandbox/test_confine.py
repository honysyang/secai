"""sandbox confine() 单测 —— fail-closed 裁定表 + 词表/策略联动（R3 验收核心）。

覆盖：
- 无后端 + danger 命令 → SandboxUnavailableError（不裸跑，fail-closed）；
- 有后端 + danger 命令 → SandboxBlockedError（拦截清单，永不执行）；
- 无后端 + 普通命令 → SandboxUnavailableError（禁止裸跑回退）；
- 写型命令超 Policy 档位 → SandboxBlockedError；
- 有后端 + 放行 → ConfineResult（tier/argv/writable 正确）。
"""
from __future__ import annotations

import pytest

from sandbox import (
    ConfineResult,
    SandboxBlockedError,
    SandboxUnavailableError,
    confine,
)
from sandbox.backend import ExecResult
from sandbox.policy import Policy


class _UnavailableBackend:
    """mock 无 bwrap（available=False → fail-closed 路径）。"""

    name = "none"

    def available(self) -> bool:
        return False

    def build_argv(self, argv, *, writable, workdir=None):  # pragma: no cover
        raise AssertionError("不可用后端不应被要求 build_argv")

    def run(self, argv, **kw):  # pragma: no cover
        raise AssertionError("不可用后端不应被要求 run")


class _FakeOkBackend:
    """mock 可用后端：记录 build_argv 调用，argv 前缀 wrap。"""

    name = "fake"

    def __init__(self):
        self.build_calls: list[dict] = []

    def available(self) -> bool:
        return True

    def build_argv(self, argv, *, writable, workdir=None):
        self.build_calls.append({"argv": list(argv), "writable": writable, "workdir": workdir})
        return ["wrap", *argv]

    def run(self, argv, *, timeout=None, cwd=None):
        return ExecResult(rc=0, stdout="ok")


NO_BACKEND = _UnavailableBackend()


def test_danger_without_backend_raises_unavailable() -> None:
    """无 bwrap 时 danger 命令抛 SandboxUnavailableError 不裸跑（R3 验收）。"""
    with pytest.raises(SandboxUnavailableError, match="fail-closed"):
        confine("rm -rf /", backend=NO_BACKEND)


def test_danger_with_backend_raises_blocked() -> None:
    """有后端 danger 也被拦截清单拒绝（rm -rf / 永不执行）。"""
    with pytest.raises(SandboxBlockedError, match="拦截"):
        confine("rm -rf /", backend=_FakeOkBackend())


def test_allowed_command_without_backend_fail_closed() -> None:
    """普通命令无后端同样 fail-closed（禁止裸跑回退）。"""
    with pytest.raises(SandboxUnavailableError):
        confine("cat /etc/hostname", backend=NO_BACKEND)


def test_read_only_command_with_backend_ok() -> None:
    res: ConfineResult = confine("cat /etc/hostname", backend=_FakeOkBackend())
    assert res.tier == "read-only"
    assert res.writable is False
    assert res.argv[0] == "wrap"
    assert res.backend == "fake"


def test_workspace_write_command_makes_writable() -> None:
    ok = _FakeOkBackend()
    res = confine("touch /tmp/probe.txt", backend=ok, workdir="/work")
    assert res.tier == "workspace-write"
    assert res.writable is True
    assert ok.build_calls and ok.build_calls[-1]["writable"] is True
    assert ok.build_calls[-1]["workdir"] == "/work"


def test_write_tier_blocked_by_read_only_policy() -> None:
    ok = _FakeOkBackend()
    with pytest.raises(SandboxBlockedError, match="只读策略"):
        confine("touch /tmp/probe.txt", Policy(level="read-only"), backend=ok)


def test_write_tier_blocked_by_danger_policy() -> None:
    ok = _FakeOkBackend()
    with pytest.raises(SandboxBlockedError):
        confine("python3 exploit.py", Policy(level="danger"), backend=ok)


def test_read_only_tier_allowed_even_under_strict_policy() -> None:
    res = confine("whoami", Policy(level="danger"), backend=_FakeOkBackend())
    assert res.tier == "read-only" and res.writable is False


def test_str_argv_is_wrapped_in_bash() -> None:
    ok = _FakeOkBackend()
    res = confine("echo hi", backend=ok)
    assert res.argv[:3] == ["wrap", "bash", "-c"]
    assert res.argv[3:] == ["echo hi"]


def test_list_argv_preserved() -> None:
    ok = _FakeOkBackend()
    res = confine(["bash", "-c", "echo hi"], backend=ok)
    assert res.argv == ["wrap", "bash", "-c", "echo hi"]


def test_invalid_policy_level_raises() -> None:
    with pytest.raises(ValueError):
        confine("ls", "not_a_level", backend=_FakeOkBackend())


def test_writable_without_workdir_raises() -> None:
    """可写挂载需要宿主 workdir（bubblewrap build_argv 的纯函数硬约束，不依赖 bwrap 可用性）。"""
    from sandbox.bubblewrap import BubblewrapSandbox

    real = BubblewrapSandbox(bwrap_path="/usr/bin/bwrap")
    with pytest.raises(ValueError):
        real.build_argv(["touch", "/tmp/x"], writable=True)  # writable=True 且无 workdir
