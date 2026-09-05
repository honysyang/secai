"""server/api.py 单测 —— RPC 信封 / describe / run 无 key / respond 回响 / 报告引擎桥接。

用 starlette TestClient（app 独立 AppState + fixture，互不污染）验证：
- 业务错误恒 HTTP 200 {ok:false,error:{code,message}}（载体层语义）；
- describe 200 → ok:true + result{product,version,serverTime}（HostDescription）；
- targets/engagements 列出内置 demo fixture；
- run 无 LLM Key → 结构化错误 llm_key_missing（不创建任务书）；
- respond → approval/resolved 帧 rpcId 原样回响（mux 流可收到）；
- report → R5 报告引擎桥接（totalFindings 与 generate_engagement_report 一致）；
- GET / 服务 dist index（SPA fallback 路径）。

运行：.venv/bin/python -m pytest tests/unit/test_server_api.py -v
"""
from __future__ import annotations

import os

# 关掉 demo ticker 干扰（在 import server.* 前生效）
os.environ.setdefault("SECAI_DEMO_TICK_SECONDS", "3600")

import pytest
from starlette.testclient import TestClient

from profiles.practical_pentest.report import generate_engagement_report
from server.fixture import APPROVAL_RPC, DEMO_ENGAGEMENT_ID, SESSION_B, SESSION_C, build_report_snapshot
from server.main import create_app


@pytest.fixture()
def app_client():
    app = create_app()
    with TestClient(app) as client:
        yield app.state.state, client


def _post(client: TestClient, method: str, payload: dict) -> object:
    return client.post(
        f"/api/{method}", json={"rpcId": "rpc-test", "method": method, "payload": payload}
    )


def _drain_until(client: TestClient, ws, predicate, max_frames: int = 400) -> dict:
    """从 mux 流持续收帧直到命中谓词（超限抛断言）。"""
    for _ in range(max_frames):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    raise AssertionError("drain_until 超限未命中（帧序列异常或流已断）")


def test_describe_returns_handshake_identity(app_client) -> None:
    """describe 200：信封 ok + HostDescription{product,version,serverTime}。"""
    _state, client = app_client
    res = _post(client, "describe", {})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    result = body["result"]
    assert result["product"] == "SECAI-PT"
    assert result["version"]
    assert result["serverTime"].endswith("Z")


def test_targets_lists_demo_fixture_sessions(app_client) -> None:
    """targets 返回三目标会话行头（running/awaiting_approval/completed）。"""
    _state, client = app_client
    body = _post(client, "targets", {}).json()
    assert body["ok"] is True
    headers = body["result"]
    assert {h["sessionId"] for h in headers} == {"sess-a-demo", "sess-b-demo", "sess-c-demo"}
    by_id = {h["sessionId"]: h for h in headers}
    assert by_id["sess-a-demo"]["status"] == "running"
    assert by_id["sess-b-demo"]["status"] == "awaiting_approval"
    assert by_id["sess-c-demo"]["status"] == "completed"
    assert all(h["engagementId"] == DEMO_ENGAGEMENT_ID for h in headers)


def test_engagements_lists_demo(app_client) -> None:
    """engagements 返回内置 demo 任务书（live-engagement）。"""
    _state, client = app_client
    body = _post(client, "engagements", {}).json()
    assert body["ok"] is True
    rows = body["result"]
    assert [r["engagementId"] for r in rows] == [DEMO_ENGAGEMENT_ID]
    assert rows[0]["status"] == "running"


def test_run_without_llm_key_returns_structured_error(app_client, monkeypatch) -> None:
    """run 无 LLM Key → HTTP 200 {ok:false,error.code=llm_key_missing}，不建任务书。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _state, client = app_client
    res = _post(client, "run", {"allowedTargets": ["10.10.5.2"]})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "llm_key_missing"
    assert body["error"]["message"]
    # 任务书数不变（仍只有 demo fixture）
    assert len(_post(client, "engagements", {}).json()["result"]) == 1


def test_run_missing_allowed_targets_bad_request(app_client, monkeypatch) -> None:
    """run 缺 allowedTargets → bad_request（参数校验先于 key 检查）。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _state, client = app_client
    body = _post(client, "run", {}).json()
    assert body["ok"] is False and body["error"]["code"] == "bad_request"


