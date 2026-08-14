"""SecAI 实时对话前端服务（Python 标准库，零新依赖）。

启动：
    python server.py
访问：
    http://localhost:8000

接口：
    GET  /            → 前端页面
    POST /api/chat    → 发送消息，Agent 回复（多轮对话，session 保持上下文）
    GET  /api/stream  → SSE 实时流（Agent 的 thought / tool / tool_result 事件）
"""
from __future__ import annotations

import asyncio
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agents import Agent, Runner
from agents.memory import SQLiteSession

from agents_def import SETTINGS
from config import MODEL
import db as db_mod
from demo_tools import ALL_TOOLS, TOOL_GROUPS, CORE_TOOL_NAMES
from hooks import EventStreamHooks
from status import PHASE_DEFS
from task_context import TaskContext

ROOT = Path(__file__).parent
WORKDIR = ROOT / "data" / "worker_web"
WORKDIR.mkdir(parents=True, exist_ok=True)
EVENTS_FILE = WORKDIR / "events.jsonl"
SESSION_FILE = WORKDIR / "session.sqlite"

# 可切换的事件流数据源：对话流（web）与三智能体任务流（generic = main.py 的 worker_generic）
EVENT_FILES = {
    "web": EVENTS_FILE,
    "generic": ROOT / "data" / "worker_generic" / "events.jsonl",
}

# 初始化 SQLite（data/agent.db），与 main.py 跑分任务共用同一文件，跨进程读事件
_db = db_mod.init_default()

# 智能体元数据（供 /agents 页展示）
AGENTS = [
    {"name": "Manager", "label": "管理者", "desc": "意图识别 + 写使命宪章（立法而非执行）"},
    {"name": "Planner", "label": "规划师", "desc": "任务深度分析，产出作战计划"},
    {"name": "Executor", "label": "执行者", "desc": "按角色执行使命，产出证据与结论"},
    {"name": "Reporter", "label": "报告者", "desc": "战报 + 死路蒸馏"},
    {"name": "Compactor", "label": "压缩器", "desc": "上下文超阈值时压缩历史"},
]

# kill-chain 阶段中文标签 + 常用工具映射（供 /agents 页展示阶段 → 工具/流程）
PHASE_LABELS = {
    "recon": "侦察", "enumerate": "枚举", "detect": "检测",
    "exploit": "利用", "post": "后利用",
}
PHASE_TOOLS = {
    "recon": ["list_tools", "get_tool_spec", "run_tool", "http_request", "web_search", "shell", "find_skills"],
    "enumerate": ["fuzz", "get_payload", "distinguish", "http_request", "parallel_shell"],
    "detect": ["detect_vuln", "list_vulns", "fuzz", "search_cve", "get_poc"],
    "exploit": ["get_poc", "search_cve", "run_tool", "get_knowledge", "shell"],
    "post": ["shell", "read_artifact", "write_file", "submit_flag", "close_challenge", "get_hint"],
}

# 每次启动服务从头开始（清空旧事件流）
EVENTS_FILE.write_text("", encoding="utf-8")

# 多轮对话 Agent：可调用工具获取信息后再回答
chat_agent = Agent(
    name="SecAI 对话助手",
    instructions=(
        "你是 SecAI 安全助手，用中文简洁回答用户的问题。\n"
        "需要时先调用工具（shell / http_request / search_cve / detect_vuln / "
        "get_knowledge / get_payload 等）获取信息或执行操作，再基于结果回答。\n"
        "只回答用户当前的问题，不要跑题。"
    ),
    tools=ALL_TOOLS,
    model=MODEL,
    model_settings=SETTINGS,
)

# 多轮对话的共享上下文 + 会话 + 事件 hooks
chat_ctx = TaskContext(workdir=WORKDIR)
session = SQLiteSession(session_id="web", db_path=str(SESSION_FILE))
hooks = EventStreamHooks(WORKDIR, "web")


async def run_chat(message: str) -> str:
    """跑一轮对话（可多步推理），返回 Agent 最终回复。max_turns=None 不设回合上限。"""
    result = await Runner.run(
        chat_agent, input=message, context=chat_ctx,
        hooks=hooks, session=session, max_turns=None)
    return str(result.final_output)


def _meta() -> dict:
    """构建 /api/meta 返回：智能体 + kill-chain 阶段 + 工具分组。"""
    phases = []
    for name, ph in PHASE_DEFS.items():
        phases.append({
            "name": name,
            "label": PHASE_LABELS.get(name, name),
            "goal": ph.get("goal", ""),
            "next": ph.get("next", ""),
            "tools": PHASE_TOOLS.get(name, []),
        })
    groups = {"core": sorted(CORE_TOOL_NAMES)}
    for g, names in TOOL_GROUPS.items():
        groups[g] = names
    return {"agents": AGENTS, "phases": phases, "tool_groups": groups}


