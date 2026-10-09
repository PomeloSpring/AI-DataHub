#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# AI-DataHub 数据中台 — 全量启动脚本（非容器化）
# 用法: ./start-all.sh [选项] [服务名]
#   ./start-all.sh              # 前台启动所有服务（日志合并输出到控制台）
#   ./start-all.sh -d           # 后台守护模式（日志写入 logs/ 目录）
#   ./start-all.sh authservice  # 前台启动指定服务
#   ./start-all.sh celery       # 只启动 Celery worker + beat
#   ./start-all.sh status       # 查看服务状态
# ═══════════════════════════════════════════════════════════════

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
PID_DIR="$PROJECT_ROOT/pids"
PYTHONPATH="$PROJECT_ROOT"
PYTHON="$PROJECT_ROOT/venv/bin/python"

# 创建日志和PID目录
mkdir -p "$LOG_DIR" "$PID_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 前台模式：每个服务的彩色标签（用于区分不同服务的日志）
FG_COLORS=('\033[0;36m' '\033[0;35m' '\033[0;33m' '\033[0;32m' '\033[0;34m' '\033[0;31m' '\033[1;36m' '\033[1;35m' '\033[1;33m' '\033[1;32m')
FG_PIDS=()        # 前台模式追踪的所有进程 PID
FG_MODE=false     # 是否前台模式运行

# ═══════════════════════════════════════════════════════════════
# 服务定义: name:module:port —— 唯一权威清单在 backend/scripts/services.conf
# ═══════════════════════════════════════════════════════════════
SERVICES_CONF="$PROJECT_ROOT/backend/scripts/services.conf"
if [ ! -f "$SERVICES_CONF" ]; then
    log_error "缺少服务注册表: $SERVICES_CONF"
    exit 1
fi
mapfile -t SERVICES < <(grep -vE '^\s*(#|$)' "$SERVICES_CONF")

# DataEngine 服务（Rust 二进制）
DATAENGINE_BIN="$PROJECT_ROOT/services/dataengine/target/release/datafusion-gateway"
DATAENGINE_PORT=8082
DATAENGINE_LOG="$LOG_DIR/dataengine.log"
DATAENGINE_PID="$PID_DIR/dataengine.pid"

# Celery 定时任务进程（无端口，经 Redis broker 通信；beat 有 Redis 租约防多实例）
CELERY_APP="backend.modules.flow.tasks.celery_app"
CELERY_PROCS=("celery-worker" "celery-beat")

# 前端服务（特殊处理）
FRONTEND_DIR="$PROJECT_ROOT/frontend"
FRONTEND_PORT=3000

# ═══════════════════════════════════════════════════════════════
# 函数: 启动单个服务
# ═══════════════════════════════════════════════════════════════
start_service() {
    local name="$1"
    local module="$2"
    local port="$3"
    local pid_file="$PID_DIR/${name}.pid"
    local log_file="$LOG_DIR/${name}.log"

    # 检查是否已运行
    if [ -f "$pid_file" ]; then
        local old_pid=$(cat "$pid_file")
        if kill -0 "$old_pid" 2>/dev/null; then
            log_warn "${name} 已在运行 (PID: $old_pid, 端口: $port)"
            return 0
        else
            rm -f "$pid_file"
        fi
    fi

    # 检查端口是否被占用
    if lsof -i :"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        local occupied_pid=$(lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
        log_error "${name} 端口 $port 已被进程 $occupied_pid 占用"
        return 1
    fi

    log_info "启动 ${name} (端口: $port)..."

    # 启动服务（后台运行，日志写文件；不用 tee 子进程，避免占住调用方管道）
    cd "$PROJECT_ROOT"
    PYTHONPATH="$PYTHONPATH" nohup "$PYTHON" -m uvicorn "${module}:app" \
        --host 0.0.0.0 \
        --port "$port" \
        --log-level info \
        >> "$log_file" 2>&1 < /dev/null &

    local pid=$!
    echo "$pid" > "$pid_file"

    # 等待启动
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        log_info "${name} 启动成功 (PID: $pid, 端口: $port)"
    else
        log_error "${name} 启动失败，查看日志: $log_file"
        rm -f "$pid_file"
        return 1
    fi
}

