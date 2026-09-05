"""KnowledgeEngine 单测 —— 三路路由 + 结果合并排序 + 超时（web 源 mock，无真网）。

覆盖：本地 high 命中跳过联网 / 本地不足触发联网且排序正确 / 联网超时被吞 /
fetch_urls 显式路由 / query() 聚合去重与【来源】标记 / 全源禁用空结果。
"""
from __future__ import annotations

import asyncio
from typing import Any

from pentest.knowledge.base import KnowledgeResult
from pentest.knowledge.engine import KnowledgeEngine, merge_results


def _res(answer: str, source_type: str, refs, confidence: str) -> KnowledgeResult:
    return KnowledgeResult(answer=answer, source_type=source_type, source_refs=list(refs), confidence=confidence)  # type: ignore[arg-type]


class _QueueSource:
    """按调用顺序吐出预置结果的 fake KnowledgeSource。"""

    def __init__(self, results: list[KnowledgeResult]):
        self._queue = list(results)
        self.calls: list[str] = []

    async def query(self, question: str, context: Any = None) -> KnowledgeResult:
        self.calls.append(question)
        if self._queue:
            return self._queue.pop(0)
        return _res("(空)", "local_rag", [], "low")


class _SlowSource:
    """故意挂起超过引擎超时的 fake web 源。"""

    async def query(self, question: str, context: Any = None) -> KnowledgeResult:
        await asyncio.sleep(30)
        return _res("太慢了", "web_search", ["https://slow"], "high")


class _FetchSource:
    def __init__(self):
        self.urls: list[str] = []

    async def fetch(self, url: str, session_id: str = "knowledge") -> KnowledgeResult:
        self.urls.append(url)
        return _res(f"fetched {url}", "web_fetch", [url], "high")


def _run(coro):
    return asyncio.run(coro)


def test_local_high_hit_skips_web_search() -> None:
    """本地达到 high 置信度 → 不再触发联网（节省在线配额）。"""
    local = _QueueSource([_res("本地精确命中 CVE", "local_rag", ["cve:CVE-2024-21762"], "high")])
    web = _QueueSource([_res("联网结果", "web_search", ["https://web"], "high")])
    engine = KnowledgeEngine(rag=local, web_search=web)
    results = _run(engine.search("CVE-2024-21762", allow_web=True))
    assert web.calls == []  # 未触发联网
    assert len(results) == 1 and results[0].source_type == "local_rag"


def test_local_insufficient_triggers_web_and_ranks_by_confidence() -> None:
    """本地低置信度 → 触发联网；合并后 high（web）排在 low（本地）之前。"""
    local = _QueueSource([_res("本地无命中", "local_rag", [], "low")])
    web = _QueueSource([_res("联网找到详情", "web_search", ["https://nvd/CVE-2024-21762"], "high")])
    engine = KnowledgeEngine(rag=local, web_search=web)
    results = _run(engine.search("CVE-2024-21762"))
    assert web.calls == ["CVE-2024-21762"]
    assert [r.source_type for r in results] == ["web_search", "local_rag"]
    assert results[0].confidence == "high"


def test_web_timeout_swallowed_local_only() -> None:
    """联网超时 → 该路被吞掉，本地结果照常返回（不抛错）。"""
    local = _QueueSource([_res("本地 medium", "local_rag", ["cve:CVE-2024-21762"], "medium")])
    engine = KnowledgeEngine(rag=local, web_search=_SlowSource(), web_timeout=0.05)
    results = _run(engine.search("CVE-2024-21762"))
    assert len(results) == 1 and results[0].source_type == "local_rag"


def test_query_merges_multi_source_with_markers_and_refs() -> None:
    """query() 聚合：答案带【来源】标记、refs 跨源去重合并、置信度取最高。"""
    local = _QueueSource([_res("本地命中条目", "local_rag", ["cve:CVE-2024-21762", "cve:CVE-2023-44487"], "medium")])
    web = _QueueSource([_res("在线补充", "web_search", ["https://nvd/CVE-2024-21762"], "medium")])
    engine = KnowledgeEngine(rag=local, web_search=web)
    merged = _run(engine.query("apache CVE"))
    assert "【本地RAG】" in merged.answer and "【联网搜索】" in merged.answer
    assert merged.confidence == "medium"
    assert merged.source_refs == ["cve:CVE-2024-21762", "cve:CVE-2023-44487", "https://nvd/CVE-2024-21762"]


def test_fetch_urls_routed_explicitly_only() -> None:
    """web_fetch 永不自动：仅 fetch_urls 显式传入的 URL 被抓。"""
    fetch = _FetchSource()
    engine = KnowledgeEngine(web_fetch=fetch)
    results = _run(engine.search("dummy", allow_web=False, fetch_urls=("http://example.com/doc",)))
    assert fetch.urls == ["http://example.com/doc"]
    assert len(results) == 1 and results[0].source_type == "web_fetch"
    assert results[0].source_refs == ["http://example.com/doc"]


def test_no_sources_graceful_empty() -> None:
    """全部源未注入 → query() 返回低置信度空结果（不抛错）。"""
    engine = KnowledgeEngine()
    merged = _run(engine.query("anything"))
    assert merged.source_refs == [] and merged.confidence == "low"
    assert "无命中" in merged.answer


def test_merge_results_dedupe_by_ref_and_top3_answer() -> None:
    results = [
        _res("a", "local_rag", ["r1"], "high"),
        _res("a 重复", "local_rag", ["r1"], "medium"),  # ref 相同 → 去重
        _res("b", "web_search", ["r2"], "low"),
    ]
    ranked = KnowledgeEngine._rank(results)
    assert len(ranked) == 2
    assert ranked[0].source_type == "local_rag" and ranked[1].source_type == "web_search"
    merged = merge_results(ranked, "q")
    assert merged.source_refs == ["r1", "r2"]
    assert merged.confidence == "high"
