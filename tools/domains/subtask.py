"""子任务域：声明与收尾协议（父任务后台并发调度 + 子任务结构化汇报）。

R1 纯搬家：自 demo_tools.py 按功能域拆出，业务逻辑零改动。
- spawn_subtask：声明独立子任务（objective 必填，含三道闸门）
- finish_subtask：完成子任务并结构化汇报（子任务专用结束协议）
"""
from __future__ import annotations

import json
import uuid

from agents import RunContextWrapper, function_tool

from core.task_context import TaskContext
from core.tool_pipeline import DEFAULT_PIPELINE, with_pipeline


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def spawn_subtask(ctx: RunContextWrapper[TaskContext], desc: str,
                  objective: str = "", branch_type: str = "",
                  depends_on: str = "", max_tokens: int = 0,
                  max_turns: int = 8) -> str:
    """声明一个独立子任务（互不依赖的探测点/线），主循环会后台并发调度执行。

    当任务同时出现多个独立的探测分支（如 2 个端口、2 个独立漏洞点）时，
    用本工具分别声明子任务；每个子任务用独立会话后台执行，结果写回黑板（subtask:<id>）。
    建议每题最多声明 2 个并行分支，desc 必须包含具体目标（URL/IP/路径）。

    三闸门：
    - 明确目标：objective 必填，空则拒绝创建
    - 独立预算：max_tokens / max_turns 限制子任务资源
    - 前提证伪级联回收：depends_on 声明的黑板前提被证伪（status=failed / 被
      supersedes）时，系统自动回收该子任务，避免在已判死方向上空耗预算

    Args:
        desc: 子任务描述，必须包含具体目标、范围边界、成功标准。
        objective: 子任务明确目标（必填），如"验证 /api/login 是否存在 SQLi"。
        branch_type: 分支类型（如 web/pwn/crypto/reverse/web3），子任务会按此派任角色。
        depends_on: 本子任务依赖的前提（黑板 key，逗号分隔）；任一前提被证伪即级联回收。
        max_tokens: 子任务 token 预算上限（0=继承父任务剩余预算）。
        max_turns: 子任务回合预算上限（默认 8）。
    """
    c = ctx.context
    objective = (objective or "").strip()
    if not objective:
        return json.dumps({"error": "objective 不能为空：子任务必须填写明确目标"}, ensure_ascii=False)
    from core.task_context import SubtaskBudget
    sub = {
        "id": uuid.uuid4().hex[:8],
        "desc": desc,
        "objective": objective,
        "branch_type": branch_type,
        "depends_on": (depends_on or "").strip(),
        "status": "pending",
        "result": "",
        "budget": SubtaskBudget(
            objective=objective,
            max_tokens=max_tokens,
            max_turns=max_turns,
        ),
    }
    c.subtasks.append(sub)
    return json.dumps({"spawned": {"id": sub["id"], "objective": objective,
                                   "max_turns": max_turns, "max_tokens": max_tokens}},
                      ensure_ascii=False)


@function_tool
def finish_subtask(ctx: RunContextWrapper[TaskContext], summary: str,
                   findings: str = "", flag: str = "") -> str:
    """完成子任务并结构化汇报（子任务专用结束协议）。调用后即结束，不再继续执行。

    Args:
        summary: 任务结论（一两句话），自包含——主 Agent 只看得到这个结果。
        findings: 关键发现列表（换行分隔，如 URL/参数名/凭据/文件路径等具体事实）。
        flag: 拿到的完整 flag（flag{...}）；没拿到就留空，不要编造。
    """
    c = ctx.context
    c.finalized = True
    c.final_payload = {
        "summary": (summary or "").strip(),
        "findings": [x.strip() for x in (findings or "").split("\n") if x.strip()],
        "flag": (flag or "").strip() or None,
    }
    return json.dumps({"ok": True, "summary": (summary or "").strip()[:200]},
                      ensure_ascii=False)
