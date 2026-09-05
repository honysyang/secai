"""Verifier —— 触发条件满足时的验收判定（双实现可切换，SECAI-PT 裁决 #1 不做硬架构）。

规格来源：SECAI-PT 终极执行计划 v4 第五部分 R3。
设计裁决：
- Verifier 是协议（Protocol）：async verify(profile, contract, events) -> Verdict；
- MechanicalVerifier：纯函数零 token（默认）——只用 pentest.contract.AcceptanceContract.
  evaluate() 的结果按确定性规则映射五类 Verdict，无 IO、无 LLM；
- LLMVerifier：低频唤醒（由调用方按触发条件控制，本层不内置唤醒策略）——把事件日志
  尾部 + blackboard 摘要 + 机械初判拼成提示词，交给注入的 llm 回调，输出 Verdict；
  llm 为空 / 解析失败 → 回退机械初判（fail-safe）。
- 五类 Verdict：
    verified_done  契约全过，验收达成（可出报告）；
    continue       有实质进展但未达验收，继续当前方向；
    redirect       当前方向无进展 / 攻击链为零且覆盖不足，换方向；
    corrective     质量信号异常（死路占比过高 = 覆盖率造假信号），先纠正再验证；
    need_human     机械无法裁决（覆盖已达标但攻击链缺项等），需要人工判断。
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from pentest.contract import AcceptanceContract
    from pentest.target_profile import TargetProfile

Verdict = Literal["continue", "redirect", "corrective", "verified_done", "need_human"]

VERDICTS: tuple[Verdict, ...] = (
    "continue", "redirect", "corrective", "verified_done", "need_human",
)

# LLM 输出归一化：把自由文本里的 verdict 记号提取成标准枚举
_VERDICT_KEYWORDS: dict[Verdict, tuple[str, ...]] = {
    "verified_done": ("verified_done", "verified done", "verifieddone", "done", "verify_done"),
    "continue": ("continue", "continue_exec", "keep going"),
    "redirect": ("redirect", "redirection"),
    "corrective": ("corrective", "correct"),
    "need_human": ("need_human", "need human", "human", "escalate"),
}


class Verifier(Protocol):
    """Verifier 协议：verify 输入 profile + contract（+可选事件证据），输出 Verdict。"""

    name: str

    async def verify(
        self,
        profile: TargetProfile,
        contract: AcceptanceContract,
        events: list[dict] | None = None,
    ) -> Verdict:
        ...


def parse_verdict(text: str) -> Verdict:
    """把 LLM 输出/任意文本解析成标准 Verdict；无法识别 → need_human（fail-safe）。纯函数。"""
    if not text:
        return "need_human"
    low = text.strip().lower()
    # 常见包装：VERDICT: verified_done / ```verified_done``` / "verified_done" 等
    for token in low.replace("`", "").replace('"', "'").replace("：", ":").replace(" ", "_").split():
        for verdict, keywords in _VERDICT_KEYWORDS.items():
            if token in keywords:
                return verdict
    # 宽松兜底：整串包含关键词
    for verdict, keywords in _VERDICT_KEYWORDS.items():
        for kw in keywords:
            if kw in low:
                return verdict
    return "need_human"


def format_evidence(events: list[dict] | None, profile: TargetProfile, *, max_chars: int = 6000) -> str:
    """把事件日志尾部 + profile 摘要格式化为证据文本（LLMVerifier 输入）。纯函数。"""
    parts: list[str] = []
    if events:
        lines: list[str] = []
        budget = max_chars
        for ev in reversed(events):
            line = json.dumps(
                {"kind": ev.get("kind"), "data": ev.get("data")},
                ensure_ascii=False,
            )
            if len(line) + 1 > budget:
                if not lines:
                    lines.append(line[: max(0, budget)])  # 首条事件至少保留片段
                break
            lines.append(line)
            budget -= len(line) + 1
            if budget <= 0:
                break
        parts.append("== 最近事件 ==")
        parts.extend(reversed(lines))
    parts.append("== profile 快照 ==")
    parts.append(f"target={profile.target_id} 覆盖率={profile.coverage_ratio():.0%}")
    parts.append(f"攻击面: {len(profile.attack_surface.ports)} 端口 / "
                 f"{len(profile.attack_surface.web)} Web")
    parts.append(f"已验证攻击链={len(profile.validated)} 死路={len(profile.dead_ends)} "
                 f"假设={len(profile.hypotheses)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# MechanicalVerifier —— 纯函数初筛（默认，零 token）
# ---------------------------------------------------------------------------

class MechanicalVerifier:
    """纯函数 Verifier：AcceptanceContract.evaluate() 结果 → 五类 Verdict。无 IO 无副作用。

    判定规则（顺序短路，见 verify docstring）：
      1. passed                       → verified_done
      2. dead_end_ratio 超限           → corrective（覆盖率造假信号）
      3. 零进展（覆盖 0 且无任何交付物）→ redirect
      4. 攻击链为 0 且覆盖不足         → redirect（exploit 方向尚未打开）
      5. 覆盖达标但攻击链缺项          → need_human（需人工裁决是否收尾）
      6. 其余                          → continue（有进展，继续）
    """

    name: str = "mechanical"

    async def verify(
        self,
        profile: TargetProfile,
        contract: AcceptanceContract,
        events: list[dict] | None = None,
    ) -> Verdict:
        return self.verify_sync(profile, contract)

    def verify_sync(self, profile: TargetProfile, contract: AcceptanceContract) -> Verdict:
        """同步纯函数本体（单测可直调，无需事件循环）。"""
        check = contract.evaluate(profile)

        # 1. 契约全过 → 验收达成
        if check.passed:
            return "verified_done"

        # 2. 死路占比过高 → 质量纠正
        if check.dead_end_ratio > contract.max_dead_end_ratio:
            return "corrective"

        # 3. 零进展（无覆盖率、无任何已达成交付物）→ 换方向
        if check.coverage == 0.0 and not any(v > 0 for v in check.fulfilled.values()):
            return "redirect"

        # 4. 攻击链为零 + 覆盖不足 → 当前 exploit 方向无效，换方向
        chain_fulfilled = check.fulfilled.get("validated_chain", 0)
        chain_required = check.required.get("validated_chain", 0)
        if chain_required and chain_fulfilled == 0 and check.coverage < contract.minimum_coverage:
            return "redirect"

        # 5. 覆盖达标但攻击链未达要求 → 机械无法裁决该不该继续/收尾
        if (chain_required and chain_fulfilled < chain_required
                and check.coverage >= contract.minimum_coverage):
            return "need_human"

        # 6. 有实质进展但未达验收 → 继续
        return "continue"


# ---------------------------------------------------------------------------
# LLMVerifier —— 低频唤醒（调用方控制频率），看证据输出 Verdict
# ---------------------------------------------------------------------------

@dataclass
class LLMVerifier:
    """低频 LLM Verifier：输入事件日志尾部 + blackboard/profile 摘要，输出 Verdict。

    llm：注入的模型回调 Callable[[str], str | Awaitable[str]]（str = prompt）；
        为空 → 回退机械初判（fail-safe，保证双实现可切换且无 key 也能跑）。
    mechanical：内部机械初判器（可注入替身测试）。
    """

    name: str = "llm"
    llm: Callable[[str], str | Awaitable[str]] | None = None
    mechanical: MechanicalVerifier | None = None
    max_evidence_chars: int = 6000

    def __post_init__(self) -> None:
        self._mechanical = self.mechanical or MechanicalVerifier()

    async def verify(
        self,
        profile: TargetProfile,
        contract: AcceptanceContract,
        events: list[dict] | None = None,
    ) -> Verdict:
        mechanical_v = await self._mechanical.verify(profile, contract)
        if self.llm is None:
            return mechanical_v

        evidence = format_evidence(events, profile, max_chars=self.max_evidence_chars)
        check = contract.evaluate(profile)
        contract_summary = json.dumps(
            {
                "passed": check.passed,
                "coverage": check.coverage,
                "dead_end_ratio": check.dead_end_ratio,
                "fulfilled": check.fulfilled,
                "required": check.required,
            },
            ensure_ascii=False,
        )
        prompt = (
            "你是 SECAI 渗透验收判定器。仅基于以下证据与机械初判，输出一个 Verdict 记号：\n"
            "verified_done / continue / redirect / corrective / need_human\n\n"
            f"机械初判: {mechanical_v}\n"
            f"契约检查: {contract_summary}\n"
            f"{evidence}\n\n"
            "只输出一个 Verdict 记号，不要解释。"
        )
        raw = self.llm(prompt)
        if isinstance(raw, Awaitable):  # type: ignore[arg-type, misc]
            raw = await raw
        return parse_verdict(str(raw))


# ---------------------------------------------------------------------------
# 工厂：双实现可切换（配置选择，默认 mechanical）
# ---------------------------------------------------------------------------

def build_verifier(kind: str = "mechanical", **kwargs: Any) -> Verifier:
    """按 kind 构建 Verifier：mechanical（默认，纯函数零 token）/ llm（低频，可注入 llm）。"""
    kind = (kind or "mechanical").lower()
    if kind == "llm":
        return LLMVerifier(llm=kwargs.get("llm"), mechanical=kwargs.get("mechanical"))
    if kind == "mechanical":
        return kwargs.get("mechanical") or MechanicalVerifier()
    raise ValueError(f"未知 Verifier 类型: {kind!r}（可用 mechanical/llm）")


__all__ = [
    "VERDICTS",
    "LLMVerifier",
    "MechanicalVerifier",
    "Verifier",
    "Verdict",
    "build_verifier",
    "format_evidence",
    "parse_verdict",
]
