"""RunHooks：把 SDK 的内部回调投影成统一事件流（打印 + 落盘 events.jsonl），
并承担「多 Skills 渐进披露」的运行时触发：扫描工具输出，命中触发词就追加技能到 context。

注意：function_tool 的 on_tool_start/on_tool_end 里，context 是 ToolContext（继承 RunContextWrapper），
context.context 才是我们的 TaskContext。
"""
from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from agents import RunHooks

from arsenal.registries.skill_registry import detect_skill_triggers
from core.events import BUS
from runtime.log import log_debug, log_info, log_warn

# 无进展工具：这些工具不产生攻击进展，调用它们不计入「本轮工具调用」，
# 否则 think/todo/checkpoint 会合法绕过「连续 N 轮空转 → 机械换题」防线。
_NO_PROGRESS_TOOLS = {"think", "todo_add", "todo_list", "todo_mark", "checkpoint", "list_tools"}

# ---- events.jsonl 缓冲落盘：避免每轮多次 open/write/close（IO 优化） ----
_EMIT_BUFFER: dict = {}
_EMIT_BUFFER_LOCK = threading.Lock()
_EMIT_FLUSH_THRESHOLD = 20  # 单文件缓冲条数，达到即刷盘（Q1：阈值由 100 降到 20，崩溃丢帧更少）
# 关键事件：敏感凭据获取/阶段切换/网络异常/智能体结束/prompt 漂移，立即刷盘绕过缓冲
# （Q1：kill -9/OOM 崩溃现场恰恰最需要事件流，不能让缓冲吞掉现场）
_CRITICAL_EVENT_KINDS = {"reward", "phase_changed", "net_unreachable",
                         "agent_end", "prompt_drift"}


def _flush_emit_buffer(workdir_str: str = "") -> None:
    """把指定文件（空串=全部）的缓冲行批量写盘。进程退出时由 atexit 兜底。"""
    with _EMIT_BUFFER_LOCK:
        if workdir_str:
            keys = [k for k in _EMIT_BUFFER if k == workdir_str]
        else:
            keys = list(_EMIT_BUFFER.keys())
        for k in keys:
            lines = _EMIT_BUFFER.pop(k, None)
            if not lines:
                continue
            try:
                Path(k).parent.mkdir(parents=True, exist_ok=True)
                with open(k, "a", encoding="utf-8") as f:
                    f.write("".join(lines))
            except Exception:
                pass


atexit.register(_flush_emit_buffer)


def _boost_role_by_trigger(task_ctx) -> None:
    """证据触发的阶段增强角色：黑板 confirmed 键命中角色 trigger 时注入对应打法。

    与渐进披露技能（disclosed_skills）不同，角色增强是「攻击链阶段」级方法论，
    随证据生长——同一场战役里执行者先后「成为」侦察兵、审计员、提权专员。
    触发信号只认黑板 confirmed 键（rce_confirmed/sqli_confirmed 等），不 grep 事件文本。
    """
    if task_ctx is None:
        return
    from arsenal.registries.role_registry import load_roles
    bb_keys = set(task_ctx.blackboard.keys())
    for r in load_roles():
        trig = (r.get("trigger") or "").strip()
        if not trig:
            continue
        if any(t.strip() in bb_keys for t in trig.split(",")) and r["role"] not in task_ctx.boosted_roles:
            task_ctx.boosted_roles.append(r["role"])
            task_ctx.role_boost = r.get("style", "")
            log_warn(f"[role-boost] 证据触发注入增强角色「{r['role']}」")


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


def _extract_reasoning(response) -> str:
    """从 Response 提取模型思考（reasoning）文本，供终端/日志实时展示。

    优先读取 output 中的 reasoning 项（summary 多段拼接）；SDK 未暴露时兜底
    读取 raw_response.choices[0].message.reasoning_content（DeepSeek 直返字段）。
    任何异常都返回空串，不影响主流程。
    """
    parts = []
    for item in getattr(response, "output", []) or []:
        get = (lambda k, d=None: item.get(k, d)) if isinstance(item, dict) \
            else (lambda k, d=None: getattr(item, k, d))
        if get("type", "") != "reasoning":
            continue
        summary = get("summary") or get("text") or ""
        if isinstance(summary, list):
            for s in summary:
                if isinstance(s, dict):
                    parts.append(str(s.get("text", "")))
                else:
                    parts.append(str(s))
        elif isinstance(summary, str):
            parts.append(summary)
    if not parts:
        try:
            raw = getattr(response, "raw_response", None)
            msg = raw.choices[0].message
            rc = getattr(msg, "reasoning_content", None)
            if rc:
                parts.append(str(rc))
        except Exception:
            pass
    return "\n".join(p for p in parts if p).strip()


