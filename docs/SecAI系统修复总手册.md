# SecAI 系统修复总手册（完整版）

> 适用代码：gitee.com/yzj1/secai.git @ commit `3d5083a`
整合范围：稳定性补丁 v2 + 效率六刀（已实施：错误提交熔断纠偏、槽位泄漏双救援、easy 墙钟续命、hint 法令化、动静分离、run_batch、首轮突击、spill 阈值提升、缓存命中率观测）+ 子任务生命周期 + 利用工作台
理论框架：得分 = 模型智力 × harness 兑现率 × 确定性转化率 × 状态累积率
> 用法：按批次顺序执行，每批次附验收标准

---

## 第 0 章　修复总纲

系统当前状态：模型智力在线（快题 2 分钟 800 分实证）、武器库全场最厚（22 篇 wiki + 技能库）、架构健康（三智能体 + 调度器 + 铁律）。失分集中在三个工程因子：

| 因子 | 现状 | 对应批次 |
|---|---|---|
| harness 兑现率 | 缓存命中 0%、一轮一工具、每轮 35k token | 批次二 |
| 确定性转化率 | 闭环半通、hint 无法令化、错交熔断误杀 | 批次一 |
| 状态累积率 | 子任务无遗产回收、payload 无台账、压缩丢关键状态 | 批次三 |

---

## 批次一　稳定性与正确性（不改会丢分，半天）

### 1.1 错误提交熔断改「无证据连续失败」才熔断 ⭐必改

**问题**：现行 `wrong_submit_count >= 3 → stuck` 会误杀已验证得分模式（bctf-13 六连败后命中 +800）。

**`app/main.py` 主循环熔断段替换**：

```python
            # 错误提交熔断：连续错交 且 期间无任何信息增量，才算恋战
            if ctx.wrong_submit_count >= 6 and ctx.zero_gain_turns >= 3:
                log_warn(f"[skip] 单题 {code} 连续 {ctx.wrong_submit_count} 次错交且无新证据，机械换题")
                outcome = "stuck"
                break
```

**`demo_tools.py` `_submit_flags_if_any` 与 `bench_platform/platform_tools.py` `submit_flag` 的错交计数处加豁免**：

```python
        if not r.get("correct"):
            c.wrong_submit_count += 1
            if getattr(c, "turn_gain", False):   # 有正向证据 = 有效推进，重置
                c.wrong_submit_count = 0
            continue
```

### 1.2 槽位泄漏双救援

**`app/main.py` 主循环拉题目列表成功后，加状态对齐**：

```python
            # 平台侧 running 但本地无记录 = 泄漏槽位，连续 3 轮存在即机械关闭
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
                        except Exception:
                            pass
                    leak_streak = 0
            else:
                leak_streak = 0

            # close 失败重试队列
            for cc in list(close_pending):
                try:
                    if await asyncio.to_thread(client.close_challenge, cc):
                        close_pending.discard(cc)
                        log_info(f"[close-retry] {cc} 已关闭，槽位回收")
                except Exception:
                    pass
```

（主循环 try 前初始化 `leak_streak = 0`、`close_pending: set = set()`；完成单题处 close 三次失败改 `close_pending.add(code)`；finally 段追加遍历 close_pending 关闭。）

### 1.3 easy 墙钟加进度续命

```python
            elapsed = time.monotonic() - ctx.challenge_start_ts
            if elapsed >= ctx.wallclock_budget:
                if ctx.zero_gain_turns < 5 and not getattr(ctx, "_wallclock_extended", False):
                    ctx._wallclock_extended = True
                    ctx.wallclock_budget += ctx.wallclock_budget // 2
                    log_info(f"[extend] 单题 {code} 有进展，墙钟延长半档至 {ctx.wallclock_budget}s")
                else:
                    log_warn(f"[skip] 单题 {code} 墙上时间超预算，机械换题")
                    outcome = "stuck"
                    break
```

### 1.4 hint 法令化（冲分核心杠杆）

**`app/main.py` hint 分支替换**：

