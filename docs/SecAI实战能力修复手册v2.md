# SecAI 实战能力修复手册 v2

> 注：本文档基于 commit 63a0101 的扁平目录编写。后续工程化重构后，文件路径已迁移到分层目录：
> - `main.py` → `app/main.py`
> - `agents_def.py` / `hooks.py` / `context_manager.py` / `events.py` / `task_context.py` / `charter.py` → `core/`
> - `scheduler.py` / `platform_client.py` / `platform_tools.py` → `platform/`
> - `budget.py` / `stop_policy.py` / `status.py` → `runtime/`
> - `config.py` / `db.py` → `adapters/`
> - `solution_templates.py` → `solvecraft/solution_templates.py`
> - `demo_tools.py` 保留在项目根目录
> - 各 `*_registry.py` → `arsenal/registries/`；skills/roles/pocs/vulns/payloads/knowledge/tools → `arsenal/`

> 依据：当前仓库（commit 63a0101）评估报告。上一轮的六条修复已全部落地，本手册处理**残留的 1 个重伤 + 3 个中伤 + 3 个小伤**，全部为可直接照抄的代码补丁。
> 总目标：通关题不再空烧 token、hint/skip 机制对 Web 题恢复生效、终局信号零丢失、接力档案按题检索。

---

## 残留问题总览

| # | 级别 | 问题 | 修复文件 |
|---|---|---|---|
| R1 | 重伤 | 通关出口是 LLM 的 finalize，平台 is_completed 缺席——通关题空烧到 suspend 档（easy 题 100 万 token） | `demo_tools.py`（项目根目录） |
| R2 | 中伤 | 信息增量判定过宽：任何 HTTP 状态码都算增量 → hint/skip 对 Web 题永不触发 | `core/hooks.py` / `core/task_context.py` |
| R3 | 中伤 | 铁律吞终局异常（TaskEnded 变文本），与 platform_tools 的 fatal 机制行为不一致 | `demo_tools.py`（项目根目录） |
| R4 | 中伤 | field_notes 仍读尾部 3000 字符，按题检索缺席；题级沉淀缺席 | `app/main.py` 新增两函数 |
| R5 | 小伤 | brief 无 flag 进度（多 flag 题"已拿 M/N 面"丢失） | `app/main.py` |
| R6 | 小伤 | 工作纪律 16 条，单条最长 200+ 字，prompt 治理回潮 | `core/agents_def.py` |
| R7 | 小伤 | 压缩 clear_session 可能留下孤儿 tool 响应（配对 400） | `core/context_manager.py` |

---

## R1（重伤）：通关机械出口——铁律提交后直接问平台

**原理**：`submit_flag` 的响应里本来就带 `correct_flag_count / total_flag_count`，correct=true 时机械比对；相等（或平台 is_completed 复核为真）→ 直接置 `ctx.finalized`，不再等 LLM 想起 finalize。

### demo_tools.py（项目根目录）—— `_submit_flags_if_any` 整函数替换

