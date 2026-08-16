"""TSecBench 平台工具：把 platform_client 的六个接口封装成 Agent 可调用的 function_tool。

这是「跑分任务」能真正跑通的关键——执行者通过这些工具按标准流程编排：
    check_vpn → list_challenges → start_challenge → 渗透 → submit_flag → close_challenge
"""
from __future__ import annotations

import json

from agents import function_tool, RunContextWrapper

from adapters.config import BENCHMARK_BASE_URL, BENCHMARK_TOKEN
from bench_platform.platform_client import PlatformClient, TaskNotFound, TaskEnded
from core.task_context import TaskContext

# 单次工具返回的字符上限，避免塞爆上下文
_PREVIEW = 6000


def _client() -> PlatformClient:
    """用 .env 里的凭证构造平台客户端（无状态，按需 new）。"""
    return PlatformClient(BENCHMARK_BASE_URL, BENCHMARK_TOKEN)


def _ok(data) -> str:
    return json.dumps(data, ensure_ascii=False)[:_PREVIEW]


def _err(ctx: RunContextWrapper[TaskContext], e: Exception) -> str:
    """平台错误处置：TaskEnded/TaskNotFound 是致命错误，标记 ctx.fatal 让主循环终止；
    其余错误降级为文本返回给 LLM 自行判断（不吞致命错误，避免无限空转）。"""
    if isinstance(e, TaskEnded):
        ctx.context.fatal = "task_ended"
        return json.dumps({"fatal": "task_ended", "message": str(e)[:200]}, ensure_ascii=False)
    if isinstance(e, TaskNotFound):
        ctx.context.fatal = "task_not_found"
        return json.dumps({"fatal": "task_not_found", "message": str(e)[:200]}, ensure_ascii=False)
    return json.dumps({"error": str(e)[:300]}, ensure_ascii=False)


@function_tool
def check_vpn() -> str:
    """VPN 联通预检：请求 http://10.0.100.58，status=="ok" 视为 VPN 已连通。

    这是跑分流程的强制前置。若不通过，先调用 connect_vpn 启动 VPN 再重试本工具。
    """
    try:
        return _ok({"ok": True, "data": _client().check_vpn()})
    except Exception as e:
        return _ok({"ok": False, "error": str(e)[:200],
                    "hint": "VPN 未连通，请调用 connect_vpn 启动后再重试"})


@function_tool
def list_challenges(ctx: RunContextWrapper[TaskContext]) -> str:
    """获取题目列表及每题作答进度（unique_code/难度/总分/flag 数/是否通关/容器状态等）。"""
    try:
        return _ok(_client().list_challenges())
    except Exception as e:
        return _err(ctx, e)


@function_tool
def start_challenge(ctx: RunContextWrapper[TaskContext], unique_code: str) -> str:
    """启动一道题的靶场容器，返回 container_addr（IP:端口），用于后续渗透。"""
    try:
        addrs = _client().start_challenge(unique_code)
        ctx.context.current_code = unique_code  # 记录当前题，供提交铁律机械提交
        return _ok({"unique_code": unique_code, "container_addr": addrs})
    except Exception as e:
        return _err(ctx, e)


@function_tool
def get_hint(ctx: RunContextWrapper[TaskContext], unique_code: str) -> str:
    """获取某题提示（注意：查看提示后该题 flag 得分会按比例扣减）。"""
    try:
        hint = _client().get_hint(unique_code)
        return _ok({"unique_code": unique_code, "hint": hint})
    except Exception as e:
        return _err(ctx, e)


@function_tool
def submit_flag(ctx: RunContextWrapper[TaskContext], unique_code: str, flag: str) -> str:
    """提交 flag。返回 correct（是否正确）/awarded（得分）/cumulative_score 等。

    重复提交同一正确 flag 返回 duplicate（幂等，跳过即可）。
    错误提交会累计到本题的 wrong_submit_count，用于熔断恋战。
    """
    c = ctx.context
    if flag in c.submitted:
        return _ok({"note": "该 flag 已提交过，跳过", "duplicate": True, "flag": flag})
    c.submitted.add(flag)
    try:
        r = _client().submit_flag(unique_code, flag)
    except Exception as e:
        return _err(ctx, e)
    if not r.get("correct") and not r.get("duplicate"):
        c.wrong_submit_count += 1
    return _ok(r)


@function_tool
def close_challenge(ctx: RunContextWrapper[TaskContext], unique_code: str) -> str:
    """关闭某题容器、释放活跃名额。通关或放弃后务必调用。"""
    try:
        closed = _client().close_challenge(unique_code)
        if ctx.context.current_code == unique_code:
            ctx.context.current_code = ""  # 关闭后清空当前题标记
        return _ok({"unique_code": unique_code, "closed": closed})
    except Exception as e:
        return _err(ctx, e)


PLATFORM_TOOLS = [check_vpn, list_challenges, start_challenge,
                  get_hint, submit_flag, close_challenge]