def _log_thinking(agent: str, reasoning: str) -> None:
    """把 AI 思考（reasoning）实时打印：终端 INFO 可见（截断防刷屏）+ 完整落 DEBUG 文件。

    终端默认开启，长题嫌刷屏可设环境变量 SECAI_SHOW_THINKING=0 关闭。
    """
    show = os.getenv("SECAI_SHOW_THINKING", "1").lower() in ("1", "true", "yes")
    if show:
        brief = reasoning if len(reasoning) <= 800 else \
            reasoning[:800] + "\n…（已截断，完整见日志文件与 events.jsonl）"
        log_info(f"[思考:{agent}] {brief}")
    log_debug(f"[思考全量:{agent}] {reasoning}")


_HTTP_STATUS_RE = re.compile(r"\b(?:200|201|204|301|302|307|308|401|403|405|500)\b")
# 枚举类工具中视为「正向存活」的状态码：2xx 成功 / 3xx 重定向
_POSITIVE_STATUS_CODES = {"200", "201", "204", "301", "302", "307", "308"}
_PORT_OPEN_RE = re.compile(r"\b\d{1,5}/(?:tcp|udp)\s+open\b", re.IGNORECASE)
# 增量打分 v3：路径抽取与敏感文件识别（配合 seen_signatures 去重）
_PATH_EXTRACT_RE = re.compile(r"(?:/[A-Za-z0-9_.~%-]{2,}){1,4}")
_SENSITIVE_RE = re.compile(
    r"(config\.php|\.git/|backup|\.env|phpinfo|wp-config|"
    r"\.bak|\.sql|\.zip|web\.config|id_rsa|shadow)", re.IGNORECASE)
_ENUM_TOOLS = {"run_tool", "fuzz", "parallel_shell"}   # 枚举类工具的状态码算增量
# shell/http_request 等交互类工具的行为差异关键词（SQLi/命令注入/SSRF/反序列化）
# D2 收紧：强信号命中即算增量；弱词（admin/root 等）必须与错误/异常强信号同现才算
_STRONG_BEHAVIOR_HINTS = (
    "syntax error", "mysql", "sqlite", "postgresql", "ORA-", "pg_sleep",
    "whoami", "id\n", "uid=", "root:", "deserialization", "serial",
    "gadget", "__destruct", "__wakeup", "popen", "system(", "eval(",
    "exec(", "shell_exec", "sleep(", "benchmark(", "flag{", "password",
)
_WEAK_BEHAVIOR_HINTS = ("admin", "root", "secret", "internal", "localhost")
_ERROR_CONTEXT_RE = re.compile(r"error|exception|failed|denied|refused", re.IGNORECASE)


def _extract_hint_keywords(hint: str) -> list:
    """从 hint 原文提取技术名词（英文标识符/协议名/参数名），用于方向锁定。"""
    words = re.findall(r"[A-Za-z][A-Za-z0-9_.\-]{3,}", hint or "")
    stop = {"this", "that", "with", "from", "what", "does", "the", "and", "you", "your", "hint"}
    return [w for w in set(words) if w.lower() not in stop][:8]


# 差分基线：exploit 阶段 payload 台账（避免同一失败变体重复）
_EXPLOIT_LEDGER_TOOLS = {"shell", "http_request", "run_batch"}


