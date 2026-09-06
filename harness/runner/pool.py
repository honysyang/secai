"""进程级全局模型池句柄。

外层 Agent（Strategist/Reporter）共享同一灾备池，与单题 ExecutorLoop 内部的
ModelPool 隔离（run_single_challenge 自建/外部注入）。以 set/get 函数读写，
避免 harness 各模块互相 import 造成循环依赖。
"""
from __future__ import annotations

from runtime.model_pool import ModelPool

_global_model_pool: ModelPool | None = None


def set_global_model_pool(pool: ModelPool | None) -> None:
    """设置进程级全局模型池（任务入口初始化时调用一次）。"""
    global _global_model_pool
    _global_model_pool = pool


def get_global_model_pool() -> ModelPool | None:
    """取进程级全局模型池；未初始化时返回 None（调用方自行兜底）。"""
    return _global_model_pool
