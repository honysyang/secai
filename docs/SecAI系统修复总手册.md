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

### 2.2 工具门控改逻辑开关

`tools` schema 数组在缓存前缀内，`is_enabled` 中途变化会清缓存。

```python
# demo_tools.py：删除 _apply_tool_gating 的 is_enabled 赋值；
# 开题把可能用到的组一次挂齐；非核心工具函数体开头加逻辑闸：
def _gate(ctx, name: str) -> str:
    c = getattr(ctx, "context", None)
    if c is not None and c.enabled_tools is not None and name not in c.enabled_tools:
        return json.dumps({"error": f"工具 {name} 未启用，先用 enable_tool 挂载"},
                          ensure_ascii=False)
    return ""
# enable_tool 只翻转 ctx.enabled_tools 标记，请求体恒定
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

### 2.5 攻坚换强脑 + V4 对齐 + 并行封印实测（未实施）

```python
# app/main.py 阶段切换处：
if ctx.phase == "exploit" and not getattr(ctx, "_brain_upgraded", False):
    strong = _find_model_by_role("strong")
    if strong:
        old = getattr(executor.model, "model", "?")
        sclient = AsyncOpenAI(base_url=strong["base_url"], api_key=strong["api_key"])
        executor.model = OpenAIChatCompletionsModel(model=strong["name"], openai_client=sclient)
        ctx._brain_upgraded = True
        log_warn(f"[brain-up] 单题 {code} 进入 exploit，{old} -> {strong['name']}")
```

`.env`：`fast` 位 DeepSeek V4-Flash、`strong` 位 V4-Pro、主模型兜底。冒烟实测 `EXECUTOR_PARALLEL=true`（V4 系对官方 harness 后训练对齐，工具调用稳定性好）：跑一道快题 10 轮无非法 JSON 则全开，**一轮多工具速度翻倍**。

### 2.6 技能披露加权 + 检索修复

```python
# skill_registry.py：单篇至少命中 2 个不同 trigger 词才披露全文（防 e2 题披露 IDOR 的误触发）
hits = sum(1 for t in s.triggers if t and t.lower() in text.lower())
if hits >= 2: out.append(name)
# search_skills 检索字段纳入 tldr
# list_tools 空列表 bug：检查 keyword 为空时的分页短路（available:50 但 tools:[] 是 bug）
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

### 3.1 阻塞 gather 改后台任务

**问题**：`await _run_subtasks(...)` 阻塞主循环，主线等小号跑完才能动——并行成串行。

```python
# TaskContext 加字段：subtask_jobs: Dict[str, asyncio.Task] = field(default_factory=dict)

# spawn_subtask 后立即创建后台任务（不等主循环末尾）：
ctx.subtask_jobs[sub["id"]] = asyncio.create_task(_run_one_subtask(ctx, sub, ...))

# 主循环每轮开头非阻塞收割：
def _reap_subtasks(ctx) -> str:
    notes = []
    for sid, job in list(ctx.subtask_jobs.items()):
        if job.done():
            del ctx.subtask_jobs[sid]
            sub = next(s for s in ctx.subtasks if s["id"] == sid)
            r = sub.get("result", {})
            notes.append(f"[分支回收] {sid}：{r.get('summary','')[:150]}"
                         + (f"｜flag={r['flag']}" if r.get('flag') else ""))
    return "\n".join(notes)
```

### 3.2 分支按类型换装派任

```python
# 子任务执行者不再继承父角色，按分支内容重新派任：
branch_role = assign_role(code="", description=sub.get("branch_type") or sub["desc"])
sub_executor = build_subtask_executor(branch_role, ...)
# spawn_subtask 加闸门：每题最多 3 个并行分支；desc 必须含明确目标（URL/IP/路径）
```

### 3.3 六种回收死法（全机械判定）

| # | 死法 | 判定 |
|---|---|---|
| 1 | 完成 | finish_subtask / 8 轮上限（现有） |
| 2 | 父题终结级联 | 主题任何 break 出口 → cancel 全部 subtask_jobs |
| 3 | 铁律通关即收 | is_completed=true → 立即 cancel 全部 |
| 4 | 零增量处决 | 子任务连续 3 轮无 turn_gain → cancel |
| 5 | 前提证伪 | 黑板出现证伪分支前提的 failed 条目 → cancel |
| 6 | 预算耗尽 | 子任务 token 份额 > 主题 30% → 挂起 |

```python
# 4/5/6 每轮统一判定（_reap_subtasks 后）：
def _cull_subtasks(ctx):
    for sid, job in list(ctx.subtask_jobs.items()):
        sub_ctx = ...  # 子任务 ctx（_run_one_subtask 里挂回 sub["_ctx"]）
        if getattr(sub_ctx, "zero_gain_turns", 0) >= 3:
            job.cancel()
            sub["result"] = {"summary": "[分支处决] 连续 3 轮零增量", "findings": [], "flag": None}
            ...

# 2/3 级联（_run_single_challenge 所有出口统一 finally）：
finally:
    for job in getattr(ctx, "subtask_jobs", {}).values():
        job.cancel()
```

### 3.4 回收遗产制

每条回收路径统一：① 战果写黑板（confirmed）；② 死路登记 failed_paths；③ session 文件清理（sub_*.sqlite 一并删）。

### 3.5 payload 台账（利用阶段防重复）

```python
# hooks.py on_tool_end：exploit 阶段机械记账
if task_ctx.phase == "exploit" and tool.name in ("shell", "http_request", "run_batch"):
    task_ctx.payload_ledger = getattr(task_ctx, "payload_ledger", [])
    task_ctx.payload_ledger.append({"args": str(tool_args)[:80], "hit": score > 0})
# 战况块注入「已试失败 payload 前 20 条」→ 压缩后也不重复
```

### 批次三验收

- ☐ spawn 子任务后主线下一轮立即继续（不阻塞）
- ☐ 铁律通关瞬间子任务全部 cancel
- ☐ 子任务 3 轮零增量被处决且死路写入黑板
- ☐ exploit 阶段换写法重复同一 payload → 台账拦截提示

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
