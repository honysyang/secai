#!/usr/bin/env bash
# 构建 SECAI Docker 镜像（优化体积，目标 < 3G）。
#
# 用法：
#   ./build-docker.sh                     # 默认 secai:latest，精简工具集
#   ./build-docker.sh secai v1.0          # 指定镜像名和 tag
#   SEC_TOOLS_FULL=1 ./build-docker.sh    # 安装完整安全工具集（体积会增大）
set -euo pipefail

IMAGE_NAME="${1:-${IMAGE_NAME:-secai}}"
IMAGE_TAG="${2:-${IMAGE_TAG:-latest}}"
SEC_TOOLS_FULL="${SEC_TOOLS_FULL:-0}"
MAX_SIZE_GB=3

cd "$(dirname "$0")"

echo "== 构建镜像 ${IMAGE_NAME}:${IMAGE_TAG}（SEC_TOOLS_FULL=${SEC_TOOLS_FULL}）=="
# 上下文指向项目根目录，Dockerfile 位于 docker/Dockerfile
docker build --build-arg SEC_TOOLS_FULL="${SEC_TOOLS_FULL}" \
    -f Dockerfile \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" ..

# 构建后检查镜像大小（Size 为解压后镜像各层总占用）
SIZE_BYTES=$(docker image inspect "${IMAGE_NAME}:${IMAGE_TAG}" --format '{{.Size}}' 2>/dev/null || echo 0)
SIZE_GB=$(awk "BEGIN {printf \"%.2f\", ${SIZE_BYTES}/1024/1024/1024}")

echo ""
echo "镜像大小：${SIZE_GB} GB"

if awk "BEGIN {exit !(${SIZE_GB} > ${MAX_SIZE_GB})}"; then
    echo "⚠ 警告：镜像超过 ${MAX_SIZE_GB} GB！建议 SEC_TOOLS_FULL=0 重新构建精简版。"
else
    echo "✓ 镜像大小在 ${MAX_SIZE_GB} GB 以内。"
fi

cat <<EOF

运行示例：
  # 普通跑分（无 VPN）
  docker run --rm -it --env-file .env -v "\$(pwd)/data:/app/data" ${IMAGE_NAME}:${IMAGE_TAG}

  # 需要 VPN 内网（加 TUN 设备 + 网络权限）
  docker run --rm -it --env-file .env -v "\$(pwd)/data:/app/data" \\
      --cap-add NET_ADMIN --device /dev/net/tun ${IMAGE_NAME}:${IMAGE_TAG}

  # 启动 Web 监控前端（访问 http://localhost:8000）
  docker run --rm -it -p 8000:8000 --env-file .env -v "\$(pwd)/data:/app/data" \\
      ${IMAGE_NAME}:${IMAGE_TAG} python3 -m app.server
EOF
