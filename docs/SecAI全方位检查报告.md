# SecAI 全方位检查报告（智能体 / 运行逻辑 / 代码质量）

> 基线：commit 84c5fba。本文与《SecAI工程化修补方案》互补：
> 那份管"代码组织与可维护性"，这份管**"逻辑正确性与智能体行为"**。
> 本次新发现 2 个得分级逻辑 bug（D1、D2），务必优先修。
> 分级：P0 = 直接丢分/错误结论；P1 = 行为漂移/资源浪费；P2 = 卫生。

---

## 第一部分 智能体层（定义、角色、工具权力）

### D1（P0）hint 方向锁"只上锁、不解锁"——转化成功后仍在锁死

**事实链**：
- `ctx.hint_grace_active` 全仓库只有两处赋值：初始 `False`（main.py:519）、看 hint 时 `True`（main.py:739）；
- `_score_tool_result`（hooks.py:423）中方向锁排在**铁证检查之前**：锁定期内输出不含 hint 关键词 → 直接 return 0，**连 `flag{` 都不例外**；
- 没有任何"hint 转化成功后解除锁定"的路径。

**后果**：看 hint 后哪怕顺利转化、进入 exploit 阶段，后续所有工具输出（包括读到 flag 的那条）只要不含 hint 关键词就判零增量 → `zero_gain_turns` 持续上涨 → 5 轮触发 `hint_stale` **机械换题，在即将拿分时被系统自己拽走**。机械提交能兜住 flag 本身，但"读 flag 文件→提交"之间的推进轮全部被判零，多 flag 题的第二面几乎必死。

**修复**（两处）：
```python
# ① core/hooks.py _score_tool_result：铁证永远优先于方向锁
#    把 flag/correct/vulnerable 铁证块挪到 hint 锁之前；
# ② hint 相关增量达成时解锁：
if getattr(ctx, "hint_grace_active", False):
    hint_dir = ctx.blackboard.get("hint_directive", {}).get("value", "")
    kws = _extract_hint_keywords(hint_dir)
    if kws and any(k.lower() in low for k in kws):
        ctx.hint_grace_active = False   # 命中 hint 方向 → 已转化，解锁
    elif kws:
        return 0
# 铁证检查放最前，flag{ 输出无条件 +1
```
**验收**：构造"hint→转化→读 flag（输出无 hint 词）"回放，flag 轮 score 必须为 1，且不触发 hint_stale。

### A2 遗留（P0）外层 Agent 可变单例
`strategist_agent.model = ...` / `reporter_agent.model = ...`（main.py:941-942）改全局实例，并发下互踩。修复见《工程化修补方案》修补 2（工厂化），此处不再展开。

### A5（P1）"软约束纪律"无机械兜底
Executor 纪律 9 条、Strategist"只读工具最多一次"、Reporter"不评价不抒情"——全部是 prompt 文字。模型违反时无代价。能机械化的必须机械化：
- Strategist 只读工具调用次数：hooks 计数，>1 次直接返回 `{"error": "情报工具配额已用完，请立即产出宪章"}`；
- Reporter 输出格式：代码校验必须含 `## 战报` 与 `## 死路蒸馏` 两节，缺节重试一次后落原文并记 `silent_failures`。

### A6（P1）角色增强触发器每事件全量加载
`_boost_role_by_trigger`（hooks.py）在**每个工具结果事件**里调 `load_roles()`，role_registry 无任何缓存（grep 无 lru_cache/模块级缓存）——每题上百次工具调用 = 上百次磁盘 IO + YAML/md 解析。
**修复**：`load_roles` 加 `@lru_cache(maxsize=1)`（角色文件运行时不变）；或在 hooks 初始化时快照一份。

### A7（P2）Executor prompt 职责堆叠
静态模板 = 角色 style + 9 条纪律 + 任务书 + preset 后缀 +（子任务再叠 SUBTASK_ENDING）。纪律 9 条里第 4 条（批量探测）与 preset 的 exploit_focused"不要发散"语义重叠。建议纪律压到 6 条以内，preset 与纪律去重——prompt 越长，关键条目的注意力权重越薄（这就是惰性根因之一：上下文稀释）。

---

## 第二部分 运行逻辑层（主循环、评分、子任务、调度）

### D2（P0）增量打分关键词过宽——"admin/root/session=" 见啥都算进展

**事实链**（hooks.py）：
- 铁证表含 `"session="`、`"响应存在差异"`；
- `_BEHAVIOR_DIFF_HINTS` 含 `"admin"`、`"root"`、`"secret"`、`"internal"`、`"localhost"`、`"id\n"`；
- 交互类工具（shell/http_request）输出命中任一即 +1。

