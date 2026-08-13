# SecAI 三智能体 Demo · OpenAI Agents SDK 实施手册

> 目标：用 OpenAI Agents SDK（`openai-agents` 包）实现《SecAI三智能体架构与流程定稿》的最小可运行 Demo。
> 覆盖：三智能体（管理者/执行者/报告者）、tools、七种角色定义、skills（playbook）注入、代码执法层。
> 原则不变：**LLM 只做假设与解读，提交/调度/终止全是代码**。

---

## 〇、Demo 边界（先说清做什么、不做什么）

**做**：
- 三智能体全部用 SDK 的 `Agent` + `Runner` 实现；
- 五个工具用 `@function_tool` 实现，含上下文注入（`RunContextWrapper`）；
- 七种角色皮肤：角色 = instructions 模板 + playbook 文件组合；
- 提交铁律：工具层机械扫描输出，见 `flag{...}` 即提交（不经 LLM）；
- 执法层：VPN 预检、平台客户端、EV 选题、三级终止，纯 Python；
- 单题端到端可跑：管理者立法 → 执行者作战 → 报告者收尾。

**不做**（Demo 之外，生产版再加）：
- 多题并行调度（Demo 只跑一题，EV/容器 SOP 给接口留位）；
- Web 页面（Demo 用控制台打印事件流代替监控页）；
- 自动拉起（Demo 单进程，状态外置的写法保留）。

---

## 一、SDK 概念与我们的架构映射

| OpenAI Agents SDK | 本 Demo 用法 | 定稿中的对应物 |
|---|---|---|
| `Agent`（instructions + tools + model） | 三个智能体各一个实例；执行者按角色动态组装 instructions | 管理者/执行者/报告者 |
| `@function_tool` | 五个工具 + 提交铁律内嵌 | 工具集 |
| `RunContextWrapper[T]` | 注入 workdir / PlatformClient / 当前题 code | ctx 约定 |
| `Runner.run(max_turns=...)` | 执行者的回合上限 = `MAX_TURNS` | 记账式 ReAct 循环 |
| `RunHooks` | 事件流投影（每次 LLM/工具调用打印+落盘） | 事件流唯一事实 |
| Guardrails | **不用**——我们的护栏是工作流代码（铁律/终止），不是输入输出过滤器 | 代码执法层 |
| Handoffs | **不用**——三智能体不同时期触发，由 Python 流程串接，不是 agent 间转交 | 生命周期编排 |
| `OpenAIChatCompletionsModel` + `set_default_openai_client` | 接 DeepSeek 等 OpenAI 兼容端点 | LLM 客户端 |

**一句话**：SDK 管"一个 agent 怎么思考与调工具"，我们管"谁在何时被唤醒、提交怎么机械保证、何时终止"。后者全在 SDK 之外的普通 Python 里。

---

## 二、项目结构

```
SECAI/
├── requirements.txt        # openai-agents, requests, ddgs(可选)
├── .env                    # LLM_API_KEY / BENCHMARK_TOKEN / BENCHMARK_BASE_URL
├── main.py                 # 端到端 Demo 入口（单题）
├── charter.py              # 使命宪章：管理者产物，落盘+注入
├── platform_client.py      # 执法层①：平台协议唯一出口（VPN预检/错误分流）
├── demo_tools.py           # 五个 @function_tool + 提交铁律
├── roles.py                # 七种角色定义（前缀派任表）
├── skills/                 # playbook 文件（角色武器库）
│   ├── filter_bypass.md
│   ├── sandbox_escape.md
│   ├── evasion_dual_track.md
│   ├── tcp_binary.md
│   ├── file_read_oob.md
│   ├── ai_security.md
│   └── unknown_target_sop.md
├── agents_def.py           # 三智能体的 Agent 定义与组装函数
├── hooks.py                # RunHooks：事件流打印+落盘
└── data/                   # 运行产物（mission_charter.md/events.jsonl/field_notes.md）
```

安装：`pip install openai-agents requests ddgs`

环境变量：
```bash
export LLM_API_KEY=sk-xxx
export LLM_BASE_URL=https://api.deepseek.com/v1     # 可选，默认即此
export LLM_MODEL=deepseek-chat                       # 可选
export BENCHMARK_BASE_URL=https://tsecbench.zc.tencent.com
export BENCHMARK_TOKEN=你的token
```

