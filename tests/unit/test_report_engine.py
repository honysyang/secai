"""R5 报告引擎单测 —— mock TargetProfile + events 纯函数投影（无 LLM）。

覆盖：generate_report（findings/复现步骤/证据链）、classify_severity、
summarize_deadends（负面章节 + overturn_condition）、find_cross_target_chains
（A 目标凭据打 B 目标：credential_surface / evidence_reuse 两通道）、
generate_engagement_report（章节结构与模板一致）、render_report_yaml。

运行：.venv/bin/python -m pytest tests/unit/test_report_engine.py -v
"""
from __future__ import annotations

import pytest

from pentest.deadends import DeadEnd
from pentest.hypothesis import Hypothesis
from pentest.target_profile import (
    AttackChain,
    AttackSurface,
    CredentialLead,
    Identity,
    TargetProfile,
)
from profiles.practical_pentest.report import (
    DEFAULT_REPORT_TEMPLATE,
    EngagementSnapshot,
    classify_severity,
    extract_evidence_chain,
    extract_steps,
    find_cross_target_chains,
    generate_engagement_report,
    generate_report,
    load_report_template,
    render_report_yaml,
    summarize_deadends,
    validate_severity,
)
from profiles.practical_pentest.report.engine import SEVERITY_TO_CVSS

T0 = "2026-09-05T08:00:00Z"


def make_event(event_id: str, seq: int, type_: str, data: dict, created_at: str = T0) -> dict:
    return {"event_id": event_id, "seq": seq, "type": type_, "data": data, "created_at": created_at}


def make_hypothesis(hid: str = "h1", linked: list[str] | None = None, statement: str = "目标服务存在已知漏洞") -> Hypothesis:
    return Hypothesis(hypothesis_id=hid, statement=statement, status="validated",
                      linked_actions=list(linked or []))


def make_chain(chain_id: str = "c1", steps: list[str] | None = None,
               severity: str = "high", evidence_refs: list[str] | None = None) -> AttackChain:
    return AttackChain(chain_id=chain_id, steps=list(steps or ["h1"]),
                       impact="远程代码执行", evidence_refs=list(evidence_refs or []),
                       severity=severity)  # type: ignore[arg-type]


def make_dead(did: str = "d1") -> DeadEnd:
    return DeadEnd(dead_end_id=did, path_description="SQL注入: 登录表单 → sqlmap → 无注入点",
                   hypothesis_id="h9", falsified_at_step=3, falsified_at="2026-09-05T08:10:00Z",
                   evidence_snapshot="sqlmap --level 5 未发现注入点",
                   falsification_method="sqlmap", overturn_condition="banner contains OpenSSH_9")


def make_profile(target_id: str = "A", **kwargs) -> TargetProfile:
    defaults = dict(identity=Identity(ip="10.0.0.1", hostname="edge.example"),
                    scope_ref="scope-1")
    defaults.update(kwargs)
    return TargetProfile(target_id=target_id, **defaults)


# ---------------------------------------------------------------------------
# generate_report：findings / 复现步骤 / 证据链
# ---------------------------------------------------------------------------
def test_generate_report_one_finding_per_validated_chain() -> None:
    """每个 validated 攻击链投影一条发现详情，字段齐全。"""
    h = make_hypothesis(statement="8080 Jetty 存在 CVE-2021-XXXX 反序列化 RCE")
    p = make_profile(hypotheses=[h], validated=[make_chain(severity="critical")])
    rep = generate_report(p, [])
    assert len(rep.findings) == 1
    f = rep.findings[0]
    assert f.finding_id == "F-c1"
    assert f.target_id == "A"
    assert f.title == "8080 Jetty 存在 CVE-2021-XXXX 反序列化 RCE"
    assert f.severity == "critical"
    assert f.cvss_base_score == SEVERITY_TO_CVSS["critical"]
    assert f.affected == "edge.example 10.0.0.1"
    assert f.remediation  # 按严重度通用修复指引非空
    assert rep.dead_end_summary == rep.excluded_attack_surfaces  # 负面章节别名


