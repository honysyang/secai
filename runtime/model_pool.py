"""多模型灾备池：额度/限流/API 失败时自动切换模型并继续同一会话。

设计要点：
- 主模型（adapters.config.MODEL）始终作为默认入口；
- ESCALATION_MODELS 中配置的候选模型作为灾备池；
- 切换时保持同一个 SQLiteSession，确保会话上下文连续；
- 记录已失败模型，避免反复重试同一个坏模型；
- 所有模型耗尽后抛出 ModelExhaustedError，由外层调度器决策。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from openai import APIError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel

from adapters.config import (API_KEY, BASE_URL, MODEL_NAME, get_model)
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
    # 维度 2 补充：400 且消息含模型/配额关键词时也视为模型失败
    if isinstance(status_code, int) and status_code == 400:
        if any(k in msg for k in ("model", "quota", "额度", "不存在", "not exist", "invalid")):
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


def is_permanent_model_failure(exc: Exception) -> bool:
    """判断是否「永久性」模型失败（鉴权失败 / 模型名错误），这类失败重试无意义。

    与之相对的是暂时性失败（限流 / 超时 / 5xx），冷却后可重试同模型。
    """
    msg = str(exc).lower()
    # 鉴权 / 密钥 / 模型名错误：永久失败，重试也救不了
    perm_keywords = (
        "invalid api key", "unauthorized", "authentication",
        "invalid_request_error", "model not found", "invalid model",
        "model not supported", "not exist", "不存在", "鉴权", "key 无效",
    )
    if any(k in msg for k in perm_keywords):
        return True
    # HTTP 401/403/404（鉴权 / 资源不存在）视为永久失败
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None) or getattr(exc, "body", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
    return isinstance(status_code, int) and status_code in (401, 403, 404)


class ModelPool:
    """管理主模型 + 候选模型，支持失败时切换与冷却重试。

    preferred_name：指定首选模型名或角色（如 "fast"/"strong"），把它提到池子最前作为当前首选模型。
                   双模型分工：执行者池用 fast，外层分析池用 strong，主模型兜底。
                   匹配规则：先按 role 字段匹配，再按 model name 匹配。
    """

    COOLDOWN_SECONDS = 30  # 暂时性失败后的冷却时间（冷却后可重试同模型）

    def __init__(self, preferred_name: Optional[str] = None) -> None:
        self._entries: List[ModelEntry] = []
        self._used: List[str] = []
        self._failed: Set[str] = set()                 # 永久失败（鉴权/模型名错误）
        self._transient_failed: Dict[str, float] = {}  # 暂时失败 name -> 失败时间戳

        # 解析 ESCALATION_MODELS 并保留 role 信息（用于按 role 选择）
        self._escalation_specs = []
        for item in ESCALATION_MODELS:
            if isinstance(item, dict):
                self._escalation_specs.append(item)

        # 主模型（延迟初始化已触发，这里安全构造真实实例）
        entries = [ModelEntry(
            name=MODEL_NAME,
            base_url=BASE_URL,
            api_key=API_KEY,
            model=get_model(),
            is_default=True,
        )]

        # 灾备候选模型
        for item in self._escalation_specs:
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
            entries.append(ModelEntry(
                name=name,
                base_url=base_url,
                api_key=api_key,
                model=model,
            ))

        # 双模型分工：把 preferred_name 提到最前作为当前首选模型；主模型仍在池中兜底
        if preferred_name:
            preferred_idx = self._find_entry_index(entries, preferred_name)
            if preferred_idx is not None and preferred_idx > 0:
                entries.insert(0, entries.pop(preferred_idx))

        self._entries = entries

    def _find_entry_index(self, entries: List[ModelEntry], key: str) -> Optional[int]:
        """按 role 或 name 匹配条目索引。优先 role 匹配，再 name 匹配。"""
        key_lower = key.lower()
        # 先按 role 匹配
        for i, e in enumerate(entries):
            spec = next((s for s in self._escalation_specs
                         if s.get("model") == e.name), {})
            if spec.get("role", "").lower() == key_lower:
                return i
        # 再按 name 匹配
        for i, e in enumerate(entries):
            if e.name == key or e.name.lower() == key_lower:
                return i
        return None

    def switch_to_role(self, role: str) -> Optional[ModelEntry]:
        """切换到指定 role 的可用模型。返回新条目，或 None（无可用/已在该角色）。"""
        idx = self._find_entry_index(self._entries, role)
        if idx is None:
            return None
        target = self._entries[idx]
        if not self._available(target, self.current.name):
            return None
        if target.name not in self._used:
            self._used.append(target.name)
        # 把目标条目提到最前作为当前模型
        self._entries.insert(0, self._entries.pop(idx))
        return self.current

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

    def mark_failed(self, name: str, permanent: bool = False) -> None:
        """标记模型失败。

        permanent=True：永久失败（鉴权/模型名错误），冷却后也不重试；
        permanent=False：暂时失败（限流/超时），冷却后可重试同模型。
        """
        if permanent:
            self._failed.add(name)
        else:
            self._transient_failed[name] = time.time()

    def _available(self, entry: ModelEntry, current_name: str) -> bool:
        """判断某模型当前是否可用（非当前、非永久失败、暂时失败已过冷却期）。"""
        if entry.name == current_name:
            return False
        if entry.name in self._failed:
            return False
        failed_at = self._transient_failed.get(entry.name)
        if failed_at is not None and (time.time() - failed_at) < self.COOLDOWN_SECONDS:
            return False
        return True

    def next(self, *, current_name: str = "", reason: str = "") -> Optional[ModelEntry]:
        """选择下一个可用模型。

        逻辑：
        1. 跳过当前模型、永久失败模型、冷却期内的暂时失败模型；
        2. 返回第一个可用候选（主/灾备之间可多轮切换）；
        3. 全部不可用返回 None（暂时失败由外层等待冷却后重试）。
        """
        if not current_name:
            current_name = self.current.name
        for entry in self._entries:
            if self._available(entry, current_name):
                if entry.name not in self._used:
                    self._used.append(entry.name)
                return entry
        return None

    def __repr__(self) -> str:
        names = [e.name for e in self._entries]
        return (f"ModelPool(entries={names}, used={self._used}, "
                f"failed={self._failed}, transient={list(self._transient_failed)})")
