# SECAI 架构设计文档

> SECAI-PT v4（SECAI「破阵」终极执行计划 v4 全量实施后）技术架构与工程设计说明
> 版本：v4.0 ｜ 更新：2026-09-06 ｜ 与代码现状同步

---

## 目录

1. [文档定位与分工](#1-文档定位与分工)
2. [设计思想与核心原则](#2-设计思想与核心原则)
3. [总体架构（六层 L0–L6）](#3-总体架构六层-l0l6)
4. [多智能体体系](#4-多智能体体系)
5. [工作模式与单题执行循环](#5-工作模式与单题执行循环)
6. [调度器（跑分编排，零 LLM）](#6-调度器跑分编排零-llm)
7. [pentest 数据模型（L0/L1）](#7-pentest-数据模型-l0l1)
8. [L5 护栏层（Scope / 审批 / 沙箱）](#8-l5-护栏层scope--审批--沙箱)
9. [L4 知识层（三路知识获取）](#9-l4-知识层三路知识获取)
10. [L6 学习层（蒸馏与成长）](#10-l6-学习层蒸馏与成长)
11. [Verifier 双核](#11-verifier-双核)
12. [server + 前端（Web 控制面）](#12-server--前端web-控制面)
13. [CTF 兼容（profiles/ctf_legacy 收敛）](#13-ctf-兼容profilesctf_legacy-收敛)
14. [关键设计决策与教训](#14-关键设计决策与教训)

---

## 1. 文档定位与分工

| 文档 | 定位 | 分工 |
|---|---|---|
| **README.md**（仓库根） | 概览 + 运维入口 | 六章：系统架构（L0–L6/双核 Verifier/SessionManager/前后端拓扑）、功能模块、部署与运行（venv/.env/server/前端/gate.sh/-m e2e）、日志监控、安全机制、性能与缓存；目录结构树见 README 第 7 章 |
| **本文档** | 设计 + 规格 | 设计思想、六层架构、数据模型逐文件规格、L5/L4/L6 深度设计、Verifier、server+前端契约、CTF 收敛、关键决策与教训 |

两者口径统一；发现不一致时**以代码为准**（docstring 是最新事实源）。

---

## 2. 设计思想与核心原则

### 2.1 五条公理（仍有效）

| 公理 | 含义 | 落地 |
|---|---|---|
| ① 提交是机械保证 | flag 提交不靠 LLM 自觉 | `profiles/ctf_legacy/platform.py`（`_submit_flags_if_any`）经 `core/tool_pipeline.py` 的 AutoSubmitFlag 中间件执行 |
| ② 调度是纯函数零 LLM | 选题/换题/看 hint/容器管理是代码 | `bench_platform/scheduler.py` |
| ③ LLM 只做假设与解读 | LLM 不承担机械决策 | 调度器 + Verifier + 黑板事实不变量接管 |
| ④ 状态外置、重启无感 | 进度持久化 | checkpoint + SQLiteSession + 黑板（`data/`）+ `mission_charter` 幂等缓存 |
| ⑤ 观测是状态的投影 | 事件流 = 可观测事实源 | `core/events.py` BUS → hooks → SQLite（唯一真相源）→ UI |

### 2.2 v4 设计裁决（终极执行计划 v4 第一部分，代码现状印证）

| # | 议题 | 裁决 → 落点 |
|---|---|---|
| 1 | 双核 | 不做硬架构：Verifier 可插拔（`harness/runner/verifier.py`：Mechanical 默认 / LLM 低频） |
| 2 | kill-chain 五阶段 | 不做控制流状态机：PTES 阶段仅作 blackboard fact_key category 命名空间（`pentest/blackboard/categories.py`） |
| 3 | 三 Provider ABC | 不做：ModelPool 已是事实 Provider 边界（`runtime/model_pool.py`），ToolMiddleware ABC 已存在（`core/tool_pipeline.py`） |
| 4 | 多任务并行 | 每目标 = 独立 Session/ExecutorLoop，SessionManager 统一管理（`harness/session_manager.py`），前端 mux WS 同时监视 |
| 5 | 前端 | React + CSS Modules + `--secai-*` Design Token + 三栏布局（`apps/web/`）；禁 Tailwind/SCSS |
| 6 | 网络搜索 | 三层知识获取：本地 RAG（离线）+ web_search（T1 自动）+ web_fetch（T2 审批，SSRF 防护）→ `pentest/knowledge/` |
| 7 | 学习成长 | L6 学习层：每 engagement 结束后自动蒸馏沉淀、跨任务复用 → `pentest/learning/` |

### 2.3 关键设计思想

1. **让 LLM 做高层判断，让代码做确定性动作** —— 侦察/爆破预算/审批分级/提交/判停全代码化。
2. **护栏是机械化强制执行点，不是 prompt 自约束** —— `pentest/presets/pentest_preset.py`
   的 persona.disciplines 明示：ScopeCheck、DeadEnd、黑板事实是文字背后的强制执行点。
3. **fail-closed 优先** —— 沙箱后端不可用禁止裸跑；ScopeConstraint 解析失败抛错不放行；
   web_fetch 解析不了即拒绝（防 DNS rebinding）。
4. **观测单一真相源** —— SQLite 为唯一真相源，events.jsonl 仅人读留痕（崩溃现场除外）。
5. **止血 > 增产 > 降本** —— 先修丢分项（R0-R6 把 H1-H13 全结清），再做吞吐/降本优化。

---

## 3. 总体架构（六层 L0–L6）

### 3.1 六层架构

```
┌──────────────────────────────────────────────────────────────┐
│ L6 学习层   pentest/learning/：经验蒸馏引擎 + 剧本库 + 负面知识 │
│              + 成长度量（lessons/playbooks/negatives/metrics） │
├──────────────────────────────────────────────────────────────┤
│ L5 护栏层   pentest/scope.py（纯函数）+ pentest/approval.py    │
│              （T1-T4 分级 + HITL 审批）+ sandbox/（bwrap       │
│              fail-closed）+ presets 工具分级词表 + 审计         │
├──────────────────────────────────────────────────────────────┤
│ L4 知识层   pentest/knowledge/：local_rag（离线）+ web_search   │
│              （T1 自动）+ web_fetch（T2 审批, SSRF 防护）       │
│              + sources/（cve_db/tool_manuals/methodology/     │
│              negative_findings）                              │
├──────────────────────────────────────────────────────────────┤
│ L3 工具层   pentest/tool_adapters/：ToolAdapter 协议 + 14 适配 │
│             器（parse_output 结构化提取 + build_command 注入   │
│             校验）；执行侧 sec_tools（92 个 CLI YAML）          │
├──────────────────────────────────────────────────────────────┤
│ L2 编排层   harness/：SessionManager（多目标并行）+             │
│              ExecutorLoop（pre/step/post）+ Verifier（双核）   │
│              + subtasks 三闸门 + pool（全局模型池）            │
├──────────────────────────────────────────────────────────────┤
│ L1 认知层   pentest/target_profile.py + hypothesis.py +        │
│              deadends.py + blackboard/ → SQLite durable        │
│              （pentest.db 五表，跨 session 存活）               │
├──────────────────────────────────────────────────────────────┤
│ L0 方法论层 pentest/blackboard/categories.py（PTES fact_key    │
│             命名空间）+ pentest/presets/pentest_preset.py      │
│             （词汇表，非控制流）                               │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 两条执行面与核心数据流

**执行面 A：授权渗透 Web（v4 主线）** —— `python -m server.main --port 8700`

```
任务书（前端 → POST /api/run）
  → server/run_spec.py 归一（task_brief/targets/scope/settings；预算护栏收敛）
  → harness.SessionManager.start_target()（每目标独立 Session/总线/state，并行）
  → harness/runner/pentest_target.py（ScopeCheck → T2 审批门(approval/requested 帧)
      → 只读工具白名单执行 → 黑板）→ 事件经 BUS 扇出 mux WS
  → /api/report 桥接 R5 报告引擎（纯函数投影）
```

**执行面 B：CTF 跑分/通用 CLI（legacy，保留）** —— `python -m app.main`

```
app/main.py run_task 调度循环（while True / _run_one / _endgame_sweep，
结构被 tests/test_core.py AST 锁定）
  ├─ legislate_charter（harness/runner/orchestrator.py：战略家立法+规划，幂等缓存）
  ├─ 单题闭环 → harness/runner/executor.py run_single_challenge() → ExecutorLoop.run()
  │    ├─ RunnerState(pre/step/post) + _pre_step/_step/_post_step 直驱
  │    ├─ 上下文增量注入（charter/plan 版本化 + field_notes 仅首轮 + 黑板 diff）
  │    ├─ 停滞治理：hint（scheduler 机械阈值）→ fork_analyze（3 轮零增量，≤1 次）→ 机械换题
  │    ├─ 成本治理：爆破预算 / 换脑 switch / 挂起 suspend（runtime/budget.py）
  │    ├─ 后台子任务：三闸门（subtasks.py）+ 情报 sub_intel.jsonl 合并（context.py）
  │    └─ 收尾：cost_report / trajectory / stuck-replay / dashboard（runtime/reporting.py）
  ├─ finalize_report（战报 + field_notes 机械沉淀 + 四指标看板）
  └─ profiles/ctf_legacy：提交铁律 / 通关复核 / finalize（CTF 假设全收敛）
```

### 3.3 核心模块职责（v4 现状）

| 模块 | 路径 | 职责 |
|---|---|---|
| `session_manager.py` | `harness/` | 多目标并行 SessionManager（start_target/list/stop/broadcast/close；独立 bus+state） |
| `executor.py` | `harness/runner/` | ExecutorLoop（单题 pre/step/post 循环）+ `run_single_challenge` setup |
| `state.py` | `harness/runner/` | RunnerState 显式状态（原闭包 nonlocal 变量字段化） |
| `verifier.py` | `harness/runner/` | Verifier 协议 + Mechanical/LLM 双实现 + parse_verdict |
| `subtasks.py` | `harness/runner/` | 后台子任务三闸门调度/收割/回收 |
| `context.py` | `harness/runner/` | field_notes / 黑板载入 / sub_intel 合并 / fork_analyze 复盘 |
| `orchestrator.py` | `harness/runner/` | run_task 入口编排段：legislate_charter / finalize_report |
| `pool.py` | `harness/runner/` | 进程级全局 ModelPool 句柄 |
| `pentest_target.py` | `harness/runner/` | `/api/run` 真实执行 runner（轻量授权目标循环） |
| `scope.py` | `pentest/` | ScopeConstraint 纯函数授权守卫（fail-closed） |
| `target_profile.py` | `pentest/` | 目标认知状态机（Identity/AttackSurface/AttackChain/coverage） |
| `hypothesis.py` | `pentest/` | 假设队列（attempts 上限 2 → 强制 falsified） |
| `deadends.py` | `pentest/` | 死路蒸馏（overturn_condition 决定复活） |
| `contract.py` | `pentest/` | 验收契约纯函数（coverage/dead_end_ratio/deliverables） |
| `approval.py` | `pentest/` | ApprovalGate（T1-T4 分级 + 人审 + 审计双写） |
| `blackboard/` | `pentest/` | L0 事实黑板（categories/facts/store：pentest.db 五表） |
| `presets/` | `pentest/` | PENTEST_PRESET 注册（persona/verifier/tool_levels/knowledge/subagents） |
| `tool_adapters/` | `pentest/` | L3 14 适配器（结构化输出解析 + 参数注入校验） |
| `knowledge/` | `pentest/` | L4 知识层（engine 三路 + 三源 + sources/ 本地源） |
| `learning/` | `pentest/` | L6 学习层（models/store/distiller/retriever/metrics） |
| `sandbox/` | 根目录 | L5 沙箱（backend 协议/bubblewrap/policy 词表/selfcheck） |
| `backend.py` | `sandbox/` | SandboxBackend 协议 + fail-closed 失败语义 |
| `policy.py` | `sandbox/` | 命令分级词表（read-only/workspace-write/danger） |
| `bubblewrap.py` | `sandbox/` | bwrap 真实隔离后端（--unshare-all + 根只读 + 工作区可写） |
| `selfcheck.py` | `sandbox/` | `python -m sandbox.selfcheck` 自检 |
| `events.py` | `core/` | 进程级 EventBus + BUS |
| `hooks.py` | `core/` | RunHooks 事件投影 + 渐进披露 + 增量打分 + 缓冲落盘 |
| `tool_pipeline.py` | `core/` | 统一工具管线（BruteGate/注入防护/Spill/AutoSubmitFlag 等 middleware） |
| `agents_def.py` | `core/` | Strategist/Executor/Reporter/Compactor 定义 + 动态 instructions |
| `task_context.py` | `core/` | TaskContext + L5GuardrailConfig + SubtaskBudget |
| `context_manager.py` | `core/` | 上下文压缩（append-only）+ 断点续跑 |
| `charter.py` | `core/` | 使命宪章落盘/加载 |
| `memory.py` | `core/` | 三层记忆统一访问（含黑板快照渲染） |
| `reporting.py` | `runtime/` | first_strike/cost_report/轨迹导出/stuck-replay/四指标看板 |
| `budget.py` | `runtime/` | 成本治理单一事实源（爆破/hint/switch/suspend 阈值） |
| `model_pool.py` | `runtime/` | 多模型灾备池 + ModelExhaustedError |
| `model_fallback.py` | `runtime/` | 外层 Agent Runner.run 灾备重试包装 |
| `stuck.py` | `runtime/` | 模型惰性治理（自救/切换模型） |
| `fork_analyst.py` | `runtime/` | 轨迹分叉分析（一次性强模型调用） |
| `status.py` | `runtime/` | 阶段状态机 + PHASE_DEFS |
| `log.py` | `runtime/` | 统一日志（终端+文件双写） |
| `main.py` | `server/` | Starlette 组装 + uvicorn 入口 |
| `api.py` | `server/` | 七方法 RPC（describe/targets/engagements/run/steer/respond/report） |
| `ws.py` | `server/` | mux/host 双 WS 纯下行帧流 |
| `state.py` | `server/` | AppState（SessionManager 桥接 + Hub 扇出 + 审批注册表） |
| `run_spec.py` | `server/` | /api/run 请求归一 + 预算护栏（max_rounds/wallclock/approval/token） |
| `fixture.py` | `server/` | demo engagement fixture（离线三态） |
| `static.py` | `server/` | apps/web/dist SPA 托管 |
| `engine.py` | `profiles/practical_pentest/report/` | R5 报告引擎纯函数投影 |
| `task.py` / `platform.py` | `profiles/ctf_legacy/` | CTF 默认任务书 / 提交铁律与终局收敛 |
| `scheduler.py` | `bench_platform/` | 零 LLM 纯函数调度（EV 选题/停滞决策） |
| `platform_client.py` | `bench_platform/` | 平台协议唯一实现 + `get_platform_client` 单例 |

---

## 4. 多智能体体系

> 本体系描述 **CLI/跑分执行面**（app.main + ExecutorLoop）的角色编排；v4 Web 面的轻量
> runner（pentest_target）不使用这些角色，只驱动单一执行 Agent + 人工审批（见第 12 章）。

| Agent | 角色定位 | 产物 | 触发时机 |
|---|---|---|---|
| **Strategist** 战略家 | 立法 + 深度分析（为什么打、按什么顺序） | 使命宪章 + 作战计划 plan | 任务开始一次性；`build_strategist` 工厂化按需构建，同任务幂等缓存（`orchestrator.legislate_charter`） |
| **Executor** 执行者 | 执行（怎么打） | 证据、黑板、flag | 每轮循环（单主线，`agents_def.build_executor`） |
| **Subtask Executor** | 子任务并发执行（N≤2，三闸门） | `finish_subtask` 结构化结论（summary/findings/flag） | 主 Agent `spawn_subtask` 后并发调度（`harness/runner/subtasks.py`） |
| **Reporter** 报告者 | 战报 + 死路蒸馏 | 战报（`## 战报` / `## 死路蒸馏`）+ field_notes | 任务结束一次（`orchestrator.finalize_report`） |
| **Compactor** 压缩器 | 历史压缩（append-only） | 压缩摘要（定点截断 + 锚点追加） | 上下文超阈值 / 卡壳自救强制 |
| **fork_analyst**（非 Agent） | 轨迹分叉复盘 | `next_directive` 写黑板 | 3 轮零增量触发一次（`runtime/fork_analyst.py`，一次性强模型调用） |

> 已退役：Manager/Planner 并入 Strategist；Coach 机制代码保留但默认关闭
> （`ENABLE_COACH=true` 复活），由 fork_analyst 替代。

Executor 的 instructions 是**动态函数**，每轮从 TaskContext 读取最新状态重渲染，注入：
角色风格 + 当前阶段 + charter + plan + 纪律 + playbooks + field_notes + 压缩摘要 + 黑板。
hook 运行时追加技能 / 切换阶段后，下一轮系统提示自动带上新状态。

---

## 5. 工作模式与单题执行循环

### 5.1 两种工作模式

| 模式 | 说明 |
|---|---|
| 通用模式 | 单 Executor 循环，Agent 自主编排（`python -m app.main "<任务>"`） |
| 跑分模式（调度器） | 代码机械编排选题→启动→渗透→提交→关闭→换题（TSecBench 类平台） |
| Web 受控模式 | server `/api/run`：预算护栏 + 审批门 + 只读白名单（见第 12 章） |

### 5.2 单题循环（现为 harness/runner ExecutorLoop）

R1 可测试性重构后，原 `app/main.py` 的 `_run_single_challenge` 等价变换为：

- `RunnerState`（`harness/runner/state.py`）：把原 pre/step/post 三个内嵌 async 闭包的
  nonlocal 捕获变量（switched/outcome/death_reason/intervention_count/hint_used/
  coach_used/turn_count/next_input）全部提升为数据类字段；
- `ExecutorLoop`（`harness/runner/executor.py`）：循环控制 + `_pre_step/_step/_post_step`
  三个可直驱方法（fake state/clock/scorer/model_pool 注入 → 13 条直驱单测）；
- `run_single_challenge()`：保留 setup 流程（工作区/角色派任/黑板回注/工具裁剪/
  first_strike/缓存观测/executor 与 session 构建），组装后交给 `ExecutorLoop.run()`。

`ExecutorLoop.run()` 主循环：

```
while True:
  pre  → _pre_step()：静态 prompt hash 断言（[cache-guard]）· 墙钟硬顶（有进展延长半档）·
         token/时钟到档 → 无感知 switch 模型 · StuckDetector 自救/切换
  step → _step()：Runner.run(max_turns=1)（模型失败自动 fallback 重试同输入）
  post → _post_step()：信息增量累计 → hint 机械前置 / fork_analyze 破局（3 轮零增量 ≤1 次）/
         replan / 机械换题（空转 SINGLE_EMPTY_TURNS=4 轮 / 网络不可达 2 次 / hint 后零增益
         HINT_GRACE_TURNS=5）· 子任务调度收割 · 压缩 · 熔断（fatal/solved/stuck）
finally：统一回收（子任务取消 / 事件冲刷 / session 清理）
收尾：field_notes 机械沉淀 + cost_report + trajectory + stuck-replay + dashboard
```

### 5.3 跑分模式主流程（app.main 调度循环）

```
① 战略家立法+规划 → charter + plan（幂等缓存，可复用）
② 调度器主循环（零 LLM）：
     deadline 检查 → list_challenges → 补满并发槽（EV 选题排除活跃题，start 容器）
     → 等待任一完成（FIRST_COMPLETED）→ close 容器 + 记录结果 + 补位
     → 任一题 fatal（TaskEnded/TaskNotFound）→ 取消其余 → 终止
③ _endgame_sweep 终局重扫：主循环退出后再拉列表，补漏题/重试 stuck
④ Reporter 战报 + 死路蒸馏（finalize_report）
```

---

## 6. 调度器（跑分编排，零 LLM）

`bench_platform/scheduler.py` 纯函数层：选题/停滞决策全代码，不靠 LLM 自觉。

### 6.1 EV 选题

```
EV = total_score × 难度系数 × 衰减^attempts（从未做过 ×3 零启动倾斜）
难度系数：easy=1.3  medium=1.0  hard=0.7
衰减：常规 DECAY=0.3；终局回捞 ENDGAME_DECAY=0.6（收尾阶段对放弃过的题回捞）
终局判定：is_endgame() = 所有未完成题都至少被放弃过一次
```

### 6.2 单题停滞决策（decide_stuck_action，阈值按难度分级）

优先级：`zero_gain >= skip → 'skip' 换题；zero_gain >= hint 且未看过 hint → 'hint'；
否则 'continue'`。

| 难度 | hint 阈值 | skip 阈值 |
|---|---|---|
| easy | 4 | 8 |
| medium | 6 | 12 |
| hard | 8 | 18 |
| 未知 | 6 | 12 |

- 云存储/SaaS 类指纹题（azure/s3/blob/lambda/firebase 等）hint/skip 阈值整体提前 2/4 轮；
- `SINGLE_EMPTY_TURNS = 4`：连续 4 轮无工具调用 → 机械换题（空转与难度无关）；
- hint 后仍零增益 `HINT_GRACE_TURNS = 5` 轮 → `hint_stale` 机械换题（runtime/budget.py 常量）。

### 6.3 成本治理（runtime/budget.py，单一事实源）

| 层 | 触发 | 动作 |
|---|---|---|
| 爆破预算 | 单题爆破调用 ≥ `BRUTEFORCE_MAX_CALLS`（默认 20） | `brute_gate` 拦截，强制定向验证 |
| hint 预算 | 卡题且 token 达挂起档 `HINT_BUDGET_RATIO`（默认 0.35） | 机械拉 hint |
| 换脑 switch | 单题 token 达 switch_tokens（按难度分档） | 无感知切换候选模型 |
| 挂起 suspend | token 达 suspend_tokens 或墙钟达 `SUSPEND_SECONDS`（默认 2700） | 停止本次尝试释放槽位 |
| 干预上限 | 单题累计干预 ≤ MAX_STUCK_INTERVENTIONS（easy 3 / medium 5 / hard 8） | 超限机械换题 |

---

## 7. pentest 数据模型（L0/L1）

> 规格来源：终极执行计划 v4 第三部分。全部为数据模型/纯函数，无 IO 控制流。

### 7.1 scope.py —— 授权范围守卫（L5 起点）

- `ScopeConstraint`：allowed_targets（IP/CIDR/域名/`*.` 通配）/ excluded_targets /
  forbidden_actions（dos/social_engineering/data_theft）/ time_window / max_intensity；
- `check(target, action, now_iso)`：**排除优先于授权** → 不在授权范围 → 禁区动作 → 时间窗；
- `from_task_brief()`：解析失败抛 `ScopeParseError`（不静默放行）。

### 7.2 target_profile.py —— 目标认知状态机（L1）

Identity（ip/hostname/domains/os/tech_stack）→ PortFinding / WebFinding / CredentialLead
→ AttackSurface → Hypothesis 队列 → validated AttackChain → DeadEnd 列表 → coverage 统计
（category → {done,total}）。

关键方法：
- `surface_diff(other)`：与上一快照的差异是**唯一应进入 LLM 上下文**的部分；
- `coverage_ratio()`：供 AcceptanceContract 计算覆盖率。

### 7.3 hypothesis.py —— 假设队列（L1）

- `Hypothesis`：statement / evidence_basis（引用 fact_key）/ expected_outcome /
  success_criteria / failure_criteria / priority / status
  （pending/testing/validated/falsified/inconclusive）/ attempts；
- 纪律：inconclusive 允许换方法重试，attempts 上限 2，超过强制 falsified
  （由调用方生成 DeadEnd）；
- 匹配辅助：matches_criteria（contains 等子句解析）。

### 7.4 deadends.py —— 死路蒸馏（L1）

- `DeadEnd`：path_description / hypothesis_id / falsified_at_step / falsified_at /
  evidence_snapshot / falsification_method / **overturn_condition**；
- 注入纪律：ExecutorLoop 每轮只注入 path_description + overturn_condition 摘要
  （不注入 evidence_snapshot 全文）；
- `is_overturned_by(new_facts)`：新事实满足 overturn_condition 才允许复活该方向。

### 7.5 blackboard/ —— 事实黑板（L0 落地）

- `categories.py`：PTES fact_key 命名空间（词汇表非控制流）——`recon/ delivery/
  exploitation/ c2/ objectives/ finding/ chain/ exploit/ poc/`，key 均以 `/` 结尾；
- `facts.py`：`ProjectFact`（fact_key/category/body/links/confidence/created_by/
  engagement_id），**写入即校验**：fact_key 必在 category 命名空间、必须有 ≥1 条关联边、
  body 非空 —— 「未记录事实不得宣称漏洞」；
- `store.py`：`BlackboardStore`（默认 `pentest.db`）五表：`target_profiles / hypotheses /
  dead_ends / blackboard / events`（黑板上事件表供 ApprovalGate 等审计落库）；
  **`diff_since()` 是黑板上下文注入的唯一来源**；`record_event()/query_events()` 供审计。

### 7.6 contract.py —— 验收契约（Verifier 输入）

`AcceptanceContract`：required_deliverables（kind: attack_surface_map / validated_chain /
dead_end_coverage / report，minimum 默认 1）+ minimum_coverage（默认 0.8）+
max_dead_end_ratio（默认 0.5）。`evaluate(profile)` 纯函数返回
ContractCheckResult（passed/reasons/coverage/dead_end_ratio/fulfilled/required）。
死路占比过高 = 覆盖率造假信号。

---

## 8. L5 护栏层（Scope / 审批 / 沙箱）

### 8.1 执行链

```
ScopeCheck（scope.check 逐 token 越范围硬拦截）
  → sandbox.confine（policy 命令分级 + bwrap 包装，fail-closed）
  → (T3) ApprovalGate（高危调用暂停等人审）
  → 执行 → ToolAdapter.parse_output 结构化提取 → 黑板/状态更新
```

集成点：
- `core/task_context.py::L5GuardrailConfig`（task_id/scope/target/policy/backend/approval，
  鸭子类型避免 core→pentest/sandbox 静态依赖；None = 旧模式透传）；
- `core/tool_pipeline.py` 统一工具管线挂护栏中间件；
- `pentest/presets/pentest_preset.py` 注册护栏语义（Tier 词表 + verifier + knowledge 分级）。

### 8.2 ApprovalGate（pentest/approval.py）

- 词表 `DEFAULT_TOOL_TIERS`：shell/run_batch/parallel_shell/exploit_fuzz = T3；
  fuzz/run_tool/spawn_subtask = T2；未登记默认 T1；T4 = forbidden 由
  ScopeConstraint.forbidden_actions 拦截（与工具名解耦）；
- `request()` 唯一入口：T1 直接放行不产生记录；T2/T3 有 deny 缓存 → 结构化拒绝；
  有 grant 缓存 → granted_cached 放行；否则落 `ApprovalRecord(pending)` + 事件
  `approval/requested` + 返回「暂停执行」；
- `grant()` 只能裁决 pending；事件 `approval/resolved`；
- 审计双写：EventBus + （可选）SQLite events 表；线程安全（锁内变更、锁外 emit/persist）。

### 8.3 sandbox/（fail-closed 门面）

裁定表（`sandbox/__init__.py::confine`）：

| tier \ backend | 可用 | 不可用 |
|---|---|---|
| danger | `SandboxBlockedError`（拦截清单，永不执行） | `SandboxUnavailableError`（禁止裸跑） |
| 其余 | ConfineResult（写档按 policy 决定工作区可写） | `SandboxUnavailableError`（禁止裸跑回退） |

- `policy.py` 三档：read-only / workspace-write / danger（写即危险）。danger 正则 +
  子串词表覆盖 `rm -rf /`（任意 r/f 顺序）、`--no-preserve-root`、mkfs、dd 写裸盘、
  根递归 chmod/chown、shutdown/reboot/halt/poweroff、fork 炸弹、`>/dev/sd*`；
- `bubblewrap.py`：`--unshare-all --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp`
  （+ writable 时 `--bind <workdir>`）；启动失败（stderr 以 "bwrap: " 开头）抛
  `SandboxUnavailableError`，禁止降级裸跑；`--unshare-all` 含新 net namespace → 沙箱内无外网；
- `selfcheck.py`：`python -m sandbox.selfcheck`（bwrap 二进制 / 真实 userns 探针 /
  危险词表 ≥6 条 / 拔后端后 danger 命令抛 SandboxUnavailableError）。

---

## 9. L4 知识层（三路知识获取）

### 9.1 统一接口（base.py）

`KnowledgeSource` 协议（async query(question, context) → KnowledgeResult）+
`KnowledgeResult`（answer/source_type/source_refs/confidence）。设计纪律：失败/降级一律
低置信度 + 空 source_refs 表达，不抛错打断检索。

### 9.2 路由引擎（engine.py）

```
1. L4a local_rag（离线零成本）——总是先查；
2. L4b web_search（T1 自动）——本地最高置信度 < high 且 allow_web=True 才触发；
3. L4c web_fetch（T2 审批）——永不自动，只抓调用方显式传入的 fetch_urls。
合并：跨源去重 → 置信度降序（high>medium>low）→ 同置信度本地优先；
单路独立 asyncio.wait_for 超时，超时/异常吞掉不影响其他路。
```

### 9.3 三源实现

| 源 | 说明 |
|---|---|
| `local_rag.py` | SQLite FTS5（knowledge_items + knowledge_fts，key/content 关键词索引；无 FTS5 退 LIKE）+ 可选 embedding 列（无向量自动退纯关键词）；key 精确命中（COLLATE NOCASE，支持 CVE 编号）置顶 |
| `web_search.py` | DeepSeek Anthropic 兼容端点（base `https://api.deepseek.com/anthropic`）`web_search_20250305` 服务端工具；复用 `DEEPSEEK_API_KEY`，未配置 available=False 优雅降级；解析 content 块 web_search_tool_result 来源列表 |
| `web_fetch.py` | HTML→Markdown（标准库 parser）；`ssrf_check` 纯函数：URL 主机解析 IP，全局公网放行，保留段（环回/RFC1918/CGNAT/链路本地/组播/IPv6 ULA 等）要求解析 IP 显式在 ScopeConstraint.allowed_targets；域名多 IP 任一被拒 → 整体拒绝（防 DNS rebinding）；解析失败 → 拒绝 |

### 9.4 本地知识源（sources/）

`cve_db`（CVE/Nuclei 索引）、`tool_manuals`（工具手册）、`methodology`（方法论卡片）、
`negative_findings`（负面知识：R4.5 阶段自带内存后端 register/search；
`connect_learning_store()` 在 R6 后经 lazy import 同步 L6 NegativeKnowledge 表，
探测失败返回 False 不抛错——负面知识检索不成为执行循环硬依赖）。

> 接入现状说明：知识层以可单测的库形态落地（`tests/unit/knowledge/` 覆盖各源 + SSRF），
> 供执行循环/编排层注入；无 key 环境全链路离线可用（local_rag 本地索引 + 降级结果）。

---

## 10. L6 学习层（蒸馏与成长）

### 10.1 数据模型（models.py）

- `Lesson`（情景→语义记忆）：title + context_tags（tool:sqlmap / phase:exploitation /
  tech:wordpress）+ content；applied_count 由 LearningRetriever 命中后回写；
- `PlaybookStep / Playbook`（程序记忆）：仅当完整 AttackChain 被 validated 才蒸馏；
  结构 = preconditions（目标指纹特征）+ steps（tool + params_template + expected_check）
  + expected_checks；success_count/fail_count 供复用降权或标记失效；
- `NegativeKnowledge`（负面知识）：deadEnds 跨目标泛化；overturned 由新证据推翻后置 True
  ——只标记不物理删除（保留复现历史审计）；
- `GrowthMetrics`：单 engagement 成长度量（输出前端 LearningPanel）。

### 10.2 SQLite store（四表）

`LearningStore`（默认 `learning.db`）：lessons / playbooks / negatives / growth_metrics。
upsert 同主键 INSERT OR REPLACE（蒸馏幂等可重放）；计数走 bump/record 增量；
search_lessons：tag 过滤（精确 / "tool:" 前缀 / 段值）∪ 关键词词元覆盖打分；
`iter_negatives(db_path)` 供知识层 `negative_findings.connect_learning_store` 对接
（库文件缺失抛 FileNotFoundError，连接失败返回 False）。

### 10.3 蒸馏引擎（distiller.py）

`EngagementTrace`（事件日志 + blackboard + profile/deadEnds）→
`{"lessons": [...], "playbooks": [...], "negatives": [...], "metrics": {...}}`；
`validate_distillate()` schema 校验；`Distiller.distill()` 永远产出 schema 合法结果。

- LLM 蒸馏为可注入的一次性低频调用（async callable，本模块不 import LLM SDK）；
- 无 LLM / LLM 抛错 / 输出 schema 无效 → **规则回退（离线保证可用）**：
  Lessons = 工具异常事件按工具聚合；Playbook = 仅 validated AttackChain 存在时生成
  （前提 = 目标指纹，与 retriever.fingerprint_tokens 同口径）；Negatives = deadEnds
  泛化；Metrics = compute_growth_metrics 机械化计算。

### 10.4 检索器（retriever.py）与注入点

`LearningRetriever.retrieve(profile, hypothesis)` → LearningContext，三路全本地
（关键词/指纹匹配，无 LLM）：

1. 负面命中 → 拦截/跳过并给原因（优先级最高，已推翻项自动排除）；
2. Playbook 前提命中 → 步骤模板注入；
3. Lesson 命中 → 注意事项注入（回写 applied_count）。

`fingerprint_tokens(profile)` 为公开指纹词表（tech:/os:/service:/version:），
distiller 与 retriever 共用，保证「写入-检索」两端口径一致。

> 接入现状：本层按设计面向 ExecutorLoop pre-step 注入与 engagement 结束蒸馏提供
> 完整可用的组件库 + 43 条单测；调用方按需组装（不破坏默认离线全绿测试）。

### 10.5 成长度量（metrics.py）

`compute_growth_metrics`：hypothesis_hit_rate / deadend_overturn_rate /
inconclusive_rate / token_per_validated_chain / playbook_reuse / lesson_injection；
overturned 数用 blackboard 新事实对 deadEnd.overturn_condition 的满足情况机械化判定。

---

## 11. Verifier 双核

`harness/runner/verifier.py`（裁决 #1：不做硬架构，可插拔）。

- 协议：`async verify(profile: TargetProfile, contract: AcceptanceContract, events) -> Verdict`；
- `Verdict` 五值：`verified_done`（契约全过，可出报告）/ `continue`（有实质进展未达验收）/
  `redirect`（当前方向无进展/攻击链为零且覆盖不足）/ `corrective`（死路占比过高=造假信号，
  先纠正再验证）/ `need_human`（机械无法裁决需人工）；
- `MechanicalVerifier`：纯函数零 token（默认），只用 `AcceptanceContract.evaluate()`
  结果按确定性规则映射；无 IO、无 LLM；
- `LLMVerifier`：低频唤醒（唤醒策略由调用方按触发条件控制）——事件日志尾部 + 黑板摘要 +
  机械初判 → llm 回调 → parse_verdict 归一化；llm 为空/解析失败 → 回退机械初判（fail-safe）；
- 触发注册：`pentest/presets/pentest_preset.py`（`DEFAULT_VERIFIER="llm"` +
  `FALLBACK_VERIFIER="mechanical"`；触发条件 phase_complete / contract_80pct /
  stall_5_steps）。

---

## 12. server + 前端（Web 控制面）

### 12.1 server 包（Starlette + uvicorn）

| 模块 | 职责 |
|---|---|
| `server/__init__.py` | 包加载时自载项目根 `.env`（load_dotenv）；握手身份 SERVER_NAME=SECAI-PT / SERVER_VERSION=4.0.0 |
| `server/main.py` | app 组装 + 路由表 + demo fixture 安装 + lifespan（ticker 启停/统一收尾）；`python -m server.main --port 8700` |
| `server/api.py` | 七方法 RPC。业务错误恒 HTTP 200 + `{ok:false,error:{code,message,details}}`；describe=严格握手；run=无 LLM Key → `llm_key_missing`，有 Key → run_spec 归一 → 逐目标接 pentest_target runner；steer=会话指令注入（managed 会话）；respond=审批裁决（rpcId 回响）；report=桥接 R5 报告引擎 |
| `server/ws.py` | `/api/events.mux`（MuxFrame：session/event / subscribed / projection / queue / approval/requested / approval/resolved / jobs）+ `/api/events.host`（HostFrame：session-added / removed / status / engagement-changed）。连接后先回放快照（首帧基线），持续扇出 live 帧；客户端上行 → 1008 |
| `server/state.py` | AppState：SessionManager(shared_bus=BUS) 桥接 + BUS 订阅→SessionEvent 落账→Hub 扇出 + 审批注册表 + engagements/sessions 编排记录 + demo ticker（`SECAI_DEMO_TICK_SECONDS` 可调） |
| `server/run_spec.py` | /api/run 请求归一：新式 `{task_brief, targets, scope, settings}` + 兼容旧式 allowedTargets；预算护栏 max_rounds≤10(默认3)/墙钟≤300s(默认60)/审批等待≤45s 且必短于墙钟/token 硬顶 100k；解析失败抛 RunSpecError |
| `server/fixture.py` | 内置 demo engagement：三会话（sess-a-demo running 周期性活动 / sess-b-demo awaiting_approval 挂 approval/requested / sess-c-demo completed 全量投影），与前端 demo.ts 同一叙事；engagementId=live-engagement 与前端 LIVE_ENGAGEMENT_ID 对齐 |
| `server/static.py` | GET / → `apps/web/dist`（已知文件 FileResponse + 其余 SPA fallback + 防路径穿越）；dist 缺失 404 提示先 `npm run build` |

### 12.2 真实执行 runner（harness/runner/pentest_target.py）

`/api/run` 经 SessionManager 每目标独立驱动：

```
runner(ctx) 循环（≤max_rounds 轮 / ≤wallclock 墙钟）：
  drain steer_queue（人工 /api/steer 指令优先）
  → LLM 单轮（agents SDK Runner.run，FAST_MODEL，SQLiteSession 保历史）
  → 工具选择：blackboard(set/list) · run_recon_tool（ScopeCheck 逐目标硬拦截
      → bridge.request_approval（approval/requested 帧）→ wait_for_approval 唤醒
      → 只读白名单执行：nmap/httpx/whatweb/dnsx/subfinder/certsh/searchsploit/arp-scan）
      · complete_task（收敛结束）
  → 事件经 BUS（session_id 键隔离）→ state 落 SessionRec.events + 扇出 mux WS
审批等待超时 → 自动拒绝（无人应答也收敛）
```

### 12.3 前端（apps/web）

- 技术栈：React 19 + TypeScript + Vite + CSS Modules + `--secai-*` Design Token
  （禁 Tailwind/SCSS）；脚本 dev/build(tsc -b && vite build)/lint(oxlint)/preview；
  开发态 vite.config.ts 代理 `/api`（含 WS）→ `127.0.0.1:8700`；
- 通信契约（`src/connection/api.ts`）：RPC 上行 `{rpcId, method, payload}` +
  双 WS 纯下行帧协议；ConnectionController（`connection.ts`）：双流握手
  （describe + 双 WS onOpen，3s 超时兜底）、指数退避重连（500ms×2 封顶 10s ±50% 抖动）、
  HTTP RPC 客户端（transport/business 双层错误）；
- 运行层（`src/runtime/`）：demo.ts（内嵌 mock 帧驱动，90ms 步进三会话交错投递）/
  appRuntime.ts（`DEMO_MODE` 常量切换离线 DEMO 与真实通道；true=createDemo，
  false=ConnectionController）/ engagement/session/projections 状态对象；
- UI：三栏布局（conversation 对话 + sidebar 目标列表 + details 详情）：
  ApprovalCard（审批）/ HypothesisQueue（假设队列）/ DeadEndList（死路清单）/
  EvidenceChain（证据链）/ PortScanResult / ToolOutputPanel / ReportPreview /
  LearningPanel（成长面板），主题三态、拖拽分栏。

---

## 13. CTF 兼容（profiles/ctf_legacy 收敛）

R4 H12：CTF 专属假设从主循环/工具层全部收敛到 `profiles/ctf_legacy/`：

| 收敛前 | 收敛后 |
|---|---|
| app.main 读平台模板与凭证常量 | `profiles/ctf_legacy/task.py::build_default_task()`（读 prompts/tsec_task.txt 替换占位符） |
| demo_tools/tools/domains 内嵌提交铁律/通关复核 | `profiles/ctf_legacy/platform.py`（`_submit_flags_if_any` / `_is_completed` / `_late_bind_submit` / `finalize`） |
| 多处构造 PlatformClient | `bench_platform/platform_client.py::get_platform_client()` 模块级单例（唯一构造点），`platform_configured()` 判定凭证 |

主循环 grep 已无 `BENCHMARK_TOKEN` 直接引用；CTF 跑分仍走 `python -m app.main`（调度编排
留在 app/main.py，单题闭环在 harness/runner）。

配套资产库 `arsenal/`（声明式本地资产）：roles/（17 角色）、skills/（97 技能 md）、
tools/（92 CLI YAML，经 `arsenal/registries/sec_tools.py` 装载执行）、vulns/（9 漏洞模块）、
pocs/（29 POC）、knowledge/（5 条目）、payloads/（字典+脚本）+ registries/（加载器）。

---

## 14. 关键设计决策与教训

### 14.1 已落地关键决策（v4 批次摘要）

1. **单题循环类化（R1，H1）**：闭包状态机 → `ExecutorLoop + RunnerState`，可直驱单测；
   工具巨石（H2）按域拆到 `tools/domains/`（11 域 + registry），demo_tools.py 收敛为
   re-export shim。
2. **动态上下文增量注入（R2，H3）**：charter/plan 版本化、field_notes 仅首轮、
   黑板 diff 注入（第 10 轮 ≤40%）。
3. **真实统计替代硬编码（R2，H4）**：zero_gain_events 看板聚合 cost_report 真实值。
4. **session 物理清理（R2，H5）**：sub_*.sqlite 句柄登记 + 兜底 close + glob 删除。
5. **Executor 工作纪律 6 条收口（R2，H6）**：语义去重，约束不删。
6. **破局链（R2，H7）**：惰性点 ±5 步回放自动导出 + 前提证伪级联回收。
7. **执行安全（R3，H9/H10）**：sandbox bwrap fail-closed（rm -rf / 真实拦截验证）；
   ApprovalGate T1 自动/T2-T3 审批/T4 禁止，审计入事件总线 + events 表。
8. **Verifier 双核（R3）**：Mechanical 纯函数默认 + LLM 低频，fail-safe 回退。
9. **预设注册 + 多目标（R4，H12）**：pentest/presets + harness/session_manager；
   CTF 提交/终局收敛 profiles/ctf_legacy；平台客户端单例收口。
10. **知识层（R4.5）**：本地 RAG + 联网搜索 + 网页抓取三路（web_fetch 过审批门 + SSRF 防护）。
11. **工具层补全（R4.5）**：11 个命令型适配器 + 参数注入校验 + 字段级解析。
12. **质量门 + 报告引擎（R5）**：gate.sh（ruff + unit + replay，无网）+ 纯函数报告投影
    （含已排除攻击面负面章节 / 跨目标链 / 快照回放）。
13. **学习层（R6）**：四蒸馏物管线（LLM 可注入 / 规则回退离线可用）+ 四表 SQLite +
    pre-step 三路检索 + GrowthMetrics 成长度量。
14. **server 面（R3-R4）**：RPC 七方法 + mux/host 双 WS 纯下行 + dist 静态托管 +
    demo fixture 离线三态；/api/run 接真实执行器（ScopeCheck→T2 审批→只读工具→黑板→
    BUS 扇出），run_spec 预算护栏（默认 ≤3 轮/60s/审批超时自动拒）；服务端自载 .env。
15. **前端（F0-F3）**：双 WS 通信层与帧契约 → 三栏拖拽 + 主题三态 + 渗透组件全集 +
    DEMO 驱动（无 LLM key 可展示）。
16. **E2E 隔离**：真实 LLM 联调测试移入 tests/e2e + e2e marker，默认 pytest/gate 不触发
    真实 API，显式 `-m e2e` 才跑。
17. **依赖清单基线**：pyproject（project secai 0.1.0 / pytest / ruff py311）+ requirements.txt；
    gate.sh 覆盖受控架构路径，历史存量违规不进本门（白名单哲学，只减不增）。

### 14.2 实战教训（保留自 v1.2，仍有效）

1. openai-agents 默认走 Responses API，DeepSeek 不支持 → 改用 `OpenAIChatCompletionsModel`。
2. DeepSeek 不支持 strict json_schema → 结构化输出用 tool + 手动校验。
3. `parallel_tool_calls=False` 规避 DeepSeek 并行工具调用 JSON 不稳定（exploit 阶段可
   `EXECUTOR_PARALLEL=true` 显式开启）。
4. 工具输出外置（ArtifactSpill）+ token 压缩（append-only 保前缀缓存）解决上下文膨胀。
5. VPN 需 CAP_NET_ADMIN，`--daemon` 后台 fork 会误报成功 → 必须验证 tun0。
6. default-soft：工具失败是信息不是死路；deadEnd/判停必须有证据门槛（黑板事实不变量）。
7. 观测单一真相源：SQLite 唯一真相，jsonl 仅人读留痕；关键事件（flag/阶段/网络异常/
   agent_end/prompt 漂移）立即刷盘绕过缓冲。
8. fail-closed 三件套：沙箱不可用禁止裸跑、Scope 解析失败抛错、web_fetch 解析不了即拒
   （DNS rebinding 防不住就整体拒绝）。

---

> **作者：一片丹心（别名：奋进的小杨）**
>
> *本文档随代码演进持续更新，是跨会话连续性的技术基座；规格细节冲突以代码 docstring 为准。*
