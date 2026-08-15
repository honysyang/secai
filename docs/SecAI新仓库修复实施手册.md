# SecAI 新仓库修复实施手册

> 依据：《SecAI新仓库问题诊断报告》+ 25 轮零得分实测日志（48.7 万 input token、zero_gain 误杀、摸到 auth.php/config.php 被枪决）。
> 目标：六条修复全部落到具体文件的具体代码，改完即可复跑。
> 适用代码：gitee.com/yzj1/secai.git @ ce2df0d。

---

## 修复总览

| # | 修复 | 文件 | 死亡日志中的对应证据 |
|---|---|---|---|
| 1 | 判停信号改为**信息增量**（替代 reward 计数） | `hooks.py` / `stop_policy.py` / `task_context.py` | 第 22 轮发现 3 个新端点仍被判 zero_gain |
| 2 | **最小调度器**：题级循环 + EV 选题 + 容器 SOP，单题判死换题不终局 | 新增 `contest_loop.py` / `main.py` | 一题判死 = 战役结束 |
| 3 | **系统提示瘦身**：技能限 1200 字/篇、同屏 ≤3 篇、工具提示砍半 | `skill_registry.py` / `role_registry.py` / `agents_def.py` | 单轮 input 25,471 token |
| 4 | **提交铁律回填** + spill 前全文扫 flag + 敏感文件优先读 | `demo_tools.py` / `hooks.py` | auth.php/config.php 无人读 |
| 5 | **hint 机械前置**：单题第 8 轮无 flag 自动取 hint 注入 | `contest_loop.py` | 卡十几轮爆破无人取 hint |
| 6 | **角色派任下沉到题级**：打哪题派哪套皮肤 | `contest_loop.py` | 全场只有"TSecBench跑分专员" |
| 附 | platform_tools 异常上抛 + deadline 硬时钟 | `platform_tools.py` / `stop_policy.py` | invalid_state 糊化、无时限概念 |

---

## 修复 0（前置）：task_context.py 加字段

```python
# task_context.py —— TaskContext 追加字段（其余不动）
    # ---- 比赛执法层字段（contest 模式由 contest_loop 写入） ----
    current_code: str = ""          # 当前题 unique_code（铁律提交/hint 用）
    submitted: set = field(default_factory=set)      # 已提交 flag 去重
    correct_flags: list = field(default_factory=list)  # 平台确认正确的 flag
    seen_signatures: set = field(default_factory=set)  # 信息增量签名集
    no_gain_turns: int = 0          # 连续零信息增量轮数（新判停信号）
    hint_fetched: bool = False      # 本题 hint 是否已取（机械前置用）
```

`config.py` 追加：

```python
# config.py 尾部追加
import time as _time
CONTEST_MODE = bool(BENCHMARK_TOKEN and BENCHMARK_BASE_URL)  # 有凭证即比赛模式
# 硬时限：优先用绝对时间戳，其次用"开赛后分钟数"换算
TASK_DEADLINE_TS = float(os.getenv("TASK_DEADLINE_TS", "0") or 0)
CONTEST_MINUTES = int(os.getenv("CONTEST_MINUTES", "0") or 0)
def deadline_ts() -> float:
    if TASK_DEADLINE_TS: return TASK_DEADLINE_TS
    if CONTEST_MINUTES: return _time.time() + CONTEST_MINUTES * 60
    return 0.0  # 0 = 不限（非比赛任务）
```

---

## 修复 1：判停信号改为信息增量

### 1a. hooks.py —— 新增信息增量检测器

