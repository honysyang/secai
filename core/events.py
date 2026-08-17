"""进程级事件总线：Agent 运行事件的结构化发射、内存历史保存（SSE 重放）与订阅者机制。

事件格式：{"seq": int, "ts": float, "task_id": str, "kind": str, "data": dict}
- task_id：题目 unique_code（或 "generic" / "sub_<id>"），用于区分不同题/子任务的事件流
- data：原始事件负载（含 agent 智能体名、tool、text、usage 等，由调用方决定）

transcript 落盘（events.jsonl）仍由 hooks.py 完成（保留现有文件留痕），本模块只负责
内存历史 + 订阅者分发（SQLite 落库等）。Agent 只发射事件，不写两套。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable


def format_events_tail(events: list[dict], max_chars: int = 4000) -> str:
    """把事件列表格式化为可读文本摘要，优先保留最近事件。"""
    if not events:
        return ""
    lines = []
    total = 0
    for ev in reversed(events):
        kind = ev.get("kind", "unknown")
        ts = ev.get("ts", 0)
        data = ev.get("data", {})
        line = f"[{ts:.1f}] {kind}: {json.dumps(data, ensure_ascii=False)}"
        if total + len(line) + 1 > max_chars and lines:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(reversed(lines))


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: dict[str, list[dict]] = {}
        self._seq: dict[str, int] = {}
        self._subscribers: list[Callable[[dict], None]] = []

    def emit(self, task_id: str, kind: str, **data) -> dict:
        """发射一条事件，返回完整事件（含自增 seq）。订阅者在锁外调用，异常不影响发射方。"""
        with self._lock:
            seq = self._seq.get(task_id, 0) + 1
            self._seq[task_id] = seq
            event = {"seq": seq, "ts": time.time(), "task_id": task_id,
                     "kind": kind, "data": data}
            self._history.setdefault(task_id, []).append(event)
            subs = list(self._subscribers)
        for fn in subs:
            try:
                fn(event)
            except Exception:
                pass
        return event

    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """注册订阅者，返回取消订阅函数。"""
        with self._lock:
            self._subscribers.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subscribers:
                    self._subscribers.remove(fn)

        return unsubscribe

    def history(self, task_id: str) -> list[dict]:
        """返回某 task 的完整事件历史（副本）。"""
        with self._lock:
            return list(self._history.get(task_id, []))


# 进程级总线：CLI 与未来 Web 服务共用
BUS = EventBus()
