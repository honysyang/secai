"""RunHooks：把 SDK 的内部回调投影成统一事件流（打印 + 落盘 events.jsonl），
并承担「多 Skills 渐进披露」的运行时触发：扫描工具输出，命中触发词就追加技能到 context。

注意：function_tool 的 on_tool_start/on_tool_end 里，context 是 ToolContext（继承 RunContextWrapper），
context.context 才是我们的 TaskContext。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from agents import RunHooks

from events import BUS
from skill_registry import detect_skill_triggers


def _output_text(response) -> str:
    """从 Response.output 提取完整可读文本（思考/回复/工具调用），不截断。

    兼容 dataclass 与 dict 两种 item 形态；message 取 output_text，
    function_call 取 name(arguments)，reasoning 取 summary。
    """
    parts = []
    for item in getattr(response, "output", []) or []:
        get = (lambda k, d=None: item.get(k, d)) if isinstance(item, dict) \
            else (lambda k, d=None: getattr(item, k, d))
        t = get("type", "")
        if t == "message":
            content = get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    ct = c.get("type", "")
                    if ct in ("output_text", "input_text", "text"):
                        parts.append(str(c.get("text") or c.get("content") or ""))
        elif t == "function_call":
            parts.append(f"调用工具 {get('name', '')}({get('arguments', '')})")
        elif t == "reasoning":
            summary = get("summary", "")
            if isinstance(summary, list):
                for s in summary:
                    if isinstance(s, dict):
                        parts.append(str(s.get("text", "")))
            else:
                parts.append(str(summary))
    return "\n".join(p for p in parts if p).strip()


def _auto_advance_phase(task_ctx, text: str) -> bool:
    """根据工具输出证据自动推进阶段（代码兜底，防止 Agent 忘记 set_phase）。

    规则（保守，避免误切）：
      - 出现 flag 线索（flag{ 或 correct=true / findFlag=true）→ 直接切 post；
      - 漏洞被确认（vuln=true / vulnerable=true / fuzz differentiated=true）→ 切 exploit。
    """
    if task_ctx is None:
        return False
    low = text.lower()
    phase = task_ctx.phase
    if ("flag{" in text or '"correct": true' in low or '"correct":true' in low
            or '"findflag": true' in low or '"findflag":true' in low):
        if phase != "post":
            task_ctx.phase = "post"
            return True
    elif phase in ("recon", "enumerate", "detect"):
        if ('"vuln": true' in low or '"vuln":"true"' in low
                or '"vulnerable": true' in low or '"differentiated": true' in low):
            task_ctx.phase = "exploit"
            return True
    return False


_HTTP_STATUS_RE = re.compile(r"\b(?:200|201|204|301|302|307|308|401|403|405|500)\b")
_PORT_OPEN_RE = re.compile(r"\b\d{1,5}/(?:tcp|udp)\s+open\b", re.IGNORECASE)


def _score_tool_result(tool: str, text: str) -> int:
    """把工具输出量化为「信息增量」：+1 正向新认知 / 0 中性（纯规则，零 LLM）。

    对齐 sec-agent-v2 的 default-soft 原则：工具失败/网络错误不是「死路」而是「信息」，
    一律判 0 交给 LLM 自行决策（换工具/改参数），只识别明确的「新认知」作为正向增量，
    避免把正常侦察（目录枚举发现路径、端口扫描发现开放端口）误判成死路导致误停。
    正向 = flag/漏洞确认/登录/响应差异/新路径入口（HTTP 状态码）/开放端口。
    """
    low = text.lower()
    # 明确的关键证据（flag/提交成功/漏洞确认/登录/响应差异）
    if any(k in low for k in (
            "flag{", '"correct": true', '"correct":true',
            '"vulnerable": true', '"vulnerable":"true"',
            '"differentiated": true', '"vuln": true', '"vuln":"true"',
            "login success", "logged in", "session=", "响应存在差异")):
        return 1
    # 新路径/入口发现（目录枚举、HTTP 探测有状态码响应）或开放端口（nmap/masscan）
    if _HTTP_STATUS_RE.search(text) or _PORT_OPEN_RE.search(text) or "open port" in low:
        return 1
    return 0


_NET_UNREACHABLE_HINTS = (
    "connection refused", "no route to host", "timed out", "timeout",
    "name or service not known", "network is unreachable",
    "could not resolve host", "连接超时", "网络不可达",
)


def _is_network_unreachable(text: str) -> bool:
    """检测工具输出是否命中「网络不可达」信号（连接拒绝/超时/无路由）。

    用于快速换题：同一目标连续命中 ≥2 次即判定不可达，避免 VPN 断开后死磕。
    """
    low = text.lower()
    return any(k in low for k in _NET_UNREACHABLE_HINTS)


class EventStreamHooks(RunHooks):
    def __init__(self, workdir: Path, code: str):
        self.workdir = workdir
        self.code = code
        self.task_id = code  # 事件总线/落库的 task 标识（题目 unique_code 或 "generic"/"sub_<id>"）

    def _emit(self, kind: str, **data):
        # 保留文件留痕（向后兼容，main.py 读 events.jsonl 的地方不变）
        entry = {"kind": kind, "ts": round(time.time(), 1), "code": self.code, **data}
        line = json.dumps(entry, ensure_ascii=False)
        print(f"  [{kind}] {json.dumps(data, ensure_ascii=False)[:200]}")
        with open(self.workdir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # 发射到进程级事件总线（内存历史 + SQLite 落库订阅者）
        BUS.emit(self.task_id, kind, **data)

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self._emit("llm_call", agent=agent.name)

    async def on_llm_end(self, context, agent, response):
        # 统计 token 用量（累计到 TaskContext，供 status.json / 最终总结展示）
        usage = getattr(response, "usage", None)
        task_ctx = getattr(context, "context", None)
        cur = {"input": 0, "output": 0, "total": 0}
        if usage is not None:
            cur["input"] = int(getattr(usage, "input_tokens", 0) or 0)
            cur["output"] = int(getattr(usage, "output_tokens", 0) or 0)
            cur["total"] = int(getattr(usage, "total_tokens", 0) or 0)
        if task_ctx is not None:
            task_ctx.token_usage["input"] += cur["input"]
            task_ctx.token_usage["output"] += cur["output"]
            task_ctx.token_usage["total"] += cur["total"]
            task_ctx.token_usage["requests"] += 1
            # 记录最近一次请求的真实 prompt_tokens（上下文真实大小），供压缩观测/校准
            task_ctx.last_prompt_tokens = cur["input"]

        # 完整文本进事件流（不截断），另发一条 token 事件供 UI 实时显示用量
        text = _output_text(response)
        self._emit("thought", agent=agent.name, text=text, usage=cur)
        if task_ctx is not None:
            self._emit("token", agent=agent.name, usage=cur,
                       total=dict(task_ctx.token_usage))

    async def on_tool_start(self, context, agent, tool):
        self._emit("tool", agent=agent.name, tool=tool.name, status="executing")
        task_ctx = getattr(context, "context", None)
        if task_ctx is not None:
            task_ctx.turn_tool_count += 1

    async def on_tool_end(self, context, agent, tool, result):
        # 完整结果进事件流（不截断），供 UI 展示「执行了什么」
        self._emit("tool_result", agent=agent.name, tool=tool.name,
                   text=str(result), status="done")

        # ---- 渐进披露：按证据追加技能 ----
        task_ctx = getattr(context, "context", None)
        if task_ctx is None:
            return
        for skill in detect_skill_triggers(str(result), task_ctx.disclosed_skills):
            task_ctx.disclosed_skills.append(skill)
            task_ctx.skill_events.append(f"{tool.name} 命中证据 → 披露 {skill}")
            self._emit("skill_disclosed", tool=tool.name, skill=skill)

        # ---- 证据自动切阶段（代码兜底，防止 Agent 忘记 set_phase）----
        if _auto_advance_phase(task_ctx, str(result)):
            self._emit("phase_changed", agent=agent.name,
                       phase=task_ctx.phase, trigger=tool.name)

        # ---- 信息增量打分：正向证据置位 turn_gain，供判停/replan 复用 ----
        score = _score_tool_result(tool.name, str(result))
        if score > 0:
            task_ctx.turn_gain = True
        self._emit("reward", tool=tool.name, score=score)

        # ---- 网络不可达检测：本轮命中即置位，供单题循环快速换题（防 VPN 死磕）----
        if _is_network_unreachable(str(result)):
            task_ctx.turn_net_fail = True
            self._emit("net_unreachable", tool=tool.name)

    async def on_agent_start(self, context, agent):
        self._emit("agent_start", agent=agent.name)

    async def on_agent_end(self, context, agent, output):
        self._emit("agent_end", agent=agent.name, text=str(output))
