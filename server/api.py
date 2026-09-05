"""api.py —— JSON REST（HTTP RPC 上行面）。

对齐前端契约（apps/web/src/connection/api.ts）：
- 上行载体：POST /api/{method}，请求体 { rpcId, method, payload }（rpcId 仅载体，
  响应不做外包装回响；approval 回响发生在 WS 帧级 rpcId）；
- 业务错误恒 HTTP 200 + { ok:false, error:{code,message,details?} }，HTTP 状态只表达
  载体层（方法表 = api.ts API_METHODS：describe/targets/engagements/run/steer/
  respond/report）；
- describe：严格握手身份（result = HostDescription{product,version,serverTime}）；
- run：无 LLM Key（LLM_API_KEY / OPENAI_API_KEY）→ 结构化错误 llm_key_missing；
  有 Key 则新建任务书并逐目标入队 SessionManager（编排面立即返回 RunResponse）；
- respond：approval 应答——rpcId 原样回响 resolution 帧（ws 层），本层写裁决；
- report：桥接 profiles/practical_pentest/report/engine.py 纯函数投影（R5）。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from profiles.practical_pentest.report import generate_engagement_report
from server import SERVER_NAME, SERVER_VERSION
from server.fixture import build_report_snapshot, respond_aftermath, steer_reply
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
    """严格握手身份：product=SECAI-PT、version 冻结、serverTime 即时。"""
    return _ok({"product": SERVER_NAME, "version": SERVER_VERSION, "serverTime": now_iso()})


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
# run：任务书 → SessionManager 入队（无 LLM Key → 结构化错误）
# ---------------------------------------------------------------------------
async def api_run(request: Request) -> JSONResponse:
    payload = await _rpc_payload(request)
    targets = payload.get("allowedTargets")
    if not isinstance(targets, list) or not targets or not all(isinstance(t, str) and t for t in targets):
        return _error("bad_request", "allowedTargets 必须是非空的目标列表", {"field": "allowedTargets"})
    intensity = payload.get("maxIntensity")
    if intensity is not None and intensity not in MAX_INTENSITY_VALUES:
        return _error("bad_request", f"maxIntensity 非法（可用 {', '.join(MAX_INTENSITY_VALUES)}）", {"field": "maxIntensity"})
    if not _llm_key_present():
        return _error(
            "llm_key_missing",
            "未配置 LLM API Key，无法启动真实任务；离线演示请使用内置 demo engagement fixture。",
            {"hint": "配置 LLM_API_KEY 或 OPENAI_API_KEY 后重启服务（python -m server.main --port 8700）"},
        )
    state = _state(request)
    title = str(payload.get("title") or f"任务书 {uuid.uuid4().hex[:6]}")
    try:
        engagement_id, session_ids = state.start_run(title, targets)
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
    steer_reply(state, session_id, instruction)
    return _ok({})


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
            }
        )
    report = generate_engagement_report([snapshot], generated_at=now_iso())
    return _ok(
        {
            "engagementId": engagement_id,
            "status": "ready" if ready else "drafting",
            "sections": REPORT_SECTION_TITLES,
            "totalFindings": len(report.findings),
            "generatedAt": report.cover.get("generated_at"),
        }
    )


__all__ = [
    "REPORT_SECTION_TITLES",
    "api_describe",
    "api_engagements",
    "api_report",
    "api_respond",
    "api_run",
    "api_steer",
    "api_targets",
]
