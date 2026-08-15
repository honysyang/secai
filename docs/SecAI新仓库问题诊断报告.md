# SecAI 新仓库问题诊断报告

> 审查对象：https://gitee.com/yzj1/secai.git（commit ce2df0d「SecAI 多智能体安全攻防框架」）
> 代码规模：19 个 Python 文件 3065 行 + 9 个角色 / 40+ 技能 / 90 个安全 CLI YAML / 8 个漏洞卡 / 5 个 POC
> 审查基准：《SecAI三智能体架构与流程定稿》五条公理 + TSec Benchmark 任务书约束 + 七轮实战日志尸检教训
> 审查日期：2026-08-14

---

## 〇、总评

**地基很好，支柱缺失。**

做对了的：platform_client 的 SDK 语义分类（invalid_state 两义/duplicate 幂等/VPN 预检）、多 Skills 渐进披露、全局黑板、checkpoint 断点续跑、RunHooks 事件流、动态 instructions、工具输出外置 artifacts、90 个本地安全工具 YAML 注册表——这些都是对的方向，工程完成度不低。

但把它对准 **TSec 比赛**这个目标（有闸门、有上限、有死期）看，比赛的四根支柱——**机械提交、平台语义、调度并发、时限感知**——被当作"靶场层私事"留空了。定稿的分工恰恰是：这四根支柱必须是不经 LLM 的代码。

**根因一句话**：通用多智能体 demo 的壳，直接套在了比赛这个目标上。壳里的好东西都在，但壳与比赛之间缺了四根承重柱。

---

## 一、P0 致命问题（直接丢分或失控）

### P0-1 提交铁律被拆除 ★最严重

**证据**：
- `demo_tools.py:5` 注释："提交铁律 / flag 扫描 / submit_flag 属于 CTF 靶场层，后续单独接入，不进通用流程。"
- `main.py:384` 默认任务就是整本 TSec 任务书（`build_default_task()`）。

**问题**：跑分完全靠 prompt 恳求 LLM 自觉调用 `submit_flag`——任务书里加粗的"禁止调用 finalize 停下"（main.py:110）就是哀求。旧日志铁证：LLM 管提交产出过 `duplicate:true, submitted:false` 的假登记。**公理①（提交是机械保证，不是 LLM 决策）被违反。**

**雪上加霜**：`demo_tools.py:36` 的 `_spill_output` 把超 800 字符的工具输出截断外置到 artifacts——**flag 若出现在第 801 字符之后，LLM 看不见、铁律又不存在，这面 flag 永远埋在文件里无人提交。**

**修复**：
1. shell / http_request / fuzz 工具返回前，代码机械扫描**完整输出（含被 spill 的全文）**，见 `flag{...}` 立即调 `PlatformClient.submit_flag`，回执拼在返回文本尾部（"[系统·提交铁律] flag{...} → correct=true"）。
2. LLM 只读回执：correct=false 继续分析；correct=true 查是否还有下一面。

---

### P0-2 平台错误语义在 tool 层被糊掉

**证据**：
- `platform_client.py:51-58`：invalid_state 两义分流（max active→ContainerBusy / 否则→TaskEnded）——分类正确。
- `platform_tools.py:28`：`_err(e)` 把**所有异常**包成 `{"error": str(e)}` 文本返回给 LLM。

**问题**：client 层的精确分类在 tool 层全部报废。任务结束后 LLM 能否停下，取决于它读不读得懂一段报错文本——**这是已修复的"任务结束无限空转"bug 的复发形态**。同理，3 容器上限（close 再试）、duplicate 跳过、503 重试，全部退化为 LLM 自觉。

**修复**：
1. platform_tools 不吞异常：TaskEnded / TaskNotFound 原样上抛，主循环接住 → 写停止标记、close 全部容器、终止全流程。
2. ContainerBusy 在 tool 内直接处置（close 最旧一题 → 轮询 stopped → 重试 start），LLM 只看到"容器已就绪"。
3. duplicate 在 client 层已是幂等返回，保持。

---

### P0-3 判停器对比赛是摆设

**证据**：`stop_policy.py:15-17`

```python
MAX_TURNS = 0        # 0 = 不限
CHAR_BUDGET = 0      # 0 = 不限
EMPTY_TURN_LIMIT = 5 # 唯一有效判停
```

**问题**：
1. 比赛有**硬总时限**，系统里没有任何 deadline 概念（无 start_ts、无剩余时间感知、无"比赛结束前 N 分钟收尾"策略）；
2. 空转检测形同虚设——LLM 每轮调一个无害工具（如 `blackboard get`）即可永久续命，空转成本为零；
3. 唯一真判停是 finalize——即"LLM 自己决定何时停"，又回到旧架构 CONCLUDE 早退的病。

