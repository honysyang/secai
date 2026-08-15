"""SQLite 持久化层：data/agent.db（WAL），供后续分析/复盘/Web 监控。

线程安全方案：每线程一个连接（threading.local）+ 写操作全局写锁串行化。
默认实例由入口（CLI）通过 init_default() 初始化；未初始化时 get_db() 返回 None，
各接入点静默跳过（测试隔离）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

DB_PATH = Path(__file__).parent.parent / "data" / "agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
    id TEXT PRIMARY KEY,
    task TEXT,
    status TEXT,
    started_at REAL,
    finished_at REAL,
    answer TEXT
);
CREATE TABLE IF NOT EXISTS events(
    task_id TEXT,
    seq INTEGER,
    kind TEXT,
    ts REAL,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task_seq ON events(task_id, seq);
"""


class Database:
    """每线程连接 + 写锁；WAL 模式允许读写并发。"""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        conn = self._connect()
        conn.executescript(SCHEMA)
        conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._write_lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    # ---- tasks ----
    def task_started(self, task_id: str, task: str) -> None:
        self.execute(
            "INSERT OR REPLACE INTO tasks(id, task, status, started_at,"
            " finished_at, answer) VALUES(?,?,?,?,NULL,NULL)",
            (task_id, task, "running", time.time()))

    def task_finished(self, task_id: str, status: str,
                      answer: str | None = None) -> None:
        self.execute(
            "UPDATE tasks SET status=?, finished_at=?, answer=? WHERE id=?",
            (status, time.time(), answer, task_id))

    def list_tasks(self) -> list[dict]:
        return [dict(r) for r in
                self.query("SELECT * FROM tasks ORDER BY started_at DESC")]

    # ---- events ----
    def insert_event(self, event: dict) -> None:
        self.execute(
            "INSERT INTO events(task_id, seq, kind, ts, data) VALUES(?,?,?,?,?)",
            (event["task_id"], event["seq"], event["kind"], event["ts"],
             json.dumps(event["data"], ensure_ascii=False)))

    def get_events(self, task_id: str) -> list[dict]:
        return [
            {"seq": r["seq"], "ts": r["ts"], "task_id": r["task_id"],
             "kind": r["kind"], "data": json.loads(r["data"])}
            for r in self.query(
                "SELECT * FROM events WHERE task_id=? ORDER BY seq", (task_id,))
        ]

    def get_events_after(self, task_id: str, seq: int) -> list[dict]:
        """返回指定序号之后的事件，供 SSE 低成本增量追赶。"""
        return [
            {"seq": r["seq"], "ts": r["ts"], "task_id": r["task_id"],
             "kind": r["kind"], "data": json.loads(r["data"])}
            for r in self.query(
                "SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq",
                (task_id, seq))
        ]


# ---- 默认实例（入口初始化；未初始化时接入点静默跳过）----
_default: Database | None = None


def init_default(path: str | Path = DB_PATH) -> Database:
    global _default
    _default = Database(path)
    return _default


def get_db() -> Database | None:
    return _default


def db_subscriber(db: Database | None = None) -> Callable[[dict], None]:
    """事件 → events 表的订阅者（与 events.jsonl 文件留痕双写）。"""

    def _write(event: dict) -> None:
        d = db or get_db()
        if d is not None:
            d.insert_event(event)

    return _write
