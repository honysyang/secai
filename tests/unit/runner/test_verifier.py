"""harness/runner/verifier.py 单测 —— R3 验收：Mechanical 纯函数五类 Verdict + 双实现可切换。"""

import asyncio

from harness.runner.verifier import (
    VERDICTS,
    LLMVerifier,
    MechanicalVerifier,
    build_verifier,
    format_evidence,
    parse_verdict,
)
from pentest.contract import AcceptanceContract, DeliverableSpec
from pentest.target_profile import AttackChain, Identity, TargetProfile


def make_profile(coverage: dict | None = None, validated: list | None = None,
                 dead_ends: list | None = None) -> TargetProfile:
    from pentest.target_profile import AttackSurface, PortFinding

    return TargetProfile(
        target_id="t1",
        identity=Identity(ip="10.0.0.1"),
        scope_ref="scope-1",
        attack_surface=AttackSurface(ports=[PortFinding(port=80, protocol="tcp", service="http")]),
        coverage=coverage or {},
        validated=validated or [],
        dead_ends=dead_ends or [],
    )


def make_chain(chain_id: str = "c1") -> AttackChain:
    return AttackChain(chain_id=chain_id, steps=["h1"], impact="RCE",
                       evidence_refs=["e1"], severity="high")


def contract(chain_min: int = 1, *, max_dead_end_ratio: float = 0.5,
             min_coverage: float = 0.8) -> AcceptanceContract:
    return AcceptanceContract(
        required_deliverables=[
            DeliverableSpec(kind="attack_surface_map", description="攻击面测绘", minimum=1),
            DeliverableSpec(kind="validated_chain", description="已验证攻击链", minimum=chain_min),
        ],
        minimum_coverage=min_coverage,
        max_dead_end_ratio=max_dead_end_ratio,
    )


def make_dead(i: int):
    from pentest.deadends import DeadEnd

    return DeadEnd(
        dead_end_id=f"d{i}", path_description="x", hypothesis_id=f"h{i}",
        falsified_at_step=1, falsified_at="2025-01-01T00:00:00Z", evidence_snapshot="",
        falsification_method="", overturn_condition="",
    )


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# MechanicalVerifier —— 纯函数，五类 Verdict 全覆盖
# ---------------------------------------------------------------------------

def test_mechanical_five_verdicts_reachable() -> None:
    m = MechanicalVerifier()
    # 1. verified_done：契约全过
    p_done = make_profile(coverage={"recon/": {"done": 4, "total": 5}}, validated=[make_chain()])
    # 2. corrective：死路占比 > 0.5（造假信号）
    p_bad = make_profile(coverage={"recon/": {"done": 4, "total": 5}}, validated=[make_chain()],
                         dead_ends=[make_dead(1), make_dead(2), make_dead(3)])
    # 3. redirect：零进展
    p_zero = make_profile()
    # 4. redirect：攻击链为 0 且覆盖不足
    p_chainless = make_profile(coverage={"recon/": {"done": 3, "total": 5}})
    # 5. need_human：覆盖达标但攻击链缺项
    p_human = make_profile(coverage={"recon/": {"done": 4, "total": 5}})
    # 6. continue：有进展但未达验收
    p_cont = make_profile(coverage={"recon/": {"done": 2, "total": 4}}, validated=[make_chain()])

    assert run(m.verify(p_done, contract())) == "verified_done"
    assert run(m.verify(p_bad, contract())) == "corrective"
    assert run(m.verify(p_zero, contract())) == "redirect"
    assert run(m.verify(p_chainless, contract())) == "redirect"
    assert run(m.verify(p_human, contract())) == "need_human"
    assert run(m.verify(p_cont, contract(chain_min=2))) == "continue"


def test_mechanical_verify_is_pure_and_sync() -> None:
    """verify_sync 同步纯函数本体：无 IO、无状态、可直调。"""
    m = MechanicalVerifier()
    p = make_profile(coverage={"recon/": {"done": 4, "total": 5}}, validated=[make_chain()])
    assert m.verify_sync(p, contract()) == "verified_done"
    # 同一输入多次调用结果稳定（纯函数）
    assert m.verify_sync(p, contract()) == "verified_done"
    assert m.name == "mechanical"


def test_verdict_enum_contains_five() -> None:
    assert set(VERDICTS) == {"continue", "redirect", "corrective", "verified_done", "need_human"}


# ---------------------------------------------------------------------------
# 双实现可切换（R3 验收）
# ---------------------------------------------------------------------------

def test_build_verifier_default_mechanical() -> None:
    v = build_verifier("mechanical")
    assert isinstance(v, MechanicalVerifier)
    v2 = build_verifier()  # 默认 mechanical
    assert isinstance(v2, MechanicalVerifier)


def test_build_verifier_unknown_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        build_verifier("magic")


def test_llm_verifier_without_llm_falls_back_to_mechanical() -> None:
    v = build_verifier("llm")  # 无 llm → fail-safe 回退机械
    assert isinstance(v, LLMVerifier)
    p = make_profile(coverage={"recon/": {"done": 4, "total": 5}}, validated=[make_chain()])
    assert run(v.verify(p, contract())) == "verified_done"


def test_llm_verifier_uses_injected_llm_and_evidence() -> None:
    seen: dict = {}

    def fake_llm(prompt: str) -> str:
        seen["prompt"] = prompt
        return "VERDICT: redirect"

    v = build_verifier("llm", llm=fake_llm)
    p = make_profile(coverage={"recon/": {"done": 4, "total": 5}}, validated=[make_chain()])
    events = [{"kind": "tool/output", "data": {"tool": "nmap", "rc": 0}}]
    verdict = run(v.verify(p, contract(), events=events))
    assert verdict == "redirect"
    # 证据（事件日志 + profile 摘要）确实进了 prompt
    assert "最近事件" in seen["prompt"]
    assert "profile 快照" in seen["prompt"]
    assert "机械初判" in seen["prompt"]


def test_llm_verifier_supports_async_llm() -> None:
    async def fake_llm(prompt: str) -> str:
        return "need_human"

    v = build_verifier("llm", llm=fake_llm)
    p = make_profile(coverage={"recon/": {"done": 2, "total": 5}})
    assert run(v.verify(p, contract())) == "need_human"


def test_parse_verdict_normalization() -> None:
    assert parse_verdict("") == "need_human"
    assert parse_verdict("VERDICT: verified_done") == "verified_done"
    assert parse_verdict("```verified_done```") == "verified_done"
    assert parse_verdict("redirect the attack") == "redirect"
    assert parse_verdict("corrective") == "corrective"
    assert parse_verdict("need human review") == "need_human"
    assert parse_verdict("continue executing") == "continue"
    assert parse_verdict("随便说点啥") == "need_human"


def test_format_evidence_truncates_and_summarizes() -> None:
    p = make_profile(coverage={"recon/": {"done": 4, "total": 5}}, validated=[make_chain()])
    text = format_evidence([{"kind": "a", "data": {"x": 1}}], p)
    assert "覆盖率=80%" in text
    assert "已验证攻击链=1" in text
    text2 = format_evidence([{"kind": "big", "data": {"pad": "x" * 20000}}], p, max_chars=500)
    assert len(text2) < 2000  # 截断生效
