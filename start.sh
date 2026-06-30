#!/bin/bash
# ChatBI 一键启动脚本
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}[ChatBI] 启动中...${NC}"

# 检查 .env
ENV_FILE="$SCRIPT_DIR/backend/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}[错误] 未找到 .env 文件: $ENV_FILE${NC}"
    echo "请先创建 .env 并配置 DORIS_USER, DORIS_PASSWORD, LLM_API_KEY 等变量"
    exit 1
fi

# 解析参数
RUN_INIT=false
PROD_MODE=false
WORKERS=0  # 0 = auto detect
for arg in "$@"; do
    case "$arg" in
        --init|-i)
            RUN_INIT=true
            ;;
        --prod|-p)
            PROD_MODE=true
            ;;
        --workers=*)
            WORKERS="${arg#*=}"
            ;;
        --help|-h)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --init,-i       初始化数据（同步元数据、SQL 模板、业务术语、菜单）"
            echo "  --prod,-p       生产模式（多 Worker，无热重载）"
            echo "  --workers=N     指定 Worker 数量（默认自动检测 CPU 核心数）"
            echo "  --help,-h       显示帮助信息"
            echo ""
            echo "示例:"
            echo "  $0                # 开发模式（单 Worker，热重载）"
            echo "  $0 --prod         # 生产模式（多 Worker）"
            echo "  $0 --prod --workers=4  # 生产模式，4 个 Worker"
            exit 0
            ;;
        *)
            echo -e "${RED}[错误] 未知参数: $arg${NC}"
            echo "使用 --help 查看帮助"
            exit 1
            ;;
    esac
done

# 初始化数据
if [ "$RUN_INIT" = true ]; then
    echo -e "${GREEN}[初始化] 同步元数据...${NC}"
    python -m sync.metadata_sync
    echo -e "${GREEN}[初始化] 同步 SQL 模板...${NC}"
    python -m sync.seed_templates
    echo -e "${GREEN}[初始化] 同步业务术语...${NC}"
    python -m sync.seed_terms
    echo -e "${GREEN}[初始化] 同步菜单...${NC}"
    python sync/seed_menus.py
    echo -e "${GREEN}[初始化完成]${NC}"
fi

# 检查并安装后端依赖
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}[提示] 安装后端 Python 依赖...${NC}"
    pip install -r backend/requirements.txt
fi

# 检查并安装前端依赖
if [ ! -d "frontend/node_modules" ]; then
    echo -e "${YELLOW}[提示] 安装前端 Node.js 依赖...${NC}"
    cd frontend && npm install && cd "$SCRIPT_DIR"
fi

# ── 启动后端 ──────────────────────────────────────────────────────────

if [ "$PROD_MODE" = true ]; then
    # 生产模式：多 Worker
    if [ "$WORKERS" -eq 0 ] 2>/dev/null; then
        # 自动检测 CPU 核心数
        WORKERS=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
        # 限制最大 Worker 数为 12
        if [ "$WORKERS" -gt 12 ]; then
            WORKERS=12
        fi
    fi

    echo -e "${BLUE}[后端] 生产模式启动 (端口 8000, $WORKERS 个 Worker)...${NC}"
    cd backend && uvicorn main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --workers "$WORKERS" \
        --timeout-keep-alive 300 \
        --limit-concurrency 100 \
        --limit-max-requests 10000 \
        --access-log \
        &
    BACKEND_PID=$!
    cd "$SCRIPT_DIR"
else
    # 开发模式：单 Worker + 热重载
    echo -e "${GREEN}[后端] 开发模式启动 (端口 8000, 热重载)...${NC}"
    cd backend && uvicorn main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --reload \
        &
    BACKEND_PID=$!
    cd "$SCRIPT_DIR"
fi

# ── 启动前端 ──────────────────────────────────────────────────────────

if [ "$PROD_MODE" = true ]; then
    # 生产模式：构建前端静态文件
    echo -e "${BLUE}[前端] 构建生产版本...${NC}"
    cd frontend && npm run build && cd "$SCRIPT_DIR"
    echo -e "${BLUE}[前端] 生产版本已构建到 frontend/dist/${NC}"
    # 生产模式通常使用 Nginx 服务静态文件，这里不启动 Vite
else
    # 开发模式：Vite 开发服务器
    echo -e "${GREEN}[前端] 启动 Vite 开发服务器 (端口 3000)...${NC}"
    cd frontend && npm run dev -- --port 3000 &
    FRONTEND_PID=$!
    cd "$SCRIPT_DIR"
fi

sleep 2
echo ""
echo -e "${GREEN}[ChatBI] 服务已启动:${NC}"
if [ "$PROD_MODE" = true ]; then
    echo -e "  后端: ${GREEN}http://localhost:8000${NC} (${BLUE}生产模式, $WORKERS Worker${NC})"
    echo -e "  前端: ${YELLOW}请使用 Nginx 服务 frontend/dist/ 目录${NC}"
else
    echo -e "  前端: ${GREEN}http://localhost:3000${NC}"
    echo -e "  后端: ${GREEN}http://localhost:8000${NC} (${YELLOW}开发模式${NC})"
fi
echo ""

if [ "$PROD_MODE" = true ]; then
    echo -e "${YELLOW}按 Ctrl+C 停止后端服务${NC}"
    trap "kill $BACKEND_PID 2>/dev/null; exit 0" SIGINT SIGTERM
    wait $BACKEND_PID
else
    echo -e "${YELLOW}按 Ctrl+C 停止所有服务${NC}"
    trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" SIGINT SIGTERM
    wait -n $BACKEND_PID $FRONTEND_PID
    echo -e "${RED}[ChatBI] 有服务异常退出，正在清理...${NC}"
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
fi
exit 1