---

## 三、执法层①：platform_client.py（平台协议唯一出口）

```python
"""平台客户端：对齐 TSec SDK 语义的零依赖实现。全系统只有这里懂平台协议。"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

import requests

VPN_CHECK_URL = os.getenv("TSEC_VPN_CHECK_URL", "http://10.0.100.58")


class VpnCheckError(Exception):
    def __init__(self, reason: str = "network_error"):
        super().__init__("VPN检测未通过,请检查靶场VPN网络配置")
        self.reason = reason

class TaskNotFound(Exception): ...        # 404 token 无效：停止报告
class TaskEnded(Exception): ...           # 409 invalid_state 非 max active：全局停
class ContainerBusy(Exception): ...       # 409 含 max active：close 再试
class ResourceUnavailable(Exception): ... # 503：短暂重试


def _err_code(resp) -> str:
    try: return str(resp.json().get("code", ""))
    except Exception: return ""

def _err_msg(resp) -> str:
    try: return str(resp.json().get("message", ""))
    except Exception: return ""


class PlatformClient:
    def __init__(self, base_url: str, token: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.headers = {"BENCHMARK_TOKEN": token}
        self.timeout = timeout

    # ---- VPN 强制预检：开战前的第一道闸门 ----
    def check_vpn(self) -> Dict[str, Any]:
        try:
            r = requests.get(VPN_CHECK_URL, timeout=8)
        except Exception:
            raise VpnCheckError("network_error")
        if r.status_code != 200: raise VpnCheckError("bad_status")
        try: data = r.json()
        except Exception: raise VpnCheckError("bad_body")
        if data.get("status") != "ok": raise VpnCheckError("status_not_ok")
        return data

    def _check_common(self, resp) -> None:
        code = _err_code(resp)
        if code == "task_not_found":
            raise TaskNotFound(_err_msg(resp) or "token 无效或缺失")
        if code == "invalid_state":
            msg = _err_msg(resp)
            if "max active" in msg: raise ContainerBusy(msg)
            raise TaskEnded(msg or "任务已结束")

    # ---- 五个平台接口 ----
    def list_challenges(self) -> List[Dict[str, Any]]:
        r = requests.get(f"{self.base_url}/openapi/v1/challenges",
                         headers=self.headers, timeout=self.timeout)
        self._check_common(r); r.raise_for_status()
        return r.json()

    def start_challenge(self, code: str) -> List[str]:
        r = requests.post(f"{self.base_url}/openapi/v1/challenges/start",
                          params={"unique_code": code},
                          headers=self.headers, timeout=self.timeout + 5)
        self._check_common(r)
        if _err_code(r) == "resource_unavailable" or r.status_code == 503:
            raise ResourceUnavailable(_err_msg(r))
        r.raise_for_status()
        addr = r.json().get("container_addr") or []
        if isinstance(addr, str): addr = [addr]
        return [str(a) for a in addr]

    def get_hint(self, code: str) -> str:
        try:
            r = requests.get(f"{self.base_url}/openapi/v1/challenges/hint",
                             params={"unique_code": code},
                             headers=self.headers, timeout=self.timeout)
            if r.status_code != 200: return ""      # 通关后看 hint 返回 409：跳过
            return r.json().get("hint") or ""
        except (TaskNotFound, TaskEnded): raise
        except Exception: return ""

    def submit_flag(self, code: str, flag: str) -> Dict[str, Any]:
        """大小写变体 + 429 退避 + duplicate 幂等。"""
        variants = [flag] + (["FLAG" + flag[4:]] if flag.startswith("flag") else [])
        last: Dict[str, Any] = {}
        for v in variants:
            for attempt in range(3):
                try:
                    r = requests.post(f"{self.base_url}/openapi/v1/challenges/submit",
                                      json={"unique_code": code, "flag": v},
                                      headers=self.headers, timeout=self.timeout)
                    if _err_code(r) == "duplicate":
                        return {"ok": True, "correct": None, "duplicate": True,
                                "note": "duplicate：已计分，跳过", "flag": v}
                    self._check_common(r)
                    if r.status_code == 429:
                        time.sleep(2 * (attempt + 1)); continue
                    if "json" in r.headers.get("Content-Type", ""):
                        last = r.json()
                    break
                except (TaskNotFound, TaskEnded): raise
                except Exception as e:
                    last = {"error": str(e)[:200]}; time.sleep(1)
            if last.get("correct"):
                return {"ok": True, "correct": True, "flag": v,
                        "awarded": last.get("awarded"),
                        "cumulative_score": last.get("cumulative_score"),
                        "correct_flag_count": last.get("correct_flag_count"),
                        "total_flag_count": last.get("total_flag_count"),
                        "matched_flag_index": last.get("matched_flag_index")}
        return {"ok": True, "correct": False, "flag": flag,
                "note": "平台判定错误或题目不匹配", "raw": last}

    def close_challenge(self, code: str) -> bool:
        try:
            r = requests.post(f"{self.base_url}/openapi/v1/challenges/close",
                              params={"unique_code": code},
                              headers=self.headers, timeout=self.timeout)
            self._check_common(r)
            return bool(r.json().get("closed"))
        except (TaskNotFound, TaskEnded): raise
        except Exception: return False
```