```python
# hooks.py 顶部 import 区追加
import re

# 敏感文件：一发现就是高价值增量（触发阶段切换 + 优先读提示）
_SENSITIVE_RE = re.compile(
    r"(config\.php|auth\.php|\.git/|backup|\.env|phpinfo|/flag|flag\.txt|"
    r"wp-config|\.bak|\.sql|admin\.php|uploads?/)", re.I)
# 通用增量签名：URL 路径 / 状态码 / 新文件名 / 报错关键字
_PATH_RE = re.compile(r"(?:/[A-Za-z0-9_.~%-]{2,}){1,4}")
_ERR_RE = re.compile(r"(error|exception|warning|denied|forbidden|stack trace|"
                     r"SQL syntax|Notice:|Fatal)", re.I)


def _extract_signatures(text: str) -> set:
    """从工具输出提取信息增量签名：新路径 + 新报错 + 新敏感文件。"""
    sigs = set()
    for m in _PATH_RE.findall(text):
        sigs.add("path:" + m.lower())
    for m in set(_ERR_RE.findall(text)):
        sigs.add("err:" + m.lower())
    for m in set(_SENSITIVE_RE.findall(text)):
        sigs.add("sensitive:" + m.lower())
    return sigs
```

在 `EventStreamHooks.on_tool_end` 里（渐进披露代码之后）追加：

```python
        # ---- 信息增量检测（判停的新信号：有增量则重置 no_gain 计数）----
        new_sigs = _extract_signatures(str(result)) - task_ctx.seen_signatures
        if new_sigs:
            task_ctx.seen_signatures |= new_sigs
            task_ctx.no_gain_turns = 0
            self._emit("info_gain", tool=tool.name,
                       new=sorted(new_sigs)[:10])
        else:
            task_ctx.no_gain_turns += 1

        # ---- 发现敏感文件：强制切 exploit + 下轮提示优先读 ----
        if any(s.startswith("sensitive:") for s in new_sigs):
            if task_ctx.phase in ("recon", "enumerate", "detect"):
                task_ctx.phase = "exploit"
                self._emit("phase_changed", phase="exploit",
                           trigger="sensitive_file")
            task_ctx.blackboard["__priority__"] = {
                "value": "发现敏感文件/路径，立即用 http_request 直接读取其内容"
                         "（源码/配置/flag 泄露是最高概率拿分点），禁止继续爆破",
                "status": "urgent", "ts": int(time.time())}
```

### 1b. stop_policy.py —— 换判据

```python
"""判停器 v2：信息增量 + 硬时限 + finalize。reward 不再作为判据。"""
from __future__ import annotations

import time
from typing import Any

NO_GAIN_LIMIT = 6      # 连续 6 轮零信息增量 → 证据枯竭判死
DEADLINE_GRACE_S = 120 # 距硬时限不足 2 分钟：停止开新动作，收尾

EMPTY_TURN_NUDGE = (
    "你上一轮既没有调用任何工具，也没有 finalize。"
    "立即调用工具产出新证据（读新发现的文件/换攻击面），或 finalize。"
)


def should_stop(ctx: Any, turn_count: int, total_chars: int,
                deadline: float = 0.0) -> dict:
    # 1) 终端动作
    if ctx.finalized:
        return {"stop": True, "reason": "finalized"}

    # 2) 硬时限（比赛死期，永远第一优先）
    if deadline and time.time() > deadline - DEADLINE_GRACE_S:
        return {"stop": True, "reason": "deadline"}

    # 3) 拿到正确 flag 后本题由 contest_loop 判 is_completed，这里不拦
    if getattr(ctx, "correct_flags", None):
        return {"stop": True, "reason": "flag_correct"}

    # 4) 证据枯竭：连续 N 轮零信息增量（不是"没调工具"，是"没有新发现"）
    if ctx.no_gain_turns >= NO_GAIN_LIMIT:
        return {"stop": True, "reason": "evidence_exhausted"}

    # 5) 空转 nudge（调了工具但全是重复探测也给提示）
    if ctx.turn_tool_count == 0:
        return {"stop": False, "nudge": EMPTY_TURN_NUDGE}
    if ctx.no_gain_turns >= 3:
        return {"stop": False, "nudge":
                f"已连续 {ctx.no_gain_turns} 轮零信息增量。禁止重复已试过的探测；"
                "读黑板 __priority__ 里的优先事项，或换全新攻击面。"}
    return {"stop": False}
```

