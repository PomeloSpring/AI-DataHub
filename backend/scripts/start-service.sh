#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 通用服务启动脚本 —— 唯一实现（各服务目录下的 start.sh 是薄壳）
# 用法: ./start.sh {start|stop|restart|status}
#
# 薄壳通过环境变量传入:
#   SERVICE_NAME  服务名（必填）
#   SERVICE_DIR   服务目录（默认 backend/modules/$SERVICE_NAME）
# 服务模块与端口唯一登记处: backend/scripts/services.conf
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"          # backend/scripts
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"      # 仓库根
SERVICE_NAME="${SERVICE_NAME:?缺少 SERVICE_NAME（请通过 backend/modules/<name>/start.sh 调用）}"
SERVICE_DIR="${SERVICE_DIR:-$PROJECT_ROOT/backend/modules/$SERVICE_NAME}"
LOG_DIR="$PROJECT_ROOT/logs"
PID_DIR="$PROJECT_ROOT/pids"
PYTHON="$PROJECT_ROOT/venv/bin/python"
SERVICES_CONF="$SCRIPT_DIR/services.conf"

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

# 获取服务配置行: name:module:port（查注册表，不再内置 case 清单）
#
# Phase 4 起全部模块 router 合入 web 单进程，注册表只登记 web:backend.app.main:8000。
# 历史服务名（datamind/datacatalog/…）查不到时**显式**解析为注册表唯一行并播报口径：
# 同一进程同一端口，同时只能有一个在跑（第二个由端口占用检查报错，不静默换端口）。
# 返回完整行供调用方解析 name/module/port（服务名归一不在子 shell 里做，否则丢改动）。
get_service_line() {
    local line
    line=$(grep -E "^${SERVICE_NAME}:" "$SERVICES_CONF" 2>/dev/null | head -1)
    if [ -z "$line" ]; then
        local rows
        rows=$(grep -vE '^\s*(#|$)' "$SERVICES_CONF" 2>/dev/null)
        if [ "$(printf '%s\n' "$rows" | wc -l)" -eq 1 ]; then
            log_info "${SERVICE_NAME} 已并入 web 单进程，按注册表唯一行启动：${rows}"
            line="$rows"
        else
            log_error "Unknown service: $SERVICE_NAME (注册表: $SERVICES_CONF)"
            exit 1
        fi
    fi
    echo "$line"
}

start() {
    local line=$(get_service_line)
    SERVICE_NAME=$(echo "$line" | cut -d: -f1)   # 历史别名归一为注册表真名（pid/日志同名）
    local module=$(echo "$line" | cut -d: -f2)
    local port=$(echo "$line" | cut -d: -f3)
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
    # 日志只写文件、不占调用方管道（避免 `./start.sh | tee` 等场景挂住不返回）
    PYTHONPATH="$PROJECT_ROOT" nohup "$PYTHON" -m uvicorn "${module}:app" \
        --host 0.0.0.0 --port "$port" --log-level info \
        >> "$log_file" 2>&1 < /dev/null &

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
    local line=$(get_service_line)
    SERVICE_NAME=$(echo "$line" | cut -d: -f1)   # 同上：历史别名归一，不然 pid 文件对不上
    local port=$(echo "$line" | cut -d: -f3)
    local pid_file="$PID_DIR/${SERVICE_NAME}.pid"

    if [ ! -f "$pid_file" ]; then
        log_warn "$SERVICE_NAME 未在运行"
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
    local line=$(get_service_line)
    SERVICE_NAME=$(echo "$line" | cut -d: -f1)
    local port=$(echo "$line" | cut -d: -f3)
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
