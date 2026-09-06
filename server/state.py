"""AppState —— server 编排面状态：SessionManager 桥接 + BUS 订阅 + WS 扇出 + 审批注册。

单一共享对象挂 app.state.state（create_app 组装），api.py / ws.py 经
`request.app.state.state` 读写。职责：

- 编排面：engagements（任务书）/ sessions（目标会话行头/事件历史/队列/投影）登记
  与查询，供 /api/targets /api/engagements /api/run 直接投影；
- 事件源桥接：SessionManager(shared_bus=BUS) 与 demo fixture 都向 core/events BUS
  发射事件；本模块订阅 BUS，把「已知会话」的事件转换为前端 SessionEvent 落进
  SessionRec.events（断线重连回放基线）并即时扇出 MuxFrame session/event；
- 帧扇出：Hub 维护 mux/host 两主题的下行订阅队列（纯下行，客户端消息由 ws.py
  以 1008 拒绝）；subscribed/queue/projection/approval 等结构化帧由本模块直接广播；
- demo ticker：running 会话（sess-a-demo）周期性活动帧（事件 + projection），
  无 LLM key 离线也能看到运行态直播。

线程/循环约束：全部在 uvicorn 事件循环内使用（BUS.emit 为同步调用，扇出仅
put_nowait）；start_target 由 /api/run 在请求协程内调用（必须处于运行中的循环）。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.events import BUS
from harness.session_manager import SessionContext, SessionManager
from server.artifacts import ArtifactsStore

# 产物落盘根目录（data/ 已被 .gitignore 忽略；可用 SECAI_ARTIFACTS_DIR 覆盖）
DEFAULT_ARTIFACTS_DIR = "data/artifacts"

TERMINAL_STATUSES = frozenset({"completed", "failed", "stopped"})
# 会话状态词表（对齐前端 SessionStatus 子集）
ACTIVE_STATUSES = frozenset({"running", "awaiting_approval", "idle"})

# demo ticker 周期（秒）；离线演示 running 会话的活跃频率，可用 env 覆盖
DEMO_TICK_SECONDS = float(os.getenv("SECAI_DEMO_TICK_SECONDS", "5"))


def now_iso() -> str:
    """UTC ISO-8601（毫秒、Z 后缀），与前端 demo.ts iso() 同构。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _json(frame: dict[str, Any]) -> str:
    return json.dumps(frame, ensure_ascii=False)


class Hub:
    """WS 下行扇出：topic（mux/host）→ 订阅队列集合。广播只 put 预序列化文本。"""

    def __init__(self, maxsize: int = 2048) -> None:
        self._maxsize = maxsize
        self._subs: dict[str, set[asyncio.Queue[str]]] = {"mux": set(), "host": set()}

    def register(self, topic: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._maxsize)
        self._subs.setdefault(topic, set()).add(queue)
        return queue

    def unregister(self, topic: str, queue: asyncio.Queue[str]) -> None:
        self._subs.setdefault(topic, set()).discard(queue)

    def broadcast(self, topic: str, frame: dict[str, Any]) -> None:
        """向该主题全部订阅者投放一帧；慢消费者丢帧不阻塞（事件已落 SessionRec 可回放）。"""
        text = _json(frame)
        for queue in list(self._subs.get(topic, ())):
            try:
                queue.put_nowait(text)
            except Exception:
                continue

    @property
    def subscriber_count(self) -> int:
        return sum(len(qs) for qs in self._subs.values())


