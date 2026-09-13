#!/usr/bin/env bash
# 一键启动 SECAI-PT 后端（v4 §6：8700 端口 = 后端 + 静态前端）
#
# 行为：
# - 默认走真实路径：必须有 LLM_API_KEY/OPENAI_API_KEY，否则 8700 启动但 run 500
# - 设 SECAI_FIXTURE=1 走离线演示三会话（fixture.steer_reply 应答，不消耗 LLM）
# - 设 SECAI_AUTO_APPROVE=1 走单人/单机自用：审批落地即预设 allow，不再阻塞 run
# - 强制使用 .venv 里的 python（openai-agents / uvicorn 等依赖装在 venv）
set -euo pipefail

cd "$(dirname "$0")"

# venv 自检（kali 默认装的 openai-agents 在 .venv；系统 python 没有）
if [[ ! -x ".venv/bin/python" ]]; then
  echo "❌ .venv 不存在或不可执行；先：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

PY=".venv/bin/python"
echo "→ python: $($PY -V)"
echo "→ SECAI_FIXTURE=${SECAI_FIXTURE:-0}"
echo "→ SECAI_AUTO_APPROVE=${SECAI_AUTO_APPROVE:-0}"
echo "→ LLM_API_KEY=$([[ -n "${LLM_API_KEY:-}${OPENAI_API_KEY:-}" ]] && echo set || echo empty)"

exec "$PY" -m server.main --port 8700