"""多模型灾备池：额度/限流/API 失败时自动切换模型并继续同一会话。

设计要点：
- 主模型（adapters.config.MODEL）始终作为默认入口；
- ESCALATION_MODELS 中配置的候选模型作为灾备池；
- 切换时保持同一个 SQLiteSession，确保会话上下文连续；
- 记录已失败模型，避免反复重试同一个坏模型；
- 所有模型耗尽后抛出 ModelExhaustedError，由外层调度器决策。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set

from openai import APIError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel

from adapters.config import API_KEY, BASE_URL, MODEL_NAME, get_model
from runtime.budget import ESCALATION_MODELS


@dataclass
class ModelEntry:
    """模型池中的一个条目。"""
    name: str
    base_url: str
    api_key: str
    model: OpenAIChatCompletionsModel
    is_default: bool = False


class ModelExhaustedError(RuntimeError):
    """候选池与主模型全部失败，无模型可用。"""


def is_model_failure(exc: Exception) -> bool:
    """判断异常是否属于「模型服务端失败/额度/限流/鉴权」类，应触发灾备切换。

    判断维度：
    1. OpenAI SDK 标准异常类型（APIError / RateLimitError / AuthenticationError 等）
    2. 异常对象携带的 HTTP 状态码（401/403/404/429/5xx）
    3. 异常文本中的常见错误关键词（覆盖百度等兼容网关）

    不包含：MaxTurnsExceeded（回合上限，不是模型问题）。
    """
    # 维度 1：OpenAI SDK 标准异常
    if isinstance(exc, (APIError, RateLimitError, AuthenticationError,
                        APIStatusError, APITimeoutError)):
        return True

    msg = str(exc).lower()

    # 维度 2：HTTP 状态码（兼容百度等网关直接返回 401/403/404/429/5xx 的场景）
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        # 尝试从 body / response 里取
        response = getattr(exc, "response", None) or getattr(exc, "body", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and (status_code in (401, 403, 404, 429)
                                           or 500 <= status_code < 600):
        return True
    # 异常 message 里也可能直接出现 "401" / "429" 等状态码
    if any(f" {code}" in msg or f"{code}:" in msg for code in (401, 403, 404, 429, 500, 502, 503, 504)):
        return True

    # 维度 3：错误文本关键词（覆盖百度/智谱/通义等兼容网关）
    keywords = (
        # 额度 / 余额 / 配额
        "insufficient_quota", "quota", "insufficient balance",
        "account balance", "no quota", "credit exhausted", "余额不足",
        # 限流
        "rate limit", "too many requests", "throttled", "request rate",
        # 鉴权
        "invalid api key", "unauthorized", "authentication", "token invalid",
        "api key invalid", "invalid token", "鉴权", "key 无效", "被禁用",
        # 超时
        "timeout", "timed out",
        # 服务端不可用
        "service unavailable", "internal server error", "bad gateway",
        "gateway timeout", " temporarily unavailable",
        # 模型/请求不可用
        "model not found", "model is not available", "invalid model",
        "model not supported", "请求体不合协议",
    )
    if any(k in msg for k in keywords):
        return True
    return False


class ModelPool:
    """管理主模型 + 候选模型，支持失败时切换。"""

    def __init__(self) -> None:
        self._entries: List[ModelEntry] = []
        self._used: List[str] = []
        self._failed: Set[str] = set()

        # 主模型（延迟初始化已触发，这里安全构造真实实例）
        self._entries.append(ModelEntry(
            name=MODEL_NAME,
            base_url=BASE_URL,
            api_key=API_KEY,
            model=get_model(),
            is_default=True,
        ))

        # 灾备候选模型
        for item in ESCALATION_MODELS:
            if not isinstance(item, dict):
                continue
            name = item.get("model")
            if not name or not isinstance(name, str):
                continue
            # 跳过与主模型同名且同 base_url 的重复项
            base_url = (item.get("base_url") or BASE_URL).rstrip("/")
            api_key = item.get("api_key") or API_KEY
            if name == MODEL_NAME and base_url == BASE_URL.rstrip("/"):
                continue
            client = AsyncOpenAI(base_url=base_url, api_key=api_key or None)
            model = OpenAIChatCompletionsModel(model=name, openai_client=client)
            self._entries.append(ModelEntry(
                name=name,
                base_url=base_url,
                api_key=api_key,
                model=model,
            ))

    @property
    def default(self) -> ModelEntry:
        return self._entries[0]

    @property
    def current(self) -> ModelEntry:
        """返回当前应使用的模型条目。"""
        if self._used:
            last_name = self._used[-1]
            for entry in self._entries:
                if entry.name == last_name:
                    return entry
        return self.default

    @property
    def has_alternative(self) -> bool:
        """是否存在非主候选模型。"""
        return any(not e.is_default for e in self._entries)

    def mark_failed(self, name: str) -> None:
        """标记某个模型已失败。"""
        self._failed.add(name)

    def next(self, *, current_name: str = "", reason: str = "") -> Optional[ModelEntry]:
        """选择下一个可用模型。

        逻辑：
        1. 优先选不是当前模型、且未失败的候选；
        2. 候选耗尽后回退未失败的主模型；
        3. 所有模型均失败时返回 None（外层应抛 ModelExhaustedError）。

        允许在多个模型间反复切换，直到全部失败。
        """
        # 优先选未失败的非当前候选（支持同一候选被多次复用）
        for entry in self._entries:
            if entry.name == current_name:
                continue
            if entry.name in self._failed:
                continue
            if entry.name not in self._used:
                self._used.append(entry.name)
            return entry

        # 所有模型都失败或只剩当前模型
        return None

    def __repr__(self) -> str:
        names = [e.name for e in self._entries]
        return f"ModelPool(entries={names}, used={self._used}, failed={self._failed})"
