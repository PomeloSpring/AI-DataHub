#!/bin/bash
# ChatBI 一键停止脚本
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}[ChatBI] 正在停止服务...${NC}"

STOPPED=0

# 停止后端 (端口 8000)
BACKEND_PID=$(lsof -ti:8000 2>/dev/null || true)
if [ -n "$BACKEND_PID" ]; then
    echo -e "${GREEN}[后端] 停止 FastAPI 服务 (PID: $BACKEND_PID)...${NC}"
    kill $BACKEND_PID 2>/dev/null || true
    STOPPED=$((STOPPED + 1))
else
    echo -e "${YELLOW}[后端] 未发现运行中的服务 (端口 8000)${NC}"
fi

# 停止前端 (端口 3000)
FRONTEND_PID=$(lsof -ti:3000 2>/dev/null || true)
if [ -n "$FRONTEND_PID" ]; then
    echo -e "${GREEN}[前端] 停止 Vite 开发服务器 (PID: $FRONTEND_PID)...${NC}"
    kill $FRONTEND_PID 2>/dev/null || true
    STOPPED=$((STOPPED + 1))
else
    echo -e "${YELLOW}[前端] 未发现运行中的服务 (端口 3000)${NC}"
fi

sleep 1

# 强制清理残留进程
BACKEND_PID=$(lsof -ti:8000 2>/dev/null || true)
if [ -n "$BACKEND_PID" ]; then
    echo -e "${RED}[后端] 进程未退出，强制终止...${NC}"
    kill -9 $BACKEND_PID 2>/dev/null || true
fi

FRONTEND_PID=$(lsof -ti:3000 2>/dev/null || true)
if [ -n "$FRONTEND_PID" ]; then
    echo -e "${RED}[前端] 进程未退出，强制终止...${NC}"
    kill -9 $FRONTEND_PID 2>/dev/null || true
fi

if [ $STOPPED -gt 0 ]; then
    echo -e "${GREEN}[ChatBI] 服务已停止${NC}"
else
    echo -e "${YELLOW}[ChatBI] 没有发现运行中的服务${NC}"
fi
