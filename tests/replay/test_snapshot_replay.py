"""R5 tests/replay 首条快照回放 —— 报告投影 + 看板聚合的关键行为回放（无 LLM）。

回放语义：以固定快照（事件日志 / cost_report 文件样本）作为输入，重放
纯函数（报告引擎 / runtime.reporting.write_dashboard），断言输出关键行为
不变——报告章节结构、复现步骤提取、负面发现章节、跨目标链检出、看板聚合。

输入快照为手工固定的 dict（不依赖任何外部服务/模型），输出与期望逐项断言，
任何对纯函数投影语义的破坏都会在此暴露。

运行：.venv/bin/python -m pytest tests/replay/ -v
"""
from __future__ import annotations

import json

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
    EngagementSnapshot,
    find_cross_target_chains,
    generate_engagement_report,
    render_report_yaml,
)
from runtime.reporting import write_dashboard

# ---------------------------------------------------------------------------
# 快照 1：单目标完整 engagement 事件日志（nmap 探测 → RCE 确认链 + SQLi 死路）
# ---------------------------------------------------------------------------
SNAPSHOT_EVENTS: list[dict] = [
    {"event_id": "e-recon-1", "seq": 1, "type": "tool/exec",
     "data": {"tool": "nmap", "command": "nmap -sV -p 8080 10.0.0.1"},
     "created_at": "2026-09-05T08:00:00Z"},
    {"event_id": "e-recon-2", "seq": 2, "type": "tool/output",
     "data": {"tool": "nmap", "output": "8080/tcp open http-proxy Jetty 9.4.39"},
     "created_at": "2026-09-05T08:01:00Z"},
    {"event_id": "e-exploit-1", "seq": 3, "type": "exploit/run",
     "data": {"tool": "exploit", "command": "python3 exp.py --url http://10.0.0.1:8080",
              "summary": "RCE confirmed, uid=0(root)"},
     "created_at": "2026-09-05T08:02:00Z"},
    {"event_id": "e-lateral-1", "seq": 4, "type": "tool/output",
     "data": {"tool": "netexec", "output": "[+] alice:P@ss (Pwn3d!) on 10.0.0.2"},
     "created_at": "2026-09-05T08:03:00Z"},
]
SNAPSHOT_HYPOTHESES: list[Hypothesis] = [
    Hypothesis(hypothesis_id="h-jetty", statement="8080 Jetty 9.4.39 存在 CVE-2021-XXXX RCE",
               status="validated", linked_actions=["e-recon-1", "e-recon-2", "e-exploit-1"]),
]
SNAPSHOT_CHAINS: list[AttackChain] = [
    AttackChain(chain_id="c-rce", steps=["h-jetty"], impact="服务器 root 远程代码执行",
                evidence_refs=["e-recon-2", "e-exploit-1"], severity="critical"),
]
SNAPSHOT_DEAD_ENDS: list[DeadEnd] = [
    DeadEnd(dead_end_id="d-sqli", path_description="SQL注入: /login 表单 → sqlmap → 无注入点",
            hypothesis_id="h-sqli", falsified_at_step=2, falsified_at="2026-09-05T08:20:00Z",
            evidence_snapshot="sqlmap --level 5 --risk 3 无注入点",
            falsification_method="sqlmap", overturn_condition="wappalyzer 识别到数据库错误回显"),
]


def make_profile_a() -> TargetProfile:
    return TargetProfile(
        target_id="A", identity=Identity(ip="10.0.0.1", hostname="edge.example"),
        scope_ref="scope-auth-1",
        attack_surface=AttackSurface(credentials=[
            CredentialLead(source="leak", username="alice", password="P@ss", confidence="confirmed")]),
        hypotheses=list(SNAPSHOT_HYPOTHESES), validated=list(SNAPSHOT_CHAINS),
        dead_ends=list(SNAPSHOT_DEAD_ENDS), coverage={"recon/": {"done": 5, "total": 5}},
    )


