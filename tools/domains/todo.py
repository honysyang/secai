"""待办清单域：执行者自我管理多面 flag / 多步骤攻击链的进度。

工具域模块：按功能域划分的工具实现。
- todo_add / todo_list / todo_mark + 优先级/状态枚举校验
"""
from __future__ import annotations

import json
import time
import uuid

from agents import RunContextWrapper, function_tool

from core.task_context import TaskContext

_VALID_TODO_PRIORITY = {"low", "normal", "high", "critical"}
_VALID_TODO_STATUS = {"pending", "in_progress", "done"}


@function_tool
def todo_add(ctx: RunContextWrapper[TaskContext], todos: str) -> str:
    """添加待办事项（可批量），用于跟踪多阶段任务 / 多步骤攻击链的进度。

    todos 传 JSON 数组，每项 ``{"title": "...", "priority": "low|normal|high|critical"}``，
    priority 默认 normal。单个待办也传一个元素的数组。
    例：todo_add(todos='[{"title":"读配置文件","priority":"high"},{"title":"测 SQLi"}]')
    """
    c = ctx.context
    try:
        data = json.loads(todos) if isinstance(todos, str) else todos
    except json.JSONDecodeError:
        return json.dumps({"error": "todos 必须是 JSON 数组"}, ensure_ascii=False)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return json.dumps({"error": "todos 必须是 JSON 数组"}, ensure_ascii=False)
    created = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        prio = str(item.get("priority", "normal")).lower()
        if prio not in _VALID_TODO_PRIORITY:
            prio = "normal"
        tid = uuid.uuid4().hex[:6]
        c.todos.append({"id": tid, "title": title, "status": "pending",
                        "priority": prio, "created_at": int(time.time()), "done_at": None})
        created.append({"id": tid, "title": title, "priority": prio})
    return json.dumps({"ok": True, "created": created, "total": len(c.todos)},
                      ensure_ascii=False)


@function_tool
def todo_list(ctx: RunContextWrapper[TaskContext]) -> str:
    """列出当前待办清单，按状态（pending/in_progress 在前，done 在后）与优先级排序。"""
    c = ctx.context
    status_order = {"pending": 0, "in_progress": 1, "done": 2}
    prio_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    items = sorted(c.todos, key=lambda t: (
        status_order.get(t.get("status"), 99),
        prio_order.get(t.get("priority"), 99)))
    summary = {"pending": 0, "in_progress": 0, "done": 0}
    for t in c.todos:
        s = t.get("status", "pending")
        summary[s] = summary.get(s, 0) + 1
    return json.dumps({"todos": items, "total": len(c.todos), "summary": summary},
                      ensure_ascii=False)


@function_tool
def todo_mark(ctx: RunContextWrapper[TaskContext], todo_ids: str, status: str) -> str:
    """标记待办状态。todo_ids 传 JSON 数组（单个也传数组），status 取 pending/in_progress/done。"""
    c = ctx.context
    status = (status or "").strip().lower()
    if status not in _VALID_TODO_STATUS:
        return json.dumps({"error": f"status 必须是 {sorted(_VALID_TODO_STATUS)} 之一"},
                          ensure_ascii=False)
    try:
        ids = json.loads(todo_ids) if isinstance(todo_ids, str) else todo_ids
    except json.JSONDecodeError:
        return json.dumps({"error": "todo_ids 必须是 JSON 数组"}, ensure_ascii=False)
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list):
        return json.dumps({"error": "todo_ids 必须是 JSON 数组"}, ensure_ascii=False)
    marked = []
    for raw_id in ids:
        tid = str(raw_id).strip()
        for t in c.todos:
            if t.get("id") == tid:
                t["status"] = status
                t["done_at"] = int(time.time()) if status == "done" else None
                marked.append(tid)
                break
    return json.dumps({"ok": True, "marked": marked, "status": status}, ensure_ascii=False)
