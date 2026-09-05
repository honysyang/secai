"""CTF Legacy 平台域：跑分平台的 flag 提交铁律 / 通关复核 / finalize。

R4 H12 收敛：原 demo_tools → tools/domains/platform.py 的 CTF 专属代码整包移入
profiles/ctf_legacy/，主循环（app/ harness/ core/ tools/）不再触碰平台凭证常量：
- _submit_flags_if_any：提交铁律——扫描 flag、机械提交 + 机械通关判决
- _is_completed：平台通关状态机械复核（记分牌是唯一权威）
- _late_bind_submit：把提交函数绑定到默认管线的 auto_submit_flag middleware
- finalize：任务收尾（机械复核未通关则拒绝）

平台协议实现与客户端实例统一收敛在 bench_platform/（模块级单例
get_platform_client 为唯一构造点），本模块不直接读 BENCHMARK_BASE_URL/BENCHMARK_TOKEN，
「凭证是否配置」经 platform_configured() 判定。
"""
from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from bench_platform.platform_client import (
    PlatformClient,
    TaskEnded,
    TaskNotFound,
    get_platform_client,
    platform_configured,
)
from core.task_context import TaskContext
from core.tool_pipeline import DEFAULT_PIPELINE, with_pipeline
from runtime.log import log_warn
from tools.domains._base import _scan_flags


def _is_completed(client: PlatformClient, code: str) -> bool:
    """机械复核平台通关状态（记分牌是唯一权威）。"""
    try:
        for c in client.list_challenges():
            if c.get("unique_code") == code:
                return bool(c.get("is_completed"))
    except (TaskEnded, TaskNotFound):
        raise                       # R3：终局信号必须上抛
    except Exception:
        return False
    return False


def _submit_flags_if_any(ctx: RunContextWrapper[TaskContext], text: str) -> str:
    """提交铁律：扫描完整输出中的 flag，机械提交 + 机械通关判决。

    被 core.tool_pipeline.AutoSubmitFlagMiddleware / ArtifactSpillMiddleware 调用，
    作为统一工具管线的后置动作。客户端经 get_platform_client() 单例获取。
    """
    flags = _scan_flags(text)
    if not flags:
        return ""
    log_warn(f"[FLAG] 检测到 flag：{', '.join(flags)}")
    c = ctx.context
    code = c.current_code
    notes = [f"[系统·检测到flag] {f}" for f in flags]
    if not code:
        notes.append("[系统] 当前题 unique_code 未知，请调用 submit_flag 手动提交")
        return "\n".join(notes)
    if not platform_configured():
        notes.append("[系统] 未配置平台凭证（BENCHMARK_BASE_URL/BENCHMARK_TOKEN），无法机械提交")
        return "\n".join(notes)

    client = get_platform_client()
    for f in flags:
        if f in c.submitted:
            continue
        c.submitted.add(f)
        try:
            r = client.submit_flag(code, f)
        except (TaskEnded, TaskNotFound):
            c.fatal = "task_ended"    # R3：与 platform_tools 行为对齐
            raise
        except Exception as e:
            notes.append(f"[系统·提交异常] {str(e)[:120]}")
            continue

        notes.append(f"[系统·提交铁律] {f} → {json.dumps(r, ensure_ascii=False)[:200]}")
        if not r.get("correct"):
            c.wrong_submit_count += 1
            if getattr(c, "turn_gain", False):   # 有正向证据 = 有效推进，重置
                c.wrong_submit_count = 0
            continue

        # ---- R1 核心：correct=true 后的机械判决，不等 LLM finalize ----
        c.correct_flags.append(f)
        fc, tc = r.get("correct_flag_count"), r.get("total_flag_count")
        log_warn(f"[FLAG] {f} 提交正确（进度 {fc}/{tc or '?'}）")
        if fc and tc and fc < tc:
            notes.append(
                f"[系统] 本题共 {tc} 面 flag，已拿 {fc} 面——"
                f"继续找下一面，不要 finalize")
        else:
            # 单 flag 题或最后一面：机械复核平台通关状态
            try:
                done = _is_completed(client, code)
            except (TaskEnded, TaskNotFound):
                c.fatal = "task_ended"
                raise
            if done:
                c.finalized = True
                c.final_payload = {"findings":
                    f"平台确认 {code} 全部 flag 通关（铁律提交，机械判决）"}
                notes.append("[系统·通关判决] 平台 is_completed=true，本题结束，"
                             "系统将自动换题")
    return "\n".join(notes)


# 把实际提交函数绑定到默认管线的 flag 扫描 middleware，保持铁律提交不丢失。
def _late_bind_submit() -> None:
    for _mw in DEFAULT_PIPELINE.middlewares:
        if getattr(_mw, "name", "") == "auto_submit_flag":
            _mw.submit_fn = _submit_flags_if_any  # type: ignore
            break


# 定义后立即把实际提交函数绑定到默认管线，让管线工具复用同一套铁律提交逻辑。
_late_bind_submit()


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def finalize(ctx: RunContextWrapper[TaskContext], findings: str = "") -> str:
    """当你认为任务已完成（目标达成或证据枯竭）时调用，提交最终结论并结束本次执行。

    系统会机械复核平台通关状态：未通关则拒绝结束并回注提示。
    """
    c = ctx.context
    # 机械复核：有题码且有平台凭证时，以平台 is_completed 为唯一通关依据
    if c.current_code and platform_configured():
        try:
            done = _is_completed(get_platform_client(), c.current_code)
        except (TaskEnded, TaskNotFound):
            c.fatal = "task_ended"
            raise
        except Exception:
            done = None  # 复核失败不阻断（网络抖动），但标注未确认
        if done is False:
            fc = len(getattr(c, "correct_flags", []))
            tc = getattr(c, "total_flag_count", "?")
            return (f"[系统·复核拒绝] 平台确认本题尚未通关（已拿 {fc}/{tc} 面 flag）。"
                    "finalize 被拒绝：请继续攻击，或在证据彻底枯竭时说明理由后重试。")
    c.finalized = True
    c.final_payload = {"findings": findings}
    return json.dumps({"finalized": True, "findings": findings}, ensure_ascii=False)
