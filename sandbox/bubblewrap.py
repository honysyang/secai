"""Bubblewrap(bwrap) 沙箱后端 —— 真实进程级隔离。

规格来源：SECAI-PT 终极执行计划 v4 第五部分 R3。
依赖：bubblewrap ≥ 0.8（本机 0.11.0，/usr/bin/bwrap）。安装：apt install bubblewrap。

挂载设计（build_argv，纯函数不执行）：
    bwrap --unshare-all            # 新 user/pid/ipc/uts/net/cgroup namespace
          --ro-bind / /            # 宿主根文件系统只读绑定（系统只读）
          --bind <workdir> <workdir>  # 仅工作区可写（writable=True 时追加）
          --dev /dev               # 干净的设备树（null/zero/random/tty…）
          --proc /proc
          --tmpfs /tmp             # 可写临时目录（宿主 /tmp 不可写）
          <业务 argv…>

注意：
- --unshare-all 包含新 net namespace → 沙箱内进程无外部网络（隔离副产品）。
  R3 以「文件系统破坏防护」为首要目标，按规格固定 --unshare-all；
  若未来需要沙箱内出网（如 curl 目标机），由上层按目标放开，不在此默认。
- run() 对「沙箱启动失败」（stderr 以 "bwrap: " 开头且 rc != 0）抛
  SandboxUnavailableError —— fail-closed，禁止降级裸跑。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from sandbox.backend import ExecResult, SandboxUnavailableError

BWRAP_FLAGS = [
    "--unshare-all",
    "--ro-bind", "/", "/",
    "--dev", "/dev",
    "--proc", "/proc",
    "--tmpfs", "/tmp",
]

_DEFAULT_BIN = os.environ.get("SECAI_BWRAP", "") or shutil.which("bwrap") or "bwrap"


@dataclass
class BubblewrapSandbox:
    """bwrap 0.11 实现。name="bwrap"，可注入 bwrap 路径（单测 mock 无 bwrap 用）。"""

    bwrap_path: str = _DEFAULT_BIN
    name: str = "bwrap"

    def available(self) -> bool:
        if not self.bwrap_path:
            return False
        return os.path.isfile(self.bwrap_path) and os.access(self.bwrap_path, os.X_OK)

    def build_argv(
        self,
        argv: Sequence[str],
        *,
        writable: bool,
        workdir: str | None = None,
    ) -> list[str]:
        """把业务 argv 包进 bwrap。writable=True 要求 workdir（--bind 源路径）。"""
        if not argv:
            raise ValueError("沙箱命令 argv 不能为空")
        cmd = [self.bwrap_path, *BWRAP_FLAGS]
        if writable:
            if not workdir:
                raise ValueError("workspace-write 命令沙箱化需要 workdir（可写挂载源）")
            workdir = os.path.abspath(workdir)
            cmd += ["--bind", workdir, workdir]
        cmd += list(argv)
        return cmd

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> ExecResult:
        """执行包装后的 argv（通常是 build_argv 的产物）。

        沙箱环境故障 → SandboxUnavailableError；命令本身 rc != 0 正常返回。
        """
        if not self.available():
            raise SandboxUnavailableError(
                f"bwrap 沙箱后端不可用（路径 {self.bwrap_path!r} 不存在或不可执行），fail-closed 拒绝执行")
        try:
            p = subprocess.run(
                list(argv), capture_output=True, text=True,
                timeout=timeout, cwd=cwd, check=False,
            )
        except FileNotFoundError:
            raise SandboxUnavailableError(
                f"bwrap 沙箱后端不可用（{self.bwrap_path!r} 无法启动），fail-closed 拒绝执行") from None
        except subprocess.TimeoutExpired as e:
            return ExecResult(rc=-124,
                              stdout=str(e.stdout or ""),
                              stderr=str(e.stderr or "") + "\n[error] 沙箱命令超时",
                              timed_out=True)
        stderr = p.stderr or ""
        if p.returncode != 0 and stderr.lstrip().startswith("bwrap: "):
            # 沙箱启动失败（内核禁 userns / 资源限制等），不是业务命令失败 → fail-closed
            raise SandboxUnavailableError(
                f"bwrap 沙箱启动失败（rc={p.returncode}）：{stderr.strip()[:300]}，fail-closed 拒绝执行")
        return ExecResult(rc=p.returncode, stdout=p.stdout or "", stderr=stderr)


__all__ = ["BubblewrapSandbox"]
