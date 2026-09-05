"""tests/unit/learning 共享 builder —— mock engagement 数据（无 LLM、纯离线）。"""
from __future__ import annotations

from pentest.blackboard.facts import ProjectFact
from pentest.deadends import DeadEnd
from pentest.hypothesis import Hypothesis
from pentest.target_profile import (
    AttackChain,
    AttackSurface,
    Identity,
    PortFinding,
    TargetProfile,
    WebFinding,
)

T0 = "2026-09-05T08:00:00Z"


def make_event(event_id: str, seq: int, type_: str, data: dict, created_at: str = T0) -> dict:
    """构造 query_events 形态事件 dict。"""
    return {"event_id": event_id, "seq": seq, "type": type_, "data": data, "created_at": created_at}


def make_hypothesis(
    hid: str = "h1",
    statement: str = "wordpress 插件存在 RCE",
    *,
    status: str = "pending",
    success_criteria: str = "",
    failure_criteria: str = "",
    linked: list[str] | None = None,
) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hid,
        statement=statement,
        success_criteria=success_criteria,
        failure_criteria=failure_criteria,
        status=status,  # type: ignore[arg-type]
        linked_actions=list(linked or []),
        created_at=T0,
    )


def make_chain(
    chain_id: str = "c1",
    steps: list[str] | None = None,
    severity: str = "high",
    evidence_refs: list[str] | None = None,
    impact: str = "远程代码执行",
) -> AttackChain:
    return AttackChain(
        chain_id=chain_id,
        steps=list(steps or ["h1"]),
        impact=impact,
        evidence_refs=list(evidence_refs or []),
        severity=severity,  # type: ignore[arg-type]
    )


def make_dead(
    did: str = "d1",
    *,
    path_description: str = "SQL注入: 登录表单 → sqlmap → 无注入点",
    overturn_condition: str = "banner contains OpenSSH_9.6",
    falsified_at: str = "2026-09-05T08:10:00Z",
) -> DeadEnd:
    return DeadEnd(
        dead_end_id=did,
        path_description=path_description,
        hypothesis_id="h2",
        falsified_at_step=3,
        falsified_at=falsified_at,
        evidence_snapshot="sqlmap --level 5 未发现注入点",
        falsification_method="sqlmap",
        overturn_condition=overturn_condition,
        engagement_id="e1",
    )


def make_fact(
    fact_key: str = "recon/host/10.0.0.1",
    *,
    body: str = "服务指纹 OpenSSH_9.6p1",
    created_at: str = "2026-09-05T08:20:00Z",
    engagement_id: str = "e1",
) -> ProjectFact:
    return ProjectFact(
        fact_key=fact_key,
        category="recon/",
        body=body,
        links=["recon/port/10.0.0.1/22"],
        confidence="confirmed",
        created_at=created_at,
        created_by="executor",
        engagement_id=engagement_id,
    )


def make_profile(
    target_id: str = "T1",
    *,
    tech: tuple[str, ...] = ("wordpress",),
    os: str = "linux",
    ip: str = "10.0.0.1",
    service: str = "http",
    hypotheses: list[Hypothesis] | None = None,
    validated: list[AttackChain] | None = None,
    dead_ends: list[DeadEnd] | None = None,
) -> TargetProfile:
    surface = AttackSurface(
        ports=[PortFinding(port=80, protocol="tcp", service=service, confidence="confirmed")],
        web=[WebFinding(url=f"http://{ip}/", status_code=200, tech_stack=list(tech))],
    )
    return TargetProfile(
        target_id=target_id,
        identity=Identity(ip=ip, hostname="edge.example", os=os, tech_stack=list(tech)),
        scope_ref="scope-1",
        attack_surface=surface,
        hypotheses=list(hypotheses or []),
        validated=list(validated or []),
        dead_ends=list(dead_ends or []),
    )