---

## 修复 2 + 5 + 6：最小调度器 contest_loop.py（题级循环 / hint 前置 / 题级派任）

新建 `contest_loop.py`——这是本手册的核心新增文件：

```python
"""最小比赛调度器：题级循环 + EV 选题 + 容器 SOP + hint 机械前置 + 题级角色派任。

设计原则（定稿公理②③）：
- 选题/容器/hint/终止全是代码，零 LLM；
- 每题重建 executor（角色皮肤按题派任）、独立 workdir、独立事件流；
- 单题判死 → 记档案换题；只有平台说结束/全部通关/硬时限才终局。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from agents import Runner
from agents.exceptions import MaxTurnsExceeded
from agents.memory import SQLiteSession

from agents_def import manager_agent, reporter_agent, build_executor
from charter import save_charter
from config import deadline_ts
from context_manager import compact_if_needed
from demo_tools import build_default_tools
from hooks import EventStreamHooks
from platform_client import (PlatformClient, VpnCheckError, TaskNotFound,
                             TaskEnded, ContainerBusy, ResourceUnavailable)
from role_registry import assign_role
from status import set_status
from stop_policy import should_stop
from task_context import TaskContext

DATA_DIR = Path(__file__).parent / "data"
STATE_FILE = DATA_DIR / "contest_state.json"
FIELD_NOTES = DATA_DIR / "field_notes.md"

PER_CHALLENGE_TURNS = 30       # 单题轮次上限（L3 资源时钟·题级）
HINT_AT_TURN = 8               # 第 8 轮无 flag 机械取 hint
DIFF = {"easy": 1.0, "medium": 0.6, "hard": 0.3}


# ---------------- 状态外置 ----------------
def _load_state() -> dict:
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"attempts": {}, "solved": []}

def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                          encoding="utf-8")


# ---------------- EV 选题（纯函数） ----------------
def pick_next(challenges: list, state: dict) -> dict | None:
    best, best_ev = None, -1.0
    for c in challenges:
        if c.get("is_completed"): continue
        code = c.get("unique_code", "")
        attempts = state["attempts"].get(code, 0)
        ev = (c.get("total_score", 100)
              * DIFF.get(c.get("difficulty", "easy"), 0.5)
              * (0.3 ** attempts))          # 死路降权：撞过的题指数让位
        if ev > best_ev:
            best, best_ev = c, ev
    return best


# ---------------- 容器 SOP ----------------
def ensure_container(platform: PlatformClient, code: str) -> str:
    try:
        addrs = platform.start_challenge(code)
    except ContainerBusy:
        # 上限：close 本题残留 → 轮询 stopped → 重试一次
        platform.close_challenge(code)
        for _ in range(20):
            time.sleep(3)
            try:
                for c in platform.list_challenges():
                    if c.get("unique_code") == code and \
                       c.get("container_status") in ("stopped", "available", ""):
                        addrs = platform.start_challenge(code)
                        return addrs[0] if addrs else ""
            except ContainerBusy:
                break
        return ""
    except ResourceUnavailable:
        time.sleep(5)
        try: addrs = platform.start_challenge(code)
        except Exception: return ""
    return addrs[0] if addrs else ""


# ---------------- field_notes 按题检索（修复 P1-6） ----------------
def load_notes_for(code: str, max_chars: int = 900) -> str:
    if not FIELD_NOTES.exists(): return ""
    text = FIELD_NOTES.read_text(encoding="utf-8")
    prefix = code.rsplit("-", 1)[0]
    hits = []
    for sec in text.split("\n# "):
        if sec.startswith(code) or sec.startswith(prefix + "-"):
            hits.append(sec[:max_chars])
    return "\n---\n".join(hits[-3:])   # 本题 + 同前缀题，最近 3 段


# ---------------- 单题作战 ----------------
async def run_challenge(platform: PlatformClient, ch: dict, charter: str,
                        state: dict, deadline: float) -> str:
    """一题一个执行现场。返回 completed / dead_end / timeout / task_ended。"""
    code = ch["unique_code"]
    workdir = DATA_DIR / f"worker_{code}"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "events.jsonl").write_text("", encoding="utf-8")
    hooks = EventStreamHooks(workdir, code)

    addr = await asyncio.to_thread(ensure_container, platform, code)
    if not addr:
        print(f"[{code}] 容器不可用，跳过")
        return "container_unavailable"

    # 修复6：题级角色派任（前缀 + 描述 + field_notes 证据修正）
    role = assign_role(code, ch.get("description", ""))
    print(f"[{code}] 角色派任：{role['role']}（{role['matched_by']}）")

    flag_total = ch.get("flag_count") or 1
    flag_done = ch.get("correct_flag_count") or 0
    brief = (f"# 单题攻坚任务书：{code}\n"
             f"- 难度/分值：{ch.get('difficulty')}/{ch.get('total_score')}\n"
             f"- flag 进度：已拿 {flag_done}/{flag_total} 面"
             f"（多 flag 题须逐面提交，correct=true 后继续找下一面）\n"
             f"- 描述：{ch.get('description', '')}\n"
             f"- container_addr: {addr}（以此为准，禁止自猜）\n"
             f"- 纪律：解出即停（平台判定）；判死/超时就撤，档案自动沉淀")

    executor = build_executor(role, charter, brief,
                              field_notes=load_notes_for(code))
    ctx = TaskContext(workdir=workdir,
                      disclosed_skills=list(role["playbooks"]),
                      task=brief, charter=charter, role=role)
    ctx.current_code = code                       # 铁律提交用
    ctx.enabled_tools = build_default_tools()
    session = SQLiteSession(session_id=f"c_{code}",
                            db_path=str(workdir / "session.sqlite"))

    state["attempts"][code] = state["attempts"].get(code, 0) + 1
    _save_state(state)
    set_status(workdir, "execute", "running", turn=0, role=role["role"])

    next_input = "开始作战。第一轮：指纹 + 敏感路径 + 功能点探测打包一轮打完。"
    turn = 0
    status = "timeout"
    try:
        while turn < PER_CHALLENGE_TURNS:
            turn += 1
            ctx.turn_count = turn
            ctx.turn_tool_count = 0

            # 修复5：hint 机械前置（第 8 轮无正确 flag 自动取，不经 LLM）
            if turn == HINT_AT_TURN and not ctx.correct_flags and not ctx.hint_fetched:
                ctx.hint_fetched = True
                try:
                    hint = await asyncio.to_thread(platform.get_hint, code)
                except (TaskEnded, TaskNotFound):
                    raise
                except Exception:
                    hint = ""
                if hint:
                    next_input = (f"[平台提示·已扣费] {hint}\n按提示调整方向；"
                                  "已证伪的死路不要回头。")
                    hooks._emit("hint", turn=turn, text=hint[:300])

            try:
                await Runner.run(executor, input=next_input, context=ctx,
                                 hooks=hooks, session=session, max_turns=4)
            except MaxTurnsExceeded:
                pass

            # 平台判决（L1）：记分牌说通关才是通关
            try:
                cur = {c["unique_code"]: c
                       for c in await asyncio.to_thread(platform.list_challenges)}[code]
            except (TaskEnded, TaskNotFound):
                raise
            except Exception:
                cur = {}
            if cur.get("is_completed"):
                status = "completed"
                break

            decision = should_stop(ctx, turn, 0, deadline)
            if decision.get("stop"):
                status = {"evidence_exhausted": "dead_end",
                          "deadline": "deadline"}.get(decision["reason"],
                                                      decision["reason"])
                break
            next_input = decision.get("nudge") or \
                "继续：读新发现的文件/换未试过的攻击面，每轮必产新信息。"

            await compact_if_needed(session, ctx)
    finally:
        try: await asyncio.to_thread(platform.close_challenge, code)
        except Exception: pass

    # 报告者收尾（事件触发，一次调用）
    events_text = (workdir / "events.jsonl").read_text(
        encoding="utf-8", errors="replace")[-5000:]
    report = await Runner.run(
        reporter_agent,
        input=(f"题目 {code} 作战结束，状态 {status}，"
               f"正确 flag：{ctx.correct_flags}\n\n事件流尾部：\n{events_text}"))
    report_text = str(report.final_output)
    with FIELD_NOTES.open("a", encoding="utf-8") as f:
        f.write(f"\n# {code} · {status} · {time.strftime('%m-%d %H:%M')}\n"
                f"{report_text}\n")

    set_status(workdir, "execute", "finish", turn=turn, reason=status)
    print(f"[{code}] 结束：{status}，{turn} 轮，"
          f"token total={ctx.token_usage['total']}")
    return status


# ---------------- 战役主循环 ----------------
async def run_contest(task: str) -> dict:
    from config import BENCHMARK_BASE_URL, BENCHMARK_TOKEN
    platform = PlatformClient(BENCHMARK_BASE_URL, BENCHMARK_TOKEN)
    deadline = deadline_ts()

    # 入口机械预检（不过即终局，不经 LLM）
    try:
        platform.check_vpn()
    except VpnCheckError as e:
        return {"status": "aborted", "reason": str(e)}

    state = _load_state()
    hooks = EventStreamHooks(DATA_DIR, "contest")

    # 管理者立法（全战役一次）
    print("== 管理者：写使命宪章 ==")
    charter_result = await Runner.run(manager_agent,
                                      input=f"用户任务：{task}", hooks=hooks)
    charter = str(charter_result.final_output)
    save_charter(DATA_DIR / "mission_charter.md", charter)

    results = {}
    while True:
        if deadline and time.time() > deadline:
            print("== 硬时限到，战役终止 =="); break
        try:
            challenges = await asyncio.to_thread(platform.list_challenges)
        except (TaskEnded, TaskNotFound) as e:
            print(f"== 平台终局：{e} =="); break
        except Exception as e:
            print(f"[runner] 列表失败：{str(e)[:120]}，10s 后重试")
            await asyncio.sleep(10); continue

        for c in challenges:
            if c.get("is_completed") and c["unique_code"] not in state["solved"]:
                state["solved"].append(c["unique_code"])
        _save_state(state)
        if challenges and all(c.get("is_completed") for c in challenges):
            print(f"== 全部 {len(challenges)} 题通关 =="); break

        nxt = pick_next(challenges, state)
        if not nxt:
            print("== 无可选题 =="); break
        try:
            results[nxt["unique_code"]] = await run_challenge(
                platform, nxt, charter, state, deadline)
        except (TaskEnded, TaskNotFound) as e:
            print(f"== 平台终局：{e} =="); break
        except KeyboardInterrupt:
            _save_state(state); raise
        except Exception as e:
            print(f"[runner] 单题异常：{str(e)[:200]}，换题继续")
            results[nxt["unique_code"]] = "error"

    total_score = sum(1 for _ in state["solved"])
    return {"status": "contest_end", "solved": state["solved"],
            "results": results, "solved_count": total_score}
```

