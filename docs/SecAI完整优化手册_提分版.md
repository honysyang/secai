# SecAI 完整优化手册（提分版）

> 适用对象：https://gitee.com/yzj1/secai.git @ commit 94caec6
> 目标：① 最大化发挥 AI 在安全/CTF 上的能力；② 系统提供稳定高效的平台——有证据必闭环、杜绝空泡、全程算经济账；③ 所有改动直接服务于得分。
> 编制日期：2026-08-14

---

## 第 0 章　总纲：AI 与系统的权力分界

整套系统只做一道判断题：**这个决定需要「理解证据」还是「执行规则」？**

- 需要理解证据的（假设生成、结果解读、方向选择、payload 构造）→ 全部交给 AI，并且给它最好的条件：充分的上下文、合适的角色、自由的思考空间（think/todo/子任务）。
- 执行规则的（提交 flag、通关判定、选题换题、看 hint、终止、预算、容器 SOP）→ 全部交给代码，零 LLM，机械保证。

本手册所有优化都落在这条线的两侧：**给 AI 侧松绑（释放能力），给代码侧加锁（堵住空泡与误判），中间用「证据闭环」和「经济账」两个传动轴连接。**

```
                    ┌─────────── 代码执法层（零 LLM）───────────┐
                    │ 铁律提交 │ EV 调度 │ 判停 │ 预算 │ 闭环触发 │
                    └──────┬───────────────────────┬───────────┘
                           │ 机械触发               │ 结构化证据
┌────────── AI 能力侧 ─────┴───────────────────────┴──────────┐
│  Manager(立法)  Planner(规划)  Executor(执行·唯一碰目标)      │
│  Coach(教练)    Reporter(报告)  Compactor(压缩)              │
└──────────────────────────────────────────────────────────────┘
```

---

## 第 1 章　智能体权力矩阵：谁能碰工具，谁只能动笔

### 1.1 权力分配总表

| 智能体 | 工具配置 | 轮次上限 | token 预算 | 一句话定位 |
|---|---|---|---|---|
| Manager | **零工具** | 1 | 1% | 立法者：只写宪章，不下车间 |
| Planner | **只读三件套**（find_skills / list_knowledge+get_knowledge / list_tools） | 4 | 3% | 规划者：先查武器库再定计划 |
| Executor | **ALL_TOOLS**（唯一可碰目标） | 不设硬顶，受预算档约束 | 93% | 战士：唯一产出证据的智能体 |
| Subtask Executor | ALL_TOOLS + finish_subtask | 8 | （计入 Executor） | 分身：独立会话，结构化回传 |
| Coach | **只读三件套** | 3 | 1% | 教练：查着技能库给方向 |
| Reporter | **零工具** | 1 | 1% | 翻译官：只翻译机械聚合好的数据 |
| Compactor | **零工具** | 1 | 1% | 书记员：只压缩文本 |

### 1.2 三条铁约束（写进代码，不靠自觉）

1. **能碰目标的工具（shell/fuzz/http_request/run_tool/distinguish…）只挂 Executor 系。** 任何其他智能体的 `Agent(...)` 定义里出现这些工具即为 bug。
2. **改变战役状态的工具（finalize/submit_flag/finish_subtask）只挂 Executor 系。** Reporter 若有 finalize 权，LLM 的乐观偏差会直接变成终止指令。
3. **分析型智能体（Planner/Coach）的只读工具不进黑板、不写状态、限轮。** 它们的检索只服务于当下产出，黑板是执行者的账本。

### 1.3 落地代码：只读三件套的定义与挂载

```python
# demo_tools.py —— 工具打标：在 _TOOL_SPECS 之外维护权力分级
READONLY_INTEL_TOOLS = {"find_skills", "list_knowledge", "get_knowledge", "list_tools"}
STATE_CHANGING_TOOLS = {"finalize", "finish_subtask"}   # submit_flag 走铁律，不直接挂
TARGET_TOUCHING_TOOLS = {"shell", "fuzz", "http_request", "run_tool",
                         "distinguish", "parallel_shell", "connect_vpn"}

def intel_tools():
    """分析型智能体的只读检索包。"""
    return [t for t in _BASE_TOOLS if t.name in READONLY_INTEL_TOOLS]
```

