"""main.py —— server 包入口：Starlette app 组装 + uvicorn CLI。

启动：python -m server.main --port 8700
接口面（与 api.ts / ws.py 对齐）：
- GET  /                       → apps/web/dist index.html（SPA fallback）
- POST /api/{describe|targets|engagements|run|steer|respond|report}
- WS   /api/events.mux        → MuxFrame 下行（session/event|subscribed|projection|approval…）
- WS   /api/events.host       → HostFrame 下行（session-added|removed|status|engagement-changed）

lifespan：启动 demo ticker（running 会话周期帧），退出时统一收尾
（ticker / 后台任务 / SessionManager / BUS 退订）。
"""
from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute

from server.api import (
    api_assets,
    api_describe,
    api_engagements,
    api_export_report,
    api_list_artifacts,
    api_report,
    api_respond,
    api_risks,
    api_run,
    api_steer,
    api_targets,
    api_reports,
    api_update_risk,
)
from server.auth import AuthMiddleware, load_api_keys
from server.fixture import install_demo
from server.state import AppState
from server.static import spa
from server.ws import events_host, events_mux

ROUTES = [
    Route("/api/describe", api_describe, methods=["POST"]),
    Route("/api/targets", api_targets, methods=["POST"]),
    Route("/api/engagements", api_engagements, methods=["POST"]),
    Route("/api/run", api_run, methods=["POST"]),
    Route("/api/steer", api_steer, methods=["POST"]),
    Route("/api/respond", api_respond, methods=["POST"]),
    Route("/api/report", api_report, methods=["POST"]),
    Route("/api/listArtifacts", api_list_artifacts, methods=["POST"]),
    Route("/api/exportReport", api_export_report, methods=["POST"]),
    Route("/api/assets", api_assets, methods=["POST"]),
    Route("/api/risks", api_risks, methods=["POST"]),
    Route("/api/reports", api_reports, methods=["POST"]),
    Route("/api/updateRisk", api_update_risk, methods=["POST"]),
    WebSocketRoute("/api/events.mux", events_mux),
    WebSocketRoute("/api/events.host", events_host),
    Route("/{path:path}", spa, methods=["GET"]),
]

# demo fixture 开关：默认关闭（真枪实弹）；设 SECAI_FIXTURE=1 恢复离线演示三会话
FIXTURE_ENABLED = os.getenv("SECAI_FIXTURE", "").strip() in ("1", "true", "yes")


def create_app() -> Starlette:
    """组装整站 app（业务状态挂 app.state.state；每次调用得到独立 AppState）。"""
    state = AppState()
    if FIXTURE_ENABLED:
        install_demo(state)  # 离线演示：无 LLM key 也能展示三会话运行态

    @asynccontextmanager
    async def lifespan(_: Starlette):
        state.start_ticker()
        try:
            yield
        finally:
            await state.close()

    app = Starlette(routes=ROUTES, lifespan=lifespan)
    # API Key 鉴权包在最外层：SECAI_API_KEYS 未设置/为空 → 空集 → 完全放行（向后兼容）
    app.add_middleware(AuthMiddleware, api_keys=load_api_keys())
    app.state.state = state
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="server.main", description="SECAI-PT v4 server（RPC + 双 WS 下行 + 静态）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8700, help="监听端口（默认 8700）")
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
