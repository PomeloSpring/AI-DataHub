#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# AI-DataHub 数据中台 — 全量停止脚本（非容器化）
# 用法: ./stop-all.sh [服务名]
#   ./stop-all.sh          # 停止所有服务
#   ./stop-all.sh authservice   # 只停止指定服务
# ═══════════════════════════════════════════════════════════════

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$PROJECT_ROOT/pids"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }

SERVICES=(
    "web"          # web 入口（单进程承载全部契约端口，Phase 4；dataengine 已退役）
    "celery-beat"    # 先停调度派发，再停 worker
    "celery-worker"
    "frontend"
)

stop_service() {
    local name="$1"
    local pid_file="$PID_DIR/${name}.pid"
    local pid=""

    # 尝试从 PID 文件获取
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if ! kill -0 "$pid" 2>/dev/null; then
            pid=""
            rm -f "$pid_file"
        fi
    fi

    # PID 文件不存在时，尝试通过端口查找（含脚本外手工拉起的进程，停完下次由脚本重新纳管）
    if [ -z "$pid" ]; then
        local port=""
        case "$name" in
            celery-worker|celery-beat)
                # celery 无端口，按 cmdline 兜底查找；取最小 PID 为 worker 主进程
                # （TERM 主进程会带走池子进程，下方 pkill -P 再兜底清子进程）
                pid=$(ps -eo pid,cmd | awk -v app="backend.modules.flow.tasks.celery_app" \
                    -v kw=" ${name#celery-}" \
                    '$2 != "awk" && index($0, "-m celery -A " app) > 0 && index($0, kw) > 0 {print $1}' \
                    | sort -n | head -1)
                ;;
            frontend)   port=3000 ;;
            backend)    port=8000 ;;
            web)        port=$(grep -vE '^\s*(#|$)' "$PROJECT_ROOT/backend/scripts/services.conf" 2>/dev/null | head -1 | cut -d: -f3) ;;
            *)
                port=$(grep -E "^${name}:" "$PROJECT_ROOT/backend/scripts/services.conf" 2>/dev/null | head -1 | cut -d: -f3)
                ;;
        esac
        if [ -n "$port" ]; then
            pid=$(lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
        fi
    fi

    if [ -z "$pid" ]; then
        log_warn "${name} 未在运行"
        return 0
    fi

    echo -e "  停止 ${name} (PID: $pid)..."
    kill "$pid" 2>/dev/null

    # 等待退出（最多10秒）
    for i in {1..10}; do
        if ! kill -0 "$pid" 2>/dev/null; then break; fi
        sleep 1
    done

    # 杀掉子进程
    pkill -P "$pid" 2>/dev/null

    # 强制终止
    if kill -0 "$pid" 2>/dev/null; then
        echo -e "  ${YELLOW}强制终止 ${name}...${NC}"
        kill -9 "$pid" 2>/dev/null
    fi

    rm -f "$pid_file"
    echo -e "  ${GREEN}${name} 已停止${NC}"
}

# ═══════════════════════════════════════════════════════════════
# 主逻辑
# ═══════════════════════════════════════════════════════════════

case "${1:-all}" in
    all)
        echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
        echo -e "${BLUE}  AI-DataHub 数据中台 — 停止所有服务${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
        echo ""

        for name in "${SERVICES[@]}"; do
            stop_service "$name"
        done

        # 兜底：开发单端口模式（start-service.sh / start-all.sh <name>）遗留的分服务进程
        if [ -f "$PROJECT_ROOT/backend/scripts/services.conf" ]; then
            while IFS=':' read -r name _module _port; do
                [ -f "$PID_DIR/${name}.pid" ] && stop_service "$name"
            done < <(grep -vE '^\s*(#|$)' "$PROJECT_ROOT/backend/scripts/services.conf")
        fi

        echo ""
        log_info "所有服务已停止"
        ;;
    *)
        # celery 别名 → 一次停 worker + beat
        targets=("$1")
        [ "$1" = "celery" ] && targets=("celery-beat" "celery-worker")
        # 可停对象：常驻进程 + services.conf 分服务名（开发单端口模式）
        known_names=("${SERVICES[@]}")
        if [ -f "$PROJECT_ROOT/backend/scripts/services.conf" ]; then
            while IFS=':' read -r name _module _port; do
                known_names+=("$name")
            done < <(grep -vE '^\s*(#|$)' "$PROJECT_ROOT/backend/scripts/services.conf")
        fi
        found=false
        for name in "${targets[@]}"; do
            for known in "${known_names[@]}"; do
                if [ "$name" = "$known" ]; then
                    stop_service "$name"
                    found=true
                    break
                fi
            done
        done
        if [ "$found" = false ]; then
            echo -e "${RED}未知服务: $1${NC}"
            echo "可用服务: ${known_names[*]} celery"
            exit 1
        fi
        ;;
esac
