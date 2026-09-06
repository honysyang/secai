# SECAI — AI 驱动的授权渗透测试系统（SECAI-PT v4）

> 面向**授权渗透交付**的 AI 安全评估系统。
> 六层架构（L0–L6）+ 双核 Verifier + 多目标 SessionManager + Web 控制面（server + React 前端）。
> 本 README 与代码现状同步更新（SECAI-PT v4 全量改造后，2026-09）。

- **文档定位**：本 README 是**概览 + 运维入口**；设计规格与分层深度说明见
  [docs/SECAI架构设计文档.md](docs/SECAI架构设计文档.md)。
- **代码现状基线**：H1–H13 债务全结清（见 [docs/DEBT_LEDGER.md](docs/DEBT_LEDGER.md)），
  全仓 437 个测试全绿（436 常规 + 1 个真实 LLM 联调 `e2e`）。

---

## 目录

1. [系统架构](#1-系统架构)
2. [功能模块](#2-功能模块)
3. [部署与运行](#3-部署与运行)
4. [日志与监控](#4-日志与监控)
5. [安全机制](#5-安全机制)
6. [性能与缓存](#6-性能与缓存)
7. [目录结构](#7-目录结构)
8. [文档索引](#8-文档索引)

---

## 1. 系统架构

### 1.1 定位与执行面

SECAI 自 9_6 起只保留一条执行面：授权渗透 Web 面（v4 主线）。原 CTF 跑分/通用 CLI 面
（`app/main.py` + `bench_platform/` + `profiles/ctf_legacy/`）已整体删除，不再维护。
全套 L0–L6 模块库由 Web 面独占：

| 执行面 | 入口 | 驱动 | 目标 |
|---|---|---|---|
| **授权渗透 Web 面（v4 主线，唯一执行面）** | `python -m server.main --port 8700` + 前端 `apps/web` | `/api/run` → `harness.SessionManager` → `harness/runner/pentest_target.py` 轻量目标循环 | 对授权范围内的目标做受控侦察/渗透，ScopeCheck → T2 审批门 → 工具白名单，事件实时上 mux WS |

Web 面独占：`core/`（事件总线/工具管线/hooks）、`runtime/`（模型池/预算/日志）、
`pentest/`（L0–L1 数据模型 + L4/L5/L6 模块）、`harness/`（ExecutorLoop 等单目标执行骨架）、
`sandbox/`、`adapters/`。

### 1.2 六层架构（L0–L6）

自上而下，各层在仓库中的落点：

```
┌────────────────────────────────────────────────────────────────────┐
│ L6 学习层    pentest/learning/  —— 蒸馏引擎 + 四表 SQLite + pre-step │
│              检索器 + 成长度量（lessons/playbooks/negatives/metrics）│
├────────────────────────────────────────────────────────────────────┤
│ L5 护栏层    pentest/scope.py(纯函数) + pentest/approval.py(T1-T4)  │
│              + sandbox/(bwrap fail-closed) + presets 分级词表       │
├────────────────────────────────────────────────────────────────────┤
│ L4 知识层    pentest/knowledge/ —— local_rag(离线) + web_search(T1) │
│              + web_fetch(T2, SSRF 防护) + sources/ 本地源          │
├────────────────────────────────────────────────────────────────────┤
│ L3 工具层    pentest/tool_adapters/ —— 14 个适配器（base 协议 +     │
│              parse_output 字段级解析 + build_command 注入校验）     │
├────────────────────────────────────────────────────────────────────┤
│ L2 编排层    harness/ —— SessionManager(多目标并行) + ExecutorLoop  │
│              + verifier(双核) + subtasks 三闸门 + pentest_target    │
├────────────────────────────────────────────────────────────────────┤
│ L1 认知层    pentest/target_profile.py + hypothesis.py +            │
│              deadends.py + blackboard/ → SQLite durable             │
├────────────────────────────────────────────────────────────────────┤
│ L0 方法论层  pentest/blackboard/categories.py PTES fact_key 命名空间 │
│              + presets/pentest_preset.py（词汇表，非控制流）         │
└────────────────────────────────────────────────────────────────────┘
```

### 1.3 双核 Verifier（不做硬架构）

`harness/runner/verifier.py` 定义 `Verifier` 协议
`verify(profile, contract, events) -> Verdict`，两个实现可切换（裁决 #1「双核」指**可插拔**，不做强制双跑）：

- **MechanicalVerifier（默认，零 token）**：纯函数，用 `pentest/contract.py` 的
  `AcceptanceContract.evaluate()` 结果按确定性规则映射五类 Verdict；
- **LLMVerifier（低频唤醒）**：把事件日志尾部 + blackboard 摘要 + 机械初判拼提示词，
  交给注入的 llm 回调；`llm` 为空 / 解析失败回退机械初判（fail-safe）。

Verdict 五值：`verified_done / continue / redirect / corrective / need_human`。
默认实现与触发条件注册在 `pentest/presets/pentest_preset.py`（`DEFAULT_VERIFIER="llm"`，
`FALLBACK_VERIFIER="mechanical"`，触发器 `phase_complete / contract_80pct / stall_5_steps`）。

### 1.4 多目标 SessionManager

`harness/session_manager.py`（L2 编排层，裁决 #4）：**每个目标 = 一个独立
Session/ExecutorLoop**，`SessionManager` 统一管理：

- `start_target()`：为单个目标注册 session（独立 EventBus + 独立 state），随即 spawn
  `asyncio.Task` 并行驱动 runner，支持 ≥2 目标同时跑、上下文互不污染；
- `list_sessions() / status() / stop() / broadcast() / close()`：编排面查询 / 幂等停止 /
  向全部 session 广播（MuxFrame host 通道数据源）/ 收尾；
- runner 签名 `Callable[[SessionContext], Awaitable[Any]]`，单测注入 fake runner。

Web 控制面的 `server/state.py` 用 `SessionManager(shared_bus=BUS)` 桥接：每个目标会话的
事件落 `SessionRec.events` 并即时扇出到 `mux` WS。

### 1.5 前后端分离拓扑（Web 控制面）

```
apps/web（React 19 + TS + Vite，三栏 UI）
   │  HTTP RPC: POST /api/{describe|targets|engagements|run|steer|respond|report}
   │          请求体 { rpcId, method, payload }；业务错误恒 HTTP 200 {ok:false,error}
   │  WS 纯下行（客户端上行 → 1008）:
   │    /api/events.mux   MuxFrame：session/event|subscribed|projection|queue|approval/…
   │    /api/events.host  HostFrame：session-added|removed|status|engagement-changed
   ▼
server/（Starlette + uvicorn）
   main.py    组装 + 路由 + demo fixture 安装（lifespan 启停 ticker）
   api.py     七方法 JSON REST（run 接真实执行 runner；respond 写审批裁决并唤醒）
   ws.py      mux/host 双 WS 下行骨架（Hub 注册/回放/扇出）
   static.py  GET / → apps/web/dist（SPA fallback）
   state.py   AppState：SessionManager 桥接 + BUS 订阅 + WS 扇出 Hub + 审批注册表
   fixture.py 内置 demo engagement（无 LLM key 离线三态展示）
   run_spec.py /api/run 请求体归一（新式 task_brief/targets/scope + 兼容旧式 allowedTargets）
```

开发态 `apps/web` vite dev server（5173）把 `/api` 与两条 WS 代理到 `127.0.0.1:8700`
（见 `apps/web/vite.config.ts`）。

---

## 2. 功能模块

### 2.1 单题执行循环（ExecutorLoop，L2）

`harness/runner/executor.py`（R1 可测试性重构核心）：

- `RunnerState`（`harness/runner/state.py`）：把原单题执行闭包
  里的 nonlocal cell 变量提升为数据类字段（phase/steps/budget/seq/outcome/…）；
- `ExecutorLoop.run()`：`pre → step → post` 三段主循环，`_pre_step/_step/_post_step`
  三个方法可用 fake state/clock/scorer/model_pool 直驱单测
  （`tests/unit/runner/test_executor_loop.py` 13 条）；
- `run_single_challenge()`：保留对外签名与 setup 流程（工作区/派任/黑板回注/工具裁剪/
  first_strike/缓存观测/executor 与 session 构建），组装依赖后交给 `ExecutorLoop.run()`。

循环内的机械治理（legacy CLI 面）：静态 prompt 字节级 hash 断言（`[cache-guard]`）、
墙上时钟分档硬顶（有进展可延长半档）、换脑 switch / 挂起 suspend（按难度分档）、
`StuckDetector`（自救 → 切换模型）、hint 预算、破局 `fork_analyze`（3 轮零增量，每题 ≤1 次）、
子任务三闸门调度与收割、收尾 cost_report / trajectory / stuck-replay / dashboard。

编排支撑（原 `run_task` 内嵌段按职责抽离）：

- `harness/runner/orchestrator.py`：`legislate_charter`（战略家立法 + 规划，同任务幂等缓存）
  与 `finalize_report`（战报后台生成 + field_notes + 四指标看板）；
- `harness/runner/context.py`：field_notes 读写 / 黑板载入 / 子任务情报合并 / 破局复盘与教练；
- `harness/runner/subtasks.py`：后台子任务三闸门（明确目标 / 独立 SubtaskBudget / 统一回收）；
- `harness/runner/pool.py`：进程级全局 ModelPool 句柄（set/get，避免循环 import）。

### 2.2 认知层（L1）与事实黑板（L0）

- `pentest/target_profile.py`：目标认知状态机（Identity/PortFinding/WebFinding/
  CredentialLead/AttackSurface/AttackChain + `coverage_ratio()`），`surface_diff()`
  只把与上一快照的差异注入 LLM 上下文；
- `pentest/hypothesis.py`：假设队列（priority 排序；inconclusive 换方法重试但 attempts
  上限 2，超过强制 falsified → 由调用方生成 DeadEnd）；
- `pentest/deadends.py`：死路蒸馏，`overturn_condition` + `is_overturned_by()` 决定可否复活；
- `pentest/blackboard/`：`categories.py`（PTES fact_key 命名空间：recon/delivery/
  exploitation/c2/objectives/finding/chain/exploit/poc）+ `facts.py`（ProjectFact
  **写入即校验**：fact_key 必在 category 命名空间、必须 ≥1 条关联边）+ `store.py`
  （`pentest.db` 五表：target_profiles/hypotheses/dead_ends/blackboard/events；
  **`diff_since()` 是黑板上下文注入的唯一来源**）；
- `pentest/contract.py`：验收契约纯函数（minimum_coverage ≥0.8、max_dead_end_ratio ≤0.5、
  required_deliverables），供 Verifier 初筛。

### 2.3 审批门（ApprovalGate，L5）

`pentest/approval.py`：

- 工具分级词表 `TOOL_TIERS`：`T1` 只读自动放行（不产生记录）／`T2` 需审批（grant 后同签名
  缓存放行）／`T3` 高危每调用暂停等人审／`T4` 结构化禁止；默认 T1；
- `ApprovalGate.request()` 是唯一入口，落 `ApprovalRecord(pending)` 并发射事件
  `approval/requested`，返回结构化「暂停执行」；`grant(request_id, decision, approver, note)`
  仅能裁决 pending，发射 `approval/resolved`；
- 审计双写：每笔记录进 EventBus + （可选）经 store 落 SQLite events 表；
- Web 面：前端 ApprovalCard → `POST /api/respond`（rpcId 原样回响）→
  `server/state.py` 写裁决并唤醒等待中的 runner。

### 2.4 沙箱（L5，fail-closed）

`sandbox/` 四件套：

- `backend.py`：`SandboxBackend` 协议 + `ExecResult` + `SandboxUnavailableError`（后端不可用
  → 调用方必须当「命令不能执行」，**禁止裸跑回退**）；
- `bubblewrap.py`：真实进程级隔离（`--unshare-all` + 根只读绑定 + 仅工作区可写 + tmpfs），
  沙箱启动失败抛 `SandboxUnavailableError`；
- `policy.py`：命令分级词表（read-only / workspace-write / danger 三档），danger 拦截
  `rm -rf /`、`mkfs`、dd 写裸盘、根递归 chmod/chown、关机重启、fork 炸弹等；
- `selfcheck.py`：`python -m sandbox.selfcheck` 自检 bwrap 二进制 / 真实 userns 探针 /
  危险词表条数 / fail-closed 行为（退出码 0=可用，1=存在不可用项）。

门面 `sandbox/__init__.py` 的 `confine(argv, policy, backend)` 在执行链上统一裁定
（tier=danger + backend 可用 → `SandboxBlockedError`；任何 backend 不可用 → 抛
`SandboxUnavailableError`，永不降级裸跑）。

### 2.5 知识层三路（L4）

`pentest/knowledge/`：

| 路 | 模块 | 说明 |
|---|---|---|
| L4a 本地 | `local_rag.py` | SQLite FTS5 关键词检索 + 可选 embedding（无向量自动退纯关键词），离线可用 |
| L4b 联网搜索 | `web_search.py` | DeepSeek Anthropic 兼容端点 `web_search_20250305` 服务端工具；复用 `DEEPSEEK_API_KEY`，未配置优雅降级 |
| L4c 网页抓取 | `web_fetch.py` | HTML→Markdown（标准库 parser）；**ssrf_check 纯函数**：解析 IP，保留段仅当 IP 显式在 allowed_targets 内放行，DNS rebinding fail-closed |

- `engine.py`：`KnowledgeEngine` 三路路由（本地总先查 → 本地最高置信度 < high 且允许联网才
  触发 web_search → web_fetch 永不自动、只抓显式传入 URL）+ 跨源去重按置信度排序 + 单路超时；
- `sources/`：本地知识源（`cve_db` / `tool_manuals` / `methodology` / `negative_findings`，
  负面知识对接 L6 NegativeKnowledge，失败不成为执行循环硬依赖）。

### 2.6 学习层蒸馏（L6）

`pentest/learning/`（engagement 结束后把事件日志蒸馏为可复用知识）：

- `models.py`：Lesson（经验）/ Playbook + PlaybookStep（剧本，仅 validated AttackChain 才蒸馏）/
  NegativeKnowledge（负面知识，overturned 只标记不删除）/ GrowthMetrics；
- `store.py`：SQLite 四表（lessons / playbooks / negatives / growth_metrics），upsert 幂等、
  计数走 bump/record 增量；
- `distiller.py`：`Distiller.distill(EngagementTrace)` → `{lessons, playbooks, negatives,
  metrics}`；LLM 可注入（一次性低频），无 LLM / 抛错 / schema 无效 → **规则回退离线可用**；
- `retriever.py`：`LearningRetriever.retrieve(profile, hypothesis)` 三路（负面拦截 →
  playbook 步骤注入 → lesson 注意事项），`fingerprint_tokens()` 与 distiller 共用同一指纹口径；
- `metrics.py`：`compute_growth_metrics`（假设命中率/死路推翻率/inconclusive 率/token 每链成本）。

### 2.7 报告引擎（纯函数投影）

`profiles/practical_pentest/report/engine.py`（R5，无 LLM）：

- 输入 = `EngagementSnapshot`（TargetProfile + events + 注入的元数据时间），相同输入相同输出，
  可离线快照回放（`tests/replay/test_snapshot_replay.py`）；
- 报告结构：执行摘要 → 攻击面图谱 → 发现详情（CVSS/复现步骤/证据/修复建议）→
  **已排除攻击面（负面发现章节）** → **跨目标攻击链** → 方法论 → 局限性；
- `find_cross_target_chains()`：A 目标获得的凭据用于 B 目标的跨目标攻击链检测；
- 模板 `profiles/practical_pentest/report/templates/pentest_report.yaml` 为「结构合同」，
  默认文案权威来源 `profiles/practical_pentest/config.yaml`；
- Web 面 `/api/report` 桥接本引擎（`server/api.py`），章节标签在
  `server/api.py::REPORT_SECTION_TITLES`。

### 2.8 工具适配器（L3）

`pentest/tool_adapters/`：`base.py` 定义协议与校验（`reject_shell_chars / require_dns_name /
require_http_url / reject_crlf`，防参数注入），共 **14 个适配器**：

- R2 解析器：`nmap` `httpx` `whatweb`（parse_output 字段级提取）；
- R4.5 命令型：`dnsx` `subfinder` `certsh` `feroxbuster` `nuclei` `sqlmap` `nikto`
  `searchsploit` `netexec` `bloodhound` `impacket`（均实现 `build_command` 参数注入校验
  + `parse_output` 结构化提取）。

CLI 执行侧的本地安全工具集仍在 `arsenal/registries/sec_tools.py`（92 个 YAML 定义、按
`shutil.which` 可用性装载、`run_tool` 调用），供执行循环与 `/api/run` 工具白名单
（`pentest_target.ALLOWED_RECON_TOOLS`）使用。

### 2.9 工具管线与渐进披露（core/）

- `core/events.py`：进程级 `EventBus`（`BUS`），事件格式
  `{seq, ts, task_id, kind, data}`；**SQLite 为唯一真相源，events.jsonl 仅人读留痕**；
- `core/hooks.py`：`EventStreamHooks` 把 SDK 回调投影为事件流 + 多技能渐进披露 + 增量打分
  + `_flush_emit_buffer` 缓冲落盘（关键事件立即刷盘）；
- `core/tool_pipeline.py`：统一工具调用管线（pre → guard → around → post 可插拔
  middleware）：BruteGate（爆破预算）、prompt 注入防护、
  ArtifactSpill（输出外置）、L5 confine 接线等；
- `core/agents_def.py`：Strategist/Executor/Reporter/Compactor 定义与动态 instructions；
- `core/task_context.py`：TaskContext 执行现场 + `L5GuardrailConfig`
  （scope/policy/backend/approval 鸭子类型注入）+ `SubtaskBudget`。

### 2.10 前端 UI（apps/web）

React 19 + TypeScript + Vite + CSS Modules（`--secai-*` Design Token，三栏布局，主题三态）：

- 通信层：`src/connection/api.ts`（契约类型）与 `connection.ts`（ConnectionController：
  双 WS 纯下行 + 指数退避重连 + HTTP RPC）；
- 运行层：`src/runtime/`（demo.ts 内嵌 mock 帧 / appRuntime.ts 装配，`DEMO_MODE` 常量
  切换「离线 DEMO 驱动」与「真实通道联调」；engagement/session/projections 状态对象）；
- UI 组件：conversation（ChatView/ApprovalCard/HypothesisQueue/DeadEndList）、details
  （EvidenceChain/PortScanResult/ToolOutputPanel/ReportPreview/LearningPanel）、sidebar
  （TargetList/TargetItem/ApprovalBadge）、primitives、theme。

### 2.11 CTF 跑分面（已随 9_6 整体删除，历史存档）

> 9_6 起 `profiles/ctf_legacy/`、`bench_platform/`、`app/`、`prompts/tsec_task.txt`
> 已整体删除，本节仅作历史存档，所述模块均已不存在。

R4 H12 曾把 CTF 专属假设全部收敛（历史描述）：

- `profiles/ctf_legacy/task.py`：读 `prompts/tsec_task.txt` 模板替换凭证占位符（`build_default_task`）；
- `profiles/ctf_legacy/platform.py`：提交铁律 `_submit_flags_if_any` / 通关机械复核
  `_is_completed` / `finalize`（未复核通关拒绝收尾）；
- `bench_platform/platform_client.py`：唯一懂平台协议的地方；`get_platform_client()` 模块级
  单例是**唯一构造点**，`platform_configured()` 判定凭证；
- `bench_platform/scheduler.py`：零 LLM 纯函数调度层（EV 选题 / 难度分级停滞决策 / 容器 SOP /
  终局回捞），供 `app/main.py` 调度循环调用。

---

## 3. 部署与运行

### 3.1 环境要求

| 项 | 要求 |
|---|---|
| Python | ≥ 3.11（仓库 `.venv` 为 3.13；ruff 目标 py311） |
| Node | ≥ 20.19 或 ≥ 22.12（vite 8 engines 要求，见 `apps/web/package.json`） |
| 系统 | Linux（bwrap 沙箱需 bubblewrap；VPN/安全 CLI 依赖 bash） |
| 网络 | 模型网关（OpenAI 兼容）；内网目标需 OpenVPN |

### 3.2 安装（venv + requirements）

```bash
cd /home/kali/SECAI
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # 全量依赖：运行 + server 面 + dev（质量门）
```

`requirements.txt` 已分节收录全部依赖：LLM 编排核心（openai-agents/requests/ddgs/python-dotenv/pyyaml）、知识层 HTTP（httpx）、server 面（starlette/uvicorn/websockets）、dev 质量门（pytest/ruff）。
`pyproject.toml` 的 `[project].dependencies` 与 requirements 运行时对齐；`pip install -e ".[dev]"` 亦可作为备选安装路径。

### 3.3 配置 .env（只入 .env，gitignored）

```bash
cp .env.example .env    # 真实密钥写 .env；.gitignore 已排除 .env
```

| 键 | 必填 | 说明 |
|---|---|---|
| `LLM_API_KEY` | ✅ | 主模型 API Key（缺省回退读 `OPENAI_API_KEY`） |
| `LLM_BASE_URL` | | OpenAI 兼容网关，可省略末尾 `/v1`（代码自动补齐）；默认 `https://api.deepseek.com/v1` |
| `LLM_MODEL` | | 主模型名；默认 `deepseek-chat` |
| `ESCALATION_MODELS` | | 灾备模型池，单行 JSON：`[{"model","base_url","api_key","role"}]`，role ∈ backup/reasoning/cheap/fast/strong |
| `DEEPSEEK_API_KEY` | | L4 联网搜索（`pentest/knowledge/web_search.py`）专用；未配置时 web_search 优雅降级「不可用」 |
| `BENCHMARK_BASE_URL` / `BENCHMARK_TOKEN` | 已废弃 | 原 TSecBench 跑分平台凭证；9_6 跑分面删除后不再被任何代码读取（保留注释仅为历史存档） |
| `BRUTEFORCE_MAX_CALLS` / `HINT_BUDGET_RATIO` / `SUSPEND_SECONDS` | | 成本治理（默认 20 / 0.35 / 2700） |
| `MODEL_SWITCH_TURNS` / `MODEL_SELF_RESCUE_MAX` | | 模型惰性治理（默认 6 / 2） |
| `VPN_CONFIG` / `VPN_AUTH` / `VPN_CMD` | 内网 | OpenVPN 完整内容 / 账密 / 命令 |
| `TOOLS_DIR` | | 本地安全 CLI YAML 目录（缺省 `arsenal/tools/`） |

### 3.4 启动 Web 控制面（v4 主线）

```bash
# 1) 起后端（Starlette + uvicorn；自动加载根 .env）
.venv/bin/python -m server.main --port 8700

# 2) 前端（二选一）
cd apps/web
npm install        # 首次
npm run dev        # 开发态：vite 5173，/api 与双 WS 代理到 127.0.0.1:8700
npm run build      # 产物 apps/web/dist（server 以 SPA fallback 托管）
```

- 无 LLM key 也能打开页面：`server/fixture.py` 内置三会话 demo engagement（running /
  awaiting_approval / completed），与前端 `demo.ts` 同一叙事；
- 真实联调：前端 `apps/web/src/runtime/appRuntime.ts` 的 `DEMO_MODE` 翻 `false`，
  配好 `LLM_API_KEY` 后 `POST /api/run` 走真实执行 runner（ScopeCheck→审批→只读工具→黑板）。

### 3.5 启动方式（面向实战）

启动 Web 控制面后，经 `/api/run` 提交授权目标，由 `harness.SessionManager` 驱动
`pentest_target.py` 循环逐目标执行（ScopeCheck → 审批门 → 工具白名单 → 黑板），
事件实时经 mux WS 推送到前端。

```bash
.venv/bin/python -m server.main --port 8700    # 起后端（9_6 起唯一执行入口）
```

（本地安全 CLI 工具可用性/安装：`python -m arsenal.registries.sec_tools list|missing|install`。）

### 3.6 质量门与测试

```bash
bash scripts/gate.sh      # ruff check（受控架构路径）+ pytest tests/unit + pytest tests/replay，全程无网

.venv/bin/python -m pytest                    # 默认：436 个常规测试（pyproject addopts 排除 e2e）
.venv/bin/python -m pytest -m e2e             # 真实 LLM 联调（tests/e2e/test_server_run_live.py）；
                                              # 无 LLM_API_KEY/OPENAI_API_KEY 自动 skip
```

`-m e2e` 说明：默认 `pytest` / `gate.sh` 不触发真实 API（烧钱隔离），显式加 `-m e2e` 才跑；
e2e 用例为单目标 `127.0.0.1` 只读侦察，预算收敛（≤3 轮 / ≤90s / 审批超时自动拒）。

### 3.7 L5 沙箱自检

```bash
.venv/bin/python -m sandbox.selfcheck   # 退出码 0 = bwrap 链路可用；1 = 存在不可用项（按 fail-closed 处理）
```

---

## 4. 日志与监控

### 4.1 事件总线与落库（审计链地基）

- `core/events.py`：进程级 `BUS`（内存历史 + 订阅者分发），事件经
  `core/hooks.py` 的 `EventStreamHooks` 投影发射；
- SQLite 落库：`adapters/db.py`（`data/agent.db`，tasks/events 表，WAL，
  线程安全：每线程连接 + 写锁）；pentest 面 `pentest/blackboard/store.py` 的 `events` 表
  （ApprovalGate 审批记录等 R3 审计落库）；
- `events.jsonl` 仅人读留痕（崩溃现场保护除外）；关键事件（flag/阶段切换/网络异常/agent_end/
  prompt 漂移）立即刷盘绕过缓冲。

### 4.2 赛后/运行期报告（runtime/reporting.py）

| 产物 | 位置 | 内容 |
|---|---|---|
| `first_strike()` | 零 LLM 首轮机械预侦察 | LLM 介入前先探测常见入口/敏感路径（省一轮 LLM 回合） |
| `write_cost_report()` | `worker_*/cost_report.json` | token 明细 / 缓存命中率 / 死因 / 轮次 / 零增量统计 |
| `export_trajectory()` | `trajectory_<code>.jsonl` | 事件总线历史全量导出，供赛后回放 |
| `write_stuck_replay()` | `replay_stuck_<code>_turn<turn>_<reason>.jsonl` | 惰性点 ±5 步回放窗口自动导出 |
| `write_dashboard()` | `dashboard.json` | 四指标看板：缓存命中率 / 零增量事件数 / 轮次有效动作比 / 单题 token 成本 |

统一日志：`runtime/log.py`（终端 + `data/logs/secai-YYYYMMDD.log` 双写，级别着色，
AI 思考 reasoning 实时打印，`SECAI_SHOW_THINKING=0` 关闭）。

### 4.3 Web 控制面观测

- `server/ws.py`：mux 流连接后先回放全部已知会话快照（首帧 `session/subscribed`，
  回放基线 = lastSeq），随后持续推送 live 帧；host 流回放会话登记（`host/session-added`）；
  两条流均纯下行（客户端上行 1008 拒绝，见 `Hub` 慢消费者丢帧兜底）；
- `server/state.py`：BUS 订阅 → `SessionRec.events`（断线重连回放基线）→ 即时扇出
  `session/event`；demo ticker 让 running 会话离线也有周期性活动帧。

### 4.4 审计链样例

```
工具调用 → L5 confine（policy 词表 + bwrap）→ ApprovalGate.request
  → EventBus(approval/requested) → events 表 + mux WS 帧
  → 人工 POST /api/respond → ApprovalRecord(resolved) 入事件总线/表 → runner 唤醒放行
```

---

## 5. 安全机制

| 机制 | 实现 | 位置 |
|---|---|---|
| **授权范围守卫** | `ScopeConstraint` 纯函数（无 IO，fail-closed）：排除优先于授权，支持精确 IP/域名/CIDR/`*.` 通配/时间窗/forbidden_actions/强度上限；解析失败抛 `ScopeParseError` 不静默放行 | `pentest/scope.py` |
| **T1–T4 分级** | T1 自动放行 / T2 审批可预授权缓存 / T3 高危每调用人审 / T4 禁止；未登记默认 T1；词表与语义在 `pentest/presets/pentest_preset.py::TOOL_TIER_KEYS` | `pentest/approval.py` + `pentest/presets/` |
| **人工审批门** | `ApprovalGate`：请求/裁决/缓存/审计双写（事件总线 + events 表），线程安全 | `pentest/approval.py` |
| **命令沙箱** | bwrap fail-closed：`--unshare-all` 根只读、仅工作区可写；后端不可用禁止裸跑（`SandboxUnavailableError`）；danger 词表永不执行（`SandboxBlockedError`） | `sandbox/` |
| **参数注入校验** | `reject_shell_chars / require_dns_name / require_http_url / reject_crlf`（防 CRLF/空字节）；URL 白名单字符集 | `pentest/tool_adapters/base.py` |
| **SSRF 防护** | `ssrf_check`：环回/链路本地/RFC1918/CGNAT/保留段等要求解析 IP 显式在 allowed_targets；域名多 IP 任一被拒整体拒绝（防 DNS rebinding） | `pentest/knowledge/web_fetch.py` |
| **prompt 注入防御** | 工具输出统一扫描注入特征，命中追加安全提醒、按不可信数据处理 | `tools/domains/_base.py` + `core/tool_pipeline.py` |
| **只读白名单** | `/api/run` 的 `run_recon_tool` 只放行无副作用侦察工具（nmap/httpx/whatweb/dnsx/subfinder/certsh/searchsploit/arp-scan），超出结构化拒绝 | `harness/runner/pentest_target.py` |
| **密钥边界** | 真实 key 只入 `.env`（gitignored）；模板/文档示例走 `.env.example`；VPN 配置目录 `vpn/` 与 `*.ovpn` gitignored；`docker/` 大包 gitignored | `.gitignore` |
| **黑板事实不变量** | `ProjectFact.validate()`：未记录事实不得宣称漏洞；写入即校验 | `pentest/blackboard/facts.py` |
| **run 预算护栏** | max_rounds ≤10（默认 3）/ 墙钟 ≤300s（默认 60）/ 审批等待 ≤45s 自动拒 / token 硬顶 100k | `server/run_spec.py` |

---

## 6. 性能与缓存

### 6.1 动态上下文增量注入

- charter/plan 版本化（`core/context_manager.py` / `harness/runner/context.py`），
  field_notes 仅首轮注入；动态上下文按「增量」进 user message，不动静态 prompt
  （H3：第 10 轮 ≤40% 全量）；
- `TargetProfile.surface_diff()` 只注入与上一快照的差异（L1）；
- 黑板注入：`BlackboardStore.diff_since()` 是黑板上下文注入的**唯一来源**；
  `core/memory.py::render_blackboard_snapshot()` 压缩锚点只保留 verified/confirmed 关键条目。

### 6.2 上下文压缩（compaction）

- `COMPACT_TOKEN_THRESHOLD = 20000` 触发 Compactor 摘要（append-only：定点截断旧工具输出 +
  摘要锚点追加，不 clear_session 保前缀缓存）；`_split_for_compact` 回合边界切分，保证
  tool_calls 配对不拆散；被摘要旧 items 归档 `compacted_archive.jsonl`；
- 卡壳自救可 force 压缩（`compact_if_needed(force=True)`，`core/context_manager.py`）。

### 6.3 缓存命中统计

- 静态 system prompt 每轮字节级 hash 断言（漂移即 `[cache-guard]` ERROR）；
- `cost_report.json` 与 `dashboard.json` 输出真实 `prefix_hit_rate`（cache_read/总量，
  目标 ≥85%）与 cache_hits/cache_misses；H4 后零增量轮为真实汇总。

### 6.4 模型灾备池与预算护栏

- `adapters/config.py` 解析主模型 + `ESCALATION_MODELS`（role: backup/fast/strong/
  reasoning）；`runtime/model_pool.py` 在额度/限流/鉴权/状态码失败时切换候选模型并保持
  同一 SQLiteSession；全部耗尽抛 `ModelExhaustedError`；
- `runtime/model_fallback.py`：外层 Agent（Strategist/Reporter/…）的 Runner.run 灾备包装
  （永久失败拉黑 / 暂时失败冷却重试 / 最多 max_rounds 轮）；
- `runtime/budget.py`：爆破预算 / hint 预算 / 换脑 switch / 挂起 suspend + 按难度分档
  （token 与墙上时钟双档），全部阈值集中本文件（单一事实源）；
- `server/run_spec.py`：服务面轮次/墙钟/审批超时/token 四重收敛（防真实 LLM 烧钱）；
  e2e 实测典型 2 轮 / ≤4 次 LLM 调用即收敛。

---

## 7. 目录结构

```
SECAI/
├── server/                  # v4 Web 控制面（Starlette + uvicorn）
│   ├── main.py              #   app 组装 + `python -m server.main --port 8700`
│   ├── api.py               #   七方法 RPC（describe/targets/engagements/run/steer/respond/report）
│   ├── ws.py                #   /api/events.mux + /api/events.host 纯下行帧流
│   ├── state.py             #   AppState：SessionManager 桥接 + BUS 订阅 + Hub 扇出
│   ├── run_spec.py          #   /api/run 请求归一 + 预算护栏
│   ├── fixture.py           #   demo engagement（离线三态展示）
│   └── static.py            #   GET / → apps/web/dist（SPA fallback）
├── apps/web/                # React 19 + TS + Vite 前端（src/connection、runtime、components）
├── harness/                 # L2 编排层
│   ├── session_manager.py   #   多目标并行 SessionManager（独立 bus + 独立 state）
│   └── runner/              #   executor(ExecutorLoop) / state / verifier(双核) / subtasks /
│                             #   context / orchestrator / pool / pentest_target(/api/run runner)
├── pentest/                 # L0/L1 数据模型 + L4/L5/L6 模块
│   ├── scope.py             #   授权范围守卫（纯函数）
│   ├── target_profile.py    #   目标认知状态机（L1）
│   ├── hypothesis.py        #   假设队列（L1）
│   ├── deadends.py          #   死路蒸馏（L1）
│   ├── contract.py          #   验收契约（纯函数）
│   ├── approval.py          #   ApprovalGate（T1-T4）
│   ├── blackboard/          #   L0 事实黑板（categories/facts/store → pentest.db 五表）
│   ├── presets/             #   PENTEST_PRESET 注册 + 子 agent 模板
│   ├── tool_adapters/       #   14 个工具适配器（base + nmap/httpx/whatweb/dnsx/subfinder/…）
│   ├── knowledge/           #   L4 知识层（local_rag/web_search/web_fetch/engine/sources）
│   └── learning/            #   L6 学习层（models/store/distiller/retriever/metrics）
├── sandbox/                 # L5 沙箱：backend(协议) / policy(词表) / bubblewrap / selfcheck
├── profiles/                # 任务画像族
│   └── practical_pentest/   #   config.yaml + report/（纯函数报告引擎 + YAML 模板）
│                             #   （9_6 注：ctf_legacy/ 已随 CTF 跑分面整体删除）
├── core/                    # 事件总线 / hooks / 工具管线 / Agent 定义 / 上下文管理
│   ├── events.py            #   EventBus + BUS（进程级）
│   ├── tool_pipeline.py     #   统一工具调用管线（middleware）
│   ├── hooks.py / agents_def.py / task_context.py / context_manager.py / charter.py / memory.py
├── tools/domains/           # 执行工具按域拆分（exec/web/knowledge/payload/seccli/… + registry）
├── runtime/                 # model_pool / model_fallback / budget / stuck / reporting / log / …
├── adapters/                # config（env/模型）/ db（SQLite data/agent.db）
├── arsenal/                 # 声明式资产库：roles(17)/skills(97)/tools(92 CLI)/vulns/pocs/
│                             # knowledge/payloads + registries
├── demo_tools.py            # re-export shim（R1 后收敛为兼容导出层）
├── scripts/gate.sh          # 质量门（ruff + tests/unit + tests/replay，无网）
├── docs/                    # SECAI架构设计文档 / USER_GUIDE / DEBT_LEDGER 等
├── tests/                   # unit（含 runner/pentest/knowledge/learning/sandbox）+
│                             # replay（快照回放）+ e2e（真实 LLM 联调，-m e2e）
├── pyproject.toml           # 项目元数据 / pytest 配置 / ruff 规则
├── requirements.txt         # 核心运行依赖
├── .env.example / .env      # 配置模板 / 真实密钥（gitignored）
└── data/                    # 运行时：agent.db / worker_*/ / field_notes.md / logs /
                              # mission_charter.md / dashboard.json（gitignored）
```

---

## 8. 文档索引

| 文档 | 内容 |
|---|---|
| [docs/SECAI架构设计文档.md](docs/SECAI架构设计文档.md) | 设计思想 / 六层架构 / 数据模型 / 护栏 / 知识 / 学习 / server+前端 深度规格（与本文 README 分工：README=概览+运维，架构文档=设计+规格） |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | 用户手册（配置参考、运行模式、故障排查） |
| [docs/DEBT_LEDGER.md](docs/DEBT_LEDGER.md) | H1–H13 债务「承诺-现状」核对表（R0–R6 已全结清） |
| docs/SecAI 系列手册 | 历史工程化/诊断/修复手册（随版本演进归档，与 v4 现状存在差异时以代码为准） |

---

## 免责声明

本系统仅用于**授权的安全测试、CTF 竞赛与靶场练习**。禁止用于任何未授权的渗透测试或攻击
行为。使用者需自行承担合规责任，并遵守目标系统所在司法辖区的法律法规。
