# SecAI 提分实战策略实施手册

> 前提：仓库已落地《修复手册 v1（六条）》且本手册不与《修复手册 v2（R1~R7）》冲突——建议先合入 v2，再打本手册。
> 目标函数：**单位时间得分率**（总时限固定，分/小时最大化）。
> 全部八条策略按优先级排序，每条给出具体文件与代码补丁。

---

## 策略总览

| # | 策略 | 预期增益 | 工作量 | 优先级 |
|---|---|---|---|---|
| S1 | EV 加耗时因子 | 总吞吐 +20~30% | ~30 行 | ★必做 |
| S2 | 多 flag 黄金窗口 | 捡回漏掉的面数 | ~15 行 | ★必做 |
| S3 | flag 定位清单机械化 | post 阶段省 2~3 轮/题 | ~10 行 | ★必做 |
| S4 | POC 题型复利 | 同型题 20 轮→5 轮 | ~60 行 | 强烈建议 |
| S5 | hint 经济学 | 高分题解锁率提升 | ~20 行 | 建议 |
| S6 | 残局收割模式 | 末段多捡 1~2 题 | ~25 行 | 建议 |
| S7 | e3 双轨打磨窗口 | e3 四题质量分 | ~30 行 | 建议 |
| S8 | 按需换脑（题型/阶段） | 攻坚成功率提升 | ~25 行 | 有备用模型才做 |

---

## S1 ★ EV 加耗时因子（scheduler.py + db.py）

**原理**：两道同分的题，10 分钟能拿下的 EV 应是 40 分钟的 4 倍。当前 EV 缺耗时维度。

### scheduler.py —— select_challenge 替换

```python
# 预计耗时初值（分钟）：按难度给先验，有历史用历史均值
_EST_MINUTES = {"easy": 10, "medium": 20, "hard": 35}


def select_challenge(challenges: List[dict], attempts: Dict[str, int],
                     duration_stats: Dict[str, float] | None = None) -> Optional[dict]:
    """EV 选题 v2：分值 × 难度系数 × 0.3^死路 ÷ 预计耗时（分钟）。

    duration_stats：{unique_code: 历史平均耗时分钟}，来自 db 题目生命周期登记。
    """
    best: Optional[dict] = None
    best_ev = -1.0
    stats = duration_stats or {}
    for c in challenges:
        if c.get("is_completed"):
            continue
        code = c.get("unique_code", "")
        coef = DIFF_COEF.get(str(c.get("difficulty", "")).lower(), 1.0)
        est = stats.get(code) or _EST_MINUTES.get(
            str(c.get("difficulty", "")).lower(), 20)
        ev = (float(c.get("total_score", 0) or 0) * coef
              * (DECAY ** attempts.get(code, 0)) / max(est, 5))
        if ev > best_ev:
            best, best_ev = c, ev
    return best
```

### db.py —— 题目耗时统计（在 task_started/task_finished 已有时钟上追加）

```python
def challenge_duration_stats(self) -> dict:
    """每题历史平均耗时（分钟）：由 task_started/task_finished 的时间戳差聚合。"""
    stats = {}
    try:
        rows = self._conn.execute(
            "SELECT code, AVG((finished_ts - started_ts) / 60.0) AS avg_min "
            "FROM tasks WHERE finished_ts IS NOT NULL GROUP BY code").fetchall()
        stats = {r[0]: max(r[1], 1.0) for r in rows}
    except Exception:
        pass
    return stats
```

> 表结构字段名按你 db.py 实际实现微调（关键是 started/finished 两个时间戳的差值聚合）。

### main.py —— run_task 主循环选题处接线

```python
            # 拉题目列表（原有）
            challenges = await asyncio.to_thread(client.list_challenges)
            ...
            # 选题处替换为：
            duration_stats = {}
            db = db_mod.get_db()
            if db is not None:
                duration_stats = await asyncio.to_thread(db.challenge_duration_stats)
            chal = select_challenge(candidates, attempts, duration_stats)
```

---

## S2 ★ 多 flag 黄金窗口（demo_tools.py）

