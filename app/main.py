"""TSec Benchmark 跑分主程序（多智能体安全攻防编排）。

调度循环选题 → 单题状态机（pre/step/post）→ 三闸门子任务 → 终局重扫 → 报告收尾。
依赖：平台客户端（凭证由 bench_platform 单例统一收敛）、flag 机械提交铁律
（profiles/ctf_legacy，R4 H12）、多模型灾备池（ModelPool）、事件总线落库。

R1 可测试性重构后本文件只保留「调度器编排」：run_task 主循环 + _endgame_sweep
（结构被 tests/test_core.py AST 锁定）+ 入口。单题执行闭环移入
harness/runner/executor.py（ExecutorLoop）、子任务移入 harness/runner/subtasks.py、
上下文辅助移入 harness/runner/context.py、立法/战报段移入
harness/runner/orchestrator.py。跑分任务模板与平台凭证占位符替换收敛到
profiles/ctf_legacy（task.py）。

用法：
    python -m app.main                              # 跑分模式（配置了平台凭证自动进调度器）
    python -m app.main "<任务描述>" [角色提示]       # 通用渗透任务
    python -m app.main --resume                     # 从上次 checkpoint 续跑
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

import adapters.db as db_mod
from adapters.config import (API_KEY, BASE_URL, FAST_MODEL_NAME, MODEL_NAME,
                             VPN_CONFIG)
from arsenal.registries import sec_tools
from arsenal.registries.role_registry import assign_role
from arsenal.registries.skill_registry import load_skills
from bench_platform.platform_client import (ContainerBusy, PlatformClient,
                                            TaskEnded, TaskNotFound,
                                            get_platform_client,
                                            platform_configured)
from bench_platform.scheduler import is_endgame, select_challenge
from core.events import BUS
from core.hooks import EventStreamHooks
from harness.runner.executor import run_single_challenge
from harness.runner.orchestrator import (StrategistFailed, finalize_report,
                                         legislate_charter)
from harness.runner.pool import get_global_model_pool, set_global_model_pool
from profiles.ctf_legacy import build_default_task
from runtime.deadline import DEADLINE_SAFE_MARGIN, TASK_DEADLINE_TS
from runtime.log import log_error, log_info, log_warn
from runtime.model_pool import ModelPool
from runtime.status import set_status

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

WORKDIR = DATA_DIR / "worker_generic"
WORKDIR.mkdir(parents=True, exist_ok=True)
_db_initialized = False


def _init_observability() -> None:
    """初始化 SQLite 落库 + 事件总线订阅（只初始化一次，防重复订阅）。

    hooks 发射的事件经 BUS 分发到 db 订阅者，与 events.jsonl 文件双写留痕。
    """
    global _db_initialized
    if _db_initialized:
        return
    db = db_mod.init_default()
    BUS.subscribe(db_mod.db_subscriber(db))
    _db_initialized = True
    log_info("可观测性初始化完成：SQLite 落库 + 事件总线订阅")


async def run_task(task: str, role_hint: str = "", resume: bool = False) -> dict:
    """跑分任务总入口：立法 → 调度器主循环 → 终局重扫 → 报告收尾。

    主循环结构（while True + _run_one + _endgame_sweep + 最终 return）被
    tests/test_core.py 的 AST 测试锁定，不得把主循环搬出本函数。
    """
    _init_observability()  # 事件总线 → SQLite 落库（只初始化一次）
    log_info("===== 跑分任务开始 =====")
    log_info(
        f"启动配置：模型 {MODEL_NAME}，网关 {BASE_URL}，"
        f"API_KEY {'已配置' if API_KEY else '未配置'}，"
        f"平台 {'已配置' if platform_configured() else '未配置'}，"
        f"VPN {'已配置' if VPN_CONFIG else '未配置'}，resume={resume}"
    )
    log_info(f"任务摘要：{task.strip()[:200]}")
    skills = load_skills()
    log_info(f"技能库加载：{len(skills)} 个技能（{', '.join(sorted(skills))[:300]}）")
    available_tools = sec_tools.available_tools()
    log_info(f"本机安全工具：{len(available_tools)} 个可用（{', '.join(sorted(available_tools))[:300]}）")
    workdir = WORKDIR
    workdir.mkdir(exist_ok=True)
    hooks = EventStreamHooks(workdir, "generic")

    # 外层 Agent 全局模型池（与单题 executor 内部模型池隔离，互不污染）；
    # 主模型 glm 优先，deepseek（flash/pro）仅作灾备兜底。句柄寄宿 harness/runner/pool.py。
    set_global_model_pool(ModelPool())
    log_info(f"== 模型池就绪：{get_global_model_pool()} ==")
    # 注意：外层 Agent 一律工厂化按需构建（build_strategist/build_reporter），
    # 不再有模块级可变单例（A2），此处无需同步模型。

    # 清理旧 checkpoint / 事件流（调度器模式：题目进度在平台侧，本地不依赖续跑状态）
    for f in ("state.json", "session.sqlite"):
        (workdir / f).unlink(missing_ok=True)
    (workdir / "events.jsonl").write_text("", encoding="utf-8")

    # 全局 fallback 角色提示（单题会按 unique_code 重新派任，这里只做参考）
    base_role = assign_role(role_hint, task)
    log_info(f"== 全局角色提示：{base_role['role']} ==")

    # ① 战略家·立法与规划（全局一次；立法幂等：同任务缓存宪章/计划则复用）
    try:
        charter, global_plan = await legislate_charter(task, base_role, hooks,
                                                       workdir, DATA_DIR)
    except StrategistFailed as e:
        return {"status": "error", "reason": e.reason, "results": [], "report": ""}

    # ③ 调度器主循环：自适应并发（持续 start 直到 container_busy，天然适配平台容器上限）
    # 平台客户端单例（R4 H12 收口：全进程唯一构造点，凭证收敛在 bench_platform）
    client = get_platform_client()
    # 执行者共享模型池：FAST_MODEL（deepseek-v4-flash）优先，glm 兜底；
    # 全局共享一份，避免每题新建池导致灾备状态丢失。
    fast_pool = ModelPool(preferred_name=FAST_MODEL_NAME)
    attempts: Dict[str, int] = {}
    active: Dict[str, asyncio.Task] = {}  # code -> 单题 asyncio 任务
    MAX_SLOTS = int(os.getenv("PLATFORM_MAX_ACTIVE", "3"))  # 软上限保护（默认 3，适配平台活跃容器上限）
    results = []
    fatal_reason = ""
    list_fail_streak = 0  # 拉题目列表连续失败计数（网络抖动重试，超限停止，不崩溃退出）
    LIST_RETRY_MAX = int(os.getenv("LIST_RETRY_MAX", "10"))  # 连续失败上限
    slot_wait = 0  # 连续「有未完成题但拿不到容器名额」轮数（残留容器清理/判停用）
    leak_streak = 0  # 平台侧残留容器连续出现轮数
    close_pending: set = set()  # close 失败重试队列

    # 启动前不清理残留容器：依赖平台 max_active 自然淘汰，避免启动阶段
    # 浪费大量时间逐个关闭容器（参考日志 secai-20260814.log #L26-35）。

    async def _run_one(code: str, desc: str, addrs: list, difficulty: str,
                       chal: dict, model_pool: ModelPool) -> str:
        """运行一道已 start 成功的题，返回 outcome。

        start 由主循环同步完成（以便立即感知 container_busy），本函数只负责跑题。
        单题执行闭环在 harness.runner.executor.run_single_challenge（ExecutorLoop）。
        """
        set_status(workdir, "execute", "running", code=code)
        outcome = await run_single_challenge(
            code, desc, addrs, charter, task, global_plan, hooks, workdir,
            client, difficulty,
            flag_total=chal.get("flag_count") or 1,
            flag_done=chal.get("correct_flag_count") or 0,
            model_pool=model_pool)
        return outcome

    try:
        while True:
            # 全局 deadline 检查（比赛硬时限，含安全余量）
            if TASK_DEADLINE_TS:
                try:
                    if time.time() >= float(TASK_DEADLINE_TS) - DEADLINE_SAFE_MARGIN:
                        log_warn("== deadline 到达，停止跑分 ==")
                        break
                except ValueError:
                    pass

            # 拉题目列表（网络抖动/5xx/空列表重试，不因偶发异常崩溃退出）
            try:
                challenges = await asyncio.to_thread(client.list_challenges)
                if not isinstance(challenges, list) or not challenges:
                    # 空列表不是「全部完成」，而是异常（任务未开始/已结束/被清空），
                    # 必须重试并最终报错，避免静默误判为全部完成而提前退出、漏题。
                    raise ValueError("题目列表为空")
                list_fail_streak = 0
            except (TaskEnded, TaskNotFound) as e:
                fatal_reason = str(e)
                log_warn(f"== 平台终止：{fatal_reason} ==")
                break
            except Exception as e:
                list_fail_streak += 1
                log_warn(f"[retry] 拉取题目列表失败({list_fail_streak})：{str(e)[:200]}")
                if list_fail_streak >= LIST_RETRY_MAX:
                    fatal_reason = f"list_challenges 连续失败 {list_fail_streak} 次：{str(e)[:200]}"
                    break
                await asyncio.sleep(2)
                continue

            # 状态对齐：平台侧 running 但本地未记录 = 泄漏槽位
            leaked = [c.get("unique_code") for c in challenges
                      if c.get("container_status") == "running"
                      and c.get("unique_code") not in active]
            if leaked:
                leak_streak += 1
                log_warn(f"[leak] 平台侧残留容器 {leaked}，第 {leak_streak} 轮")
                if leak_streak >= 3:
                    for lc in leaked:
                        try:
                            await asyncio.to_thread(client.close_challenge, lc)
                            log_warn(f"[leak] 已机械关闭残留容器 {lc}")
                        except Exception:
                            pass
                    leak_streak = 0
            else:
                leak_streak = 0

            # close 失败重试队列：每轮尝试关闭之前没关掉的容器
            for cc in list(close_pending):
                try:
                    if await asyncio.to_thread(client.close_challenge, cc):
                        close_pending.discard(cc)
                        log_info(f"[close-retry] {cc} 已关闭，槽位回收")
                except Exception:
                    pass

            # 自适应并发：持续 start 直到 container_busy（名额满）或没题或软上限
            while len(active) < MAX_SLOTS:
                # 排除已活跃的题，避免重复选题
                candidates = [c for c in challenges if c.get("unique_code") not in active]
                # 收尾回捞：所有未完成题都至少放弃过一次时，降低衰减逐个回捞
                endgame = is_endgame(challenges, attempts)
                chal = select_challenge(candidates, attempts, endgame=endgame)
                if chal is None:
                    break
                code = chal.get("unique_code", "")
                desc = chal.get("description", "") or ""
                difficulty = chal.get("difficulty", "")
                # 同步 start：立即感知 container_busy，被拒就停止派发（等活跃题 close 释放）
                try:
                    addrs = await asyncio.to_thread(client.start_challenge, code)
                except ContainerBusy:
                    break
                except (TaskEnded, TaskNotFound) as e:
                    # 平台任务结束/token 无效：全局终止信号，不能当普通启动失败跳过
                    fatal_reason = str(e)
                    log_warn(f"== 平台终止：{fatal_reason} ==")
                    break
                except Exception as e:
                    attempts[code] = attempts.get(code, 0) + 1
                    log_warn(f"[start] 启动 {code} 失败：{str(e)[:200]}，跳过")
                    continue
                if not addrs:
                    attempts[code] = attempts.get(code, 0) + 1
                    continue
                # start 成功：真正占用容器，创建跑题任务
                t = asyncio.create_task(_run_one(code, desc, addrs, difficulty, chal, fast_pool))
                active[code] = t
                log_info(f"[slot] 启动 {code}（活跃 {len(active)}/{MAX_SLOTS}）")

            if fatal_reason:
                # start 阶段遇到 TaskEnded/TaskNotFound：取消并发题，终止整个跑分
                for other in active.values():
                    other.cancel()
                if active:
                    await asyncio.gather(*active.values(), return_exceptions=True)
                break

            if not active:
                # 区分「全部完成」与「还有题但拿不到名额」：后者是残留容器占位，
                # 不能误报为全部完成（否则提前退出、漏题）。
                unfinished = [c for c in challenges if not c.get("is_completed")]
                if not unfinished:
                    log_info("== 全部题目已完成 ==")
                    break
                slot_wait += 1
                if slot_wait == 1:
                    log_warn("[slot] 无可用槽位但仍有未完成题，批量清理非 running 残留容器")
                    for c in unfinished:
                        if c.get("container_status") in ("available", "stopped", ""):
                            try:
                                await asyncio.to_thread(client.close_challenge, c.get("unique_code"))
                            except Exception:
                                pass
                if slot_wait >= 10:
                    fatal_reason = "连续 10 轮拿不到容器名额（疑似平台侧残留/泄漏）"
                    break
                await asyncio.sleep(5)
                continue

            slot_wait = 0  # 成功拿到槽位，重置无槽位计数

            # 等待任一单题完成
            done, _ = await asyncio.wait(
                list(active.values()), return_when=asyncio.FIRST_COMPLETED)

            # 处理完成的单题
            fatal_hit = False
            for t in done:
                code = next(c for c, task in active.items() if task is t)
                del active[code]
                try:
                    outcome = t.result()  # _run_one 直接返回 outcome
                except Exception as e:
                    outcome = "error"
                    log_error(f"[error] 单题 {code} 异常：{str(e)[:200]}")
                # 关闭容器释放名额：检查返回值，失败重试（close 静默失败是 container_busy 灾难根因）
                closed = False
                for _ in range(3):
                    try:
                        closed = await asyncio.to_thread(client.close_challenge, code)
                    except Exception:
                        closed = False
                    if closed:
                        break
                    await asyncio.sleep(1)
                if not closed:
                    log_warn(f"[warn] 单题 {code} 容器关闭失败，进入重试队列")
                    close_pending.add(code)
                else:
                    close_pending.discard(code)
                # attempts 只对真正跑过且未解的题降权（container_busy/start_failed 已在 start 阶段处理）
                if outcome in ("stuck", "suspended", "error"):
                    attempts[code] = attempts.get(code, 0) + 1
                results.append({"code": code, "outcome": outcome})
                log_info(f"== 单题 {code} 结果：{outcome} ==")
                if outcome == "fatal":
                    fatal_reason = "task_ended"
                    fatal_hit = True

            if fatal_hit:
                # 任一题致命错误：取消其余并发任务，终止战役
                # 必须 await 取消完成，确保各任务的 finally 能正常关闭容器
                for other in active.values():
                    other.cancel()
                if active:
                    await asyncio.gather(*active.values(), return_exceptions=True)
                break
    except KeyboardInterrupt:
        log_warn("== 已中断 ==")
        set_status(workdir, "execute", "interrupted")
        for t in active.values():
            t.cancel()
        return {"status": "interrupted", "results": results}
    finally:
        # 清理所有仍活跃的容器，避免残留导致下次 max active
        for code in list(active.keys()):
            try:
                await asyncio.to_thread(client.close_challenge, code)
            except Exception:
                pass
        # finally 兜底：重试队列里仍未关闭的容器也尝试关闭
        for code in list(close_pending):
            try:
                await asyncio.to_thread(client.close_challenge, code)
            except Exception:
                pass

    # ④ 终局重扫：主循环退出后再次拉列表，尝试未完成 / stuck 题，防止提前退出漏题
    if TASK_DEADLINE_TS:
        try:
            await _endgame_sweep(
                client, fast_pool, active, attempts, results, charter, task,
                global_plan, hooks, workdir, max_retries=2)
        except Exception as e:
            log_warn(f"[endgame] 终局重扫异常：{str(e)[:200]}")

    # ⑤ 报告者·收尾（后台异步生成战报 + 落 field_notes + 四指标看板，不阻塞主进程结束）
    report_text = await finalize_report(workdir, results, fatal_reason, hooks, DATA_DIR)

    log_info(f"== 跑分结果：{json.dumps(results, ensure_ascii=False)} ==")
    return {"status": "finished", "results": results, "report": report_text}


async def _endgame_sweep(client: PlatformClient, model_pool: ModelPool,
                         active: Dict[str, asyncio.Task], attempts: Dict[str, int],
                         results: List[dict], charter: str, task: str,
                         global_plan: str, hooks, workdir: Path,
                         max_retries: int = 2) -> None:
    """终局重扫：主循环退出后再次拉题目列表，尝试未完成的题或重试 stuck 题。

    - 只有在 `TASK_DEADLINE_TS` 配置且剩余时间 >= DEADLINE_SAFE_MARGIN + 60s 时才执行；
    - 优先选「未完成且未做过」的题，其次选「之前 stuck/outcome 为 stuck 或 suspended」的题；
    - 每道题最多重试 max_retries 次，避免无限循环；
    - 全程同步 start 并等单题跑完，不额外占用并发槽位（重扫阶段保守策略）。
    """
    try:
        ts = float(TASK_DEADLINE_TS)
    except ValueError:
        return
    if time.time() >= ts - DEADLINE_SAFE_MARGIN - 60:
        log_info("[endgame] 剩余时间不足，跳过终局重扫")
        return

    log_info("[endgame] 开始终局重扫：拉取题目列表检查是否漏题...")
    try:
        challenges = await asyncio.to_thread(client.list_challenges)
    except Exception as e:
        log_warn(f"[endgame] 拉取题目列表失败：{str(e)[:200]}")
        return
    if not isinstance(challenges, list) or not challenges:
        return

    # 已收录结果
    done_codes = {r["code"] for r in results}
    # 平台侧未完成的题
    not_completed = [c for c in challenges if not c.get("is_completed")
                     and c.get("unique_code")]
    # 优先顺序：未做过的 > stuck 过的 > 其他
    def _score(c):
        code = c.get("unique_code")
        if code not in done_codes:
            return 0
        r = next((x for x in results if x["code"] == code), None)
        if r and r.get("outcome") in ("stuck", "suspended"):
            return 1
        return 2

    not_completed.sort(key=_score)

    for chal in not_completed:
        code = chal.get("unique_code", "")
        if not code:
            continue
        if time.time() >= ts - DEADLINE_SAFE_MARGIN - 30:
            log_info("[endgame] 剩余时间不足，停止重扫")
            break
        # 超过重试上限不再碰
        if attempts.get(code, 0) >= max_retries:
            continue
        desc = chal.get("description", "") or ""
        difficulty = chal.get("difficulty", "")
        log_info(f"[endgame] 重扫 {code}（已尝试 {attempts.get(code, 0)} 次）")
        try:
            addrs = await asyncio.to_thread(client.start_challenge, code)
        except Exception as e:
            log_warn(f"[endgame] start {code} 失败：{str(e)[:200]}")
            continue
        if not addrs:
            attempts[code] = attempts.get(code, 0) + 1
            continue
        try:
            outcome = await run_single_challenge(
                code, desc, addrs, charter, task, global_plan, hooks, workdir,
                client, difficulty,
                flag_total=chal.get("flag_count") or 1,
                flag_done=chal.get("correct_flag_count") or 0,
                model_pool=model_pool)
            results.append({"code": code, "outcome": outcome})
            if outcome in ("stuck", "suspended", "error"):
                attempts[code] = attempts.get(code, 0) + 1
            log_info(f"[endgame] 单题 {code} 结果：{outcome}")
        except Exception as e:
            log_error(f"[endgame] 单题 {code} 异常：{str(e)[:200]}")
            results.append({"code": code, "outcome": "error"})
            attempts[code] = attempts.get(code, 0) + 1
        finally:
            # 尽力关闭容器释放名额
            for _ in range(3):
                try:
                    if await asyncio.to_thread(client.close_challenge, code):
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)

    try:
        challenges2 = await asyncio.to_thread(client.list_challenges)
        still_unfinished = [c.get("unique_code") for c in (challenges2 or [])
                            if not c.get("is_completed")]
        log_info(f"[endgame] 重扫结束，仍显示未完成：{still_unfinished[:20]}")
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    resume = "--resume" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--resume"]
    task = args[0] if args else build_default_task()
    role_hint = args[1] if len(args) > 1 else ""

    # 比赛连续性保险：未预期异常退出后自动重启（指数退避），避免单点崩溃导致全程退出。
    # 注意：平台 TaskEnded / TaskNotFound 已在 run_task 内部正常终止，不会触发此重启；
    # 只有真正未捕获异常（模型/网络/工具/进程异常）才重启。
    SECAI_MAX_RESTARTS = int(os.getenv("SECAI_MAX_RESTARTS", "5"))
    SECAI_RESTART_DELAY = float(os.getenv("SECAI_RESTART_DELAY", "2"))
    out = None
    for restart in range(SECAI_MAX_RESTARTS + 1):
        try:
            out = asyncio.run(run_task(task, role_hint, resume=resume))
            break  # 正常结束
        except KeyboardInterrupt:
            log_warn("== 已中断 ==")
            sys.exit(130)
        except Exception as e:
            if restart >= SECAI_MAX_RESTARTS:
                log_error(f"== 已达最大重启次数 {SECAI_MAX_RESTARTS}，终止 ==")
                sys.exit(1)
            delay = SECAI_RESTART_DELAY * (2 ** restart)
            log_error(f"== 未预期异常退出（{restart + 1}/{SECAI_MAX_RESTARTS}）："
                      f"{type(e).__name__}: {str(e)[:300]}；{delay:.1f}s 后重启 ==")
            time.sleep(delay)
            # 保留 resume 参数，让重启后尽可能复用现场（field_notes / 平台状态）
            resume = True

    if out is not None:
        log_info(f"最终状态：{json.dumps({k: v for k, v in out.items() if k != 'report'}, ensure_ascii=False)}")
