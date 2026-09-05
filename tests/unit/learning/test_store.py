"""L6 store 单测 —— SQLite 四表 upsert/query/搜索（tag 过滤）+ iter_negatives。"""
from __future__ import annotations

import sqlite3

import pytest

from pentest.learning.models import (
    GrowthMetrics,
    Lesson,
    NegativeKnowledge,
    Playbook,
    PlaybookStep,
)
from pentest.learning.store import LearningStore, iter_negatives

T0 = "2026-09-05T08:00:00Z"


def make_lesson(lesson_id: str = "les-1", **kwargs) -> Lesson:
    defaults = dict(
        title="sqlmap JSON body 参数注意事项",
        context_tags=["tool:sqlmap", "phase:exploitation"],
        content="sqlmap 对 JSON body 的 API 需加 --json 参数。",
        source_engagement="e1",
        created_at=T0,
    )
    defaults.update(kwargs)
    return Lesson(lesson_id=lesson_id, **defaults)


def make_playbook(**kwargs) -> Playbook:
    defaults = dict(
        playbook_id="pb-1",
        name="wordpress 攻击链剧本",
        preconditions=["tech:wordpress", "service:http"],
        steps=[PlaybookStep(tool="sqlmap", params_template="sqlmap -u {target} --json",
                            expected_check="body contains flag")],
        expected_checks=["body contains flag"],
        source_engagement="e1",
    )
    defaults.update(kwargs)
    return Playbook(**defaults)


def make_negative(negative_id: str = "neg-1", **kwargs) -> NegativeKnowledge:
    defaults = dict(
        pattern="wordpress==6.4 && 该插件组合 → 无已知 RCE",
        evidence_summary="多目标验证一致：nuclei cve-2023-xxxx 无命中",
        source_engagement="e1",
    )
    defaults.update(kwargs)
    return NegativeKnowledge(negative_id=negative_id, **defaults)


@pytest.fixture
def store(tmp_path) -> LearningStore:
    return LearningStore(tmp_path / "learning.db")


