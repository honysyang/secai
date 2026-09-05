"""Payload/漏洞检测域：内置漏洞类型与载荷字典（vuln 组 + fuzz 共享解析）。

R1 纯搬家：自 demo_tools.py 按功能域拆出，业务逻辑零改动。
- list_vulns：列出内置漏洞类型检测模块
- detect_vuln：按类型取标准检测规范 + 基础 payload
- get_payload：按类型取载荷字典
- _parse_payloads：解析载荷（内置字典优先，否则逗号列表 / a-b 数值范围）
"""
from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from arsenal.registries import vuln_registry
from core.task_context import TaskContext
from core.tool_pipeline import DEFAULT_PIPELINE, with_pipeline


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def list_vulns(ctx: RunContextWrapper[TaskContext]) -> str:
    """列出系统内置的漏洞类型检测模块（SQLI/XSS/SSTI/LFI/RCE/IDOR/SSRF/XXE/UPLOAD）。

    先看有哪些类型，再用 detect_vuln 取某类型的标准检测规范与 payload。
    """
    return json.dumps({"vulns": vuln_registry.list_vulns()}, ensure_ascii=False)


@function_tool
def detect_vuln(ctx: RunContextWrapper[TaskContext], vuln_type: str) -> str:
    """按漏洞类型加载标准检测规范 + 基础 payload（如 SQLI/XSS/SSTI）。

    指纹到目标后，用本工具取对应漏洞类型的标准打法，再结合 shell/http_request 执行。
    先 list_vulns 看有哪些类型。
    """
    v = vuln_registry.get_vuln(vuln_type)
    if v is None:
        return json.dumps({"error": f"未找到漏洞类型 {vuln_type}，可用 list_vulns 查看"},
                          ensure_ascii=False)
    return json.dumps({
        "type": v.type,
        "name": v.name,
        "description": v.description,
        "need_detect": v.need_detect,
        "prompt": v.prompt,
        "payloads": v.payloads,
    }, ensure_ascii=False)


@function_tool
def get_payload(ctx: RunContextWrapper[TaskContext], payload_type: str) -> str:
    """按类型取 payload 字典（sqli/path/lfi/xss/ssrf/ssti/rce/idor/upload/xxe），返回每行一个载荷。

    用于路径爆破、注入 fuzz 等；配合 parallel_shell 或 shell 使用。
    """
    payloads = vuln_registry.load_payloads(payload_type)
    if not payloads:
        return json.dumps({"error": f"未找到 payload 类型 {payload_type}（可用 sqli/path/lfi/xss 等）"},
                          ensure_ascii=False)
    return json.dumps({"type": payload_type, "count": len(payloads),
                       "payloads": payloads}, ensure_ascii=False)


def _parse_payloads(payloads: str, payload_type: str) -> list[str]:
    """解析载荷：payload_type 优先从内置字典加载，否则解析 payloads（逗号分隔或 a-b 范围）。"""
    if payload_type:
        pl = vuln_registry.load_payloads(payload_type.strip().lower())
        return [p for p in pl if p.strip()]
    if (payloads or "").strip():
        raw = payloads.strip()
        if "-" in raw and raw.replace("-", "").isdigit():
            lo_s, hi_s = raw.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if hi < lo:
                lo, hi = hi, lo
            if hi - lo > 499:
                hi = lo + 499
            return [str(i) for i in range(lo, hi + 1)]
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []
