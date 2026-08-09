#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 通用服务启动脚本
# 用法: ./start.sh {start|stop|restart|status}
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="$(basename "$SCRIPT_DIR")"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
PID_DIR="$PROJECT_ROOT/pids"
PYTHON="$PROJECT_ROOT/venv/bin/python"

mkdir -p "$LOG_DIR" "$PID_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 加载环境变量
if [ -f "$PROJECT_ROOT/services/.env" ]; then
    set -a; source "$PROJECT_ROOT/services/.env"; set +a
fi

# 获取服务配置: module:port
get_service_config() {
    case "$SERVICE_NAME" in
        authservice)   echo "services.authservice.main:8006" ;;
        datacatalog)   echo "services.datacatalog.main:8005" ;;
        datagov)       echo "services.datagov.main:8002" ;;
        dataviz)       echo "services.dataviz.main:8004" ;;
        datamind)      echo "services.datamind.main:8001" ;;
        dataflow)      echo "services.dataflow.main:8003" ;;
        aiplatform)    echo "services.aiplatform.main:8007" ;;
        vectorservice) echo "services.shared.vectorservice.main:8010" ;;
        graphservice)  echo "services.shared.graphservice.main:8011" ;;
        *) log_error "Unknown service: $SERVICE_NAME"; exit 1 ;;
    esac
}

start() {
    local config=$(get_service_config)
    local module=$(echo "$config" | cut -d: -f1)
    local port=$(echo "$config" | cut -d: -f2)
    local pid_file="$PID_DIR/${SERVICE_NAME}.pid"
    local log_file="$LOG_DIR/${SERVICE_NAME}.log"

    if [ -f "$pid_file" ]; then
        local old_pid=$(cat "$pid_file")
        if kill -0 "$old_pid" 2>/dev/null; then
            log_warn "$SERVICE_NAME 已在运行 (PID: $old_pid, 端口: $port)"
            return 0
        else
            rm -f "$pid_file"
        fi
    fi

    if lsof -i :"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        local occupied_pid=$(lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
        log_error "$SERVICE_NAME 端口 $port 已被进程 $occupied_pid 占用"
        return 1
    fi

    log_info "启动 $SERVICE_NAME (端口: $port)..."

    cd "$PROJECT_ROOT"
    PYTHONPATH="$PROJECT_ROOT" nohup "$PYTHON" -m uvicorn "${module}:app" \
        --host 0.0.0.0 --port "$port" --log-level info \
        > >(tee -a "$log_file") 2>&1 &

    local pid=$!
    echo "$pid" > "$pid_file"

    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        log_info "$SERVICE_NAME 启动成功 (PID: $pid, 端口: $port)"
        return 0
    else
        log_error "$SERVICE_NAME 启动失败，查看日志: $log_file"
        rm -f "$pid_file"
        return 1
    fi
}

stop() {
    local pid_file="$PID_DIR/${SERVICE_NAME}.pid"

    if [ ! -f "$pid_file" ]; then
        log_warn "$SERVICE_NAME 未在运行"
        local config=$(get_service_config)
        local port=$(echo "$config" | cut -d: -f2)
        local pid=$(lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
        if [ -n "$pid" ]; then
            log_info "通过端口发现进程 (PID: $pid)"
            kill "$pid" 2>/dev/null; sleep 2
            kill -9 "$pid" 2>/dev/null
            log_info "$SERVICE_NAME 已停止"
        fi
        return 0
    fi

    local pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
        log_info "停止 $SERVICE_NAME (PID: $pid)..."
        kill "$pid" 2>/dev/null
        for i in {1..10}; do
            if ! kill -0 "$pid" 2>/dev/null; then break; fi
            sleep 1
        done
        kill -9 "$pid" 2>/dev/null
        rm -f "$pid_file"
        log_info "$SERVICE_NAME 已停止"
    else
        rm -f "$pid_file"
        log_warn "$SERVICE_NAME 进程已不存在"
    fi
}

status() {
    local config=$(get_service_config)
    local port=$(echo "$config" | cut -d: -f2)
    local pid_file="$PID_DIR/${SERVICE_NAME}.pid"
    local status="未运行"
    local pid="-"

    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            status="运行中"
        else
            status="已停止"; pid="-"
        fi
    fi

    echo "═══════════════════════════════"
    echo "  服务名: $SERVICE_NAME"
    echo "  端口:   $port"
    echo "  PID:    $pid"
    echo "  状态:   $status"
    echo "  日志:   $LOG_DIR/${SERVICE_NAME}.log"
    echo "═══════════════════════════════"
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    *) echo "用法: $0 {start|stop|restart|status}"; exit 1 ;;
esac
