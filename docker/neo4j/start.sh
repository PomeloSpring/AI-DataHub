#!/bin/bash

# Neo4j 启动脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=================================="
echo "  AI-DataHub Neo4j 启动脚本"
echo "=================================="
echo ""

# 检查Docker是否安装
if ! command -v docker &> /dev/null; then
    echo "❌ 错误: Docker 未安装"
    echo "请先安装 Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

# 检查Docker Compose是否安装
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "❌ 错误: Docker Compose 未安装"
    echo "请先安装 Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
fi

# 进入脚本目录
cd "$SCRIPT_DIR"

# 启动Neo4j
echo "🚀 正在启动 Neo4j..."
echo ""

if docker compose version &> /dev/null; then
    docker compose up -d
else
    docker-compose up -d
fi

echo ""
echo "✅ Neo4j 启动成功！"
echo ""
echo "📊 访问信息:"
echo "   - 浏览器: http://localhost:7474"
echo "   - Bolt协议: bolt://localhost:7687"
echo "   - 用户名: neo4j"
echo "   - 密码: ai-datahub-2024"
echo ""
echo "📝 常用命令:"
echo "   - 查看日志: docker logs -f ai-datahub-neo4j"
echo "   - 停止服务: cd $SCRIPT_DIR && docker-compose down"
echo "   - 重启服务: cd $SCRIPT_DIR && docker-compose restart"
echo ""
