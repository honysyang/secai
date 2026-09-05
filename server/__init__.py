"""server —— v4 Web 服务面（F1 双 WS 纯下行 + HTTP RPC + 静态托管）。

仓库结构最后一块：把 core/events BUS、harness/session_manager、R5 报告引擎与
apps/web 前端契约（apps/web/src/connection/api.ts）缝合为可离线运行的整站服务。

模块划分：
- main.py     Starlette app 组装 + uvicorn 入口（`python -m server.main --port 8700`）；
- api.py      JSON REST：/api/describe|targets|engagements|run|steer|respond|report
              （业务错误恒 HTTP 200 {ok:false,error}，对齐 RpcResult 信封）；
- ws.py       /api/events.mux + /api/events.host 纯下行帧流（客户端上行 1008 拒绝）；
- static.py   apps/web/dist 静态资源 + SPA fallback；
- state.py    AppState：SessionManager 桥接 + BUS 订阅→帧转换 + WS 扇出 Hub +
              审批注册表 + 编排面会话/任务书记录；
- fixture.py  内置 demo engagement fixture（离线展示 running / awaiting_approval /
              completed 三种运行态，数据对齐前端 demo.ts 场景）。

前端契约对齐说明见各模块 docstring 与 tests/unit/test_server_*.py。
"""
from __future__ import annotations

# describe 握手身份（前端 HostDescription.product/version 语义）
SERVER_NAME = "SECAI-PT"
SERVER_VERSION = "4.0.0"

__all__ = ["SERVER_NAME", "SERVER_VERSION"]
