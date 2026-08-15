"""统一日志：终端 + 文件双写，带时间戳与级别。

目的：让运行过程可追溯、可调试。终端打印与 data/logs/secai-YYYYMMDD.log 同步落盘，
多次运行按时间线连续追加，方便事后查阅与对比。

用法：
    from runtime.log import log, log_info, log_warn, log_error

    log_info("单题 a-01 启动")
    log_warn("模型切换 a -> b")
    log_error("单题异常：name 'json' is not defined")
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

_LOGGER_NAME = "secai"
_FMT = "%(asctime)s [%(levelname)s] %(message)s"
_CONSOLE_DATE_FMT = "%H:%M:%S"        # 终端：只看时间点，简洁
_FILE_DATE_FMT = "%Y-%m-%d %H:%M:%S"  # 文件：完整日期，跨天可追溯

# 允许的级别名（兼容常见简写 WARN）
_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

# ANSI 颜色：不同级别用不同颜色（终端专用；文件保持纯文本便于 grep）
_LEVEL_COLORS = {
    logging.DEBUG: "\033[90m",    # 灰
    logging.INFO: "\033[32m",     # 绿
    logging.WARNING: "\033[33m",  # 黄
    logging.ERROR: "\033[31m",    # 红
}
_RESET = "\033[0m"


class _ColorFormatter(logging.Formatter):
    """终端专用 Formatter：整行按级别着色，让 INFO/WARN/ERROR 一眼区分。"""

    def format(self, record):
        msg = super().format(record)
        color = _LEVEL_COLORS.get(record.levelno)
        return f"{color}{msg}{_RESET}" if color else msg


_initialized = False


def _init() -> None:
    """惰性初始化 logger（终端 handler + 按天文件 handler），只执行一次。"""
    global _initialized
    if _initialized:
        return

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # WARNING 统一显示为 WARN（更紧凑，符合观察习惯）
    logging.addLevelName(logging.WARNING, "WARN")

    # 终端 handler：默认 INFO 及以上（调试细节只落盘，避免刷屏），时间戳只到秒，按级别着色
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_ColorFormatter(_FMT, _CONSOLE_DATE_FMT))
    logger.addHandler(console)

    # 文件 handler：按天滚动，跨多次运行连续追加（含 DEBUG 全量），完整日期时间
    log_dir = Path(__file__).resolve().parent.parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        log_dir / f"secai-{time.strftime('%Y%m%d')}.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FMT, _FILE_DATE_FMT))
    logger.addHandler(file_handler)

    _initialized = True


def log(msg: str, level: str = "INFO") -> None:
    """写一条日志（终端 + 文件）。level 支持 DEBUG/INFO/WARN/ERROR。"""
    _init()
    lvl = _LEVELS.get(str(level).upper(), logging.INFO)
    logging.getLogger(_LOGGER_NAME).log(lvl, msg)


def log_debug(msg: str) -> None:
    log(msg, "DEBUG")


def log_info(msg: str) -> None:
    log(msg, "INFO")


def log_warn(msg: str) -> None:
    log(msg, "WARN")


def log_error(msg: str) -> None:
    log(msg, "ERROR")
