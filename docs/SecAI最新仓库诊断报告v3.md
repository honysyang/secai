# SecAI 最新仓库诊断报告 v3

> 分析对象：https://gitee.com/yzj1/secai.git @ commit `998fc4a`（feat(runtime): 自救时压缩上下文并注入摘要）
> 分析日期：2026-08-14
> 总体评价：包化重构质量不错，修复手册 v2 的 R1–R4、R6 已扎实落地，模型灾备池是真实战力提升。但仍存在 2 个会直接丢分的 P0 漏洞、5 个 P1 结构隐患，提分手册 S1–S8 基本未落地。

---

## 一、已修复确认（对照修复手册 v2）

| 条目 | 状态 | 证据位置 |
|---|---|---|
| R1 通关机械出口 | ✅ 已落地 | `demo_tools.py` `_submit_flags_if_any`：correct=true 后读 `correct_flag_count/total_flag_count`，未满提示继续，满了机械复核 `_is_completed` → 置 `finalized` |
| R2 信息增量判定收窄 | ✅ 已落地 | `core/hooks.py` `_score_tool_result` v2：状态码仅限 `_ENUM_TOOLS={run_tool,fuzz,parallel_shell}` 且 ≥2 个不同码才算；敏感文件 / 新路径走 `seen_signatures` 去重 |
| R3 铁律异常上抛 | ✅ 已落地 | `_submit_flags_if_any` 对 `(TaskEnded, TaskNotFound)` 置 `ctx.fatal` 并 `raise`，与 platform_tools 行为对齐 |
| R4 按题检索沉淀 | ✅ 已落地 | `app/main.py` `_append_mechanical_note`（零 LLM 题级沉淀）+ `load_notes_for(code)` 按前缀检索最近 3 段 |
| R6 纪律 16→8 条 | ✅ 已落地 | `core/agents_def.py` 工作纪律正好 8 条 |
| 新机制：多模型灾备池 | ✅ 新增亮点 | `runtime/model_pool.py`：额度/限流/鉴权失败自动切换候选模型、保持同一 SQLiteSession 会话连续 |
| 新机制：模型惰性治理 | ✅ 新增亮点 | `runtime/stuck.py` StuckDetector：优先单模型自救换思路（含上下文压缩），自救用尽再切换模型接管 |
| 新机制：教练软干预 | ✅ 新增亮点 | `app/main.py` `_coach`：hint 后仍零增益 3 轮，给 1~2 条具体方向（每题仅 1 次，控制成本） |

---

## 二、P0：会直接丢分的问题（必须先修）

### P0-1　子任务的提交铁律失明

**现象**：`_run_subtasks`（`app/main.py`）创建子任务上下文 `sub_ctx` 时，只复制了 `disclosed_skills / blackboard / token_usage / enabled_tools / phase / plan`，**没有设置 `current_code`**。

**后果链**：
1. 子任务会话中工具输出命中 flag → 触发 `_submit_flags_if_any`；
2. 铁律第一道闸门 `code = c.current_code; if not code:` → 直接返回「请调用 submit_flag 手动提交」；
3. 子任务拿到的 flag 全部依赖 LLM 自觉手动提交——铁律在最需要它的并发路径上被绕过；
4. 另外 `submitted` 集合未共享，主/子任务可能对同一 flag 重复提交（平台 duplicate 幂等兜底，但浪费一轮）。

**修复（`app/main.py` `_run_one` 内，`sub_ctx` 创建后加两行）**：

```python
        sub_ctx = TaskContext(
            workdir=workdir,
            disclosed_skills=list(ctx.disclosed_skills),
            task=ctx.task,
            charter=ctx.charter,
            role=ctx.role,
        )
        sub_ctx.current_code = ctx.current_code   # ★ 铁律复明：子任务也能机械提交
        sub_ctx.submitted = ctx.submitted          # ★ 共享已提交集合，防重复提交
        sub_ctx.blackboard = ctx.blackboard
        sub_ctx.token_usage = ctx.token_usage
```

> 注意：`TaskContext` 的 `submitted` 若是 `dataclass field(default_factory=set)`，直接赋值引用即可共享；同时确认 `correct_flags` 也共享（`sub_ctx.correct_flags = ctx.correct_flags`），否则子任务通关后主 ctx 的 flag 计数不准。

### P0-2　LLM 版 finalize 仍是伪通关出口

**现象**：R1 只堵住了铁律路径。`demo_tools.py` 的 `finalize` 工具依然是：

```python
c.finalized = True
c.final_payload = {"findings": findings}
```

