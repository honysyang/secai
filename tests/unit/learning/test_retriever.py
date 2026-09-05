"""L6 retriever 单测 —— 三路检索命中：playbook 前提匹配 / lesson 关键词 / negative 拦截。"""
from __future__ import annotations

import asyncio

import pytest

from pentest.learning import LearningRetriever
from pentest.learning.models import Lesson, NegativeKnowledge, Playbook, PlaybookStep
from pentest.learning.store import LearningStore
from tests.unit.learning.helpers import T0, make_hypothesis, make_profile


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path) -> LearningStore:
    instance = LearningStore(tmp_path / "learning.db")
    instance.upsert_playbook(
        Playbook(
            playbook_id="pb-wp",
            name="wordpress 攻击链剧本",
            preconditions=["tech:wordpress", "service:http"],
            steps=[PlaybookStep(tool="sqlmap", params_template="sqlmap -u {target}/wp-admin --json",
                                expected_check="body contains flag")],
            expected_checks=["body contains flag"],
            source_engagement="e0",
        )
    )
    instance.upsert_playbook(
        Playbook(
            playbook_id="pb-tomcat",
            name="tomcat 攻击链剧本",
            preconditions=["tech:tomcat", "service:http"],
            steps=[PlaybookStep(tool="nuclei", params_template="nuclei -u {target} -t tomcat-rce")],
            expected_checks=["matched contains rce"],
            source_engagement="e0",
        )
    )
    instance.upsert_lesson(
        Lesson(
            lesson_id="les-sqlmap",
            title="sqlmap JSON body 需加 --json",
            context_tags=["tool:sqlmap", "phase:exploitation"],
            content="sqlmap 对 JSON body 的 API 需加 --json 参数，否则 400 拒绝浪费步骤。",
            source_engagement="e0",
            created_at=T0,
        )
    )
    instance.upsert_negative(
        NegativeKnowledge(
            negative_id="neg-wp-rce",
            pattern="wordpress==6.4 该插件组合 登录表单 sqlmap 注入 → 无已知 RCE",
            evidence_summary="nuclei cve-2023-xxxx 族多目标零命中",
            source_engagement="e0",
        )
    )
    return instance


def _run_retrieve(store: LearningStore | None, profile, hypothesis):
    retriever = LearningRetriever(store)
    return _run(retriever.retrieve(profile, hypothesis))


def test_playbook_precondition_hit_injects_step_suggestions(store: LearningStore) -> None:
    """目标指纹（tech:wordpress + service:http）命中 playbook 前提 → 步骤建议注入。"""
    profile = make_profile(tech=("wordpress",))
    hypothesis = make_hypothesis("h1", statement="enumerate wordpress site assets and version")
    context = _run_retrieve(store, profile, hypothesis)
    assert not context.blocked
    assert len(context.playbook_hits) == 1
    hit = context.playbook_hits[0]
    assert "tech:wordpress" in hit.matched_preconditions
    assert hit.playbook.playbook_id == "pb-wp"
    suggestions = context.steps_suggestions
    assert any("sqlmap" in line and "wp-admin" in line for line in suggestions)
    assert any("预期校验点" in line for line in suggestions)


def test_playbook_no_hit_when_fingerprint_mismatch(store: LearningStore) -> None:
    """非 wordpress 目标（tomcat 技术栈）不命中 wordpress 剧本。"""
    profile = make_profile(tech=("tomcat",), service="http")
    context = _run_retrieve(store, profile, make_hypothesis("h1", statement="tomcat 后台弱口令"))
    # tomcat 剧本命不命中取决于假设与指纹——此处验证 wordpress 剧本绝不命中
    assert all(hit.playbook.playbook_id != "pb-wp" for hit in context.playbook_hits)
    assert "tech:wordpress" not in [p for h in context.playbook_hits for p in h.matched_preconditions]


def test_playbook_no_hit_on_absent_attack_surface(store: LearningStore) -> None:
    """空指纹目标：无任何 playbook 前提可匹配。"""
    profile = make_profile(tech=(), os="")
    # make_profile 固定端口 80/http → 手工清空指纹
    profile.identity.tech_stack = []
    profile.attack_surface.ports = []
    profile.attack_surface.web = []
    context = _run_retrieve(store, profile, make_hypothesis("h1", statement="端口扫描"))
    assert context.playbook_hits == []