```python
_PLATFORM = None  # 模块级单例（顺手修掉"每次新建 PlatformClient"）

def _platform() -> PlatformClient:
    global _PLATFORM
    if _PLATFORM is None:
        _PLATFORM = PlatformClient(BENCHMARK_BASE_URL, BENCHMARK_TOKEN)
    return _PLATFORM


def _is_completed(client: PlatformClient, code: str) -> bool:
    """机械复核平台通关状态（记分牌是唯一权威）。"""
    try:
        for c in client.list_challenges():
            if c.get("unique_code") == code:
                return bool(c.get("is_completed"))
    except (TaskEnded, TaskNotFound):
        raise                       # R3：终局信号必须上抛
    except Exception:
        return False
    return False


def _submit_flags_if_any(ctx: RunContextWrapper[TaskContext], text: str) -> str:
    """提交铁律：扫描完整输出中的 flag，机械提交 + 机械通关判决。

    必须在 _spill_output 截断前调用（全文扫描），否则 flag 落在截断点之后会被埋没。
    """
    flags = _scan_flags(text)
    if not flags:
        return ""
    c = ctx.context
    code = c.current_code
    notes = [f"[系统·检测到flag] {f}" for f in flags]
    if not code:
        notes.append("[系统] 当前题 unique_code 未知，请调用 submit_flag 手动提交")
        return "\n".join(notes)
    if not (BENCHMARK_BASE_URL and BENCHMARK_TOKEN):
        notes.append("[系统] 未配置平台凭证，无法机械提交")
        return "\n".join(notes)

    client = _platform()
    for f in flags:
        if f in c.submitted:
            continue
        c.submitted.add(f)
        try:
            r = client.submit_flag(code, f)
        except (TaskEnded, TaskNotFound) as e:
            c.fatal = "task_ended"    # R3：与 platform_tools 行为对齐
            raise
        except Exception as e:
            notes.append(f"[系统·提交异常] {str(e)[:120]}")
            continue

        notes.append(f"[系统·提交铁律] {f} → {json.dumps(r, ensure_ascii=False)[:200]}")
        if not r.get("correct"):
            continue

        # ---- R1 核心：correct=true 后的机械判决，不等 LLM finalize ----
        c.correct_flags.append(f)
        fc, tc = r.get("correct_flag_count"), r.get("total_flag_count")
        if fc and tc and fc < tc:
            notes.append(
                f"[系统] 本题共 {tc} 面 flag，已拿 {fc} 面——"
                f"继续找下一面，不要 finalize")
        else:
            # 单 flag 题或最后一面：机械复核平台通关状态
            try:
                done = _is_completed(client, code)
            except (TaskEnded, TaskNotFound):
                c.fatal = "task_ended"
                raise
            if done:
                c.finalized = True
                c.final_payload = {"findings":
                    f"平台确认 {code} 全部 flag 通关（铁律提交，机械判决）"}
                notes.append("[系统·通关判决] 平台 is_completed=true，本题结束，"
                             "系统将自动换题")
    return "\n".join(notes)
```

**配套**：`core/task_context.py` 确认存在 `submitted: set` 与 `correct_flags: list` 字段（若无则补上）。

**效果**：通关当轮立即退出单题循环，`outcome="solved"`，不进入 suspend 档，不白花 token；多 flag 题被明确告知"继续找下一面"。

---

## R2（中伤）：信息增量判定收窄——状态码不再是万金油

**病灶**：`hooks.py:79` `_HTTP_STATUS_RE` 匹配任何输出里的 `200/301/500…`——每次 `http_request` 都含 `status=200`，`turn_gain` 恒真，`zero_gain_turns` 永不增长，`platform/scheduler.decide_stuck_action` 的 hint/skip 预算对 Web 题形同虚设。

### core/task_context.py —— 加签名集字段

```python
    seen_signatures: set = field(default_factory=set)  # 已见路径/指纹签名（增量去重用）
```

### core/hooks.py —— `_score_tool_result` 整函数替换 + on_tool_end 调用处改签名

```python
# core/hooks.py 顶部追加
_PATH_EXTRACT_RE = re.compile(r"(?:/[A-Za-z0-9_.~%-]{2,}){1,4}")
_SENSITIVE_RE = re.compile(
    r"(config\.php|\.git/|backup|\.env|phpinfo|/flag|flag\.txt|wp-config|"
    r"\.bak|\.sql|\.zip|web\.config|id_rsa|shadow)", re.I)
_ENUM_TOOLS = {"run_tool", "fuzz", "parallel_shell"}   # 只有枚举类工具的状态码算增量


def _score_tool_result(tool: str, text: str, ctx) -> int:
    """信息增量打分 v2：+1 正向新认知 / 0 中性（纯规则，零 LLM）。

    收窄原则：
    - 铁证（flag/提交正确/漏洞确认/登录/差分判定）任何工具都算；
    - HTTP 状态码只在【枚举类工具】输出里算增量，且必须出现 2 个以上不同状态码
      （单次 http_request 的状态码不算——内容差异才算）；
    - 新路径/敏感文件：与历史签名去重后，首次出现才算；
    - 工具失败/网络错误判 0 交给 LLM 决策（default-soft 不变）。
    """
    low = text.lower()
    # ① 铁证：任何工具
    if any(k in low for k in (
            "flag{", '"correct": true', '"correct":true',
            '"vulnerable": true', '"vulnerable":"true"',
            '"differentiated": true', '"vuln": true', '"vuln":"true"',
            "login success", "logged in", "session=", "响应存在差异")):
        return 1
    # ② 新敏感文件：任何工具，首次出现才算（去重）
    sensitive = {m.lower() for m in _SENSITIVE_RE.findall(text)}
    if sensitive - ctx.seen_signatures:
        ctx.seen_signatures |= sensitive
        return 1
    # ③ 枚举类工具：多状态码并存（说明扫到了存活的端点集合）
    if tool in _ENUM_TOOLS:
        codes = set(_HTTP_STATUS_RE.findall(text))
        if len(codes) >= 2 or _PORT_OPEN_RE.search(text) or "open port" in low:
            return 1
        # 枚举发现的新路径（去重后仍有新货）
        new_paths = {p.lower() for p in _PATH_EXTRACT_RE.findall(text)} \
                    - ctx.seen_signatures
        if len(new_paths) >= 2:                      # 至少 2 条新路径才算一轮增量
            ctx.seen_signatures |= new_paths
            return 1
    # ④ shell/http_request：只看铁证与敏感文件（①②已覆盖），状态码一律不算
    return 0
```

