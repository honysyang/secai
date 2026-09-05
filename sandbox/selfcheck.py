"""sandbox 自检 —— python -m sandbox.selfcheck。

验证本机 L5 沙箱链路可用性：
1. bwrap 二进制存在且可执行；
2. 内核允许非特权用户创建 namespace（真实 bwrap 最小探针）；
3. 策略词表已加载（danger 拦截清单条数）；
4. 默认 fail-closed 行为（无后端时 confine 抛 SandboxUnavailableError）。

退出码：0 = 全部可用；1 = 存在不可用项（调用方应按 fail-closed 处理）。
"""
from __future__ import annotations

import sys

from sandbox import (
    BubblewrapSandbox,
    SandboxBlockedError,
    SandboxUnavailableError,
    confine,
)
from sandbox.policy import DANGER_REGEXES, DANGER_SUBSTRINGS


def _probe_real_bwrap() -> str | None:
    """用最小命令真实探针 bwrap；返回错误描述（None = 可用）。"""
    try:
        backend = BubblewrapSandbox()
        if not backend.available():
            return "bwrap 二进制缺失或不可执行（apt install bubblewrap）"
        res = backend.run(["true"], timeout=10)
        if not res.ok:
            return f"bwrap 最小探针失败 rc={res.rc}: {res.stderr.strip()[:200]}"
        return None
    except SandboxUnavailableError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - 自检需覆盖任意环境异常
        return f"bwrap 探针异常: {e}"


def main() -> int:
    print("== SECAI L5 sandbox 自检 ==")
    problems: list[str] = []

    backend = BubblewrapSandbox()
    print(f"[1] bwrap 二进制: {backend.bwrap_path!r} available={backend.available()}")
    if not backend.available():
        problems.append("bwrap 不可用")

    err = _probe_real_bwrap()
    print(f"[2] 真实 bwrap 最小探针: {'OK' if err is None else 'FAIL'}")
    if err is not None:
        problems.append(f"真实 bwrap 探针失败: {err}")

    print(f"[3] 危险词表: {len(DANGER_REGEXES)} 条正则 + {len(DANGER_SUBSTRINGS)} 条子串"
          f"（共 {len(DANGER_REGEXES) + len(DANGER_SUBSTRINGS)} 条，要求 ≥6）")
    if len(DANGER_REGEXES) + len(DANGER_SUBSTRINGS) < 6:
        problems.append("危险拦截清单不足 6 条")

    # fail-closed 行为探针：拔掉后端后 danger 命令必须抛 SandboxUnavailableError
    class _NoBackend:
        name = "none"
        available = lambda self: False  # noqa: E731

    try:
        confine("rm -rf /", backend=_NoBackend())  # type: ignore[arg-type]
        problems.append("无后端时 danger 命令未被 fail-closed 拦截（严重）")
    except SandboxUnavailableError:
        print("[4] fail-closed 无后端 danger → SandboxUnavailableError: OK")
    except SandboxBlockedError:
        problems.append("无后端时 danger 命令抛了 BlockedError 而非 UnavailableError（顺序异常）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"无后端探针异常: {e}")

    if problems:
        print("\n自检失败：")
        for p in problems:
            print(f"  - {p}")
        print("结论：沙箱链路不可用 —— 上层必须 fail-closed（禁止裸跑）。")
        return 1
    print("\n自检全部通过：L5 沙箱链路可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
