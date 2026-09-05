"""LocalRAG 单测 —— FTS5 关键词检索：key 精确命中 + 内容模糊命中（R4.5 验收双命中）。

另覆盖：category 过滤、embedding 可选列 / 离线退纯关键词（query_by_vector fallback）、
KnowledgeSource 协议返回形状。全程 tmp_path 临时库，不触网。
"""
from __future__ import annotations

import asyncio

import pytest

from pentest.knowledge.local_rag import LocalRAG


def _seed(rag: LocalRAG) -> None:
    rag.add_item(
        "cve",
        "CVE-2024-21762",
        "Apache HTTP Server 2.4.55 and earlier are vulnerable to path traversal and "
        "source disclosure (CVE-2024-21762).",
    )
    rag.add_item(
        "cve",
        "CVE-2023-44487",
        "HTTP/2 rapid reset enables denial of service against many servers.",
    )
    rag.add_item(
        "nuclei_template",
        "http-missing-security-headers",
        "Nuclei template that checks missing security headers on web targets.",
    )
    rag.add_item(
        "methodology",
        "ptes:recon",
        "PTES 情报收集阶段：被动 OSINT 与主动端口/服务测绘。",
    )


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def rag(tmp_path) -> LocalRAG:
    instance = LocalRAG(tmp_path / "kb.sqlite")
    _seed(instance)
    yield instance
    instance.close()


# ---------------------------------------------------------------------------
# 精确 + 模糊双命中（验收）
# ---------------------------------------------------------------------------
def test_exact_key_hit_top(rag: LocalRAG) -> None:
    """CVE 编号精确查询：key 精确命中置顶。"""
    items = rag.search("CVE-2024-21762")
    assert items and items[0]["key"] == "CVE-2024-21762"
    assert items[0]["exact"] is True and items[0]["score"] == 1.0


def test_exact_key_case_insensitive(rag: LocalRAG) -> None:
    """精确命中大小写不敏感（cve- 小写可命中 CVE- 大写存储）。"""
    items = rag.search("cve-2024-21762")
    assert items and items[0]["key"] == "CVE-2024-21762"


def test_fuzzy_keyword_hit(rag: LocalRAG) -> None:
    """非编号关键词模糊命中：内容 FTS5 关键词召回。"""
    result = _run(rag.query("apache path traversal source disclosure"))
    assert result.source_type == "local_rag"
    assert "cve:CVE-2024-21762" in result.source_refs
    assert "模糊" in result.answer
    assert result.confidence == "medium"  # 纯模糊 → medium；精确命中才 high


def test_query_exact_returns_high_confidence(rag: LocalRAG) -> None:
    result = _run(rag.query("CVE-2024-21762"))
    assert result.confidence == "high"
    assert "cve:CVE-2024-21762" in result.source_refs
    assert "精确" in result.answer


def test_no_hit_graceful_degrade(rag: LocalRAG) -> None:
    """无命中 → 低置信度空 refs（不抛错）。"""
    result = _run(rag.query("zzzzzz nosuchterm qqqqq"))
    assert result.source_refs == [] and result.confidence == "low"
    assert "无命中" in result.answer


# ---------------------------------------------------------------------------
# category 过滤 / 批量 / FTS 未命中兜底
# ---------------------------------------------------------------------------
def test_category_filter(rag: LocalRAG) -> None:
    result = _run(rag.query("apache", category="cve"))
    assert result.source_refs and all(ref.startswith("cve:") for ref in result.source_refs)


def test_add_items_bulk(tmp_path) -> None:
    rag = LocalRAG(tmp_path / "bulk.sqlite")
    try:
        count = rag.add_items(
            [
                {"category": "cve", "key": "CVE-2024-0001", "content": "bulk item one"},
                {"category": "cve", "key": "CVE-2024-0002", "content": "bulk item two"},
            ]
        )
        assert count == 2 and rag.count() == 2
        assert rag.count(category="cve") == 2
    finally:
        rag.close()


# ---------------------------------------------------------------------------
# 可选 embedding 列：向量召回 + 离线退纯关键词
# ---------------------------------------------------------------------------
def test_embedding_search_ranks_by_dot(rag: LocalRAG) -> None:
    rag.add_item("cve", "CVE-2025-0001", "vector A content", embedding=[1.0, 0.0, 0.0])
    rag.add_item("cve", "CVE-2025-0002", "vector B content", embedding=[0.0, 1.0, 0.0])
    hits = rag.embedding_search([1.0, 0.0, 0.0])
    assert hits and hits[0]["key"] == "CVE-2025-0001"
    hits2 = rag.embedding_search([0.0, 1.0, 0.0])
    assert hits2 and hits2[0]["key"] == "CVE-2025-0002"


def test_query_by_vector_offline_falls_back_to_keyword(tmp_path) -> None:
    """无 embedding（离线）→ query_by_vector 自动退化为纯关键词检索。"""
    rag = LocalRAG(tmp_path / "offline.sqlite")
    try:
        rag.add_item("cve", "CVE-2024-9999", "known cve content about apache server")
        result = _run(rag.query_by_vector([0.25, 0.75], fallback_question="CVE-2024-9999"))
        assert "CVE-2024-9999" in result.answer and result.confidence == "high"
    finally:
        rag.close()


def test_embedding_empty_returns_empty(rag: LocalRAG) -> None:
    assert rag.embedding_search([1.0, 1.0]) == []