def test_reproduce_steps_follow_linked_actions_in_seq_order() -> None:
    """复现步骤沿链步假设的 linked_actions 事件按 seq 升序投影，动作含 command。"""
    events = [
        make_event("e1", 1, "tool/exec", {"tool": "nmap", "command": "nmap -sV -p 8080 10.0.0.1"}),
        make_event("e2", 3, "tool/output", {"tool": "nmap", "output": "8080 open http-proxy Jetty"}),
        make_event("e3", 2, "exploit/run", {"tool": "exploit", "command": "python3 exp.py --url http://10.0.0.1:8080",
                                            "summary": "RCE confirmed"}),
    ]
    h = make_hypothesis(linked=["e3", "e1", "e2"])  # 乱序 linked_actions → 应按时序
    p = make_profile(hypotheses=[h], validated=[make_chain()])
    steps = extract_steps(p, p.validated[0], events)
    assert [s.action for s in steps] == [
        "nmap -sV -p 8080 10.0.0.1",
        "python3 exp.py --url http://10.0.0.1:8080",
        "调用工具 nmap",
    ]
    assert steps[2].evidence == "8080 open http-proxy Jetty"
    assert [s.event_id for s in steps] == ["e1", "e3", "e2"]


def test_reproduce_steps_missing_hypothesis_or_events_keeps_placeholder() -> None:
    """链步假设缺失 / 无关联事件时给占位步骤，不臆造动作、不抛错。"""
    h = make_hypothesis(hid="h1", linked=[], statement="无事件假设")
    p = make_profile(hypotheses=[h], validated=[make_chain(steps=["h1", "h-missing"])])
    steps = extract_steps(p, p.validated[0], [])
    assert len(steps) == 2
    assert steps[0].event_id is None
    assert "不可复现" in steps[0].evidence
    assert "h-missing" in steps[1].intent


def test_evidence_chain_from_evidence_refs_marks_missing_events() -> None:
    """证据链由 chain.evidence_refs 投影；日志缺事件时给占位节点。"""
    events = [make_event("e2", 2, "tool/output", {"summary": "Jetty 9.4.39 识别"})]
    chain = make_chain(evidence_refs=["e2", "e-absent"])
    out = extract_evidence_chain(chain, events)
    assert len(out) == 2
    assert out[0].event_id == "e2" and "Jetty" in out[0].summary
    assert out[1].event_id == "e-absent"
    assert "缺失" in out[1].summary


# ---------------------------------------------------------------------------
# summarize_deadends：负面发现（已排除攻击面）+ overturn_condition
# ---------------------------------------------------------------------------
def test_summarize_deadends_is_negative_findings_with_overturn_condition() -> None:
    """deadEnd 汇总为已排除攻击面；overturn_condition 原样携带供人工推翻判断。"""
    dead = make_dead()
    summary = summarize_deadends([dead])
    assert len(summary) == 1
    item = summary[0]
    assert item.dead_end_id == "d1"
    assert item.path_description.startswith("SQL注入")
    assert item.overturn_condition == "banner contains OpenSSH_9"
    assert item.overturned is False
    assert item.evidence_snapshot.startswith("sqlmap")


def test_summarize_deadends_overturned_when_new_facts_satisfy_condition() -> None:
    """新事实命中 overturn_condition → is_overturned_by 纯函数判 overturned=True。"""
    summary = summarize_deadends([make_dead()], new_facts=["服务指纹 OpenSSH_9.6p1"])
    assert summary[0].overturned is True
    # 无关新事实不推翻
    summary2 = summarize_deadends([make_dead()], new_facts=["nginx 1.24"])
    assert summary2[0].overturned is False


def test_report_dict_contains_negative_findings_section() -> None:
    """单目标报告 to_dict 含负面发现（已排除攻击面）章节与覆盖率。"""
    h = make_hypothesis()
    p = make_profile(hypotheses=[h], validated=[make_chain()], dead_ends=[make_dead()],
                     coverage={"recon/": {"done": 4, "total": 5}})
    rep = generate_report(p, [])
    payload = rep.to_dict()
    excluded = payload["excluded_attack_surfaces"]
    assert len(excluded) == 1
    assert excluded[0]["overturn_condition"] == "banner contains OpenSSH_9"
    assert payload["coverage_ratio"] == 0.8


