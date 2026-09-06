#!/usr/bin/env bash
# SECAI-PT R5 质量门：ruff 静态检查 + 单测 + 快照回放（全程无网）。
#
# 运行：bash scripts/gate.sh
# 范围说明：
# - ruff 检查 R2-R5 受控架构路径（pentest/profiles/harness/sandbox/scripts +
#   gate 实际运行的测试路径 tests/unit、tests/replay）。仓库存在历史存量违规
#   （core/tools/arsenal 等 R0 基线即白名单哲学；bench_platform 已随 9_6 跑分面删除），
#   不在本门内；新增代码必须通过本门。tests/e2e（有 key 才跑）不在本门内。
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"

echo "[gate] ruff check（pentest profiles harness sandbox scripts tests/unit tests/replay）"
"$PY" -m ruff check pentest profiles harness sandbox scripts tests/unit tests/replay

echo "[gate] pytest tests/unit"
"$PY" -m pytest tests/unit

echo "[gate] pytest tests/replay"
"$PY" -m pytest tests/replay

echo "[gate] OK —— ruff + unit + replay 全部通过"