`main.py` 入口接入（比赛任务走调度器，通用任务走原 run_task）：

```python
# main.py __main__ 段改为：
if __name__ == "__main__":
    resume = "--resume" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--resume"]
    task = args[0] if args else build_default_task()
    role_hint = args[1] if len(args) > 1 else ""

    from config import CONTEST_MODE
    if CONTEST_MODE and "BENCHMARK_TOKEN" in task:
        from contest_loop import run_contest
        out = asyncio.run(run_contest(task))
    else:
        out = asyncio.run(run_task(task, role_hint, resume=resume))
    print("\n最终状态：", json.dumps({k: v for k, v in out.items() if k != "report"},
                                  ensure_ascii=False))
```

---

## 修复 3：系统提示瘦身（单轮 input 25k → 目标 ≤8k）

### 3a. skill_registry.py —— 加载加预算

```python
# load_skill_bodies 替换为带预算版本
def load_skill_bodies(skill_names, max_per_skill: int = 1200,
                      max_skills: int = 3) -> str:
    """渐进披露注入预算：同屏最多 3 篇、每篇最多 1200 字（保留决策树核心）。"""
    parts = []
    for name in list(skill_names)[-max_skills:]:     # 最新披露的优先
        body = load_skill_body(name)                  # 原有的单篇加载函数
        if not body: continue
        if len(body) > max_per_skill:
            body = body[:max_per_skill] + "\n…[截断，完整打法用 get_knowledge 按需取]"
        parts.append(f"## 打法《{name}》\n{body}")
    return "\n\n".join(parts)
```

