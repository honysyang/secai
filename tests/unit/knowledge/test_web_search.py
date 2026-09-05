"""DeepSeekWebSearch 单测 —— mock httpx（无真网）+ 无 DEEPSEEK_API_KEY 优雅降级。

验收（R4.5）：无 key 返回“不可用”降级结果；有 key 时经注入 fake client 走
Anthropic 兼容 Messages API 形状，解析 web_search_tool_result 结构化来源。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from pentest.knowledge.web_search import DeepSeekWebSearch

RESULT_PAYLOAD = {
    "content": [
        {"type": "text", "text": "根据搜索结果，Apache HTTP Server 存在路径遍历与源码泄露风险。"},
        {
            "type": "web_search_tool_result",
            "tool_use_id": "toolu_01",
            "results": [
                {
                    "type": "web_search_results",
                    "title": "NVD - CVE-2024-21762",
                    "url": "https://nvd.nist.gov/vuln/detail/CVE-2024-21762",
                    "content": "Apache HTTP Server 2.4.55 及以下版本存在路径遍历漏洞。",
                },
                {
                    "type": "web_search_results",
                    "title": "Apache 官方公告",
                    "url": "https://httpd.apache.org/security/vulnerabilities_24.html",
                    "content": "官方修复版本 2.4.56。",
                },
            ],
        }
    ],
    "stop_reason": "end_turn",
}


def _fake_client(payload=RESULT_PAYLOAD):
    resp = SimpleNamespace(json=lambda: payload)
    client = SimpleNamespace(post=AsyncMock(return_value=resp))
    return client


def _run(coro):
    return asyncio.run(coro)


def test_missing_api_key_degrades_gracefully(monkeypatch) -> None:
    """无 DEEPSEEK_API_KEY → available=False，search 返回“不可用”结果而非抛错/触网。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    source = DeepSeekWebSearch()
    assert not source.available
    result = _run(source.search("Apache CVE-2024-21762"))
    assert result.source_type == "web_search"
    assert result.source_refs == [] and result.confidence == "low"
    assert "不可用" in result.answer and "DEEPSEEK_API_KEY" in result.answer


def test_mock_httpx_extracts_structured_sources(monkeypatch) -> None:
    """mock httpx client：解析 web_search_tool_result → source_refs URL 列表 + 高置信度。"""
    client = _fake_client()
    source = DeepSeekWebSearch(api_key="sk-test", client=client)
    result = _run(source.search("Apache CVE-2024-21762"))
    assert result.source_refs == [
        "https://nvd.nist.gov/vuln/detail/CVE-2024-21762",
        "https://httpd.apache.org/security/vulnerabilities_24.html",
    ]
    assert result.confidence == "high"
    assert "路径遍历" in result.answer
    # 请求形状：Anthropic 兼容头 + web_search_20250305 服务端工具
    kwargs = client.post.await_args.kwargs
    assert kwargs["headers"]["x-api-key"] == "sk-test"
    assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
    assert kwargs["json"]["tools"] == [{"type": "web_search_20250305"}]


def test_max_results_trims_sources(monkeypatch) -> None:
    client = _fake_client()
    source = DeepSeekWebSearch(api_key="sk-test", client=client)
    result = _run(source.search("Apache", max_results=1))
    assert len(result.source_refs) == 1
    assert result.source_refs[0].startswith("https://nvd.nist.gov")


def test_no_tool_result_low_confidence() -> None:
    """响应无 web_search_tool_result 块 → 空 refs + 低置信度（不抛错）。"""
    payload = {"content": [{"type": "text", "text": "没有搜索到来源。"}], "stop_reason": "end_turn"}
    source = DeepSeekWebSearch(api_key="sk-test", client=_fake_client(payload))
    result = _run(source.search("nothing"))
    assert result.source_refs == [] and result.confidence == "low"


def test_http_error_degrades_gracefully() -> None:
    """请求失败（httpx 异常）→ 低置信度降级结果。"""
    client = SimpleNamespace(post=AsyncMock(side_effect=httpx.ConnectError("connection refused")))
    source = DeepSeekWebSearch(api_key="sk-test", client=client)
    result = _run(source.search("Apache"))
    assert result.confidence == "low" and result.source_refs == []
    assert "不可用" in result.answer


def test_query_protocol_alias() -> None:
    """KnowledgeSource 适配：query(question) == search(question)。"""
    client = _fake_client()
    source = DeepSeekWebSearch(api_key="sk-test", client=client)
    result = _run(source.query("Apache CVE"))
    assert result.source_type == "web_search" and result.source_refs
