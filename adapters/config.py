"""统一配置：env 加载 + OpenAI 客户端构造 + Chat Completions 模型。

对齐 learn-openai-agents 的 config.py 模式：
- 所有 Agent 定义只依赖本模块的 MODEL；
- DeepSeek 等 OpenAI 兼容后端没有官方 trace endpoint，必须关闭向 OpenAI 平台上报。

说明：
    为了使 `python -m app.main --help` 及无 API Key 的启动阶段能够正常加载，
    `_client` 与 `MODEL` 采用延迟初始化。`MODEL` 是一个 agents SDK 兼容的 `Model`
    代理；首次调用 `get_response` / `stream_response` 时才创建 `AsyncOpenAI` 实例。
    未配置 API Key 时只有真正调用 LLM 才会报错。
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, Model, set_tracing_disabled

# 加载项目根目录的 .env（放在最前，保证后续 os.getenv 能读到）
load_dotenv(Path(__file__).parent.parent / ".env")

# DeepSeek 等兼容后端不支持官方 trace endpoint，关掉，否则每轮会尝试上报
set_tracing_disabled(disabled=True)

# 兼容用户遗漏 /v1 后缀的网关地址（OpenAI SDK 会在 base_url 后拼 /chat/completions）
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
if not BASE_URL.endswith("/v1"):
    BASE_URL += "/v1"
MODEL_NAME = os.getenv("LLM_MODEL", "deepseek-chat")
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""

# OpenVPN 连接配置（任务需要走 VPN/内网时，由 connect_vpn 工具后台启用）
VPN_CMD = os.getenv("VPN_CMD", "openvpn").strip()
VPN_CONFIG = os.getenv("VPN_CONFIG", "").strip()
VPN_AUTH = os.getenv("VPN_AUTH", "").strip()

# 靶场平台（TSecBench）——跑分任务的认证凭证
BENCHMARK_BASE_URL = os.getenv("BENCHMARK_BASE_URL", "").rstrip("/")
BENCHMARK_TOKEN = os.getenv("BENCHMARK_TOKEN", "")

# 成本治理（爆破/hint 预算、换脑、挂起）已抽离到 budget.py

# 延迟初始化：避免模块一导入阶段就要求 API Key 必须存在。
_client = None
_model = None
_planner_model = None
_fast_model = None


def _parse_escalation_models():
    """解析 ESCALATION_MODELS 环境变量，返回规范化的候选列表（每项 dict：model, base_url, api_key, role）。"""
    raw = os.getenv("ESCALATION_MODELS", "[]")
    try:
        models = json.loads(raw)
    except json.JSONDecodeError:
        models = []
    if not isinstance(models, list):
        return []
    out = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("model")
        if not name or not isinstance(name, str):
            continue
        base = (item.get("base_url") or BASE_URL).rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        key = item.get("api_key") or API_KEY
        role = item.get("role", "backup")
        out.append({"name": name, "base_url": base, "api_key": key, "role": role})
    return out


def _find_model_by_role(role):
    """返回指定 role 的第一个候选 spec；未找到返回 None。"""
    for spec in _parse_escalation_models():
        if spec.get("role") == role:
            return spec
    return None


def _find_model_by_name(name):
    """返回指定 model 名的候选 spec；未找到返回 None。"""
    for spec in _parse_escalation_models():
        if spec.get("name") == name:
            return spec
    return None


# 预解析模型名（字符串常量），供 runtime 层按角色构建 ModelPool 使用。
# 未配置或找不到时回退主模型名。
_FAST_MODEL_NAME = (_find_model_by_role("fast") or {}).get("name") or MODEL_NAME
_PLANNER_MODEL_NAME = (_find_model_by_role("strong") or {}).get("name") or MODEL_NAME


class _ModelProxy(Model):
    """agents SDK 兼容的模型代理，首次真正调用模型时才初始化底层客户端。

    所有 `model=MODEL` 的 Agent 定义保持原样；Runner 调用模型时才会触发
    `get_model()` 创建 `AsyncOpenAI` 实例。
    """

    async def _cleanup_on_run_end(self, owner: object) -> None:
        return await get_model()._cleanup_on_run_end(owner)

    async def close(self) -> None:
        return await get_model().close()

    def get_retry_advice(self, request):
        return get_model().get_retry_advice(request)

    async def get_response(self, *args, **kwargs):
        return await get_model().get_response(*args, **kwargs)

    def stream_response(self, *args, **kwargs):
        return get_model().stream_response(*args, **kwargs)

    def __repr__(self) -> str:
        return f"_ModelProxy(target={MODEL_NAME}, initialized={_model is not None})"


class _PlannerModelProxy(Model):
    """强模型代理（双模型分工）：Planner/Coach/Reporter/Compactor 分析型智能体使用，延迟初始化。"""

    def _init(self):
        global _planner_model
        if _planner_model is not None:
            return _planner_model
        spec = _find_model_by_role("strong")
        if spec is None or spec.get("name") == MODEL_NAME:
            _planner_model = get_model()
        else:
            client = AsyncOpenAI(base_url=spec["base_url"], api_key=spec["api_key"] or None)
            _planner_model = OpenAIChatCompletionsModel(model=spec["name"], openai_client=client)
        return _planner_model

    async def _cleanup_on_run_end(self, owner: object) -> None:
        return await self._init()._cleanup_on_run_end(owner)

    async def close(self) -> None:
        return await self._init().close()

    def get_retry_advice(self, request):
        return self._init().get_retry_advice(request)

    async def get_response(self, *args, **kwargs):
        return await self._init().get_response(*args, **kwargs)

    def stream_response(self, *args, **kwargs):
        return self._init().stream_response(*args, **kwargs)

    def __repr__(self) -> str:
        return f"_PlannerModelProxy(target={_PLANNER_MODEL_NAME}, initialized={_planner_model is not None})"


class _FastModelProxy(Model):
    """快模型代理（双模型分工）：Executor 执行者智能体使用，延迟初始化。"""

    def _init(self):
        global _fast_model
        if _fast_model is not None:
            return _fast_model
        spec = _find_model_by_role("fast")
        if spec is None or spec.get("name") == MODEL_NAME:
            _fast_model = get_model()
        else:
            client = AsyncOpenAI(base_url=spec["base_url"], api_key=spec["api_key"] or None)
            _fast_model = OpenAIChatCompletionsModel(model=spec["name"], openai_client=client)
        return _fast_model

    async def _cleanup_on_run_end(self, owner: object) -> None:
        return await self._init()._cleanup_on_run_end(owner)

    async def close(self) -> None:
        return await self._init().close()

    def get_retry_advice(self, request):
        return self._init().get_retry_advice(request)

    async def get_response(self, *args, **kwargs):
        return await self._init().get_response(*args, **kwargs)

    def stream_response(self, *args, **kwargs):
        return self._init().stream_response(*args, **kwargs)

    def __repr__(self) -> str:
        return f"_FastModelProxy(target={_FAST_MODEL_NAME}, initialized={_fast_model is not None})"


def get_model() -> OpenAIChatCompletionsModel:
    """返回 Chat Completions 模型；首次调用时创建 OpenAI 客户端。"""
    global _client, _model
    if _model is None:
        _client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY or None)
        _model = OpenAIChatCompletionsModel(model=MODEL_NAME, openai_client=_client)
    return _model


# 兼容原有 `from adapters.config import MODEL` 写法：
# 导入阶段不报错，运行时首次调用模型方法才初始化。
MODEL = _ModelProxy()
PLANNER_MODEL = _PlannerModelProxy()
FAST_MODEL = _FastModelProxy()
FAST_MODEL_NAME = _FAST_MODEL_NAME
PLANNER_MODEL_NAME = _PLANNER_MODEL_NAME