# ═══════════════════════════════════════════════════════════════
# 函数: 启动 web（合并入口，单进程绑定全部契约端口 —— Phase 4 进程终态）
# ═══════════════════════════════════════════════════════════════
# 全部模块 router 拼进一个 FastAPI app（backend/processes/main.py），
# 经 backend.processes.serve 一个进程绑定 services.conf 全部端口。
all_service_ports() {
    printf '%s\n' "${SERVICES[@]}" | cut -d: -f3
}

start_web() {
    local pid_file="$PID_DIR/web.pid"
    local log_file="$LOG_DIR/web.log"

    if [ -f "$pid_file" ]; then
        local old_pid=$(cat "$pid_file")
        if kill -0 "$old_pid" 2>/dev/null; then
            log_warn "web 已在运行 (PID: $old_pid)"
            return 0
        else
            rm -f "$pid_file"
        fi
    fi

    # 全部契约端口必须空闲（单进程一次绑定）
    local ports=""
    while read -r port; do
        ports="$ports $port"
        if lsof -i :"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            local occupied_pid=$(lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
            log_error "web 端口 $port 已被进程 $occupied_pid 占用"
            return 1
        fi
    done < <(all_service_ports)

    log_info "启动 web 合并入口 (端口:${ports})..."
    cd "$PROJECT_ROOT"
    PYTHONPATH="$PYTHONPATH" nohup "$PYTHON" -m backend.processes.serve \
        --host 0.0.0.0 --log-level info \
        >> "$log_file" 2>&1 < /dev/null &

    local pid=$!
    echo "$pid" > "$pid_file"

    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        log_info "web 启动成功 (PID: $pid, 端口:${ports})"
    else
        log_error "web 启动失败，查看日志: $log_file"
        rm -f "$pid_file"
        return 1
    fi
}

# ═══════════════════════════════════════════════════════════════
# 函数: 启动 Celery worker / beat（后台守护模式）
# ═══════════════════════════════════════════════════════════════
# 按 cmdline 查找 celery 实例（前台实例不写 pid 文件，只靠 pid 文件查重会漏，
# 叠加启动会产生双 worker/双 beat，定时任务会被重复调度）
# role: celery-worker | celery-beat
find_celery_pids() {
    local role="$1" kw="worker"
    [ "$role" = "celery-beat" ] && kw="beat"
    ps -eo pid,cmd | awk -v app="$CELERY_APP" -v kw=" $kw" \
        '$2 != "awk" && index($0, "-m celery -A " app) > 0 && index($0, kw) > 0 {print $1}'
}

# 构造指定 Celery 进程的命令到全局数组 CELERY_CMD
build_celery_cmd() {
    local name="$1"
    if [ "$name" = "celery-worker" ]; then
        CELERY_CMD=("$PYTHON" -m celery -A "$CELERY_APP" worker -Q scheduled,default -l info -c 4)
    else
        CELERY_CMD=("$PYTHON" -m celery -A "$CELERY_APP" beat -l info)
    fi
}

start_celery_process() {
    local name="$1"
    local pid_file="$PID_DIR/${name}.pid"
    local log_file="$LOG_DIR/${name}.log"

    if [ -f "$pid_file" ]; then
        local old_pid=$(cat "$pid_file")
        if kill -0 "$old_pid" 2>/dev/null; then
            log_warn "${name} 已在运行 (PID: $old_pid)"
            return 0
        fi
        rm -f "$pid_file"
    fi

    # pid 文件之外再按 cmdline 兜底查重（已有实例则不叠加）
    local existing=$(find_celery_pids "$name" | tr '\n' ' ')
    if [ -n "$existing" ]; then
        log_warn "${name} 已有实例运行 (PID: ${existing}), 跳过启动"
        return 0
    fi

    log_info "启动 ${name}..."

    build_celery_cmd "$name"
    cd "$PROJECT_ROOT"
    PYTHONPATH="$PYTHONPATH" nohup "${CELERY_CMD[@]}" \
        >> "$log_file" 2>&1 < /dev/null &

    local pid=$!
    echo "$pid" > "$pid_file"

    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        log_info "${name} 启动成功 (PID: $pid)"
    else
        log_error "${name} 启动失败，查看日志: $log_file"
        rm -f "$pid_file"
        return 1
    fi
}

