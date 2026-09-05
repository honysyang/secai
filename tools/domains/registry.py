"""工具注册中心：清单单一事实源 + 按需加载控制（demo_tools 拆分——纯搬家）。

原 demo_tools.py 底部《工具按需加载（分桶）》区域整体迁移，逻辑不变：
- _TOOL_SPECS 声明式清单（工具对象 + 是否核心 + 归属分组）——单一事实源
- CORE_TOOL_NAMES / TOOL_GROUPS / _BASE_TOOLS / ALL_TOOL_NAMES / ALL_TOOLS 自动派生
- enable_tool / list_disabled_tools / _tool_gate：按需加载控制
- build_default_tools：构建「初始启用工具集」（核心 + 指定组）
"""
from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from bench_platform import platform_tools
from core.task_context import TaskContext
from profiles.ctf_legacy.platform import finalize
from tools.domains.artifacts import read_artifact, write_file
from tools.domains.blackboard import blackboard
from tools.domains.exec import checkpoint, http_request, parallel_shell, run_batch, set_phase, shell, think
from tools.domains.knowledge import (
    find_skills,
    get_knowledge,
    get_poc,
    list_knowledge,
    remember,
    search_cve,
    web_search,
)
from tools.domains.payload import detect_vuln, get_payload, list_vulns
from tools.domains.seccli import get_tool_spec, list_tools, run_tool
from tools.domains.subtask import spawn_subtask
from tools.domains.todo import todo_add, todo_list, todo_mark
from tools.domains.vpn import connect_vpn
from tools.domains.web import distinguish, exploit_fuzz, fuzz

# ================= 工具按需加载（分桶） =================
# 声明式工具清单（单一事实源）：工具对象 + 是否核心 + 归属分组。
# CORE_TOOL_NAMES / TOOL_GROUPS / _BASE_TOOLS 均由此自动派生，避免多份清单漂移
# （新增工具只需在此登记一行，核心/分组随定义一起声明，不再手写三处清单）。
_TOOL_SPECS = [
    # 核心工具（常驻，任何任务都需要，不参与按需分组）
    (shell, True, ()),
    (run_batch, True, ()),
    (http_request, True, ()),
    (read_artifact, True, ()),
    (write_file, True, ()),
    (finalize, True, ()),
    (checkpoint, True, ()),
    (think, True, ()),
    (todo_add, True, ()),
    (todo_list, True, ()),
    (todo_mark, True, ()),
    (remember, True, ()),
    (blackboard, True, ()),
    (set_phase, True, ()),
    (find_skills, True, ()),
    (fuzz, True, ()),
    (exploit_fuzz, True, ()),
    (spawn_subtask, True, ()),
    (parallel_shell, True, ()),
    # 按需工具（分组建制，enable_tool 可整组启用）
    (distinguish, False, ("web",)),
    (web_search, False, ("web",)),
    (list_tools, False, ("seccli",)),
    (get_tool_spec, False, ("seccli",)),
    (run_tool, False, ("seccli",)),
    (search_cve, False, ("poc",)),
    (get_poc, False, ("poc",)),
    (list_vulns, False, ("vuln",)),
    (detect_vuln, False, ("vuln",)),
    (get_payload, False, ("vuln",)),
    (list_knowledge, False, ("knowledge",)),
    (get_knowledge, False, ("knowledge",)),
    (connect_vpn, False, ("vpn",)),
]
# 平台工具（platform_tools 导入）：统一归入 platform 组，非核心
_TOOL_SPECS += [(t, False, ("platform",)) for t in platform_tools.PLATFORM_TOOLS]

_BASE_TOOLS = [t for t, _, _ in _TOOL_SPECS]
# 核心工具名（enable_tool/list_disabled_tools 为控制工具，单独挂载、不参与分组）
CORE_TOOL_NAMES = ({t.name for t, core, _ in _TOOL_SPECS if core}
                   | {"enable_tool", "list_disabled_tools"})
# 工具分组：由 _TOOL_SPECS 自动派生
TOOL_GROUPS: dict[str, list[str]] = {}
for _t, _, _groups in _TOOL_SPECS:
    for _g in _groups:
        TOOL_GROUPS.setdefault(_g, []).append(_t.name)

ALL_TOOL_NAMES = {t.name for t in _BASE_TOOLS}


# ================= 工具按需加载控制工具 =================
@function_tool
def enable_tool(ctx: RunContextWrapper[TaskContext], name: str) -> str:
    """启用一个此前未挂载的工具。可传「组名」（platform/poc/vuln/knowledge/seccli/web/vpn）
    或单个工具名；当前未挂载的工具用 list_disabled_tools 查看。"""
    c = ctx.context
    if c.enabled_tools is None:
        return json.dumps({"error": "当前上下文已全部启用工具，无需按需加载"}, ensure_ascii=False)
    key = (name or "").strip().lower()
    if key in TOOL_GROUPS:
        enabled = {n for n in TOOL_GROUPS[key]}
    else:
        hit = next((n for n in ALL_TOOL_NAMES if n.lower() == key), None)
        if hit is None:
            return json.dumps({"error": f"未知工具/组：{name}，可用 list_disabled_tools 查看"},
                              ensure_ascii=False)
        enabled = {hit}
    c.enabled_tools.update(enabled)
    return json.dumps({"enabled": sorted(enabled),
                       "available_now": sorted(c.enabled_tools)}, ensure_ascii=False)


@function_tool
def list_disabled_tools(ctx: RunContextWrapper[TaskContext]) -> str:
    """列出当前未挂载（需用 enable_tool 启用）的工具，以及可一次性启用的工具组。"""
    c = ctx.context
    if c.enabled_tools is None:
        return json.dumps({"disabled": [], "note": "全部工具已启用"}, ensure_ascii=False)
    disabled = sorted(n for n in ALL_TOOL_NAMES if n not in c.enabled_tools)
    return json.dumps({"disabled": disabled, "groups": TOOL_GROUPS}, ensure_ascii=False)


def _tool_gate(name: str, ctx: RunContextWrapper[TaskContext]) -> str:
    """逻辑开关：工具 schema 恒定挂载，但调用时检查是否已启用（兼容旧按需加载逻辑）。

    当前策略：开题一次性挂齐全部常用组，enable_tool 调用极少，前缀缓存更稳定。
    保留逻辑闸用于极少数未默认挂载的工具（如 connect_vpn 在不需 VPN 时）。
    """
    c = getattr(ctx, "context", None)
    if c is not None and c.enabled_tools is not None and name not in c.enabled_tools:
        return json.dumps({"error": f"工具 {name} 未启用，先用 enable_tool 挂载该工具/组"},
                          ensure_ascii=False)
    return ""


# 老命名兼容
_gate = _tool_gate


ALL_TOOLS = _BASE_TOOLS + [enable_tool, list_disabled_tools]


def build_default_tools(groups=("platform", "vpn", "seccli", "web", "poc", "vuln", "knowledge")) -> set:
    """构建「初始启用工具集」= 核心工具 + 指定工具组。

    用于 main.py 跑分任务初始化 ctx.enabled_tools：核心工具常驻，平台/VPN/安全CLI
    以及 web/poc/vuln/knowledge 等组一次性挂齐，避免运行时 enable_tool 变动工具 schema
    破坏前缀缓存。剩余未启用的工具仍可用 enable_tool 挂载（逻辑闸保留兼容）。
    """
    enabled = set(CORE_TOOL_NAMES)
    for g in groups:
        enabled.update(TOOL_GROUPS.get(g, []))
    return enabled
