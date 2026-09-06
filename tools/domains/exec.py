"""执行域：基础命令 / HTTP 执行 + 执行流程自管理工具。

R1 纯搬家：自 demo_tools.py 按功能域拆出，业务逻辑零改动。
- run_batch / shell / http_request / parallel_shell：统一管线执行工具
- _python_traceback_hint：python3 脚本报错提炼（shell 后置 hint）
- think / checkpoint / set_phase：执行者决策缓冲 / 里程碑存档 / 阶段切换

（9_6 注：原提交铁律 AutoSubmitFlagMiddleware 已随 CTF 跑分面删除；超大输出仍由
  core.tool_pipeline 的 ArtifactSpillMiddleware 统一外置。）
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests
from agents import RunContextWrapper, function_tool

from core.task_context import TaskContext
from core.tool_pipeline import DEFAULT_PIPELINE, with_pipeline
from tools.domains._base import PREVIEW


# 以下核心执行工具已纳入统一管线：横切关注点（预算/提交/注入/台账/打分/落盘）
# 由 DEFAULT_PIPELINE 处理，工具本体只负责业务逻辑与原始输出。
@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def run_batch(ctx: RunContextWrapper[TaskContext], script: str, timeout: int = 120) -> str:
    """程序化批量探测：一个脚本内部完成「枚举→筛选→追加验证」多步逻辑，只返回结论。

    适用：目录枚举后对 200 的逐个试 payload；差分实验（基线+变体族）；
    任意需要多步串联但只把结论回传 Agent 的场景。
    脚本用 python3 执行，print 输出结论；flag 出现系统机械提交。
    """
    c = ctx.context
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     dir=str(c.workdir)) as f:
        f.write(script)
        path = f.name
    try:
        env = os.environ.copy()
        env["TARGET_WORKDIR"] = str(c.workdir)
        p = subprocess.run(["python3", path], capture_output=True, text=True,
                           timeout=min(timeout, 300), cwd=str(c.workdir), env=env, check=False)
        out = f"rc={p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr[-2000:]}"
    except subprocess.TimeoutExpired:
        out = "[error] run_batch 超时——拆小脚本或加内部超时"
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
    return out


# ================= 基础执行 / 侦察工具 =================
def _python_traceback_hint(command: str, stderr: str, rc: int) -> str:
    """Python 脚本执行失败时，提炼 traceback 关键错误并给出修复指引。"""
    if rc == 0 or "Traceback" not in stderr:
        return ""
    if not re.search(r"\bpython3?(?:\s|$)", command):
        return ""
    lines = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    m = re.search(r'File "([^"]+\.py)"', stderr)
    script = m.group(1) if m else "脚本"
    return (f"\n[系统·脚本报错] {script} 执行失败（rc={rc}）：{last}\n"
            "请定位上述错误（常见：变量未定义/路径不对/编码问题），修正脚本后重新执行。")


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def shell(ctx: RunContextWrapper[TaskContext], command: str, timeout: int = 30) -> str:
    """在工作目录执行 shell 命令。探测请打包：一条命令完成多个动作。
    复杂逻辑（多行 python3 脚本）请先用 write_file 写到文件，再执行 `python3 <文件>`，避免命令参数过长撑爆上下文。
    """
    try:
        p = subprocess.run(["bash", "-c", command], capture_output=True, text=True,
                           timeout=min(timeout, 120), cwd=str(ctx.context.workdir), check=False)
        out = f"rc={p.returncode}\nstdout:\n{p.stdout[:PREVIEW]}\nstderr:\n{p.stderr[:1000]}"
        hint = _python_traceback_hint(command, p.stderr, p.returncode)
        if hint:
            out += hint
        return out
    except subprocess.TimeoutExpired:
        return f"命令超时（{timeout}s）。hint: 缩短范围或加 --max-time"
    except Exception as e:
        return f"执行失败: {str(e)[:300]}"


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def http_request(ctx: RunContextWrapper[TaskContext], url: str, method: str = "GET",
                 body: str = "", timeout: int = 15) -> str:
    """发送单次 HTTP 请求，返回状态码/响应头/正文预览。批量探测请用 shell+python3。"""
    try:
        r = requests.request(method, url, data=body or None,
                             timeout=min(timeout, 60), verify=False)
        head = "; ".join(f"{k}: {v}" for k, v in list(r.headers.items())[:8])
        out = f"status={r.status_code}\nheaders: {head}\nbody:\n{r.text[:PREVIEW]}"
        return out
    except Exception as e:
        return f"请求失败: {str(e)[:300]}"


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
async def parallel_shell(ctx: RunContextWrapper[TaskContext], commands: str,
                         timeout: int = 60, max_workers: int = 8) -> str:
    """并发执行多条独立 shell 命令（换行分隔），用于路径爆破/端口扫描/多 payload 测试等互不依赖的探测。

    commands 用换行分隔，每条独立并发执行，返回各自 rc/输出预览。
    使用 asyncio.to_thread 避免阻塞事件循环。
    """
    cmds = [c.strip() for c in (commands or "").split("\n") if c.strip()]
    if not cmds:
        return json.dumps({"error": "commands 为空"}, ensure_ascii=False)

    def _run(cmd):
        try:
            p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                               timeout=min(timeout, 120), cwd=str(ctx.context.workdir), check=False)
            out = (p.stdout or p.stderr or "")[:200]
            return {"cmd": cmd[:80], "rc": p.returncode, "out": out}
        except subprocess.TimeoutExpired:
            return {"cmd": cmd[:80], "error": "超时"}
        except Exception as e:
            return {"cmd": cmd[:80], "error": str(e)[:100]}

    max_workers = max(1, min(max_workers, len(cmds)))
    semaphore = asyncio.Semaphore(max_workers)

    async def _bounded(cmd):
        async with semaphore:
            return await asyncio.to_thread(_run, cmd)

    results = await asyncio.gather(*[_bounded(cmd) for cmd in cmds])
    return json.dumps({"results": results}, ensure_ascii=False)


@function_tool
def think(thought: str) -> str:
    """记录私有推理（无副作用，不产生新信息，仅作决策缓冲）。

    在关键决策前调用本工具，把「当前已知事实 → 待验证假设 → 下一步动作 → 风险评估」
    写清楚，避免边想边做导致冲动决策。典型场景：
    - 分析工具输出（SQL 报错 / 异常栈 / 响应差异）后，判断下一步该换什么 payload；
    - 构造 exploit 前，梳理攻击链每个环节与预期正/负证据；
    - 多面 flag 题：梳理「已拿几面、还差几面、下一面从哪个入口找」。

    不要用 think 聊天或输出最终结论——它是推理缓冲，不是输出通道。
    """
    if not thought or not thought.strip():
        return json.dumps({"error": "思考内容不能为空"}, ensure_ascii=False)
    return json.dumps({"ok": True, "note": "思考已记录"}, ensure_ascii=False)


@function_tool
def checkpoint(ctx: RunContextWrapper[TaskContext], reason: str = "") -> str:
    """在关键节点（阶段完成/重要发现/进展显著）主动存档，便于中断后 --resume 续跑。

    reason 说明为何存档（如「已完成端口扫描，得到 80/443 开放」）。调用时机由你判断，
    不必每轮都存，但遇到值得保留的里程碑时应主动存。
    """
    # 延迟 import，避免与 context_manager 的模块级循环依赖
    from core.context_manager import save_state
    c = ctx.context
    save_state(c.workdir, c, c.turn_count, c.task, c.charter, c.role)
    return json.dumps({"checkpointed": True, "turn": c.turn_count,
                       "reason": reason[:200]}, ensure_ascii=False)


@function_tool
def set_phase(ctx: RunContextWrapper[TaskContext], sub: str) -> str:
    """切换当前阶段（recon/enumerate/detect/exploit/post），驱动系统提示按阶段切换目标与焦点。

    当当前阶段目标已达成或需要换方向时调用；下一轮系统提示会自动带上新阶段的目标与焦点。
    注意：阶段之间有合法转移约束，乱跳会被拒绝（发现 flag 线索可直接切 post）。
    """
    from runtime.status import PHASE_DEFS, PHASE_TRANSITIONS, set_status
    c = ctx.context
    sub = (sub or "").strip().lower()
    if sub not in PHASE_DEFS:
        return json.dumps({"error": f"未知阶段 {sub}（可用 {list(PHASE_DEFS.keys())}）"},
                          ensure_ascii=False)
    if sub != c.phase:
        allowed = PHASE_TRANSITIONS.get(c.phase, [])
        if sub not in allowed:
            return json.dumps({"error": f"阶段 {c.phase} 只能切到 {allowed}，不能切到 {sub}"},
                              ensure_ascii=False)
    c.phase = sub
    set_status(c.workdir, "execute", "running", sub=sub, turn=c.turn_count)
    return json.dumps({"ok": True, "phase": sub,
                       "goal": PHASE_DEFS[sub]["goal"]}, ensure_ascii=False)
