#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# kill-ports.sh — 清理本项目服务占用的端口
#
# 用法:
#   ./kill-ports.sh              杀掉全部项目端口占用 (交互确认)
#   ./kill-ports.sh -y           跳过确认直接杀
#   ./kill-ports.sh --status     仅查看端口占用, 不杀
#   ./kill-ports.sh 8001 8012    只杀指定端口
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$SCRIPT_DIR/pids"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 端口 → 服务名: Python 微服务读 services.conf（唯一权威清单），其余特殊进程在此补充
declare -A PORT_SERVICE=(
    [3000]="frontend-dev"
    [8082]="dataengine"
)
if [ -f "$SCRIPT_DIR/services/shared/scripts/services.conf" ]; then
    while IFS=: read -r _name _module _port; do
        PORT_SERVICE[$_port]="$_name"
    done < <(grep -vE '^\s*(#|$)' "$SCRIPT_DIR/services/shared/scripts/services.conf")
else
    log_error "缺少 services.conf，仅处理特殊端口"
fi

STATUS_ONLY=0
AUTO_YES=0
TARGET_PORTS=()

for arg in "$@"; do
    case "$arg" in
        --status) STATUS_ONLY=1 ;;
        -y|--yes) AUTO_YES=1 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        [0-9]*)
            [ -z "${PORT_SERVICE[$arg]}" ] && log_warn "端口 $arg 不在项目清单中, 仍按用户指定处理"
            TARGET_PORTS+=("$arg") ;;
        *) log_error "未知参数: $arg (见 --help)"; exit 1 ;;
    esac
done
# 未指定端口时默认全部项目端口
[ ${#TARGET_PORTS[@]} -eq 0 ] && TARGET_PORTS=("${!PORT_SERVICE[@]}")

# 按端口号排序, 输出稳定
IFS=$'\n' TARGET_PORTS=($(sort -n <<<"${TARGET_PORTS[*]}")); unset IFS

# 查询监听某端口的 PID (优先 ss, 兜底 lsof)
pids_on_port() {
    local port="$1" pids=""
    pids=$(ss -ltnp 2>/dev/null | grep -E ":${port} " | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
    if [ -z "$pids" ] && command -v lsof >/dev/null 2>&1; then
        pids=$(lsof -i :"${port}" -sTCP:LISTEN -t 2>/dev/null | sort -u)
    fi
    echo "$pids"
}

# celery 组无端口, 只能按 cmdline 查找. kill-ports 的语义是"清理本项目服务", 若漏掉 celery:
# API 服务全停后 celery 幸存继续刷日志(造成"celery 日志在跑但 API 全 pending"的误关联),
# 且下次启动叠加成双 worker/双 beat 致定时任务双发.
celery_pids() {
    ps -eo pid,cmd | awk -v app="services.dataflow.tasks.celery_app" \
        '$2 != "awk" && index($0, "-m celery -A " app) > 0 {print $1}' | sort -u
}

# ── 收集占用 ──
FOUND=0
declare -A PORT_PIDS
for port in "${TARGET_PORTS[@]}"; do
    pids=$(pids_on_port "$port")
    [ -z "$pids" ] && continue
    FOUND=1
    PORT_PIDS[$port]="$pids"
    name="${PORT_SERVICE[$port]:-custom}"
    for pid in $pids; do
        cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-80)
        echo "  :${port} (${name})  PID=${pid}  ${cmd}"
    done
done

# celery 组一并纳入清理范围
CELERY_PIDS=$(celery_pids)
if [ -n "$CELERY_PIDS" ]; then
    FOUND=1
    for pid in $CELERY_PIDS; do
        cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-80)
        echo "  :-- (celery)  PID=${pid}  ${cmd}"
    done
fi

if [ $FOUND -eq 0 ]; then
    log_info "目标端口与 celery 组均空闲, 无需处理"
    exit 0
fi
[ $STATUS_ONLY -eq 1 ] && exit 0

# ── 确认 ──
if [ $AUTO_YES -eq 0 ]; then
    read -r -p "$(echo -e ${YELLOW}终止以上进程? [y/N]${NC} )" ans
    [[ "$ans" != "y" && "$ans" != "Y" ]] && { log_warn "已取消"; exit 0; }
fi

# ── 终止: 先 TERM 优雅退出, 超时再 KILL ──
SELF_PID=$$
for port in "${!PORT_PIDS[@]}"; do
    name="${PORT_SERVICE[$port]:-custom}"
    for pid in ${PORT_PIDS[$port]}; do
        [ "$pid" = "$SELF_PID" ] && continue
        kill -TERM "$pid" 2>/dev/null
    done
done
for pid in $CELERY_PIDS; do
    [ "$pid" = "$SELF_PID" ] && continue
    kill -TERM "$pid" 2>/dev/null
done
# 统一等待回退, 避免逐端口串行 sleep
for i in {1..8}; do
    LEFT=0
    for port in "${!PORT_PIDS[@]}"; do
        [ -n "$(pids_on_port "$port")" ] && LEFT=1 && break
    done
    [ -n "$CELERY_PIDS" ] && [ -n "$(celery_pids)" ] && LEFT=1
    [ $LEFT -eq 0 ] && break
    sleep 1
done
for port in "${!PORT_PIDS[@]}"; do
    name="${PORT_SERVICE[$port]:-custom}"
    remain=$(pids_on_port "$port")
    if [ -n "$remain" ]; then
        log_warn "${name} (:${port}) 未响应 TERM, 强制终止: $remain"
        for pid in $remain; do kill -9 "$pid" 2>/dev/null; done
    else
        log_info "${name} (:${port}) 已释放"
    fi
    # 清理对应残留 pid 文件, 防止 start.sh 误判"已在运行"
    [ -n "$name" ] && rm -f "$PID_DIR/${name}.pid"
done

# celery 组收尾: 强杀残留 + 清 pid 文件(避免过期 pid 文件让 stop-all/start-all 误判)
if [ -n "$CELERY_PIDS" ]; then
    remain=$(celery_pids)
    if [ -n "$remain" ]; then
        log_warn "celery 未响应 TERM, 强制终止: $remain"
        for pid in $remain; do kill -9 "$pid" 2>/dev/null; done
    else
        log_info "celery 组已清理"
    fi
    rm -f "$PID_DIR/celery-worker.pid" "$PID_DIR/celery-beat.pid"
fi
log_info "完成"
