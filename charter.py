"""使命宪章：管理者产物的落盘与加载。

管理者每次立法生成一份宪章，落盘到 data/mission_charter.md 供审计与接力复用，
并在构建执行者时作为系统提示的一部分注入。
"""
from __future__ import annotations

from pathlib import Path


def save_charter(path: Path, charter: str) -> Path:
    """把使命宪章落盘（自动创建父目录）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(charter, encoding="utf-8")
    return path


def load_charter(path: Path) -> str:
    """读取已落盘的使命宪章；不存在时返回空串。"""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""
