# SecAI 赛前稳定性补丁 v2

> 适用代码：gitee.com/yzj1/secai.git @ commit `5e4bcb1`
> 目标：修复 1 个回归级误杀 + 3 个健壮性缺口 + hint 法令化欠账
> 赛前预计耗时：1~1.5 小时，全部改完务必跑冒烟

---

## 补丁 1　错误提交熔断改为「无证据连续失败」才熔断（赛前必改）

**问题**：`app/main.py` 现行逻辑 `wrong_submit_count >= 3 → stuck` 会误杀已验证的得分模式——上午 bctf-13（6 次错交后命中 +800）、bctf-12（4 次错交后命中 +800）、bctf-29/30（2~4 次错交后命中 +750×2）全部会被此熔断提前枪决。

**改法**：熔断条件叠加「期间无任何信息增量」，且阈值提到 6；每次错交后若有新证据则重置。

### 1.1 `app/main.py` 主循环熔断段（约 383 行处）替换

```python
            # 错误提交熔断：只有「连续错交 且 期间无任何信息增量」才算恋战
            # （快题实证：越权/支付类允许 5~10 次快速试错，见 bctf-13 的 6 次错交后命中）
            if ctx.wrong_submit_count >= 6 and ctx.zero_gain_turns >= 3:
                log_warn(f"[skip] 单题 {code} 连续 {ctx.wrong_submit_count} 次错交且无新证据，机械换题")
                outcome = "stuck"
                break
```

### 1.2 `demo_tools.py` `_submit_flags_if_any` 中（错交计数处）加增量豁免

```python
        if not r.get("correct"):
            c.wrong_submit_count += 1
            # 增量豁免：上一轮若有正向证据（新路径/漏洞确认），说明在有效推进，重置错交计数
            if getattr(c, "turn_gain", False):
                c.wrong_submit_count = 0
            continue
```

### 1.3 `bench_platform/platform_tools.py` `submit_flag` 同样加豁免

```python
    if not r.get("correct") and not r.get("duplicate"):
        c.wrong_submit_count += 1
        if getattr(c, "turn_gain", False):
            c.wrong_submit_count = 0
```

---

## 补丁 2　槽位泄漏双救援（部分泄漏感知 + close 失败重试队列）

**问题**：残留容器清理只在 `active == 0` 时触发——平台上挂 1~2 个残留容器时，系统全程只用 2 槽静默跑完，无告警；close 失败 3 次后永久泄漏。

### 2.1 `app/main.py` 主循环加「状态对齐报告」（拉题目列表成功之后、选题之前）

```python
            # 状态对齐：平台上 running 但不在本地 active 的容器 = 泄漏槽位
            leaked = [c.get("unique_code") for c in challenges
                      if c.get("container_status") == "running"
                      and c.get("unique_code") not in active]
            if leaked:
                leak_streak = leak_streak + 1 if 'leak_streak' in dir() else 1
                log_warn(f"[leak] 平台侧残留容器 {leaked}（本地无记录），第 {leak_streak} 轮")
                if leak_streak >= 3:
                    for code in leaked:
                        try:
                            await asyncio.to_thread(client.close_challenge, code)
                            log_warn(f"[leak] 已机械关闭残留容器 {code}")
                        except Exception as e:
                            log_warn(f"[leak] 关闭 {code} 失败：{str(e)[:120]}")
                    leak_streak = 0
            else:
                leak_streak = 0
```

（`leak_streak = 0` 在主循环 `try:` 前与 `list_fail_streak` 并列初始化；上面 `dir()` 写法可换成直接引用外层变量，保持风格一致即可。）

### 2.2 close 失败重试队列（完成单题的处理段）

