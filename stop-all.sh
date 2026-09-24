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
    "dataengine"
    "authservice"
    "datacatalog"
    "datagov"
    "dataviz"
    "datamind"
    "celery-beat"    # 先停调度派发，再停 worker，最后停 dataflow
    "celery-worker"
    "dataflow"
    "aiplatform"
    "graphservice"
    "semanticservice"
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
            frontend)   port=3000 ;;
            backend)    port=8000 ;;
            dataengine) port=8082 ;;
            *)
                port=$(grep -E "^${name}:" "$PROJECT_ROOT/services/shared/scripts/services.conf" 2>/dev/null | head -1 | cut -d: -f3)
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

        echo ""
        log_info "所有服务已停止"
        ;;
    *)
        # celery 别名 → 一次停 worker + beat
        targets=("$1")
        [ "$1" = "celery" ] && targets=("celery-beat" "celery-worker")
        found=false
        for name in "${targets[@]}"; do
            for known in "${SERVICES[@]}"; do
                if [ "$name" = "$known" ]; then
                    stop_service "$name"
                    found=true
                    break
                fi
            done
        done
        if [ "$found" = false ]; then
            echo -e "${RED}未知服务: $1${NC}"
            echo "可用服务: ${SERVICES[*]} celery"
            exit 1
        fi
        ;;
esac