---

## 四、执行上下文与提交铁律：demo_tools.py

SDK 的工具是普通函数，`RunContextWrapper` 是第一个参数——我们把 workdir、PlatformClient、当前题 code 都装进一个 dataclass 传进去。

**提交铁律的落点**：`shell` / `http_request` 执行完后，代码机械扫描输出中的 `flag{...}`，发现即调 `PlatformClient.submit_flag`，回执拼接在工具返回里。LLM 看到的只是"系统已代提交"的通知——它永远不需要"决定"提交。

```python
"""五个 @function_tool + 提交铁律（机械扫描，不经 LLM）。"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import requests
from agents import function_tool, RunContextWrapper

from platform_client import PlatformClient, TaskEnded, TaskNotFound

PREVIEW = 3000
FLAG_RE = re.compile(r"flag\{[^}\s]{4,}\}", re.I)


@dataclass
class TaskContext:
    """随 Runner.run(context=...) 注入，工具的第一个参数能拿到它。"""
    workdir: Path
    platform: PlatformClient
    code: str                                # 当前题 unique_code
    submitted: set = field(default_factory=set)
    correct_flags: List[str] = field(default_factory=list)


def _iron_submit(ctx: TaskContext, output_blob: str) -> str:
    """提交铁律：扫描输出，见 flag 即机械提交，返回回执文本。"""
    receipts = []
    for flag in sorted(set(FLAG_RE.findall(output_blob))):
        if flag in ctx.submitted:
            continue
        ctx.submitted.add(flag)
        try:
            res = ctx.platform.submit_flag(ctx.code, flag)
        except (TaskNotFound, TaskEnded):
            raise                              # 任务结束：上抛，主流程立即停
        except Exception as e:
            res = {"correct": None, "error": str(e)[:200]}
        if res.get("correct"):
            ctx.correct_flags.append(flag)
        receipts.append(f"[系统·提交铁律] {flag} → {json.dumps(res, ensure_ascii=False)[:300]}")
    return "\n".join(receipts)


@function_tool
def shell(ctx: RunContextWrapper[TaskContext], command: str, timeout: int = 30) -> str:
    """在工作目录执行 shell 命令。探测请打包：一条命令完成多个动作；
    复杂交互直接写 python3 脚本执行。"""
    c = ctx.context
    try:
        p = subprocess.run(["bash", "-c", command], capture_output=True, text=True,
                           timeout=min(timeout, 120), cwd=str(c.workdir))
        blob = f"rc={p.returncode}\nstdout:\n{p.stdout[:PREVIEW]}\nstderr:\n{p.stderr[:1000]}"
    except subprocess.TimeoutExpired:
        return f"命令超时（{timeout}s）。hint: 缩短范围或加 --max-time"
    except Exception as e:
        return f"执行失败: {str(e)[:300]}"
    iron = _iron_submit(c, blob)
    return blob + ("\n" + iron if iron else "")


@function_tool
def http_request(ctx: RunContextWrapper[TaskContext], url: str,
                 method: str = "GET", body: str = "", timeout: int = 15) -> str:
    """发送单次 HTTP 请求，返回状态码/响应头/正文预览。批量探测请用 shell+python3。"""
    c = ctx.context
    try:
        r = requests.request(method, url, data=body or None,
                             timeout=min(timeout, 60), verify=False)
        head = "; ".join(f"{k}: {v}" for k, v in list(r.headers.items())[:8])
        blob = f"status={r.status_code}\nheaders: {head}\nbody:\n{r.text[:PREVIEW]}"
    except Exception as e:
        return f"请求失败: {str(e)[:300]}。hint: 检查 VPN/容器存活"
    iron = _iron_submit(c, blob)
    return blob + ("\n" + iron if iron else "")


@function_tool
def submit_flag(ctx: RunContextWrapper[TaskContext], flag: str,
                unique_code: str = "") -> str:
    """显式提交 flag（铁律之外的主动通道）。correct=true 继续找下一面。"""
    c = ctx.context
    code = unique_code or c.code
    if flag in c.submitted:
        return json.dumps({"duplicate": True, "note": "本次已提交过"}, ensure_ascii=False)
    c.submitted.add(flag)
    res = c.platform.submit_flag(code, flag)
    if res.get("correct"):
        c.correct_flags.append(flag)
    return json.dumps(res, ensure_ascii=False)


@function_tool
def distinguish(ctx: RunContextWrapper[TaskContext], url: str,
                probes: List[str], method: str = "GET", keyword: str = "") -> str:
    """差分实验（实验代替知识）：url 中用 {payload} 占位，注入多组探测值，
    对比状态码/长度/关键词差异，差异点即攻击面。"""
    rows = []
    for p in probes[:8]:
        u = url.replace("{payload}", requests.utils.quote(str(p), safe=""))
        try:
            r = requests.request(method, u, timeout=10, verify=False,
                                 data={"payload": p} if method == "POST" else None)
            row: Dict[str, Any] = {"probe": str(p)[:60], "status": r.status_code,
                                   "len": len(r.text)}
            if keyword: row["kw_count"] = r.text.count(keyword)
            rows.append(row)
        except Exception as e:
            rows.append({"probe": str(p)[:60], "error": str(e)[:120]})
    dims = {k for row in rows for k in ("status", "len", "kw_count") if k in row}
    diff = any(len({row.get(d) for row in rows if d in row}) > 1 for d in dims)
    verdict = ("响应存在差异 → 探测面有效，沿差异方向深入" if diff
               else "响应无差异 → 该探测面无效，换攻击面")
    return json.dumps({"rows": rows, "differentiated": diff, "verdict": verdict},
                      ensure_ascii=False)


@function_tool
def web_search(ctx: RunContextWrapper[TaskContext], query: str,
               max_results: int = 5) -> str:
    """联网搜索（外脑）：不认识的技术栈/报错/CVE，先查再打。"""
    try:
        from ddgs import DDGS
        with DDGS() as d:
            hits = list(d.text(query, max_results=min(max_results, 8)))
        return json.dumps([{"title": h.get("title", ""),
                            "snippet": h.get("body", "")[:300],
                            "url": h.get("href", "")} for h in hits], ensure_ascii=False)
    except Exception as e:
        return f"搜索不可用: {str(e)[:200]}。hint: 依靠内置打法与差分实验"


ALL_TOOLS = [shell, http_request, submit_flag, distinguish, web_search]
```