def test_lesson_keyword_hit_injects_attention_and_counts_applied(store: LearningStore) -> None:
    """当前假设语义命中 lesson（tool:sqlmap 标签 + 关键词）→ 注意事项 + applied_count 回写。"""
    profile = make_profile(tech=("python",))  # 无 wordpress 指纹 → 不触发负面拦截
    hypothesis = make_hypothesis("h1", statement="sqlmap 对 json body 接口调用参数")
    context = _run_retrieve(store, profile, hypothesis)
    assert context.lesson_hits
    assert context.lesson_hits[0].lesson_id == "les-sqlmap"
    assert any("--json" in note or "sqlmap" in note for note in context.attention_notes)
    # auto_count：检索命中即 bump applied_count（再次命中继续 +1）
    assert store.get_lesson("les-sqlmap").applied_count == 1
    _run_retrieve(store, profile, hypothesis)
    assert store.get_lesson("les-sqlmap").applied_count == 2


def test_negative_hit_blocks_hypothesis(store: LearningStore) -> None:
    """当前假设命中负面模式 → blocked + skip_reason + warnings（直接跳过该 hypothesis）。"""
    profile = make_profile(tech=("wordpress",))
    hypothesis = make_hypothesis("h1", statement="wordpress 插件 sqlmap 登录表单注入 RCE")
    context = _run_retrieve(store, profile, hypothesis)
    assert context.blocked is True
    assert context.negative_hits
    assert "wordpress==6.4" in context.skip_reason
    assert context.warnings
    assert "[learning/blocked]" in context.to_injection_text()


def test_negative_takes_precedence_over_playbook_and_lesson(store: LearningStore) -> None:
    """§4.5 顺序：命中负面模式 → 直接跳过，不再注入 playbook/lesson（省 token）。"""
    profile = make_profile(tech=("wordpress",))  # 同时满足 playbook 与 negative
    context = _run_retrieve(store, profile, make_hypothesis("h1", statement="wordpress 插件 sqlmap 登录注入 RCE"))
    assert context.blocked is True
    assert context.playbook_hits == []
    assert context.lesson_hits == []
    assert context.steps_suggestions == [] and context.attention_notes == []


def test_overturned_negative_no_longer_blocks(store: LearningStore) -> None:
    """负面知识被推翻（overturned）后不再拦截：默认查询即排除。"""
    assert store.mark_negative_overturned("neg-wp-rce") is True
    profile = make_profile(tech=("wordpress",))
    hypothesis = make_hypothesis("h1", statement="wordpress 插件 sqlmap 登录表单注入 RCE")
    context = _run_retrieve(store, profile, hypothesis)
    assert context.blocked is False
    assert context.negative_hits == []


def test_no_store_returns_empty_context() -> None:
    """无学习库（离线冷启动）→ 空上下文、不抛错、无注入噪音。"""
    context = _run_retrieve(None, make_profile(), make_hypothesis("h1", statement="任意假设"))
    assert context.playbook_hits == []
    assert context.lesson_hits == []
    assert context.negative_hits == []
    assert context.blocked is False
    assert context.to_injection_text() == ""


def test_context_injection_text_sections(tmp_path) -> None:
    """命中 playbook + lesson（无 negative）→ injection_text 含 playbook/lesson 两段。"""
    store = LearningStore(tmp_path / "learning.db")
    store.upsert_playbook(
        Playbook(playbook_id="pb-x", name="x 剧本", preconditions=["tech:wordpress"],
                 steps=[PlaybookStep(tool="sqlmap", params_template="sqlmap -u {target}")],
                 expected_checks=["body contains flag"], source_engagement="e0")
    )
    store.upsert_lesson(
        Lesson(lesson_id="les-x", title="sqlmap json", context_tags=["tool:sqlmap"],
               content="json body 需加 --json", source_engagement="e0", created_at=T0)
    )
    profile = make_profile(tech=("wordpress",))
    context = _run_retrieve(store, profile, make_hypothesis("h1", statement="wordpress sqlmap json 注入"))
    text = context.to_injection_text()
    assert "[learning/playbook]" in text
    assert "[learning/lesson]" in text
    assert "[learning/blocked]" not in text