```python
# core/agents_def.py —— Planner/Coach 挂载
planner_agent = Agent(name="Planner", instructions=PLANNER_INSTRUCTIONS,
                      tools=intel_tools(),          # ★ 新增
                      model=MODEL, model_settings=PLANNER_SETTINGS)
coach_agent = Agent(name="Coach", instructions=COACH_INSTRUCTIONS,
                    tools=intel_tools(),            # ★ 新增
                    model=MODEL, model_settings=COACH_SETTINGS)
```

> 为什么必须给：Coach 的 prompt 现在写着「优先从已解锁技能和知识库里找方向」，但它没有任何检索工具——这条要求**物理上无法执行**，等于逼着它凭记忆瞎编。给了只读工具，建议质量才会落地。

---

## 第 2 章　智能体定义逐一体检与修复

### 2.1 Manager：删掉「终止判据」，宪章生成加校验

**问题 A：双头立法。** 宪章模板要求 LLM 写「终止判据」，而真正的判停在代码（三级证据）。两套判据并存时，Agent 会拿宪章的宽松表述当提前 finalize 的挡箭牌。

**修复**：宪章四节改三节，`MANAGER_INSTRUCTIONS` 中删除「# 终止判据」整节，末节改为：

```
# 成功判据 —— 怎么算成功，一句话说死（终止与判停由系统负责，与本宪章无关）
```

**问题 B：生成无校验。** 单次 LLM 生成，漏节/跑题无人发现，缺陷宪章注入所有题。

**修复**（`app/main.py` 立法段落后加机械校验）：

```python
_REQUIRED_CHARTER_SECTIONS = ("# 目标", "# 关键原则", "# 约束", "# 成功判据")
if not all(s in charter for s in _REQUIRED_CHARTER_SECTIONS):
    missing = [s for s in _REQUIRED_CHARTER_SECTIONS if s not in charter]
    log_warn(f"[charter] 宪章缺节 {missing}，重试一次")
    charter_result = await run_with_model_fallback(
        manager_agent,
        input=f"用户任务：{task}\n\n上次输出缺少章节：{missing}，请严格按四节重输出。",
        hooks=hooks, model_pool=global_model_pool, agent_name="Manager")
    charter = str(charter_result.final_output)
    # 再缺则用默认骨架补齐，绝不让缺节宪章下游传播
    for s in _REQUIRED_CHARTER_SECTIONS:
        if s not in charter:
            charter += f"\n{s}\n（系统默认：按任务书执行，证据驱动。）\n"
```

### 2.2 Planner：全局计划减肥，单题计划按需

**问题**：`global_plan` 是对 63 题全量描述的一次性规划，原样注入每道题的 system prompt——对单题 90% 是噪声，烧 token、稀释注意力。

**修复**：Planner 输出分两层，注入时只取方法论层。

```python
PLANNER_INSTRUCTIONS 末尾追加：
"""输出分两段，用 <!--SPLIT--> 分隔：
第一段「方法论」（≤500 字）：本类目标的通用作战原则，将被注入每一道题；
第二段「题目级要点」（不限长）：按 unique_code 前缀分组的题型要点，仅存档备用。"""

# app/main.py：全局注入只取第一段
global_plan = str(plan_result.final_output)
methodology, _, per_code = global_plan.partition("<!--SPLIT-->")
(DATA_DIR / "plan_full.md").write_text(global_plan, encoding="utf-8")  # 全文存档
global_plan = methodology.strip() or global_plan[:1500]               # 注入只用方法论
# 单题 brief 组装时，从 plan_full.md 按 code 前缀检索该题段落追加（仿 load_notes_for）
```

