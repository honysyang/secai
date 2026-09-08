"""scheduler RPC 单测 —— scheduleEngagement/listSchedules/cancelSchedule 三个入口 + state 触发回路。"""
from __future__ import annotations

import asyncio
import os
import time

# 关掉 demo ticker + 调度循环干扰单测
os.environ.setdefault("SECAI_DEMO_TICK_SECONDS", "3600")
os.environ.setdefault("SECAI_SCHED_TICK_SECONDS", "3600")

import pytest
from starlette.testclient import TestClient

from server.fixture import DEMO_ENGAGEMENT_ID
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


def test_schedule_immediate_runs_synchronously(app_client) -> None:
    """immediate 调度在 listSchedules 时已 active；触发回调在单测外跑。"""
    state, client = app_client
    res = _post(client, "scheduleEngagement", {
        "kind": "immediate", "title": "立即",
        "run": {"allowedTargets": ["10.10.5.2"]},
    })
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    rec = body["result"]["schedule"]
    assert rec["kind"] == "immediate"
    assert rec["status"] == "active"
    assert rec["scheduleId"].startswith("sch-")


def test_schedule_cron_computes_next_run(app_client) -> None:
    state, client = app_client
    res = _post(client, "scheduleEngagement", {
        "kind": "cron", "title": "cron", "cron": "*/1 * * * *",
        "run": {"allowedTargets": ["10.10.5.2"]},
    })
    body = res.json()
    assert body["ok"] is True
    rec = body["result"]["schedule"]
    assert rec["cron"] == "*/1 * * * *"
    assert rec["status"] == "active"
    assert rec["nextRunAt"] is not None


def test_schedule_datetime_in_past_bad_request(app_client) -> None:
    state, client = app_client
    res = _post(client, "scheduleEngagement", {
        "kind": "datetime", "title": "x", "runAt": "2000-01-01T00:00:00Z",
        "run": {"allowedTargets": ["x"]},
    })
    body = res.json()
    assert body["ok"] is False and body["error"]["code"] == "bad_request"


def test_schedule_bad_cron_bad_request(app_client) -> None:
    state, client = app_client
    res = _post(client, "scheduleEngagement", {
        "kind": "cron", "title": "x", "cron": "*/abc",
        "run": {"allowedTargets": ["x"]},
    })
    body = res.json()
    assert body["ok"] is False and body["error"]["code"] == "bad_request"


def test_list_schedules_returns_all(app_client) -> None:
    state, client = app_client
    _post(client, "scheduleEngagement", {"kind": "immediate", "title": "a", "run": {"allowedTargets": ["x"]}})
    _post(client, "scheduleEngagement", {"kind": "cron", "title": "b", "cron": "*/1 * * * *", "run": {"allowedTargets": ["x"]}})
    res = _post(client, "listSchedules", {})
    body = res.json()
    assert body["ok"] is True
    assert len(body["result"]["schedules"]) >= 2


def test_cancel_schedule_marks_cancelled(app_client) -> None:
    state, client = app_client
    created = _post(client, "scheduleEngagement", {
        "kind": "cron", "title": "c", "cron": "*/5 * * * *",
        "run": {"allowedTargets": ["x"]},
    }).json()
    sid = created["result"]["schedule"]["scheduleId"]
    res = _post(client, "cancelSchedule", {"scheduleId": sid})
    body = res.json()
    assert body["ok"] is True
    assert body["result"]["schedule"]["status"] == "cancelled"
    assert body["result"]["schedule"]["nextRunAt"] is None


def test_cancel_unknown_schedule_not_found(app_client) -> None:
    state, client = app_client
    res = _post(client, "cancelSchedule", {"scheduleId": "sch-nope"})
    body = res.json()
    assert body["ok"] is False and body["error"]["code"] == "not_found"


def test_cancel_missing_id_bad_request(app_client) -> None:
    state, client = app_client
    res = _post(client, "cancelSchedule", {})
    body = res.json()
    assert body["ok"] is False and body["error"]["code"] == "bad_request"


def test_trigger_schedule_immediate_marks_done_and_creates_engagement(app_client, monkeypatch) -> None:
    """直接调 state._trigger_schedule：immediate 触发后 status=done + 独立任务书创建。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state, client = app_client
    rec = state.schedule_store.get(
        _post(client, "scheduleEngagement", {
            "kind": "immediate", "title": "trigger",
            "run": {"allowedTargets": ["10.10.5.2"]},
        }).json()["result"]["schedule"]["scheduleId"]
    )
    assert rec is not None
    asyncio.run(state._trigger_schedule(rec))
    final = state.schedule_store.get(rec.id)
    assert final.status == "done"
    assert final.last_run_at is not None
    assert final.next_run_at is None
    # 降级路径应创建 sched-{id} 任务书
    assert f"sched-{rec.id}" in state.engagements


def test_trigger_schedule_cron_advances_next_run(app_client, monkeypatch) -> None:
    """cron 触发后 status 保持 active 且 next_run_at 推进。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state, client = app_client
    rec = state.schedule_store.get(
        _post(client, "scheduleEngagement", {
            "kind": "cron", "title": "adv", "cron": "*/1 * * * *",
            "run": {"allowedTargets": ["10.10.5.2"]},
        }).json()["result"]["schedule"]["scheduleId"]
    )
    first_next = rec.next_run_at
    asyncio.run(state._trigger_schedule(rec))
    final = state.schedule_store.get(rec.id)
    assert final.status == "active"
    assert final.last_run_at is not None
    assert final.next_run_at is not None
    assert final.next_run_at >= first_next  # 推进或不变（不会回退）