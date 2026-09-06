"""server/auth.py 单测 —— API Key 鉴权中间件（产品化商业化地基）。

验证（仿 test_server_api.py 的 starlette TestClient 风格）：
- 未启用鉴权（SECAI_API_KEYS 未设置/空）→ 完全向后兼容：describe/run 等仍 200、
  WebSocket 照常建立、静态资源不受影响；
- 启用鉴权（SECAI_API_KEYS="k1,k2"）：
  - 无 X-API-Key / 错误 key → HTTP 401 + {ok:false,error.code=unauthorized}；
  - 正确 key（k1 / k2）→ 200（describe 握手身份可通）；
  - 非 /api/ 路径（SPA GET /）→ 不被 401；
  - WebSocket /api/events.mux 无 key → 握手被拒（4401）；
  - WebSocket 携带正确 key → 正常建立（首帧 session/subscribed）；
- 每个用例独立 create_app() + monkeypatch 控制环境变量，避免单例泄漏。

运行：.venv/bin/python -m pytest tests/unit/test_server_auth.py -q
"""
from __future__ import annotations

import os

# 关掉 demo ticker 干扰（在 import server.* 前生效）
os.environ.setdefault("SECAI_DEMO_TICK_SECONDS", "3600")

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.auth import load_api_keys
from server.fixture import SESSION_A, SESSION_B, SESSION_C
from server.main import create_app


def _post(client: TestClient, method: str, payload: dict) -> object:
    return client.post(
        f"/api/{method}", json={"rpcId": "rpc-test", "method": method, "payload": payload}
    )


# ---------------------------------------------------------------------------
# load_api_keys：解析 SECAI_API_KEYS
# ---------------------------------------------------------------------------
def test_load_api_keys_parses_and_normalizes(monkeypatch) -> None:
    monkeypatch.setenv("SECAI_API_KEYS", " k1 ,k2,,  k2 ,  ,k3 ")
    assert load_api_keys() == {"k1", "k2", "k3"}


def test_load_api_keys_unset_or_empty_means_disabled(monkeypatch) -> None:
    monkeypatch.delenv("SECAI_API_KEYS", raising=False)
    assert load_api_keys() == set()
    monkeypatch.setenv("SECAI_API_KEYS", "")
    assert load_api_keys() == set()
    monkeypatch.setenv("SECAI_API_KEYS", "   ,  ")
    assert load_api_keys() == set()


# ---------------------------------------------------------------------------
# 未启用鉴权：完全向后兼容
# ---------------------------------------------------------------------------
def test_disabled_describe_and_run_unaffected(monkeypatch) -> None:
    """无 SECAI_API_KEYS → 无 X-API-Key 的 describe/run 仍 200（向后兼容）。"""
    monkeypatch.delenv("SECAI_API_KEYS", raising=False)
    with TestClient(create_app()) as client:
        res = _post(client, "describe", {})
        assert res.status_code == 200
        assert res.json()["ok"] is True
        # 业务错误仍恒 200（run 无 LLM Key 的既有约定不被鉴权层破坏）
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        body = _post(client, "run", {"allowedTargets": ["10.10.5.2"]}).json()
        assert body["ok"] is False and body["error"]["code"] == "llm_key_missing"


def test_disabled_websocket_connects_without_key(monkeypatch) -> None:
    """鉴权关闭 → /api/events.mux 无 key 照常建立（首帧 session/subscribed）。"""
    monkeypatch.delenv("SECAI_API_KEYS", raising=False)
    with TestClient(create_app()) as client:
        with client.websocket_connect("/api/events.mux") as ws:
            first = ws.receive_json()
            assert first["type"] == "session/subscribed"


# ---------------------------------------------------------------------------
# 启用鉴权：HTTP 401 / WS 4401 / 静态资源放行
# ---------------------------------------------------------------------------
def test_enabled_missing_key_http_401(monkeypatch) -> None:
    monkeypatch.setenv("SECAI_API_KEYS", "k1,k2")
    with TestClient(create_app()) as client:
        res = _post(client, "describe", {})
        assert res.status_code == 401
        body = res.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "unauthorized"
        assert body["error"]["message"] == "missing or invalid X-API-Key header"


def test_enabled_wrong_key_http_401(monkeypatch) -> None:
    monkeypatch.setenv("SECAI_API_KEYS", "k1,k2")
    with TestClient(create_app()) as client:
        for bad in ("nope", "K1"):
            res = client.post(
                "/api/describe",
                json={"rpcId": "rpc-test", "method": "describe", "payload": {}},
                headers={"X-API-Key": bad},
            )
            assert res.status_code == 401
            assert res.json()["error"]["code"] == "unauthorized"


def test_enabled_valid_key_describe_200(monkeypatch) -> None:
    """k1 / k2 任一命中 → 200 + 完整 HostDescription（鉴权全 /api/*，含 describe）。"""
    monkeypatch.setenv("SECAI_API_KEYS", "k1,k2")
    with TestClient(create_app()) as client:
        for key in ("k1", "k2"):
            res = client.post(
                "/api/describe",
                json={"rpcId": "rpc-test", "method": "describe", "payload": {}},
                headers={"X-API-Key": key},
            )
            assert res.status_code == 200
            result = res.json()["result"]
            assert result["product"] == "SECAI-PT"
            assert result["serverTime"].endswith("Z")


def test_enabled_static_resource_not_protected(monkeypatch) -> None:
    """非 /api/ 路径（SPA GET /）不被 401，照常 200。"""
    monkeypatch.setenv("SECAI_API_KEYS", "k1,k2")
    with TestClient(create_app()) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert "SECAI-PT" in res.text


def test_enabled_websocket_missing_key_handshake_rejected(monkeypatch) -> None:
    """mux 流无 key → 握手被拒（4401），未被 accept。"""
    monkeypatch.setenv("SECAI_API_KEYS", "k1,k2")
    with TestClient(create_app()) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/events.mux"):
                pass  # pragma: no cover - 预期根本进不来
        assert exc_info.value.code == 4401


def test_enabled_websocket_wrong_key_handshake_rejected(monkeypatch) -> None:
    monkeypatch.setenv("SECAI_API_KEYS", "k1,k2")
    with TestClient(create_app()) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/api/events.mux", headers={"X-API-Key": "nope"}
            ):
                pass  # pragma: no cover - 预期根本进不来
        assert exc_info.value.code == 4401


def test_enabled_websocket_valid_key_connects(monkeypatch) -> None:
    """mux 流携带正确 key → 正常建立，首帧 session/subscribed。"""
    monkeypatch.setenv("SECAI_API_KEYS", "k1,k2")
    with TestClient(create_app()) as client:
        with client.websocket_connect(
            "/api/events.mux", headers={"X-API-Key": "k1"}
        ) as ws:
            first = ws.receive_json()
            assert first["type"] == "session/subscribed"
            assert first["sessionId"] in (SESSION_A, SESSION_B, SESSION_C)
