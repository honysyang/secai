"""Phase 1 治理双层防线单元测试（gates/operation_state/enforce/refusal_guard）。"""
import json
import tempfile
from pathlib import Path

from pentest.gates import GATES, gates_list, stage_gate, gate_status, append_gate_log
from core.operation_state import (
    OperationState, load_operation_state, save_operation_state,
    operation_goal, operation_criteria, operation_criterion_done,
    operation_intent, operation_intent_done, operation_constraints, operation_progress,
)
from pentest.enforce import (
    kill_switch_active, trigger_kill_switch, clear_kill_switch,
    scan_dangerous, scan_rate, scan_ask, constraint_hits,
    is_writable, report_gate_check, operation_clear_check,
    append_enforce_log, check_tool_execution,
)
from pentest.refusal_guard import detect_refusal, RefusalGuard


class TestGates:
    def test_gates_list(self):
        gates = gates_list("pentest")
        assert len(gates) == 3
        assert gates[0]["gate_id"] == "P1"

    def test_stage_gate_p1_fail_empty(self):
        with tempfile.TemporaryDirectory() as td:
            result = stage_gate(td, "P1")
            assert not result.passed

    def test_stage_gate_p1_pass(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "blackboard.md").write_text("## 事实\n- test", encoding="utf-8")
            result = stage_gate(td, "P1")
            assert result.passed
            assert len(result.manual_remainders) > 0

    def test_gate_status(self):
        with tempfile.TemporaryDirectory() as td:
            assert not gate_status(td, "P1")

    def test_append_gate_log(self):
        with tempfile.TemporaryDirectory() as td:
            result = stage_gate(td, "P1")
            log = append_gate_log(td, result)
            assert log.exists()
            text = log.read_text(encoding="utf-8")
            assert "P1 FAIL" in text


class TestOperationState:
    def test_goal(self):
        with tempfile.TemporaryDirectory() as td:
            r = operation_goal(td, "test goal")
            assert r["ok"] and r["goal"] == "test goal"
            state = load_operation_state(td)
            assert state.goal == "test goal"

    def test_criteria(self):
        with tempfile.TemporaryDirectory() as td:
            operation_criteria(td, ["c1", "c2"])
            progress = operation_progress(td)
            assert progress["criteria_total"] == 2
            assert progress["criteria_done"] == 0
            operation_criterion_done(td, 0)
            progress = operation_progress(td)
            assert progress["criteria_done"] == 1
            assert not progress["all_clear"]
            operation_criterion_done(td, 1)
            assert operation_progress(td)["all_clear"]

    def test_intents(self):
        with tempfile.TemporaryDirectory() as td:
            operation_intent(td, "intent1")
            progress = operation_progress(td)
            assert progress["intents_open"] == 1
            operation_intent_done(td, "intent1")
            assert operation_progress(td)["intents_open"] == 0


class TestEnforce:
    def test_kill_switch(self):
        clear_kill_switch()
        assert not kill_switch_active()
        trigger_kill_switch()
        assert kill_switch_active()
        clear_kill_switch()
        assert not kill_switch_active()

    def test_scan_dangerous(self):
        assert scan_dangerous("rm -rf /") is not None
        assert scan_dangerous("DROP TABLE users") is not None
        assert scan_dangerous("nmap -sV target") is None

    def test_scan_rate(self):
        assert scan_rate("nmap -p- target") is not None
        assert scan_rate("masscan --rate 5000 target") is not None
        assert scan_rate("ffuf -u target") is not None
        assert scan_rate("nmap -sV -T3 target") is None

    def test_scan_ask(self):
        assert scan_ask("useradd testuser") is not None
        assert scan_ask("iptables -A INPUT") is not None
        assert scan_ask("nmap -sV target") is None

    def test_constraint_hits(self):
        assert constraint_hits("attack example.com", ["example.com"]) is not None
        assert constraint_hits("attack other.com", ["example.com"]) is None

    def test_is_writable(self):
        with tempfile.TemporaryDirectory() as td:
            assert is_writable(td, "file.txt")
            assert is_writable(td, td + "/sub/file.txt")
            assert not is_writable(td, "/etc/passwd")

    def test_report_gate_check(self):
        with tempfile.TemporaryDirectory() as td:
            assert report_gate_check(td) is not None

    def test_operation_clear_check(self):
        with tempfile.TemporaryDirectory() as td:
            operation_criteria(td, ["c1"])
            assert operation_clear_check(td) is not None
            operation_criterion_done(td, 0)
            assert operation_clear_check(td) is None

    def test_check_tool_execution_kill(self):
        with tempfile.TemporaryDirectory() as td:
            trigger_kill_switch()
            result = check_tool_execution(td, "bash", {"command": "ls"})
            assert not result["allowed"]
            clear_kill_switch()

    def test_check_tool_execution_dangerous(self):
        with tempfile.TemporaryDirectory() as td:
            result = check_tool_execution(td, "bash", {"command": "rm -rf /"})
            assert not result["allowed"]
            assert result["tier"] == "block"

    def test_check_tool_execution_rate(self):
        with tempfile.TemporaryDirectory() as td:
            result = check_tool_execution(td, "bash", {"command": "nmap -p- target"})
            assert not result["allowed"]

    def test_check_tool_execution_ask(self):
        with tempfile.TemporaryDirectory() as td:
            result = check_tool_execution(td, "bash", {"command": "useradd test"})
            assert not result["allowed"]
            assert result["tier"] == "ask"

    def test_check_tool_execution_pass(self):
        with tempfile.TemporaryDirectory() as td:
            result = check_tool_execution(td, "nmap", {"target": "127.0.0.1"})
            assert result["allowed"]


class TestRefusalGuard:
    def test_detect_strong(self):
        result = detect_refusal("I cannot help with that request.")
        assert result is not None and result["level"] == 1

    def test_detect_weak(self):
        result = detect_refusal("这个任务有些复杂，不能立即完成")
        assert result is not None and result["level"] == 2

    def test_detect_soft(self):
        result = detect_refusal("As an AI language model, I can...")
        assert result is not None and result["level"] == 3

    def test_detect_pass(self):
        assert detect_refusal("nmap scan result: 22/tcp open ssh") is None

    def test_guard_streak(self):
        with tempfile.TemporaryDirectory() as td:
            guard = RefusalGuard(td)
            guard.record_user_request("test request")
            r1 = guard.check_assistant("I cannot help")
            assert r1["action"] == "reanchor"
            r2 = guard.check_assistant("I cannot help")
            assert r2["action"] == "retry"
            r3 = guard.check_assistant("I cannot help")
            assert r3["action"] == "audit"
            # 正常回复重置
            r4 = guard.check_assistant("nmap result: 80 open")
            assert r4["action"] == "pass"

    def test_guard_audit_log(self):
        with tempfile.TemporaryDirectory() as td:
            guard = RefusalGuard(td)
            guard.check_assistant("I cannot help")
            guard.check_assistant("I cannot help")
            guard.check_assistant("I cannot help")
            audit = Path(td) / "refusal-audit.md"
            assert audit.exists()
