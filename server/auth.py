"""auth.py —— 纯 ASGI API Key 鉴权中间件（产品化商业化地基）。

启用方式：环境变量 SECAI_API_KEYS="key1,key2,..."（逗号分隔，去空格/去空项/去重）。
未设置或为空字符串 → 空集 → 鉴权完全关闭（向后兼容默认行为）。

规则：
- 仅对 /api/ 前缀的 HTTP 请求与 WebSocket 握手鉴权；非 /api/ 路径（SPA 静态资源）
  一律放行，不做鉴权；
- 通过：请求头 X-API-Key 命中 api_keys 集合 → 放行给内层 app；
- 拒绝（HTTP）：401 + {"ok": false, "error": {"code": "unauthorized", "message": ...}}
  （鉴权层用真实状态码，区别于业务错误的恒 200）；
- 拒绝（WebSocket）：握手阶段 websocket.close(code=4401) 后返回，不 accept。

实现为纯 ASGI middleware（不依赖 Starlette 请求/响应对象、不读取 body），
因此不侵入 api.py / ws.py 的任何业务逻辑。
"""
from __future__ import annotations

import json
import os
from typing import Any

UNAUTHORIZED_BODY = {
    "ok": False,
    "error": {"code": "unauthorized", "message": "missing or invalid X-API-Key header"},
}
_API_PREFIX = "/api/"
_API_KEYS_ENV = "SECAI_API_KEYS"


def load_api_keys() -> set[str]:
    """从 SECAI_API_KEYS 读取逗号分隔 key 集合（去空格/去空项/去重）。

    未设置或全空白 → 空集（鉴权关闭）。
    """
    raw = os.getenv(_API_KEYS_ENV, "")
    return {k.strip() for k in raw.split(",") if k.strip()}


class AuthMiddleware:
    """纯 ASGI API Key 鉴权：api_keys 为空集/None → 完全放行（向后兼容）。"""

    def __init__(self, app: Any, api_keys: set[str] | None = None) -> None:
        self.app = app
        self.api_keys = frozenset(k for k in (api_keys or ()) if k)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        # 未启用鉴权，或非 http/websocket（如 lifespan）→ 直通
        if not self.api_keys or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        # 仅 /api/ 前缀鉴权；SPA 静态资源等非 /api/ 路径放行
        if not scope.get("path", "").startswith(_API_PREFIX):
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        key = headers.get(b"x-api-key", b"").decode("latin-1").strip()
        if key and key in self.api_keys:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # 握手阶段拒绝：不 accept，直接 4401 关闭
            await send({"type": "websocket.close", "code": 4401})
            return
        await self._reject_http(send)

    @staticmethod
    async def _reject_http(send: Any) -> None:
        body = json.dumps(UNAUTHORIZED_BODY, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = ["AuthMiddleware", "load_api_keys"]
