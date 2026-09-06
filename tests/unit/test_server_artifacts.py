"""server/artifacts.py + 导出端点单测 —— 报告一键导出闭环（产品化 Phase 0-2）。

用 starlette TestClient（独立 AppState + fixture，仿 test_server_api.py）验证：
- api_report 触发「生成即存」：返回新增 artifacts 字段（md + json 各一份，
  元信息含 artifactId/format/sizeBytes/createdAt）；
- exportReport（md）→ HTTP 200 + Content-Disposition attachment，body 含
  报告标题与 findings 内容；
- exportReport（json）→ 合法 JSON（含 cover/findings 键）；
- listArtifacts → 返回刚落盘的 2 个产物；
- 未知 engagementId → 恒 HTTP 200 的 _error（unknown_engagement）；
- 产物落盘目录走 SECAI_ARTIFACTS_DIR（tmp_path 隔离，不污染 data/）。

运行：.venv/bin/python -m pytest tests/unit/test_server_artifacts.py -v
"""
from __future__ import annotations

import json
import os

# 关掉 demo ticker 干扰 + 产物落盘目录隔离到 tmp（在 import server.* 前生效）
os.environ.setdefault("SECAI_DEMO_TICK_SECONDS", "3600")

import pytest
from starlette.testclient import TestClient

