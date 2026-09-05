"""ws.py —— /api/events.mux 与 /api/events.host 纯下行帧流。

前端契约（apps/web/src/connection/api.ts）：
- 消息即 MuxFrame / HostFrame（无 rpcId 外包装；需要回响处帧内自带 rpcId）；
- 两条流都是服务端纯下行：客户端发消息 → 服务端以 1008 关闭（ConnectionController
  永不 send，本层是物理收口）；
- mux 流连接后先回放全部已知会话快照，首帧必为 session/subscribed（回放完成基线
  = lastSeq），随后持续推送 live 帧（session/event、session/projection、
  session/queue、approval/requested、approval/resolved、session/jobs）；
- host 流连接后回放全部会话登记（host/session-added），随后推送
  host/session-removed / host/session-status / host/engagement-changed。

事件源：core/events BUS（state 订阅 → SessionEvent 落账 + 扇出）+ 内置 demo
engagement fixture（server/fixture.py，离线展示 running/awaiting_approval/completed）。
帧 schema 见 api.ts MuxFrame/HostFrame 类型表与 tests/unit/test_server_ws.py。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from starlette.websockets import WebSocket


def _state(ws: WebSocket) -> Any:
    return ws.app.state.state


async def _pump(ws: WebSocket, queue: asyncio.Queue[str]) -> None:
    """订阅队列 → WS 文本帧（慢消费者丢帧已在 Hub 侧兜底）。"""
    while True:
        text = await queue.get()
        if text is None:
            return
        await ws.send_text(text)


async def _serve(
    ws: WebSocket,
    topic: str,
    replay: Callable[[], list[dict[str, Any]]],
) -> None:
    """单条下行流骨架：握手 → 回放快照 → 扇出 live 帧 → 纯下行 1008 收口。"""
    state = _state(ws)
    await ws.accept()
    queue = state.hub.register(topic)
    pump = asyncio.create_task(_pump(ws, queue))
    try:
        for frame in replay():
            await ws.send_text(json.dumps(frame, ensure_ascii=False))
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            # 纯下行：任何客户端上行（文本/二进制）→ 1008 策略拒绝
            await ws.close(code=1008, reason=f"{topic} 流纯下行：客户端消息被拒绝")
            break
    except Exception:
        pass
    finally:
        state.hub.unregister(topic, queue)
        pump.cancel()
        try:
            await pump
        except BaseException:
            # 取消/断线路径：干净收口泵任务，不把 CancelledError 二次抛出
            pass


async def events_mux(ws: WebSocket) -> None:
    """Mux 下行流：按 sessionId 路由到目标会话的帧（session/* + approval/*）。"""
    await _serve(ws, "mux", _state(ws).mux_replay_frames)


async def events_host(ws: WebSocket) -> None:
    """Host 下行流：跨目标会话集群视图帧（host/*）。"""
    await _serve(ws, "host", _state(ws).host_replay_frames)


__all__ = ["events_host", "events_mux"]
