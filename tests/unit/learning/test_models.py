"""L6 models 单测 —— Lesson/Playbook/NegativeKnowledge/GrowthMetrics schema（v4 §4.3）。"""
from __future__ import annotations

from dataclasses import asdict

from pentest.learning.models import (
    GrowthMetrics,
    Lesson,
    NegativeKnowledge,
    Playbook,
    PlaybookStep,
)

T0 = "2026-09-05T08:00:00Z"


def make_lesson(**kwargs) -> Lesson:
    defaults = dict(
        lesson_id="les-1",
        title="sqlmap JSON body 参数注意事项",
        context_tags=["tool:sqlmap", "phase:exploitation", "tech:wordpress"],
        content="sqlmap 对 JSON body 的 API 需加 --json，本次因漏加浪费 3 步。",
        source_engagement="e1",
        created_at=T0,
    )
    defaults.update(kwargs)
    return Lesson(**defaults)


def make_playbook(**kwargs) -> Playbook:
    defaults = dict(
        playbook_id="pb-1",
        name="wordpress 攻击链剧本：RCE",
        preconditions=["tech:wordpress", "service:http", "os:linux"],
        steps=[
            PlaybookStep(tool="sqlmap", params_template="sqlmap -u {target}/wp-admin --json",
                         expected_check="status_code=200 && body contains flag"),
            PlaybookStep(tool="http", params_template="curl {target}/exp.php", expected_check="body contains flag"),
        ],
        expected_checks=["status_code=200 && body contains flag", "body contains flag"],
        source_engagement="e1",
    )
    defaults.update(kwargs)
    return Playbook(**defaults)


def test_lesson_default_applied_count_zero() -> None:
    lesson = make_lesson()
    assert lesson.applied_count == 0
    assert lesson.context_tags == ["tool:sqlmap", "phase:exploitation", "tech:wordpress"]
    assert lesson.source_engagement == "e1"
    assert lesson.created_at == T0


def test_lesson_asdict_roundtrip_preserves_fields() -> None:
    payload = asdict(make_lesson(applied_count=3))
    restored = Lesson(**payload)
    assert restored == make_lesson(applied_count=3)
    assert restored.applied_count == 3


def test_playbook_steps_are_playbook_step_with_default_counts() -> None:
    playbook = make_playbook()
    assert all(isinstance(step, PlaybookStep) for step in playbook.steps)
    assert playbook.success_count == 0 and playbook.fail_count == 0
    nested = asdict(playbook)["steps"]
    assert nested[0]["tool"] == "sqlmap"
    assert nested[0]["expected_check"].startswith("status_code=200")
    assert nested[1]["params_template"].startswith("curl")


def test_playbook_step_defaults_blank_template() -> None:
    step = PlaybookStep(tool="verify")
    assert step.params_template == "" and step.expected_check == ""


def test_negative_knowledge_default_not_overturned() -> None:
    negative = NegativeKnowledge(
        negative_id="neg-1",
        pattern="wordpress==6.4 && 该插件组合 → 无已知 RCE",
        evidence_summary="多目标验证一致：nuclei cve-2023-xxxx 无命中",
        source_engagement="e1",
    )
    assert negative.overturned is False
    marked = NegativeKnowledge(**{**asdict(negative), "overturned": True})
    assert marked.overturned is True


def test_growth_metrics_fields_and_to_dict() -> None:
    metrics = GrowthMetrics(
        engagement_id="e1",
        hypothesis_hit_rate=0.5,
        deadend_overturn_rate=0.25,
        inconclusive_rate=0.2,
        token_per_validated_chain=1234.0,
        playbook_reuse=2,
        lesson_injection=4,
    )
    payload = metrics.to_dict()
    assert payload["engagement_id"] == "e1"
    assert payload["hypothesis_hit_rate"] == 0.5
    assert payload["lesson_injection"] == 4
    assert len(payload) == 7  # §4.4 全部成长度量字段


def test_dataclass_equality_distinguishes_ids() -> None:
    assert make_lesson() == make_lesson()
    assert make_lesson(lesson_id="les-2") != make_lesson()