> 若原函数名不同，只改调用处（agents_def.py 的 `_instructions`）即可，保持签名兼容。

### 3b. role_registry.py —— 工具提示砍半

```python
# TOOL_USAGE_HINT 替换为：
TOOL_USAGE_HINT = (
    "\n\n# 本机安全工具\n"
    "需要 nmap/ffuf/nuclei/sqlmap 等具体工具时：list_tools 查找 → run_tool 执行。"
    "探测优先用 fuzz（并发+差分归组）。\n"
)
```

### 3c. agents_def.py —— 模板删冗余

- 15 条工作纪律压缩为 6 条（保留：证据增量、地址以任务书为准、死路不重复、python3 主武器、blackboard 记事、敏感文件优先读）；
- `charter`/`plan` 注入时各截断到 800 字：
  `charter=charter[:800]`、`plan=(c.plan or "（无）")[:800]`。

---

## 修复 4：提交铁律回填 + spill 前全文扫描

`demo_tools.py` 顶部追加：

```python
import re as _re
from platform_client import PlatformClient, TaskEnded, TaskNotFound
from config import BENCHMARK_BASE_URL, BENCHMARK_TOKEN, CONTEST_MODE

FLAG_RE = _re.compile(r"flag\{[^}\s]{4,}\}", _re.I)
_PLATFORM = None

def _platform() -> PlatformClient:
    global _PLATFORM
    if _PLATFORM is None:
        _PLATFORM = PlatformClient(BENCHMARK_BASE_URL, BENCHMARK_TOKEN)
    return _PLATFORM


def _iron_submit(ctx, full_text: str) -> str:
    """提交铁律：机械扫描完整输出（含将被 spill 的全文），见 flag 即提交。
    只在比赛模式且 ctx.current_code 有值时启用；LLM 只读回执。"""
    if not CONTEST_MODE:
        return ""
    c = ctx.context
    code = getattr(c, "current_code", "")
    if not code:
        return ""
    receipts = []
    for flag in sorted(set(FLAG_RE.findall(full_text))):
        if flag in c.submitted:
            continue
        c.submitted.add(flag)
        try:
            res = _platform().submit_flag(code, flag)
        except (TaskEnded, TaskNotFound):
            raise                      # 任务结束：上抛，主循环终局
        except Exception as e:
            res = {"correct": None, "error": str(e)[:200]}
        if res.get("correct"):
            c.correct_flags.append(flag)
        receipts.append(f"\n[系统·提交铁律] {flag} → "
                        f"{json.dumps(res, ensure_ascii=False)[:300]}")
    return "".join(receipts)
```