# ---------------------------------------------------------------------------
# classify_severity / CVSS 投影
# ---------------------------------------------------------------------------
def test_classify_severity_cvss_boundaries() -> None:
    """CVSS 数值 → 等级边界：0/0.1/3.9/4.0/6.9/7.0/8.9/9.0/10。"""
    assert classify_severity(0.0) == "info"
    assert classify_severity(0.1) == "low"
    assert classify_severity(3.9) == "low"
    assert classify_severity(4.0) == "medium"
    assert classify_severity(6.9) == "medium"
    assert classify_severity(7.0) == "high"
    assert classify_severity(8.9) == "high"
    assert classify_severity(9.0) == "critical"
    assert classify_severity(10.0) == "critical"
    assert classify_severity(-1) == "info"  # 越界夹紧
    assert validate_severity("critical") == "critical"
    with pytest.raises(ValueError):
        validate_severity("catastrophic")


def test_empty_profile_report_does_not_crash() -> None:
    """空 profile（无假设/链/死路）仍产出结构完整报告。"""
    rep = generate_report(make_profile(), [])
    assert rep.findings == []
    assert rep.excluded_attack_surfaces == []
    assert rep.coverage_ratio == 0.0
    assert rep.target_label == "edge.example 10.0.0.1"


# ---------------------------------------------------------------------------
# find_cross_target_chains：A 目标凭据打 B 目标
# ---------------------------------------------------------------------------
def make_cred(username: str = "alice", password: str = "P@ss", source: str = "leak") -> CredentialLead:
    return CredentialLead(source=source, username=username, password=password, confidence="confirmed")


def test_cross_target_chain_credential_surface_reuse() -> None:
    """A 的凭据同值出现在 B 攻击面凭据表（credential_surface 通道）。"""
    p_a = make_profile(target_id="A", attack_surface=AttackSurface(credentials=[make_cred()]))
    p_b = make_profile(target_id="B", identity=Identity(ip="10.0.0.2"), scope_ref="scope-1",
                       attack_surface=AttackSurface(credentials=[make_cred()]))
    chains = find_cross_target_chains([p_a, p_b], events=[])
    assert len(chains) == 1
    c = chains[0]
    assert c.source_target_id == "A" and c.target_target_id == "B"
    assert c.username == "alice"
    assert c.credential_source == "leak"
    assert c.match == "credential_surface"
    assert c.chain_id == "X-A-B-1"
    assert c.severity == "high"


def test_cross_target_chain_evidence_reuse_detected_in_chain_events() -> None:
    """B 的 validated 链关联事件含 A 凭据 username（evidence_reuse 通道）。"""
    p_a = make_profile(target_id="A", attack_surface=AttackSurface(credentials=[make_cred()]))
    h_b = make_hypothesis(hid="hb1", linked=["b1"], statement="用 alice 凭据横移 B")
    chain_b = make_chain(chain_id="cB", steps=["hb1"], evidence_refs=["b1"])
    p_b = make_profile(target_id="B", identity=Identity(ip="10.0.0.2"), scope_ref="scope-1",
                       hypotheses=[h_b], validated=[chain_b])
    ev_b = [make_event("b1", 1, "tool/output",
                       {"tool": "netexec", "output": "[+] alice:P@ss (Pwn3d!)"}, created_at=T0)]
    chains = find_cross_target_chains([p_a, p_b], events=ev_b)
    assert len(chains) == 1
    c = chains[0]
    assert c.match == "evidence_reuse"
    assert c.related_chain_id == "cB"
    assert "netexec" not in c.note and "已验证链 cB" in c.note


def test_cross_target_chain_no_false_positive_without_source_or_username_match() -> None:
    """凭据 source 为空不参与扫描；无同凭据/无证据命中不产生跨链。"""
    cred_nosource = CredentialLead(source="leak", username="root", password="root")
    cred_nosource.source = ""  # source 字段为空 → 不参与
    p_a = make_profile(target_id="A", attack_surface=AttackSurface(credentials=[cred_nosource]))
    p_b = make_profile(target_id="B", identity=Identity(ip="10.0.0.2"), scope_ref="scope-1")
    # 密码不同 → 不算同一凭据复用
    p_c = make_profile(target_id="C", identity=Identity(ip="10.0.0.3"), scope_ref="scope-1",
                       attack_surface=AttackSurface(
                           credentials=[CredentialLead(source="guess", username="root", password="other")]))
    assert find_cross_target_chains([p_a, p_b, p_c], events=[]) == []
    assert find_cross_target_chains([p_b, p_c], events=[]) == []