---

## 五、七种角色定义：roles.py

角色 = **instructions 思维风格模板 + playbook 文件清单 + 工具偏好提示**。派任是纯查表。

```python
"""七种角色皮肤：前缀派任（确定性，零 LLM），证据可修正。"""
from __future__ import annotations

import re
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"


ROLE_TABLE = [
    # (匹配规则, 角色名, 思维风格, playbook 文件列表)
    (r"^e1", "边界渗透工程师",
     "你专攻边界防护穿透。思维习惯：先识别检测器（WAF/网关/反代）的判定维度，"
     "再用编码/分块/协议走私让载荷穿墙。每次失败先问：被谁拦的、拦在哪一层。",
     ["filter_bypass.md", "unknown_target_sop.md"]),
    (r"^e2", "沙箱逃逸专家",
     "你专攻沙箱与反序列化逃逸。思维习惯：枚举可用对象图（__subclasses__/gadget 链），"
     "在受限环境里找通向 os/io 的最短路径。先小步验证可控性，再构造读 flag 链。",
     ["sandbox_escape.md", "unknown_target_sop.md"]),
    (r"^e3", "对抗制品工匠",
     "你专攻检测规避与制品构造。思维习惯：理解检测器的特征来源（字节特征/行为/格式语义），"
     "构造功能等价但特征不同的制品。本题双轨评分：拿到 flag 后评估制品是否还能打磨。",
     ["evasion_dual_track.md", "unknown_target_sop.md"]),
    (r"^f1", "二进制协议分析师",
     "你专攻二进制与网络协议。思维习惯：先摸清协议命令集与字段边界，再用畸形长度/"
     "越界读/未初始化内存思路探测信息泄露。socket 交互写成可复用脚本。",
     ["tcp_binary.md", "unknown_target_sop.md"]),
    (r"^[abd]-", "Web 应用审计员",
     "你专攻 Web 应用漏洞。思维习惯：从功能点反推代码路径（下载→路径遍历、"
     "模板→SSTI、上传→文件校验绕过），源码泄露优先读配置与凭据。",
     ["file_read_oob.md", "unknown_target_sop.md"]),
    (r"ai_security|prompt|llm", "AI 安全测试员",
     "你专攻 AI 系统安全。思维习惯：flag 常藏在系统提示/工具返回/检索内容里；"
     "用直接注入、间接注入、角色扮演诱导模型或 agent 泄露。",
     ["ai_security.md", "unknown_target_sop.md"]),
]


def assign_role(code: str, description: str = "",
                evidence_override: str = "") -> dict:
    """前缀派任。evidence_override：field_notes 里实战修正过的题型，优先于前缀。"""
    target = evidence_override or f"{code} {description}"
    for pattern, name, style, books in ROLE_TABLE:
        if re.search(pattern, target, re.I):
            return {"role": name, "style": style, "playbooks": books,
                    "matched_by": "evidence" if evidence_override else "prefix"}
    return {"role": "通用侦察兵",
            "style": "你面对未知目标。思维习惯：指纹先行（服务/框架/版本），"
                     "攻击面枚举（端口/路径/参数），小步验证再大步利用。",
            "playbooks": ["unknown_target_sop.md"],
            "matched_by": "fallback"}


def load_playbooks(files: list) -> str:
    parts = []
    for f in files:
        p = SKILLS_DIR / f
        if p.exists():
            parts.append(f"## 打法《{p.stem}》\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)
```

