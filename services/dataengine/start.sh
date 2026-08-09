#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# DataFusion Gateway 启动脚本
# 用法: ./gateway/start.sh [--dev|--docker|--background]
# ─────────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$SCRIPT_DIR/.gateway.pid"
LOG_FILE="$SCRIPT_DIR/gateway.log"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查是否已运行
check_running() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        # PID 文件存在但进程已死，清理
        rm -f "$PID_FILE"
    fi
    return 1
}

# ── 启动模式 ──────────────────────────────────────────────────────

MODE="${1:---dev}"

case "$MODE" in
    --dev)
        # 开发模式：前台运行，带日志输出
        info "启动 Gateway（开发模式，前台运行）"
        echo -e "${CYAN}按 Ctrl+C 停止${NC}"
        cd "$SCRIPT_DIR"
        RUST_LOG=debug cargo run
        ;;

    --background)
        # 后台模式：编译后后台运行
        if check_running; then
            warn "Gateway 已在运行 (PID: $(cat "$PID_FILE"))"
            exit 0
        fi

        info "编译 Gateway..."
        cd "$SCRIPT_DIR"
        cargo build --release 2>&1 | tail -5

        if [ ! -f "$SCRIPT_DIR/target/release/datafusion-gateway" ]; then
            error "编译失败"
            exit 1
        fi

        info "启动 Gateway（后台模式）"
        RUST_LOG=info nohup "$SCRIPT_DIR/target/release/datafusion-gateway" \
            > "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"

        # 等待启动
        sleep 2
        if check_running; then
            info "Gateway 已启动 (PID: $(cat "$PID_FILE"))"
            info "日志: tail -f $LOG_FILE"
            info "端口: 50051"
        else
            error "Gateway 启动失败，查看日志: $LOG_FILE"
            exit 1
        fi
        ;;

    --docker)
        # Docker 模式
        info "通过 Docker Compose 启动 Gateway"
        cd "$PROJECT_ROOT"
        docker compose -f docker-compose.full.yml up -d gateway
        info "Gateway 容器已启动"
        docker compose -f docker-compose.full.yml ps gateway
        ;;

    --docker-build)
        # Docker 模式（重新构建）
        info "重新构建并启动 Gateway"
        cd "$PROJECT_ROOT"
        docker compose -f docker-compose.full.yml up -d --build gateway
        info "Gateway 容器已构建并启动"
        ;;

    *)
        echo "用法: $0 [--dev|--background|--docker|--docker-build]"
        echo ""
        echo "  --dev           开发模式（前台运行，实时日志）"
        echo "  --background    后台模式（编译后后台运行）"
        echo "  --docker        Docker 模式（使用预构建镜像）"
        echo "  --docker-build  Docker 模式（重新构建镜像）"
        exit 1
        ;;
esac
