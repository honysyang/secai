"""黑板域：全局黑板工具 + 落盘（跨尝试/挂起恢复）。

R1 纯搬家：自 demo_tools.py 按功能域拆出，业务逻辑零改动。
- blackboard：set / get / list / del（结构化记忆语义：verified/evidence/supersedes）
- _persist_blackboard：黑板落盘到 workdir/blackboard.json
- BLACKBOARD_MAX_ENTRIES / BLACKBOARD_FILE：容量与落盘文件名
"""
from __future__ import annotations

import json
import time

from agents import RunContextWrapper, function_tool

from core.task_context import TaskContext
from runtime.log import log_info

BLACKBOARD_MAX_ENTRIES = 50     # 黑板容量上限，超出淘汰最旧条目（优先淘汰 done/failed）
BLACKBOARD_FILE = "blackboard.json"  # 黑板落盘文件名（跨尝试/挂起恢复用）


def _persist_blackboard(ctx: RunContextWrapper[TaskContext]) -> None:
    """把黑板落盘到 workdir/blackboard.json（跨尝试/挂起恢复时回注，不丢进度）。"""
    try:
        (ctx.context.workdir / BLACKBOARD_FILE).write_text(
            json.dumps(ctx.context.blackboard, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


@function_tool
def blackboard(ctx: RunContextWrapper[TaskContext], action: str, key: str = "",
               value: str = "", status: str = "done", verified: bool = True,
               evidence: str = "", supersedes: str = "") -> str:
    """全局黑板：跨轮记录「已完成事项 / 全局变量」，每条带时间戳、状态与验证标记。

    action 取值：
      - set : 写入 key=value，可带 status（pending/doing/done/failed，默认 done）
      - get : 读取 key 的完整条目（含 value/status/时间戳/verified/evidence）
      - list: 列出全部条目
      - del : 删除 key

    结构化记忆语义（set 时生效）：
      - verified：结论是否经过实际验证。缺省 True；但当 status=failed 且未提供
        evidence 时强制记为 False（一次失败观察不判死整个方向，只是未验证线索）。
      - evidence：证据来源（命令/响应特征）。写「失败/排除」类结论时必填，
        附上 evidence 才视为 verified=True 的判死（禁止重做）。
      - supersedes：要取代的旧 key（发现旧结论错误/被证伪时用）；被取代的旧条目
        从黑板中删除，不再误导后续决策。
    """
    c = ctx.context
    a = (action or "").strip().lower()
    if a == "set":
        if not key:
            return json.dumps({"error": "set 需要提供 key"}, ensure_ascii=False)
        key = key.strip()
        status = (status or "done").strip()
        evidence = (evidence or "").strip()
        supersedes = (supersedes or "").strip()
        if supersedes:
            if supersedes == key:
                return json.dumps({"error": "supersedes 不能指向自身"},
                                  ensure_ascii=False)
            if supersedes not in c.blackboard:
                return json.dumps({"error": f"supersedes 目标 {supersedes} 不存在"},
                                  ensure_ascii=False)
            # 先删除被取代的旧结论：释放容量，并保证其不再出现在 list/注入摘要中
            c.blackboard.pop(supersedes, None)
        # 失败观察缺省未验证：status=failed 且无 evidence 时强制 verified=False，
        # 避免一次失败就被当作「该方向已判死」从而永久排除
        if status == "failed" and not evidence:
            verified = False
        if key not in c.blackboard and len(c.blackboard) >= BLACKBOARD_MAX_ENTRIES:
            # 淘汰最旧条目：优先淘汰已完成/失败的，避免挤掉进行中的关键项
            done = [k for k, v in c.blackboard.items()
                    if isinstance(v, dict) and v.get("status") in ("done", "failed")]
            victim = min(done, key=lambda k: c.blackboard[k].get("ts", 0)) if done \
                else min(c.blackboard, key=lambda k: c.blackboard[k].get("ts", 0)
                         if isinstance(c.blackboard[k], dict) else 0)
            c.blackboard.pop(victim, None)
        entry = {
            "value": value,
            "status": status,
            "ts": int(time.time()),
            "verified": bool(verified),
        }
        if evidence:
            entry["evidence"] = evidence
        if supersedes:
            entry["supersedes"] = supersedes
        c.blackboard[key] = entry
        _persist_blackboard(ctx)  # 落盘，挂起/重试时保留进度
        # R2：子任务情报共享——新 key + 结论性状态时 append 到 sub_intel.jsonl
        # （append-only 免锁竞争；主线每轮读后增量合并，运行期即可见，不必等子任务结束）
        if (getattr(c, "is_subtask", False)
                and key not in getattr(c, "_snapshot_keys", set())
                and status in ("confirmed", "done", "success", "failed")):
            try:
                intel = {"key": key, "entry": entry}
                with open(c.workdir / "sub_intel.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(intel, ensure_ascii=False) + "\n")
            except Exception:
                pass
        ev = f"，证据={evidence[:60]}" if evidence else ""
        log_info(f"[黑板] {key} = {str(value)[:60]}（{status}，verified={verified}{ev}）")
        return json.dumps({"ok": True, "key": key, "entry": entry},
                          ensure_ascii=False)
    if a == "get":
        return json.dumps({"key": key, "entry": c.blackboard.get(key)},
                          ensure_ascii=False)
    if a == "list":
        return json.dumps({"blackboard": c.blackboard}, ensure_ascii=False)
    if a == "del":
        c.blackboard.pop(key, None)
        _persist_blackboard(ctx)  # 落盘，挂起/重试时保留进度
        return json.dumps({"ok": True, "deleted": key}, ensure_ascii=False)
    return json.dumps({"error": f"未知 action：{action}（可用 set/get/list/del）"},
                      ensure_ascii=False)