```python
# 主循环 try 前初始化：
    close_pending: set = set()

# 处理完成单题处，close 三次失败的分支替换：
                if not closed:
                    log_warn(f"[warn] 单题 {code} 容器关闭失败，进入重试队列")
                    close_pending.add(code)
                else:
                    close_pending.discard(code)

# 主循环每轮（状态对齐报告旁）重试：
            for code in list(close_pending):
                try:
                    if await asyncio.to_thread(client.close_challenge, code):
                        close_pending.discard(code)
                        log_info(f"[close-retry] {code} 已关闭，槽位回收")
                except Exception:
                    pass

# finally 段兜底：close_pending 与 active 一起关闭（现有 finally 已遍历 active，
# 追加遍历 close_pending 即可）
```

---

## 补丁 3　ModelPool 全局共享 + preferred_name 接线

**问题**：每题 `ModelPool()` 新建——主模型若已永久失败，每道题都白炸一次再切灾备（全场 63 次无谓失败）；`preferred_name`（FAST/STRONG 分工）已实现但无人传参，双模型分工是摆设。

### 3.1 `app/main.py` 单题内模型池改全局共享

```python
# _run_single_challenge 内（约 332 行）：
#   删除：model_pool = ModelPool()
#   改为使用外层传入的全局池（签名加参数）
async def _run_single_challenge(..., model_pool=None, ...):
    if model_pool is None:
        model_pool = ModelPool(preferred_name=FAST_MODEL_NAME or None)  # 兜底

# 构建执行者时用池当前模型（FAST 优先，主模型兜底）：
    executor = build_executor(role, charter, brief,
                              field_notes=load_notes_for(code) or _load_field_notes(),
                              model=model_pool.current.model)
```

### 3.2 `run_task` 里创建并传入

```python
# run_task 调度器段（global_model_pool 已存在处）：
    fast_pool = ModelPool(preferred_name=FAST_MODEL_NAME or None)   # 执行者池：FAST 优先

# _run_one 调用处透传：
    outcome = await _run_single_challenge(..., model_pool=fast_pool, ...)
```

### 3.3 外层分析智能体用 STRONG 池

```python
    strong_pool = ModelPool(preferred_name=PLANNER_MODEL_NAME or None)  # 分析池：STRONG 优先
    # manager/planner/reporter 的 run_with_model_fallback(..., model_pool=strong_pool)
```

> 效果：主模型死了，全场只有第一题付一次切换成本；执行/分析各用各的脑，互不惊扰。

---

## 补丁 4　hint 法令化（冲 20000 的核心杠杆，当前完全缺失）

**问题**：全仓库无 `hint_directive`——hint 仍是一条普通 user 消息 + 计数器清零，上午 17 次 hint 零转化的机制原样保留。

### 4.1 hint 进黑板 + 强制聚焦（`app/main.py` hint 分支替换）

```python
HINT_GRACE_TURNS = 5   # hint 后转化宽限轮数

            if action == "hint":
                try:
                    hint = await asyncio.to_thread(client.get_hint, code)
                except Exception as e:
                    hint = f"（获取提示失败：{str(e)[:120]}）"
                hint_used = True
                ctx.zero_gain_turns = 0
                # ① hint 进黑板最高优先级（压缩不丢、每轮可见）
                ctx.blackboard["hint_directive"] = {
                    "value": hint, "status": "confirmed",
                    "ts": int(time.time()), "verified": True,
                    "evidence": "platform_hint"}
                ctx.hint_grace_active = True   # 开启 hint 相关增量判定
                print(f"  [hint] 单题 {code} 停滞，机械看提示（已写入 hint_directive）")
                next_input = (
                    f"【系统法令】平台提示已写入黑板 hint_directive，具有最高优先级。\n"
                    f"原文：{hint}\n\n"
                    f"接下来 {HINT_GRACE_TURNS} 轮你的每个动作必须直接验证该提示中的断言，"
                    f"与提示无关的侦察/扫描将被系统判为零增量。")
                continue

            # ② hint 熔断（decide_stuck_action 判定之前）：
            if hint_used and ctx.zero_gain_turns >= HINT_GRACE_TURNS:
                log_warn(f"[hint-stale] 单题 {code} hint 后 {HINT_GRACE_TURNS} 轮无转化，机械换题")
                outcome = "stuck"
                break
```