def test_report_engine_replay_key_behaviors() -> None:
    """快照回放：报告结构/复现步骤/负面章节/跨目标链 关键行为与契约一致。"""
    profile_a = make_profile_a()
    # B 目标：A 的凭据被用于横向移动（evidence_reuse）
    hyp_b = Hypothesis(hypothesis_id="h-lateral", statement="用 alice 凭据横移 10.0.0.2",
                       status="validated", linked_actions=["e-lateral-1"])
    profile_b = TargetProfile(
        target_id="B", identity=Identity(ip="10.0.0.2"), scope_ref="scope-auth-1",
        hypotheses=[hyp_b],
        validated=[AttackChain(chain_id="c-lateral", steps=["h-lateral"], impact="内网横向移动",
                               evidence_refs=["e-lateral-1"], severity="high")],
    )
    eng = generate_engagement_report(
        [EngagementSnapshot(engagement_id="eng-2026-001", client="ACME",
                            authorization_ref="AUTH-2026-001", profiles=[profile_a, profile_b],
                            events=SNAPSHOT_EVENTS)],
        generated_at="2026-09-05T09:00:00Z",
    )
    report = eng.to_dict()

    # 行为 1：发现详情来自 validated 链，复现步骤沿 linked_actions 事件按序投影
    assert report["executive_summary"]["validated_chain_count"] == 2
    rce = next(f for f in report["findings"] if f["chain_id"] == "c-rce")
    assert rce["severity"] == "critical"
    assert rce["reproduce_steps"][0]["action"] == "nmap -sV -p 8080 10.0.0.1"
    assert rce["reproduce_steps"][-1]["event_id"] == "e-exploit-1"
    assert any("RCE confirmed" in s["evidence"] for s in rce["reproduce_steps"])

    # 行为 2：负面发现（已排除攻击面）章节含 deadEnd 汇总与 overturn_condition
    excluded = report["excluded_attack_surfaces"]
    assert excluded[0]["dead_end_id"] == "d-sqli"
    assert excluded[0]["overturn_condition"] == "wappalyzer 识别到数据库错误回显"
    assert excluded[0]["overturned"] is False

    # 行为 3：跨目标链检出（A 凭据打 B，evidence_reuse 通道）
    assert report["executive_summary"]["cross_target_chain_count"] == 1
    cross = report["cross_target_chains"][0]
    assert (cross["source_target_id"], cross["target_target_id"]) == ("A", "B")
    assert cross["username"] == "alice" and cross["match"] == "evidence_reuse"

    # 行为 4：渲染 YAML 章节顺序与模板一致（负面章节在 findings 之后）
    text = render_report_yaml(eng)
    assert text.index("findings:") < text.index("excluded_attack_surfaces:")
    assert "overturn_condition: wappalyzer 识别到数据库错误回显" in text

    # 行为 5：跨链检测纯函数直接回放同样命中
    direct = find_cross_target_chains([profile_a, profile_b], events=SNAPSHOT_EVENTS)
    assert [(c.source_target_id, c.match) for c in direct] == [("A", "evidence_reuse")]


# ---------------------------------------------------------------------------
# 快照 2：worker cost_report 文件样本（H4 看板聚合回放）
# ---------------------------------------------------------------------------
def _write_cost_report(workdir, code: str, outcome: str, tokens_total: int,
                       cache_read: int, cache_write: int, turns: int,
                       zero_gain: int, peak_streak: int) -> None:
    w = workdir / f"worker_{code}"
    w.mkdir()
    payload = {
        "code": code, "outcome": outcome, "turns": turns,
        "tokens": {"total": tokens_total},
        "cache": {"cache_read": cache_read, "cache_write": cache_write},
        "zero_gain": {"total": zero_gain, "peak_streak": peak_streak},
    }
    (w / "cost_report.json").write_text(json.dumps(payload), encoding="utf-8")


def test_write_dashboard_replay_aggregates_snapshot(tmp_path) -> None:
    """快照回放：固定 cost_report 样本 → dashboard 聚合关键数字稳定。"""
    _write_cost_report(tmp_path, "t1", "solved", tokens_total=200, cache_read=160,
                       cache_write=40, turns=8, zero_gain=3, peak_streak=2)
    _write_cost_report(tmp_path, "t2", "stuck", tokens_total=400, cache_read=200,
                       cache_write=200, turns=12, zero_gain=5, peak_streak=3)
    write_dashboard(tmp_path)
    data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
    assert data["challenge_count"] == 2
    assert data["solved_count"] == 1
    assert data["cache"]["hit_rate"] == 0.6  # (160+200)/(200+400)
    assert data["tokens"]["total"] == 600
    assert data["zero_gain_events"] == 8  # 3 + 5（非硬编码 0）
    assert data["peak_zero_gain_streak"] == 3
    # 无 worker_* 快照时看板不落盘（行为契约）
    empty = tmp_path / "empty"
    empty.mkdir()
    write_dashboard(empty)
    assert not (empty / "dashboard.json").exists()