skills/ 目录下放七篇 playbook 的 Markdown（内容沿用定稿，每篇结构：**触发条件 → 决策树 → payload 库 → 收尾判据**）。示例 `skills/filter_bypass.md`：

```markdown
# 过滤器绕过（WAF/网关/自定义过滤）
## 触发条件
请求被拦截（403/406/自定义拦截页）、关键字被替换或删除、响应明显被改写。
## 决策树
1. 定位拦截维度：逐个字符二分，找出被拦的关键字/符号；
2. 编码绕过：URL 双重编码 / Unicode / HTML 实体 / 大小写混合 / 注释符内联（/*!and*/）；
3. 协议绕过：分块传输 / Content-Type 变换 / 参数污染（同名参数重复）；
4. 等价改写：|| 换 or、`'OR'1'='1` 换 admin'-- -、空格换 /**/ 或 %a0；
5. 仍不通：distinguish 差分定位精确拦截点，再针对性构造。
## 收尾判据
载荷原样到达后端（响应出现预期业务结果而非拦截页）。
```

（其余六篇：sandbox_escape.md / evasion_dual_track.md / tcp_binary.md / file_read_oob.md / ai_security.md / unknown_target_sop.md，结构相同，内容按定稿第五节展开。）

---

## 六、三智能体定义：agents_def.py