### 4.2 hint 相关增量判定（`core/hooks.py` `_score_tool_result` 开头加闸门）

```python
import re as _re

def _extract_hint_keywords(hint: str) -> list:
    """从 hint 原文提取技术名词（英文标识符/协议名/参数名），用于方向锁定。"""
    words = _re.findall(r"[A-Za-z][A-Za-z0-9_.\-]{3,}", hint or "")
    stop = {"this", "that", "with", "from", "what", "does", "the", "and", "you"}
    return [w for w in set(words) if w.lower() not in stop][:8]

# _score_tool_result 函数体最前面：
    hint_dir = ""
    if getattr(ctx, "hint_grace_active", False):
        hint_dir = ctx.blackboard.get("hint_directive", {}).get("value", "")
    if hint_dir:
        kws = _extract_hint_keywords(hint_dir)
        if kws and not any(k.lower() in text.lower() for k in kws):
            return 0   # 与 hint 无关的输出一律零增量 → 方向上锁
```

### 4.3 压缩保护（`core/context_manager.py` / `runtime/stuck.py` 压缩摘要注入处）

```python
# compact_session 生成摘要后，注入文本强制前置黑板快照（含 hint_directive）：
bb_snapshot = json.dumps(
    {k: v for k, v in ctx.blackboard.items()
     if isinstance(v, dict) and v.get("status") in ("done", "confirmed", "failed")},
    ensure_ascii=False)[:1500]
next_input = f"[已确认/已排除结论快照，禁止重复]\n{bb_snapshot}\n\n" + next_input
```

---

## 补丁 5　easy 墙钟加「进度续命」

**问题**：easy=10 分钟偏紧（bctf-15 用了 6.9 分钟贴线，bctf-20 用了 14 分钟），慢热快题会被误杀。

```python
# app/main.py 墙钟判定段替换：
            elapsed = time.monotonic() - ctx.challenge_start_ts
            if elapsed >= ctx.wallclock_budget:
                # 进度续命：近 5 轮有信息增量 → 延长一次（半档预算），防误杀慢热题
                if ctx.zero_gain_turns < 5 and not getattr(ctx, "_wallclock_extended", False):
                    ctx._wallclock_extended = True
                    ctx.wallclock_budget += ctx.wallclock_budget // 2
                    log_info(f"[extend] 单题 {code} 有进展，墙钟延长半档至 {ctx.wallclock_budget}s")
                else:
                    log_warn(f"[skip] 单题 {code} 墙上时间 {elapsed:.0f}s 超预算，机械换题")
                    outcome = "stuck"
                    break
```

---

## 补丁 6（顺手）　子任务 session 清理

```python
# _run_single_challenge 的 finally 段，challenge sqlite 删除旁追加：
        import glob as _glob
        for p in _glob.glob(str(SESSIONS_DIR / f"sub_*.sqlite")):
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
```

---

## 改后验收（冒烟清单）

1. ☐ 构造一道越权类快题，故意错交 5 次但每轮有新路径发现 → **不应**触发熔断；
2. ☐ 构造同题连错 6 次且零增量 → 应触发 stuck 换题；
3. ☐ 手动在平台留 1 个 running 残留容器再启动 → 日志应出现 `[leak]` 报告并在第 3 轮机械关闭；
4. ☐ 把主模型 API key 改错启动 → 第一题切换灾备后，第二题应直接使用灾备（无再次失败日志）；
5. ☐ 看 hint 后连续 3 轮调与 hint 关键词无关的工具 → 日志应显示零增量累计（方向锁生效）；
6. ☐ 一道 easy 题在第 9 分钟仍有新证据 → 日志出现 `[extend]`，不判 stuck。
