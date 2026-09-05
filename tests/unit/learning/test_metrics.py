"""L6 metrics 单测 —— GrowthMetrics 纯函数计算（§4.4，无 LLM/无 IO）。"""
from __future__ import annotations

from pentest.learning.metrics import compute_growth_metrics
from tests.unit.learning.helpers import make_chain, make_dead, make_fact, make_hypothesis


def test_hit_rate_validated_over_validated_plus_falsified() -> None:
    hypotheses = [
        make_hypothesis("h1", status="validated"),
        make_hypothesis("h2", status="validated"),
        make_hypothesis("h3", status="falsified"),
        make_hypothesis("h4", status="pending"),  # pending 不计入分母
    ]
    metrics = compute_growth_metrics("e1", hypotheses=hypotheses)
    assert metrics.hypothesis_hit_rate == round(2 / 3, 4)


def test_deadend_overturn_rate_via_new_facts() -> None:
    """new_facts 走 DeadEnd.is_overturned_by 纯函数：满足 overturn_condition 才算推翻。"""
    dead_ends = [make_dead("d1"), make_dead("d2", overturn_condition="http contains 404")]
    overturning = [make_fact(body="服务指纹 OpenSSH_9.6p1")]  # 只推翻 d1
    metrics = compute_growth_metrics("e1", dead_ends=dead_ends, new_facts=overturning)
    assert metrics.deadend_overturn_rate == 0.5
    # 显式 overturned_count 优先于 new_facts
    explicit = compute_growth_metrics("e1", dead_ends=dead_ends, overturned_count=2)
    assert explicit.deadend_overturn_rate == 1.0
    # 无 deadEnds / 无推翻信息 → 0.0
    assert compute_growth_metrics("e1").deadend_overturn_rate == 0.0


def test_inconclusive_rate_and_zero_outcome_safety() -> None:
    hypotheses = [
        make_hypothesis("h1", status="validated"),
        make_hypothesis("h2", status="inconclusive"),
        make_hypothesis("h3", status="inconclusive"),
    ]
    metrics = compute_growth_metrics("e1", hypotheses=hypotheses)
    assert metrics.inconclusive_rate == round(2 / 3, 4)
    assert metrics.hypothesis_hit_rate == 1.0  # 分母只看 validated+falsified
    # 全无 outcome → 各比率 0.0 不抛错
    empty = compute_growth_metrics("e-empty", hypotheses=[make_hypothesis("h1", status="pending")])
    assert empty.hypothesis_hit_rate == 0.0
    assert empty.deadend_overturn_rate == 0.0
    assert empty.inconclusive_rate == 0.0
    assert empty.token_per_validated_chain == 0.0


def test_token_per_validated_chain_and_counters() -> None:
    chains = [make_chain("c1"), make_chain("c2", impact="数据泄露")]
    metrics = compute_growth_metrics(
        "e1",
        hypotheses=[make_hypothesis("h1", status="validated")],
        validated_chains=chains,
        tokens_used=1000,
        playbook_reuse=3,
        lesson_injection=5,
    )
    assert metrics.token_per_validated_chain == 500.0
    assert metrics.playbook_reuse == 3
    assert metrics.lesson_injection == 5
    # 无 validated 链 → 0.0（不除以 0）
    no_chain = compute_growth_metrics("e1", tokens_used=999)
    assert no_chain.token_per_validated_chain == 0.0


def test_fractional_tokens_rounded_four_decimals() -> None:
    chains = [make_chain("c1"), make_chain("c2"), make_chain("c3")]
    metrics = compute_growth_metrics("e1", validated_chains=chains, tokens_used=1000)
    assert metrics.token_per_validated_chain == round(1000 / 3, 4)
