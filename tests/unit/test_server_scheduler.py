"""server/scheduler.py 单测 —— 调度入参归一 / 持久化 / cron 解析 / 触发循环。"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

# 关掉 demo ticker 干扰
os.environ.setdefault("SECAI_DEMO_TICK_SECONDS", "3600")

import pytest

from server.scheduler import (
    ScheduleRecord,
    ScheduleStore,
    SchedulerLoop,
    _cron_next,
    normalize_schedule_payload,
)


# ---- 入参归一 ----

def test_immediate_active_now() -> None:
    rec = normalize_schedule_payload({"kind": "immediate", "title": "t", "run": {"allowedTargets": ["10.10.5.2"]}})
    assert rec.kind == "immediate"
    assert rec.status == "active"
    assert rec.next_run_at is not None and rec.next_run_at <= time.time() + 1


def test_datetime_future_only() -> None:
    future = "2099-01-01T00:00:00Z"
    rec = normalize_schedule_payload({"kind": "datetime", "title": "t", "runAt": future, "run": {"allowedTargets": ["x"]}})
    assert rec.kind == "datetime"
    assert rec.run_at and rec.run_at > time.time()
    assert rec.next_run_at == rec.run_at


def test_datetime_in_past_rejected() -> None:
    with pytest.raises(Exception) as ei:
        normalize_schedule_payload({"kind": "datetime", "title": "t", "runAt": "2000-01-01T00:00:00Z"})
    assert "不能在过去" in str(ei.value)


def test_cron_next_within_a_minute() -> None:
    rec = normalize_schedule_payload({"kind": "cron", "cron": "*/1 * * * *", "run": {"allowedTargets": ["x"]}})
    assert rec.kind == "cron" and rec.cron_expr == "*/1 * * * *"
    assert rec.next_run_at and rec.next_run_at - time.time() <= 90


def test_bad_kind_rejected() -> None:
    with pytest.raises(Exception) as ei:
        normalize_schedule_payload({"kind": "yolo"})
    assert "kind 必须是" in str(ei.value)


def test_bad_cron_rejected() -> None:
    with pytest.raises(Exception) as ei:
        normalize_schedule_payload({"kind": "cron", "cron": "*/abc"})
    assert "cron" in str(ei.value).lower()


# ---- cron 解析 ----

def test_cron_parse_minutes_step() -> None:
    nxt = _cron_next("*/5 * * * *", datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert nxt.minute % 5 == 0 and nxt.minute != 0  # 0:00 不命中 */5


def test_cron_parse_list() -> None:
    nxt = _cron_next("0,30 * * * *", datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert nxt.minute == 30


def test_cron_parse_range() -> None:
    nxt = _cron_next("0 9-11 * * *", datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert nxt.hour == 9


# ---- Store CRUD + 重启恢复 ----

def test_store_upsert_and_list(tmp_path) -> None:
    store = ScheduleStore(tmp_path / "sched.db")
    rec = ScheduleRecord(
        id="sch-test-1",
        kind="immediate",
        title="t",
        payload={"allowedTargets": ["x"]},
        status="active",
        next_run_at=time.time(),
    )
    store.upsert(rec)
    assert store.get("sch-test-1").status == "active"
    assert len(store.list_active()) == 1
    # done 后从 list_active 消失
    rec.status = "done"
    store.upsert(rec)
    assert len(store.list_active()) == 0
    # list_all 仍可见
    assert len(store.list_all()) == 1


def test_store_list_due(tmp_path) -> None:
    store = ScheduleStore(tmp_path / "sched.db")
    past = ScheduleRecord(id="sch-past", kind="immediate", title="p", payload={}, status="active", next_run_at=time.time() - 10)
    future = ScheduleRecord(id="sch-future", kind="datetime", title="f", payload={}, status="active", next_run_at=time.time() + 60)
    store.upsert(past)
    store.upsert(future)
    due = store.list_due(now=time.time())
    assert {r.id for r in due} == {"sch-past"}


# ---- 后台循环触发 ----

def test_loop_triggers_due(tmp_path) -> None:
    """注册一条 due 调度 → 触发回调应收到它（异步 1 tick 内）。"""
    store = ScheduleStore(tmp_path / "sched.db")
    rec = ScheduleRecord(id="sch-loop", kind="immediate", title="l", payload={"allowedTargets": ["x"]}, status="active", next_run_at=time.time() - 1)
    store.upsert(rec)

    seen: list[str] = []

    async def on_due(r: ScheduleRecord) -> None:
        seen.append(r.id)

    async def driver() -> None:
        loop = SchedulerLoop(store, on_due=on_due)
        loop._tick_seconds = 0.05  # 加速测试
        loop.start()
        # 跑 0.5s 内等待触发
        for _ in range(20):
            if seen:
                break
            await asyncio.sleep(0.025)
        await loop.stop()
        assert seen == ["sch-loop"]

    asyncio.run(driver())
    # 单测里 on_due 不回写状态（生产环境 _trigger_schedule 负责），仅断言
    # 调度器能在 1 tick 内调用回调；状态回写走 state.py 单测覆盖。
    assert seen == ["sch-loop"]
    # 调度器自身循环 stop 后，rec 仍保持 active（未消费）
    assert store.get("sch-loop").status == "active"