**后果**：几乎所有正经 Web 页面都含 "admin"（导航/页脚/注释）、"localhost"（报错堆栈）、"session="（Set-Cookie）。**zero_gain_turns 被永久稀释，空转熔断（3 轮破局）与 hint_stale（5 轮换题）双双失效**——系统失去"承认卡住"的能力，表现为一道题磨到墙钟超时。这与 D1 叠加更糟：hint 锁期间"假增量"不存在（锁内判 0），锁外假增量泛滥，两个方向的判定都失真。

**修复**（收紧 + 上下文绑定）：
```python
# ① 从铁证表删除 "session="、"响应存在差异"（太宽）
# ② _BEHAVIOR_DIFF_HINTS 改为"证据对"：弱词必须与强信号同现
_WEAK_HINTS = ("admin", "root", "secret", "internal", "localhost")
_STRONG_HINTS = ("syntax error", "mysql", "postgresql", "ORA-", "pg_sleep",
                 "whoami", "__destruct", "gadget", "flag{", "uid=")
if tool in ("shell", "http_request"):
    if any(k in low for k in _STRONG_HINTS):
        return 1
    if any(k in low for k in _WEAK_HINTS) and "error" in low:
        return 1   # 弱词需 error 同现才算行为差异
```
**验收**：用历届真实日志回放打分，统计每题 `zero_gain_turns` 分布——修复前应有大量题"全程零零增量"（假），修复后分布应出现真实的停滞段。

### R1（P1）四套破局机制并存，触发条件互相打架
卡壳干预现有：**fork_analyze/replan（3 轮）→ coach（hint 后 N 轮）→ plan-mode（再 3 轮）→ 干预上限换题**。四套各自重置 `zero_gain_turns`，实际效果是：一道真死题要熬 3+5+3+... ≈ 15+ 轮才被放弃，而墙钟只有 10~25 分钟——**破局机制的总时长可能超过题目预算本身**。

**修复**：收敛为两级——
1. 3 轮零增量 → fork_analyze 一次（产 next_directive，写黑板）；
2. directive 后 3 轮仍零增量 → 直接机械换题（不再 coach、不再 plan-mode）。
coach 与 plan-mode 代码保留但默认关闭（`ENABLE_COACH=false`），赛后用日志对比决定是否复活。

### R2（P1）子任务情报"运行期不可见"
`sub_ctx.blackboard = copy.deepcopy(ctx.blackboard)`（main.py:246）：子任务拿快照起跑，运行期间发现的凭证/漏洞确认，**主任务要等子任务结束才以 `subtask:<id>` 一条形式可见**。若子任务跑 5 分钟，主线这 5 分钟在重复子任务已排除的方向。

**修复**：子任务的 `blackboard set` 工具改为写**共享黑板文件**（challenge_workdir/blackboard.json，加文件锁），主任务每轮 `_build_dynamic_context` 前重读合并（只增量合并 `verified` 条目，防伪证回流）。深拷贝保留为启动快照。

### R3（P1）子任务继承父阶段导致语义错位
`sub_ctx.phase = ctx.phase`（main.py:247）：父任务在 exploit 阶段 spawn 的"侦察 8080 端口"子任务，一出生就在 exploit 阶段——recon 阶段纪律（先指纹后 payload）不生效，阶段推进逻辑（set_phase 切换条件）引用的是父战场状态。
**修复**：子任务 phase 强制从 `recon` 起跑：`sub_ctx.phase = "recon"`；子任务上下文小，阶段推进反而比父任务更真实。

### R4（P2）子任务 flag 记账双轨
子任务自己的 `_submit_flags_if_any` 已机械提交（sub_ctx.current_code 已继承，路径正确）；结束后又把 flag append 进父 `ctx.correct_flags`（main.py:312）——父任务 finalize 时按 correct_flags 数旗，与平台 `correct_flag_count` 可能不一致。
**修复**：父侧不再 append 子任务 flag，改为 finalize 前以平台 `correct_flag_count` 为唯一真相源复核一次（现有 `_is_completed` 已这么做，把 correct_flags 仅用于战报展示）。

### R5（P2）`stuck_turns` 与 `zero_gain_turns` 双停滞计数
`_post_step` 开头按 phase 是否变化维护 `stuck_turns`，但后续所有决策都用 `zero_gain_turns`——`stuck_turns` 是死代码（仅日志）。删除或改作 heuristics 输入，不要留着让读者以为它参与决策。

