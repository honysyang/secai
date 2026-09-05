"""R4 后半验收：H12 CTF 假设收敛 + 平台客户端收口（tests/unit/test_ctf_legacy.py）。

覆盖三组断言：
- 平台客户端单一构造：get_platform_client 为模块级单例（重复调用同一实例、按
  .env 凭证构造、platform_tools 走同一构造点），消除 demo_tools/_client/main 的重复 new；
- 提交链路可用：profiles.ctf_legacy 的提交铁律已绑定默认管线 auto_submit_flag
  middleware，_submit_flags_if_any 经 get_platform_client 单例可机械提交 + 机械通关
  判决（fake client，零网络），终局信号（TaskEnded）上抛置 fatal，无凭证走提示不提交；
- profile 可加载：profiles.ctf_legacy 包导入即收敛 finalize / build_default_task 等
  符号，demo_tools shim 仍兼容导出提交/终局符号（core.tool_pipeline 运行时依赖）。

运行：.venv/bin/python -m pytest tests/unit/test_ctf_legacy.py -v
"""
from __future__ import annotations

import pytest
from agents import RunContextWrapper

import bench_platform.platform_client as pc_mod
from bench_platform.platform_client import (
    PlatformClient,
    TaskEnded,
    get_platform_client,
)
from core.task_context import TaskContext
from core.tool_pipeline import DEFAULT_PIPELINE


# ---------------------------------------------------------------------------
# fake 平台客户端（提交链路用；杜绝真实网络）
# ---------------------------------------------------------------------------
class FakePlatformClient:
    def __init__(self):
        self.submitted = []
        self.rows = []          # list_challenges 返回的题目行

    def submit_flag(self, code: str, flag: str):
        self.submitted.append(flag)
        return {"ok": True, "correct": True, "flag": flag,
                "correct_flag_count": 1, "total_flag_count": 1}

    def list_challenges(self):
        return self.rows


# ================= 平台客户端单一构造 =================

def test_get_platform_client_is_module_level_singleton():
    a = get_platform_client()
    b = get_platform_client()
    assert a is b
    assert isinstance(a, PlatformClient)


def test_get_platform_client_builds_from_config(monkeypatch):
    """单例按 .env 凭证构造：base_url/认证头取自 bench_platform 配置常量。"""
    pc_mod._platform_client = None
    monkeypatch.setattr(pc_mod, "BENCHMARK_BASE_URL", "https://fake-bench.example")
    monkeypatch.setattr(pc_mod, "BENCHMARK_TOKEN", "tok-abc-123")
    try:
        client = get_platform_client()
        assert client.base_url == "https://fake-bench.example"
        assert client.headers == {"BENCHMARK_TOKEN": "tok-abc-123"}
        assert pc_mod.platform_configured() is True
    finally:
        pc_mod._platform_client = None


def test_platform_tools_use_singleton_client(monkeypatch):
    """platform_tools._client 与 get_platform_client 同一实例（无重复构造）。"""
    from bench_platform.platform_tools import _client
    pc_mod._platform_client = None
    try:
        assert _client() is get_platform_client()
        assert _client() is _client()
    finally:
        pc_mod._platform_client = None


def test_platform_configured_reflects_credentials(monkeypatch):
    monkeypatch.setattr(pc_mod, "BENCHMARK_BASE_URL", "")
    monkeypatch.setattr(pc_mod, "BENCHMARK_TOKEN", "")
    assert pc_mod.platform_configured() is False
    monkeypatch.setattr(pc_mod, "BENCHMARK_BASE_URL", "https://bench.example")
    monkeypatch.setattr(pc_mod, "BENCHMARK_TOKEN", "tok")
    assert pc_mod.platform_configured() is True


# ================= 提交链路可用 =================

def test_default_pipeline_auto_submit_bound_to_ctf_legacy():
    """ctf_legacy 包加载后，默认管线 auto_submit_flag 中间件绑定提交铁律。"""
    import profiles.ctf_legacy  # noqa: F401
    mw = next(m for m in DEFAULT_PIPELINE.middlewares if m.name == "auto_submit_flag")
    assert mw.submit_fn is not None
    assert mw.submit_fn.__name__ == "_submit_flags_if_any"