主循环检测到 `ctx.finalized` 即 `outcome = "solved"`——**没有平台复核**。多 flag 题拿 1 面后 LLM 自觉 finalize，会永久丢失剩余面数（`is_completed=false` 的题虽然还会被调度器选回，但 `attempts` 不增、且黑板回注重打一遍，效率与分数双损）；更坏的情况是 LLM 在证据枯竭时 finalize「投降」被记成 solved，战报失真。

**修复（`demo_tools.py` `finalize` 工具替换为复核版）**：

```python
@function_tool
def finalize(ctx: RunContextWrapper[TaskContext], findings: str = "") -> str:
    """当你认为任务已完成（目标达成或证据枯竭）时调用，提交最终结论并结束本次执行。

    系统会机械复核平台通关状态：未通关则拒绝结束并回注提示。
    """
    c = ctx.context
    # ★ 机械复核：有题码且有平台凭证时，以平台 is_completed 为唯一通关依据
    if c.current_code and BENCHMARK_BASE_URL and BENCHMARK_TOKEN:
        try:
            done = _is_completed(_platform(), c.current_code)
        except (TaskEnded, TaskNotFound):
            c.fatal = "task_ended"
            raise
        except Exception:
            done = None  # 复核失败不阻断（网络抖动），但标注未确认
        if done is False:
            fc, tc = len(getattr(c, "correct_flags", [])), "?"
            return (f"[系统·复核拒绝] 平台确认本题尚未通关（已拿 {fc}/{tc} 面 flag）。"
                    "finalize 被拒绝：请继续攻击，或在证据彻底枯竭时说明理由后重试。")
    c.finalized = True
    c.final_payload = {"findings": findings}
    return json.dumps({"finalized": True, "findings": findings}, ensure_ascii=False)
```

> 配套：主循环里 `outcome = "solved"` 前可加一道断言——`ctx.final_payload` 来自铁律通关判决的标记（如 `payload.get("_platform_confirmed")`）才计 solved，否则计 `stopped` 并进 `attempts` 降权。

---

## 三、P1：结构残留与新机制隐患

### P1-1　`stop_policy.should_stop` 已成死代码

`app/main.py` 只 import 了 `TASK_DEADLINE_TS / DEADLINE_SAFE_MARGIN` 两个常量，判停逻辑（空转/零增量/fatal/finalize）全部在 while 循环里内联重写了一遍。两份判停逻辑并存，下次改阈值必然漂移。

**处置**：二选一——
- （推荐）删除 `should_stop` 函数，保留常量定义，`stop_policy.py` 改名 `deadline.py`；
- 或把主循环的内联判停收拢回调用 `should_stop(ctx, turn_count, total_chars)`。

### P1-2　模型灾备切换存在静默失败窗口

`is_model_failure` 维度 2 靠异常文本匹配（`" 401" in msg`、`"429:" in msg`）。百度等兼容网关若把错误包装成 `BadRequestError`（HTTP 400，如「模型不存在」「参数错误」类），不会被识别，异常直接 `raise` 杀死单题（outcome=fatal 之外的未捕获路径）。

**处置**：
```python
# 维度 2 补充：400 且消息含模型/配额关键词时也视为模型失败
if isinstance(status_code, int) and status_code == 400:
    if any(k in msg for k in ("model", "quota", "额度", "不存在", "not exist", "invalid")):
        return True
```
同时在 `_run_single_challenge` 的兜底 `except Exception` 处补一行日志分类（模型类/平台类/工具类），便于赛后归因。

### P1-3　自救压缩后黑板不回注，可能重蹈覆辙

`stuck.py` 自救把 `phase` 重置为 recon，`compact_session` 清空 session 注入摘要——但摘要由 compactor_agent 生成，**不保证覆盖黑板里的关键结论**（已拿到的凭据、已排除的死路、已确认的入口）。压缩后 Agent 等于「失忆重侦察」，可能把已排除路径再走一遍，白白消耗 token。

**处置**：压缩后的注入文本强制拼接黑板快照（零 LLM 部分）：
```python
# compact_session 成功后，在 next_input 前部注入：
bb_snapshot = json.dumps(
    {k: v for k, v in ctx.blackboard.items()
     if isinstance(v, dict) and v.get("status") in ("done", "failed")},
    ensure_ascii=False)[:1500]
next_input = f"[已确认/已排除结论快照，禁止重复]\n{bb_snapshot}\n\n" + stuck_action.next_input
```

### P1-4　EV 调度无耗时因子（提分手册 S1 未落地）

`scheduler.select_challenge` 仍是 `total_score × 难度系数 × 0.3^attempts`。hard 500 分题平均耗时可能是 easy 100 分题的 8 倍，实际期望得分率反而更低，EV 系统性高估难题。`adapters/db.py` 也没有耗时统计表。