### 2.3 Executor：温度分层 + 系统提示动静分离（省钱大头）

**问题 A：动态 instructions 每轮重建 → 前缀缓存全失效。** 角色/纪律/宪章/计划/brief 是不变的，阶段/黑板摘要/档案是每轮变的，全拼在一个动态函数里，导致每轮 prompt 前缀都不同，LLM 网关的前缀缓存命中率为零——这是单轮 input token 居高不下的结构性根源。

**修复**（`core/agents_def.py`）：

```python
# 静态部分：build 时一次性生成，不再用动态函数
EXECUTOR_STATIC_TEMPLATE = """你是 SecAI 的执行者，角色：{role_name}。
# 角色思维风格
{role_style}
# 使命宪章
{charter}
# 作战方法论
{plan}
# 工作纪律
（8 条不变）
# 可用打法（初始披露）
{playbooks}
# 当前任务书
{brief}
"""
# 动态部分（阶段/黑板摘要/压缩摘要/档案）改为每轮 user 消息注入：
# app/main.py 主循环里
next_input = (f"[战况] 阶段={ctx.phase}｜flag={len(ctx.correct_flags)}/{ctx.total_flag_count}"
              f"｜黑板：{_format_blackboard(ctx.blackboard)[:600]}\n"
              f"继续攻击本题：调用工具产出新证据增量，或 finalize 提交结论。")
```

> 效果：system prompt 整段稳定，前缀缓存可命中 90%+ 的 input token，长会话成本降一个量级。技能渐进披露改由 hooks 在工具结果尾部追加「已解锁新打法：xxx」的消息通道，不再依赖 system prompt 重渲染。

**问题 B：所有角色共用 temperature=0.3。** e3 对抗制品工匠需要发散（0.5），web 审计需要严谨（0.2）。

**修复**：角色 frontmatter 加 `temperature` 字段，`build_executor` 读取：

```python
temp = float(role.get("temperature") or 0.3)
settings = ModelSettings(temperature=temp, max_tokens=4096, parallel_tool_calls=False)
```

### 2.4 Coach / Compactor / 子任务：灾备接线补全

**问题**：`_coach`、`_replan`、`compact_session` 内裸 `Runner.run` 且绑定主模型——主模型挂掉时，自救链路在最需要它的时刻断掉。

**修复**：三处统一换 `run_with_model_fallback(..., model_pool=global_model_pool, agent_name=...)`（该包装函数已在 main.py 存在，直接复用）。子任务的 `Runner.run` 同样替换；子任务灾备失败降级为「[子任务异常]」的现状可保留。

### 2.5 Reporter：两阶段聚合，杜绝「偏尾战报」

**问题**：Reporter 只读 events.jsonl 尾部 6000 字符 + results——63 题战役的战报只覆盖最后几题，前面 60 题的死路经验全丢。

**修复**：报告分两阶段，LLM 只见聚合结果。

```python
# app/main.py 报告段落，替换原输入构造
def _aggregate_campaign(results, client) -> str:
    """零 LLM 机械聚合：逐题战果 + 逐题档案段落（field_notes 已有按题结构）。"""
    lines = ["# 全战役逐题结果"]
    for r in results:
        lines.append(f"- {r['code']}: {r['outcome']}")
    notes = FIELD_NOTES_FILE.read_text(encoding="utf-8") if FIELD_NOTES_FILE.exists() else ""
    lines.append("\n# 逐题死路与战果（机械沉淀原文）")
    lines.append(notes[-8000:])   # field_notes 已是按题分段结构，尾部 8K 覆盖近期全量
    return "\n".join(lines)

report = await run_with_model_fallback(
    reporter_agent,
    input=("任务执行结束。以下是系统机械聚合的全量逐题数据，"
           "你的唯一工作是把它们蒸馏成战报与死路清单：\n\n" + _aggregate_campaign(results, client)),
    hooks=hooks, model_pool=global_model_pool, agent_name="Reporter")
```