def _task_summaries() -> list[dict]:
    """合并 tasks 表（生命周期 status/answer）与 events 表（事件数/最新活动），
    供监控页任务列表展示。"""
    task_rows = {r["id"]: r for r in _db.query("SELECT * FROM tasks")}
    event_rows = _db.query(
        "SELECT task_id, COUNT(*) AS c, MAX(ts) AS last_ts FROM events "
        "GROUP BY task_id")
    event_counts = {r["task_id"]: r for r in event_rows}

    all_ids = set(task_rows) | set(event_counts)
    out = []
    for tid in all_ids:
        t = task_rows.get(tid)
        ev = event_counts.get(tid)
        last = _db.query(
            "SELECT kind FROM events WHERE task_id=? ORDER BY seq DESC LIMIT 1", (tid,))
        out.append({
            "task_id": tid,
            "status": t["status"] if t else "unknown",
            "answer": (t["answer"] or "")[:200] if t else "",
            "started_at": t["started_at"] if t else None,
            "finished_at": t["finished_at"] if t else None,
            "count": ev["c"] if ev else 0,
            "last_ts": ev["last_ts"] if ev else None,
            "last_kind": last[0]["kind"] if last else "",
        })
    out.sort(key=lambda x: (x.get("started_at") or 0), reverse=True)
    return out


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            self._send_file("index.html")
        elif path == "/monitor":
            self._send_file("monitor.html")
        elif path == "/agents":
            self._send_file("agents.html")
        elif path == "/api/meta":
            self._send_json(_meta())
        elif path == "/api/tasks":
            self._send_json({"tasks": _task_summaries()})
        elif path == "/api/events":
            self._api_events(query)
        elif path == "/api/stream":
            self._sse()
        elif path == "/api/stream-db":
            self._sse_db(query)
        else:
            self._send(404, "text/plain; charset=utf-8", "not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/chat":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length) or b"{}")
                message = (data.get("message") or "").strip()
            except Exception:
                message = ""
            if not message:
                self._send(400, "application/json; charset=utf-8",
                           json.dumps({"error": "message 为空"}, ensure_ascii=False))
                return
            try:
                reply = asyncio.run(run_chat(message))
            except Exception as e:
                reply = f"（执行出错：{str(e)[:300]}）"
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"reply": reply}, ensure_ascii=False))
        else:
            self._send(404, "text/plain; charset=utf-8", "not found")

    def _sse(self):
        """SSE 实时流：持续把所选 events.jsonl 的新行推给前端。

        查询参数 ?dir=web|generic 选择事件源；web=对话流，generic=三智能体任务流。
        """
        dirname = (parse_qs(urlparse(self.path).query).get("dir") or ["web"])[0]
        events_file = EVENT_FILES.get(dirname, EVENTS_FILE)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        offset = 0
        try:
            while True:
                try:
                    lines = events_file.read_text(encoding="utf-8").splitlines()
                except Exception:
                    lines = []
                new_lines = lines[offset:]
                offset = len(lines)
                for line in new_lines:
                    self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _send(self, code: int, ctype: str, body: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, name: str):
        try:
            html = (ROOT / "static" / name).read_text(encoding="utf-8")
            self._send(200, "text/html; charset=utf-8", html)
        except Exception:
            self._send(404, "text/plain; charset=utf-8", "not found")

    def _send_json(self, obj):
        self._send(200, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False))

    @staticmethod
    def _flatten(event: dict) -> dict:
        """把 db 事件 {seq,ts,task_id,kind,data} 拍平成 {seq,ts,task_id,kind,...data}。"""
        return {"seq": event["seq"], "ts": event["ts"],
                "task_id": event["task_id"], "kind": event["kind"],
                **(event.get("data") or {})}

    def _api_events(self, query):
        task_id = (query.get("task_id") or [""])[0]
        if not task_id:
            self._send_json({"error": "task_id 必填"})
            return
        try:
            after = int((query.get("after_seq") or ["0"])[0])
        except ValueError:
            after = 0
        events = (_db.get_events_after(task_id, after) if after > 0
                  else _db.get_events(task_id))
        self._send_json({"events": [self._flatten(e) for e in events]})

    def _sse_db(self, query):
        """SSE 实时流：轮询 SQLite 里某 task_id 的新事件推送（拍平后给前端）。"""
        task_id = (query.get("task_id") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        last_seq = 0
        try:
            while True:
                try:
                    events = _db.get_events_after(task_id, last_seq) if task_id else []
                except Exception:
                    events = []
                for ev in events:
                    flat = self._flatten(ev)
                    self.wfile.write(
                        f"data: {json.dumps(flat, ensure_ascii=False)}\n\n".encode("utf-8"))
                    last_seq = ev["seq"]
                self.wfile.flush()
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *args):
        pass  # 静默访问日志


if __name__ == "__main__":
    port = 8000
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"SecAI 前端服务已启动：http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