@dataclass
class EngagementRec:
    """任务书（engagement）编排记录。"""

    engagement_id: str
    title: str
    created_at: str
    updated_at: str
    session_ids: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, str]:
        return {
            "engagementId": self.engagement_id,
            "title": self.title,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass
class SessionRec:
    """目标会话编排记录：行头 + 回放历史（events/queue/projections/approvals）。"""

    session_id: str
    target: str
    engagement_id: str
    status: str
    created_at: str
    updated_at: str
    events: list[dict[str, Any]] = field(default_factory=list)  # SessionEvent shape
    queue: list[dict[str, Any]] = field(default_factory=list)  # QueueItem shape
    projections: list[dict[str, Any]] = field(default_factory=list)  # [{seq, values}]
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)  # rpcId → ApprovalRequest
    managed: bool = False          # True = SessionManager 驱动的真实 run 会话（区别于 demo）
    steer_queue: Any = None        # asyncio.Queue[str] | None：run runner 的 steer 注入通道
    _event_seq: int = 0
    _projection_seq: int = -1

    def header(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "target": self.target,
            "status": self.status,
            "engagementId": self.engagement_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class AppState:
    def __init__(self) -> None:
        self.hub = Hub()
        self.engagements: dict[str, EngagementRec] = {}
        self.sessions: dict[str, SessionRec] = {}
        # 报告桥接：completed 会话 → 纯函数报告引擎输入（fixture 注册，真实引擎接入后同源）
        self.report_profiles: dict[str, Any] = {}
        # 报告产物落盘：api_report「生成即存」hook 的目标存储（目录惰性创建）
        artifacts_root = os.getenv("SECAI_ARTIFACTS_DIR", DEFAULT_ARTIFACTS_DIR)
        self.artifacts = ArtifactsStore(artifacts_root)
        # 全局账本：资产/风险清单（host/assets、host/risks 帧数据源 + /api/{assets,risks}）
        # assets 键 = 归一化目标；risks 键 = 风险 id。均为「最新全量」语义（帧整表投递）
        self.ledger_assets: dict[str, dict[str, Any]] = {}
        self.ledger_risks: dict[str, dict[str, Any]] = {}
        # SessionManager 以共享 BUS 桥接（事件按 session_id 键隔离，seq 各自计数）
        self.manager = SessionManager(shared_bus=BUS)
        self._unsubscribe = BUS.subscribe(self._on_bus_event)
        self._tasks: set[asyncio.Task] = set()
        self._ticker: asyncio.Task | None = None
        self._demo_ticks = 0
        self._closed = False
        # 审批等待者：rpcId → asyncio.Future（runner 等待 /api/respond 裁决的唤醒通道）
        self._approval_waiters: dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------------
    # 任务书 / 会话登记
    # ------------------------------------------------------------------
    def create_engagement(self, title: str, *, engagement_id: str | None = None) -> EngagementRec:
        eng_id = engagement_id or f"eng-{uuid.uuid4().hex[:8]}"
        now = now_iso()
        eng = EngagementRec(engagement_id=eng_id, title=title, created_at=now, updated_at=now)
        self.engagements[eng_id] = eng
        self.hub.broadcast("host", {"type": "host/engagement-changed", "engagementId": eng_id})
        return eng

    def add_session(
        self,
        session_id: str,
        target: str,
        engagement_id: str,
        status: str,
        *,
        managed: bool = False,
    ) -> SessionRec:
        now = now_iso()
        rec = SessionRec(
            session_id=session_id,
            target=target,
            engagement_id=engagement_id,
            status=status,
            created_at=now,
            updated_at=now,
            managed=managed,
        )
        self.sessions[session_id] = rec
        if engagement_id in self.engagements:
            eng = self.engagements[engagement_id]
            if session_id not in eng.session_ids:
                eng.session_ids.append(session_id)
                eng.updated_at = now
        # 向在线订阅者补投（新会话 = host 登记 + mux 快照回放）
        self.hub.broadcast("host", {"type": "host/session-added", "session": rec.header()})
        for frame in self.session_frames(rec):
            self.hub.broadcast("mux", frame)
        return rec

    def set_status(self, session_id: str, status: str) -> SessionRec:
        rec = self._require_session(session_id)
        if rec.status == status:
            return rec
        rec.status = status
        rec.updated_at = now_iso()
        self.hub.broadcast("host", {"type": "host/session-status", "sessionId": session_id, "status": status})
        self._touch_engagement(rec.engagement_id)
        return rec

    def remove_session(self, session_id: str) -> None:
        rec = self.sessions.pop(session_id, None)
        if rec is None:
            return
        self.hub.broadcast("host", {"type": "host/session-removed", "sessionId": session_id})
        self._touch_engagement(rec.engagement_id)

    def _touch_engagement(self, engagement_id: str) -> None:
        eng = self.engagements.get(engagement_id)
        if eng is None:
            return
        eng.updated_at = now_iso()
        self.engagement_status(eng)
        self.hub.broadcast("host", {"type": "host/engagement-changed", "engagementId": engagement_id})

    def engagement_status(self, eng: EngagementRec) -> str:
        """running：存在活跃会话；completed：全部终态且至少一个 completed；否则 failed。"""
        if not eng.session_ids:
            return "completed"
        statuses = [self.sessions[sid].status for sid in eng.session_ids if sid in self.sessions]
        if any(s not in TERMINAL_STATUSES for s in statuses):
            return "running"
        return "completed" if any(s == "completed" for s in statuses) else "failed"

    def _require_session(self, session_id: str) -> SessionRec:
        try:
            return self.sessions[session_id]
        except KeyError:
            raise KeyError(f"未知 session: {session_id}") from None

    # ------------------------------------------------------------------
    # 事件 / 结构化帧写入（单一数据源 = BUS 事件 + 直发结构化帧）
    # ------------------------------------------------------------------
    def emit_event(self, session_id: str, kind: str, **data: Any) -> str:
        """向 BUS 发射会话事件；订阅回调负责落 SessionRec.events 与扇出。返回 eventId。"""
        event = BUS.emit(session_id, kind, **data)
        return f"ev-{session_id}-{event['seq']}"

    def _on_bus_event(self, event: dict[str, Any]) -> None:
        """BUS → SessionEvent 转换：仅处理已知会话；approval/* 走专用帧（不重复成事件）。"""
        session_id = event["task_id"]
        rec = self.sessions.get(session_id)
        if rec is None:
            return
        kind = event["kind"]
        if kind.startswith("approval/"):
            return
        ev: dict[str, Any] = {
            "eventId": f"ev-{session_id}-{event['seq']}",
            "seq": event["seq"],
            "type": kind,
            "data": event.get("data") or {},
            "createdAt": iso_from_ts(event["ts"]),
        }
        rec.events.append(ev)
        rec._event_seq = event["seq"]
        self.hub.broadcast("mux", {"type": "session/event", "sessionId": session_id, "data": ev})

    def push_queue(self, session_id: str, items: list[dict[str, Any]]) -> None:
        rec = self._require_session(session_id)
        rec.queue = list(items)
        self.hub.broadcast("mux", {"type": "session/queue", "sessionId": session_id, "items": rec.queue})

    def push_projection(self, session_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """projection 帧（seq 单调）；历史留档供断线回放。"""
        rec = self._require_session(session_id)
        rec._projection_seq += 1
        proj = {"seq": rec._projection_seq, "values": dict(values)}
        rec.projections.append(proj)
        self.hub.broadcast(
            "mux", {"type": "session/projection", "sessionId": session_id, **proj}
        )
        return proj

    def request_approval(
        self,
        session_id: str,
        rpc_id: str,
        *,
        action: str,
        description: str,
        detail: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        """落一条 pending 审批并下发 approval/requested 帧（响应可 respond 回响）。"""
        rec = self._require_session(session_id)
        payload: dict[str, Any] = {
            "action": action,
            "description": description,
            "createdAt": created_at or now_iso(),
        }
        if detail:
            payload["detail"] = detail
        rec.approvals[rpc_id] = payload
        self.hub.broadcast(
            "mux",
            {"type": "approval/requested", "sessionId": session_id, "rpcId": rpc_id, "payload": payload},
        )
        BUS.emit(session_id, "approval/requested", rpc_id=rpc_id, action=action, description=description)

    async def wait_for_approval(
        self, session_id: str, rpc_id: str, *, timeout: float
    ) -> str | None:
        """等待该 rpcId 被 /api/respond 裁决：allow/deny；超时返回 None（未裁决）。

        供真实 run runner 在工具审批门内 await：respond 路径 resolve_approval 会
        以 decision 唤醒对应 future（超时由本方法兜底返回 None → 调用方自动拒绝）。
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._approval_waiters[rpc_id] = future
        try:
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                return None
        finally:
            self._approval_waiters.pop(rpc_id, None)

    def resolve_approval(
        self, rpc_id: str, decision: str, comment: str | None = None
    ) -> dict[str, Any] | None:
        """裁决一条 pending 审批：回响 rpcId、写 approval 记录、唤醒等待者、会话转 running。"""
        target: SessionRec | None = None
        for rec in self.sessions.values():
            if rpc_id in rec.approvals:
                target = rec
                break
        if target is None:
            return None
        payload: dict[str, Any] = {"decision": decision, "decidedAt": now_iso()}
        if comment is not None:
            payload["comment"] = comment
        del target.approvals[rpc_id]
        self.hub.broadcast(
            "mux",
            {"type": "approval/resolved", "sessionId": target.session_id, "rpcId": rpc_id, "payload": payload},
        )
        BUS.emit(
            target.session_id,
            "approval/resolved",
            rpc_id=rpc_id,
            decision=decision,
            comment=comment or "",
            decided_at=payload["decidedAt"],
        )
        waiter = self._approval_waiters.get(rpc_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(decision)
        self.set_status(target.session_id, "running")
        return payload

    # ------------------------------------------------------------------
    # 全局账本（资产 / 风险 / 报告）
    # ------------------------------------------------------------------
    def commit_assets(self, entries: list[dict[str, Any]]) -> None:
        """合并上报资产条目（runner 侦察产出调用）：同 id 覆盖、首见时间保留；
        每次合并后广播 host/assets 整表。entry 需含 id/target；sessionId 存在时
        服务端补全 engagementId 与 firstSeen/lastSeen 时间戳。"""
        changed = False
        for entry in entries:
            asset_id = str(entry.get("id") or "")
            if not asset_id:
                continue
            now = now_iso()
            incoming = dict(entry)
            rec = self.sessions.get(str(incoming.get("sessionId") or ""))
            if rec is not None:
                incoming["engagementId"] = rec.engagement_id
            incoming.setdefault("firstSeenAt", now)
            incoming["lastSeenAt"] = now
            existing = self.ledger_assets.get(asset_id)
            if existing is None:
                self.ledger_assets[asset_id] = incoming
                changed = True
                continue
            # 合并：可变字段以新值覆盖（空值不抹旧），services/ports 取并集
            merged = dict(existing)
            merged.update({k: v for k, v in incoming.items() if v not in (None, [], "")})
            for list_key in ("services", "ports"):
                combined = list(
                    dict.fromkeys([*(existing.get(list_key) or []), *(incoming.get(list_key) or [])])
                )
                merged[list_key] = combined
            merged["firstSeenAt"] = existing.get("firstSeenAt") or incoming.get("firstSeenAt") or now
            self.ledger_assets[asset_id] = merged
            changed = True
        if changed:
            self.broadcast_assets()

    def refresh_risks(self, engagement_id: str, findings: list[Any]) -> None:
        """报告引擎 findings → 风险账本（api_report 生成 hook 调用）：
        finding（dataclass/dict 容忍）→ 风险条目（id = engagement+title 哈希，重复
        生成不重复计数）；已有条目人工处理状态保留。落账后广播 host/risks 整表。"""
        import dataclasses
        import hashlib

        for finding in findings:
            if dataclasses.is_dataclass(finding) and not isinstance(finding, type):
                data = dataclasses.asdict(finding)
            elif isinstance(finding, dict):
                data = finding
            else:
                continue
            title = str(data.get("title") or "").strip()
            if not title:
                continue
            digest = hashlib.sha1(f"{engagement_id}:{title}".encode("utf-8")).hexdigest()[:12]
            risk_id = f"risk-{digest}"
            affected = str(data.get("affected") or "")
            existing = self.ledger_risks.get(risk_id)
            evidence_raw = data.get("evidence_chain") or []
            evidence: list[str] = []
            for item in evidence_raw[:4]:
                if isinstance(item, dict):
                    text = str(item.get("summary") or item.get("detail") or item.get("command") or "")
                else:
                    text = str(item)
                if text:
                    evidence.append(text)
            entry: dict[str, Any] = {
                "id": risk_id,
                "title": title,
                "severity": str(data.get("severity") or "info"),
                "target": affected or None,
                "status": (existing or {}).get("status", "open"),
                "discoveredAt": (existing or {}).get("discoveredAt") or now_iso(),
                "engagementId": engagement_id,
                "evidence": evidence or None,
            }
            # 证据兜底：无证据链时取影响摘要
            if not entry["evidence"]:
                impact = str(data.get("impact") or "")
                entry["evidence"] = [impact] if impact else None
            self.ledger_risks[risk_id] = entry
        self.broadcast_risks()

    def set_risk_status(self, risk_id: str, status: str) -> dict[str, Any] | None:
        """人工处理状态流转（open/mitigating/accepted/resolved）；落账后广播整表。"""
        entry = self.ledger_risks.get(risk_id)
        if entry is None:
            return None
        entry["status"] = status
        self.broadcast_risks()
        return dict(entry)

    def broadcast_assets(self) -> None:
        if self.ledger_assets:
            self.hub.broadcast("host", {"type": "host/assets", "assets": self.assets_snapshot()})

    def broadcast_risks(self) -> None:
        if self.ledger_risks:
            self.hub.broadcast("host", {"type": "host/risks", "risks": self.risks_snapshot()})

    def broadcast_reports(self) -> None:
        reports = self.reports_snapshot()
        if reports:
            self.hub.broadcast("host", {"type": "host/reports", "reports": reports})

    def assets_snapshot(self) -> list[dict[str, Any]]:
        """资产全量（首见时间升序）。"""
        rows = [dict(entry) for entry in self.ledger_assets.values()]
        rows.sort(key=lambda e: str(e.get("firstSeenAt") or ""))
        return rows

    def risks_snapshot(self) -> list[dict[str, Any]]:
        """风险全量（severity 权重升序，同级按发现时间）。"""
        weights = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        rows = [dict(entry) for entry in self.ledger_risks.values()]
        rows.sort(key=lambda e: (weights.get(str(e.get("severity")), 9), str(e.get("discoveredAt") or "")))
        return rows

    def reports_snapshot(self) -> list[dict[str, Any]]:
        """报告清单：每个任务书一行（engagement × 产物聚合）。

        status = ready（已有落盘产物）/ drafting（可生成）；findingsCount 与
        severityCounts 从风险账本按 engagementId 聚合（refresh_risks 落账后同源）。
        """
        rows: list[dict[str, Any]] = []
        for eng in self.engagements.values():
            metas = self.artifacts.list(eng.engagement_id)
            status = "ready" if metas else "drafting"
            generated_at = max((m.created_at for m in metas), default=None)
            risks = [r for r in self.ledger_risks.values() if r.get("engagementId") == eng.engagement_id]
            severity_counts: dict[str, int] = {}
            for risk in risks:
                key = str(risk.get("severity") or "info")
                severity_counts[key] = severity_counts.get(key, 0) + 1
            rows.append(
                {
                    "id": f"report-{eng.engagement_id}",
                    "engagementId": eng.engagement_id,
                    "title": eng.title,
                    "status": status,
                    "generatedAt": generated_at,
                    "findingsCount": len(risks),
                    "severityCounts": severity_counts or None,
                    "artifacts": [m.to_dict() for m in metas] or None,
                }
            )
        rows.sort(key=lambda r: str(r.get("engagementId") or ""))
        return rows

    # ------------------------------------------------------------------
    # 帧回放（WS 连接即订阅后先回放整幅快照）
    # ------------------------------------------------------------------
    def session_frames(self, rec: SessionRec) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = [
            {"type": "session/subscribed", "sessionId": rec.session_id, "lastSeq": rec._event_seq}
        ]
        if rec.queue:
            frames.append({"type": "session/queue", "sessionId": rec.session_id, "items": rec.queue})
        for ev in rec.events:
            frames.append({"type": "session/event", "sessionId": rec.session_id, "data": ev})
        for proj in rec.projections:
            frames.append(
                {"type": "session/projection", "sessionId": rec.session_id, "seq": proj["seq"], "values": proj["values"]}
            )
        for rpc_id, payload in rec.approvals.items():
            frames.append(
                {"type": "approval/requested", "sessionId": rec.session_id, "rpcId": rpc_id, "payload": payload}
            )
        return frames

    def mux_replay_frames(self) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        for rec in self.sessions.values():
            frames.extend(self.session_frames(rec))
        return frames

    def host_replay_frames(self) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = [
            {"type": "host/session-added", "session": rec.header()} for rec in self.sessions.values()
        ]
        # 全局清单回放：资产/风险/报告（新连接首屏即有全量，不依赖增量推送）
        if self.ledger_assets:
            frames.append({"type": "host/assets", "assets": self.assets_snapshot()})
        if self.ledger_risks:
            frames.append({"type": "host/risks", "risks": self.risks_snapshot()})
        reports = self.reports_snapshot()
        if reports:
            frames.append({"type": "host/reports", "reports": reports})
        return frames

    # ------------------------------------------------------------------
    # /api/run 桥接：SessionManager 入队（runner 缺省占位；真实执行由调用方注入）
    # ------------------------------------------------------------------
    def start_run(
        self,
        title: str,
        targets: list[str],
        *,
        engagement_id: str | None = None,
        runner: Callable[[SessionContext], Awaitable[Any]] | None = None,
        labels: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        """新建任务书 + 每目标一条 SessionManager 会话（编排面入队，立即返回）。

        - runner：目标执行入口（默认占位挂起 _hold_runner；真实执行传
          harness.runner.pentest_target.run_pentest_target 并在 meta 注入
          bridge/scope/settings 等编排元信息）；
        - labels：target id → 会话展示标签（缺省用 id 本身）；
        - meta：随 start_target 注入每个 SessionContext.meta 的共享编排元信息。
        """
        eng = self.create_engagement(title or "任务书（未命名）", engagement_id=engagement_id)
        run_runner = runner or _hold_runner
        labels = labels or {}
        sids: list[str] = []
        for target in targets:
            session_id = uuid.uuid4().hex[:12]
            self.add_session(
                session_id,
                target=labels.get(target, target),
                engagement_id=eng.engagement_id,
                status="running",
                managed=True,
            )
            try:
                self.manager.start_target(target, run_runner, session_id=session_id, meta=meta)
            except Exception as exc:  # 编排面启动失败 → 会话转 error 状态，不拖垮其余目标
                self.set_status(session_id, "failed")
                self.emit_event(session_id, "message", role="system", content=f"会话启动失败：{exc}")
                continue
            sids.append(session_id)
        return eng.engagement_id, sids

    # ------------------------------------------------------------------
    # 后台任务 / 收尾
    # ------------------------------------------------------------------
    def spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def start_ticker(self) -> None:
        if self._ticker is None or self._ticker.done():
            self._ticker = asyncio.create_task(self._tick_loop())

    async def _tick_loop(self) -> None:
        from server import fixture  # 延迟 import：避免 fixture 双向依赖

        while True:
            await asyncio.sleep(DEMO_TICK_SECONDS)
            if self._closed:
                return
            # 演示 tick 只作用于 demo fixture 会话（sess-*-demo），真实会话不受影响；
            # fixture 未安装（默认）时循环空转，仅作为未来周期任务的挂载点。
            self._demo_ticks += 1
            try:
                fixture.demo_tick(self, self._demo_ticks)
            except Exception:
                continue

    async def close(self) -> None:
        """收尾：停 ticker / 取消后台任务 / SessionManager 统一停止 / 退订 BUS。"""
        if self._closed:
            return
        self._closed = True
        if self._ticker is not None:
            self._ticker.cancel()
            try:
                await self._ticker
            except (asyncio.CancelledError, Exception):
                pass
            self._ticker = None
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                continue
        try:
            await self.manager.close()
        except Exception:
            pass
        self._unsubscribe()


async def _hold_runner(ctx: SessionContext) -> None:
    """占位 runner：start_run 未注入真实执行器时挂起（编排面已入队）。

    真实执行已接入：/api/run（有 LLM Key）会注入
    harness/runner/pentest_target.run_pentest_target（ScopeCheck→审批门→
    只读工具→blackboard→事件扇出），本占位仅供无执行器的编排面兜底。
    """

    await asyncio.Event().wait()


__all__ = [
    "ACTIVE_STATUSES",
    "AppState",
    "DEFAULT_ARTIFACTS_DIR",
    "DEMO_TICK_SECONDS",
    "EngagementRec",
    "Hub",
    "SessionRec",
    "TERMINAL_STATUSES",
    "iso_from_ts",
    "now_iso",
]