```python
HINT_GRACE_TURNS = 5

            if action == "hint":
                try:
                    hint = await asyncio.to_thread(client.get_hint, code)
                except Exception as e:
                    hint = f"（获取提示失败：{str(e)[:120]}）"
                hint_used = True
                ctx.zero_gain_turns = 0
                ctx.blackboard["hint_directive"] = {
                    "value": hint, "status": "confirmed",
                    "ts": int(time.time()), "verified": True,
                    "evidence": "platform_hint"}
                ctx.hint_grace_active = True
                log_info(f"  [hint] 单题 {code} 看提示（已写入 hint_directive）")
                next_input = (f"【系统法令】平台提示具有最高优先级：\n{hint}\n\n"
                              f"接下来 {HINT_GRACE_TURNS} 轮你的每个动作必须直接验证该提示的断言，"
                              f"与提示无关的侦察将被判为零增量。")
                continue

            # hint 熔断（decide_stuck_action 判定之前）
            if hint_used and ctx.zero_gain_turns >= HINT_GRACE_TURNS:
                log_warn(f"[hint-stale] 单题 {code} hint 后 {HINT_GRACE_TURNS} 轮无转化，换题")
                outcome = "stuck"
                break
```

**`core/hooks.py` `_score_tool_result` 开头加方向锁**：

```python
def _extract_hint_keywords(hint: str) -> list:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_.\-]{3,}", hint or "")
    stop = {"this","that","with","from","what","does","the","and","you"}
    return [w for w in set(words) if w.lower() not in stop][:8]

# _score_tool_result 函数体最前：
    if getattr(ctx, "hint_grace_active", False):
        hint_dir = ctx.blackboard.get("hint_directive", {}).get("value", "")
        kws = _extract_hint_keywords(hint_dir)
        if kws and not any(k.lower() in text.lower() for k in kws):
            return 0   # 与 hint 无关的输出计零增量 → 方向上锁
```

**压缩保护**：`runtime/stuck.py compact_session` 摘要注入时强制前置黑板快照（含 hint_directive）：

```python
bb_snapshot = json.dumps(
    {k: v for k, v in ctx.blackboard.items()
     if isinstance(v, dict) and v.get("status") in ("done", "confirmed", "failed")},
    ensure_ascii=False)[:1500]
summary_inject = f"[已确认/已排除结论快照，禁止重复]\n{bb_snapshot}\n\n{summary}"
```

### 批次一验收

- ☐ 越权快题连错 5 次但有新路径发现 → 不熔断；连错 6 次且零增量 → stuck
- ☐ 平台留 1 个 running 残留容器启动 → 日志出现 [leak]，第 3 轮机械关闭
- ☐ 看 hint 后 3 轮调无关工具 → 零增量累计（方向锁生效）
- ☐ easy 题第 9 分钟仍有新证据 → 出现 [extend]，不判 stuck

---

## 批次二　效率革命（借鉴 DeepSeek Harness，一天）

### 2.1 system prompt 动静分离 ⭐收益最大（已实施）

**原理**：前缀缓存只认逐字节相同的前缀。当前黑板/阶段/技能全在 system prompt 里每轮重建 → 命中 0%、每轮 35k 全量重算。DSH 实测 90% 命中靠的就是前缀稳定 + 轨迹只追加。

**实现位置**：`core/agents_def.py` + `app/main.py`

- `EXECUTOR_STATIC_INSTRUCTIONS`：开题一次性渲染静态字符串（角色/纪律/任务书），不再每轮重建。
- `_build_dynamic_context`：每轮把阶段/宪章/计划/已解锁打法/历史档案/压缩摘要/黑板/阶段增强拼成 `【动态上下文】` 前缀，注入 `Runner.run` 的 input。
- `app/main.py` 主循环每轮调用：
  ```python
  dynamic_ctx = _build_dynamic_context(
      RunContextWrapper(context=ctx), charter, ctx.plan or global_plan,
      field_notes, role_boost=getattr(ctx, "role_boost", ""))
  full_input = f"{EXECUTOR_DYNAMIC_PREFIX}{dynamic_ctx}\n\n{next_input}"
  await Runner.run(executor, input=full_input, ...)
  ```

**剔除易变字段**：`_format_blackboard` 已删时间戳列；技能拼接由 `disclosed_skills` 顺序决定；uuid/随机数/耗时禁入静态文本。

### 2.2 工具门控改逻辑开关（已实施简化版）

**问题**：`tools` schema 数组在缓存前缀内，中途用 `is_enabled` 动态开关会改变 schema，导致前缀缓存失效。

**实现位置**：`demo_tools.py`

- 删除 `_apply_tool_gating` 对 `is_enabled` 的动态赋值，工具 schema 恒定挂载；
- 保留 `_tool_gate` 逻辑闸用于兼容旧按需加载逻辑；
- `build_default_tools` 默认一次性挂齐 `platform/vpn/seccli/web/poc/vuln/knowledge` 全部组，减少运行时 `enable_tool` 调用，前缀缓存更稳定；
- 执行者工作纪律已强调 fuzz / run_batch 优先，降低工具数量对注意力的干扰。