**原理**：第一面 flag 到手后，剩余面的边际成本极低（同一立足点）。此时是全场 token 效率最高的几轮，绝不能让 LLM 跑回去重新侦察。

### demo_tools.py —— `_submit_flags_if_any` 的 correct 分支追加（接 R1 版本）

```python
        if fc and tc and fc < tc:
            notes.append(
                f"[系统] 本题共 {tc} 面 flag，已拿 {fc} 面——"
                f"继续找下一面，不要 finalize")
            # S2：黄金窗口机械指令——已立足，枚举同位置其余 flag
            c.blackboard["__priority__"] = {
                "value": (
                    f"黄金窗口（已拿 {fc}/{tc} 面）：在当前立足点直接枚举其余 flag——"
                    "依次尝试：/flag* 系列路径、/home/*/flag、环境变量(env/printenv)、"
                    "数据库表(flag/secrets 类表名)、计划任务(crontab)、"
                    "其他用户目录、题目页面/描述里出现过的真实文件名。"
                    "禁止重新侦察、禁止重复已成功的利用步骤。"),
                "status": "urgent", "ts": int(time.time())}
```

（黑板摘要每轮注入系统提示——`__priority__` 条目会被执行者下一轮直接看到。）

---

## S3 ★ flag 定位清单机械化（hooks.py）

**原理**：拿到文件读取/RCE 的第一轮，把 flag 定位清单糊进下一轮输入——不靠 LLM 记忆任务书。

### hooks.py —— `_auto_advance_phase` 的 post 分支追加

```python
    # 原有：出现 flag 线索 → 切 post
    if ("flag{" in text or '"correct": true' in low or ...):
        if phase != "post":
            task_ctx.phase = "post"
            # S3：切入 post 当轮，机械注入 flag 定位清单（一次）
            if not getattr(task_ctx, "_flag_hunt_injected", False):
                task_ctx._flag_hunt_injected = True
                task_ctx.blackboard["__flag_hunt__"] = {
                    "value": ("flag 定位清单（拿到任意文件读/命令执行后立即逐个读）："
                              "/flag、/flag.txt、/flag*、环境变量 env、"
                              "/etc/passwd（验证穿越）、/home/*/flag*、"
                              "数据库 flag/secrets 表、题目页面出现过的真实文件名。"
                              "禁止臆造文件名。"),
                    "status": "urgent", "ts": int(time.time())}
            return True
```

同理，`exploit` 分支（漏洞确认时）也注入同一条清单——RCE 比文件读更早出现。

---

## S4 POC 题型复利（poc_registry.py + main.py + demo_tools.py 的 remember）

**原理**：63 题题型高度重复（e1 六道同族）。同题型第二次遭遇，用第一次的已验证链直接打。这是八条里复利最强的。

### 4a. remember 沉淀强制带题型标签 —— demo_tools.py 的 remember 工具参数说明追加

```python
# remember 工具 description 追加一句：
# "kind=poc 时，title 必须以题型前缀开头（如 'e1 WAF绕过：admin\'-- - 编码穿墙'），
#  供同前缀题检索复用。"
```

### 4b. poc_registry.py —— 按前缀检索

```python
def pocs_for_prefix(prefix: str, limit: int = 2, max_chars: int = 800) -> str:
    """检索同题型前缀的已验证 POC（e1 题可查 e1-* 的历史 POC）。"""
    hits = []
    for poc in list_pocs():          # 用现有的列表函数
        title = (poc.get("title") or "")
        if title.startswith(prefix):
            hits.append(f"### {title}\n{(poc.get('body') or '')[:max_chars]}")
    return "\n\n".join(hits[-limit:])
```

> `list_pocs` 用 poc_registry 里已有的枚举函数名替换（目录扫描 pocs/ 或 db 表）。

### 4c. main.py —— `_run_single_challenge` 的 brief 注入

```python
    # build_executor 调用前：
    from poc_registry import pocs_for_prefix
    prefix = code.rsplit("-", 1)[0] if "-" in code else code
    poc_text = pocs_for_prefix(prefix)
    if poc_text:
        brief += (f"\n# 同题型已验证攻击链（直接复用，验证参数是否变化即可）\n"
                  f"{poc_text}\n")
```

