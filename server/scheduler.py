"""任务调度器 —— 后端引擎定时/cron 触发 engagement run。

设计要点：
- 数据模型：ScheduleRecord（id, kind, run_payload 序列化, cron/datetime, status,
  nextRunAt, lastRunAt, createdAt）；status = pending|active|cancelled|done。
- 存储：SQLite（data/agent.db WAL，单库复用 adapters/db.py 模式），scheduler 表
  单进程内 read/write 串行化（写锁 _write_lock）。restart 恢复：load_active() 把
  status=active 且 nextRunAt 在过去/将来的全部读回，按 kind 重新调度。
- 触发：后台循环 SchedulerLoop 挂在 AppState._tick_loop 同源异步任务上，每
  SCHEDULER_TICK_SECONDS（默认 1s）扫描 nextRunAt<=now 的 active 调度；命中
  后调 AppState.trigger_schedule() → 复用 api_run 的真实执行路径（无 LLM Key
  时降级为 fixture demo 会话）。
- 调度器自身挂载：AppState.start_scheduler() / stop_scheduler() 由 lifespan 调
  度；异常隔离循环 try/except 防单点炸裂。
- RPC：/api/scheduleEngagement / listSchedules / cancelSchedule 三个新增入口
  （api.py 注册）。

Cron 解析：标准 5 字段 cron（分 时 日 月 周），仅实现基础语义（*, 数字, 列表,
范围, 步长）。依赖零（无 croniter），可读易测。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

ScheduleKind = Literal["immediate", "datetime", "cron"]
ScheduleStatus = Literal["pending", "active", "cancelled", "done"]

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules(
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    cron_expr TEXT,
    run_at REAL,
    status TEXT NOT NULL,
    next_run_at REAL,
    last_run_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run
    ON schedules(status, next_run_at);
"""

# 调度器 tick 周期：默认 1 秒扫描一次（cron 触发精度足够；CPU 占用可忽略）
SCHEDULER_TICK_SECONDS = float(__import__("os").getenv("SECAI_SCHED_TICK_SECONDS", "1"))


@dataclass
class ScheduleRecord:
    id: str
    kind: ScheduleKind
    title: str
    # run payload 序列化（与 /api/run 一致：title/allowedTargets/targets/scope/
    # settings…）。持久化用 JSON 字符串，运行时 parse 回 dict。
    payload: dict[str, Any] = field(default_factory=dict)
    # cron 表达式（kind=cron 时必填）；datetime 模式不用
    cron_expr: str | None = None
    # ISO 字符串 → epoch 秒（kind=datetime 时必填）；cron 模式运行时计算
    run_at: float | None = None
    status: ScheduleStatus = "pending"
    next_run_at: float | None = None
    last_run_at: float | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheduleId": self.id,
            "kind": self.kind,
            "title": self.title,
            "payload": dict(self.payload),
            "cron": self.cron_expr,
            "runAt": _iso(self.run_at) if self.run_at is not None else None,
            "status": self.status,
            "nextRunAt": _iso(self.next_run_at) if self.next_run_at is not None else None,
            "lastRunAt": _iso(self.last_run_at) if self.last_run_at is not None else None,
            "createdAt": _iso(self.created_at),
            "updatedAt": _iso(self.updated_at),
            "lastError": self.last_error,
        }