### 2.6 阶段机：post 开回路

**问题**：`PHASE_TRANSITIONS` 里 `post: []` 是死胡同。「读到 flag 但权限不足」「读到 config.php 要拿凭据继续利用」都需要 post→exploit 回退。

**修复**（`runtime/status.py`）：

```python
PHASE_TRANSITIONS = {
    "recon":     ["enumerate", "post"],
    "enumerate": ["detect", "recon"],
    "detect":    ["exploit", "enumerate", "post"],
    "exploit":   ["post", "detect"],
    "post":      ["exploit"],          # ★ 拿到部分战果但需要更深利用时允许回退
}
```

---

## 第 3 章　角色体系优化

### 3.1 每个角色加「## 禁止」负面清单

正面描述告诉 AI 做什么，负面清单才能防跑偏。统一格式为「定位 1 句 / 打法 ≤5 条 / 禁止 2–3 条 / 输出要求 1 条」，正文压到 500 字符内。示例（web_auditor.md 改写）：

```markdown
---
name: Web 应用审计员
pattern: "^[abd]-"
playbooks: file_read_oob, unknown_target_sop
tool_groups: web
temperature: 0.2
---

## 定位
Web 应用漏洞审计员，从功能点反推代码路径。

## 打法
- 功能点反推：下载→路径遍历、模板→SSTI、上传→校验绕过。
- 路径遍历阶梯 → 绝对路径 → file:// → php://filter（PHP）。
- SSTI 用 {{7*7}} / ${7*7} 探测引擎与过滤点。
- 源码泄露优先读配置与凭据：.git/config、/proc/self/cwd、备份文件。

## 禁止
- 未完成路径遍历阶梯前，禁止上 SQL 注入/爆破。
- 禁止扫描与 flag 无关的后台路由与通用目录字典。

## 输出
读到 flag 或可深入利用的凭据/源码，证据优先。
```

各角色禁止事项建议：

| 角色 | 禁止事项 |
|---|---|
| 边界渗透工程师 | 禁止盲换 payload 超过 3 次不做二分定位；禁止对非拦截层（如 404）做绕过尝试 |
| 沙箱逃逸专家 | 禁止在确认解释器/过滤表前跑通用 exp；禁止破坏性 payload（rm/fork 炸弹） |
| 对抗制品工匠 | 禁止牺牲制品功能换免杀（双轨评分功能分优先）；禁止未经本地验证直接提交制品 |
| 二进制协议分析师 | 禁止未做完协议字段测绘就构造溢出；禁止忽略内存布局直接打 payload |
| 漏洞综合利用专家 | 禁止重新侦察已确认的攻击面；禁止无回显时不试外带就放弃 |
| AI 安全测试员 | 禁止对非 AI 接口做提示注入；禁止编造模型回复当作证据 |

### 3.2 playbook 引用启动自检

```python
# arsenal/registries/role_registry.py，load_roles() 末尾加：
def validate_roles(roles, known_skills):
    for r in roles:
        for p in r["playbooks"]:
            if p not in known_skills:
                raise ValueError(f"角色「{r['role']}」引用了不存在的技能：{p}")
# main.py 启动时：validate_roles(load_roles(), set(load_skills()))
# 拼写错误在启动第一秒就炸出来，而不是在赛场上静默裸奔。
```

### 3.3 工具组显式声明，废弃关键词猜测

`_tool_groups_for` 目前用 `"二进制" in role_name` 判型——角色改名即错配。改为读 frontmatter：

```python
# role_registry._parse_frontmatter 已支持任意字段，直接加：
r["tool_groups"] = [x.strip() for x in meta.get("tool_groups", "").split(",") if x.strip()]
# main.py：
groups = tuple(role.get("tool_groups") or ["web"]) + ("platform", "vpn", "seccli")
ctx.enabled_tools = build_default_tools(groups=groups)
```

每个角色 md 的 frontmatter 补 `tool_groups`（二进制/协议题不填 web，其余填 web）。

