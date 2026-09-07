"""route_boost 单元测试。"""
import tempfile

from pentest.route_boost import (
    PhaseState, infer_evidence_level, build_tool_readiness,
    build_surface_guard, RouteBoost,
)


class TestPhaseState:
    def test_initial_phase(self):
        ps = PhaseState()
        assert ps.current == "recon"

    def test_infer_recon_to_analyze(self):
        ps = PhaseState()
        assert ps.infer("扫描目标端口") == "recon"
        assert ps.infer("分析漏洞证据") == "analyze"

    def test_sticky_phase(self):
        ps = PhaseState()
        ps.infer("分析漏洞")
        assert ps.current == "analyze"
        # 无关键词时保持
        assert ps.infer("继续") == "analyze"

    def test_negation_context(self):
        ps = PhaseState()
        assert ps.infer("学习如何防御扫描攻击") == "recon"


class TestEvidenceLevel:
    def test_unknown(self):
        assert infer_evidence_level({}) == "unknown"

    def test_confirmed(self):
        bb = {f"k{i}": {"status": "done"} for i in range(7)}
        bb.update({f"f{i}": {"status": "failed"} for i in range(3)})
        assert infer_evidence_level(bb) == "confirmed"

    def test_partial(self):
        bb = {"k1": {"status": "done"}, "k2": {"status": "pending"}}
        assert infer_evidence_level(bb) == "partial"


class TestSurfaceGuard:
    def test_report_phase_blocks(self):
        result = build_surface_guard("report", "nmap")
        assert not result["allowed"]

    def test_report_phase_allows_report(self):
        result = build_surface_guard("report", "complete_task")
        assert result["allowed"]

    def test_recon_phase_allows(self):
        result = build_surface_guard("recon", "nmap")
        assert result["allowed"]


class TestRouteBoost:
    def test_render_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            rb = RouteBoost(td)
            env = rb.render("扫描目标", {"k1": {"status": "done"}})
            assert env is not None
            assert "route-boost" in env
            assert "phase=recon" in env

    def test_envelope_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            rb = RouteBoost(td)
            env1 = rb.render("扫描目标")
            env2 = rb.render("扫描目标")  # 相同输入 → 无变化
            assert env1 is not None
            assert env2 is None

    def test_phase_switch_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            rb = RouteBoost(td)
            rb.render("扫描端口")
            env = rb.render("分析漏洞")
            assert env is not None
            assert "phase=analyze" in env
