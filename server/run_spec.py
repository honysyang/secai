"""runspec —— /api/run 请求体解析（新式 {task_brief, targets, scope} + 兼容旧式 allowedTargets）。

遗留①接线面：把前端/调用方描述的一次 run 请求归一成编排面可消费的 spec：
- title：任务书标题（缺省自动生成）；
- targets / labels：逐目标会话的 id（IP/主机名/CIDR）与展示标签；
- brief_text：任务书文本（LLM 注入）；可为文本或 dict（dict 时抽取 description 等，
  其 allowed_targets/… 字段并入 scope，与 pentest/scope.ScopeConstraint.from_task_brief 对齐）；
- scope：ScopeConstraint（fail-closed 授权范围；缺省 = 仅 targets 自身可访问）；
- settings：预算护栏（max_rounds / wallclock_seconds / approval_timeout_seconds，
  全部按上限收敛，防真实 LLM 烧钱）。

兼容：旧式 payload（allowedTargets/maxIntensity）原样可用（行为与 v4 一致），
新式字段存在时优先。解析失败抛 RunSpecError → api 层转 {ok:false,error.code}。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from pentest.scope import ScopeConstraint

MAX_INTENSITY_VALUES = ("passive", "active", "aggressive")

# 预算护栏：默认值 + 硬上限（真实执行的成本/时间防御）
DEFAULT_MAX_ROUNDS = 3          # 轮次上限默认 3（≤ 验收口径 5 轮）
MAX_ROUNDS_LIMIT = 10
DEFAULT_WALLCLOCK_SECONDS = 60  # 墙钟默认 60s
WALLCLOCK_LIMIT = 300
APPROVAL_TIMEOUT_LIMIT = 45     # 单次审批等待上限（无人裁决自动拒绝）
TOKEN_BUDGET = 100_000          # 单会话累计 token 硬顶（防失控）

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")
# ScopeConstraint.from_task_brief 接受的键；解析时 camelCase 转 snake_case 后过滤
_SCOPE_KEYS = frozenset(
    {"allowed_targets", "excluded_targets", "forbidden_actions", "time_window", "max_intensity"}
)
_TARGET_DICT_KEYS = ("ip", "hostname", "host", "address", "target", "url")


class RunSpecError(Exception):
    """请求体解析失败：code/message/details 对应 api 业务错误信封。"""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _snake(key: str) -> str:
    return _CAMEL_RE.sub("_", key).lower()


def _to_int(value: Any, name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize_settings(raw: Any, *, wallclock_default: int = DEFAULT_WALLCLOCK_SECONDS) -> dict:
    """settings 归一 + 收敛：max_rounds ≤10、墙钟 ≤300s、审批等待 ≤45s 且必短于墙钟。"""
    src = raw if isinstance(raw, dict) else {}
    wallclock = _to_int(
        src.get("wallclock_seconds", src.get("wallclockSeconds")),
        "wallclock_seconds", wallclock_default, 10, WALLCLOCK_LIMIT,
    )
    max_rounds = _to_int(
        src.get("max_rounds", src.get("maxRounds")),
        "max_rounds", DEFAULT_MAX_ROUNDS, 1, MAX_ROUNDS_LIMIT,
    )
    approval = _to_int(
        src.get("approval_timeout_seconds", src.get("approvalTimeoutSeconds")),
        "approval_timeout_seconds", min(30, max(5, wallclock - 5)),
        5, min(APPROVAL_TIMEOUT_LIMIT, max(5, wallclock - 5)),
    )
    return {
        "max_rounds": max_rounds,
        "wallclock_seconds": wallclock,
        "approval_timeout_seconds": approval,
        "token_budget": TOKEN_BUDGET,
    }


def _norm_scope_dict(value: Any) -> dict:
    """把 scope / task_brief 里的范围字段（camelCase 或 snake_case）归一成 snake_case 子集。"""
    out: dict[str, Any] = {}
    if not isinstance(value, dict):
        return out
    for k, v in value.items():
        key = _snake(str(k))
        if key in _SCOPE_KEYS and v is not None:
            out[key] = v
    return out


def _extract_intensity(d: dict, *, fallback: str = "active") -> str:
    raw = d.get("max_intensity")
    if raw is None:
        return fallback
    value = str(raw).lower()
    if value not in MAX_INTENSITY_VALUES:
        raise RunSpecError(
            "bad_request",
            f"max_intensity 非法（可用 {', '.join(MAX_INTENSITY_VALUES)}）",
            {"field": "max_intensity"},
        )
    return value


def _normalize_targets(raw: Any) -> tuple[list[str], dict[str, str]]:
    """targets 归一 → ([target_id, …], {target_id: 展示标签})。

    元素可为字符串或 {ip/hostname/…} dict（dict 优先 ip 作 id，hostname 拼进展示标签）。
    """
    ids: list[str] = []
    labels: dict[str, str] = {}
    if raw is None:
        return ids, labels
    if not isinstance(raw, list):
        raise RunSpecError("bad_request", "targets 必须是目标数组", {"field": "targets"})
    for item in raw:
        if isinstance(item, str):
            tid = item.strip()
            if not tid:
                continue
            ids.append(tid)
            labels[tid] = tid
            continue
        if isinstance(item, dict):
            parts: list[str] = []
            chosen = ""
            for key in _TARGET_DICT_KEYS:
                value = item.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                value = value.strip()
                if not chosen or key == "ip":
                    chosen = value
                if value not in parts:
                    parts.append(value)
            if not chosen:
                raise RunSpecError(
                    "bad_request",
                    f"targets 元素缺少可用地址字段（{'/'.join(_TARGET_DICT_KEYS)}）",
                    {"field": "targets"},
                )
            if chosen not in ids:
                ids.append(chosen)
            labels[chosen] = " · ".join(parts)
            continue
        raise RunSpecError("bad_request", "targets 元素必须是字符串或地址对象", {"field": "targets"})
    if not ids:
        raise RunSpecError("bad_request", "targets 必须是非空的目标列表", {"field": "targets"})
    return ids, labels


def _build_scope(*, allowed: list[str], excluded: list[str], forbidden: list[str],
                 time_window: Any, max_intensity: str) -> ScopeConstraint:
    window: tuple[str, str] | None = None
    if time_window not in (None, ""):
        if (not isinstance(time_window, (list, tuple)) or len(time_window) != 2
                or not all(isinstance(x, str) and x for x in time_window)):
            raise RunSpecError(
                "bad_request", "time_window 必须是 [start_iso, end_iso] 两元素数组",
                {"field": "time_window"},
            )
        window = (time_window[0], time_window[1])
    return ScopeConstraint(
        allowed_targets=[str(a) for a in (allowed or [])],
        excluded_targets=[str(e) for e in (excluded or [])],
        forbidden_actions=[str(f) for f in (forbidden or [])],
        time_window=window,
        max_intensity=max_intensity,
    )


def _default_brief(targets: list[str], scope: ScopeConstraint, title: str) -> str:
    allowed = ", ".join(scope.allowed_targets or targets) or ", ".join(targets)
    forbidden = "、".join(scope.forbidden_actions) or "无"
    return (
        f"授权渗透侦察任务书（{title}）。\n"
        f"- 授权目标：{allowed}；本次会话聚焦 {', '.join(targets)}。\n"
        f"- 强度上限：{scope.max_intensity}；禁区动作：{forbidden}。\n"
        f"- 纪律：每个动作先过 ScopeCheck，越范围目标/禁区动作一律拒绝；"
        f"确认的事实先写 blackboard 再推进；侦察结论收敛后调用 complete_task 收尾。"
    )


def normalize_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """归一 /api/run payload → start_run spec（解析失败抛 RunSpecError）。"""
    payload = payload or {}

    # ── 新式字段优先；旧式 allowedTargets 兜底（v4 兼容：无 key 场景语义不变） ──
    has_new = any(k in payload for k in ("task_brief", "targets", "scope"))
    legacy = payload.get("allowedTargets")

    title = str(payload.get("title") or "").strip() or f"任务书 {uuid.uuid4().hex[:6]}"

    if not has_new:
        # 旧式：allowedTargets 必填（保留原 bad_request 语义）
        if not isinstance(legacy, list) or not legacy or not all(
                isinstance(t, str) and t for t in legacy):
            raise RunSpecError(
                "bad_request",
                "allowedTargets（或新式 targets）必须是非空的目标列表",
                {"field": "allowedTargets"},
            )
        intensity = str(payload.get("maxIntensity") or "active").lower()
        if intensity not in MAX_INTENSITY_VALUES:
            raise RunSpecError(
                "bad_request",
                f"maxIntensity 非法（可用 {', '.join(MAX_INTENSITY_VALUES)}）",
                {"field": "maxIntensity"},
            )
        targets = [t.strip() for t in legacy if t.strip()]
        scope = ScopeConstraint(allowed_targets=targets, max_intensity=intensity)
        return {
            "title": title,
            "targets": targets,
            "labels": {t: t for t in targets},
            "brief_text": _default_brief(targets, scope, title),
            "scope": scope,
            "settings": _normalize_settings(payload.get("settings")),
        }

    # ── 新式：task_brief / targets / scope 三元组 ──
    scope_raw: dict[str, Any] = {}
    brief_text = ""
    brief = payload.get("task_brief")
    if isinstance(brief, str):
        brief_text = brief.strip()
    elif isinstance(brief, dict):
        scope_raw.update(_norm_scope_dict(brief))
        for k in ("description", "brief", "objective", "text"):
            value = brief.get(k)
            if isinstance(value, str) and value.strip():
                brief_text = value.strip()
                break
        if not brief.get("title"):
            pass  # title 已在上面取 payload.title
    elif brief is not None:
        raise RunSpecError("bad_request", "task_brief 必须是文本或对象", {"field": "task_brief"})

    raw_scope = payload.get("scope")
    if raw_scope is not None and not isinstance(raw_scope, dict):
        raise RunSpecError("bad_request", "scope 必须是对象", {"field": "scope"})
    if isinstance(raw_scope, dict):
        scope_raw.update(_norm_scope_dict(raw_scope))
    # 顶层旧式强度兼容
    if "max_intensity" not in scope_raw and payload.get("maxIntensity"):
        scope_raw["max_intensity"] = payload["maxIntensity"]

    targets, labels = _normalize_targets(payload.get("targets"))

    allowed = [str(a) for a in (scope_raw.get("allowed_targets") or []) if str(a).strip()]
    if targets and allowed:
        # targets 是本次实际授权会话；scope.allowed 外的目标并入（API 显式目标 = 授权）
        for tid in targets:
            if tid not in allowed:
                allowed.append(tid)
    elif targets and not allowed:
        allowed = list(targets)
    if not allowed and not targets:
        raise RunSpecError(
            "bad_request",
            "缺少授权目标：需提供 targets 或 scope.allowed_targets（允许为空时直接给 targets）",
            {"field": "targets"},
        )

    scope = _build_scope(
        allowed=allowed,
        excluded=scope_raw.get("excluded_targets") or [],
        forbidden=scope_raw.get("forbidden_actions") or [],
        time_window=scope_raw.get("time_window"),
        max_intensity=_extract_intensity(scope_raw),
    )
    if not targets:
        targets = list(allowed)
        labels = {t: t for t in allowed}

    return {
        "title": title,
        "targets": targets,
        "labels": labels,
        "brief_text": brief_text or _default_brief(targets, scope, title),
        "scope": scope,
        "settings": _normalize_settings(payload.get("settings")),
    }


__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_WALLCLOCK_SECONDS",
    "MAX_INTENSITY_VALUES",
    "MAX_ROUNDS_LIMIT",
    "RunSpecError",
    "TOKEN_BUDGET",
    "WALLCLOCK_LIMIT",
    "normalize_run_payload",
]