def test_submit_flags_mechanical_submit_and_verdict(tmp_path, monkeypatch):
    """单 flag 题：检测到 flag → 机械提交 → 平台通关 → 机械判决 finalized。"""
    from profiles.ctf_legacy.platform import _submit_flags_if_any

    fake = FakePlatformClient()
    fake.rows = [{"unique_code": "ch1", "is_completed": True}]
    monkeypatch.setattr("profiles.ctf_legacy.platform.get_platform_client", lambda: fake)
    monkeypatch.setattr("profiles.ctf_legacy.platform.platform_configured", lambda: True)

    ctx = TaskContext(workdir=tmp_path)
    ctx.current_code = "ch1"
    note = _submit_flags_if_any(RunContextWrapper(context=ctx),
                                "读取到 flag{abc_123} 提交")
    assert fake.submitted == ["flag{abc_123}"]
    assert "提交铁律" in note
    assert ctx.correct_flags == ["flag{abc_123}"]
    assert ctx.finalized is True                 # 机械通关判决，不等 LLM finalize
    assert "通关判决" in note


def test_submit_flags_duplicate_skip(tmp_path, monkeypatch):
    from profiles.ctf_legacy.platform import _submit_flags_if_any

    fake = FakePlatformClient()
    monkeypatch.setattr("profiles.ctf_legacy.platform.get_platform_client", lambda: fake)
    monkeypatch.setattr("profiles.ctf_legacy.platform.platform_configured", lambda: True)

    ctx = TaskContext(workdir=tmp_path)
    ctx.current_code = "ch1"
    ctx.submitted.add("flag{dup}")
    _submit_flags_if_any(RunContextWrapper(context=ctx), "again flag{dup}")
    assert fake.submitted == []                  # 已提交过 → 不再重复提交


def test_submit_flags_task_ended_fatal_raise(tmp_path, monkeypatch):
    """终局信号 TaskEnded：置 fatal 并上抛（与 R3 platform_tools 行为对齐）。"""
    from profiles.ctf_legacy.platform import _submit_flags_if_any

    class _EndedClient(FakePlatformClient):
        def submit_flag(self, code: str, flag: str):
            raise TaskEnded("task ended")

    monkeypatch.setattr("profiles.ctf_legacy.platform.get_platform_client",
                        lambda: _EndedClient())
    monkeypatch.setattr("profiles.ctf_legacy.platform.platform_configured", lambda: True)

    ctx = TaskContext(workdir=tmp_path)
    ctx.current_code = "ch1"
    with pytest.raises(TaskEnded):
        _submit_flags_if_any(RunContextWrapper(context=ctx), "flag{boom}")
    assert ctx.fatal == "task_ended"


def test_submit_flags_no_credential_guard(tmp_path, monkeypatch):
    """无平台凭证：不构造客户端、不提交，仅回注提示（链路安全兜底）。"""
    from profiles.ctf_legacy.platform import _submit_flags_if_any

    monkeypatch.setattr("profiles.ctf_legacy.platform.platform_configured", lambda: False)
    ctx = TaskContext(workdir=tmp_path)
    ctx.current_code = "ch1"
    note = _submit_flags_if_any(RunContextWrapper(context=ctx), "see flag{cfg}")
    assert "未配置平台凭证" in note
    assert ctx.finalized is False
    assert ctx.correct_flags == []


# ================= profile 可加载 =================

def test_ctf_legacy_profile_importable():
    """profiles.ctf_legacy 包导入即可用：收敛符号齐全 + 默认任务书可构建。"""
    import profiles.ctf_legacy as profile
    assert callable(profile.build_default_task)
    assert profile.finalize is not None
    assert profile.TSEC_TASK_FILE.exists()
    # 默认任务书走平台模板（不打印凭证内容，只验证协议描述存在）
    text = profile.build_default_task()
    assert "openapi/v1/challenges" in text


def test_demo_tools_shim_reexports_submit_symbols():
    """demo_tools shim 兼容导出：core.tool_pipeline 运行时的延迟导入不破。"""
    from demo_tools import _submit_flags_if_any, finalize
    assert _submit_flags_if_any is not None
    assert finalize is not None