`on_tool_end` 里的调用处改为：

```python
        score = _score_tool_result(tool.name, str(result), task_ctx)
```

**效果**：Web 题连续 curl 出 200 不再骗过判停；`zero_gain_turns` 正常累计 → 第 6/8/10 轮（按难度）机械拉 hint、第 12/20/25 轮机械换题，scheduler 恢复实战意义。

---

## R3（中伤）：铁律与 platform_tools 终局行为对齐

已并入 R1 的替换代码（两处 `except (TaskEnded, TaskNotFound): c.fatal = "task_ended"; raise`）。
**核对点**：改完后全仓库搜索 `_submit_flags_if_any` 内不应再有裸 `except Exception` 吞掉终局异常的路径（提交网络的瞬时异常仍吞，这是对的——终局异常必须上抛）。

---

## R4（中伤）：field_notes 按题检索 + 题级机械沉淀

**思路**：沉淀不靠 LLM 报告者（那是战役级），每题结束时用**纯代码**从黑板提取死路/战果写档案——零 token；注入时按题号 + 同前缀检索。

### app/main.py —— 新增两函数，替换 `_load_field_notes` 的调用

```python
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
```

### app/main.py `_run_single_challenge` 两处接线

```python
# ① 构建 executor 处，替换 field_notes 参数：
    executor = build_executor(role, charter, brief,
                              field_notes=load_notes_for(code) or _load_field_notes())

# ② finally 段（db.task_finished 之前）追加：
    _append_mechanical_note(code, outcome, ctx)
```

**效果**：打 a-05 时注入的是 a-05 和 a-0x 的死路，不再是随便哪题的战报尾巴；沉淀零 token 成本。

---

## R5（小伤）：brief 补 flag 进度

### app/main.py —— `_run_single_challenge` 签名与 brief

```python
# 签名加两参数
async def _run_single_challenge(code, desc, addrs, charter, task, global_plan,
                                hooks, workdir, client, difficulty="",
                                flag_total: int = 1, flag_done: int = 0) -> str:

# brief 替换为：
    brief = (f"# 任务书\n{task}\n\n"
             f"# 当前题目（只打这道题）\n"
             f"- unique_code: {code}\n- 描述: {desc}\n- 容器地址: {addrs}\n"
             f"- flag 进度：已拿 {flag_done}/{flag_total} 面"
             f"（多 flag 题须逐面提交；系统提交回执会告知剩余面数）\n\n"
             f"选题/换题/看 hint 由系统调度负责，你只专注攻击本题容器；"
             f"不要自己调用 list_challenges / start_challenge / close_challenge。")

# run_task 里 _start_one 调用处传入：
        outcome = await _run_single_challenge(
            code, desc, addrs, charter, task, global_plan, hooks, workdir,
            client, difficulty,
            flag_total=chal.get("flag_count") or 1,     # chal 需透传进 _start_one
            flag_done=chal.get("correct_flag_count") or 0)
```

（`_start_one` 签名相应加 `chal: dict` 或这两个字段，由 `run_task` 选题处透传。）

---

## R6（小伤）：纪律 16 条 → 8 条

### core/agents_def.py —— EXECUTOR_TEMPLATE 的 `# 工作纪律` 段整段替换