# ---------------------------------------------------------------------------
# generate_engagement_report + YAML 渲染
# ---------------------------------------------------------------------------
def test_engagement_report_sections_follow_template_order() -> None:
    """Engagement 级报告顶层章节与 YAML 模板一致（负面发现位于 findings 之后）。"""
    events = [
        make_event("e1", 1, "tool/exec", {"command": "nmap -sV 10.0.0.1"}),
        make_event("e2", 2, "tool/output", {"summary": "CVE 命中，RCE 确认"}),
    ]
    h = make_hypothesis(linked=["e1", "e2"])
    p = make_profile(hypotheses=[h], validated=[make_chain(severity="high")],
                     dead_ends=[make_dead()], coverage={"recon/": {"done": 4, "total": 5}})
    empty = make_profile(target_id="B", identity=Identity(ip="10.0.0.2"), scope_ref="scope-1")
    eng = generate_engagement_report(
        [EngagementSnapshot(engagement_id="eng1", client="ACME", authorization_ref="AUTH-2026-001",
                            profiles=[p, empty], events=events)],
        generated_at="2026-09-05T09:00:00Z",
    )
    payload = eng.to_dict()
    template = load_report_template(DEFAULT_REPORT_TEMPLATE)
    # 结构合同：报告顶层章节键 = 模板顶层键（键序一致）
    assert list(payload) == list(template)
    section_keys = [k for k in payload if k != "report_version"]
    assert section_keys == ["cover", "executive_summary", "attack_surface_map", "findings",
                            "excluded_attack_surfaces", "cross_target_chains", "methodology",
                            "limitations"]
    # 负面发现（已排除攻击面）位于 findings 之后
    assert payload["findings"] and payload["excluded_attack_surfaces"]
    assert payload["cover"]["ai_assisted_declaration"]
    assert payload["cover"]["scope_refs"] == ["scope-1"]
    summary = payload["executive_summary"]
    assert summary["target_count"] == 2
    assert summary["validated_chain_count"] == 1
    assert summary["severity_breakdown"]["high"] == 1
    assert summary["dead_end_count"] == 1
    assert summary["average_coverage_ratio"] == 0.4
    assert summary["cross_target_chain_count"] == 0
    assert payload["methodology"]["stages"]


def test_render_report_yaml_is_deterministic_and_embeds_negative_section() -> None:
    """渲染 YAML：同一输入两次输出相同；负面章节在文本中位于 findings 之后。"""
    h = make_hypothesis(linked=["e1"])
    p = make_profile(hypotheses=[h], validated=[make_chain()], dead_ends=[make_dead()])
    events = [make_event("e1", 1, "tool/exec", {"command": "curl /admin"})]
    eng = generate_engagement_report(
        [EngagementSnapshot(engagement_id="eng1", profiles=[p], events=events)],
        generated_at="2026-09-05T09:00:00Z",
    )
    text = render_report_yaml(eng)
    assert text == render_report_yaml(eng)  # 纯函数稳定
    assert text.index("findings:") < text.index("excluded_attack_surfaces:")
    assert "overturn_condition: banner contains OpenSSH_9" in text
    assert "ai_assisted_declaration:" in text


def test_engagement_report_pure_with_identical_input() -> None:
    """相同输入（含显式 generated_at）→ 两份报告完全一致（快照回放前提）。"""
    p = make_profile(validated=[make_chain()], dead_ends=[make_dead()])
    kwargs = dict(engagements=[EngagementSnapshot(engagement_id="eng1", profiles=[p], events=[])],
                  generated_at="2026-09-05T09:00:00Z")
    a = generate_engagement_report(**kwargs).to_dict()
    b = generate_engagement_report(**kwargs).to_dict()
    assert a == b


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