def _ledger_signature(tool: str, args: Any) -> str:
    """把工具调用参数归一化为签名，用于判断是否是同一 payload 变体。

    args 兼容 str（旧调用方）或 dict（工具管线传入），统一序列化后归一化。
    """
    if isinstance(args, dict):
        raw = json.dumps(args, ensure_ascii=False, default=str)
    else:
        raw = str(args)
    raw = f"{tool}:{raw}".lower()
    # 去掉随机 token/nonce/sessid 等常见噪声，保留结构
    raw = re.sub(r"\b[a-f0-9]{16,64}\b", "<hex>", raw)
    raw = re.sub(r"\b\d{6,}\b", "<num>", raw)
    raw = re.sub(r"\s+", "", raw)
    return raw[:160]


def _record_payload_ledger(task_ctx, tool: str, args: Any, text: str) -> None:
    """在 exploit 阶段记账：同一签名失败 2 次就告警，并注入系统提示避免继续重复。"""
    if not getattr(task_ctx, "payload_ledger", None):
        task_ctx.payload_ledger = []
    sig = _ledger_signature(tool, args)
    hit = any(k in text.lower() for k in (
        "flag{", '"correct": true', '"vulnerable": true',
        '"differentiated": true', "login success", "logged in"))
    for entry in task_ctx.payload_ledger:
        if entry.get("signature") == sig:
            entry["count"] = entry.get("count", 0) + 1
            if hit:
                entry["hit"] = True
            return
    preview = (json.dumps(args, ensure_ascii=False, default=str) if isinstance(args, dict)
               else str(args))[:120]
    task_ctx.payload_ledger.append({"signature": sig, "tool": tool,
                                     "args_preview": preview, "hit": hit, "count": 1})


def _ledger_failed_summary(task_ctx, top_n: int = 8) -> str:
    """返回已连续失败 2 次及以上的 payload 清单，用于注入系统提示。"""
    failed = [e for e in getattr(task_ctx, "payload_ledger", [])
              if not e.get("hit") and e.get("count", 0) >= 2]
    if not failed:
        return ""
    lines = ["# 已失败 payload 清单（不要再重复）"]
    for e in failed[:top_n]:
        lines.append(f"- [{e.get('tool')}] {e.get('args_preview', '')[:80]} (失败 {e.get('count')} 次)")
    lines.append("请换目标、换参数、换 payload 类型或换利用链，不要重复上表。")
    return "\n".join(lines)


def _score_tool_result(tool: str, text: str, ctx) -> int:
    """信息增量打分 v3：+1 正向新认知 / 0 中性（纯规则，零 LLM）。

    原则：
    - 铁证（敏感凭据/漏洞确认/登录成功/差分判定）任何工具都算；
    - 枚举类工具：出现正向存活码 + 不同状态码差异/新路径/敏感文件即算增量；
    - 交互类工具（shell/http_request）：响应出现可利用行为特征（SQLi 报错、命令回显、
      内网内容、反序列化异常、敏感关键词）也算增量，避免精心构造的 payload 被误判为零；
    - 新路径/敏感文件：与历史签名去重后，首次出现即可算增量（由 ≥2 降为 ≥1）；
    - 工具失败/网络错误判 0 交给 LLM 决策（default-soft 不变）。
    """
    low = text.lower()

    # ① 铁证：任何工具，永远优先于 hint 方向锁（读到敏感凭据必须 +1，D1 修复）
    if any(k in low for k in (
            "flag{", "credential", "password", "passwd", "password=",
            "token:", "token=", "access_token", "api_key", "secret_key",
            '"vulnerable": true', '"vulnerable":"true"',
            '"differentiated": true', '"vuln": true', '"vuln":"true"',
            "login success", "logged in")):
        return 1

    # ② hint 方向锁：锁定期内，命中 hint 方向 → 判定已转化并解锁；未命中 → 零增量
    #（D1 修复：hint 转化成功后解锁，避免已推进的题被判零增量而被机械换题）
    if getattr(ctx, "hint_grace_active", False):
        hint_dir = ctx.blackboard.get("hint_directive", {}).get("value", "")
        kws = _extract_hint_keywords(hint_dir)
        if kws and any(k.lower() in low for k in kws):
            ctx.hint_grace_active = False   # 命中 hint 方向 → 已转化，解锁
            return 1
        if kws:
            return 0  # 与 hint 无关的输出一律零增量 → 方向上锁

    # ③ 新敏感文件：任何工具，首次出现才算（去重）
    sensitive = {m.lower() for m in _SENSITIVE_RE.findall(text)}
    if sensitive - ctx.seen_signatures:
        ctx.seen_signatures |= sensitive
        return 1
    # ④ 枚举类工具：状态码必须包含正向存活码（2xx/3xx）才说明扫到可访问端点
    if tool in _ENUM_TOOLS:
        codes = set(_HTTP_STATUS_RE.findall(text))
        has_positive = bool(codes & _POSITIVE_STATUS_CODES)
        has_negative = bool(codes - _POSITIVE_STATUS_CODES)
        # 放宽：一个正向码 + 任意差异（不同状态码 / 敏感路径 / 开放端口）即算增量
        if has_positive and (len(codes) >= 2 or has_negative or _PORT_OPEN_RE.search(text)
                           or "open port" in low):
            return 1
        # 枚举发现的新路径（去重后仍有新货）≥1 条即算，避免漏掉单条高价值路径
        new_paths = {p.lower() for p in _PATH_EXTRACT_RE.findall(text)} \
                    - ctx.seen_signatures
        if new_paths:
            ctx.seen_signatures |= new_paths
            return 1
    # ⑤ 交互类工具：行为差异也算增量，避免 payload 被误判为零进展
    if tool in ("shell", "http_request"):
        if any(k in low for k in _STRONG_BEHAVIOR_HINTS):
            return 1
        # D2 收紧：弱词（admin/root/secret/internal/localhost）必须与错误/异常同现才算
        if any(k in low for k in _WEAK_BEHAVIOR_HINTS) and _ERROR_CONTEXT_RE.search(low):
            return 1
        # HTTP 单次请求中同时出现状态码 + 响应关键词线索，说明触发了后端逻辑
        if tool == "http_request":
            codes = set(_HTTP_STATUS_RE.findall(text))
            if (codes & _POSITIVE_STATUS_CODES
                    and any(k in low for k in ("error", "syntax", "warning", "exception",
                                              "serial", "deserialization"))):
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