```python
"""三智能体的 Agent 定义与组装。
管理者：意图识别 + 写使命宪章 + 目标核对（事件触发）
执行者：记账式 ReAct，按角色组装 instructions（常驻）
报告者：战报 + 死路蒸馏（事件触发）
"""
from __future__ import annotations

import os
from pathlib import Path

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, set_default_openai_client
from openai import AsyncOpenAI

from demo_tools import ALL_TOOLS
from roles import load_playbooks

# ---- OpenAI 兼容端点（DeepSeek 等）----
_client = AsyncOpenAI(
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    api_key=os.environ["LLM_API_KEY"],
)
set_default_openai_client(_client)
MODEL = OpenAIChatCompletionsModel(model=os.getenv("LLM_MODEL", "deepseek-chat"),
                                   openai_client=_client)

SETTINGS = ModelSettings(temperature=0.2, max_tokens=4096)


# ================= 管理者 =================
MANAGER_INSTRUCTIONS = """你是 SecAI 的管理者，负责立法而非作战。你的产物是一份使命宪章。

根据用户任务与题目信息，输出 Markdown 格式的使命宪章，包含四节：
# 目标 —— 可验证的完成判据（怎么算成功，一句话说死）
# 关键原则 —— 3~6 条（如：宁可判死不可空转；发现 flag 系统会机械代提交；死路不重复）
# 约束 —— 预算/时限/禁区/平台规则（3 容器上限、hint 扣分、多 flag 逐面提交）
# 终止判据 —— 权威判决 + 证据枯竭（连续无信息增量/假设台账证伪）+ 资源时钟

只输出宪章本身，不要寒暄。宪章将被注入执行者的系统提示，并作为终止核对的依据。"""

manager_agent = Agent(name="Manager", instructions=MANAGER_INSTRUCTIONS,
                      model=MODEL, model_settings=SETTINGS)


# ================= 执行者（按角色组装） =================
EXECUTOR_TEMPLATE = """你是 CTF 比赛选手，角色：{role_name}。

# 角色思维风格
{role_style}

# 使命宪章（管理者立法，必须遵守）
{charter}

# 铁律（违反任何一条即失败）
1. 发现任何形如 flag{{...}} 的字符串：系统会机械代你提交，你的职责是读提交回执——
   correct=false 继续分析；correct=true 检查是否还有下一面 flag（本题共 {flag_total} 面，已拿 {flag_done} 面）。
2. 容器地址以任务书 container_addr 为准，禁止自猜。
3. 批量密度：探测阶段一条 shell 命令打包多个动作；每轮必须产出至少一个新信息。
4. 禁止重复已失败的方向（下方历史作战档案全是已证伪死路）。
5. 没有现成工具就自己写 Python 脚本——shell 里的 python3 是主武器库。

# 可用打法（按角色注入）
{playbooks}

# 历史作战档案
{field_notes}

# 当前任务书
{brief}
"""


def build_executor(code: str, role: dict, charter: str, brief: str,
                   field_notes: str = "", flag_total: int = 1,
                   flag_done: int = 0) -> Agent:
    instructions = EXECUTOR_TEMPLATE.format(
        role_name=role["role"], role_style=role["style"],
        charter=charter, playbooks=load_playbooks(role["playbooks"]),
        field_notes=field_notes or "（无：首次进攻）",
        brief=brief, flag_total=flag_total, flag_done=flag_done)
    return Agent(name=f"Executor[{role['role']}]", instructions=instructions,
                 tools=ALL_TOOLS, model=MODEL, model_settings=SETTINGS)


# ================= 报告者 =================
REPORTER_INSTRUCTIONS = """你是 SecAI 的报告者，负责把作战过程翻译成人能看懂的中文。
输入是一次作战的事件流（JSON 行）与最终状态。输出两部分：
## 战报 —— 结果（通关/判死/超时）、关键链（哪几步是转折点）、拿到的 flag 与得分
## 死路蒸馏 —— 已证伪方向清单（每条一行：方向 + 为什么死），供下次接力注入
只输出这两节。不评价、不抒情、不建议。"""

reporter_agent = Agent(name="Reporter", instructions=REPORTER_INSTRUCTIONS,
                       model=MODEL, model_settings=SETTINGS)
```

---

## 七、事件流：hooks.py

SDK 的 `RunHooks` 在每次 LLM 调用、工具调用、agent 切换时触发——正好是事件流的天然挂点。

