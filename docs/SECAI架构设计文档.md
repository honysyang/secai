# SECAI 架构设计文档

> 多智能体安全攻防框架（AutoPentest）技术架构与工程设计说明
> 版本：v1.1 ｜ 更新：2026-08-14

---

## 目录

1. [项目概述](#1-项目概述)
2. [设计思想与核心原则](#2-设计思想与核心原则)
3. [总体架构](#3-总体架构)
4. [多智能体体系](#4-多智能体体系)
5. [工作模式与工作流程](#5-工作模式与工作流程)
6. [调度器（跑分编排）](#6-调度器跑分编排)
7. [杀伤链（Kill-Chain）](#7-杀伤链kill-chain)
8. [上下文生命周期管理](#8-上下文生命周期管理)
9. [工具体系](#9-工具体系)
10. [Skills 技能体系](#10-skills-技能体系)
11. [角色体系](#11-角色体系)
12. [漏洞检测体系](#12-漏洞检测体系)
13. [POC 与知识库](#13-poc-与知识库)
14. [前端可视化](#14-前端可视化)
15. [目录结构](#15-目录结构)
16. [关键设计决策与教训](#16-关键设计决策与教训)
17. [能力覆盖（四类题型）](#17-能力覆盖四类题型)
18. [内容资产](#18-内容资产)

---

## 1. 项目概述

SECAI 是一个基于 **openai-agents SDK** 的多智能体安全攻防框架，目标是把「AI 自动化渗透测试」从玩具级 Demo 提升为可工程化的跑分/实战系统。

核心能力：

- **多智能体协作**：Manager（立法）→ Planner（规划）→ Executor（执行）→ Reporter（战报）→ Compactor（压缩）
- **子任务并发**：`spawn_subtask` 声明子任务 + `finish_subtask` 结构化结束协议，主 Agent 上下文隔离
- **跑分调度**：面向 TSecBench 等靶场平台的「选题 → 启动 → 渗透 → 提交 → 关闭 → 换题」机械编排
- **成本治理**：爆破/hint 预算 → 无感知换脑（switch）→ 挂起（suspend），token + 时钟双档止损
- **信息增量判停**：从「看阶段切换」升级为「看产出质量」，正向证据清零、零增量累计，统一驱动 replan/判停
- **提交铁律**：工具输出先全文扫 flag 再机械提交，不靠 LLM 自觉
- **Prompt 注入防御**：工具输出统一检测注入特征，命中追加安全提醒、按不可信数据对待
- **题级角色派任**：每道题按 unique_code 前缀派任对应角色皮肤
- **上下文生命周期**：分层管理 + token 压缩 + 断点续跑 + 死路蒸馏 + 全局黑板（落盘持久化）
- **事件总线 + 落库**：进程级 EventBus → SQLite（tasks/events 表，WAL），events.jsonl 双写留痕
- **渐进披露 + 技能预算**：技能按触发词解锁，注入带预算（同屏 3 篇 / 每篇 1200 字 / 总 8k）
- **声明式内容**：skills / tools / roles / vulns / pocs / payloads / knowledge 全部本地化、自包含、可扩展
- **实时可视化**：标准库后端 + SSE 实时流 + 三页前端（对话/监控/智能体 kill-chain）

技术栈约束（硬性）：

- 后端：Python + openai-agents SDK（DeepSeek 等 OpenAI 兼容后端）
- 前端：原生 JS，无框架 / CDN / 外部库
- 无构建步骤，无额外运行时依赖

---

## 2. 设计思想与核心原则

### 2.1 五条公理

| 公理 | 含义 | 落地 |
|---|---|---|
| ① 提交是机械保证 | flag 提交不靠 LLM 自觉 | `_submit_flags_if_any` 扫描 → `submit_flag` |
| ② 调度是纯函数零 LLM | 选题/换题/看 hint/容器管理是代码 | `scheduler.py` |
| ③ LLM 只做假设与解读 | LLM 不承担机械决策 | 调度器 + 判停器接管 |
| ④ 状态外置、重启无感 | 进度持久化 | checkpoint + SQLiteSession + 黑板 |
| ⑤ 观测是状态的投影 | 事件流 = 可观测事实源 | RunHooks → events.jsonl → UI |

### 2.2 关键设计思想

1. **让 LLM 做高层判断，让代码做确定性检测** —— 借鉴 sec-agent / ctfSolver，fuzz / 差分检测 / 提交铁律等确定性动作由代码完成，LLM 只负责"往哪打、怎么解读"。
2. **先易后难、先存在后复杂** —— 通用层优先，靶场/flag 铁律后接入。
3. **自包含、可扩展** —— 所有内容（技能/角色/漏洞/POC/payload）都是声明式本地文件，改文件即改能力，无需改代码。
4. **default-soft 容错** —— 工具失败是"信息"不是"死路"，交给 LLM 决策，只有明确死路才判负向。
5. **止血 > 增产 > 降本** —— 先修丢分项，再做吞吐优化，最后降成本。

---

## 3. 总体架构

```
                        ┌─────────────────────────────────────────┐
                        │          前端 static/（三页）             │
                        │ index(对话+任务流) / monitor(监控) /      │
                        │ agents(智能体 kill-chain)                │
                        └──────────────────┬──────────────────────┘
                                           │ SSE
                        ┌──────────────────▼──────────────────────┐
                        │            server.py（标准库 HTTP）        │
                        │  / /monitor /agents + /api/*(meta/tasks/ │
                        │  events/stream/stream-db)                │
                        └──────────────────┬──────────────────────┘
                                           │
                        ┌──────────────────▼──────────────────────┐
                        │              main.py（主编排）             │
                        │  Manager 立法 → Planner 规划 → 调度器循环   │
                        └───┬──────────┬──────────┬───────────┬───┘
                            │          │          │           │
                   ┌────────▼───┐ ┌────▼────┐ ┌───▼────┐ ┌────▼─────┐
                   │ agents_def │ │ scheduler│ │ hooks  │ │ stop_    │
                   │ Agent 定义  │ │ EV选题/  │ │ 事件流 │ │ policy   │
                   │ +子任务协议  │ │ 停滞决策 │ │ 增量打分│ │ 判停     │
                   └────────────┘ └─────────┘ └───┬────┘ └──────────┘
                            │                     │ BUS.emit
        ┌───────────────────┼─────────────────────┼──────────────┐
        │                   │                     ▼              │
  ┌─────▼─────┐      ┌──────▼──────┐      ┌───────▼──────┐ ┌────▼─────┐
  │ demo_tools │      │ context_    │      │ events.py    │ │ budget.py│
  │ 执行工具集  │      │ manager     │      │ 事件总线      │ │ 成本治理  │
  │ 提交铁律   │      │ 压缩/续跑    │      │ (→db.py SQLite│ │ 换脑/挂起 │
  │ 注入防御   │      │             │      │  落库)        │ │ 预算     │
  └───────────┘      └─────────────┘      └──────────────┘ └──────────┘
                            │
                       ┌────▼──────────────────────────────┐
                       │ 声明式内容：prompts/ roles/ skills/ │
                       │ tools/ vulns/ pocs/ knowledge/     │
                       │ payloads/                          │
                       └───────────────────────────────────┘
```

### 3.1 核心模块职责

| 模块 | 职责 |
|---|---|
| `main.py` | 主编排入口：立法 → 规划 → 调度器循环 → 报告 + 子任务并发 + 成本治理 |
| `agents_def.py` | Agent 定义与动态 instructions 组装 + 子任务结束协议 |
| `scheduler.py` | 跑分编排纯函数层（EV 选题 / 停滞决策） |
| `budget.py` | 成本治理（爆破/hint 预算 + 换脑/挂起），单一事实源 |
| `hooks.py` | RunHooks 事件流 + 渐进披露 + 信息增量打分 + 网络不可达检测 |
| `events.py` | 进程级事件总线（内存历史 + 订阅者分发） |
| `db.py` | SQLite 落库（tasks/events 表，WAL，线程安全） |
| `stop_policy.py` | 判停器（deadline / 零增益 / 空转 / 回合兜底） |
| `task_context.py` | TaskContext：执行现场 + 全局状态 |
| `context_manager.py` | 上下文压缩 + 断点续跑（state/session） |
| `demo_tools.py` | 执行工具集 + 提交铁律 + Prompt 注入防御 + 工具按需加载 |
| `platform_client.py` | 平台 SDK 语义封装（唯一懂平台协议的地方） |
| `platform_tools.py` | 平台 API 的 Agent 工具封装 |
| `status.py` | 阶段状态机（PHASE_DEFS + PHASE_TRANSITIONS） |
| `charter.py` | 使命宪章落盘 |
| 各 `*_registry.py` | 声明式内容加载器（skill/vuln/poc/knowledge/role） |

---

## 4. 多智能体体系

### 4.1 Agent 一览

| Agent | 角色定位 | 产物 | 触发时机 |
|---|---|---|---|
| **Manager** 管理者 | 立法（为什么打） | 使命宪章（目标/原则/约束/终止判据） | 任务开始，一次 |
| **Planner** 规划师 | 深度分析（打哪里、按什么顺序） | 作战计划 plan（任务研判/攻击面/flag 定位/分步计划） | 任务开始 + 停滞 replan |
| **Executor** 执行者 | 执行（怎么打） | 证据、黑板、flag | 每轮循环 |
| **Subtask Executor** | 子任务并发执行 | `finish_subtask` 结构化结论（summary/findings/flag） | 主 Agent `spawn_subtask` 后并发调度 |
| **Reporter** 报告者 | 战报 + 死路蒸馏 | 战报 + field_notes | 任务结束，一次 |
| **Compactor** 压缩器 | 历史压缩 | 压缩摘要 | 上下文超阈值时 |

### 4.2 数据流

```
task(完整任务书)
   │
   ├─→ Manager ──→ charter(使命宪章)
   │
   ├─→ Planner ──→ plan(作战计划)  ← 输入 = task + charter + role
   │
   └─→ Executor(动态 instructions)
         注入：角色风格 + 阶段 + charter + plan + 纪律 + playbooks
              + field_notes + 压缩摘要 + 黑板 + brief
         反馈：黑板 + 事件流 → replan(停滞) / compact(超阈值)
```

### 4.3 Executor 的动态 instructions

Executor 的 `instructions` 是**动态函数**，每轮从 `TaskContext` 读取最新状态重新渲染：

```python
def _instructions(ctx, agent):
    c = ctx.context
    return EXECUTOR_TEMPLATE.format(
        phase_name=c.phase,          # 当前阶段
        plan=c.plan,                 # 当前计划
        playbooks=load_skill_bodies(c.disclosed_skills),  # 已披露技能
        blackboard=_format_blackboard(c.blackboard),       # 黑板摘要
        compaction_summary=c.compaction_summary,           # 压缩摘要
        ...
    )
```

这样 hooks 运行时追加技能 / 切换阶段后，下一轮系统提示自动带上新状态。

---

## 5. 工作模式与工作流程

### 5.1 两种工作模式

| 模式 | 说明 |
|---|---|
| **通用模式** | 单 Executor 循环，Agent 自主编排，适合任意渗透任务 |
| **跑分模式（调度器）** | 代码机械编排「选题→启动→渗透→提交→关闭→换题」，适合 TSecBench 等平台 |

### 5.2 通用渗透工作流程

无论跑分还是通用任务，都遵循同一套「立法 → 规划 → 杀伤链推进 → 报告」的核心流程：

```
任务接收
  → ① Manager 立法（使命宪章：目标/原则/约束/终止判据）
  → ② Planner 规划（作战计划：任务研判/攻击面/flag 定位/分步计划）
  → ③ Executor 执行（按 5 阶段杀伤链推进：recon→enumerate→detect→exploit→post）
       ├─ 停滞 → replan（重新规划，最多 3 次）
       ├─ 超阈值 → compact（上下文压缩）
       └─ 关键进展 → 写黑板 / checkpoint
  → ④ Reporter 报告（战报 + 死路蒸馏 → field_notes 沉淀）
```

跑分模式是在这套通用流程上，**增加调度器层**（选题/容器/换题/hint 的机械编排），杀伤链本身不变。

### 5.3 跑分模式完整流程（3 槽并发）

```
① Manager 立法 → charter
② Planner 规划 → global_plan
③ 调度器主循环（零 LLM，3 槽并发）：
   while True:
     ├─ deadline 检查（比赛时限）
     ├─ list_challenges 拉题目
     ├─ 补满 3 槽（排除活跃题，避免重复选题）：
     │    ├─ EV 选题（死路降权）
     │    └─ start_challenge 启动容器 → 创建单题 asyncio 任务
     ├─ asyncio.wait(FIRST_COMPLETED) 等任一题完成
     ├─ 完成处理：close 容器 → 记录结果 → 补位
     └─ 任一题 fatal（任务结束）→ 取消其余任务 → 终止
④ Reporter 战报 + 死路蒸馏
```

吞吐：串行 ×1 → 并发 ×3，配合平台 3 容器上限。

### 5.4 单题循环（_run_single_challenge）

单题独立工作区（`worker_{code}`）+ 独立事件流，避免并发交错：

```
while True:
  ├─ 成本治理：token/时钟到换脑档 → 无感知 switch 模型
  ├─ 成本治理：token/时钟到挂起档 → suspended（腾槽，下轮 EV 重选）
  ├─ Runner.run(max_turns=1)   # 每轮一个 LLM 回合
  ├─ 阶段停滞检测 / 信息增量累计
  ├─ fatal → 全局终止（任务结束/token 无效）
  ├─ finalized → 本题 solved
  ├─ 空转 6 轮 → 换题
  ├─ 网络不可达 2 次 → 换题（防 VPN 死磕）
  ├─ 停滞按难度分级 → 机械看 hint / 机械换题
  ├─ hint 预算：卡题（≥2 失败路径）且 token 达挂起档比例 → 机械拉 hint
  ├─ 停滞 5 轮 → replan（重新规划本题）
  ├─ 子任务并发调度（spawn_subtask）
  └─ 上下文压缩（超阈值）
```

单题开始登记 `tasks` 表（生命周期），结束登记终态（solved/stuck/suspended/fatal），供监控页追溯。黑板落盘 `blackboard.json`，挂起/重试时回注进度。

---

## 6. 调度器（跑分编排）

`scheduler.py` 是**零 LLM 纯函数层**，实现报告 P0-4 的核心要求。

### 6.1 EV 选题

```python
EV = total_score × 难度系数 × 0.3^死路次数
难度系数：easy=1.3  medium=1.0  hard=0.7
```

- 先易后难（easy 加权），先拿能拿的分
- 死路降权（`0.3^attempts`）：同一题放弃一次，EV 乘 0.3，避免反复撞硬题

### 6.2 单题停滞决策（按难度分级）

| 难度 | 看 hint 阈值 | 换题阈值 |
|---|---|---|
| easy | 6 轮 | 12 轮 |
| medium | 8 轮 | 20 轮 |
| hard | 10 轮 | 25 轮 |
| 未知 | 8 轮 | 16 轮 |

- 连续 N 轮零增益且未看 hint → 机械 `get_hint`（看提示）
- 看完 hint 仍零增益到 skip 阈值 → 机械换题
- 连续 6 轮无工具调用 → 机械换题（空转放弃，与难度无关）
- 连续 2 次网络不可达（连接拒绝/超时/无路由）→ 机械换题（防 VPN 死磕）

### 6.3 容器 SOP

```
start_challenge
  ├─ 成功 → 记录 active_codes
  └─ ContainerBusy(max active 3)
       ├─ 关闭最旧活跃容器 → 重试 start
       └─ 无记录 → 关闭所有 available 残留 → 换题
```

- 启动前清理残留容器
- 结束（正常/中断）统一 `finally` 清理 active_codes
- 单题循环移除 Agent 的 `start/close/list` 工具，防止破坏调度器追踪

---

## 6.5 成本治理（budget.py）

治理规则收拢在 `budget.py`（单一事实源），避免在 config/scheduler/demo_tools/main 多处漂移。目标：Agent 空烧 token 时**机械止损**，而不是一路跑到超时。

| 层 | 触发条件 | 动作 |
|---|---|---|
| 爆破预算 | 单题爆破/枚举调用 ≥ `BRUTEFORCE_MAX_CALLS`（默认 20） | `brute_gate` 拦截后续爆破，强制转向定向验证 |
| hint 预算 | 卡题（≥2 条失败路径）且 token 达挂起档 `HINT_BUDGET_RATIO`（默认 0.35） | 机械拉 hint（比继续空烧便宜） |
| 换脑 switch | 单题 token 达 `switch_tokens`（按难度分档） | 无感知切换候选模型（`ESCALATION_MODELS`） |
| 挂起 suspend | 单题 token 达 `suspend_tokens` 或时钟达 `SUSPEND_SECONDS` | 停止本次尝试、释放槽位，下轮 EV 重选 |

挂起恢复：黑板落盘 `blackboard.json`，重选该题时回注上次进度（已完成/已排除结论），不重复劳动。

---

## 7. 杀伤链（Kill-Chain）

SECAI 的渗透执行采用 **5 阶段杀伤链**，对标经典 Cyber Kill Chain / MITRE ATT&CK 方法论，是「从侦察到拿 flag」的核心推进模型。

### 7.1 杀伤链对标

| SECAI 阶段 | 对标标准杀伤链 | 目标 | 关键活动 | 退出条件 |
|---|---|---|---|---|
| `recon` 侦察 | Reconnaissance | 摸清指纹与技术栈 | HTTP头/banner/证书/端口/路径 | 拿到指纹+端口+入口 |
| `enumerate` 枚举 | Weaponization | 枚举攻击面 | 端口/路径/入口/信任边界 Top-N | 列出攻击面清单 |
| `detect` 检测 | Delivery | 漏洞检测 | fuzz/detect_vuln 假设验证 | 确认漏洞 |
| `exploit` 利用 | Exploitation | 漏洞利用 | 拿权限/读文件/执行命令 | 拿到权限/读文件能力 |
| `post` 后利用 | Actions on Objectives | 拿 flag | 读 /flag、config.php、环境变量 | 提交 flag / finalize |

### 7.2 合法转移图

```
recon     → enumerate / post
enumerate → detect / recon
detect    → exploit / enumerate / post
exploit   → post / detect
post      → （终态）
```

- `set_phase` 工具校验合法转移，防止乱跳
- hooks 里 `_auto_advance_phase` 代码兜底：发现 flag 线索自动切 post，漏洞确认自动切 exploit
- 任意阶段发现 flag 线索可直切 post（跳过中间阶段，快速收分）

### 7.3 顶层流程阶段

```
legislate（立法）→ assign（派任）→ execute（执行）→ report（收尾）
```

杀伤链（recon→post）是 `execute` 阶段的内部子状态机，驱动 Executor 的 instructions 按阶段动态切换目标与焦点。

---

## 8. 上下文生命周期管理

### 8.1 四层上下文架构

| 层 | 内容 | 生命周期 |
|---|---|---|
| **L1 稳定层** | 任务书、宪章、工具 schema | 全程不变 |
| **L2 工作记忆** | 对话历史滑动窗口 | 压缩裁剪 |
| **L3 长期记忆** | 压缩摘要 + 黑板 + 死路蒸馏 | 跨轮持久化 |
| **L4 外置存储** | artifacts/ 文件 | 按需读取 |

### 8.2 Token 压缩

- `COMPACT_TOKEN_THRESHOLD = 30000`（估算，字符/2.5）
- 超阈值触发 Compactor 把旧历史压成摘要
- 摘要上限 `COMPACTION_SUMMARY_CHARS = 2000`，防二次膨胀
- `_split_for_compact` 回退到回合边界，保证 tool_calls 配对不拆散（避免 400）

### 8.3 断点续跑

- `state.json` 持久化 TaskContext 全字段（含 phase/plan/黑板/token/zero_gain）
- `session.sqlite` 持久化会话历史
- `--resume` 恢复；调度器模式下题目进度天然在平台侧（`is_completed`）

### 8.4 信息增量信号（reward）

`hooks._score_tool_result` 纯规则打分：

- **正向（+1）**：flag / 漏洞确认 / 登录成功 / 响应差异 / HTTP 状态码 / 开放端口
- **中性（0）**：常规侦察、工具失败（default-soft，交 LLM 决策）
- 无负向（对齐 sec-agent 的 default-soft：失败是信息不是死路）

该信号统一驱动 **replan**（连续 5 轮零增益）和 **判停**（连续 50 轮零增益兜底）。

此外还有独立的**网络不可达检测**（`hooks._is_network_unreachable`）：

- 识别连接拒绝 / 超时 / 无路由 / DNS 失败
- 连续 2 次命中 → 判定本题不可达，机械换题
- 解决「VPN 断开后 Agent 死磕同一题 30+ 轮」的实战丢分问题

### 8.5 提交铁律

`_spill_output` 在截断前先扫描全文 flag：

```
shell / http_request / fuzz 输出 → 先扫 flag{...} → 截断外置
   └─ 扫描到 flag + 已知 current_code → 机械 submit_flag
       └─ 回执拼在返回尾部："[系统·提交铁律] flag → correct=true"
```

解决「flag 埋在截断点之后 LLM 看不见」和「提交靠 LLM 自觉」两个问题。

### 8.6 全局黑板

- 结构：`{key: {value, status, ts, verified, evidence, supersedes}}`（status: pending/doing/done/failed）
- LRU 淘汰（`BLACKBOARD_MAX_ENTRIES = 50`，优先淘汰 done/failed）
- 注入系统提示只列 key/状态/验证标记/时间摘要，完整值用 `blackboard get` 按需取
- 关键进展（登录成功/确认漏洞/flag 路径）强制写黑板，压缩后靠黑板恢复记忆
- **结构化记忆**：`verified` 标记是否已验证；`evidence` 附证据（判死必须附证据）；`supersedes` 指向被取代的旧 key
- **落盘持久化**：`blackboard.json` 跨尝试/挂起恢复，重试同一题回注上次进度，不重复劳动

### 8.7 死路蒸馏

- Reporter 输出「死路蒸馏」清单，追加进 `field_notes.md`
- 下次执行注入 `field_notes` 尾部，实现「死路不重复」接力

---

## 9. 工具体系

### 9.1 核心工具（常驻）

`CORE_TOOL_NAMES`（14 个，任何任务都需要）：

```
shell, http_request, read_artifact, write_file, finalize, checkpoint,
blackboard, set_phase, find_skills, fuzz, spawn_subtask, parallel_shell,
enable_tool, list_disabled_tools
```

### 9.2 工具分组（按需加载）

| 组 | 工具 | 用途 |
|---|---|---|
| `web` | distinguish, web_search | 差分实验、联网搜索 |
| `seccli` | list_tools, get_tool_spec, run_tool | 92 个本地安全 CLI |
| `poc` | search_cve, get_poc | CVE/POC 检索 |
| `vuln` | list_vulns, detect_vuln, get_payload | 漏洞类型检测 |
| `knowledge` | list_knowledge, get_knowledge | 知识库查询 |
| `vpn` | connect_vpn | VPN 启用 |
| `platform` | check_vpn, list_challenges, start_challenge, get_hint, submit_flag, close_challenge | 平台 API |

### 9.3 按需加载机制

- 核心工具常驻，非核心工具挂 `is_enabled` 动态开关
- `enable_tool` 按组/按名启用，`list_disabled_tools` 查看未挂载
- 默认启用：核心 + platform + vpn + seccli；web/poc/vuln/knowledge 按需

### 9.4 特色工具

| 工具 | 说明 |
|---|---|
| `fuzz` | 代码级差分模糊测试（并发 + 响应归一化归组），替代手写 shell 循环 |
| `distinguish` | 差分实验：多探针对比响应差异定位攻击面 |
| `spawn_subtask` | 声明独立子任务，主循环 `asyncio.gather` 并发 |
| `connect_vpn` | 后台启用 VPN，验证 tun0 隧道真正建立 |
| `read_artifact` | 读取外置到 artifacts/ 的工具输出全文 |

### 9.5 本地安全 CLI（92 个）

`tools/*.yaml` 声明式注册表，涵盖：nmap、sqlmap、ffuf、nuclei、gobuster、hydra、dirsearch、masscan、nikto、wpscan、metasploit、john、hashcat、gdb、radare2、angr、pwntools、slither、mythril 等。

- `sec_tools.py` 负责加载、构建命令、执行
- `list_tools` / `get_tool_spec` / `run_tool` 结构化调用
- 启动时可 `shutil.which` 普查可用性（P2 优化）

---

## 10. Skills 技能体系

### 10.1 结构

`skills/` 目录，62 个 Markdown 文件（顶层 + 分类子目录）：

```
skills/
├── unknown_target_sop.md     # 未知目标通用 SOP
├── file_read_oob.md          # 文件读取越权
├── filter_bypass.md          # 过滤绕过
├── evasion_dual_track.md     # 免杀双轨
├── sandbox_escape.md         # 沙箱逃逸
├── tcp_binary.md             # TCP 二进制协议
├── ai_security.md            # AI 安全
├── binary/                   # 二进制（pwn_exploitation/static_analysis）
├── ai_security/              # AI 安全（tool_misuse）
├── blockchain/               # 区块链（smart_contract_security）
├── cloud/                    # 云安全
├── coordination/             # 协调
├── custom/                   # 自定义技能
├── frameworks/               # 框架
├── protocols/                # 协议
├── reconnaissance/           # 侦察
├── scan_modes/               # 扫描模式
├── technologies/             # 技术栈
├── tooling/                  # 工具使用
└── vulnerabilities/          # 漏洞（23 个 Web 漏洞技能）
```

### 10.2 渐进披露（Progressive Disclosure）

- 初始技能包在角色派任时注入
- 运行中 hooks 扫描工具输出，命中触发词 → 追加技能到 `disclosed_skills`
- `find_skills` 工具可主动检索并解锁
- 下一轮动态 instructions 自动带上新技能（避免一次性塞 58 篇撑爆上下文）

---

## 11. 角色体系

### 11.1 角色定义（9 个）

`roles/*.md`，frontmatter（name/pattern/playbooks）+ 思维风格正文：

| 角色 | 匹配 pattern（前缀锚定） | 定位 |
|---|---|---|
| `web_auditor` | Web 应用 | Web 应用审计员 |
| `binary_protocol_analyst` | f1/二进制/tcp | 二进制/协议分析师 |
| `sandbox_escape_expert` | 沙箱/e2 | 沙箱逃逸专家 |
| `evasion_craftsman` | 免杀/e3/检测 | 免杀规避工匠 |
| `boundary_penetration` | WAF/边界/e1 | 边界渗透 |
| `lateral_movement` | 横向移动 | 横向移动 |
| `privilege_escalation` | 提权 | 提权 |
| `ai_security_tester` | AI 安全 | AI 安全测试员 |
| `tscbench` | TSec/Benchmark/跑分 | TSecBench 跑分专员 |

### 11.2 派任机制

- `assign_role(code, description)`：先按 `^` 前缀锚定题型，再按描述关键词，最后 fallback 通用侦察兵
- **题级派任**：调度器每 start 一题，用 `unique_code` 前缀 + 描述派任，注入该题专属 playbook（报告 P0-5）
- 角色 = 思维风格模板 + 初始技能包，与阶段机正交分离

---

## 12. 漏洞检测体系

### 12.1 漏洞类型模块（9 个）

`vulns/*.yaml`，每种漏洞一份结构化检测规范：

```
SQLI, XSS, SSTI, LFI, RCE, IDOR, SSRF, XXE, UPLOAD
```

每份 YAML 含：name / description / need_detect / prompt / payloads

### 12.2 检测工具

- `list_vulns`：列出内置漏洞类型
- `detect_vuln(vuln_type)`：取某类型的标准检测规范 + payload
- `get_payload(payload_type)`：取 payload 字典

### 12.3 Payload 字典（10 个）

`payloads/*.txt`：`sqli / lfi / path / xss / ssti / rce / idor / ssrf / upload / xxe`

配合 `fuzz` / `parallel_shell` 使用。

---

## 13. POC 与知识库

### 13.1 POC（3 个精选）

`pocs/` 目录，`search_cve` 检索 + `get_poc` 取全文，含利用原理/步骤/载荷/验证方式。

> 注：早期曾导入 1967 个只有元数据无 poc 段的死数据，已清理，只保留有效 POC。

### 13.2 知识库（3 个条目）

`knowledge/` 目录，`list_knowledge` 看简介 + `get_knowledge` 按 id 取全文（如 get_flag / post_exploit / waf_bypass）。

---

## 14. 前端可视化

`static/`（三页）+ `server.py`（标准库 HTTP + SSE 实时流）：

| 页面 | 路由 | 用途 |
|---|---|---|
| 对话 / 任务流 | `/` | 左对话流（`dir=web`）+ 右任务流（`dir=generic`），实时展示 Agent 思考/执行 |
| 监控页 | `/monitor` | 任务生命周期（status/answer/事件数/最新活动），按题追踪 |
| 智能体页 | `/agents` | 按 kill-chain 展示 5 个智能体 + 各阶段对应工具/流程 |

- 事件类型：`llm_call / thought / tool / tool_result / reward / phase_changed / net_unreachable / token / skill_disclosed / agent_start / agent_end`
- 文本不截断，实时展示阶段切换、token 预算、信息增量、网络不可达
- 事件经 `events.py` 总线 → `db.py` 落库（tasks/events 表），监控页经 `/api/tasks` / `/api/events` / `/api/stream-db` 追溯历史

---

## 15. 目录结构

```
SECAI/
├── main.py                 # 主编排（立法→规划→调度→报告 + 子任务并发 + 成本治理）
├── agents_def.py           # Agent 定义 + 动态 instructions + 子任务结束协议
├── scheduler.py            # 跑分调度器（EV选题/停滞决策，纯函数）
├── budget.py               # 成本治理（爆破/hint 预算 + 换脑/挂起）
├── hooks.py                # 事件流 + 渐进披露 + 增量打分 + 网络不可达检测
├── events.py               # 进程级事件总线（内存历史 + 订阅者分发）
├── db.py                   # SQLite 落库（tasks/events 表，WAL，线程安全）
├── stop_policy.py          # 判停器
├── task_context.py         # TaskContext 状态
├── context_manager.py      # 压缩 + 断点续跑
├── demo_tools.py           # 执行工具 + 提交铁律 + 注入防御 + 按需加载
├── platform_client.py      # 平台 SDK 语义
├── platform_tools.py       # 平台 API 工具
├── status.py               # 阶段状态机
├── charter.py              # 宪章落盘
├── sec_tools.py            # 92 个 CLI 工具执行
├── role_registry.py        # 角色加载/派任
├── skill_registry.py       # 技能加载/渐进披露
├── vuln_registry.py        # 漏洞检测模块加载
├── poc_registry.py         # POC 加载
├── knowledge_registry.py   # 知识库加载
├── server.py               # SSE 实时流服务（三页 + 监控 API）
├── config.py               # env 配置 + 模型
├── prompts/                # 任务模板（tsec_task.txt）
├── roles/                  # 9 个角色定义
├── skills/                 # 62 个技能（含子目录）
├── tools/                  # 92 个 CLI 工具 YAML
├── vulns/                  # 9 个漏洞检测模块
├── pocs/                   # 3 个 POC
├── knowledge/              # 3 个知识条目
├── payloads/               # 10 个 payload 字典
├── static/                 # 前端三页（index/monitor/agents）
├── docs/                   # 文档
├── data/                   # 运行时数据（agent.db + worker_generic/ 下含 worker_{code}/ 题级独立工作区）
└── .env                    # 凭证配置
```

---

## 16. 关键设计决策与教训

### 16.1 已落地的关键决策

1. **角色/阶段正交分离**：流程阶段（recon→post）合并进 phase 状态机，角色只保留题型风格，避免"阶段角色永不触发"（报告 P0-5）。
2. **单 Executor + 阶段状态机**：替代多 Agent 分治，减少上下文切换成本。
3. **Planner + replan 可修正闭环**：深度分析产计划，停滞 5 轮重新规划（上限 3 次）。
4. **信息增量信号统一判停/replan**：从"看阶段切换"升级为"看产出质量"。
5. **提交铁律代码机械化**：flag 扫描 + 机械提交，不靠 LLM 自觉（报告 P0-1）。
6. **调度器零 LLM 编排**：选题/换题/看 hint/容器 SOP 全代码（报告 P0-2/P0-4）。
7. **平台异常上抛**：TaskEnded/TaskNotFound 标记 `ctx.fatal` 让主循环终止（报告 P0-2）。
8. **成本治理归拢单一事实源**：爆破/hint 预算、换脑、挂起收拢到 `budget.py`，避免规则多处漂移。
9. **事件总线 + SQLite 落库**：hooks 只发射事件，`events.py` 分发、`db.py` 落库，与 events.jsonl 双写留痕，监控页可追溯历史。
10. **子任务上下文隔离**：`finish_subtask` 结构化结束协议，主 Agent 只拿 summary/findings/flag，不接触子任务海量输出。
11. **结构化记忆（黑板）**：`verified/evidence/supersedes` 三字段 + 落盘持久化，判死必须附证据、被取代指向旧 key。
12. **Prompt 注入防御**：工具输出统一检测注入特征，只检测不修改原文，命中追加安全提醒。
13. **任务模板外置**：跑分任务模板抽离到 `prompts/tsec_task.txt`，与编排代码解耦。

### 16.2 实战教训

1. **openai-agents 默认走 Responses API**，DeepSeek 不支持 → 改用 `OpenAIChatCompletionsModel`。
2. **DeepSeek 不支持 `response_format=json_schema` strict** → 结构化输出用 tool + 手动校验。
3. **`parallel_tool_calls=False`** 规避 DeepSeek 并行工具调用 JSON 不稳定。
4. **工具输出外置**（`_spill_output`）+ token 压缩，解决上下文膨胀。
5. **VPN 需要 CAP_NET_ADMIN**：openvpn 创建 tun0 需 root，`--daemon` 后台 fork 会误报成功 → 必须验证 tun0。
6. **default-soft**：工具失败是信息不是死路，判停别误杀正常侦察。
7. **"2000+ 步才正常"**：真实漏洞挖掘需要长战线，迭代上限宁宽勿严。

---

## 17. 能力覆盖（四类题型）

面向 CTF / 攻防比赛的四类题型覆盖：

| 题型 | 占比 | 覆盖情况 |
|---|---|---|
| Web 漏洞挖掘 | 67% | 23 个漏洞技能（含业务逻辑/竞态/越权）+ 大量工具，覆盖强 |
| 二进制漏洞挖掘 | 20% | pwn_exploitation/static_analysis 技能 + gdb/pwntools/angr/ROPgadget |
| AI 漏洞挖掘 | 7% | prompt_injection/tool_misuse 技能 + AI 安全测试员角色 |
| 区块链漏洞挖掘 | 6% | smart_contract_security 技能 + slither/mythril 工具 |

加权覆盖估算约 **85%**（Web 是基本盘，二进制/区块链是补齐后的增量）。

---

## 18. 内容资产

| 资产 | 数量 | 说明 |
|---|---|---|
| 角色 roles/ | 9 | Web审计、二进制协议、沙箱逃逸、免杀、边界渗透、AI安全、提权、横向、跑分 |
| 技能 skills/ | 62 | Web漏洞（23）+ 二进制 + AI + 区块链 + 侦察 + 框架 + 云 + 协议 |
| CLI 工具 tools/ | 92 | nmap/sqlmap/ffuf/gdb/pwntools/angr/slither/mythril 等 |
| 漏洞模块 vulns/ | 9 | SQLI/XSS/SSTI/LFI/RCE/IDOR/SSRF/XXE/UPLOAD |
| POC pocs/ | 3 | 精选有效 POC |
| 知识 knowledge/ | 3 | get_flag/post_exploit/waf_bypass |
| Payload payloads/ | 10 | sqli/lfi/path/xss/ssti/rce/idor/ssrf/upload/xxe |

---

*本文档随代码演进持续更新，是跨会话连续性的技术基座。*
