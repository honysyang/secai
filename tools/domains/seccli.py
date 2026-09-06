"""安全 CLI 工具域：本地安全 CLI 的查询 / 规格查看 / 执行（seccli 组）。

工具域模块：按功能域划分的工具实现。
- list_tools：列出已安装安全 CLI
- get_tool_spec：查看单个工具完整参数定义
- run_tool：执行本地安全 CLI（统一管线处理爆破预算/注入过滤/台账）
"""
from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from arsenal.registries import sec_tools
from core.task_context import TaskContext
from core.tool_pipeline import DEFAULT_PIPELINE, with_pipeline


@function_tool
def list_tools(ctx: RunContextWrapper[TaskContext], keyword: str = "", limit: int = 20) -> str:
    """列出本机已安装、可直接调用的安全 CLI 工具（nmap/sqlmap/ffuf/nuclei 等）。

    keyword 可按名称或描述过滤。看完整参数用 get_tool_spec，执行用 run_tool。
    """
    tools = sec_tools.available_tools()
    rows = []
    for name, spec in tools.items():
        if keyword and keyword.lower() not in f"{name} {spec.short_description}".lower():
            continue
        rows.append({"name": name, "description": spec.short_description or spec.description[:60]})
        if len(rows) >= max(1, limit):
            break
    return json.dumps({"available": len(tools), "tools": rows}, ensure_ascii=False)


@function_tool
def get_tool_spec(ctx: RunContextWrapper[TaskContext], tool_name: str) -> str:
    """查看某个安全工具的完整说明与参数定义（先 list_tools 找名字）。"""
    spec = sec_tools.get_spec(tool_name)
    if spec is None:
        return json.dumps({"error": f"工具 '{tool_name}' 不存在"}, ensure_ascii=False)
    return json.dumps({
        "name": spec.name,
        "command": spec.command,
        "description": spec.description,
        "parameters": [
            {"name": p.get("name"), "type": p.get("type"), "required": p.get("required"),
             "flag": p.get("flag"), "format": p.get("format"),
             "description": str(p.get("description", ""))[:200]}
            for p in spec.parameters
        ],
    }, ensure_ascii=False)


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def run_tool(ctx: RunContextWrapper[TaskContext], tool_name: str,
             args_json: str = "{}", timeout: int = 300) -> str:
    """执行一个本地安全 CLI 工具。

    args_json 是参数字典的 JSON 字符串，例如 '{"target":"10.0.0.1","ports":"80,443"}'。
    先 list_tools 看有哪些工具，再 get_tool_spec 看该工具的参数字段。

    爆破预算 / flag 提交 / 注入过滤 / 台账由统一管线处理。
    """
    try:
        args = json.loads(args_json) if args_json else {}
    except Exception as e:
        # 不要静默降级为 {} 再误执行，明确回错误让模型重试
        return json.dumps({"error": f"args_json 不是合法 JSON：{str(e)[:120]}，请修正后重试"},
                          ensure_ascii=False)
    if not isinstance(args, dict):
        return json.dumps({"error": "args_json 必须是对象（字典），请修正后重试"},
                          ensure_ascii=False)
    result = sec_tools.execute(tool_name, args, timeout=timeout)
    return json.dumps(result, ensure_ascii=False)
