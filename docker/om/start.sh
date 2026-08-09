#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# OpenMetadata 启动脚本
# 用法: ./start.sh
# ═══════════════════════════════════════════════════════════════

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 加载环境变量（OM_* 覆盖项在 services/.env 中）
if [ -f "$PROJECT_ROOT/services/.env" ]; then
    set -a
    source "$PROJECT_ROOT/services/.env"
    set +a
fi

OM_SERVER_PORT="${OM_SERVER_PORT:-8585}"

# 选择 docker compose 命令
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    log_error "未找到 docker compose / docker-compose，请先安装 Docker"
    exit 1
fi

cd "$SCRIPT_DIR"
log_info "启动 OpenMetadata 编排 (compose: $DC)..."
$DC up -d

# 健康检查轮询
log_info "等待 OpenMetadata Server 就绪 (http://localhost:${OM_SERVER_PORT}) ..."
wait_count=0
while [ $wait_count -lt 60 ]; do
    if curl -sf "http://localhost:${OM_SERVER_PORT}/api/v1/system/version" >/dev/null 2>&1; then
        log_info "OpenMetadata Server 已就绪"
        echo ""
        echo "  UI:     http://localhost:${OM_SERVER_PORT}  (默认账号 admin / admin)"
        echo "  Airflow: http://localhost:${OM_AIRFLOW_PORT:-18080}  (admin / admin)"
        echo ""
        log_info "下一步: 初始化数据源与采集管道"
        echo "  cd $SCRIPT_DIR && python init_om.py"
        exit 0
    fi
    sleep 5
    ((wait_count++))
done

log_error "OpenMetadata Server 启动超时（5 分钟），请检查: $DC logs openmetadata-server"
exit 1
