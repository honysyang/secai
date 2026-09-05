"""bubblewrap 真实沙箱测试 —— R3 验收「真实 bwrap 验证 rm -rf / 被拦 + workspace 可写」。

本机 bwrap 0.11.0 已装（/usr/bin/bwrap）。无 bwrap / 内核禁 userns 的环境自动 skip
（真实 bwrap 用例）；纯 build_argv / 不可用后端用例不依赖真机，任何环境都跑。
"""
from __future__ import annotations

import os

import pytest

from sandbox import SandboxBlockedError, SandboxUnavailableError, confine
from sandbox.backend import ExecResult
from sandbox.bubblewrap import BubblewrapSandbox


def _bwrap_ready() -> bool:
    """真实 bwrap 是否可跑（binary + 最小探针）。"""
    backend = BubblewrapSandbox()
    if not backend.available():
        return False
    try:
        res = backend.run(["true"], timeout=10)
        return res.ok
    except SandboxUnavailableError:
        return False


BWRAP_READY = _bwrap_ready()
needs_bwrap = pytest.mark.skipif(not BWRAP_READY, reason="本机 bwrap 不可用/内核禁 userns")


def test_build_argv_pure_flags() -> None:
    """build_argv 是纯函数：系统只读 + 默认 unshare/设备/proc/tmpfs 挂载。"""
    backend = BubblewrapSandbox(bwrap_path="/usr/bin/bwrap")
    argv = backend.build_argv(["whoami"], writable=False)
    assert argv[:2] == ["/usr/bin/bwrap", "--unshare-all"]
    assert "--ro-bind" in argv and "/" in argv
    assert "--dev" in argv and "/dev" in argv
    assert "--tmpfs" in argv
    assert "--bind" not in argv  # 只读档不 bind 工作区
    assert argv[-1] == "whoami"


def test_build_argv_writable_adds_bind() -> None:
    backend = BubblewrapSandbox(bwrap_path="/usr/bin/bwrap")
    argv = backend.build_argv(["touch", "/w/x"], writable=True, workdir="/w")
    i = argv.index("--bind")
    assert argv[i + 1] == "/w" and argv[i + 2] == "/w"


def test_run_with_missing_binary_raises_unavailable() -> None:
    """bwrap 二进制不存在 → SandboxUnavailableError（不静默降级）。"""
    backend = BubblewrapSandbox(bwrap_path="/nonexistent/bwrap")
    with pytest.raises(SandboxUnavailableError):
        backend.run(["true"])


@needs_bwrap
def test_real_bwrap_rm_root_blocked_by_wordlist() -> None:
    """真实 bwrap 后端 + 词表：rm -rf / 在 confine 层被拦（SandboxBlockedError），永不执行。"""
    backend = BubblewrapSandbox()
    with pytest.raises(SandboxBlockedError, match="拦截"):
        confine("rm -rf /", backend=backend)


@needs_bwrap
def test_real_bwrap_rm_root_inside_sandbox_harms_nothing(tmp_path) -> None:
    """纵深验证：即使 rm -rf / 绕过词表直达沙箱执行，也只作用于只读根，宿主无损。"""
    backend = BubblewrapSandbox()
    marker = "/etc/hostname"
    assert os.path.exists(marker)
    argv = backend.build_argv(["rm", "-rf", "/"], writable=False)
    res = backend.run(argv, timeout=30)
    assert res.rc != 0  # 只读根上删除必然失败
    assert os.path.exists(marker)  # 宿主文件系统完好


@needs_bwrap
def test_real_bwrap_workspace_writable(tmp_path) -> None:
    """真实 bwrap 验证 workspace 可写：工作区内 touch 成功且落盘到宿主工作区。"""
    backend = BubblewrapSandbox()
    workdir = str(tmp_path)
    target = str(tmp_path / "probe.txt")
    argv = backend.build_argv(["touch", target], writable=True, workdir=workdir)
    res = backend.run(argv, timeout=20, cwd=workdir)
    assert res.ok, f"touch 失败: rc={res.rc} stderr={res.stderr[:200]}"
    assert (tmp_path / "probe.txt").exists()


@needs_bwrap
def test_real_bwrap_system_readonly(tmp_path) -> None:
    """真实 bwrap 验证系统只读：/etc 下写文件失败，宿主 /etc 无残留。"""
    backend = BubblewrapSandbox()
    workdir = str(tmp_path)
    argv = backend.build_argv(["touch", "/etc/__secai_l5_probe__"], writable=True, workdir=workdir)
    res = backend.run(argv, timeout=20, cwd=workdir)
    assert res.rc != 0
    assert not os.path.exists("/etc/__secai_l5_probe__")


@needs_bwrap
def test_real_bwrap_simple_command_ok() -> None:
    backend = BubblewrapSandbox()
    res = backend.run(backend.build_argv(["echo", "hello"], writable=False), timeout=20)
    assert res.ok
    assert "hello" in res.stdout


def test_exec_result_ok_property() -> None:
    assert ExecResult(rc=0).ok
    assert not ExecResult(rc=1).ok
    assert not ExecResult(rc=0, timed_out=True).ok
