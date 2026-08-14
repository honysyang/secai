"""统一配置：env 加载 + OpenAI 客户端构造 + Chat Completions 模型。

对齐 learn-openai-agents 的 config.py 模式：
- 所有 Agent 定义只依赖本模块的 MODEL；
- DeepSeek 等 OpenAI 兼容后端没有官方 trace endpoint，必须关闭向 OpenAI 平台上报。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, set_tracing_disabled

# 加载项目根目录的 .env（放在最前，保证后续 os.getenv 能读到）
load_dotenv(Path(__file__).parent / ".env")

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

_client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY or None)
MODEL = OpenAIChatCompletionsModel(model=MODEL_NAME, openai_client=_client)