**处置**（S1 落地，详见《SecAI提分实战策略实施手册》）：
```python
# db.py 增加：
#   CREATE TABLE IF NOT EXISTS challenge_duration(...)
#   每次 task_finished 记录耗时
# scheduler.select_challenge 改为：
ev = score * coef * (DECAY ** attempts.get(code, 0)) / max(est_minutes, 5)
```

### P1-5　子任务事件流写入全局 workdir，排障困难

`_run_subtasks` 里 `EventStreamHooks(workdir, f"sub_{sub['id']}")` 用全局 workdir，而单题用 `challenge_workdir`——子任务轨迹与父题事件流分离，监控页无法按题串联。

**处置**：`_run_subtasks` 签名加 `challenge_workdir` 参数，hooks 与子任务 session 均落到 `challenge_workdir / f"sub_{sub['id']}"`。

---

## 四、P2：提分策略落地情况盘点

| 策略 | 内容 | 状态 | 缺口 |
|---|---|---|---|
| S1 耗时因子 | EV ÷ max(est,5) | ❌ | 见 P1-4 |
| S2 多 flag 黄金窗口 | correct 且 fc<tc 写黑板 `__priority__` | ❌ | 无 `__priority__` 机制；铁律只在 note 里提示「继续找下一面」，调度器层面无优先回访 |
| S3 flag 定位清单 | 切 post/exploit 阶段注入 `__flag_hunt__` | ❌ | hooks 无阶段切换钩子注入 |
| S4 POC 前缀复利 | remember 带前缀标签 + pocs_for_prefix | ⚠️ 半落地 | `load_solution_hint` 已有；但 `remember` 沉淀未带前缀标签，无法按前缀检索 POC |
| S5 hint 经济账 | total_score 参与决策 | ⚠️ 半落地 | `should_pull_hint_by_budget` 只看 token 档位比例，未算「hint 扣分 vs 题目分值」的账 |
| S6 残局收割 | endgame 过滤 easy/medium 或部分通关题 | ❌ | 无 `ENDGAME_MINUTES` 机制 |
| S7 e3 打磨窗口 | finalized 后撤销给 5 轮打磨 | ❌ | e3 双轨评分题无二次打磨通道 |
| S8 按需换脑 | 按 phase+brain_role 选模型 | ❌ | `ModelPool` 只按灾备/惰性切换；`brain_role` 标签已打但无人读 |

另：`MAX_SLOTS = 6` 与平台 3 活跃容器上限不符——虽有 `container_busy` 自适应兜底不会出错，但每次启动多发 3 个注定被拒的 start 请求，且启动清理逻辑白跑。建议改为 `MAX_SLOTS = 3`（或读环境变量 `PLATFORM_MAX_ACTIVE`）。

---

## 五、修复优先级与工作量估算

| 顺序 | 条目 | 改动量 | 收益 |
|---|---|---|---|
| 1 | P0-1 子任务铁律复明 | 3 行 | 并发路径 flag 不再依赖 LLM 自觉，直接止损 |
| 2 | P0-2 finalize 机械复核 | ~20 行 | 堵死伪通关，多 flag 题不再丢面 |
| 3 | P1-4 EV 耗时因子（S1） | db 一张表 + 调度一行 | 单位时间得分率提升，性价比最高的提分项 |
| 4 | P1-3 自救压缩注黑板 | ~5 行 | 自救不再失忆重侦察 |
| 5 | P1-2 灾备 400 识别 | ~4 行 | 网关兼容性兜底 |
| 6 | P1-1 死代码清理 | 删 40 行 | 防阈值漂移 |
| 7 | P1-5 子任务事件流归位 | 签名加 1 参 | 可观测性 |
| 8 | S2/S6/S7/S8 | 各 20~40 行 | 按赛程阶段择需落地（残局收割 S6 建议赛前必上） |

---

## 六、五公理对照复检

| 公理 | 本版本表现 |
|---|---|
| ① 提交是机械保证 | ⚠️ 主路径已铁律化，但子任务路径失明（P0-1）、finalize 伪出口（P0-2） |
| ② 调度是纯函数零 LLM | ✅ 选题/hint/换题/容器 SOP 全部代码决策 |
| ③ LLM 只做生成假设+解读结果 | ✅ 教练/replan 定位清晰，未越权 |
| ④ 状态全外置、进程 disposable | ✅ 黑板/事件流/session/档案全部落盘，挂起回注已通 |
| ⑤ 观测是状态投影 | ⚠️ 子任务事件流与父题分离（P1-5）；死代码 stop_policy 干扰阅读（P1-1） |
