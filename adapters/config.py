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
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, Model, set_tracing_disabled

# 加载项目根目录的 .env（放在最前，保证后续 os.getenv 能读到）
load_dotenv(Path(__file__).parent.parent / ".env")

# DeepSeek 等兼容后端不支持官方 trace endpoint，关掉，否则每轮会尝试上报
set_tracing_disabled(disabled=True)

BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
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

# 延迟初始化：避免模块一导入就要求 API Key 必须存在。
_client = None
_model = None


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