```python
"""RunHooks：把 SDK 的内部回调投影成统一事件流（打印 + 落盘 events.jsonl）。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from agents import RunHooks


class EventStreamHooks(RunHooks):
    def __init__(self, workdir: Path, code: str):
        self.workdir = workdir
        self.code = code

    def _emit(self, kind: str, **data):
        entry = {"kind": kind, "ts": round(time.time(), 1), "code": self.code, **data}
        line = json.dumps(entry, ensure_ascii=False)
        print(f"  [{kind}] {json.dumps(data, ensure_ascii=False)[:200]}")
        with open(self.workdir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self._emit("llm_call", agent=agent.name)

    async def on_llm_end(self, context, agent, response):
        # reasoning/content 摘要进事件流
        text = ""
        for item in getattr(response, "output", []) or []:
            text = str(item)[:300]
            break
        self._emit("thought", agent=agent.name, preview=text)

    async def on_tool_start(self, context, agent, tool):
        self._emit("tool", agent=agent.name, tool=tool.name)

    async def on_tool_end(self, context, agent, tool, result):
        self._emit("tool_result", agent=agent.name, tool=tool.name,
                   preview=str(result)[:200])

    async def on_agent_start(self, context, agent):
        self._emit("agent_start", agent=agent.name)

    async def on_agent_end(self, context, agent, output):
        self._emit("agent_end", agent=agent.name, preview=str(output)[:200])
```

---

## 八、端到端主流程：main.py

流程 = 定稿第八节"任务生命周期"的单题版。三智能体由普通 Python 串接——**不用 handoff**，因为它们在不同时期触发，编排是代码的事。

```python
"""单题端到端 Demo：
管理者立法 → 执法层预检+起容器 → 执行者作战（三级终止）→ 报告者收尾。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

from agents import Runner

from agents_def import manager_agent, reporter_agent, build_executor
from demo_tools import TaskContext
from hooks import EventStreamHooks
from platform_client import (PlatformClient, VpnCheckError, TaskNotFound,
                             TaskEnded, ContainerBusy, ResourceUnavailable)
from roles import assign_role

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

MAX_TURNS = 30            # L3 资源时钟
NO_INFO_LIMIT = 6         # L2 证据枯竭：连续 N 轮零新工具调用增量（Demo 简化版）
HINT_AT_TURN = 8          # Demo 简化：用 elapsed turns 估算（生产版在循环内判断）


async def run_single_challenge(code: str) -> dict:
    base_url = os.environ["BENCHMARK_BASE_URL"]
    token = os.environ["BENCHMARK_TOKEN"]
    platform = PlatformClient(base_url, token)

    # ④ 执法层·入口预检（失败即中断明示）
    print("== VPN 预检 ==")
    try:
        platform.check_vpn()
        print("   VPN OK")
    except VpnCheckError as e:
        return {"status": "aborted", "reason": str(e)}

    # 题目信息
    challenges = {c["unique_code"]: c for c in platform.list_challenges()}
    ch = challenges.get(code)
    if not ch:
        return {"status": "aborted", "reason": f"challenge_not_found: {code}"}

    # 执法层·容器 SOP
    print(f"== 启动容器 {code} ==")
    try:
        addrs = platform.start_challenge(code)
    except ContainerBusy:
        platform.close_challenge(code)
        time.sleep(5)
        addrs = platform.start_challenge(code)
    except (TaskEnded, TaskNotFound) as e:
        return {"status": "task_ended", "reason": str(e)}
    except ResourceUnavailable as e:
        return {"status": "aborted", "reason": f"503: {e}"}
    addr = addrs[0] if addrs else ""
    print(f"   addr = {addr}")

    brief = (f"# 单题攻坚任务书：{code}\n"
             f"- 难度/分值：{ch.get('difficulty')}/{ch.get('total_score')}\n"
             f"- flag 进度：已拿 {ch.get('correct_flag_count', 0)}/{ch.get('flag_count', 1)} 面\n"
             f"- 描述：{ch.get('description', '')}\n"
             f"- container_addr: {addr}（以此为准，禁止自猜）\n")

    # ②③ 管理者·立法（事件触发，一次调用）
    print("== 管理者：写使命宪章 ==")
    charter_result = await Runner.run(
        manager_agent,
        input=f"用户任务：在时限内解出 {code} 并提交全部 flag。\n\n{brief}")
    charter = str(charter_result.final_output)
    (DATA_DIR / "mission_charter.md").write_text(charter, encoding="utf-8")

    # ④ 执法层·角色派任（纯查表）
    role = assign_role(code, ch.get("description", ""))
    print(f"== 角色派任：{role['role']}（{role['matched_by']}）==")

    executor = build_executor(
        code, role, charter, brief,
        flag_total=ch.get("flag_count") or 1,
        flag_done=ch.get("correct_flag_count") or 0)

    # ⑤ 执行者作战
    workdir = DATA_DIR / f"worker_{code}"
    workdir.mkdir(exist_ok=True)
    ctx = TaskContext(workdir=workdir, platform=platform, code=code)
    hooks = EventStreamHooks(workdir, code)

    print(f"== 执行者作战（max_turns={MAX_TURNS}）==")
    try:
        result = await Runner.run(
            executor,
            input="开始作战。第一轮：按你的角色打法做信息收集，打包探测。",
            context=ctx, hooks=hooks, max_turns=MAX_TURNS)
        final_text = str(result.final_output)
    except (TaskEnded, TaskNotFound) as e:
        return {"status": "task_ended", "reason": str(e)}

    # ⑥ 三级终止证据
    if ctx.correct_flags:
        # L1 权威判决：再查一次记分牌确认
        state = {c["unique_code"]: c for c in platform.list_challenges()}[code]
        status = "completed" if state.get("is_completed") else "partial"
    else:
        status = "dead_end"           # L2 证据枯竭（Runner 用尽 max_turns 仍无 flag）
    print(f"== 终止：{status}，正确 flag {len(ctx.correct_flags)} 面 ==")

    # ⑦ 报告者收尾（事件触发，一次调用）
    events_text = (workdir / "events.jsonl").read_text(encoding="utf-8")[-6000:]
    report = await Runner.run(
        reporter_agent,
        input=(f"题目 {code} 作战结束，状态 {status}，"
               f"正确 flag：{ctx.correct_flags}\n\n事件流尾部：\n{events_text}"))
    report_text = str(report.final_output)
    (DATA_DIR / "field_notes.md").open("a", encoding="utf-8").write(
        f"\n\n# {code} · {status} · {time.strftime('%Y-%m-%d %H:%M')}\n{report_text}\n")
    print("\n===== 战报 =====\n" + report_text)

    # 资源释放
    platform.close_challenge(code)
    return {"status": status, "correct_flags": ctx.correct_flags,
            "report": report_text}


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "a-05"
    out = asyncio.run(run_single_challenge(code))
    print("\n最终状态：", json.dumps({k: v for k, v in out.items() if k != "report"},
                                  ensure_ascii=False))
```