def test_schema_creates_four_tables(store: LearningStore) -> None:
    conn = sqlite3.connect(store.db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    assert {"lessons", "playbooks", "negatives", "growth_metrics"} <= names


def test_lesson_upsert_get_query_roundtrip(store: LearningStore) -> None:
    store.upsert_lesson(make_lesson())
    lesson = store.get_lesson("les-1")
    assert lesson is not None
    assert lesson.title.startswith("sqlmap")
    assert lesson.context_tags == ["tool:sqlmap", "phase:exploitation"]
    assert lesson.applied_count == 0
    # 来源 engagement 过滤 + 未命中返回 None
    assert store.query_lessons(source_engagement="e2") == []
    assert store.get_lesson("les-nope") is None


def test_lesson_upsert_same_pk_replaces(store: LearningStore) -> None:
    store.upsert_lesson(make_lesson(content="v1"))
    store.upsert_lesson(make_lesson(content="v2"))
    assert len(store.query_lessons()) == 1
    assert store.get_lesson("les-1").content == "v2"


def test_query_lessons_tag_filter_exact_prefix_and_segment(store: LearningStore) -> None:
    store.upsert_lesson(make_lesson("les-1", context_tags=["tool:sqlmap", "phase:exploitation"]))
    store.upsert_lesson(make_lesson("les-2", context_tags=["tool:nuclei", "tech:wordpress"]))
    assert [lesson.lesson_id for lesson in store.query_lessons(tags=["tool:sqlmap"])] == ["les-1"]
    assert [lesson.lesson_id for lesson in store.query_lessons(tags=["tool:"])] == ["les-1", "les-2"]
    assert [lesson.lesson_id for lesson in store.query_lessons(tags=["wordpress"])] == ["les-2"]
    assert store.query_lessons(tags=["phase:recon"]) == []


def test_search_lessons_keyword_ranking_and_tag_cofilter(store: LearningStore) -> None:
    store.upsert_lesson(make_lesson("les-1", context_tags=["tool:sqlmap", "phase:exploitation"],
                                    content="sqlmap 对 json body 需加 --json 参数，否则 400 拒绝"))
    store.upsert_lesson(make_lesson("les-2", title="nuclei 模板语法速查",
                                    context_tags=["tool:nuclei", "tech:wordpress"],
                                    content="nuclei 模板语法速查"))
    hits = store.search_lessons("sqlmap json body 参数", limit=5)
    assert hits and hits[0].lesson_id == "les-1"
    # tag 过滤 + 关键词 AND：只返回 sqlmap 类且含关键词
    hits2 = store.search_lessons("sqlmap", tags=["tool:sqlmap"])
    assert [h.lesson_id for h in hits2] == ["les-1"]
    assert store.search_lessons("sqlmap", tags=["tech:wordpress"]) == []


def test_bump_lesson_applied_increments(store: LearningStore) -> None:
    store.upsert_lesson(make_lesson())
    assert store.bump_lesson_applied("les-1") == 1
    assert store.bump_lesson_applied("les-1", delta=2) == 3
    assert store.get_lesson("les-1").applied_count == 3
    assert store.bump_lesson_applied("les-missing") == 0  # 幂等不抛错


def test_playbook_upsert_get_roundtrip_with_step_objects(store: LearningStore) -> None:
    original = make_playbook()
    store.upsert_playbook(original)
    restored = store.get_playbook("pb-1")
    assert restored == original
    assert all(isinstance(s, PlaybookStep) for s in restored.steps)
    assert restored.steps[0].tool == "sqlmap"
    assert restored.preconditions == ["tech:wordpress", "service:http"]
    # 复用结果回写：成功/失败各 +1
    assert store.record_playbook_result("pb-1", success=True) == (1, 0)
    assert store.record_playbook_result("pb-1", success=False) == (1, 1)
    assert store.get_playbook("pb-1").success_count == 1
    assert store.get_playbook("pb-1").fail_count == 1
    assert store.record_playbook_result("pb-nope", success=True) == (0, 0)


def test_negative_upsert_query_and_overturned_flag(store: LearningStore) -> None:
    store.upsert_negative(make_negative("neg-1"))
    store.upsert_negative(make_negative("neg-2", overturned=True))
    # 默认排除已推翻；include 后可查
    assert [n.negative_id for n in store.query_negatives()] == ["neg-1"]
    assert len(store.query_negatives(include_overturned=True)) == 2
    assert store.get_negative("neg-2").overturned is True
    # mark 推翻后默认查询消失（只标记不删除）
    assert store.mark_negative_overturned("neg-1") is True
    assert store.query_negatives() == []
    assert store.get_negative("neg-1").overturned is True
    assert store.mark_negative_overturned("neg-nope") is False


def test_metrics_upsert_get_roundtrip(store: LearningStore) -> None:
    metrics = GrowthMetrics(
        engagement_id="e1",
        hypothesis_hit_rate=0.5,
        deadend_overturn_rate=0.25,
        inconclusive_rate=0.2,
        token_per_validated_chain=800.0,
        playbook_reuse=2,
        lesson_injection=3,
    )
    store.upsert_metrics(metrics)
    restored = store.get_metrics("e1")
    assert restored == metrics
    assert store.get_metrics("e-missing") is None


def test_iter_negatives_requires_existing_db(tmp_path) -> None:
    """库文件不存在抛 FileNotFoundError —— negative_findings 探测即返回 False（fail-safe）。"""
    with pytest.raises(FileNotFoundError):
        list(iter_negatives(tmp_path / "learning.db"))
    store = LearningStore(tmp_path / "learning.db")
    store.upsert_negative(make_negative("neg-1"))
    store.upsert_negative(make_negative("neg-2", overturned=True))
    records = list(iter_negatives(tmp_path / "learning.db"))
    assert len(records) == 1  # 只同步未推翻项
    assert records[0]["negative_id"] == "neg-1"
    assert records[0]["pattern"].startswith("wordpress==6.4")
