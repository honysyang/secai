"""api.py —— JSON REST（HTTP RPC 上行面）。

对齐前端契约（apps/web/src/connection/api.ts）：
- 上行载体：POST /api/{method}，请求体 { rpcId, method, payload }（rpcId 仅载体，
  响应不做外包装回响；approval 回响发生在 WS 帧级 rpcId）；
- 业务错误恒 HTTP 200 + { ok:false, error:{code,message,details?} }，HTTP 状态只表达
  载体层（方法表 = api.ts API_METHODS：describe/targets/engagements/run/steer/
  respond/report）；
- describe：严格握手身份（result = HostDescription{product,version,serverTime}）；
- run：无 LLM Key（LLM_API_KEY / OPENAI_API_KEY）→ 结构化错误 llm_key_missing；
  有 Key 则把请求体（新式 {task_brief, targets, scope, settings} 或兼容旧式
  allowedTargets）经 server.run_spec 归一 → 逐目标接入真实执行 runner
  （harness/runner/pentest_target：ScopeCheck→审批门→只读工具→blackboard），
  编排面立即返回 RunResponse；
- steer：会话级指令——managed（真实 run）会话经 steer_queue 注入 runner 待消费，
  demo 会话沿用 fixture 演示应答；
- respond：approval 应答——rpcId 原样回响 resolution 帧（ws 层），本层写裁决并
  唤醒等待中的 run runner（allow/deny 真实裁决链路）；
- report：桥接 profiles/practical_pentest/report/engine.py 纯函数投影（R5）。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from profiles.practical_pentest.report import generate_engagement_report
from server import SERVER_NAME, SERVER_VERSION
from server.artifacts import FORMAT_MEDIA_TYPES
from server.fixture import APPROVAL_RPC, build_report_snapshot, respond_aftermath, steer_reply
from server.run_spec import RunSpecError, normalize_run_payload
from server.state import TERMINAL_STATUSES, now_iso

# 报告章节标题（/api/report → ReportSummary.sections，R5 章节合同中文标签）
REPORT_SECTION_TITLES = ["执行摘要", "攻击面图谱", "发现详情", "已排除攻击面", "跨目标攻击链", "方法论", "局限性"]
MAX_INTENSITY_VALUES = ("passive", "active", "aggressive")


# ---------------------------------------------------------------------------
# 信封工具
# ---------------------------------------------------------------------------
def _ok(result: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "result": result})


def _error(code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=200)


def _state(request: Request) -> Any:
    return request.app.state.state


async def _rpc_payload(request: Request) -> dict[str, Any]:
    """解析上行载体，取 payload（非法 JSON / 非 dict → 空 dict，由各方法判 bad_request）。"""
    try:
        raw = await request.json()
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    payload = raw.get("payload")
    return payload if isinstance(payload, dict) else {}


def _llm_key_present() -> bool:
    return bool((os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip())


# ---------------------------------------------------------------------------
# describe / targets / engagements
# ---------------------------------------------------------------------------
async def api_describe(request: Request) -> JSONResponse:
    """严格握手身份：product=SECAI-PT、version 冻结、serverTime 即时。

    附加 llmConfigured 布尔（复用 _llm_key_present，只暴露布尔不暴露 key 本身），
    供前端在「新建任务」前给出 LLM 配置状态可见性（产品化闭环）。
    """
    return _ok(
        {
            "product": SERVER_NAME,
            "version": SERVER_VERSION,
            "serverTime": now_iso(),
            "llmConfigured": _llm_key_present(),
        }
    )


async def api_targets(request: Request) -> JSONResponse:
    payload = await _rpc_payload(request)
    engagement_id = payload.get("engagementId")
    state = _state(request)
    headers = [rec.header() for rec in state.sessions.values()]
    if engagement_id:
        headers = [h for h in headers if h["engagementId"] == engagement_id]
    return _ok(headers)


async def api_engagements(request: Request) -> JSONResponse:
    state = _state(request)
    rows = []
    for eng in state.engagements.values():
        rows.append(
            {
                **eng.summary(),
                "status": state.engagement_status(eng),
            }
        )
    return _ok(rows)


# ---------------------------------------------------------------------------
# run：任务书 → 真实执行接线（无 LLM Key → 结构化错误；有 Key 才允许真实消耗）
# ---------------------------------------------------------------------------
async def api_run(request: Request) -> JSONResponse:
    payload = await _rpc_payload(request)
    try:
        spec = normalize_run_payload(payload)
    except RunSpecError as exc:
        return _error(exc.code, exc.message, exc.details)
    if not _llm_key_present():
        return _error(
            "llm_key_missing",
            "未配置 LLM API Key，无法启动真实任务；离线演示请使用内置 demo engagement fixture。",
            {"hint": "配置 LLM_API_KEY 或 OPENAI_API_KEY 后重启服务（python -m server.main --port 8700）"},
        )
    state = _state(request)
    # 延迟 import：只有真正启动真实执行才拉起 agents SDK / LLM 相关依赖
    from harness.runner.pentest_target import run_pentest_target

    meta: dict[str, Any] = {
        "bridge": state,
        "brief_text": spec["brief_text"],
        "scope": spec["scope"],
        "settings": spec["settings"],
        "labels": spec["labels"],
    }
    try:
        engagement_id, session_ids = state.start_run(
            spec["title"],
            spec["targets"],
            runner=run_pentest_target,
            labels=spec["labels"],
            meta=meta,
        )
    except Exception as exc:
        return _error("run_failed", f"任务入队失败：{type(exc).__name__}: {exc}")
    return _ok({"engagementId": engagement_id, "sessionIds": session_ids})


# ---------------------------------------------------------------------------
# steer / respond
# ---------------------------------------------------------------------------
async def api_steer(request: Request) -> JSONResponse:
    payload = await _rpc_payload(request)
    session_id = str(payload.get("sessionId") or "")
    instruction = str(payload.get("instruction") or "").strip()
    if not session_id or not instruction:
        return _error("bad_request", "sessionId 与 instruction 必填")
    state = _state(request)
    rec = state.sessions.get(session_id)
    if rec is None:
        return _error("unknown_session", f"未知 session: {session_id}")
    if rec.status not in ("running", "awaiting_approval"):
        return _error("session_inactive", f"会话 {session_id} 当前 {rec.status}，不接受指令")
    state.emit_event(session_id, "message", role="user", content=instruction)
    if rec.managed:
        # 真实 run 会话：指令投递给 runner 的 steer_queue（下一轮被消费执行）
        if rec.steer_queue is None:
            rec.steer_queue = asyncio.Queue()
        rec.steer_queue.put_nowait(instruction)
    else:
        # demo 会话：沿用 fixture 演示应答
        steer_reply(state, session_id, instruction)
    # 回执闭环：成功体带 received + sessionId（向后兼容，旧调用方忽略返回体）
    return _ok({"received": True, "sessionId": session_id})


async def api_respond(request: Request) -> JSONResponse:
    payload = await _rpc_payload(request)
    rpc_id = str(payload.get("rpcId") or "")
    decision = str(payload.get("decision") or "")
    comment = payload.get("comment")
    if comment is not None and not isinstance(comment, str):
        return _error("bad_request", "comment 必须是字符串")
    if not rpc_id:
        return _error("bad_request", "rpcId 必填（approval/requested 帧中的原样回响）")
    if decision not in ("allow", "deny"):
        return _error("bad_request", "decision 必须是 allow 或 deny")
    state = _state(request)
    resolution = state.resolve_approval(rpc_id, decision, comment=comment)
    if resolution is None:
        return _error("unknown_approval", f"未知或已裁决的审批请求 rpcId: {rpc_id}")
    # demo fixture 的审批（rpc-101）裁决后走演示后续脚本；真实 run 的裁决
    # 由 runner 侧的 approval waiter 直接唤醒，无需（也不应）触发 demo 后续
    if rpc_id == APPROVAL_RPC:
        respond_aftermath(state, decision)
    return _ok({})


# ---------------------------------------------------------------------------
# report：R5 报告引擎桥接
# ---------------------------------------------------------------------------
async def api_report(request: Request) -> JSONResponse:
    payload = await _rpc_payload(request)
    engagement_id = str(payload.get("engagementId") or "")
    state = _state(request)
    eng = state.engagements.get(engagement_id)
    if eng is None:
        return _error("unknown_engagement", f"未知任务书: {engagement_id}")
    statuses = [state.sessions[sid].status for sid in eng.session_ids if sid in state.sessions]
    ready = bool(statuses) and all(s in TERMINAL_STATUSES for s in statuses)
    snapshot = build_report_snapshot(state, engagement_id)
    if snapshot is None:
        return _ok(
            {
                "engagementId": engagement_id,
                "status": "ready" if ready else "drafting",
                "sections": [],
                "totalFindings": 0,
                "artifacts": [],
            }
        )
    report = generate_engagement_report([snapshot], generated_at=now_iso())
    # 「生成即存」：报告引擎输出后立刻落盘 md + json + pdf 三份产物
    # （幂等留档，不覆盖旧产物；pdf 为客户交付标准格式，随报告一并生成）
    metas = [
        state.artifacts.save_report(engagement_id, report, fmt="md"),
        state.artifacts.save_report(engagement_id, report, fmt="json"),
        state.artifacts.save_report(engagement_id, report, fmt="pdf"),
    ]
    return _ok(
        {
            "engagementId": engagement_id,
            "status": "ready" if ready else "drafting",
            "sections": REPORT_SECTION_TITLES,
            "totalFindings": len(report.findings),
            "generatedAt": report.cover.get("generated_at"),
            "artifacts": [m.to_dict() for m in metas],
        }
    )


# ---------------------------------------------------------------------------
# artifacts：报告产物检索与导出（一键导出闭环）
# ---------------------------------------------------------------------------
async def api_list_artifacts(request: Request) -> JSONResponse:
    """listArtifacts：列出任务书全部产物元信息（导出前先触发 /api/report 落盘）。"""
    payload = await _rpc_payload(request)
    engagement_id = str(payload.get("engagementId") or "")
    state = _state(request)
    if engagement_id not in state.engagements:
        return _error("unknown_engagement", f"未知任务书: {engagement_id}")
    metas = state.artifacts.list(engagement_id)
    return _ok({"artifacts": [m.to_dict() for m in metas]})


async def api_export_report(request: Request) -> Response:
    """exportReport：产物文件下载（HTTP 200 + Content-Disposition attachment）。

    业务约定例外：本端点成功路径返回原始文件流（非 _ok 信封），失败仍走
    恒 HTTP 200 的 _error JSON（not_found / bad_request）。
    """
    payload = await _rpc_payload(request)
    engagement_id = str(payload.get("engagementId") or "")
    fmt = str(payload.get("format") or "")
    if fmt not in FORMAT_MEDIA_TYPES:
        return _error("bad_request", f"format 必须是 {' / '.join(FORMAT_MEDIA_TYPES)}")
    state = _state(request)
    if engagement_id not in state.engagements:
        return _error("unknown_engagement", f"未知任务书: {engagement_id}")
    meta = state.artifacts.resolve(engagement_id, fmt)
    if meta is None or not Path(meta.path).is_file():
        return _error("not_found", f"该任务书尚无 {fmt} 格式产物——请先调用 /api/report 触发生成")
    data = Path(meta.path).read_bytes()
    filename = f"report-{engagement_id}.{fmt}"
    return Response(
        content=data,
        media_type=FORMAT_MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = [
    "REPORT_SECTION_TITLES",
    "api_describe",
    "api_engagements",
    "api_export_report",
    "api_list_artifacts",
    "api_report",
    "api_respond",
    "api_run",
    "api_steer",
    "api_targets",
]
