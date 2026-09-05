"""L6 distiller 单测 —— 规则回退蒸馏四物 + LLM 可注入（一律 mock，无真实 LLM 调用）。

验收：给定 mock 事件日志，Distiller 产出四物且 schema 校验通过；
LLM 缺失/失败/schema 非法 → 自动规则回退（离线可用）。
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict

from pentest.learning import Distiller, EngagementTrace, persist_distillate, validate_distillate
from pentest.learning.store import LearningStore
from tests.unit.learning.helpers import (
    T0,
    make_chain,
    make_dead,
    make_event,
    make_fact,
    make_hypothesis,
    make_profile,
)


def _run(coro):
    return asyncio.run(coro)


def _rich_trace() -> EngagementTrace:
    """完整 mock engagement：1 条 sqlmap 异常事件 + validated 链 + deadEnd + 覆盖事实。"""
    events = [
        make_event("e1", 1, "hypothesis/created", {"summary": "wordpress RCE 假设入队"}),
        make_event("e2", 2, "tool/exec", {"tool": "nuclei",
                                          "command": "nuclei -u http://10.0.0.1 -t cve-2023-xxxx"}),
        make_event("e3", 3, "tool/error", {"tool": "sqlmap",
                                           "command": "sqlmap -u http://10.0.0.1/login --data x",
                                           "error": "JSON body 需加 --json 参数，服务端 400 拒绝"}),
        make_event("e4", 4, "learning/playbook", {"playbook_id": "pb-e1-T1-c1"}),
        make_event("e5", 5, "learning/lesson", {"lesson_id": "les-e1-sqlmap"}),
        make_event("e6", 6, "learning/lesson", {"lesson_id": "les-e1-nuclei"}),
        make_event("e7", 7, "exploit/run", {"tool": "http", "command": "curl http://10.0.0.1/exp.php",
                                            "summary": "RCE 验证成功"}),
    ]
    hypotheses = [
        make_hypothesis("h1", statement="wordpress 插件 RCE", status="validated",
                        success_criteria="status_code=200 && body contains flag", linked=["e7"]),
        make_hypothesis("h2", statement="登录表单 SQL 注入", status="falsified",
                        failure_criteria="无注入点", linked=["e3"]),
        make_hypothesis("h3", statement="nuclei CVE 扫描", status="pending", linked=["e2"]),
    ]
    profile = make_profile(
        hypotheses=hypotheses,
        validated=[make_chain("c1", steps=["h1"], impact="远程代码执行", evidence_refs=["e7"])],
        dead_ends=[make_dead()],
    )
    return EngagementTrace(
        engagement_id="e1",
        events=events,
        blackboard=[make_fact()],
        profiles=[profile],
        tokens_used=500,
        started_at=T0,
        ended_at="2026-09-05T09:00:00Z",
    )


# ---------------------------------------------------------------------------
# 规则回退：四物 + schema
# ---------------------------------------------------------------------------
def test_rule_fallback_distills_four_items_and_passes_schema() -> None:
    """规则回退产出 lessons/playbooks/negatives/metrics 四物，schema 校验全过。"""
    output = Distiller(now_iso=T0).distill_by_rules(_rich_trace())
    assert set(output) == {"lessons", "playbooks", "negatives", "metrics"}
    assert validate_distillate(output) == []
    assert len(output["lessons"]) >= 1
    assert len(output["playbooks"]) >= 1
    assert len(output["negatives"]) >= 1


def test_rule_lessons_extracted_from_tool_error_events() -> None:
    """Lessons 从工具异常事件提取：tool: 标签 + 事件摘要进 content。"""
    output = Distiller(now_iso=T0).distill_by_rules(_rich_trace())
    lesson = output["lessons"][0]
    assert lesson["title"] == "sqlmap 调用注意事项"
    assert "tool:sqlmap" in lesson["context_tags"]
    assert "--json" in lesson["content"] or "JSON" in lesson["content"]
    assert lesson["source_engagement"] == "e1"
    assert lesson["lesson_id"] == "les-e1-sqlmap"
    # 无异常事件 → 不产出 lesson
    plain_trace = EngagementTrace(engagement_id="e0", events=[make_event("x", 1, "tool/exec",
                                                                         {"tool": "nmap", "output": "22/tcp open"})])
    assert Distiller(now_iso=T0).distill_by_rules(plain_trace)["lessons"] == []


def test_rule_playbook_only_when_validated_chain_exists() -> None:
    """Playbook 仅当 validated AttackChain 存在时生成（规则纪律）。"""
    no_chain = EngagementTrace(engagement_id="e1", profiles=[make_profile(validated=[])])
    assert Distiller(now_iso=T0).distill_by_rules(no_chain)["playbooks"] == []

    output = Distiller(now_iso=T0).distill_by_rules(_rich_trace())
    playbook = output["playbooks"][0]
    assert playbook["playbook_id"] == "pb-e1-T1-c1"
    assert playbook["name"].startswith("wordpress 攻击链剧本")
    # 前提 = 目标指纹（与 retriever 同口径）
    assert {"tech:wordpress", "service:http", "os:linux"} <= set(playbook["preconditions"])
    # 步骤 = tool + params_template（目标字面量泛化为 {target}）+ expected_check
    step = playbook["steps"][0]
    assert step["tool"] == "http"
    assert step["params_template"] == "curl http://{target}/exp.php"
    assert "10.0.0.1" not in step["params_template"]
    assert step["expected_check"] == "status_code=200 && body contains flag"
    assert "status_code=200 && body contains flag" in playbook["expected_checks"]


def test_rule_negatives_generalized_from_deadends() -> None:
    """Negatives 从 deadEnds 泛化：路径 + 排除方法 + 指纹入 pattern。"""
    output = Distiller(now_iso=T0).distill_by_rules(_rich_trace())
    negative = output["negatives"][0]
    assert negative["negative_id"] == "neg-e1-d1"
    assert negative["pattern"].startswith("SQL注入: 登录表单 → sqlmap → 无注入点 经 sqlmap 排除")
    assert "tech:wordpress" in negative["pattern"]
    assert "sqlmap --level 5" in negative["evidence_summary"]
    assert negative["overturned"] is False


def test_rule_metrics_computed_correctly() -> None:
    """Metrics（MetricDelta）：命中率 1/(1+1)=0.5；deadEnd 被新事实推翻 1/1；
    token 效率 500/1；剧本复用 1、lesson 注入 2。"""
    output = Distiller(now_iso=T0).distill_by_rules(_rich_trace())
    metrics = output["metrics"]
    assert metrics["engagement_id"] == "e1"
    assert metrics["hypothesis_hit_rate"] == 0.5
    assert metrics["deadend_overturn_rate"] == 1.0
    assert metrics["inconclusive_rate"] == 0.0
    assert metrics["token_per_validated_chain"] == 500.0
    assert metrics["playbook_reuse"] == 1
    assert metrics["lesson_injection"] == 2


def test_empty_trace_still_schema_valid() -> None:
    """空 engagement（无事件/无目标）→ 空表 + 全零指标，schema 恒合法。"""
    output = Distiller(now_iso=T0).distill_by_rules(EngagementTrace(engagement_id="e-empty"))
    assert validate_distillate(output) == []
    assert output["lessons"] == [] and output["playbooks"] == [] and output["negatives"] == []
    assert output["metrics"]["hypothesis_hit_rate"] == 0.0
    assert output["metrics"]["token_per_validated_chain"] == 0.0


# ---------------------------------------------------------------------------
# LLM 可注入（mock）：合法产出走 LLM / 非法产出规则回退
# ---------------------------------------------------------------------------
def test_llm_injectable_payload_used_and_validated() -> None:
    """注入 async mock LLM：返回 JSON 字符串 → 使用其产出且 schema 校验通过。"""
    calls: list[str] = []

    async def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return (
            '{"lessons": [{"title": "LLM 蒸馏的经验", "context_tags": ["tool:sqlmap"], '
            '"content": "mock llm 教训内容"}], "playbooks": [], "negatives": [], "metrics": {}}'
        )

    output = _run(Distiller(llm=fake_llm, now_iso=T0).distill(_rich_trace()))
    assert len(calls) == 1
    assert len(output["lessons"]) == 1
    assert output["lessons"][0]["title"] == "LLM 蒸馏的经验"
    assert output["lessons"][0]["lesson_id"] == "les-e1-0"  # 缺省 id 自动补全
    assert output["metrics"]["engagement_id"] == "e1"  # 缺失 metrics → 规则兜底
    assert validate_distillate(output) == []


def test_llm_dict_return_directly_accepted() -> None:
    async def fake_llm(prompt: str) -> dict:
        return {
            "lessons": [],
            "playbooks": [{
                "name": "LLM 剧本",
                "preconditions": ["tech:tomcat"],
                "steps": [{"tool": "nuclei", "params_template": "nuclei -u {target}",
                           "expected_check": "matched contains vuln"}],
                "expected_checks": ["matched contains vuln"],
            }],
            "negatives": [],
        }

    output = _run(Distiller(llm=fake_llm, now_iso=T0).distill(EngagementTrace(engagement_id="e1")))
    assert output["playbooks"][0]["name"] == "LLM 剧本"
    assert validate_distillate(output) == []


def test_llm_invalid_json_falls_back_to_rules() -> None:
    """LLM 返回非法 JSON → 自动规则回退，产出与规则路径完全一致。"""
    calls: list[str] = []

    async def broken_llm(prompt: str) -> str:
        calls.append(prompt)
        return "不是 JSON{{{"

    distiller = Distiller(llm=broken_llm, now_iso=T0)
    trace = _rich_trace()
    assert _run(distiller.distill(trace)) == distiller.distill_by_rules(trace)
    assert len(calls) == 1
    assert validate_distillate(distiller.distill_by_rules(trace)) == []


def test_llm_exception_falls_back_to_rules() -> None:
    async def exploding_llm(prompt: str) -> str:
        raise RuntimeError("provider 不可用")

    distiller = Distiller(llm=exploding_llm, now_iso=T0)
    trace = _rich_trace()
    assert _run(distiller.distill(trace)) == distiller.distill_by_rules(trace)


def test_llm_missing_required_fields_falls_back() -> None:
    """LLM 产出缺必填字段（lesson 无 content）→ schema 归一失败 → 规则回退。"""
    async def sparse_llm(prompt: str) -> dict:
        return {"lessons": [{"title": "缺 content"}], "playbooks": [], "negatives": []}

    distiller = Distiller(llm=sparse_llm, now_iso=T0)
    output = _run(distiller.distill(_rich_trace()))
    assert output["lessons"][0]["title"] == "sqlmap 调用注意事项"  # 规则回退产物


def test_persist_distillate_writes_four_tables(tmp_path) -> None:
    """蒸馏产出可整体写回学习库四表。"""
    output = Distiller(now_iso=T0).distill_by_rules(_rich_trace())
    store = LearningStore(tmp_path / "learning.db")
    persist_distillate(output, store)
    assert len(store.query_lessons()) == 1
    assert len(store.query_playbooks()) == 1
    assert len(store.query_negatives()) == 1
    restored = store.get_playbook("pb-e1-T1-c1")
    assert restored is not None
    assert set(restored.preconditions) == {"tech:wordpress", "service:http", "os:linux"}
    assert restored.steps[0].tool == "http"
    metrics = store.get_metrics("e1")
    assert metrics is not None
    assert metrics.hypothesis_hit_rate == 0.5
    assert asdict(metrics) == output["metrics"]
