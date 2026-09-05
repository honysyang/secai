"""sources/ 本地知识源单测 —— cve_db 精确命中 + 导入入口 / 工具手册 / 方法论 / 负面知识。

验收（R4.5）：cve_db 精确命中；各 sources 模块单测 ≥3（查询/导入/KnowledgeSource 协议）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from pentest.knowledge.sources.cve_db import CVEDB
from pentest.knowledge.sources.methodology import MethodologyIndex
from pentest.knowledge.sources.negative_findings import NegativeFindingsSource
from pentest.knowledge.sources.tool_manuals import ToolManualIndex

CVE_SAMPLE = [
    {
        "cve_id": "CVE-2024-21762",
        "description": "Apache HTTP Server 2.4.55 and earlier path traversal and source disclosure.",
        "severity": "high",
        "affected": "Apache HTTP Server <= 2.4.55",
        "references": ["https://nvd.nist.gov/vuln/detail/CVE-2024-21762"],
    },
    {
        "cve_id": "CVE-2023-44487",
        "description": "HTTP/2 rapid reset denial of service.",
        "severity": "high",
        "affected": "multiple HTTP/2 implementations",
    },
]


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# cve_db：导入 + 精确命中
# ---------------------------------------------------------------------------
@pytest.fixture
def db(tmp_path) -> CVEDB:
    instance = CVEDB(tmp_path / "cve.db")
    instance.import_cves(CVE_SAMPLE)
    yield instance
    instance.close()


def test_cve_import_and_lookup_exact_hit(db: CVEDB) -> None:
    """CVE 编号精确命中（验收用例）。"""
    row = db.lookup("CVE-2024-21762")
    assert row is not None
    assert row["cve_id"] == "CVE-2024-21762"
    assert row["severity"] == "high"
    assert row["references"] == ["https://nvd.nist.gov/vuln/detail/CVE-2024-21762"]
    assert db.lookup("CVE-2099-9999") is None


def test_cve_lookup_case_insensitive(db: CVEDB) -> None:
    assert db.lookup("cve-2024-21762") is not None


def test_cve_import_jsonl_import_entry(tmp_path) -> None:
    """导入入口 import_from_jsonl（供 scripts/import_cve_db.py 调用）。"""
    lines = [
        {"kind": "cve", "cve_id": "CVE-2024-0001", "description": "j one"},
        {"kind": "nuclei", "template_id": "cve-2024-0001", "name": "CVE-2024-0001 scan", "severity": "critical", "tags": ["cve", "rce"]},
        {"kind": "nuclei", "template_id": "http-missing-security-headers", "name": "Missing Headers", "severity": "info", "tags": ["misconfig"]},
    ]
    path = tmp_path / "feed.jsonl"
    path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8")
    db2 = CVEDB(tmp_path / "feed.db")
    try:
        counts = db2.import_from_jsonl(path)
        assert counts == {"cve": 1, "nuclei": 2}
        assert db2.lookup("CVE-2024-0001") is not None
        tpl = db2.lookup_template("cve-2024-0001")
        assert tpl is not None and tpl["tags"] == ["cve", "rce"]
    finally:
        db2.close()


def test_cve_search_keyword_and_counts(db: CVEDB) -> None:
    hits = db.search("apache")
    assert any(h["cve_id"] == "CVE-2024-21762" for h in hits)
    assert db.counts() == {"cve": 2, "nuclei": 0}


def test_cve_to_knowledge_items_bridges_localrag(db: CVEDB) -> None:
    """to_knowledge_items 输出可直接灌 LocalRAG（category cve / nuclei_template）。"""
    db.import_nuclei(
        [{"template_id": "http-missing-security-headers", "name": "Missing Headers", "severity": "info", "tags": ["misconfig"]}]
    )
    items = db.to_knowledge_items()
    categories = {item["category"] for item in items}
    assert "cve" in categories and "nuclei_template" in categories


# ---------------------------------------------------------------------------
# tool_manuals：按工具名索引 Markdown
# ---------------------------------------------------------------------------
NMAP_MD = "# nmap\n\n端口扫描：`nmap -sV -p- 10.0.0.1`\n\n- 输出解析走 ToolAdapter。"
SQLMAP_MD = "# sqlmap\n\n`sqlmap -u URL --batch --dbs`\n\nJSON body 场景需加 --json。"


@pytest.fixture
def manuals() -> ToolManualIndex:
    index = ToolManualIndex()
    index.register("nmap", NMAP_MD)
    index.register("sqlmap", SQLMAP_MD)
    return index


def test_tool_manual_register_and_get_by_name_case_insensitive(manuals: ToolManualIndex) -> None:
    assert manuals.has("NMAP")
    md = manuals.get_manual("Nmap")
    assert md is not None and "nmap -sV" in md
    assert manuals.get_manual("feroxbuster") is None


def test_tool_manual_load_dir(tmp_path) -> None:
    (tmp_path / "whatweb.md").write_text("# whatweb 使用手册\n\n指纹识别。", encoding="utf-8")
    index = ToolManualIndex(root=tmp_path)
    assert index.list_tools() == ["whatweb"]
    assert "指纹识别" in (index.get_manual("whatweb") or "")


def test_tool_manual_query_exact_tool_hit(manuals: ToolManualIndex) -> None:
    """问句含工具名 → 精确索引命中返回 high。"""
    result = _run(manuals.query("sqlmap 怎么测 JSON 接口"))
    assert result.source_type == "local_rag"
    assert result.source_refs == ["tool_manual:sqlmap"]
    assert result.confidence == "high" and "--json" in result.answer


def test_tool_manual_query_no_hit_graceful(manuals: ToolManualIndex) -> None:
    result = _run(manuals.query("hydra 无此手册库"))
    assert result.source_refs == [] and result.confidence == "low"


# ---------------------------------------------------------------------------
# methodology：PTES/OWASP 卡片
# ---------------------------------------------------------------------------
def test_methodology_get_ptes_and_owasp_card() -> None:
    index = MethodologyIndex()
    ptes = index.get_card("ptes:recon")
    assert ptes is not None and "情报收集" in ptes
    owasp = index.get_card("owasp:a03")
    assert owasp is not None and "注入" in owasp
    assert index.get_card("ptes:nope") is None


def test_methodology_list_cards_kinds() -> None:
    index = MethodologyIndex()
    ids = index.list_cards("ptes")
    assert "ptes:recon" in ids and "ptes:reporting" in ids
    assert set(index.list_cards("owasp")).issubset({f"owasp:a{i:02d}" for i in range(1, 11)})


def test_methodology_query_finds_ssrf_card() -> None:
    index = MethodologyIndex()
    result = _run(index.query("web 服务端请求伪造 SSRF 风险"))
    assert result.source_type == "local_rag"
    assert "methodology:owasp:a10" in result.source_refs


# ---------------------------------------------------------------------------
# negative_findings：L6 store 预留
# ---------------------------------------------------------------------------
@pytest.fixture
def negatives() -> NegativeFindingsSource:
    source = NegativeFindingsSource()
    source.register("wordpress==6.4 且该插件组合 → 无已知 RCE", "多目标验证一致")
    return source


def test_negative_register_match_and_search(negatives: NegativeFindingsSource) -> None:
    assert negatives.match("wordpress 6.4 插件 RCE 尝试")
    hits = negatives.search("wordpress 6.4 RCE")
    assert hits and "wordpress==6.4" in hits[0]["pattern"]


def test_negative_no_match_returns_false(negatives: NegativeFindingsSource) -> None:
    assert not negatives.match("tomcat CVE 扫描")


def test_negative_query_hit_semantics(negatives: NegativeFindingsSource) -> None:
    result = _run(negatives.query("wordpress 6.4 组合 RCE 验证"))
    assert result.source_refs and result.source_refs[0].startswith("negative:")
    assert "负面知识命中 1 条" in result.answer


def test_negative_connect_learning_store_returns_false(tmp_path) -> None:
    """R6 store 未落地 → 探测返回 False，不抛错（fail-safe）。"""
    source = NegativeFindingsSource()
    assert source.connect_learning_store(tmp_path / "learning.db") is False