### 3.4 阶段型角色改为「证据触发的增强包」（本章最重要）

提权专员、漏洞综合利用专家、横向移动专员描述的是**攻击链阶段**，不是题型。一题一派终身不变的机制下它们永远等不到自己的舞台（`^e2` 锚定先截胡）。改造为 trigger 触发：

```markdown
---
name: 提权专员
trigger: rce_confirmed, foothold      # ★ 证据触发，不参与前缀派任
---
（正文不变：sudo -l / SUID / 内核 / 容器逃逸打法）
```

```python
# core/hooks.py 闭环检测置位 rce_confirmed 之后追加：
for r in load_roles():
    trig = r.get("trigger", "")
    if trig and any(t in bb_keys_or_flags for t in trig.split(",")):
        if r["role"] not in task_ctx.boosted_roles:
            task_ctx.boosted_roles.append(r["role"])
            task_ctx.role_boost = r["style"]   # 下一轮 instructions 追加
# agents_def 动态部分末尾：
#   # 阶段增强（证据触发）
#   {role_boost}
```

角色随证据生长，而不是开局锁死——这才是「AI 能力最大化」的正确打开方式：同一场战役里，执行者先后「成为」侦察兵、审计员、提权专员。

### 3.5 补齐缺口角色：交互协议/闯关专员

题型图谱里 SSH 闯关（交互式多跳登录）无角色覆盖，会落 fallback。新增 `arsenal/roles/interactive_protocol.md`：

```markdown
---
name: 交互协议专员
pattern: "ssh|telnet|闯关|多跳|jump|bastion|堡垒机"
playbooks: protocols, unknown_target_sop
tool_groups: 
temperature: 0.2
---

## 定位
交互式协议闯关专家（SSH/Telnet/自定义 TCP 菜单），处理「非请求-响应」的目标。

## 打法
- 先用脚本化交互（python3 pexpect / socket 状态机），禁止手工逐条敲。
- 每跳记录：凭据来源、提示符形态、可用命令集，写黑板。
- 菜单型协议先枚举全部选项再逐个验证，注意隐藏选项（越界编号/空输入）。
- 凭据通常藏在上一跳的输出、home 目录、history 里。

## 禁止
- 禁止在无状态机脚本的情况下进入第二跳以后。
- 禁止忽略上一跳输出中的凭据线索直接暴力猜。

## 输出
每跳的状态迁移记录 + 最终 flag。
```

### 3.6 关键词角色的误派收紧

- `ai_security_tester` 的 `prompt|llm` 会劫持描述含 "prompt" 的普通 Web 题 → 收紧为 `prompt inject|提示注入|llm|大模型`；
- 关键词角色（云/区块链/横向/多阶段/提权）按文件名字母序先匹配先赢 → `load_roles()` 改为「锚定角色优先、关键词角色按 pattern 长度降序」（特异性高的先匹配）；
- `tscbench.md`（1324 字节，全局角色）单题永不使用，建议删除或折叠进 fallback。

---

## 第 4 章　证据闭环：有证据必须闭环，闭环必须精准

### 4.1 修复现有闭环正则的误报（`core/hooks.py`）

| 闭环分支 | 现状问题 | 修复 |
|---|---|---|
| RCE 确认 | `cmdi_match` 含裸 `root`——`/root` 路径、passwd 每行都命中 | 要求 `uid=\d+` 或命令回显上下文（`whoami` 紧邻输出）才触发 |
| SSRF 端点 | 条件含 `"200" in low`，弱信号误报面大 | 删除 `"200"`，保留 `flag{`/admin/secret 等强信号；内网 IP 必须带路径 |
| 上传成功 | 正则要求扩展名在固定列表，漏 `.phtml/.jspx` 等 | 扩展名列表补 `phtml|phar|jspx|war|cer|asa` |
| 业务逻辑 | `price|amount` 太常见，任意订单页都触发 | 要求同时出现两个以上业务字段才触发 |

