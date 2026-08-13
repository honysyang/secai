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
from demo_tools import ALL_TOOLS
from hooks import EventStreamHooks
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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
            self._send(200, "text/html; charset=utf-8", html)
        elif path == "/api/stream":
            self._sse()
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