def _clip(text, limit: int) -> str:
    """压缩换行并按上限截断，超长时附加字数提示（避免终端被长文本刷屏）。"""
    text = str(text).replace("\n", " ")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（已截断，共 {len(text)} 字符）"


# 工具返回内容中的错误特征（系统工具自身的固定报错格式，非靶场响应正文）
_TOOL_ERROR_HINTS = (
    '"error"', '命令超时', '执行失败', '请求失败', '搜索不可用',
    '未创建', '无权限', 'denied', 'refused', 'timed out', '不存在',
)


def _is_error_result(text: str) -> bool:
    """判断工具返回内容是否属于失败/报错，用于把日志级别升级为 WARN。"""
    low = text.lower()
    return any(k in low for k in _TOOL_ERROR_HINTS)


def _log_event(code: str, kind: str, data: dict) -> None:
    """把事件流中的关键事件投影成带时间戳/级别的终端日志。

    终端只展示 INFO 及以上；thought/reward/llm_call 等细粒度事件用 DEBUG，
    只落盘到 data/logs/*.log，不刷终端。完整事件仍由 _emit 写入 events.jsonl。
    """
    tag = f"[{code}] " if code and code != "generic" else ""
    agent = str(data.get("agent", ""))
    tool = str(data.get("tool", ""))

    if kind == "agent_start":
        log_info(f"{tag}智能体启动：{agent}")
    elif kind == "agent_end":
        log_info(f"{tag}智能体结束：{agent} → {_clip(data.get('text', ''), 500)}")
    elif kind == "tool":
        log_info(f"{tag}调用工具：{agent} → {tool}")
    elif kind == "tool_result":
        text = str(data.get("text", ""))
        if _is_error_result(text):
            log_warn(f"{tag}工具返回异常：{tool}（{len(text)} 字符）{_clip(text, 300)}")
        else:
            log_info(f"{tag}工具返回：{tool}（{len(text)} 字符）{_clip(text, 300)}")
    elif kind == "skill_disclosed":
        log_warn(f"{tag}技能披露：{tool} 命中证据 → {data.get('skill')}")
    elif kind == "phase_changed":
        log_warn(f"{tag}阶段切换：{agent} → {data.get('phase')}（触发 {data.get('trigger')}）")
    elif kind == "net_unreachable":
        log_warn(f"{tag}网络不可达：{tool}")
    elif kind == "token":
        usage = data.get("usage", {})
        total = data.get("total", {})
        log_info(f"{tag}Token：{agent} 本轮 +{usage.get('total', 0)}，"
                 f"累计 {total.get('total', 0)}")
    elif kind == "llm_call":
        log_debug(f"{tag}LLM 调用：{agent}")
    elif kind == "thought":
        text = str(data.get("text", ""))[:200].replace("\n", " ")
        log_debug(f"{tag}思考：{agent} → {text}")
    elif kind == "reward":
        log_debug(f"{tag}增量打分：{tool} score={data.get('score')}")
    else:
        log_info(f"{tag}[{kind}] {json.dumps(data, ensure_ascii=False)[:200]}")