### 4.2 闭环指令的「一次有效」原则

闭环消息每类只发一次（`_already` 已做到），但应再加一条：**Agent 执行闭环指令后若无结果（3 轮零增量），该闭环标记为 `closure_stale`，允许证据升级后二次触发**——防止「第一次闭环指引失败后永久沉默」。

### 4.3 新增两条高价值闭环

```python
# 凭据泄露闭环：输出含 用户:口令 或密钥 → 强制立即试登录/复用
cred_match = re.search(r"((?:password|passwd|pwd|secret|token|apikey|api_key)"
                       r"[\s:=\"']+([\w@.\-/]{4,64}))", text, re.I)
if cred_match and not _already("cred_leak", cred_match.group(2)):
    _set("cred_leak", cred_match.group(2))
    return ("已发现凭据/密钥泄露。请立即：\n"
            "1. 用该凭据尝试所有已知登录口（Web/SSH/数据库/内网服务）；\n"
            "2. 若是 token/apikey，放入 Authorization/Cookie 重放已鉴权接口；\n"
            "3. 登录成功后直奔 flag 常见位置（第 post 阶段清单）。")

# 源码/配置读取成功闭环：拿到 config → 强制提取连接串与硬编码密钥
if ("config.php" in low or "web.config" in low or ".env" in low) and \
   any(k in low for k in ("db_pass", "dbpass", "database", "secret_key", "mysql")):
    if not _already("config_read", "true"):
        _set("config_read", "true", text[:200])
        return ("已读取到配置/源码。请立即：\n"
                "1. 提取数据库连接串/硬编码密钥/内部 URL；\n"
                "2. 用提取的凭据连库查 flag 表，或访问内部服务；\n"
                "3. 将该配置全文 read_artifact 存证，供后续利用链引用。")
```

---

## 第 5 章　杜绝空泡：AI 自由的边界由代码画

### 5.1 think/todo 不得喂养空转看门狗（必修）

**问题**：`hooks.py on_tool_start` 对一切工具 `turn_tool_count += 1`——think/todo_list 这类无副作用工具也算，「连续 6 轮无工具调用→机械换题」防线被合法绕过。

**修复**：

```python
_NO_PROGRESS_TOOLS = {"think", "todo_add", "todo_list", "todo_mark", "checkpoint"}

async def on_tool_start(self, context, agent, tool):
    self._emit("tool", agent=agent.name, tool=tool.name, status="executing")
    task_ctx = getattr(context, "context", None)
    if task_ctx is not None and tool.name not in _NO_PROGRESS_TOOLS:
        task_ctx.turn_tool_count += 1
# blackboard 按 action 区分：get/list 不计，set 计
```

同时给 think 加**频次软上限**（纪律第 1 条补充）：「think 每个决策点最多 1 次；连续两轮 think 而无工具动作视为空转」。

### 5.2 隐藏工具组必须被引导发现

`enable_tool/list_disabled_tools` 已挂载，但全部提示词无一处提及——Agent 不知道有 poc/vuln/knowledge 组，门控省了 token 却把能力藏没了。

**修复**：`TOOL_USAGE_HINT` 追加一段：

```
4. 需要 CVE 检索 / POC 库 / 漏洞知识库 / 差分实验时，先 list_disabled_tools 查看
   未挂载的工具组，再用 enable_tool 挂载对应组（poc/vuln/knowledge/web）后使用。
```

### 5.3 子任务防空泡三件套

1. 子任务的 `Runner.run` 换 `run_with_model_fallback`（模型失败不再整组哑火）；
2. `_run_subtasks` 的 hooks/session 落 `challenge_workdir / f"sub_{id}"`（轨迹与父题同目录，可观测）；
3. `spawn_subtask` 工具描述加预算提醒：「子任务共享本题 token 预算，声明前先用 think 确认分支互不依赖且各自有明确成功标准」。

