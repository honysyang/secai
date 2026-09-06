"""server /api/run 真实执行联调测试（遗留①：有 LLM Key 才跑，无 Key 自动 skip）。

验证真实执行链路（agent 工具由 deepseek-v4-flash 驱动，目标 127.0.0.1 只读侦察）：
1. POST /api/run（demo 任务书 + ≤3 轮 / ≤90s 预算）→ 编排面立即返回 engagement/session；
2. SessionRec 落账真实事件流：LLM 驱动的 run_recon_tool 工具调用（tool/call→tool/output
   ok=True） + assistant/system/user message + 逐轮 projection；
3. run_recon_tool 触发 approval/requested（BUS approval/requested 事件）→ POST
   /api/respond allow → approval/resolved（BUS + mux 帧回响）→ 真实执行放行；
4. run 结束后会话转 completed；重新连 mux 可回放该会话的 session/event（tool/call）。
5. steer：managed 会话可用 /api/steer 注入指令（user message 事件落账）。

成本控制：单目标、max_rounds=3、approval_timeout 20s、wallclock 90s —— 实际典型
2 轮 / ≤4 次 LLM 调用即收敛。无 LLM_API_KEY/OPENAI_API_KEY 环境自动跳过。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

# 关掉 demo ticker 干扰（在 import server.* 前生效）
os.environ.setdefault("SECAI_DEMO_TICK_SECONDS", "3600")

import pytest
from starlette.testclient import TestClient

from core.events import BUS

# 与 server 同源加载项目根 .env（server/__init__ 亦加载；此处显式保证 skip 判定一致）
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:
    pass

HAS_LLM_KEY = bool((os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip())

pytestmark = pytest.mark.skipif(not HAS_LLM_KEY, reason="无 LLM_API_KEY/OPENAI_API_KEY，跳过真实 LLM 联调")

RUN_PAYLOAD: dict = {
    "title": "live run 只读侦察（127.0.0.1）",
    "task_brief": (
        "对授权目标 127.0.0.1 做只读端口侦察。"
        "第一动作必须调用 run_recon_tool：nmap 扫描 127.0.0.1 的 22/80 端口"
        "（tool_name=\"nmap\", args_json='{\"target\":\"127.0.0.1\",\"ports\":\"22,80\"}'）。"
        "把输出中的事实写入 blackboard，然后调用 complete_task 提交结论。"
        "只读，不要扫描范围外目标。"
    ),
    "targets": ["127.0.0.1"],
    "scope": {
        "allowed_targets": ["127.0.0.1"],
        "forbidden_actions": ["dos"],
        "max_intensity": "passive",
    },
    "settings": {"max_rounds": 3, "wallclock_seconds": 90, "approval_timeout_seconds": 20},
}

TERMINAL = frozenset({"completed", "failed", "stopped"})


@pytest.fixture()
def app_client():
    from server.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield app.state.state, c


def _post(client: TestClient, method: str, payload: dict) -> object:
    return client.post(
        f"/api/{method}", json={"rpcId": "live-test", "method": method, "payload": payload}
    )


def test_run_live_executes_recon_with_approval_and_replay(app_client) -> None:
    """真实 run：LLM 驱动工具调用 + respond 裁决 + completed + mux 回放。"""
    state, client = app_client
    t0 = time.time()

    # 发起真实 run（mux 订阅非必需：事件经 BUS 落 SessionRec，结束回放另行验证）
    res = _post(client, "run", dict(RUN_PAYLOAD))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True, body
    engagement_id = body["result"]["engagementId"]
    session_id = body["result"]["sessionIds"][0]

    # managed 真实会话：steer 应可用（指令作为 user 事件注入）
    steer = _post(client, "steer", {"sessionId": session_id, "instruction": "严格按任务书执行 nmap 后收尾"})
    assert steer.json() == {"ok": True, "result": {}}

    # 轮询 in-process 编排面：裁决出现的审批 + 等待终态（无 WS 阻塞，天然防挂起）
    approvals_granted: list[str] = []
    deadline = t0 + 100
    while time.time() < deadline:
        rec = state.sessions.get(session_id)
        if rec is None:
            time.sleep(0.3)
            continue
        for rpc_id in list(rec.approvals):
            if rpc_id in approvals_granted:
                continue
            resp = _post(client, "respond", {"rpcId": rpc_id, "decision": "allow", "comment": "只读侦察放行"})
            assert resp.json() == {"ok": True, "result": {}}
            approvals_granted.append(rpc_id)
        if rec.status in TERMINAL:
            break
        time.sleep(0.5)

    # 终态：completed（真实收敛，非占位挂起）
    rec = state.sessions[session_id]
    assert rec.status == "completed", f"run 未收敛到 completed（实际 {rec.status}）"
    assert approvals_granted, "run 过程中未出现可裁决的审批请求"

    # 事件流落账：LLM 驱动的工具调用 + nmap 真实执行成功 + 消息 + projection
    events = rec.events
    kinds = [(e["type"], e.get("data") or {}) for e in events]
    assert any(t == "message" and d.get("role") == "system" for t, d in kinds), "缺会话启动 system 事件"
    assert any(t == "message" and d.get("role") == "user" for t, d in kinds), "steer user 事件未落账"
    assert any(t == "tool/call" for t, _ in kinds), "缺 LLM 驱动的工具调用事件（tool/call）"
    assert any(
        t == "tool/output" and d.get("tool") == "nmap" and d.get("ok")
        for t, d in kinds
    ), "缺 nmap 真实执行成功输出（tool/output ok=True）"
    assert any(t == "message" and d.get("role") == "assistant" for t, d in kinds), "缺 assistant 消息事件"
    assert rec.projections, "缺 projection 帧"
    assert rec.projections[-1].get("values", {}).get("report", {}).get("status") == "completed"

    # 审批裁决闭环：BUS 上同一会话有 approval/requested + approval/resolved（mux 帧同源）
    bus_kinds = [e["kind"] for e in BUS.history(session_id)]
    assert "approval/requested" in bus_kinds and "approval/resolved" in bus_kinds

    # run 结束后 mux 重连可回放该会话事件（tool/call 出现即回放含事件流）
    replay_found = False
    with client.websocket_connect("/api/events.mux") as ws:
        for _ in range(4000):
            frame = ws.receive_json()
            if (frame.get("sessionId") == session_id and frame.get("type") == "session/event"
                    and frame["data"].get("type") == "tool/call"):
                replay_found = True
                break
    assert replay_found, "run 结束后 mux 回放未包含 tool/call 事件"

    # 联调留痕：消耗口径 = rounds / llm_calls / 审批次数
    rounds = max((p.get("values", {}).get("progress", {}).get("round", 0)
                  for p in rec.projections), default=0)
    llm_calls = max((p.get("values", {}).get("progress", {}).get("llm_calls", 0)
                     for p in rec.projections), default=0)
    print(f"\n[live-run] engagement={engagement_id} session={session_id} "
          f"status={rec.status} rounds={rounds} llm_calls={llm_calls} "
          f"approvals={len(approvals_granted)} events={len(rec.events)}")
    assert 1 <= rounds <= 3, f"轮次越界：{rounds}"
    assert llm_calls >= 1