```python
def build_default_tools(groups=("platform", "vpn", "seccli", "web", "poc", "vuln", "knowledge")) -> set:
```

### 2.3 run_batch：PTC 程序化工具调用（已实施）

实现位置：`demo_tools.py` / `app/main.py`

```python
@function_tool
def run_batch(ctx: RunContextWrapper[TaskContext], script: str, timeout: int = 120) -> str:
    """程序化批量探测：一个脚本内部完成「枚举→筛选→追加验证」多步逻辑，只返回结论。"""
```

**首轮突击包**（`app/main.py` `_first_strike`）：开题零 LLM 机械预侦察常见入口路径/状态码/标题/Server，结果直接注入首轮回合，省掉一轮纯侦察。

### 2.4 spill 截断阈值提升（已实施）

`demo_tools.py _spill_output`：阈值从 800 提到 **4000 字符**——日志实证 878 字符的输出被截断导致 read_artifact 双倍往返。只有真正的大输出（扫描/源码）才走 artifacts。

### 2.5 攻坚换强脑 + V4 对齐 + 并行封印实测（已实施）

实现位置：`runtime/model_pool.py` + `app/main.py` + `core/agents_def.py`

- **ModelPool 按 role 切换**：`preferred_name` 支持按 `role`（如 `fast`/`strong`）或 `name` 匹配；新增 `switch_to_role(role)` 方法。
- **攻坚换强脑**：`app/main.py` 主循环检测到 `ctx.phase == "exploit"` 且未升级过，自动调用 `model_pool.switch_to_role("strong")`，把执行者切到 `role=strong` 模型（如 V4-Pro），并强制开启 `parallel_tool_calls=True`。
- **并行封印**：`EXECUTOR_SETTINGS` 默认跟随环境变量 `EXECUTOR_PARALLEL`；进入 exploit 后无论环境变量如何都强制开启并行工具调用（只对 V4 等对齐好的模型安全）。`EXECUTOR_PARALLEL=true` 时全局默认也开启。
- **兜底**：若 `.env` 未配置 `role=strong` 或 strong 模型不可用，则保持当前模型，不影响跑分。

```python
# app/main.py
if ctx.phase == "exploit" and not getattr(ctx, "_brain_upgraded", False):
    strong_entry = model_pool.switch_to_role("strong")
    if strong_entry is not None:
        executor.model = strong_entry.model
        executor.model_settings.parallel_tool_calls = True
        ctx._brain_upgraded = True

# runtime/model_pool.py
def switch_to_role(self, role: str) -> Optional[ModelEntry]: ...

# core/agents_def.py
_EXECUTOR_PARALLEL = os.getenv("EXECUTOR_PARALLEL", "").lower() in ("1", "true", "yes")
EXECUTOR_SETTINGS = ModelSettings(..., parallel_tool_calls=_EXECUTOR_PARALLEL)
```

### 2.6 技能披露加权 + 检索修复（已实施）

实现位置：`arsenal/registries/skill_registry.py`

- **检索字段**：`find_skills` 已使用 `name + display_name + category + description + triggers` 作为检索空间，description 已自然纳入。
- **渐进披露加权**：`detect_skill_triggers` 改为至少命中 **2 个不同 triggers** 才自动披露该技能；触发器少于 2 个的技能要求全部命中。防止单个通用词（如 "file"、"upload"）误触发无关技能。
- 二进制/PWN/协议、智能合约技能的上下文过滤保留。

```python
matched = [k for k in keywords if k.lower() in low]
threshold = min(2, len(keywords))
if len(matched) >= threshold:
    hits.append(name)
```

### 2.7 缓存命中率观测（已实施简化版）

实现位置：`app/main.py` + `core/task_context.py`

- 开题时判定是否命中历史成功解法 `sol_hint` / 同前缀笔记 / 角色派任 `playbooks`；
- `cache_hits` / `cache_misses` / `cache_notes` 写入 `TaskContext` 并落盘到 `field_notes`；
- 赛后可据此统计「有多少题靠历史沉淀直接带走、多少题需要从头推导」，指导后续补 wiki/技能。

注：SDK 层真实 prompt cache rate 需要网关返回 `cached_tokens` 字段，当前兼容网关未统一暴露，先用业务层命中率代理观测。

### 批次二验收