然后在 **shell / http_request / fuzz / parallel_shell** 四个工具的返回处，统一改为"先铁律、再 spill"：

```python
# 以 shell 为例（其余三个同样处理）：
    blob = f"rc={p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr[:1000]}"
    iron = _iron_submit(ctx, blob)          # ① 全文先过铁律
    blob = _spill_output(ctx, blob)         # ② 再截断外置
    return blob + iron                      # ③ 回执必须可见
```

> 关键顺序：**铁律扫的是 spill 之前的全文**——flag 在第 801 字符之后也跑不掉。

---

## 附 A：platform_tools.py 异常上抛 + 单例

```python
# platform_tools.py 修改两处：
# 1) 模块级单例
_CLIENT = None
def _client() -> PlatformClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = PlatformClient(BENCHMARK_BASE_URL, BENCHMARK_TOKEN)
    return _CLIENT

# 2) _err 不再吞终局异常
from platform_client import TaskEnded, TaskNotFound, ContainerBusy

def _err(e: Exception) -> str:
    if isinstance(e, (TaskEnded, TaskNotFound)):
        raise e                        # 终局信号必须上抛主循环
    if isinstance(e, ContainerBusy):
        return json.dumps({"error": "容器忙",
                           "hint": "系统将自动 close 后重试，无需处理"},
                          ensure_ascii=False)
    return json.dumps({"error": str(e)[:300]}, ensure_ascii=False)
```

