"""Artifacts 域：超长输出外置 / 文件读写（artifacts/ 全文存取）。

R1 纯搬家：自 demo_tools.py 按功能域拆出，业务逻辑零改动。
- read_artifact：读取 artifacts/ 外置全文（可分段）
- write_file：把内容写入工作目录文件（相对路径校验）
- _spill_output：旧兼容——超长输出落盘 artifacts/ 并返回预览（供未接入管线的只读工具用）
- ARTIFACT_SPILL_THRESHOLD：外置阈值
"""
from __future__ import annotations

import json
import uuid

from agents import RunContextWrapper, function_tool

from core.task_context import TaskContext
from core.tool_pipeline import DEFAULT_PIPELINE, with_pipeline
from tools.domains._base import _guard_output
from tools.domains.platform import _submit_flags_if_any

ARTIFACT_SPILL_THRESHOLD = 4000  # 工具输出超过此字符数才外置到 artifacts/


def _spill_output(ctx: RunContextWrapper[TaskContext], text: str) -> str:
    """（兼容旧非管线工具）工具输出超长时写入 artifacts/ 文件并返回预览。

    已接入管线的工具由 ArtifactSpillMiddleware 统一处理，本函数保留给未接入
    管线的只读工具（distinguish / run_tool / read_artifact 等）使用。
    """
    submit_note = _submit_flags_if_any(ctx, text)  # 先扫全文 flag 再截断
    guard_note = _guard_output(text)               # 先扫全文注入特征再截断
    note = "\n".join(x for x in (submit_note, guard_note) if x)
    if len(text) <= ARTIFACT_SPILL_THRESHOLD:
        return text + (f"\n{note}" if note else "")
    c = ctx.context
    art_dir = c.workdir / "artifacts"
    art_dir.mkdir(exist_ok=True)
    art_id = uuid.uuid4().hex[:8]
    (art_dir / f"{art_id}.txt").write_text(text, encoding="utf-8")
    tail = (f"\n...[已截断，全文 {len(text)} 字符保存到 artifacts/{art_id}.txt]"
            + f"\n[用 read_artifact {art_id} 读取全文]")
    if note:
        tail += f"\n{note}"
    return text[:ARTIFACT_SPILL_THRESHOLD] + tail


@function_tool
def read_artifact(ctx: RunContextWrapper[TaskContext], artifact_id: str,
                  offset: int = 0, limit: int = 4000) -> str:
    """读取之前外置到 artifacts/ 的工具输出全文（大段源码/扫描结果）。

    artifact_id 是工具返回里「artifacts/xxx.txt」中的 xxx；offset/limit 可分段读取。
    """
    c = ctx.context
    path = c.workdir / "artifacts" / f"{artifact_id}.txt"
    if not path.exists():
        return json.dumps({"error": f"artifact {artifact_id} 不存在"}, ensure_ascii=False)
    text = path.read_text(encoding="utf-8")
    chunk = text[offset:offset + limit]
    result = json.dumps({"artifact_id": artifact_id, "total": len(text),
                         "offset": offset, "content": chunk}, ensure_ascii=False)
    return result + _guard_output(result)


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def write_file(ctx: RunContextWrapper[TaskContext], path: str, content: str) -> str:
    """把内容写入工作目录下的文件（如 python3 脚本、payload 文件）。

    复杂探测逻辑请先写到文件，再用 shell 执行 `python3 <文件>`——避免把大段脚本
    反复塞进 shell 命令参数、撑爆上下文。path 为相对工作目录的路径。
    """
    c = ctx.context
    try:
        p = (c.workdir / path).resolve()
        p.relative_to(c.workdir.resolve())
    except (ValueError, OSError):
        return json.dumps({"error": "path 必须在工作目录内"}, ensure_ascii=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return json.dumps({"written": str(p), "chars": len(content)}, ensure_ascii=False)
