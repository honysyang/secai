"""沙箱后端协议 —— fail-closed。

规格来源：SECAI-PT 终极执行计划 v4 第五部分 R3。
SandboxBackend 是「把命令 argv 包装进隔离执行环境」的协议：

- build_argv()：纯函数，把业务命令 argv 变换为「带沙箱包装」的完整 argv（供 subprocess 直跑）；
- run()：实际执行包装后的 argv，返回结构化 ExecResult；
- available()：后端当前是否可用（二进制存在等）。

失败语义（铁律，禁止裸跑回退）：
- SandboxUnavailableError：后端不可用 / 启动失败 / 内核限制 —— 任何情况下都不允许降级为
  「不带沙箱直接跑」；
- 命令是否危险、是否放行由 sandbox.policy 与 confine() 判定，backend 层不做策略裁决。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class SandboxUnavailableError(RuntimeError):
    """沙箱后端不可用（无 bwrap / 内核禁 userns / 启动失败）。

    fail-closed：调用方必须把该异常当作「命令不能执行」处理，
    禁止任何裸跑 fallback（不裸跑 = R3 验收红线）。
    """


@dataclass
class ExecResult:
    """沙箱内命令执行结果（backend.run 的结构化返回值）。"""

    rc: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out


class SandboxBackend(Protocol):
    """进程级隔离后端协议。"""

    name: str

    def available(self) -> bool:
        """后端当前是否可用（如 bwrap 二进制存在且可执行）。"""
        ...

    def build_argv(
        self,
        argv: Sequence[str],
        *,
        writable: bool,
        workdir: str | None = None,
    ) -> list[str]:
        """纯函数：把业务命令 argv 包装成可交给 subprocess.run 的沙箱 argv。

        writable=True 时要求 workdir 非空（工作区可写挂载需要宿主路径）。
        实现不得执行任何进程。
        """
        ...

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> ExecResult:
        """执行包装后的 argv，返回结构化结果。

        后端环境故障（二进制缺失 / 沙箱启动失败 / 内核限制）抛 SandboxUnavailableError，
        命令本身失败（rc != 0）属于正常返回，不抛异常。
        """
        ...


__all__ = ["ExecResult", "SandboxBackend", "SandboxUnavailableError"]
