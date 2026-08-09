#!/bin/bash
# AI-DataHub 启动脚本（已迁移到微服务架构）
# 请使用 start-all.sh 代替

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "提示: 项目已迁移到微服务架构，请使用 start-all.sh 启动所有服务"
echo ""
echo "  ./start-all.sh          # 启动所有服务"
echo "  ./start-all.sh status   # 查看服务状态"
echo ""

exec "$SCRIPT_DIR/start-all.sh" "$@"