### R6（P2）闭环正则误报面
业务逻辑闭环触发器 `(coupon|discount|price|amount|balance...)[\s:=]+[\w.-]+` 会命中**任何含 price 字段的 JSON 响应**（电商类题的正常浏览响应也算）→ 强制闭环指令轰炸，稀释"[闭环]优先级最高"的权威性。
**修复**：闭环指令每题每类最多发 2 次（`_already` 已有，把键加上触发类型前缀并限频）；且触发后 3 轮内未产生对应证据则自动降权该类触发器。

---

## 第三部分 代码质量层（与工程化方案互补的新发现）

| # | 级别 | 问题 | 证据 / 修复 |
|---|---|---|---|
| Q1 | P1 | **events 缓冲 atexit 刷盘在异常退出时丢失**：`_flush_emit_buffer` 依赖 atexit，但被 kill -9 / 容器 OOM 时 100 条缓冲全丢——而崩溃现场恰恰是最需要事件流的时候 | hooks.py `_EMIT_BUFFER`；修复：阈值降到 20 条 + 关键事件（flag/death/compact）立即刷盘绕过缓冲 |
| Q2 | P1 | **`_prompt_hashes` 基线只存不判**：EventStreamHooks 里维护了 per-agent prompt hash 基线，但 hooks.py 中未见 drift 告警（断言在 main.py 补丁里）——两处防线口径不一，hooks 侧是哑的 | hooks.py `__init__`；修复：hooks 内 on_llm_start 比对基线，漂移即 WARN（与 main 侧 cache-guard 双保险） |
| Q3 | P2 | **hooks.py 701 行承担 5 种职责**：事件投影、日志打印、技能披露、角色增强、增量打分、payload 台账、缓冲落盘 | 拆 `core/scoring.py`（打分+台账）与 `core/events_sink.py`（缓冲+落盘），hooks 只做编排 |
| Q4 | P2 | **`_clip`/`_is_error_result` 等工具函数与业务逻辑混居**，无法独立单测 | 随 Q3 一起搬走，test_core.py 补打分用例（配合 D2 回归） |
| Q5 | P2 | **正则编译散落模块级，部分在函数内重复编译**（如 `_extract_hint_keywords` 内 `re.findall` 用字面量） | 统一模块级预编译；性能影响小但热路径上不该有 |

> 工程组织类问题（main.py 巨石、demo_tools 上帝模块、session 不 close、docstring 失实、测试薄弱）已在《SecAI工程化修补方案》中列出，本报告不重复，执行时两份文档合并排期。

---

## 第四部分 修复优先级与排期

| 序 | 项 | 级别 | 工作量 | 理由 |
|---|---|---|---|---|
| 1 | **D1 hint 锁不解除 + 铁证前置** | P0 | 0.5 天 | 正在丢分：多 flag 题第二面被杀 |
| 2 | **D2 打分关键词收紧** | P0 | 0.5 天 + 日志回放验证 | 熔断体系的总闸门失灵 |
| 3 | R1 破局机制收敛为两级 | P1 | 0.5 天 | 单题死磕时长对齐墙钟预算 |
| 4 | A6 角色加载缓存 | P1 | 10 分钟 | 热路径 IO，纯赚 |
| 5 | R2 子任务共享黑板 | P1 | 0.5 天 | 消灭主子重复劳动 |
| 6 | R3 子任务 phase 复位 | P1 | 5 分钟 | 一行修复 |
| 7 | A5 软约束机械化 | P1 | 0.5 天 | 防纪律漂移 |
| 8 | Q1 关键事件立即刷盘 | P1 | 1 小时 | 崩溃现场保护 |
| 9 | Q2 hooks 侧 hash 双保险 | P2 | 0.5 小时 | 防线冗余 |
| 10 | R4/R5/R6/Q3-Q5 | P2 | 1 天 | 卫生批次，随下次重构顺手 |

**D1 + D2 合计 1 天，直接决定熔断体系是"真刹车"还是"装饰"。** 建议修复后用上一场完整 events.jsonl 做回放验证：把打分函数抽成纯函数（它本来就是零 LLM 的），对历史工具输出重算 `zero_gain_turns` 序列，对比实际换题时点——这是零成本的行为回归测试。

## 验收总清单

- [ ] hint 转化后 `hint_grace_active` 复位；flag 输出在锁定期内仍判铁证 +1
- [ ] 回放历史日志：`zero_gain_turns` 分布出现真实停滞段，无"全程假进展"题
- [ ] 卡壳干预链 = fork_analyze → 3 轮 → 换题，总耗时 < 单题墙钟预算的 1/3
- [ ] `load_roles` 全场只读盘一次
- [ ] 子任务运行期发现的情报主线下一轮可见
- [ ] kill -9 后 events.jsonl 保留崩溃前最后 20 条内的事件
- [ ] 打分函数可从 hooks 独立 import 并对历史日志回放
