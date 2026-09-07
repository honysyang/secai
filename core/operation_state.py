"""core/operation_state.py —— 目标契约四台账（对齐 dsh-stage-gate operation-state）。

goal（任务目标）/ criteria（完成准则）/ intents（未收口意图）/ constraints（约束）
四台账落 operation-state.json，驱动：
- sec-enforce 的未收口拦截（报告前 criteria/intents 必须清空）；
- route-boost 信封的恢复盘（中断后从 operation-state.json 重建现场）；
- 报告门对账（P3 PASS + operation 清零才允许写报告）。

设计对齐：SECAI 的 ScopeConstraint ≈ constraints；criteria/intents 为本模块新建。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OperationState:
    """目标契约：四台账 + 元信息。"""
    goal: str = ""
    criteria: list[dict[str, Any]] = field(default_factory=list)
    intents: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "criteria": self.criteria,
            "intents": self.intents,
            "constraints": self.constraints,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationState:
        return cls(
            goal=str(data.get("goal") or ""),
            criteria=list(data.get("criteria") or []),
            intents=list(data.get("intents") or []),
            constraints=list(data.get("constraints") or []),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


def load_operation_state(workspace: str | Path) -> OperationState:
    """从 operation-state.json 加载（不存在 → 空状态）。"""
    path = Path(workspace) / "operation-state.json"
    if not path.is_file():
        return OperationState()
    try:
        return OperationState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return OperationState()


def save_operation_state(workspace: str | Path, state: OperationState) -> Path:
    """落盘 operation-state.json（目录惰性创建）。"""
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    path = ws / "operation-state.json"
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def operation_goal(workspace: str | Path, goal: str) -> dict[str, Any]:
    """登记/更新任务目标（一次性覆盖）。"""
    state = load_operation_state(workspace)
    state.goal = (goal or "").strip()
    save_operation_state(workspace, state)
    return {"ok": True, "goal": state.goal}


def operation_criteria(workspace: str | Path, criteria: list[str]) -> dict[str, Any]:
    """登记完成准则（覆盖式；每条 {text, done}）。"""
    state = load_operation_state(workspace)
    state.criteria = [{"text": c, "done": False} for c in (criteria or [])]
    save_operation_state(workspace, state)
    return {"ok": True, "criteria": state.criteria}


def operation_criterion_done(workspace: str | Path, index: int) -> dict[str, Any]:
    """标记某条准则已收口（0-based index）。"""
    state = load_operation_state(workspace)
    if 0 <= index < len(state.criteria):
        state.criteria[index]["done"] = True
        save_operation_state(workspace, state)
    return {"ok": True, "criteria": state.criteria}


def operation_intent(workspace: str | Path, text: str) -> dict[str, Any]:
    """登记未收口意图（追加式）。"""
    state = load_operation_state(workspace)
    if text and text not in [i.get("text") for i in state.intents]:
        state.intents.append({"text": text, "ts": time.time()})
        save_operation_state(workspace, state)
    return {"ok": True, "intents": state.intents}


def operation_intent_done(workspace: str | Path, text: str) -> dict[str, Any]:
    """移除已收口意图（按 text 匹配）。"""
    state = load_operation_state(workspace)
    state.intents = [i for i in state.intents if i.get("text") != text]
    save_operation_state(workspace, state)
    return {"ok": True, "intents": state.intents}


def operation_constraints(workspace: str | Path, constraints: list[str]) -> dict[str, Any]:
    """登记约束（覆盖式；SECAI ScopeConstraint 的约束文本化）。"""
    state = load_operation_state(workspace)
    state.constraints = list(constraints or [])
    save_operation_state(workspace, state)
    return {"ok": True, "constraints": state.constraints}


def operation_progress(workspace: str | Path) -> dict[str, Any]:
    """进度收口查询：goal + 准则完成率 + 未收口意图数（route-boost 信封数据源）。"""
    state = load_operation_state(workspace)
    total = len(state.criteria)
    done = sum(1 for c in state.criteria if c.get("done"))
    return {
        "goal": state.goal,
        "criteria_total": total,
        "criteria_done": done,
        "criteria_open": total - done,
        "intents_open": len(state.intents),
        "constraints": state.constraints,
        "all_clear": (total > 0 and done == total and not state.intents),
    }


__all__ = [
    "OperationState",
    "load_operation_state", "save_operation_state",
    "operation_goal", "operation_criteria", "operation_criterion_done",
    "operation_intent", "operation_intent_done",
    "operation_constraints", "operation_progress",
]
