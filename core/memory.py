"""三层记忆统一访问接口：短期 session / 中期黑板 / 长期档案。

设计要点（对齐《优秀 Harness 落地指南》第 2 章）：
- 短期（ShortTermMemory）：当前会话消息，只追加不修改历史，由 SQLiteSession 承载；
- 中期（Blackboard）：已验证事实、flag、死路、hint 法令、next_directive，只由代码写入；
- 长期（FieldNotes）：跨题经验、payload 模板，落盘 field_notes.md。

本模块提供统一访问入口；TaskContext 仍保留原有字段（blackboard/notes/...）以保证
与现有代码（demo_tools / hooks / context_manager / main）向后兼容，MemoryManager
只是把这些字段的读写收敛到同一层，避免各处散落重复逻辑。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from core.task_context import TaskContext


def render_blackboard_snapshot(ctx: TaskContext, max_value_chars: int = 200) -> str:
    """把黑板关键事实渲染为压缩锚点文本（纯代码生成，零 LLM）。

    只保留 confirmed/verified 的关键条目，剔除大段未验证噪音，保证压缩后
    flag 线索、死路结论、hint 法令、破局指令不丢失。
    """
    lines: List[str] = []
    board = getattr(ctx, "blackboard", {}) or {}
    for k, v in board.items():
        if not isinstance(v, dict):
            continue
        status = str(v.get("status", ""))
        value = str(v.get("value", ""))[:max_value_chars]
        verified = bool(v.get("verified", True))
        if verified and (status == "confirmed" or status == "done"):
            lines.append(f"- {k}: {value}")
    # 关键字段显式兜底（防止上面筛选漏掉）
    for key in ("hint_directive", "next_directive", "plan"):
        v = board.get(key)
        if isinstance(v, dict) and str(v.get("value", "")).strip():
            lines.append(f"- {key}: {str(v['value'])[:max_value_chars]}")
    return "\n".join(lines)


class MemoryManager:
    """统一三层记忆访问器（薄封装，不改变 TaskContext 既有字段）。"""

    def __init__(self, ctx: TaskContext) -> None:
        self.ctx = ctx

    # ---------------- 短期：当前会话笔记（notes） ----------------
    def append_note(self, text: str) -> None:
        """追加一条短期笔记（只追加不覆盖）。"""
        if text:
            self.ctx.notes.append(text)

    def recent_notes(self, n: int = 5) -> List[str]:
        """取最近 n 条短期笔记。"""
        return list(self.ctx.notes[-n:])

    # ---------------- 中期：黑板（已验证事实 / 法令 / 死路） ----------------
    def set_blackboard(self, key: str, value: Any, status: str = "confirmed",
                       verified: bool = True, evidence: str = "") -> None:
        """写入黑板条目（只由代码写入，统一结构字段）。"""
        self.ctx.blackboard[key] = {
            "value": value,
            "status": status,
            "ts": int(time.time()),
            "verified": verified,
        }
        if evidence:
            self.ctx.blackboard[key]["evidence"] = evidence

    def get_blackboard(self, key: str) -> Any:
        """读黑板条目原始值；不存在返回 None。"""
        v = self.ctx.blackboard.get(key)
        if isinstance(v, dict):
            return v.get("value")
        return v

    def confirmed_blackboard(self) -> Dict[str, Any]:
        """只返回 confirmed/done 且 verified 的条目（供压缩/复盘复用）。"""
        out: Dict[str, Any] = {}
        for k, v in self.ctx.blackboard.items():
            if isinstance(v, dict) and bool(v.get("verified", True)) \
                    and v.get("status") in ("confirmed", "done"):
                out[k] = v.get("value")
        return out

    def mark_dead_end(self, key: str, value: str) -> None:
        """登记一条死路结论（status=failed，同分支 EV 衰减由外部策略消费）。"""
        self.ctx.blackboard[key] = {
            "value": value, "status": "failed", "ts": int(time.time()),
            "verified": True, "evidence": "dead_end",
        }

    # ---------------- 长期：跨题档案（field_notes，落盘由调用方完成） ----------------
    def append_field_note(self, text: str) -> None:
        """追加一条长期档案（只追加不覆盖）。

        长期档案文件（field_notes.md）的落盘由 main 统一 with-open 追加；
        这里仅同步到短期笔记，保证本轮上下文可见。
        """
        if text:
            self.ctx.notes.append(text)

    # ---------------- 通用 ----------------
    def snapshot(self) -> str:
        """生成当前黑板快照（压缩锚点/复盘用）。"""
        return render_blackboard_snapshot(self.ctx)
