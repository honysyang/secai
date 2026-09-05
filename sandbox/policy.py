"""命令分级策略词表 —— L5 沙箱策略层。

规格来源：SECAI-PT 终极执行计划 v4 第五部分 R3。
职责：
1. Policy：会话级沙箱预设，三档 trust 梯度——
   - read-only       只读侦察态：任何写型命令一律拒绝，挂载全只读；
   - workspace-write 默认态：写型命令可在工作区落地，系统仍只读；
   - danger          高敏态：只放行只读命令，其余全部拒绝（写即危险）。
2. 命令分级词表 classify_command()：把一条命令文本归为
   read-only / workspace-write / danger 三档——
   - danger 拦截清单：rm -rf /、mkfs、dd 写裸盘、chmod/chown -R /、关机重启、fork 炸弹等；
   - read-only 白名单：常见纯只读侦查命令；
   - 其余默认 workspace-write（写能力被沙箱限制在工作区，见 bubblewrap）。
3. SandboxBlockedError：词表拦截 / 策略级别拒写（confine 内抛出）。

分类只看「命令文本」，不执行进程，纯函数无副作用。
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# 三级词表档位（classify 返回值 + Policy 档位共用常量）
READ_ONLY = "read-only"
WORKSPACE_WRITE = "workspace-write"
DANGER = "danger"

CommandTier = Literal["read-only", "workspace-write", "danger"]
PolicyLevel = Literal["read-only", "workspace-write", "danger"]

# ---------------------------------------------------------------------------
# danger 拦截清单（rm -rf / 等自毁/破坏性命令）—— 永不执行，即使沙箱内也不放行
# ---------------------------------------------------------------------------

# 正则条目（整条命中即 danger）
DANGER_REGEXES: tuple[str, ...] = (
    # 1. rm -rf / 及变体（-rf/-fr/-Rf/-rF…，作用于根目录）：允许任何顺序的 r/f 组合
    r"\brm\s+-(?:[a-zA-Z]*[rR][a-zA-Z]*[fF][a-zA-Z]*|[a-zA-Z]*[fF][a-zA-Z]*[rR][a-zA-Z]*)\s+/(?:\*)?(?:\s|$|[;&|])",
    # 2. 绕过保护根的强制删除
    r"\brm\s+--no-preserve-root\b",
    # 3. 文件系统格式化
    r"\bmkfs(?:\.[a-z0-9]+)?\b",
    # 4. dd 直接写裸盘设备
    r"\bdd\b[^\n;&|]*\bof=/dev/(?:sd|vd|hd|nvme|mmcblk|xvd)[a-z]\b",
    # 5. 对根目录递归 chmod 全权
    r"\bchmod\s+(?:-[a-zA-Z]*[Rr][a-zA-Z]*\s+)?(?:777|000|a\+rwx)\s+/(?:\s|$)",
    # 6. 对根目录递归 chown
    r"\bchown\s+(?:-[a-zA-Z]*[Rr][a-zA-Z]*\s+)?[a-zA-Z0-9_.-]+:[a-zA-Z0-9_.-]+\s+/(?:\s|$)",
    # 7. 关机 / 重启 / 断电
    r"\b(?:shutdown|reboot|halt|poweroff)\b",
)

# 子串条目（命令文本包含即 danger）—— 主要覆盖 shell 语法类危险模式
DANGER_SUBSTRINGS: tuple[str, ...] = (
    ":(){",        # fork 炸弹 :(){ :|:& };:
    "|:&",         # fork 炸弹管道段
    ">/dev/sd",    # 重定向覆写裸盘
    "> /dev/sd",
)

# 只读白名单：命令名 → 该命令只需只读文件系统（无写工作区需求）
READ_ONLY_COMMANDS: frozenset[str] = frozenset({
    "ls", "cat", "head", "tail", "pwd", "whoami", "id", "uname", "echo",
    "printf", "date", "env", "printenv", "stat", "file", "grep", "wc",
    "find", "test", "true", "false", "hostname", "df", "du", "sha256sum",
    "md5sum", "base64", "strings", "readlink", "realpath", "seq", "sleep",
})

# 会吞掉真实命令名的前缀包装器（取其后的命令名判定白名单）
_CMD_WRAPPERS: frozenset[str] = frozenset({"sudo", "env", "nohup", "time", "command", "exec", "timeout"})


class SandboxBlockedError(PermissionError):
    """命令被策略拦截：danger 拦截清单命中，或超出 Policy 档位允许范围。"""


# ---------------------------------------------------------------------------
# Policy：会话级沙箱预设
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Policy:
    """沙箱会话策略档位。默认 workspace-write（系统只读 + 工作区可写）。"""

    level: PolicyLevel = WORKSPACE_WRITE

    def __post_init__(self) -> None:
        if self.level not in (READ_ONLY, WORKSPACE_WRITE, DANGER):
            raise ValueError(f"未知策略档位: {self.level!r}（可用 {READ_ONLY}/{WORKSPACE_WRITE}/{DANGER}）")

    def allows_write(self) -> bool:
        """本策略是否允许工作区写（唯一允许写的是 workspace-write 档）。"""
        return self.level == WORKSPACE_WRITE

    def allows_tier(self, tier: CommandTier) -> bool:
        """命令档位是否在本策略允许范围内。"""
        if tier == READ_ONLY:
            return True  # 只读命令三档策略都放行（danger 档也只读放行）
        if tier == WORKSPACE_WRITE:
            return self.level == WORKSPACE_WRITE
        return False  # danger 命令一律不放行（由 confine 抛 SandboxBlockedError）


# ---------------------------------------------------------------------------
# 命令分级
# ---------------------------------------------------------------------------

def _first_command_token(command: str) -> str:
    """尽力提取真实命令名（剥掉 sudo/env/… 包装与变量前缀）。"""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for tok in tokens:
        name = tok.strip().lstrip("\\").split("/")[-1]
        # 跳过 VAR=val 前缀与 $VAR 引用
        if "=" in name and not name.startswith(("$", "'", '"')):
            continue
        if name.startswith("$"):
            continue
        if name in _CMD_WRAPPERS:
            continue  # 找下一个非包装 token
        return name
    return ""


def classify_command(command: str | Sequence[str]) -> CommandTier:
    """命令分级：danger 拦截清单命中 → "danger"；只读白名单 → "read-only"；否则 "workspace-write"。

    输入可为整条命令文本（shell/run_batch 的 command/script 参数）或 argv 序列。
    纯函数、无副作用、不执行进程。
    """
    text = command if isinstance(command, str) else shlex.join(str(c) for c in command)
    norm = re.sub(r"\s+", " ", text).strip()
    if not norm:
        return READ_ONLY

    if any(re.search(p, norm) for p in DANGER_REGEXES):
        return DANGER
    if any(sub in norm for sub in DANGER_SUBSTRINGS):
        return DANGER

    if _first_command_token(norm) in READ_ONLY_COMMANDS:
        return READ_ONLY
    return WORKSPACE_WRITE


def danger_hit_reason(command: str | Sequence[str]) -> str:
    """命中 danger 拦截清单的规则描述（审计/报错用）；未命中返回空串。"""
    text = command if isinstance(command, str) else shlex.join(str(c) for c in command)
    norm = re.sub(r"\s+", " ", text).strip()
    for i, p in enumerate(DANGER_REGEXES, start=1):
        if re.search(p, norm):
            return f"危险命令拦截清单 #{i}: {p}"
    for sub in DANGER_SUBSTRINGS:
        if sub in norm:
            return f"危险命令拦截子串: {sub!r}"
    return ""


__all__ = [
    "DANGER",
    "DANGER_REGEXES",
    "DANGER_SUBSTRINGS",
    "READ_ONLY",
    "READ_ONLY_COMMANDS",
    "WORKSPACE_WRITE",
    "SandboxBlockedError",
    "classify_command",
    "danger_hit_reason",
]