**修复**（三级终止证据，按定稿第七节）：
- L1 权威判决：平台 is_completed / TaskEnded 异常；
- L2 证据枯竭：连续 N 轮零**信息增量**（新端口/新路径/新报错/响应差异——由 hooks 在 tool_result 里检测，不是"有没有调工具"）；
- L3 资源时钟：`TASK_DEADLINE_TS` 环境变量（比赛结束时间）+ 单题超时 + 剩余时间不足开新题时禁止开新题。

---

### P0-4 没有调度层：3 槽并发废掉、选题无 EV

**问题**：
1. 平台给 3 个容器并发，当前一个执行者串行打 63 题——**吞吐直接砍到 1/3**；
2. 选题靠任务书一句"由易到难"让 LLM 自己排——没有死路降权（`0.3^attempts`），会反复撞同一道硬题（a-05 六次进攻的结构性原因）；
3. start/close 全靠 LLM 自觉——忘 close → max active → 链路 2 的糊化文本 → 死循环风险；
4. hint 无纪律：看与不看全凭 LLM 权衡（任务书提示扣分），没有第 8 轮机械前置。

**修复**：补回调度器（纯函数，零 LLM）：
```
EV = total_score × 难度系数 × 0.3^死路次数
容器 SOP：start → ContainerBusy → close 最旧 → 轮询 stopped → 重试
一题一个执行上下文（子任务机制可复用，但按"题"而不是按"探测分支"划分）
```

---

### P0-5 角色派任粒度错误：六种角色永不触发

**证据**：
- `main.py:256`：`assign_role(role_hint, task)`——role_hint 是 CLI 第二参数（通常为空），task 是**整本任务书**。
- `roles/tscbench.md` pattern：`TSec|Benchmark|跑分|BENCHMARK_TOKEN|tsecbench`——整本任务书必命中。

**问题**：派任发生在**战役级**（63 题混合），整场只派一个"TSecBench 跑分专员"，playbooks 只有 `unknown_target_sop` 一篇。**e1/e2/e3/f1/[abd] 五个前缀角色在跑分模式下一次都不会被选中**——打 f1 二进制题时执行者拿的是通用 SOP 而不是 tcp_binary。角色库建了九种，实战只用一种。

**修复**：角色派任下沉到**题级**——每 start 一题，用该题的 unique_code 前缀 + description 派任，注入该题专属 playbook。调度器（P0-4）落地后这是自然结果。

---

## 二、P1 架构性问题

### P1-6 field_notes 退化为"最近 3000 字符"

**证据**：`main.py:157` `_load_field_notes` 读整个文件**尾部**。

**问题**：打 a-05 时注入的可能是昨天某道无关题的战报尾巴，文不对题；文件只增不删，跨战役污染。双沉淀设计的"按题检索死路档案"丢了。

**修复**：field_notes 按 `# {code}` 分节，注入时按当前题 code + 同前缀题检索；单节超 900 字符截断。

### P1-7 渐进披露只进不出 + 动态 instructions 每轮全量重渲染

**证据**：`task_context.py:17` disclosed_skills 只 append；`agents_def.py:138` 每轮重新 format 整个模板。

**问题**：40+ 篇技能全文（`load_skill_bodies` 无预算）+ 宪章 + 计划 + 黑板 + 任务书，system prompt 单调膨胀。`COMPACT_TOKEN_THRESHOLD=30000` 只管 session 历史，**管不住 system prompt 这一半**——后期每轮 input 数万 token，又贵又慢。

**修复**：
1. 披露设上限（如同屏最多 6 篇，新披露挤掉最久未命中）；
2. 每篇 playbook 注入设字符预算（如 1200 字，超出取决策树核心节）；
3. 黑板摘要已有 40 字符截断（好），保持。

### P1-8 max_turns=1 拿异常当控制流

**证据**：`main.py:296-301`。

**问题**：每个 LLM 回合一次 `Runner.run(max_turns=1)`，模型调工具必抛 `MaxTurnsExceeded` → `result=None`。若最后一轮走异常路径，`final_output` 丢失（main.py:349 只能靠 final_payload 兜底）。异常路径是 SDK 的报错通道，不是流程通道。

**修复**：一次 `Runner.run(max_turns=N)` 跑完整子任务，判停/压缩改用 hooks 内计数 + 工具侧中断（如需中途停，raise 专用异常）。

### P1-9 压缩裁剪有消息配对风险

**证据**：`context_manager.py:147-148` `clear_session()` 后重放 recent。

**问题**：一旦切点落在 assistant tool_calls 与其 tool 响应之间，OpenAI 协议直接 400，整个 session 报废。`_is_boundary` 只防了一半。**血的教训换来的原则是"只截内容、绝不动结构"**。