### 5.4 残留容器误判出口（P0，上轮已报，并入本册）

```python
# app/main.py 主循环「if not active」分支替换：
if not active:
    unfinished = [c for c in challenges if not c.get("is_completed")]
    if not unfinished:
        log_info("== 全部题目已完成 ==")
        break
    # 还有题但没槽位：说明名额被占（可能上次残留），先清理再重试
    slot_wait = getattr(run_task, "_slot_wait", 0) + 1
    if slot_wait == 1:
        log_warn("[slot] 无可用槽位，批量清理非 running 残留容器")
        for c in unfinished:
            if c.get("container_status") in ("available", "stopped", ""):
                try: client.close_challenge(c.get("unique_code"))
                except Exception: pass
    if slot_wait >= 10:
        fatal_reason = "连续 10 轮拿不到容器名额（疑似平台侧残留/泄漏）"
        break
    await asyncio.sleep(5)
    continue
```

---

## 第 6 章　经济账：每一分 token、每一秒钟、每一次 hint 都要记账

### 6.1 token 经济：三笔已算的账 + 两笔新账

已落地：爆破闸门上 20 次、成本两档（switch/suspend）、hint 预算比例 0.35。新增：

**账 1：前缀缓存账（见 2.3）**——动静分离后长会话 input 成本可降 50%+，这是全手册省钱最大的一项。

**账 2：档案/计划注入税。** 每题注入的 field_notes、plan、sol_hint 都有字符预算，但叠加后仍可能占 3–5K token/轮。设总税上限：动态注入块（黑板摘要+档案+sol_hint+战况行）合计 ≤ 1500 token，超出按「黑板 > sol_hint > 档案」优先级截断。

**账 3：think 税。** think 返回固定 16 字节但携带的 thought 本身计入 input。频次软上限（5.1）即其预算闸门，不再单独立账。

### 6.2 时间经济：EV 加耗时因子（S1 落地）

```python
# adapters/db.py 新增
CREATE TABLE IF NOT EXISTS challenge_duration(
    prefix TEXT PRIMARY KEY,          -- 题前缀（e1/e2/a/b/...）
    attempts INTEGER, solved INTEGER,
    avg_seconds REAL                  -- 已解同类题平均耗时
);
# task_finished 时更新：avg = (avg*solved + 本次耗时) / (solved+1)

# bench_platform/scheduler.py select_challenge 改造
ev = (score * coef * (base ** attempts.get(code, 0))) / max(est_minutes, 5)
# est_minutes：db 查 prefix 的 avg_seconds/60；无数据按难度默认 easy=10/medium=25/hard=45
```

hard 500 分题若历史平均 40 分钟，EV=500×0.7/40=8.75；easy 100 分题 8 分钟，EV=100×1.3/8=16.25——难题不再被系统性高估。

### 6.3 hint 经济账升级（S5 落地）

```python
def hint_worth_it(total_score: int, hint_penalty: int, tokens_used: int,
                  tokens_budget: int, difficulty: str) -> bool:
    """hint 扣分 vs 继续空烧 token 的机会成本。"""
    if hint_penalty >= total_score * 0.5:
        return False                       # 扣分过半的 hint 不值（除非残局）
    burn_ratio = tokens_used / max(tokens_budget, 1)
    return burn_ratio >= 0.25              # 已烧 1/4 预算仍无进展，hint 比继续烧便宜
# 残局（endgame）时 penalty 阈值放宽到 0.7：有分总比没分强
```

### 6.4 残局收割与回捞的账

已落地的 `is_endgame + ENDGAME_DECAY=0.6` 解决「回捞资格」；还需补「回捞预算」：endgame 阶段每题 skip 阈值减半（easy 6 轮、medium 10 轮、hard 12 轮）——残局时间贵，死磕性价比最低。

---

## 第 7 章　稳定性：异常分类与退出语义

### 7.1 异常三分法（全系统统一）

