"""成本治理：爆破预算 / hint 预算 / 换脑 / 挂起 的配置、判定与闸门。

把散落在 config / scheduler / demo_tools / main 的成本治理逻辑收拢到本模块，
避免「治理规则」在多处漂移。

依赖 config 的 BASE_URL / API_KEY（换脑候选模型缺省回退主模型）。
"""
from __future__ import annotations

import json
import os
from typing import Dict

from agents import OpenAIChatCompletionsModel, RunContextWrapper
from openai import AsyncOpenAI

from adapters.config import BASE_URL, API_KEY

# ================= 爆破预算 =================
BRUTEFORCE_MAX_CALLS = int(os.getenv("BRUTEFORCE_MAX_CALLS", "20"))  # 每题爆破调用硬上限，0=关闭
_BRUTE_TOOLS = {"fuzz", "parallel_shell", "run_tool"}                # 一定是爆破/枚举的工具
_BRUTE_BINARIES = ("hydra", "ffuf", "dirsearch", "sqlmap", "john",   # shell 里的爆破二进制
                   "masscan", "nuclei", "gobuster", "wfuzz", "dirb")

# ================= hint 预算 =================
HINT_BUDGET_RATIO = float(os.getenv("HINT_BUDGET_RATIO", "0.35"))  # 卡题且 token 达挂起档该比例即拉 hint，0=关闭

# ================= 成本两档（换脑 switch / 挂起 suspend） =================
COST_LIMITS = {
    "easy":   {"switch_tokens": 500000,  "suspend_tokens": 1000000},
    "medium": {"switch_tokens": 1000000, "suspend_tokens": 2000000},
    "hard":   {"switch_tokens": 1500000, "suspend_tokens": 3500000},
}
SUSPEND_SECONDS = int(os.getenv("SUSPEND_SECONDS", "2700"))  # 墙上时钟挂起档，0=关闭

# ================= 换脑候选 =================
# JSON 列表，每项 {"model","base_url","api_key","role"}；缺省回退主模型
def _load_escalation_models() -> list:
    """解析 ESCALATION_MODELS 环境变量；JSON 格式错误时回退空列表并告警。

    避免配置格式错误导致模块导入即崩溃（程序无法启动）。
    """
    raw = os.getenv("ESCALATION_MODELS", "[]")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[warn] ESCALATION_MODELS 不是合法 JSON，已回退空列表：{e}")
        return []
    return data if isinstance(data, list) else []


ESCALATION_MODELS = _load_escalation_models()


def build_escalation_models() -> list:
    """从 ESCALATION_MODELS 构建候选模型实例（换脑用），过滤缺失 model 的项。"""
    out = []
    for item in ESCALATION_MODELS:
        if not isinstance(item, dict):
            continue
        name = item.get("model")
        if not name:
            continue
        base = item.get("base_url") or BASE_URL
        key = item.get("api_key") or API_KEY
        client = AsyncOpenAI(base_url=base, api_key=key or None)
        model = OpenAIChatCompletionsModel(model=name, openai_client=client)
        model.brain_role = item.get("role", "")  # 特长标签（供换脑记录）
        out.append(model)
    return out


def is_brute_call(name: str, args: str = "") -> bool:
    """判断一次工具调用是否属于爆破/枚举类（成本治理用）。"""
    if name in _BRUTE_TOOLS:
        return True
    if name == "shell":
        low = (args or "").lower()
        return any(b in low for b in _BRUTE_BINARIES)
    return False


def brute_gate(ctx: RunContextWrapper, name: str, args: str = "") -> str:
    """爆破预算闸门：超限返回拦截消息（工具应直接返回它），未超限返回空串。

    返回空串 = 放行。拦截时计数已 +1，且不会实际执行该次爆破。
    """
    if BRUTEFORCE_MAX_CALLS <= 0:
        return ""
    if not is_brute_call(name, args):
        return ""
    c = ctx.context
    c.bruteforce_calls += 1
    if c.bruteforce_calls <= BRUTEFORCE_MAX_CALLS:
        return ""
    return (f"[error] 爆破预算硬上限：本会话爆破/枚举类调用已达 {c.bruteforce_calls} 次"
            f"（上限 {BRUTEFORCE_MAX_CALLS}），本次调用未执行。禁止继续大规模爆破/枚举/"
            "字典攻击（含 shell 里的 hydra/ffuf/dirsearch/sqlmap 等）——转向已确认线索的"
            "定向验证，或换攻击面。")


def should_pull_hint_by_budget(total_tokens: int, failed_paths: int,
                               difficulty: str, hint_used: bool,
                               ratio: float,
                               suspend_tokens: Dict[str, int]) -> bool:
    """hint 预算规则：卡题（≥2 条独立失败路径）且 token 达挂起档该比例时返回 True。

    hint 扣分比继续空烧 token 便宜，不应等整档预算耗尽才拉提示。
    ratio<=0 或未识别难度（suspend_tokens 无对应档）时返回 False。
    """
    if hint_used or ratio <= 0 or failed_paths < 2:
        return False
    budget = suspend_tokens.get(str(difficulty).lower(), 0) or \
        suspend_tokens.get("medium", 0)
    if not budget:
        return False
    return total_tokens >= budget * ratio
