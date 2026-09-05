"""server/ws.py 单测 —— 帧 schema / 纯下行 1008 / fixture 首帧 subscribed / report+growth。

验证前端契约（api.ts MuxFrame/HostFrame 类型表）：
- /api/events.mux：连接即回放，首帧 session/subscribed（lastSeq=回放基线）；
  帧即 MuxFrame（无 rpcId 外包装），session/event 带 SessionEvent 字段，
  projection 带 seq+values；C 会话含 report/growth 投影；B 会话含 approval 帧；
- /api/events.host：首帧 host/session-added（三会话登记，状态三态齐全）；
- 纯下行：客户端上行消息 → 服务端 1008 关闭。

运行：.venv/bin/python -m pytest tests/unit/test_server_ws.py -v
"""
from __future__ import annotations

import os

# 关掉 demo ticker 干扰（在 import server.* 前生效）
os.environ.setdefault("SECAI_DEMO_TICK_SECONDS", "3600")

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.fixture import APPROVAL_RPC, SESSION_A, SESSION_B, SESSION_C
from server.main import create_app

MUX_TYPES = {
    "session/event",
    "session/subscribed",
    "session/queue",
    "session/jobs",
    "session/projection",
    "approval/requested",
    "approval/resolved",
    "stream/error",
}
HOST_TYPES = {
    "host/session-added",
    "host/session-removed",
    "host/session-status",
    "host/engagement-changed",
    "stream/error",
}


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


def _drain(client: TestClient, ws, predicate, max_frames: int = 500) -> dict:
    for _ in range(max_frames):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    raise AssertionError("drain 超限未命中目标帧")


def test_mux_first_frame_is_subscribed_and_schema(client) -> None:
    """首帧 session/subscribed；后续帧全部符合 MuxFrame 类型表与字段 schema。"""
    with client.websocket_connect("/api/events.mux") as ws:
        first = ws.receive_json()
        assert first["type"] == "session/subscribed"
        assert first["sessionId"] in (SESSION_A, SESSION_B, SESSION_C)
        assert isinstance(first["lastSeq"], int) and first["lastSeq"] >= 0

        for _ in range(20):
            frame = ws.receive_json()
            assert isinstance(frame, dict) and "type" in frame
            assert frame["type"] in MUX_TYPES
            if frame["type"] == "session/event":
                data = frame["data"]
                for key in ("eventId", "seq", "type", "data", "createdAt"):
                    assert key in data
                assert frame["sessionId"] in (SESSION_A, SESSION_B, SESSION_C)
            elif frame["type"] == "session/projection":
                assert isinstance(frame["seq"], int)
                assert isinstance(frame["values"], dict)
            elif frame["type"] == "approval/requested":
                assert frame["rpcId"]
                for key in ("action", "description", "createdAt"):
                    assert key in frame["payload"]
            # 消息即帧：不得有 rpcId 外包装（approval 帧内 rpcId 属帧体）
            assert set(frame) <= {"type", "sessionId", "data", "lastSeq", "items", "jobs", "seq", "values", "rpcId", "payload", "message"}


def test_mux_replays_completed_session_report_and_growth(client) -> None:
    """C 会话回放含 report 与 growth 投影（completed 演示态全量）。"""
    with client.websocket_connect("/api/events.mux") as ws:
        seen_report = seen_growth = False
        for _ in range(500):
            frame = ws.receive_json()
            if frame.get("type") == "session/projection" and frame.get("sessionId") == SESSION_C:
                if "report" in frame["values"]:
                    seen_report = True
                if "growth" in frame["values"]:
                    seen_growth = True
            if seen_report and seen_growth:
                break
        assert seen_report and seen_growth


def test_mux_carries_approval_request_frame(client) -> None:
    """B 会话挂 approval/requested 帧（rpcId=rpc-101，可 respond 回响）。"""
    with client.websocket_connect("/api/events.mux") as ws:
        frame = _drain(
            client,
            ws,
            lambda f: f.get("type") == "approval/requested" and f.get("sessionId") == SESSION_B,
        )
        assert frame["rpcId"] == APPROVAL_RPC
        assert frame["payload"]["action"] == "执行登录口令探测（hydra）"


def test_host_replays_three_sessions_with_full_status_set(client) -> None:
    """host 流首段为三会话 host/session-added，状态 = running/awaiting_approval/completed。"""
    with client.websocket_connect("/api/events.host") as ws:
        added = []
        for _ in range(10):
            frame = ws.receive_json()
            assert frame["type"] in HOST_TYPES
            if frame["type"] == "host/session-added":
                header = frame["session"]
                assert header["sessionId"] in (SESSION_A, SESSION_B, SESSION_C)
                for key in ("sessionId", "target", "status", "engagementId", "createdAt", "updatedAt"):
                    assert key in header
                added.append(header)
            if len(added) == 3:
                break
        assert len(added) == 3
        assert {h["status"] for h in added} == {"running", "awaiting_approval", "completed"}


def test_mux_client_upload_rejected_with_1008(client) -> None:
    """纯下行收口：mux 流客户端上行消息 → 1008 拒绝关闭。"""
    with client.websocket_connect("/api/events.mux") as ws:
        ws.receive_json()  # 首帧 subscribed（确认已建立）
        ws.send_text("客户端上行：应被 1008 拒绝")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            for _ in range(500):
                ws.receive_json()  # 排空回放缓冲后必命中服务端 close(1008)
        assert exc_info.value.code == 1008


def test_host_client_upload_rejected_with_1008(client) -> None:
    """host 流同样纯下行：客户端上行 → 1008。"""
    with client.websocket_connect("/api/events.host") as ws:
        ws.receive_json()
        ws.send_text("ping")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            for _ in range(500):
                ws.receive_json()
        assert exc_info.value.code == 1008
