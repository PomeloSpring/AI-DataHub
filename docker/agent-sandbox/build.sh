#!/usr/bin/env bash
# 构建 python_runtime 运行时镜像（本地会话沙箱唯一使用的镜像）。
# 用法: bash docker/agent-sandbox/build.sh
# 依赖清单变更（docker/agent-sandbox/requirements.txt）后需重新执行本脚本。
set -euo pipefail

# 定位仓库根（本脚本位于 docker/agent-sandbox/）
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# 镜像名与代码默认值保持一致（services/aiplatform/services/sandbox_executor.py）
IMAGE="${ADH_AGENT_SANDBOX_IMAGE:-adh-python-runtime:1}"

echo "[python_runtime] 构建镜像 $IMAGE （上下文: $ROOT）"
docker build -f docker/agent-sandbox/Dockerfile -t "$IMAGE" .

# 兼容旧标签：已有部署若仍引用 adh-agent-sandbox:1 时可平滑切换
docker tag "$IMAGE" adh-agent-sandbox:1

echo "[python_runtime] 完成: $IMAGE (已同时标记 adh-agent-sandbox:1 以兼容旧引用)"
echo "[python_runtime] 校验预装包: docker run --rm --network=none $IMAGE python -I -c 'import pandas, numpy, openpyxl; print(pandas.__version__)'"
