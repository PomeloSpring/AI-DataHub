#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# DataFusion Gateway 停止脚本
# 用法: ./gateway/stop.sh [--docker]
# ─────────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$SCRIPT_DIR/.gateway.pid"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

MODE="${1:---local}"

case "$MODE" in
    --local)
        # 停止本地进程
        if [ ! -f "$PID_FILE" ]; then
            warn "未找到 PID 文件，Gateway 可能未在运行"
            # 尝试通过端口查找
            PID=$(lsof -ti:50051 2>/dev/null || true)
            if [ -n "$PID" ]; then
                info "发现端口 50051 上的进程 (PID: $PID)"
                kill "$PID" 2>/dev/null && info "已停止 Gateway (PID: $PID)" || error "停止失败"
            else
                info "端口 50051 无进程"
            fi
            exit 0
        fi

        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            info "停止 Gateway (PID: $PID)..."
            kill "$PID"

            # 等待进程退出（最多 10 秒）
            for i in $(seq 1 10); do
                if ! kill -0 "$PID" 2>/dev/null; then
                    break
                fi
                sleep 1
            done

            # 如果还没退出，强制杀死
            if kill -0 "$PID" 2>/dev/null; then
                warn "进程未响应，强制终止..."
                kill -9 "$PID" 2>/dev/null
            fi

            rm -f "$PID_FILE"
            info "Gateway 已停止"
        else
            warn "进程 $PID 已不存在"
            rm -f "$PID_FILE"
        fi
        ;;

    --docker)
        # 停止 Docker 容器
        info "停止 Gateway 容器"
        cd "$PROJECT_ROOT"
        docker compose -f docker-compose.full.yml stop gateway
        info "Gateway 容器已停止"
        ;;

    --docker-remove)
        # 停止并删除 Docker 容器
        info "停止并删除 Gateway 容器"
        cd "$PROJECT_ROOT"
        docker compose -f docker-compose.full.yml rm -f gateway
        info "Gateway 容器已删除"
        ;;

    *)
        echo "用法: $0 [--local|--docker|--docker-remove]"
        echo ""
        echo "  --local          停止本地进程"
        echo "  --docker         停止 Docker 容器"
        echo "  --docker-remove  停止并删除 Docker 容器"
        exit 1
        ;;
esac
