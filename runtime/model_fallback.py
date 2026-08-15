"""模型灾备重试包装：Runner.run 的模型失败自动切换 + 冷却重试。

供外层 Agent（Manager/Planner/Reporter/Coach/Compactor/Subtask）统一复用。
与单题 Executor 内部的自有模型池灾备（_run_single_challenge 主循环内）区分：
Executor 在单题 while 循环内自行切换模型，外层 Agent 用本函数兜底。

独立成 runtime 层模块，避免 main.py 与 runtime/stuck.py 之间循环导入。
"""
from __future__ import annotations

import asyncio

from agents import Runner

from runtime.model_pool import (ModelExhaustedError, is_model_failure,
                                is_permanent_model_failure)
from runtime.log import log_warn, log_error


async def run_with_model_fallback(agent, input, *, hooks=None, context=None,
                                  session=None, max_turns=None, model_pool=None,
                                  agent_name="", max_rounds=6, retry_delay=5):
    """带模型灾备切换 + 冷却重试的 Runner.run 包装函数（供外层 Agent 使用）。

    - model_pool 为 None 时，仅执行一次 Runner.run，不重试；
    - model_pool 有效时，模型失败后区分永久/暂时失败：
      永久失败（鉴权/模型名错误）拉黑；暂时失败（限流/超时）冷却后重试；
    - 主/灾备之间可多轮切换，最多 max_rounds 轮；全部不可用抛 ModelExhaustedError。
    """
    if model_pool is None:
        return await Runner.run(agent, input=input, hooks=hooks, context=context,
                                session=session, max_turns=max_turns)

    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        try:
            return await Runner.run(agent, input=input, hooks=hooks, context=context,
                                      session=session, max_turns=max_turns)
        except Exception as exc:
            if not is_model_failure(exc):
                raise
            current_name = getattr(agent.model, "model", "?")
            permanent = is_permanent_model_failure(exc)
            model_pool.mark_failed(current_name, permanent=permanent)
            entry = model_pool.next(current_name=current_name,
                                    reason=f"model_failure:{type(exc).__name__}")
            if entry is not None:
                agent.model = entry.model
                log_warn(f"[model-fallback] {agent_name or '外层 Agent'} {current_name} 失败，"
                         f"切换到 {entry.name} 继续同一会话：{str(exc)[:300]}")
                continue
            # 无可用候选：永久失败立即耗尽；暂时失败等待冷却后重试
            if permanent or rounds >= max_rounds:
                log_error(f"[model-exhausted] {agent_name or '外层 Agent'} 所有模型均不可用：{exc}")
                raise ModelExhaustedError(
                    f"{agent_name or '外层 Agent'} 模型池耗尽") from exc
            log_warn(f"[model-retry] {agent_name or '外层 Agent'} 模型暂时不可用，"
                     f"{retry_delay}s 后重试（{rounds}/{max_rounds}）：{str(exc)[:200]}")
            await asyncio.sleep(retry_delay)
            continue
    log_error(f"[model-exhausted] {agent_name or '外层 Agent'} 达到最大重试轮数 {max_rounds}")
    raise ModelExhaustedError(f"{agent_name or '外层 Agent'} 模型池耗尽")
