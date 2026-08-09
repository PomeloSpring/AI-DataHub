#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# DataFusion Gateway 状态检查脚本
# 用法: ./gateway/status.sh
# ─────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.gateway.pid"
PORT=50051

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "═══════════════════════════════════════════════════"
echo "  DataFusion Gateway 状态"
echo "═══════════════════════════════════════════════════"
echo ""

# 1. 检查本地进程
echo -n "本地进程: "
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "${GREEN}运行中${NC} (PID: $PID)"
    else
        echo -e "${RED}已停止${NC} (PID 文件存在但进程已死)"
    fi
else
    # 尝试通过端口查找
    PID=$(lsof -ti:$PORT 2>/dev/null || true)
    if [ -n "$PID" ]; then
        echo -e "${YELLOW}运行中${NC} (PID: $PID, 无 PID 文件)"
    else
        echo -e "${YELLOW}未运行${NC}"
    fi
fi

# 2. 检查端口
echo -n "端口 $PORT: "
if lsof -i:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
    echo -e "${GREEN}监听中${NC}"
else
    echo -e "${YELLOW}未监听${NC}"
fi

# 3. 健康检查
echo -n "健康检查: "
HEALTH=$(curl -s --connect-timeout 3 http://localhost:$PORT/api/health 2>/dev/null)
if [ $? -eq 0 ] && [ -n "$HEALTH" ]; then
    echo -e "${GREEN}正常${NC}"
    echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
else
    echo -e "${RED}不可达${NC}"
fi

# 4. Docker 容器状态
echo ""
echo -n "Docker 容器: "
CONTAINER=$(docker ps --filter "name=chatbi-gateway" --format "{{.Status}}" 2>/dev/null)
if [ -n "$CONTAINER" ]; then
    echo -e "${GREEN}$CONTAINER${NC}"
else
    # 检查是否已停止
    CONTAINER=$(docker ps -a --filter "name=chatbi-gateway" --format "{{.Status}}" 2>/dev/null)
    if [ -n "$CONTAINER" ]; then
        echo -e "${YELLOW}$CONTAINER${NC}"
    else
        echo -e "${YELLOW}不存在${NC}"
    fi
fi

echo ""
echo "═══════════════════════════════════════════════════"
