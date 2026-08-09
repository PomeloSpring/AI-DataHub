#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# OpenMetadata 停止脚本
# 用法: ./stop.sh [--purge]   --purge 同时删除数据卷（不可恢复）
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
else
    DC="docker-compose"
fi

cd "$SCRIPT_DIR"

if [ "$1" = "--purge" ]; then
    echo -e "${RED}[WARN] 将删除 OpenMetadata 所有数据卷（元数据/索引全部丢失）${NC}"
    read -r -p "确认删除? [y/N] " confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        $DC down -v
        echo -e "${GREEN}[INFO] OpenMetadata 已停止并清除数据${NC}"
    else
        echo "已取消"
    fi
else
    $DC down
    echo -e "${GREEN}[INFO] OpenMetadata 已停止（数据卷保留）${NC}"
fi