start_celery() {
    local success=0 fail=0
    for name in "${CELERY_PROCS[@]}"; do
        if start_celery_process "$name"; then ((success++)); else ((fail++)); fi
    done
    [ $fail -eq 0 ]
}

# ═══════════════════════════════════════════════════════════════
# 函数: 停止单个服务
# ═══════════════════════════════════════════════════════════════
stop_service() {
    local name="$1"
    local pid_file="$PID_DIR/${name}.pid"

    if [ ! -f "$pid_file" ]; then
        log_warn "${name} 未在运行"
        return 0
    fi

    local pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
        log_info "停止 ${name} (PID: $pid)..."
        kill "$pid"
        # 等待进程退出
        for i in {1..10}; do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        # 如果还在运行，强制终止
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "${name} 未响应，强制终止..."
            kill -9 "$pid" 2>/dev/null
        fi
        rm -f "$pid_file"
        log_info "${name} 已停止"
    else
        log_warn "${name} 进程已不存在"
        rm -f "$pid_file"
    fi
}

# ═══════════════════════════════════════════════════════════════
# 函数: 启动 DataEngine（Rust 二进制）
# ═══════════════════════════════════════════════════════════════
start_dataengine() {
    local pid_file="$DATAENGINE_PID"
    local log_file="$DATAENGINE_LOG"

    # 检查二进制文件
    if [ ! -f "$DATAENGINE_BIN" ]; then
        log_error "DataEngine 二进制不存在: $DATAENGINE_BIN"
        log_info "请先编译: cd services/dataengine && cargo build --release"
        return 1
    fi

    # 检查是否已运行
    if [ -f "$pid_file" ]; then
        local old_pid=$(cat "$pid_file")
        if kill -0 "$old_pid" 2>/dev/null; then
            log_warn "dataengine 已在运行 (PID: $old_pid, 端口: $DATAENGINE_PORT)"
            return 0
        else
            rm -f "$pid_file"
        fi
    fi

    # 检查端口是否被占用
    if lsof -i :"$DATAENGINE_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
        local occupied_pid=$(lsof -i :"$DATAENGINE_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
        log_error "dataengine 端口 $DATAENGINE_PORT 已被进程 $occupied_pid 占用"
        return 1
    fi

    log_info "启动 dataengine (端口: $DATAENGINE_PORT)..."

    # 启动 DataEngine
    cd "$PROJECT_ROOT/services/dataengine"
    nohup env GATEWAY_PORT="$DATAENGINE_PORT" "$DATAENGINE_BIN" \
        >> "$log_file" 2>&1 < /dev/null &

    local pid=$!
    echo "$pid" > "$pid_file"

    # 等待启动
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        log_info "dataengine 启动成功 (PID: $pid, 端口: $DATAENGINE_PORT)"
    else
        log_error "dataengine 启动失败，查看日志: $log_file"
        rm -f "$pid_file"
        return 1
    fi
}

# ═══════════════════════════════════════════════════════════════
# 函数: 停止 DataEngine
# ═══════════════════════════════════════════════════════════════
stop_dataengine() {
    local name="dataengine"
    local pid_file="$DATAENGINE_PID"

    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  停止 ${name} (PID: $pid)..."
            kill "$pid" 2>/dev/null
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null
            fi
            rm -f "$pid_file"
            echo -e "  ${GREEN}${name} 已停止${NC}"
            return 0
        fi
        rm -f "$pid_file"
    fi

    # PID 文件不存在时，通过端口查找进程
    local de_pid=$(lsof -i :"$DATAENGINE_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
    if [ -n "$de_pid" ]; then
        echo -e "  停止 ${name} (PID: $de_pid, 通过端口发现)..."
        kill "$de_pid" 2>/dev/null
        sleep 2
        if kill -0 "$de_pid" 2>/dev/null; then
            kill -9 "$de_pid" 2>/dev/null
        fi
        echo -e "  ${GREEN}${name} 已停止${NC}"
    fi
}

# ═══════════════════════════════════════════════════════════════
# 函数: 启动前端
# ═══════════════════════════════════════════════════════════════
start_frontend() {
    local pid_file="$PID_DIR/frontend.pid"
    local log_file="$LOG_DIR/frontend.log"

    # 检查是否已运行
    if [ -f "$pid_file" ]; then
        local old_pid=$(cat "$pid_file")
        if kill -0 "$old_pid" 2>/dev/null; then
            log_warn "frontend 已在运行 (PID: $old_pid, 端口: $FRONTEND_PORT)"
            return 0
        else
            rm -f "$pid_file"
        fi
    fi

    # 检查端口
    if lsof -i :"$FRONTEND_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
        local occupied_pid=$(lsof -i :"$FRONTEND_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
        log_error "frontend 端口 $FRONTEND_PORT 已被进程 $occupied_pid 占用"
        return 1
    fi

    # 检查前端目录
    if [ ! -f "$FRONTEND_DIR/package.json" ]; then
        log_warn "前端目录不存在，跳过: $FRONTEND_DIR"
        return 1
    fi

    log_info "启动 frontend (端口: $FRONTEND_PORT)..."

    cd "$FRONTEND_DIR"
    nohup npm run dev >> "$log_file" 2>&1 < /dev/null &
    local npm_pid=$!

    # 等待 vite 子进程启动并监听端口
    local wait_count=0
    while [ $wait_count -lt 15 ]; do
        # 找到监听 FRONTEND_PORT 的 vite 进程
        local vite_pid=$(lsof -i :"$FRONTEND_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
        if [ -n "$vite_pid" ]; then
            echo "$vite_pid" > "$pid_file"
            log_info "frontend 启动成功 (PID: $vite_pid, 端口: $FRONTEND_PORT)"
            return 0
        fi
        sleep 1
        ((wait_count++))
    done

    log_error "frontend 启动超时，查看日志: $log_file"
    rm -f "$pid_file"
    return 1
}

# ═══════════════════════════════════════════════════════════════
# 函数: 停止前端
# ═══════════════════════════════════════════════════════════════
stop_frontend() {
    local name="frontend"
    local pid_file="$PID_DIR/${name}.pid"

    # 尝试从 PID 文件停止
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  停止 ${name} (PID: $pid)..."
            kill "$pid" 2>/dev/null
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null
            fi
            rm -f "$pid_file"
            echo -e "  ${GREEN}${name} 已停止${NC}"
            return 0
        fi
        rm -f "$pid_file"
    fi

    # PID 文件不存在时，通过端口查找进程
    local vite_pid=$(lsof -i :"$FRONTEND_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
    if [ -n "$vite_pid" ]; then
        echo -e "  停止 ${name} (PID: $vite_pid, 通过端口发现)..."
        kill "$vite_pid" 2>/dev/null
        sleep 2
        if kill -0 "$vite_pid" 2>/dev/null; then
            kill -9 "$vite_pid" 2>/dev/null
        fi
        echo -e "  ${GREEN}${name} 已停止${NC}"
    fi
}

# ═══════════════════════════════════════════════════════════════
# 前台模式函数（日志带彩色服务名前缀，合并输出到控制台）
# ═══════════════════════════════════════════════════════════════

# 前台模式：记录真实监听进程 PID 供 stop-all.sh 精确停止
# （前台管道的 $! 是 awk 前缀进程而非服务本身，不登记会让 PID 文件过期）
write_fg_port_pid() {
    local name="$1" port="$2"
    local real_pid=""
    for _ in {1..10}; do
        real_pid=$(lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
        [ -n "$real_pid" ] && break
        sleep 1
    done
    if [ -n "$real_pid" ]; then
        echo "$real_pid" > "$PID_DIR/${name}.pid"
    else
        log_warn "${name} 未能解析监听进程，PID 文件未登记（stop-all.sh 将按端口兜底）"
    fi
}

fg_cleanup() {
    # 仅前台模式有子进程需要清理；后台(-d)模式退出也会命中本 EXIT trap，
    # 无前台子进程时静默退出，不打印误导性的“正在停止所有服务...”
    if [ -n "${FG_PIDS[*]}" ]; then
        echo ""
        log_info "正在停止所有服务..."
        for pid in "${FG_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null
            fi
        done
        sleep 1
        for pid in "${FG_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null
            fi
        done
        log_info "所有服务已停止"
    fi
    exit 0
}

trap fg_cleanup SIGINT SIGTERM EXIT

# 前台模式启动单个 Python 服务
start_service_fg() {
    local name="$1"
    local module="$2"
    local port="$3"
    local color="$4"

    # 检查端口
    if lsof -i :"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        log_error "${name} 端口 $port 已被占用"
        return 1
    fi

    log_info "启动 ${name} (端口: $port)"

    # 在子 shell 中启动，日志经 tee 落盘（前台模式也留现场）+ awk 加彩色前缀后输出到控制台
    (
        cd "$PROJECT_ROOT"
        PYTHONPATH="$PYTHONPATH" exec "$PYTHON" -m uvicorn "${module}:app" \
            --host 0.0.0.0 --port "$port" --log-level info 2>&1
    ) | tee -a "$LOG_DIR/${name}.log" | awk -v svc="$name" -v c="$color" -v n="\033[0m" \
        '{printf "%s[%-12s]%s %s\n", c, svc, n, $0; fflush()}' &

    local pid=$!
    FG_PIDS+=("$pid")

    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        log_error "${name} 启动失败"
        return 1
    fi

    write_fg_port_pid "$name" "$port"
}

# 前台模式启动 DataEngine
start_dataengine_fg() {
    local color="$1"

    if [ ! -f "$DATAENGINE_BIN" ]; then
        log_error "DataEngine 二进制不存在: $DATAENGINE_BIN"
        return 1
    fi

    if lsof -i :"$DATAENGINE_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
        log_error "dataengine 端口 $DATAENGINE_PORT 已被占用"
        return 1
    fi

    log_info "启动 dataengine (端口: $DATAENGINE_PORT)"

    (
        cd "$PROJECT_ROOT/services/dataengine"
        exec env GATEWAY_PORT="$DATAENGINE_PORT" "$DATAENGINE_BIN" 2>&1
    ) | tee -a "$DATAENGINE_LOG" | awk -v svc="dataengine" -v c="$color" -v n="\033[0m" \
        '{printf "%s[%-12s]%s %s\n", c, svc, n, $0; fflush()}' &

    local pid=$!
    FG_PIDS+=("$pid")

    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        log_error "dataengine 启动失败"
        return 1
    fi
}

# 前台模式启动前端
start_frontend_fg() {
    local color="$1"

    if [ ! -f "$FRONTEND_DIR/package.json" ]; then
        log_warn "前端目录不存在，跳过"
        return 1
    fi

    if lsof -i :"$FRONTEND_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
        log_error "frontend 端口 $FRONTEND_PORT 已被占用"
        return 1
    fi

    log_info "启动 frontend (端口: $FRONTEND_PORT)"

    (
        cd "$FRONTEND_DIR"
        exec npm run dev 2>&1
    ) | tee -a "$LOG_DIR/frontend.log" | awk -v svc="frontend" -v c="$color" -v n="\033[0m" \
        '{printf "%s[%-12s]%s %s\n", c, svc, n, $0; fflush()}' &

    local pid=$!
    FG_PIDS+=("$pid")

    write_fg_port_pid "frontend" "$FRONTEND_PORT"
}

# 前台模式启动 Celery worker / beat
start_celery_fg() {
    local name="$1"
    local color="$2"

    # 与后台模式一致兜底查重，防止前台重复启动叠加双实例
    local existing=$(find_celery_pids "$name" | tr '\n' ' ')
    if [ -n "$existing" ]; then
        log_warn "${name} 已有实例运行 (PID: ${existing}), 跳过启动"
        return 0
    fi

    log_info "启动 ${name}"

    build_celery_cmd "$name"
    (
        cd "$PROJECT_ROOT"
        PYTHONPATH="$PYTHONPATH" exec "${CELERY_CMD[@]}" 2>&1
    ) | tee -a "$LOG_DIR/${name}.log" | awk -v svc="$name" -v c="$color" -v n="\033[0m" \
        '{printf "%s[%-12s]%s %s\n", c, svc, n, $0; fflush()}' &

    FG_PIDS+=("$!")
}

# 前台模式启动 web 合并入口（单进程多端口）
start_web_fg() {
    local color="$1"

    local ports=""
    while read -r port; do
        ports="$ports $port"
        if lsof -i :"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            log_error "web 端口 $port 已被占用"
            return 1
        fi
    done < <(all_service_ports)

    log_info "启动 web 合并入口 (端口:${ports})"

    (
        cd "$PROJECT_ROOT"
        PYTHONPATH="$PYTHONPATH" exec "$PYTHON" -m backend.processes.serve \
            --host 0.0.0.0 --log-level info 2>&1
    ) | tee -a "$LOG_DIR/web.log" | awk -v svc="web" -v c="$color" -v n="\033[0m" \
        '{printf "%s[%-12s]%s %s\n", c, svc, n, $0; fflush()}' &

    local pid=$!
    FG_PIDS+=("$pid")

    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        log_error "web 启动失败"
        return 1
    fi

    write_fg_port_pid "web" "$(all_service_ports | head -1)"
}

# 前台模式主流程
run_foreground() {
    FG_MODE=true

    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  AI-DataHub 数据中台 — 前台模式（Ctrl+C 停止）${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo ""

    local ci=0
    local success=0
    local fail=0

    # dataengine 已退役（Phase 7.3）：执行载体由 semantics.execution 内嵌；
    # 应急回退（SEMANTIC_ENGINE_ENABLED=false）时手动运行 start_dataengine_fg

    # 启动 web 合并入口（单进程多端口）
    start_web_fg "${FG_COLORS[$ci]}"
    ((ci++))
    if [ $? -eq 0 ]; then ((success++)); else ((fail++)); fi

    # 启动 Celery worker + beat
    for cname in "${CELERY_PROCS[@]}"; do
        start_celery_fg "$cname" "${FG_COLORS[$((ci % ${#FG_COLORS[@]}))]}"
        ((ci++))
        ((success++))
    done

    # 启动前端
    start_frontend_fg "${FG_COLORS[$ci]}"
    if [ $? -eq 0 ]; then ((success++)); else ((fail++)); fi

    echo ""
    log_info "${success} 个服务已启动, ${fail} 个失败"
    echo ""
    echo -e "  ${BLUE}前端:${NC}     http://localhost:${FRONTEND_PORT}"
    echo -e "  ${BLUE}DataMind:${NC} http://localhost:8001"
    echo -e "  ${BLUE}AuthService:${NC} http://localhost:8006"
    echo ""
    echo -e "  ${YELLOW}按 Ctrl+C 停止所有服务${NC}"
    echo ""

    # 前台等待，任何子进程退出时继续等待其余进程
    wait
}

# ═══════════════════════════════════════════════════════════════
# 函数: 显示状态
# ═══════════════════════════════════════════════════════════════
show_status() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  AI-DataHub 数据中台 — 服务状态${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    printf "  %-20s %-8s %-8s %-10s\n" "服务名" "端口" "PID" "状态"
    echo "  ─────────────────────────────────────────────────────"

    # dataengine 已退役（Phase 7.3），状态行不再展示；应急手动启动见 start_dataengine

    # web 合并入口（单进程承载全部契约端口）
    local web_pid_file="$PID_DIR/web.pid"
    local web_status="未运行"
    local web_pid="-"
    if [ -f "$web_pid_file" ]; then
        web_pid=$(cat "$web_pid_file")
        if kill -0 "$web_pid" 2>/dev/null; then
            web_status="${GREEN}运行中${NC}"
        else
            web_status="${RED}已停止${NC}"
            web_pid="-"
        fi
    fi
    local web_ports=$(all_service_ports | paste -sd, -)
    printf "  %-20s %-16s %-8s " "web" "$web_ports" "$web_pid"
    echo -e "$web_status"

    # 前端状态
    local frontend_pid_file="$PID_DIR/frontend.pid"
    local frontend_status="未运行"
    local frontend_pid="-"
    if [ -f "$frontend_pid_file" ]; then
        frontend_pid=$(cat "$frontend_pid_file")
        if kill -0 "$frontend_pid" 2>/dev/null; then
            frontend_status="${GREEN}运行中${NC}"
        else
            frontend_status="${RED}已停止${NC}"
            frontend_pid="-"
        fi
    fi
    printf "  %-20s %-8s %-8s " "frontend" "$FRONTEND_PORT" "$frontend_pid"
    echo -e "$frontend_status"

    # Celery worker / beat 状态（无端口）
    for cname in "${CELERY_PROCS[@]}"; do
        local c_pid_file="$PID_DIR/${cname}.pid"
        local c_status="未运行"
        local c_pid="-"
        if [ -f "$c_pid_file" ]; then
            c_pid=$(cat "$c_pid_file")
            if kill -0 "$c_pid" 2>/dev/null; then
                c_status="${GREEN}运行中${NC}"
            else
                c_status="${RED}已停止${NC}"; c_pid="-"
            fi
        fi
        printf "  %-20s %-8s %-8s " "$cname" "-" "$c_pid"
        echo -e "$c_status"
    done

    echo ""
    echo -e "  日志目录: ${LOG_DIR}"
    echo -e "  PID目录:  ${PID_DIR}"
    echo ""
    echo -e "  前端:     http://localhost:3000"
    echo ""
}

# ═══════════════════════════════════════════════════════════════
# 主逻辑
# ═══════════════════════════════════════════════════════════════

# 加载环境变量
if [ -f "$PROJECT_ROOT/services/.env" ]; then
    set -a
    source "$PROJECT_ROOT/services/.env"
    set +a
fi

case "${1:-all}" in
    -d|--daemon)
        # 后台守护模式（原有的后台启动逻辑）
        trap - SIGINT SIGTERM EXIT  # 取消前台模式的 trap
        echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
        echo -e "${BLUE}  AI-DataHub 数据中台 — 后台模式启动${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
        echo ""

        success=0
        fail=0

        # dataengine 已退役（Phase 7.3）：执行载体由 semantics.execution 内嵌；
        # 应急回退（SEMANTIC_ENGINE_ENABLED=false）时手动运行 start_dataengine

        # 启动 web 合并入口（单进程多端口，替代原 8 个微服务进程）
        if start_web; then
            ((success++))
        else
            ((fail++))
        fi

        # 启动 Celery worker + beat（依赖 dataflow 任务模块，排在微服务之后）
        if start_celery; then
            ((success+=2))
        else
            ((fail++))
        fi

        # 启动前端
        if start_frontend; then
            ((success++))
        else
            ((fail++))
        fi

        echo ""
        log_info "启动完成: ${success} 成功, ${fail} 失败"
        show_status
        ;;
    all)
        # 默认前台模式
        run_foreground
        ;;
    status)
        show_status
        ;;
    frontend)
        if [ "${2:-}" = "-d" ]; then
            start_frontend
        else
            start_frontend_fg "${FG_COLORS[0]}"
            FG_MODE=true
            wait
        fi
        ;;
    celery)
        # 只启动 Celery worker + beat
        if [ "${2:-}" = "-d" ]; then
            start_celery
        else
            start_celery_fg "celery-worker" "${FG_COLORS[0]}"
            start_celery_fg "celery-beat" "${FG_COLORS[1]}"
            FG_MODE=true
            wait
        fi
        ;;
    *)
        # 启动指定服务
        found=false
        for svc in "${SERVICES[@]}"; do
            IFS=':' read -r name module port <<< "$svc"
            if [ "$name" = "$1" ]; then
                if [ "${2:-}" = "-d" ]; then
                    trap - SIGINT SIGTERM EXIT  # 后台单服务模式与全量 -d 一致，取消前台清理 trap
                    start_service "$name" "$module" "$port"
                else
                    start_service_fg "$name" "$module" "$port" "${FG_COLORS[0]}"
                    FG_MODE=true
                    wait
                fi
                found=true
                break
            fi
        done
        if [ "$found" = false ] && [ "$1" != "frontend" ] && [ "$1" != "celery" ]; then
            log_error "未知服务: $1"
            echo "可用服务: ${SERVICES[*]} frontend celery"
            exit 1
        fi
        ;;
esac