**修复**：放弃删历史，改为"保留全部配对结构，仅把旧 tool 消息内容截断为 400 字 + '[截断，全文见 artifacts]'"。

### P1-10 replan 阈值太晚、成本太高

**证据**：`main.py:161` `REPLAN_STUCK_TURNS=15`；`_replan` 每次喂 4000 字符事件流 + 2000 黑板 + 任务书 + 宪章。

**问题**：先空转 15 轮铺路费，再付一次大调用诊疗费。

**修复**：停滞判定改用"信息增量"（同 P0-3 L2），连续 5 轮零增量即触发；replan 输入只喂黑板摘要 + 最近 5 轮事件。

---

## 三、P2 工程问题

| # | 位置 | 问题 | 修复 |
|---|---|---|---|
| 11 | `platform_tools.py:19` | `_client()` 每次新建、env 现读、无复用 | 模块级单例 |
| 12 | `.env.example` | `TOOLS_DIR=/home/kali/SECAI/tools` 硬编码绝对路径 + 大小写不符（secai） | 默认 `Path(__file__).parent / "tools"` |
| 13 | `demo_tools.py:289` | VPN 预检是 LLM 工具，LLM 可跳过 | 入口代码机械预检，不过即中断（跑分模式） |
| 14 | `demo_tools.py:30` | `ARTIFACT_SPILL_THRESHOLD=800` 偏低，合法探测频繁外置，多读一轮 | 提到 3000，且 spill 前必须先扫 flag（同 P0-1） |
| 15 | `main.py:236` | `base_chars` 用文件大小做续跑预算基准，换机器/编码即失真 | 基准写进 state.json |
| 16 | `main.py:183` | subtask session db `sub_{id}.sqlite` 不清理 | 子任务结束后删除 |
| 17 | `sec_tools.py` / `tools/*.yaml` | 90 个 CLI 工具是否安装无探测，run_tool 报 not found 才暴露 | 启动时 `shutil.which` 普查，可用清单注入宪章 |

---

## 四、与定稿五条公理的对照表

| 公理 | 现状 | 判决 |
|---|---|---|
| ① 提交是机械保证 | 靠 prompt 恳求 LLM 自觉提交 | ❌ 违反（P0-1） |
| ② 调度是纯函数零 LLM | 无调度器，LLM 自选题、自管容器 | ❌ 违反（P0-4） |
| ③ LLM 只做假设与解读 | LLM 还管提交/选题/容器/终止/VPN | ❌ 违反（P0-1/2/3/4） |
| ④ 状态外置、重启无感 | checkpoint + session + field_notes 落盘 | ✅ 基本做到（P2-15 小瑕疵） |
| ⑤ 观测是状态的投影 | RunHooks 事件流 + status.json | ✅ 做到，且做得较好 |

---

## 五、修补优先级与工作量估算

| 优先级 | 事项 | 对应问题 | 工作量 |
|---|---|---|---|
| ① | 铁律回填：工具层扫 flag 机械提交 + spill 前全文扫描 | P0-1 | ~40 行 |
| ② | platform_tools 异常上抛 + 主循环接住 TaskEnded | P0-2 | ~50 行 |
| ③ | 角色派任下沉到题级 | P0-5 | ~30 行（依赖⑤） |
| ④ | 判停器加 deadline + 信息增量信号 | P0-3 / P1-10 | ~80 行 |
| ⑤ | 调度器：3 槽并发 + EV 选题 + 容器 SOP | P0-4 | ~150 行 |
| ⑥ | field_notes 按题检索 | P1-6 | ~30 行 |
| ⑦ | 披露预算 + 压缩改为截内容不删结构 | P1-7 / P1-9 | ~60 行 |
| ⑧ | max_turns=1 改为整跑 + hooks 计数 | P1-8 | ~40 行 |

①②④ 是"止血"（不修的每一分钟都在丢分），⑤③ 是"增产"（吞吐 ×3），⑥⑦⑧ 是"降本"。

---

## 六、保留并发扬光大的部分（明确不拆）

- `platform_client.py` 全文——SDK 语义分类正确，原样保留；
- RunHooks 事件流 + token 统计——监控的事实源；
- 渐进披露的**思想**——只需加预算和淘汰；
- 90 个安全 CLI YAML 注册表——武器库，加 `shutil.which` 普查即可；
- 角色库九种皮肤——派任下沉到题级后立即全部激活；
- checkpoint/SQLiteSession 断点续跑——公理④的实现，保留。

---

*结论：这不是一份要推翻重来的代码，是一份"通用层优秀、比赛层缺席"的代码。补上四根支柱（机械提交、平台语义、调度并发、时限感知），它就是定稿的完整实现。*
