"""通用执行工具（兼容 re-export shim）。

R1 可测试性重构：原 1311 行巨石已按功能域拆分到 tools/domains/，本文件降为
纯 re-export shim，所有符号（工具对象 / helper / 常量 / 注册清单）均由各域模块
汇集再导出，`from demo_tools import build_default_tools` 等既有 import 完全兼容。

拆分映射：
- 基础执行 + 流程自管理 → tools/domains/exec.py（shell/run_batch/http_request/parallel_shell/think/checkpoint/set_phase）
- Web 探测 → tools/domains/web.py（distinguish/fuzz/exploit_fuzz）
- 知识与技能 → tools/domains/knowledge.py（web_search/find_skills/query_skills/search_cve/get_poc/list_knowledge/get_knowledge/remember）
- 漏洞/Payload → tools/domains/payload.py（list_vulns/detect_vuln/get_payload/_parse_payloads）
- 安全 CLI → tools/domains/seccli.py（list_tools/get_tool_spec/run_tool）
- 黑板 → tools/domains/blackboard.py
- 待办 → tools/domains/todo.py
- 子任务 → tools/domains/subtask.py
- Artifacts → tools/domains/artifacts.py
- 平台提交铁律 → profiles/ctf_legacy/platform.py（R4 H12：CTF 专属提交/终局代码收敛）
- VPN → tools/domains/vpn.py
- 共享安全纯函数 → tools/domains/_base.py
- 清单/分组/加载控制 → tools/domains/registry.py

（提交铁律：shell/http_request 等工具返回前机械扫描 flag 并自动提交，已由
  core.tool_pipeline 的 AutoSubmitFlagMiddleware / ArtifactSpillMiddleware 统一处理，
  _late_bind_submit 绑定逻辑随 profiles/ctf_legacy/platform.py 加载执行。）
"""
from __future__ import annotations

# 共享基础：常量 + 安全纯函数
from tools.domains._base import (
    PREVIEW,                       # noqa: F401
    FLAG_RE,                       # noqa: F401
    INJECTION_PATTERNS,            # noqa: F401
    INJECTION_WARNING,             # noqa: F401
    _guard_output,                 # noqa: F401
    _scan_flags,                   # noqa: F401
)

# 执行域
from tools.domains.exec import (
    run_batch,                     # noqa: F401
    shell,                         # noqa: F401
    http_request,                  # noqa: F401
    parallel_shell,                # noqa: F401
    _python_traceback_hint,        # noqa: F401
    think,                         # noqa: F401
    checkpoint,                    # noqa: F401
    set_phase,                     # noqa: F401
)

# Web 探测域
from tools.domains.web import (
    distinguish,                   # noqa: F401
    fuzz,                          # noqa: F401
    exploit_fuzz,                  # noqa: F401
    _replace_placeholder,          # noqa: F401
    _run_http,                     # noqa: F401
)

# 知识与技能域
from tools.domains.knowledge import (
    web_search,                    # noqa: F401
    find_skills,                   # noqa: F401
    query_skills,                  # noqa: F401
    search_cve,                    # noqa: F401
    get_poc,                       # noqa: F401
    list_knowledge,                # noqa: F401
    get_knowledge,                 # noqa: F401
    remember,                      # noqa: F401
)

# 漏洞 / Payload 域
from tools.domains.payload import (
    list_vulns,                    # noqa: F401
    detect_vuln,                   # noqa: F401
    get_payload,                   # noqa: F401
    _parse_payloads,               # noqa: F401
)

# 安全 CLI 域
from tools.domains.seccli import (
    list_tools,                    # noqa: F401
    get_tool_spec,                 # noqa: F401
    run_tool,                      # noqa: F401
)

# 黑板域
from tools.domains.blackboard import (
    blackboard,                    # noqa: F401
    _persist_blackboard,           # noqa: F401
    BLACKBOARD_MAX_ENTRIES,        # noqa: F401
    BLACKBOARD_FILE,               # noqa: F401
)

# 待办清单域
from tools.domains.todo import (
    todo_add,                      # noqa: F401
    todo_list,                     # noqa: F401
    todo_mark,                     # noqa: F401
    _VALID_TODO_PRIORITY,          # noqa: F401
    _VALID_TODO_STATUS,            # noqa: F401
)

# 子任务域
from tools.domains.subtask import (
    spawn_subtask,                 # noqa: F401
    finish_subtask,                # noqa: F401
)

# Artifacts 域
from tools.domains.artifacts import (
    read_artifact,                 # noqa: F401
    write_file,                    # noqa: F401
    _spill_output,                 # noqa: F401
    ARTIFACT_SPILL_THRESHOLD,      # noqa: F401
)

# 平台域（flag 提交铁律 / 通关复核 / finalize；R4 H12 收敛到 profiles/ctf_legacy）
from profiles.ctf_legacy.platform import (
    _is_completed,                 # noqa: F401
    _submit_flags_if_any,          # noqa: F401
    _late_bind_submit,             # noqa: F401
    finalize,                      # noqa: F401
)

# VPN 域
from tools.domains.vpn import (
    connect_vpn,                   # noqa: F401
)

# 工具注册中心：清单派生常量 + 按需加载控制
from tools.domains.registry import (
    _TOOL_SPECS,                   # noqa: F401
    _BASE_TOOLS,                   # noqa: F401
    CORE_TOOL_NAMES,               # noqa: F401
    TOOL_GROUPS,                   # noqa: F401
    ALL_TOOL_NAMES,                # noqa: F401
    ALL_TOOLS,                     # noqa: F401
    enable_tool,                   # noqa: F401
    list_disabled_tools,           # noqa: F401
    _tool_gate,                    # noqa: F401
    _gate,                         # noqa: F401
    build_default_tools,           # noqa: F401
)

__all__ = [
    # _base
    "PREVIEW", "FLAG_RE", "INJECTION_PATTERNS", "INJECTION_WARNING",
    "_guard_output", "_scan_flags",
    # exec
    "run_batch", "shell", "http_request", "parallel_shell",
    "_python_traceback_hint", "think", "checkpoint", "set_phase",
    # web
    "distinguish", "fuzz", "exploit_fuzz", "_replace_placeholder", "_run_http",
    # knowledge
    "web_search", "find_skills", "query_skills", "search_cve", "get_poc",
    "list_knowledge", "get_knowledge", "remember",
    # payload
    "list_vulns", "detect_vuln", "get_payload", "_parse_payloads",
    # seccli
    "list_tools", "get_tool_spec", "run_tool",
    # blackboard
    "blackboard", "_persist_blackboard", "BLACKBOARD_MAX_ENTRIES", "BLACKBOARD_FILE",
    # todo
    "todo_add", "todo_list", "todo_mark", "_VALID_TODO_PRIORITY", "_VALID_TODO_STATUS",
    # subtask
    "spawn_subtask", "finish_subtask",
    # artifacts
    "read_artifact", "write_file", "_spill_output", "ARTIFACT_SPILL_THRESHOLD",
    # platform
    "_is_completed", "_submit_flags_if_any", "_late_bind_submit", "finalize",
    # vpn
    "connect_vpn",
    # registry
    "_TOOL_SPECS", "_BASE_TOOLS", "CORE_TOOL_NAMES", "TOOL_GROUPS",
    "ALL_TOOL_NAMES", "ALL_TOOLS", "enable_tool", "list_disabled_tools",
    "_tool_gate", "_gate", "build_default_tools",
]