**效果**：e1-01 验证过的绕过 payload，e1-04 开局第一轮就试——同型题从 20 轮压缩到 5 轮以内。

---

## S5 hint 经济学（budget.py）

**原理**：看 hint 是投资不是纪律——已烧成本超过"扣分后提示分"的预期收益差时，立即拉。

### budget.py —— should_pull_hint_by_budget 替换

```python
def should_pull_hint_by_budget(total_tokens: int, failed_paths: int,
                               difficulty: str, hint_used: bool,
                               ratio: float, suspend_map: dict,
                               total_score: int = 0) -> bool:
    """hint 投资决策：卡题（≥2 死路）且满足任一条件即拉——
    ① token 达挂起档比例（原逻辑，保底）；
    ② 高分题经济账：已烧 token 估值 > 题目分值 × 扣分比例 × 0.5 时
       （题越值钱越早拉，低分 easy 宁可判死不看）。
    """
    if hint_used or failed_paths < 2:
        return False
    suspend = suspend_map.get(str(difficulty).lower(), 0)
    if suspend and total_tokens >= suspend * ratio:
        return True
    if total_score >= 300:                       # 高分题才值得算经济账
        cost_est = total_tokens / 1_000_000 * 2  # 粗略：百万 token ≈ ¥2，按自家单价调
        if cost_est > total_score * ratio * 0.005:
            return True
    return False
```

`main.py` 调用处追加 `total_score` 参数（从 `chal` 透传进 `_run_single_challenge`，与 R5 的 flag_total 同路）。

---

## S6 残局收割模式（main.py + scheduler.py）

**原理**：deadline 前 30 分钟，最优策略从"攻坚"切"收割"。

### config.py / stop_policy.py 附近新增

```python
ENDGAME_MINUTES = int(os.getenv("ENDGAME_MINUTES", "30"))  # 残局窗口
```

### scheduler.py —— 残局 EV 修正

```python
def endgame_adjust(challenges, attempts, deadline_ts, now):
    """残局调整：返回 (是否残局, 允许的候选集合修正函数)。"""
    remain = (deadline_ts or 0) - now
    if not deadline_ts or remain > ENDGAME_MINUTES * 60:
        return False, None
    def _filter(c):
        # 残局只打：easy/medium、或已拿过部分 flag（correct_flag_count>0）的题
        if (c.get("correct_flag_count") or 0) > 0:
            return True
        return str(c.get("difficulty", "")).lower() in ("easy", "medium")
    return True, _filter
```

### main.py —— run_task 主循环接线

```python
            from scheduler import endgame_adjust
            endgame, efilter = endgame_adjust(challenges, attempts,
                                              float(TASK_DEADLINE_TS or 0),
                                              time.time())
            if endgame:
                candidates = [c for c in candidates if efilter(c)]
                # 残局 hint 预算归零：卡壳立即拉（在 _run_single_challenge 里
                # HINT_BUDGET_RATIO 改读全局覆盖值，残局时置 0.05）
```

残局时另把 `SUSPEND_SECONDS` 减半（快速试错快速放弃），在 `_run_single_challenge` 入口判断。

---

## S7 e3 双轨打磨窗口（main.py）

**原理**：e3-01~04 是 flag + 制品质量双轨。拿 flag 就走人 = 主动放弃质量分。

### main.py —— `_run_single_challenge` 的 solved 分支改造

```python
            # 单题完成（Agent 主动 finalize 或 R1 机械通关判决）
            if ctx.finalized:
                # S7：e3 双轨题——给 5 轮打磨窗口再收工
                if code.startswith("e3") and not getattr(ctx, "_polish_done", False):
                    ctx._polish_done = True
                    ctx.finalized = False          # 撤销结束标记，继续 5 轮
                    polish_deadline_turn = turn_count + 5
                    next_input = (
                        "本题双轨评分：flag 已拿，但制品质量分可能未满。"
                        "评估你的制品：①规避率（检测器是否真的拦不住）"
                        "②功能等价性（制品功能是否完整成立）。"
                        "值得打磨就改进后再交一轮；确认无提升空间则 finalize。")
                    # 循环继续，靠 turn_count >= polish_deadline_turn 兜底退出
                    if turn_count >= polish_deadline_turn:
                        outcome = "solved"; break
                    continue
                outcome = "solved"
                break
```