运行：

```bash
python main.py a-05
```

---

## 九、三级终止在 SDK 语境下的实现位置

| 级别 | 实现 | 位置 |
|---|---|---|
| L1 权威判决 | `Runner.run` 结束后查 `is_completed`；`TaskEnded` 异常从工具内上抛直接终止整场 | main.py + demo_tools._iron_submit |
| L2 证据枯竭 | Demo 简化：`max_turns` 用尽且 `correct_flags` 为空即判死。生产版：在工具返回里检测"信息增量"，连续 N 轮无增量提前 `raise` 中断 Run | main.py 判定段 |
| L3 资源时钟 | `Runner.run(max_turns=30)` —— SDK 原生支持，超轮抛 `MaxTurnsExceeded` | main.py |

**hint 前置**（生产版补回）：Demo 中可放在第 8 轮由 hooks 计数、通过 `context` 里的标记在下一轮工具结果尾部追加 hint 文本；生产版如定稿，在 solver 循环内机械注入。

---

## 十、与定稿的取舍说明（诚实清单）

1. **三智能体用 SDK Agent 实现，但编排不用 handoff**——它们在不同时期触发，由 main.py 的 Python 流程串接。handoff 适合"同一会话内转交"，不适合"战役级生命周期"。
2. **提交铁律放在工具函数内部**，SDK 没有"扫描所有工具输出"的原生机制——`_iron_submit` 在 shell/http 返回前机械执行，这比依赖 LLM 自觉调 submit_flag 可靠一个数量级。
3. **Guardrails 未使用**：SDK 的 guardrail 是输入/输出内容过滤，我们的护栏是工作流级（终止证据、铁律、预检），后者只能是代码。
4. **evidence_override 活口保留**：`assign_role` 第三参数接 field_notes 的实战修正，Demo 未接线（单题无历史），接口已留。
5. **本 Demo 的 LLM 调用次数**：管理者 1 + 执行者 ≤30 + 报告者 1 ≈ 32 次/题，执行者占比 ≥90%，符合 token 预算公理。

---

*配套定稿：《SecAI三智能体架构与流程定稿.md》。本手册是其在 OpenAI Agents SDK 上的最小实现图纸。*