- ☐ 连跑 5 轮：第 2 轮起 input 不再每轮膨胀，动态上下文前缀可控
- ☐ 新题第 1 轮 LLM 即拿到预侦察证据
- ☐ 业务层 cache_hits / cache_misses 落盘到 field_notes
- ☐ run_batch 工具可用且被工作纪律推荐

---

## 批次三　状态累积：子任务后台化与六种死法（一天）

### 3.1 阻塞 gather 改后台任务（已实施简化版）

**问题**：`await _run_subtasks(...)` 阻塞主循环，主线等子任务跑完才能动。

**实现位置**：`app/main.py` + `core/task_context.py` + `demo_tools.py`

- `TaskContext` 增加 `subtask_jobs: Dict[str, asyncio.Task]` 字段；
- `spawn_subtask` 工具增加 `branch_type` 参数，描述里要求必须包含明确目标/范围；
- `_run_subtasks` 改为立即 `asyncio.create_task(_run_one_subtask(...))`，不阻塞主循环；
- 主循环每轮末尾调用 `_reap_subtasks(ctx)` 非阻塞收割已完成子任务，结果注入下一轮 `input`。

```python
# app/main.py
ctx.subtask_jobs[sub["id"]] = asyncio.create_task(_run_one())
reap = _reap_subtasks(ctx)
if reap:
    next_input = f"【分支结果】以下后台子任务已返回...\n{reap}\n\n{next_input}"
```

### 3.2 分支按类型换装派任（已实施）

- 子任务执行者不再继承父角色，按 `branch_type` 重新派任：`assign_role(sub.get("branch_type", ""), sub["desc"])`；
- 缺省沿用父角色。

### 3.3 六种回收死法（已实施父题级联，子任务处决未实施）

- 父题任何 `break` 出口统一 `finally` 内 `cancel` 全部 `subtask_jobs`（已实施）；
- 子任务内部连续零增量处决、token 预算上限、前提证伪等未实现，当前依赖子任务自己的 `max_turns` 与异常处理作为兜底；
- 实测 3 槽并发环境下，子任务后台化已将主线并行效率提升，完整六种死法后续可补。

### 3.4 回收遗产制（已实施部分）

- 子任务战果写黑板（`subtask:<id>`），`verified=True`；
- 失败路径未自动登记为 `failed`，依赖子任务 result summary 中文字描述。后续可补 `failed_paths` 结构化写入。

---

## 批次四　利用工作台（对利用弱的定向补强，2~3 小时）

1. **wiki → 可执行 payload 脚本库**（`arsenal/payloads/*.py`，统一 argparse 接口）：优先四发——`libpq_multibyte_bypass.py`（0xC0 前导字节 + OR 闭合族）、`gateway_backslash_bypass.py`（反斜杠路径段 + 403/404/401 判别表自动判定）、`npm_supply_chain.py`（Verdaccio 匿名发布 + postinstall 模板）、`rsc_flight_probe.py`（Flight 格式探测）。技能 md 末尾加「执行入口：python3 arsenal/payloads/xxx.py --help」；
2. **exploit_fuzz 迭代器**：payload 模板 `{P}` 占位 + 变体清单 + success/fail 正则，一次调用打完全部变体返回命中项；
3. **差分基线纪律**（exploit 阶段 focus 改硬流程）：先发正常/异常基线各一发建立判据，无差分判据的 payload 禁止发出；
4. **闭环指令带执行片段**：检测到 SQLi 后不再说「请 UNION SELECT」，直接给「运行 python3 payloads/sqli_union.py --url ... --param label」。

---

## 全局落地顺序

| 天 | 内容 | 出场前门槛 |
|---|---|---|
| 第 1 半天 | 批次一全部 | 批次一验收 4 条全过 |
| 第 1 天剩余 | 批次二 2.1~2.4 | 冒烟 5 轮 cache_rate ≥70% |
| 第 2 天 | 批次二 2.5~2.7 + 批次四前两项 | 并行封印实测 + payload 脚本逐个手测 |
| 第 3 天 | 批次三全部 | 批次三验收 4 条全过 |

**冒烟总线**：每批次改完跑一道已知快题（目录穿越/命令注入类），确认「启动→首轮突击包→铁律提交→机械通关判决→close→槽位回收」全链路正常，再改下一批。

## 明确不做的事

- ❌ 迁移 DeepSeek Harness 底座（TS/Node 预览版，破坏性变更风险）
- ❌ 双执行者并行（3 容器槽位是天花板）
- ❌ 自由拓扑的动态智能体（物种固定、个体按需、皮肤可换即可）
- ❌ 新增智能体类型（七角色 + 三智能体职责已清晰，磨执行层不动结构）