| 类别 | 代表 | 处置 |
|---|---|---|
| 平台终止类 | TaskEnded / TaskNotFound | 置 `ctx.fatal`，全局停止，**绝不重试** |
| 模型服务类 | 401/403/404/429/5xx/超时/额度关键词 | ModelPool 切换，永久失败拉黑、暂时失败冷却重试 |
| 环境抖动类 | list_challenges 偶发失败 / close 失败 / VPN 断 | 有限重试（列表 10 次、close 3 次、网络不可达 2 轮换题） |

修补一处缺口：`is_model_failure` 补 `400 + (model|quota|额度|不存在|not exist|invalid)` 关键词识别（百度网关常把模型错误包装成 400）。

### 7.2 退出语义表（打印的每个退出原因必须唯一对应一种真实结局）

| 退出文案 | 允许的真实原因 |
|---|---|
| `全部题目已完成` | list_challenges 无未完成题（唯一！） |
| `平台终止` | TaskEnded / TaskNotFound |
| `deadline 到达` | 硬时限 |
| `list_challenges 连续失败` | 网络层 10 连失败 |
| `拿不到容器名额` | 槽位等待超 10 轮（5.4 新增） |
| `已中断` | KeyboardInterrupt |

「无可选题目」与「拿不到名额」两种结局不得共用文案——这是上轮误报「全部完成」的根因。

---

## 第 8 章　实施路线与验收清单

### 8.1 实施顺序（按得分性价比排序）

| 批次 | 内容 | 章节 | 预计耗时 |
|---|---|---|---|
| **第一批（赛前必上）** | 残留容器误判出口（5.4）、think 看门狗（5.1）、post 回路（2.6）、宪章去终止判据（2.1A）、工具组发现引导（5.2） | 5 / 2 | 半天 |
| **第二批** | 动静分离前缀缓存（2.3A）、EV 耗时因子（6.2）、闭环正则修复+新闭环（4）、Coach/Compactor 灾备接线（2.4） | 2 / 4 / 6 | 一天 |
| **第三批** | 角色负面清单+压缩（3.1）、playbook 自检（3.2）、tool_groups 显式化（3.3）、SSH 角色（3.5）、Reporter 两阶段（2.5）、Planner 分层（2.2） | 2 / 3 | 一天 |
| **第四批（机制升级）** | 阶段增强包 trigger 机制（3.4）、hint 经济账（6.3）、残局预算（6.4）、角色温度分层（2.3B）、子任务三件套（5.3） | 3 / 5 / 6 | 一天 |

### 8.2 验收清单（每批做完跑一遍）

- [ ] 启动自检：13+1 个角色的 playbook 全部存在，无静默缺失
- [ ] 构造测试：残留 3 个活跃容器启动系统，输出必须是「拿不到容器名额」而非「全部完成」
- [ ] 构造测试：连续 8 轮只调 think，必须被空转看门狗换题
- [ ] 构造测试：子任务拿到 flag，铁律自动提交且主 ctx.correct_flags 同步
- [ ] 构造测试：多 flag 题拿 1 面后 LLM 调 finalize，必须被平台复核拒绝
- [ ] 观测：单轮 input token 较优化前下降 ≥40%（动静分离效果）
- [ ] 观测：e2 题拿到立足点后，下一轮 instructions 出现提权专员增强段
- [ ] 观测：endgame 阶段回捞题的 skip 阈值已减半
- [ ] 全程：Reporter 战报覆盖全部题目（不只尾部）

---

## 附：本手册与既有文档的关系

- 《SecAI最新仓库诊断报告v3》：问题清单（P0-1/P0-2 已修，其余并入本册 5.4 / 7.1）
- 《SecAI提分实战策略实施手册》（S1–S8）：S1→6.2，S5→6.3，S6→is_endgame（已落地）+6.4，S2/S3/S7/S8 待后续版本
- 五公理不变：提交机械保证 / 调度纯函数 / LLM 只做假设与解读 / 状态全外置 / 观测是状态投影
