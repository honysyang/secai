"""run_task 编排支撑（立法 / 战报两大入口编排段）。

- legislate_charter：战略家立法 + 规划（全局一次 + 同任务幂等缓存复用）；
- finalize_report：报告者收尾（战报后台生成 + 落 field_notes + 四指标看板）。

global_model_pool 经 harness.runner.pool 的 set/get 句柄读写，避免模块间循环 import。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

from core.agents_def import build_reporter, build_strategist
from core.charter import save_charter
from harness.runner.pool import get_global_model_pool
from runtime.log import log_error, log_info, log_warn
from runtime.model_fallback import run_with_model_fallback
from runtime.reporting import write_dashboard
from runtime.status import set_status


class StrategistFailed(RuntimeError):
    """战略家立法两次尝试均失败（原 run_task 的 error return 分支异常化）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def legislate_charter(task: str, base_role: dict, hooks,
                            workdir: Path, data_dir: Path) -> tuple:
    """战略家·立法与规划（全局一次；合并管理者 + 规划师，减少一轮 LLM 调用）。

    立法幂等：同任务已有缓存宪章/计划则复用，避免重跑强模型（resume/重启场景）。
    返回 (charter, global_plan)；彻底失败抛 StrategistFailed（调用方转 error return）。
    """
    log_info("== 战略家：写使命宪章与作战计划 ==")
    set_status(workdir, "legislate", "running")
    _task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()[:16]
    _charter_cache = data_dir / "mission_charter.md"
    _charter_meta = data_dir / "mission_charter.meta.json"
    charter = ""
    global_plan = ""
    try:
        if _charter_cache.exists() and _charter_meta.exists():
            meta = json.loads(_charter_meta.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("task_hash") == _task_hash:
                charter = _charter_cache.read_text(encoding="utf-8")
                global_plan = str(meta.get("plan", ""))
                log_info("== 战略家：复用已缓存宪章/计划（同任务幂等）==")
    except Exception as e:
        log_warn(f"[legislate] 读取宪章缓存失败，重新立法：{str(e)[:120]}")
        charter = ""
        global_plan = ""

    if not charter:
        combined_doc = ""
        for attempt in range(2):
            try:
                pool = get_global_model_pool()
                combined_result = await run_with_model_fallback(
                    build_strategist(model=pool.current.model),
                    input=(f"用户任务：\n{task}\n\n"
                           f"角色提示：{base_role['role']}\n"
                           f"角色风格：{base_role.get('style', '')[:200]}\n\n"
                           f"请一次性输出使命宪章和作战计划。"),
                    hooks=hooks,
                    model_pool=pool,
                    agent_name="Strategist")
                combined_doc = str(combined_result.final_output)
                break
            except Exception as e:
                log_warn(f"[retry] 战略家立法/规划失败（{attempt + 1}/2）：{str(e)[:200]}")
                if attempt == 1:
                    log_error(f"== 战略家立法/规划失败：{str(e)[:200]}，无法继续 ==")
                    set_status(workdir, "legislate", "error")
                    raise StrategistFailed(
                        f"strategist_failed: {type(e).__name__}") from e
                await asyncio.sleep(3)
        # 简单拆分：宪章取「# 使命宪章」到「# 作战计划」之间的内容；计划取剩余部分
        charter_part = combined_doc
        plan_part = ""
        if "# 作战计划" in combined_doc:
            idx = combined_doc.index("# 作战计划")
            charter_part = combined_doc[:idx]
            plan_part = combined_doc[idx:]
        charter = charter_part.strip()
        global_plan = plan_part.strip() or charter
        try:
            save_charter(data_dir / "mission_charter.md", charter)
            (_charter_meta).write_text(
                json.dumps({"task_hash": _task_hash, "plan": global_plan},
                           ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log_warn(f"[legislate] 保存宪章缓存失败：{str(e)[:120]}")
    set_status(workdir, "legislate", "finish")
    return charter, global_plan


async def finalize_report(workdir: Path, results: list, fatal_reason: str,
                          hooks, data_dir: Path) -> str:
    """报告者·收尾（后台异步生成，不阻塞主进程结束）+ 四指标看板。

    返回战报文本（供 run_task 最终 return）；5 秒内能完成则直接用，否则
    提示后台生成中——随后 join 等待战报最终写入 field_notes（30s 兜底）。
    """
    set_status(workdir, "report", "running")

    async def _generate_report() -> str:
        try:
            events_text = (workdir / "events.jsonl").read_text(encoding="utf-8")[-3000:]
        except Exception:
            events_text = ""
        summary = json.dumps(results, ensure_ascii=False)[:1000]
        prompt = (f"任务执行结束（{fatal_reason or '题目遍历完成'}）。"
                  f"各题结果：{summary}\n\n事件流尾部：\n{events_text}")

        async def _one_report() -> str:
            pool = get_global_model_pool()
            rep = await run_with_model_fallback(
                build_reporter(model=pool.current.model),
                input=prompt,
                hooks=hooks,
                model_pool=pool,
                agent_name="Reporter")
            return str(rep.final_output)

        try:
            text = await _one_report()
            # A5：软约束机械化——战报必须含「## 战报」与「## 死路蒸馏」两节，
            # 缺节重试一次；仍缺节则落原文并记 [report] ERROR（赛后追责）
            if "## 战报" not in text or "## 死路蒸馏" not in text:
                log_warn("[report] 战报缺节（需要 ## 战报 / ## 死路蒸馏），重试一次")
                text2 = await _one_report()
                if "## 战报" in text2 and "## 死路蒸馏" in text2:
                    text = text2
                else:
                    log_error("[report] 重试后战报仍缺节，落原文（格式纪律未机械化到位）")
            return text
        except Exception as e:
            log_error(f"== 报告生成失败：{str(e)[:200]}，降级为无战报 ==")
            return f"（战报生成失败：{str(e)[:200]}）"

    report_task = asyncio.create_task(_generate_report())
    try:
        report_text = await asyncio.wait_for(report_task, timeout=5.0)
    except asyncio.TimeoutError:
        report_text = "（战报后台生成中，请查看 data/field_notes.md）"
        log_info("[report] 战报后台生成中，未阻塞主进程结束")

    # 无论是否超时，确保战报最终写入 field_notes（join 等待，避免进程退出时战报丢失）
    async def _persist_report():
        try:
            final = await asyncio.wait_for(asyncio.shield(report_task), timeout=30.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            final = "（战报生成超时，未写入）"
        try:
            with (data_dir / "field_notes.md").open("a", encoding="utf-8") as f:
                f.write(f"\n\n# generic · {time.strftime('%Y-%m-%d %H:%M')}\n{final}\n")
        except Exception as e:
            log_warn(f"[report] 写入 field_notes 失败：{str(e)[:120]}")
        print("\n===== 战报 =====\n" + final)
        set_status(workdir, "report", "finish")

    try:
        await asyncio.wait_for(_persist_report(), timeout=35.0)
    except asyncio.TimeoutError:
        log_warn("[report] 战报写入超时，跳过（不影响主流程）")

    # 四指标看板：汇总所有 worker_*/cost_report.json → dashboard.json
    try:
        write_dashboard(workdir)
    except Exception:
        pass
    return report_text