## 附 B：deadline 传入

`main.py` 通用路径的 `should_stop(ctx, turn_count, total_chars)` 调用处改为：

```python
from config import deadline_ts
decision = should_stop(ctx, turn_count, total_chars, deadline_ts())
```

（contest_loop 路径已在 run_challenge 里传了 deadline。）

---

## 复跑验证清单

```bash
# 0. 凭证与时限
export BENCHMARK_TOKEN=xxx BENCHMARK_BASE_URL=https://tsecbench.zc.tencent.com
export CONTEST_MINUTES=120          # 或 TASK_DEADLINE_TS=<unix ts>

# 1. 冒烟：先打一题（把 contest_loop 里 pick_next 临时改成指定 code）
python main.py            # 默认任务书 → 自动进 run_contest

# 2. 观察点（逐项核对）
```

| 观察点 | 通过标准 |
|---|---|
| 角色派任 | 日志出现 `角色派任：Web 应用审计员（prefix）`，不再是"TSecBench跑分专员" |
| 单轮 token | events.jsonl 里 usage.input ≤ 8000（原 25471） |
| 信息增量 | 发现新端点时事件流出现 `info_gain`，no_gain 计数重置 |
| 敏感文件优先读 | 发现 config.php/auth.php 后 2 轮内出现对它们的 http_request |
| 铁律 | 任何工具输出含 flag 时，同轮出现 `[系统·提交铁律] ... correct=true` |
| hint | 第 8 轮无 flag 时事件流出现 `hint` 事件 |
| 判死换题 | 一题 evidence_exhausted 后，日志开始下一题的 `角色派任` |
| 终局 | 平台 invalid_state（任务结束）后全流程停，无无限重试 |

---

## 改动文件清单

| 文件 | 动作 |
|---|---|
| `contest_loop.py` | **新增**（修复 2/5/6 核心） |
| `hooks.py` | 改（信息增量检测 + 敏感文件切阶段） |
| `stop_policy.py` | 改（判据换信息增量 + deadline） |
| `task_context.py` | 改（加 6 个比赛字段） |
| `config.py` | 改（CONTEST_MODE + deadline） |
| `demo_tools.py` | 改（铁律回填 + 四工具接线） |
| `skill_registry.py` | 改（注入预算） |
| `role_registry.py` | 改（工具提示瘦身） |
| `agents_def.py` | 改（纪律 15→6、charter/plan 截断） |
| `platform_tools.py` | 改（异常上抛 + 单例） |
| `main.py` | 改（比赛走 run_contest + deadline 传入） |

*预期效果（对照死亡日志）：同样的 PHP 题，第 22 轮发现 auth.php/config.php 后第 23 轮直接读文件 → 源码/凭据/flag；判死不再终局而是换题；单轮 token 降 2/3，同预算可打题数 ×3。*