class EventStreamHooks(RunHooks):
    def __init__(self, workdir: Path, code: str):
        self.workdir = workdir
        self.code = code
        self.task_id = code  # 事件总线/落库的 task 标识（题目 unique_code 或 "generic"/"sub_<id>"）
        # system prompt 字节级稳定性断言：同一 agent 首个 prompt 的 hash 作为基线
        self._prompt_hashes: dict = {}

    def _emit(self, kind: str, **data):
        # 保留文件留痕（向后兼容，事件消费者读 events.jsonl 的口径不变）
        entry = {"kind": kind, "ts": round(time.time(), 1), "code": self.code, **data}
        line = json.dumps(entry, ensure_ascii=False)
        _log_event(self.code, kind, data)
        # 缓冲落盘：满阈值批量写，避免每轮多次 open/write/close；
        # 关键事件立即刷盘绕过缓冲（Q1：崩溃现场不丢）
        path = str(self.workdir / "events.jsonl")
        with _EMIT_BUFFER_LOCK:
            buf = _EMIT_BUFFER.setdefault(path, [])
            buf.append(line + "\n")
            if kind in _CRITICAL_EVENT_KINDS or len(buf) >= _EMIT_FLUSH_THRESHOLD:
                lines = _EMIT_BUFFER.pop(path)
            else:
                lines = None
        if lines is not None:
            try:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write("".join(lines))
            except Exception:
                pass
        # 发射到进程级事件总线（内存历史 + SQLite 落库订阅者）
        BUS.emit(self.task_id, kind, **data)

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        # system prompt hash 断言：同一 agent 的静态指令必须字节级稳定，
        # 否则缓存击穿（prompt cache 命中率骤降），记录并告警。
        try:
            import hashlib
            sp = str(system_prompt)
            h = hashlib.sha256(sp.encode("utf-8")).hexdigest()[:16]
            base = self._prompt_hashes.get(agent.name)
            if base is None:
                self._prompt_hashes[agent.name] = h
            elif base != h:
                log_warn(f"[prompt-drift] {agent.name} system prompt 字节级漂移！"
                         f"基线 {base} 现 {h}，缓存命中将受损")
                self._emit("prompt_drift", agent=agent.name, base=base, now=h)
        except Exception:
            pass
        self._emit("llm_call", agent=agent.name)

    async def on_llm_end(self, context, agent, response):
        # 统计 token 用量（累计到 TaskContext，供 status.json / 最终总结展示）
        usage = getattr(response, "usage", None)
        task_ctx = getattr(context, "context", None)
        cur = {"input": 0, "output": 0, "total": 0, "cache_read": 0, "cache_write": 0}
        if usage is not None:
            cur["input"] = int(getattr(usage, "input_tokens", 0) or 0)
            cur["output"] = int(getattr(usage, "output_tokens", 0) or 0)
            cur["total"] = int(getattr(usage, "total_tokens", 0) or 0)
            # 真实 prompt cache 命中读取（OpenAI / DeepSeek 等模型返回的 details）
            details = getattr(usage, "prompt_tokens_details", None) or {}
            if isinstance(details, dict):
                cur["cache_read"] = int(details.get("cached_tokens") or 0)
                cur["cache_write"] = int(details.get("cache_write_tokens") or 0)
            # 兼容 DeepSeek 直返字段
            if not cur["cache_read"]:
                cur["cache_read"] = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
            if not cur["cache_write"]:
                cur["cache_write"] = int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)
        if task_ctx is not None:
            task_ctx.token_usage["input"] += cur["input"]
            task_ctx.token_usage["output"] += cur["output"]
            task_ctx.token_usage["total"] += cur["total"]
            task_ctx.token_usage["requests"] += 1
            task_ctx.token_usage["cache_read"] = task_ctx.token_usage.get("cache_read", 0) + cur["cache_read"]
            task_ctx.token_usage["cache_write"] = task_ctx.token_usage.get("cache_write", 0) + cur["cache_write"]
            # 记录最近一次请求的真实 prompt_tokens（上下文真实大小），供压缩观测/校准
            task_ctx.last_prompt_tokens = cur["input"]

        # 完整文本进事件流（不截断），另发一条 token 事件供 UI 实时显示用量与 cache 命中
        text = _output_text(response)
        self._emit("thought", agent=agent.name, text=text, usage=cur)
        # AI 思考（reasoning）实时打印：终端 INFO 可见，完整进 DEBUG 文件
        reasoning = _extract_reasoning(response)
        if reasoning:
            _log_thinking(agent.name, reasoning)
        if task_ctx is not None:
            self._emit("token", agent=agent.name, usage=cur,
                       total=dict(task_ctx.token_usage))

    @staticmethod
    def _tool_args(context) -> str:
        """从 ToolContext 提取本次工具调用的参数 JSON 字符串（供工作流视图展示「调用了什么」）。

        function_tool 的 context 是 ToolContext（含 tool_arguments）；其他工具族无此属性时返回空串。
        """
        try:
            return str(getattr(context, "tool_arguments", "") or "")
        except Exception:
            return ""

    async def on_tool_start(self, context, agent, tool):
        self._emit("tool", agent=agent.name, tool=tool.name, status="executing",
                   args=self._tool_args(context))
        task_ctx = getattr(context, "context", None)
        if task_ctx is not None and tool.name not in _NO_PROGRESS_TOOLS:
            task_ctx.turn_tool_count += 1

    async def on_tool_end(self, context, agent, tool, result):
        # 完整结果进事件流（不截断），供 UI 展示「执行了什么」+ 调用参数
        self._emit("tool_result", agent=agent.name, tool=tool.name,
                   text=str(result), status="done",
                   args=self._tool_args(context))

        # ---- 渐进披露：按证据追加技能 ----
        task_ctx = getattr(context, "context", None)
        if task_ctx is None:
            return
        for skill in detect_skill_triggers(str(result), task_ctx.disclosed_skills):
            task_ctx.disclosed_skills.append(skill)
            task_ctx.skill_events.append(f"{tool.name} 命中证据 → 披露 {skill}")
            self._emit("skill_disclosed", tool=tool.name, skill=skill)

        # ---- 阶段增强角色（证据触发）：黑板 confirmed 键命中角色 trigger 时注入打法 ----
        _boost_role_by_trigger(task_ctx)

        # ---- 信息增量打分：正向证据置位 turn_gain，供判停/replan 复用 ----
        score = _score_tool_result(tool.name, str(result), task_ctx)
        if score > 0:
            task_ctx.turn_gain = True
        self._emit("reward", tool=tool.name, score=score)

        # ---- exploit 阶段 payload 台账（差分基线纪律） ----
        if task_ctx.phase == "exploit" and tool.name in _EXPLOIT_LEDGER_TOOLS:
            args_text = ""
            try:
                args_text = json.dumps(getattr(result, "input", {}) or {}, ensure_ascii=False)
            except Exception:
                args_text = str(getattr(result, "input", "") or "")[:200]
            _record_payload_ledger(task_ctx, tool.name, args_text, str(result))

        # ---- 网络不可达检测：本轮命中即置位，供单题循环快速换题（防 VPN 死磕）----
        if _is_network_unreachable(str(result)):
            task_ctx.turn_net_fail = True
            self._emit("net_unreachable", tool=tool.name)

    async def on_agent_start(self, context, agent):
        self._emit("agent_start", agent=agent.name)

    async def on_agent_end(self, context, agent, output):
        self._emit("agent_end", agent=agent.name, text=str(output))