from server.fixture import DEMO_ENGAGEMENT_ID
from server.main import create_app


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """独立 app：artifacts 目录隔离到 tmp_path（不落真实 data/artifacts）。"""
    monkeypatch.setenv("SECAI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    # AppState 在 create_app 内构造，读环境变量拿到隔离目录
    app = create_app()
    with TestClient(app) as client:
        yield app.state.state, client


def _post(client: TestClient, method: str, payload: dict) -> object:
    return client.post(
        f"/api/{method}", json={"rpcId": "rpc-test", "method": method, "payload": payload}
    )


def _trigger_report(client: TestClient) -> dict:
    """调 /api/report 触发「生成即存」，返回信封 result。"""
    body = _post(client, "report", {"engagementId": DEMO_ENGAGEMENT_ID}).json()
    assert body["ok"] is True
    return body["result"]


def test_api_report_persists_artifacts_on_generate(app_client) -> None:
    """api_report「生成即存」：返回含 md + json + pdf 三份产物元信息。"""
    state, client = app_client
    result = _trigger_report(client)
    artifacts = result["artifacts"]
    assert len(artifacts) == 3
    by_format = {a["format"]: a for a in artifacts}
    assert set(by_format) == {"md", "json", "pdf"}
    for meta in artifacts:
        assert meta["engagementId"] == DEMO_ENGAGEMENT_ID
        assert meta["artifactId"].startswith("art-")
        assert meta["sizeBytes"] > 0
        assert meta["createdAt"].endswith("Z")
        assert os.path.isfile(meta["path"])  # 文件真实落盘
    # 编排面存储同源（ArtifactsStore 视角再核一遍）
    stored = state.artifacts.list(DEMO_ENGAGEMENT_ID)
    assert len(stored) == 3


def test_export_report_markdown_download(app_client) -> None:
    """exportReport（md）→ attachment 下载，body 含报告标题与 finding 内容。"""
    _state, client = app_client
    result = _trigger_report(client)
    res = _post(client, "exportReport", {"engagementId": DEMO_ENGAGEMENT_ID, "format": "md"})
    assert res.status_code == 200
    disposition = res.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert f"report-{DEMO_ENGAGEMENT_ID}.md" in disposition
    assert "markdown" in res.headers["content-type"]
    text = res.text
    assert "# SECAI-PT 渗透测试报告" in text
    assert "## 执行摘要" in text
    assert "## 发现详情" in text
    assert "Spring Boot Actuator 未授权暴露内部端点" in text  # C 会话 finding 标题
    assert "## 已排除攻击面" in text
    # 下载内容与落盘产物一致（同一份字节）
    md_meta = next(a for a in result["artifacts"] if a["format"] == "md")
    with open(md_meta["path"], encoding="utf-8") as fh:
        assert fh.read() == text


def test_export_report_json_download_is_valid_json(app_client) -> None:
    """exportReport（json）→ 合法 JSON（EngagementReport.to_dict 键集）。"""
    _state, client = app_client
    _trigger_report(client)
    res = _post(client, "exportReport", {"engagementId": DEMO_ENGAGEMENT_ID, "format": "json"})
    assert res.status_code == 200
    assert "json" in res.headers["content-type"]
    payload = json.loads(res.text)  # 合法 JSON 断言
    assert payload["cover"]["engagement_id"] == DEMO_ENGAGEMENT_ID
    assert len(payload["findings"]) > 0
    for key in (
        "report_version",
        "cover",
        "executive_summary",
        "attack_surface_map",
        "findings",
        "excluded_attack_surfaces",
        "cross_target_chains",
        "methodology",
        "limitations",
    ):
        assert key in payload


def test_list_artifacts_returns_persisted_metas(app_client) -> None:
    """listArtifacts → 返回刚落盘的 3 个产物（md + json + pdf）。"""
    _state, client = app_client
    _trigger_report(client)
    body = _post(client, "listArtifacts", {"engagementId": DEMO_ENGAGEMENT_ID}).json()
    assert body["ok"] is True
    artifacts = body["result"]["artifacts"]
    assert len(artifacts) == 3
    assert {a["format"] for a in artifacts} == {"md", "json", "pdf"}
    # 幂等性：再次触发 report 会留档新产物（不覆盖）
    _trigger_report(client)
    body = _post(client, "listArtifacts", {"engagementId": DEMO_ENGAGEMENT_ID}).json()
    assert len(body["result"]["artifacts"]) == 6


def test_export_and_list_unknown_engagement_error(app_client) -> None:
    """未知 engagementId → 恒 HTTP 200 的 _error（unknown_engagement）。"""
    _state, client = app_client
    for method, payload in (
        ("exportReport", {"engagementId": "eng-nope", "format": "md"}),
        ("listArtifacts", {"engagementId": "eng-nope"}),
        ("report", {"engagementId": "eng-nope"}),
    ):
        res = _post(client, method, payload)
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "unknown_engagement"


def test_export_report_not_found_before_generate(app_client) -> None:
    """尚未触发 report 落盘的任务书 → exportReport not_found 业务错误。"""
    _state, client = app_client
    # 造一个无任何产物的空任务书（直接登记，不走 run）
    _state.create_engagement("空任务书", engagement_id="eng-empty")
    res = _post(client, "exportReport", {"engagementId": "eng-empty", "format": "md"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


def test_export_report_pdf_download(app_client) -> None:
    """exportReport（pdf）→ HTTP 200 + application/pdf，body 以 %PDF 开头。"""
    _state, client = app_client
    _trigger_report(client)
    res = _post(client, "exportReport", {"engagementId": DEMO_ENGAGEMENT_ID, "format": "pdf"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    disposition = res.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert f"report-{DEMO_ENGAGEMENT_ID}.pdf" in disposition
    body = res.content
    assert body[:4] == b"%PDF"  # PDF 魔数
    assert len(body) > 1000  # 非空壳（封面 + 章节实际渲染）


def test_export_report_bad_format(app_client) -> None:
    """format 非法（pdf 已是合法格式）→ bad_request。"""
    _state, client = app_client
    _trigger_report(client)
    res = _post(client, "exportReport", {"engagementId": DEMO_ENGAGEMENT_ID, "format": "xml"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_request"


def test_report_without_snapshot_returns_empty_artifacts(app_client) -> None:
    """无 completed 画像会话的任务书 → report 返回 artifacts: []（向后兼容增量）。"""
    _state, client = app_client
    _state.create_engagement("进行中任务书", engagement_id="eng-running")
    _state.add_session("sess-run", "10.0.0.1", "eng-running", "running")
    body = _post(client, "report", {"engagementId": "eng-running"}).json()
    assert body["ok"] is True
    assert body["result"]["status"] == "drafting"
    assert body["result"]["artifacts"] == []