def test_respond_echoes_rpc_id_resolution_on_mux(app_client) -> None:
    """respond：rpcId 原样回响 approval/resolved 帧（mux 流可收到裁决）。"""
    _state, client = app_client
    with client.websocket_connect("/api/events.mux") as ws:
        # 等 fixture 的 approval/requested（rpcId=rpc-101）
        requested = _drain_until(
            client, ws, lambda f: f.get("type") == "approval/requested" and f.get("rpcId") == APPROVAL_RPC
        )
        assert requested["sessionId"] == SESSION_B
        assert requested["payload"]["action"] == "执行登录口令探测（hydra）"

        # POST /api/respond 裁决（allow + 备注）
        res = _post(client, "respond", {"rpcId": APPROVAL_RPC, "decision": "allow", "comment": "放行"})
        assert res.status_code == 200
        assert res.json() == {"ok": True, "result": {}}

        # mux 流收到 approval/resolved：rpcId 原样回响
        resolved = _drain_until(
            client, ws, lambda f: f.get("type") == "approval/resolved" and f.get("rpcId") == APPROVAL_RPC
        )
        assert resolved["sessionId"] == SESSION_B
        assert resolved["payload"]["decision"] == "allow"
        assert resolved["payload"]["comment"] == "放行"
        assert resolved["payload"]["decidedAt"].endswith("Z")
        # B 会话已转 running（状态在编排面落账）
        target_b = next(h for h in _post(client, "targets", {}).json()["result"] if h["sessionId"] == SESSION_B)
        assert target_b["status"] == "running"


def test_respond_unknown_rpc_business_error(app_client) -> None:
    """respond 未知 rpcId → 200 + {ok:false,error.code=unknown_approval}。"""
    _state, client = app_client
    body = _post(client, "respond", {"rpcId": "rpc-404", "decision": "allow"}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unknown_approval"


def test_respond_invalid_decision_bad_request(app_client) -> None:
    _state, client = app_client
    body = _post(client, "respond", {"rpcId": APPROVAL_RPC, "decision": "maybe"}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_request"


def test_report_bridges_report_engine(app_client) -> None:
    """report：completed 会话 → R5 报告引擎投影，totalFindings 与引擎输出一致。"""
    state, client = app_client
    body = _post(client, "report", {"engagementId": DEMO_ENGAGEMENT_ID}).json()
    assert body["ok"] is True
    result = body["result"]
    assert result["engagementId"] == DEMO_ENGAGEMENT_ID
    # A/B 会话仍活跃 → drafting；C completed 已进引擎
    assert result["status"] == "drafting"
    assert result["totalFindings"] > 0
    assert isinstance(result["sections"], list) and result["sections"]
    assert result["generatedAt"]

    # 引擎同源直算 → 完全一致（纯函数桥接证明）
    snapshot = build_report_snapshot(state, DEMO_ENGAGEMENT_ID)
    assert snapshot is not None
    assert snapshot.profiles and snapshot.profiles[0].target_id == SESSION_C
    report = generate_engagement_report([snapshot], generated_at=result["generatedAt"])
    assert len(report.findings) == result["totalFindings"]
    assert report.findings[0].target_id == SESSION_C
    severities = {f.severity for f in report.findings}
    assert {"high", "medium", "low"} <= severities


def test_report_unknown_engagement_error(app_client) -> None:
    _state, client = app_client
    body = _post(client, "report", {"engagementId": "eng-nope"}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unknown_engagement"


def test_spa_serves_dist_index_and_fallback(app_client) -> None:
    """GET / 返回 dist index.html 200；未知前端路由走 SPA fallback。"""
    _state, client = app_client
    root = client.get("/")
    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
    assert "SECAI-PT" in root.text

    fallback = client.get("/some/spa/route")
    assert fallback.status_code == 200
    assert "SECAI-PT" in fallback.text

    asset = client.get("/favicon.svg")
    assert asset.status_code == 200