class ScheduleStore:
    """SQLite 调度持久层：单进程串行写，读多写少。"""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ---- CRUD ----
    def upsert(self, rec: ScheduleRecord) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO schedules(
                        id, kind, title, payload_json, cron_expr, run_at, status,
                        next_run_at, last_run_at, created_at, updated_at, last_error
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        kind=excluded.kind,
                        title=excluded.title,
                        payload_json=excluded.payload_json,
                        cron_expr=excluded.cron_expr,
                        run_at=excluded.run_at,
                        status=excluded.status,
                        next_run_at=excluded.next_run_at,
                        last_run_at=excluded.last_run_at,
                        updated_at=excluded.updated_at,
                        last_error=excluded.last_error
                    """,
                    (
                        rec.id,
                        rec.kind,
                        rec.title,
                        json.dumps(rec.payload, ensure_ascii=False),
                        rec.cron_expr,
                        rec.run_at,
                        rec.status,
                        rec.next_run_at,
                        rec.last_run_at,
                        rec.created_at,
                        rec.updated_at,
                        rec.last_error,
                    ),
                )
                conn.commit()

    def get(self, schedule_id: str) -> ScheduleRecord | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM schedules WHERE id=?", (schedule_id,)
                ).fetchone()
        return _row_to_rec(row) if row else None

    def list_all(self) -> list[ScheduleRecord]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM schedules ORDER BY created_at DESC"
                ).fetchall()
        return [_row_to_rec(r) for r in rows]

    def list_active(self) -> list[ScheduleRecord]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM schedules WHERE status='active' ORDER BY next_run_at"
                ).fetchall()
        return [_row_to_rec(r) for r in rows]

    def list_due(self, *, now: float, limit: int = 50) -> list[ScheduleRecord]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM schedules WHERE status='active' AND "
                    "(next_run_at IS NULL OR next_run_at <= ?) "
                    "ORDER BY next_run_at LIMIT ?",
                    (now, limit),
                ).fetchall()
        return [_row_to_rec(r) for r in rows]

    def delete(self, schedule_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
                conn.commit()
                return cur.rowcount > 0


# ────────────────────────────────────────────────────────────────────────
# Cron 解析（标准 5 字段：分 时 日 月 周；*, 数字, 列表, 范围, 步长）
# ────────────────────────────────────────────────────────────────────────
def _parse_field(spec: str, lo: int, hi: int) -> set[int]:
    """解析一个 cron 字段（'*/5', '1,3,5', '0-23', '0' 等）为命中值集合。"""
    out: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        step = 1
        if "/" in chunk:
            base, step_s = chunk.split("/", 1)
            step = max(1, int(step_s))
        else:
            base = chunk
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            value = int(base)
            start = end = value
        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                out.add(v)
    return out


def _cron_next(cron: str, base: datetime) -> datetime:
    """下一次命中时间（base 之后的最早命中；base 必须为带 tz 的 UTC datetime）。"""
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError(f"cron 必须是 5 字段，当前：{cron!r}")
    minutes = _parse_field(parts[0], 0, 59)
    hours = _parse_field(parts[1], 0, 23)
    days = _parse_field(parts[2], 1, 31)
    months = _parse_field(parts[3], 1, 12)
    # 周字段：cron 0=周日，Python weekday() 0=周一 — 转换
    weekdays_raw = _parse_field(parts[4], 0, 6)
    weekdays = {(d - 1) % 7 for d in weekdays_raw}
    # 从 base + 1 分钟开始扫描；上限 366 天（约 5e5 分钟）
    cur = base.replace(second=0, microsecond=0)
    end = cur.timestamp() + 366 * 86400
    while cur.timestamp() < end:
        cur = cur.fromtimestamp(cur.timestamp() + 60, tz=timezone.utc)
        if (cur.month not in months) or (cur.day not in days) or (cur.weekday() not in weekdays):
            continue
        if cur.minute not in minutes or cur.hour not in hours:
            continue
        return cur
    raise ValueError(f"cron 在 366 天内无可用命中：{cron!r}")


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _row_to_rec(row: sqlite3.Row) -> ScheduleRecord:
    payload = json.loads(row["payload_json"] or "{}")
    return ScheduleRecord(
        id=row["id"],
        kind=row["kind"],
        title=row["title"],
        payload=payload,
        cron_expr=row["cron_expr"],
        run_at=row["run_at"],
        status=row["status"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_error=row["last_error"],
    )


# ────────────────────────────────────────────────────────────────────────
# 调度器主循环
# ────────────────────────────────────────────────────────────────────────
class SchedulerLoop:
    """挂 AppState 上的后台循环：扫描 due 调度 → 触发 AppState.trigger_schedule。"""

    def __init__(self, store: ScheduleStore, *, on_due) -> None:
        self.store = store
        self._on_due = on_due  # 协程：async def(rec: ScheduleRecord) -> None
        self._task: asyncio.Task | None = None
        self._stop = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = False
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
        self._task = None

    async def _loop(self) -> None:
        while not self._stop:
            try:
                await self._tick_once()
            except Exception as exc:  # 异常隔离：单点炸裂不拖垮主循环
                print(f"[scheduler] tick 异常（隔离）: {type(exc).__name__}: {exc}")
            await asyncio.sleep(SCHEDULER_TICK_SECONDS)

    async def _tick_once(self) -> None:
        now = time.time()
        for rec in self.store.list_due(now=now):
            try:
                await self._on_due(rec)
            except Exception as exc:
                rec.last_error = f"{type(exc).__name__}: {exc}"
                rec.updated_at = time.time()
                self.store.upsert(rec)


# ────────────────────────────────────────────────────────────────────────
# 入参解析
# ────────────────────────────────────────────────────────────────────────
class ScheduleSpecError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "bad_request"
        self.message = message


def normalize_schedule_payload(payload: dict[str, Any]) -> ScheduleRecord:
    """归一入参 → ScheduleRecord；kind 决定 cron_expr / run_at 必填校验。

    payload 字段：kind, title, run(payload: 与 /api/run 一致), cron?, runAt?
    """
    if not isinstance(payload, dict):
        raise ScheduleSpecError("payload 必须是对象")
    kind = str(payload.get("kind") or "").strip()
    if kind not in ("immediate", "datetime", "cron"):
        raise ScheduleSpecError("kind 必须是 immediate | datetime | cron")
    title = str(payload.get("title") or "").strip() or "调度任务（未命名）"
    raw_run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    if not raw_run:
        # 兼容：直接平铺 run payload 字段
        raw_run = {
            k: v
            for k, v in payload.items()
            if k not in ("kind", "title", "cron", "runAt")
        }

    now = time.time()
    cron_expr: str | None = None
    run_at: float | None = None
    next_run: float | None = None

    if kind == "immediate":
        # 即时：next_run_at = now（一次性触发），持久化时 status 直接 active
        next_run = now
    elif kind == "datetime":
        iso = str(payload.get("runAt") or "").strip()
        if not iso:
            raise ScheduleSpecError("datetime 模式必须提供 runAt（ISO 时间）")
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ScheduleSpecError(f"runAt 无法解析为 ISO 时间：{exc}") from None
        run_at = dt.timestamp()
        if run_at < now - 1:
            raise ScheduleSpecError("runAt 不能在过去")
        next_run = run_at
    else:  # cron
        cron_expr = str(payload.get("cron") or "").strip()
        if not cron_expr:
            raise ScheduleSpecError("cron 模式必须提供 cron 表达式")
        try:
            nxt = _cron_next(cron_expr, datetime.fromtimestamp(now, tz=timezone.utc))
            next_run = nxt.timestamp()
        except ValueError as exc:
            raise ScheduleSpecError(str(exc))

    return ScheduleRecord(
        id=f"sch-{uuid.uuid4().hex[:10]}",
        kind=kind,  # type: ignore[arg-type]
        title=title,
        payload=dict(raw_run),
        cron_expr=cron_expr,
        run_at=run_at,
        status="active",
        next_run_at=next_run,
        created_at=now,
        updated_at=now,
    )


__all__ = [
    "DB_PATH",
    "SCHEDULER_TICK_SECONDS",
    "ScheduleRecord",
    "ScheduleSpecError",
    "ScheduleStore",
    "SchedulerLoop",
    "normalize_schedule_payload",
]