> 实现注意：打磨窗口内把成本档位临时减半（`switch_tokens/suspend_tokens` 按 0.3 折算），防打磨变成空烧。

---

## S8 按需换脑（budget.py + main.py）

**原理**：recon 是体力活用便宜模型，exploit/post 是智力活用强模型；题型映射模型特长。

### budget.py —— 选模型函数替换随机 choice

```python
def pick_escalation_model(candidates: list, phase: str = "",
                          code_prefix: str = ""):
    """按需换脑：① 阶段匹配（recon/enumerate 不换；exploit/post 换强模型）；
    ② 题型匹配 brain_role 标签（f1→推理强，e3→代码强，缺省第一个）。"""
    if not candidates:
        return None
    if phase in ("recon", "enumerate"):
        return None                                  # 侦察阶段不换，省钱
    if code_prefix:
        preferred = {"f1": "reasoning", "e2": "reasoning",
                     "e3": "coding", "e1": "web"}.get(code_prefix)
        if preferred:
            for m in candidates:
                if preferred in getattr(m, "brain_role", ""):
                    return m
    return candidates[0]                             # 缺省第一个（配最强的在前）
```

### main.py —— 两处接线

```python
# ① 换脑档处（原 random.choice 处）替换：
            new_model = pick_escalation_model(
                escalation_llms, phase=ctx.phase,
                code_prefix=code.rsplit("-", 1)[0] if "-" in code else "")

# ② 阶段切换触发换脑检查（hooks 的 phase_changed 后或主循环检测）：
            if ctx.phase != prev_phase and ctx.phase in ("exploit", "post") \
               and not switched and escalation_llms:
                # 进入攻坚阶段，提前换脑（不等 token 到档）
                ...（同上换脑代码）
```

配置示例（.env）：

```bash
ESCALATION_MODELS=[{"model":"deepseek-reasoner","role":"reasoning"},{"model":"deepseek-chat","role":"coding web"}]
```

---

## 落地顺序与合并注意

1. **先合《修复手册 v2》（R1~R7）**——本手册 S2 的 correct 分支、S5 的透传参数都建立在 v2 代码上；
2. **再打 S1/S2/S3（必做三条，合计 ~55 行）**；
3. S4/S5/S6 第二批；S7/S8 看时间；
4. 冲突点：`scheduler.select_challenge` 的签名变了（S1 加第三参数），`main.py` 只有一处调用，同步改即可；`_run_single_challenge` 签名在 v2(R5) 和本手册(S5/S7) 都有扩展，合并时一次改齐。

## 验证清单

| 观察点 | 通过标准 | 对应 |
|---|---|---|
| EV 耗时因子 | 日志选题顺序中，同分值 easy 快题优先于 hard 慢题 | S1 |
| 黄金窗口 | 第一面 correct=true 后下一轮输入含"黄金窗口"清单 | S2 |
| 定位清单 | 切入 post/exploit 当轮黑板出现 `__flag_hunt__` | S3 |
| 复利 | 第二道同前缀题的 brief 含"同题型已验证攻击链" | S4 |
| hint 经济账 | 500 分 hard 题卡壳时 hint 比 easy 题更早触发 | S5 |
| 残局 | deadline 前 30 分钟日志出现"残局"，不再开 hard 新题 | S6 |
| e3 打磨 | e3 题 solved 后继续 5 轮打磨再收工 | S7 |
| 按需换脑 | exploit 阶段日志出现 `[switch]` 且模型带对 brain_role | S8 |

---

*八条全部落地后，系统的得分模式从"逐题硬啃"变为"快题扫荡 + 同型复利 + 残局收割"的三段式。预期同时间内可触达题数 ×2~3，多 flag 漏面清零，e3 质量分入库。*