```
# 工作纪律
1. 每轮必须产出至少一个新信息（证据增量），禁止空转与重复已失败方向。
2. 目标地址以任务书为准，禁止自猜；python3 脚本是主武器库。
3. 关键进展（登录态/漏洞确认/文件路径）立即写 blackboard 并附 evidence；
   判死结论必须附证据，被证伪的旧结论用 supersedes 取代。
4. 批量探测（多 payload/路径/参数）一律用 fuzz；互不依赖的动作用 parallel_shell；
   多个独立分支用 spawn_subtask。shell 只用于 fuzz 覆盖不了的场景。
5. 发现 flag 系统会机械代提交并回执：correct=true 且有剩余面数→继续找下一面；
   全部通关系统会自动结束本题。
6. 卡壳时：先 find_skills / list_knowledge 查打法；提示来了先深度分析再动手。
7. 拿到可复用攻击链后用 remember 沉淀 POC/知识/技能（只在真正有价值时）。
8. 阶段随进展用 set_phase 切换；任务完成或证据枯竭时调用 finalize 提交结论。
```

**删除的 8 条去哪了**：VPN（已由调度器/入口管）、enable_tool 说明（工具描述自带）、checkpoint 提醒（价值低）、finalize 单独条（并入 8）、压缩摘要配合条（并入 3）、hint 条（并入 6）——**凡是代码已机械保证的纪律，从 prompt 里删除**。prompt 只留代码管不了的部分。

---

## R7（小伤）：压缩后防孤儿 tool 响应

### core/context_manager.py —— `compact_if_needed` 尾部加固

```python
    # 清空会话，只保留最近若干条；摘要走系统提示，不占 session item
    await session.clear_session()
    # 防孤儿：recent 首条若是 tool 响应（function_call_output），
    # 其配对的 assistant tool_calls 已被裁掉，OpenAI 会 400——丢弃前导孤儿
    while recent and _is_orphan_tool_output(recent[0]):
        recent = recent[1:]
    await session.add_items(recent)
```

新增辅助函数：

```python
def _is_orphan_tool_output(item: Any) -> bool:
    """首条消息是游离的 tool 输出（无配对 assistant 调用）判定。"""
    if isinstance(item, dict):
        return item.get("type") == "function_call_output" or (
            item.get("role") == "tool")
    return getattr(item, "type", "") == "function_call_output"
```

---

## 验证清单（改完逐项核对）

| 观察点 | 通过标准 | 对应修复 |
|---|---|---|
| 通关即走 | 铁律提交最后一面后，当轮日志出现 `[系统·通关判决]`，单题循环立即退出 | R1 |
| 多 flag 继续 | 提交回执含 `correct_flag_count < total_flag_count` 时，提示"继续找下一面"且不 finalize | R1 |
| 增量收窄 | 连续 curl 200 不再产生 `reward score=1`；ffuf 发现 ≥2 条新路径才产生 | R2 |
| hint 恢复触发 | Web 题卡壳后第 6/8/10 轮（按难度）日志出现 `[hint]` | R2 |
| 终局零丢失 | 任务结束时刻走铁律提交，日志出现 `fatal=task_ended`，全场立即停 | R3 |
| 档案按题 | 重试 a-05 时注入的系统提示里只有 a-05/a-0x 段落 | R4 |
| brief 进度 | 单题 brief 含"flag 进度：已拿 M/N 面" | R5 |
| prompt 瘦身 | 单轮 input token 较改前再降（纪律段约省 1500 字） | R6 |
| 压缩不崩 | 触发 compact 后下一轮 LLM 调用不报 400 | R7 |

---

## 改动文件清单

| 文件 | 动作 | 对应 |
|---|---|---|
| `demo_tools.py`（项目根目录） | 改（铁律整函数替换 + 单例 + 通关判决） | R1/R3 |
| `core/hooks.py` | 改（增量打分 v2 + 调用处签名） | R2 |
| `core/task_context.py` | 改（加 seen_signatures/submitted/correct_flags） | R1/R2 |
| `app/main.py` | 改（题级沉淀/按题检索/brief 进度/参数透传） | R4/R5 |
| `core/agents_def.py` | 改（纪律 16→8） | R6 |
| `core/context_manager.py` | 改（孤儿防护） | R7 |

**预期效果**（对照上版评估）：通关题当轮退出——按 easy 题 suspend 档 100 万 token 算，每道 easy 通关题省下的空烧足以多打 2~3 题；hint/skip 对 Web 题恢复生效——卡壳题在第 6~10 轮获得平台提示而不是空转到换题档；终局信号在两条提交路径上行为一致，比赛结束时刻零延迟收工。

*改完这七条，上一轮评估的残留清单清零，系统达到实战状态。*
