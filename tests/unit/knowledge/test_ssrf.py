"""web_fetch.ssrf_check 单测 —— 判定矩阵边界 ≥6（R4.5 验收：SSRF ≥6 条）。

覆盖：环回 / 链路本地 / 0.0.0.0 / RFC1918 / 保留段放行（allowed_targets 内）/
公网放行 / 域名解析（公网与内网）/ 解析失败 fail-closed / 非法 scheme / 多 IP 任一拒绝。
"""
from __future__ import annotations

import pytest

from pentest.knowledge.web_fetch import ssrf_check
from pentest.scope import ScopeConstraint

PUBLIC_IP = "203.0.113.9"


def _check(url: str, allowed=(PUBLIC_IP,), resolver=None):
    scope = ScopeConstraint(allowed_targets=list(allowed))
    return ssrf_check(url, scope, resolver=resolver)


def test_loopback_denied() -> None:
    """环回 127.0.0.0/8 未授权 → 拒绝（ssrf_blocked）。"""
    result = _check("http://127.0.0.1:8080/admin")
    assert not result.allowed
    assert result.matched_rule == "ssrf_blocked"
    assert "127.0.0.1" in result.reason


def test_link_local_metadata_denied() -> None:
    """链路本地 169.254.0.0/16（云 metadata 端点）→ 拒绝。"""
    result = _check("http://169.254.169.254/latest/meta-data/")
    assert not result.allowed and result.matched_rule == "ssrf_blocked"


def test_zero_address_denied() -> None:
    """0.0.0.0（本网络/占位）→ 拒绝。"""
    result = _check("http://0.0.0.0/x")
    assert not result.allowed and result.matched_rule == "ssrf_blocked"


@pytest.mark.parametrize("ip", ["10.0.0.5", "172.16.0.1", "192.168.1.10"])
def test_rfc1918_denied(ip: str) -> None:
    """RFC1918 内网段未授权 → 拒绝。"""
    result = _check(f"http://{ip}/")
    assert not result.allowed and result.matched_rule == "ssrf_blocked"


def test_blocked_range_in_scope_allowed() -> None:
    """内网/环回靶标本身在 allowed_targets 内 → 放行（渗透内网授权场景）。"""
    ok_loopback = _check("http://127.0.0.1:8080/health", allowed=("127.0.0.1",))
    assert ok_loopback.allowed and ok_loopback.matched_rule == "allowed"
    ok_cidr = _check("http://10.0.0.5/", allowed=("10.0.0.0/24",))
    assert ok_cidr.allowed and ok_cidr.matched_rule == "allowed"


def test_public_ip_allowed() -> None:
    """公网 IP 直连 → 放行。"""
    result = _check("http://8.8.8.8/resolve")
    assert result.allowed and result.matched_rule == "allowed"


def test_domain_resolves_public_allowed() -> None:
    """域名解析到公网 IP → 放行。"""
    result = _check("http://example.com/doc", resolver=lambda host: ["93.184.216.34"])
    assert result.allowed and result.matched_rule == "allowed"


def test_domain_resolves_private_denied() -> None:
    """域名解析到内网 IP（DNS rebinding 形态）→ 拒绝，即便域名看似合法。"""
    result = _check("http://intra.corp.example.com/", resolver=lambda host: ["10.0.0.5"])
    assert not result.allowed and result.matched_rule == "ssrf_blocked"


def test_domain_resolve_failure_fail_closed() -> None:
    """解析失败 / 无结果 → 拒绝（无法验证即不放行）。"""
    result = _check("http://no-such-host.invalid/", resolver=lambda host: [])
    assert not result.allowed and result.matched_rule == "resolution_failed"


def test_invalid_scheme_denied() -> None:
    """非 http/https scheme → 拒绝。"""
    assert _check("file:///etc/passwd").matched_rule == "invalid_scheme"
    assert _check("ftp://example.com/x").matched_rule == "invalid_scheme"


def test_url_without_host_denied() -> None:
    assert not _check("http:///path").allowed


def test_multi_ip_any_blocked_denied() -> None:
    """多 IP 中任一命中禁止段 → 整体拒绝（fail-closed，防 rebinding 部分放行）。"""
    result = _check("http://example.com/", resolver=lambda host: ["8.8.8.8", "169.254.169.254"])
    assert not result.allowed and result.matched_rule == "ssrf_blocked"
