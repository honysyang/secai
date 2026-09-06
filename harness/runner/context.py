"""单题上下文辅助。

- 战地笔记（field_notes.md）读写与按题检索：_load_field_notes / load_notes_for /
  _append_mechanical_note（零 LLM 机械沉淀）
- 黑板载入与情报合并：_load_blackboard / _merge_subtask_intel
- 破局复盘与软干预教练：_replan / _coach（fork_analyst 一次性强模型分析）

数据路径仍为仓库根的 data/ 目录。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.events import BUS
from harness.runner.pool import get_global_model_pool
from runtime.fork_analyst import fork_analyze, update_blackboard_with_fork
from runtime.log import log_info, log_warn

DATA_DIR = Path(__file__).parent.parent.parent / "data"
FIELD_NOTES_FILE = DATA_DIR / "field_notes.md"


def _load_field_notes(max_chars: int = 3000) -> str:
    """读取上次战报尾部（含「死路蒸馏」），作为执行者的历史作战档案注入。"""
    if not FIELD_NOTES_FILE.exists():
        return ""
    return FIELD_NOTES_FILE.read_text(encoding="utf-8")[-max_chars:]


def _merge_subtask_intel(ctx, challenge_workdir: Path) -> None:
    """R2：把子任务共享情报（sub_intel.jsonl）增量合并进主线黑板。

    只合并结论性 verified 条目；主线黑板已有 verified 结论的同名 key 不覆盖
    （防伪证回流、防覆盖主线决策）。子任务运行期间即可见，无需等其结束。
    """
    p = challenge_workdir / "sub_intel.jsonl"
    if not p.exists():
        return
    try:
        merged = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            k = item.get("key")
            v = item.get("entry")
            if not k or not isinstance(v, dict) or not v.get("verified"):
                continue
            cur = ctx.blackboard.get(k)
            if cur is not None and cur.get("verified"):
                continue
            ctx.blackboard[k] = v
            merged += 1
        if merged:
            log_info(f"[黑板合并] 单题 {ctx.current_code} 共享子任务情报 {merged} 条")
    except Exception:
        pass


def _append_mechanical_note(code: str, outcome: str, ctx) -> None:
    """题级机械沉淀（零 LLM）：战果 + 死路从黑板/提交记录直接提取。"""
    failed = [k for k, v in ctx.blackboard.items()
              if isinstance(v, dict) and v.get("status") == "failed"][:8]
    wins = [f"correct:{f}" for f in getattr(ctx, "correct_flags", [])][:8]
    disclosed = ",".join(getattr(ctx, "disclosed_skills", [])[:6])
    lines = [f"\n# {code} · {outcome} · {time.strftime('%m-%d %H:%M')}",
             f"- 战果: {', '.join(wins) or '无'}",
             f"- 死路: {', '.join(failed) or '无'}",
             f"- 披露技能: {disclosed}"]
    try:
        with FIELD_NOTES_FILE.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def load_notes_for(code: str, max_chars: int = 900) -> str:
    """按题检索档案：本题 + 同前缀题的历史段落，最近 3 段。"""
    if not FIELD_NOTES_FILE.exists():
        return ""
    text = FIELD_NOTES_FILE.read_text(encoding="utf-8")
    prefix = code.rsplit("-", 1)[0] if "-" in code else code
    hits = [sec[:max_chars] for sec in text.split("\n# ")
            if sec.startswith(code) or sec.startswith(prefix + "-")]
    return "\n---\n".join(hits[-3:])


def _load_blackboard(workdir: Path) -> dict:
    """从 workdir/blackboard.json 加载黑板（存在则返回，否则空 dict）。

    挂起/重试同一题时回注上次进度，避免重复已做/已排除的结论。
    """
    p = workdir / "blackboard.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def _replan(ctx, task: str, charter: str, role: dict, hooks) -> str:
    """执行中计划修正：调用 fork_analyst 做一次性强模型分析，结果只写 next_directive。

    不再调用常驻 Strategist；fork_analyst 只读取近期轨迹与黑板，产出破局建议。
    """
    events = BUS.history(hooks.task_id)
    role_brief = (
        f"任务：{task}\n"
        f"角色：{role.get('role', 'unknown')}\n"
        f"宪章：{charter[:500]}"
    )
    pool = get_global_model_pool()
    try:
        strong = pool.switch_to_role("strong") if pool else None
        model = strong.model if strong else None
        result = await fork_analyze(
            events=events,
            blackboard=ctx.blackboard,
            role_brief=role_brief,
            model=model,
            model_pool=pool,
        )
        directive = update_blackboard_with_fork(ctx.blackboard, result)
        log_info(f"[fork-analyst] 单题 {hooks.task_id} 产出 next_directive：{directive[:80]}")
        return directive
    except Exception as e:
        log_warn(f"[fork-analyst] 调用失败：{e}；回退到静态提示")
        return "继续探索新的可验证方向，优先使用已解锁技能做最小验证。"


async def _coach(ctx, brief, hooks) -> str:
    """软干预教练：直接返回 blackboard 中 fork_analyst 的 next_directive，避免常驻 Coach Agent。"""
    fork_entry = ctx.blackboard.get("next_directive")
    if fork_entry and isinstance(fork_entry, dict) and fork_entry.get("value"):
        return str(fork_entry["value"])
    return "当前无明确教练建议。请基于黑板事实，选择一个未验证方向做最小动作并产出证据。"
