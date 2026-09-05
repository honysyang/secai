"""WebFetcher 单测 —— HTML→Markdown 转换 + mock httpx 抓取 + T2 审批 + SSRF 前置拦截。

全程注入 fake httpx client / fake resolver，不触真网。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from pentest.approval import ApprovalGate
from pentest.knowledge.web_fetch import WebFetcher, html_to_markdown
from pentest.scope import ScopeConstraint

SAMPLE_HTML = (
    "<html><head><script>alert(1)</script></head><body>"
    "<h1>漏洞标题</h1>"
    '<p>正文 <a href="https://nvd.nist.gov/vuln/detail/CVE-2024-21762">NVD 链接</a></p>'
    "<ul><li>项一</li><li><strong>项二</strong></li></ul>"
    "<pre>nuclei -t cves/2024/CVE-2024-21762.yaml\n  args</pre>"
    "</body></html>"
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# HTML → Markdown（纯标准库）
# ---------------------------------------------------------------------------
def test_html_to_markdown_headings_links_lists_code() -> None:
    md = html_to_markdown(SAMPLE_HTML)
    assert "# 漏洞标题" in md
    assert "[NVD 链接](https://nvd.nist.gov/vuln/detail/CVE-2024-21762)" in md
    assert "- 项一" in md
    assert "- **项二**" in md
    assert "alert(1)" not in md  # script 内容剔除
    assert "```" in md and "nuclei -t cves/2024/CVE-2024-21762.yaml" in md


def test_html_to_markdown_plain_text_no_tags() -> None:
    assert html_to_markdown("hello\n  world") == "hello\nworld"


def test_html_to_markdown_strips_style_and_entities() -> None:
    html = "<style>.x{color:red}</style><p>a &amp; b &lt; c</p>"
    md = html_to_markdown(html)
    assert ".x" not in md
    assert "a & b < c" in md


# ---------------------------------------------------------------------------
# fetch：mock httpx
# ---------------------------------------------------------------------------
def _public_fetcher(client, resolver=None) -> WebFetcher:
    scope = ScopeConstraint(allowed_targets=["203.0.113.9"])
    return WebFetcher(scope, client=client, resolver=resolver)


def test_fetch_returns_markdown_result() -> None:
    resp = SimpleNamespace(
        status_code=200,
        headers={"content-type": "text/html"},
        text="<html><body><h2>Apache 漏洞详情</h2></body></html>",
    )
    client = SimpleNamespace(get=AsyncMock(return_value=resp))
    fetcher = _public_fetcher(client, resolver=lambda host: ["93.184.216.34"])
    result = _run(fetcher.fetch("http://example.com/cve.html"))
    assert result.source_type == "web_fetch"
    assert result.confidence == "high"
    assert result.source_refs == ["http://example.com/cve.html"]
    assert "Apache 漏洞详情" in result.answer
    assert "HTTP 200" in result.answer


def test_fetch_ssrf_blocked_before_network() -> None:
    """SSRF 命中（链路本地）→ 前置拦截，不发网络请求。"""
    client = SimpleNamespace(get=AsyncMock())
    fetcher = _public_fetcher(client)
    result = _run(fetcher.fetch("http://169.254.169.254/latest/meta-data/"))
    assert result.source_refs == [] and result.confidence == "low"
    assert "SSRF" in result.answer
    assert client.get.await_count == 0


def test_fetch_t2_approval_denied_blocks_network() -> None:
    """T2 审批未放行 → 网络不执行，返回低置信度降级结果。"""
    client = SimpleNamespace(get=AsyncMock())
    gate = ApprovalGate(tiers={"web_fetch": "T2"})
    fetcher = WebFetcher(ScopeConstraint(allowed_targets=["203.0.113.9"]), approval=gate, client=client)
    result = _run(fetcher.fetch("http://8.8.8.8/x", session_id="s1"))
    assert result.confidence == "low" and result.source_refs == []
    assert "T2 审批未通过" in result.answer
    assert client.get.await_count == 0


def test_fetch_http_error_degrades() -> None:
    client = SimpleNamespace(get=AsyncMock(side_effect=httpx.ConnectError("refused")))
    fetcher = _public_fetcher(client)
    result = _run(fetcher.fetch("http://8.8.8.8/x"))
    assert result.confidence == "low" and result.source_refs == []
    assert "抓取失败" in result.answer


def test_fetch_http_error_status_low() -> None:
    resp = SimpleNamespace(status_code=404, headers={"content-type": "text/html"}, text="not found")
    client = SimpleNamespace(get=AsyncMock(return_value=resp))
    fetcher = _public_fetcher(client)
    result = _run(fetcher.fetch("http://8.8.8.8/missing"))
    assert result.confidence == "low"
    assert "HTTP 404" in result.answer


def test_fetch_query_protocol_alias() -> None:
    """KnowledgeSource 适配：query(question) 即 fetch(question)。"""
    resp = SimpleNamespace(
        status_code=200,
        headers={"content-type": "text/plain"},
        text="plain content line",
    )
    client = SimpleNamespace(get=AsyncMock(return_value=resp))
    fetcher = _public_fetcher(client)
    result = _run(fetcher.query("http://8.8.8.8/robots.txt"))
    assert result.source_type == "web_fetch" and result.confidence == "high"


def test_fetch_requires_scope() -> None:
    """缺 scope 的裸构造应尽早失败（防误用绕过 SSRF 防护）。"""
    with pytest.raises(TypeError):
        WebFetcher()  # type: ignore[call-arg]
