"""pentest/approval.py 单测 —— R3 验收：T3 无审批暂停 / 拒绝结构化返回 / 记录入总线 & events 表。"""

import json

import pytest

from core.events import EventBus
from pentest.approval import (
    DEFAULT_TOOL_TIERS,
    ApprovalGate,
    ApprovalRecord,
    ApprovalRequestResult,
)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()  # 独立总线，避免污染全局 BUS


@pytest.fixture
def gate(bus: EventBus) -> ApprovalGate:
    return ApprovalGate(bus=bus)


def test_t3_default_wordlist_contains_shell() -> None:
    assert DEFAULT_TOOL_TIERS["shell"] == "T3"
    assert DEFAULT_TOOL_TIERS["run_batch"] == "T3"


def test_t1_auto_allowed_without_records(bus: EventBus, gate: ApprovalGate) -> None:
    r = gate.request("s1", "http_request", {"url": "http://10.0.0.5/"})
    assert r.ok and r.status == "auto_allowed"
    assert gate.tier_of("http_request") == "T1"
    assert bus.history("s1") == []  # T1 不产生审批记录


def test_t3_no_approval_pauses(gate: ApprovalGate, bus: EventBus) -> None:
    """R3 验收：T3 工具无审批 → 暂停，Agent 收到结构化返回。"""
    r = gate.request("s1", "shell", {"command": "nmap -sV 10.0.0.5"})
    assert isinstance(r, ApprovalRequestResult)
    assert not r.ok
    assert r.status == "pending"
    assert r.tier == "T3"
    assert r.request_id
    # 结构化消息（Agent 可解析）
    msg = json.loads(r.as_message())
    assert msg["ok"] is False and msg["approval_status"] == "pending"
    assert msg["request_id"] == r.request_id
    # 记录入事件总线（approval/requested 事件含 ApprovalRecord）
    events = bus.history("s1")
    assert len(events) == 1
    assert events[0]["kind"] == "approval/requested"
    assert events[0]["data"]["tool"] == "shell"
    assert events[0]["data"]["status"] == "pending"


def test_grant_then_same_signature_allowed(gate: ApprovalGate, bus: EventBus) -> None:
    """人工 grant → 同签名重试放行（granted 缓存）。"""
    args = {"command": "whoami"}
    r1 = gate.request("s1", "shell", args)
    assert r1.status == "pending"
    rec = gate.grant(r1.request_id, "grant", approver="admin", note="目标机身份确认")
    assert isinstance(rec, ApprovalRecord)
    assert rec.status == "granted" and rec.approver == "admin"

    r2 = gate.request("s1", "shell", args)
    assert r2.ok and r2.status == "granted_cached"
    kinds = [e["kind"] for e in bus.history("s1")]
    assert kinds.count("approval/requested") == 1  # 缓存命中不再重发 requested
    assert kinds.count("approval/resolved") == 2  # 人工裁决 + 缓存放行各一条 resolved


def test_deny_structured_and_cached(gate: ApprovalGate, bus: EventBus) -> None:
    """R3 验收：人工拒绝 → 结构化拒绝；同签名后续请求直接拒绝（deny 缓存留痕）。"""
    args = {"command": "hydra -l admin 10.0.0.5"}
    r1 = gate.request("s1", "shell", args)
    gate.grant(r1.request_id, "deny", approver="admin", note="爆破不允许")
    r2 = gate.request("s1", "shell", args)
    assert not r2.ok
    assert r2.status == "denied"
    assert "拒绝" in r2.reason
    resolved = [e for e in bus.history("s1") if e["kind"] == "approval/resolved"]
    assert resolved[-1]["data"]["decision"] == "deny"


def test_grant_unknown_and_double_grant_raise(gate: ApprovalGate) -> None:
    with pytest.raises(ValueError):
        gate.grant("no-such-id", "grant")
    r = gate.request("s1", "shell", {"command": "whoami"})
    gate.grant(r.request_id, "grant")
    with pytest.raises(ValueError):
        gate.grant(r.request_id, "deny")  # 已裁决不可重复


def test_t4_forbidden(gate: ApprovalGate) -> None:
    r = gate.request("s1", "shell", {"command": "x"}, tier="T4")
    assert not r.ok and r.status == "forbidden"


def test_tier_override_via_constructor() -> None:
    g = ApprovalGate(tiers={"custom_tool": "T2"})
    assert g.tier_of("custom_tool") == "T2"
    assert g.tier_of("unknown_tool") == "T1"


def test_pending_count(gate: ApprovalGate) -> None:
    gate.request("s1", "shell", {"command": "a"})
    gate.request("s1", "shell", {"command": "b"})
    assert gate.pending_count() == 2
    # find_record 能查到第一条 pending 记录
    first = next(iter(gate._records))  # noqa: SLF001 - 单测直接观察内部表
    assert gate.find_record(first) is not None


def test_records_persist_into_events_table(tmp_path) -> None:
    """R3 验收：审批记录可在 events 表查到（经 BlackboardStore.record_event 落库）。"""
    from pentest.blackboard.store import BlackboardStore

    store = BlackboardStore(tmp_path / "pentest.db")
    bus = EventBus()
    gate = ApprovalGate(bus=bus, store=store)
    r = gate.request("eng-1", "shell", {"command": "sqlmap -u http://10.0.0.5/?id=1"})
    gate.grant(r.request_id, "grant", approver="admin", note="授权目标允许")

    rows = store.query_events(session_id="eng-1", type_="approval/requested")
    assert len(rows) == 1
    assert rows[0]["data"]["tool"] == "shell"
    assert rows[0]["data"]["tier"] == "T3"
    resolved = store.query_events(session_id="eng-1", type_="approval/resolved")
    assert len(resolved) == 1
    assert resolved[0]["data"]["decision"] == "grant"
