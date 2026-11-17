#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# frontend_port.sh — 前端 dev 端口的唯一解析口径
#
# 配置源: 根目录 .env 的 FRONTEND_PORT（缺省 3000）。同一个键也被
# docker-compose.full.yml 的端口映射消费，因此 dev 与容器部署同源。
#
# 用法:
#   source "$PROJECT_ROOT/backend/scripts/frontend_port.sh"   # 得到 $FRONTEND_PORT
#   backend/scripts/frontend_port.sh                          # 直接打印端口
#
# 为什么单独抽一个文件: start-all.sh / stop-all.sh / kill-ports.sh 都要知道
# 前端端口，历史上各自硬编码 3000 —— 改一处漏三处时，脚本会在错的端口上找
# 进程（表现为"启动超时"或"停不掉"）。端口口径只允许存在这一份实现。
#
# 注意: 后端监听口不在此处 —— 它属于 backend/scripts/services.conf
# （web:backend.app.main:8000，由 serve.py/脚本共同解析），两者不要混用。
# ═══════════════════════════════════════════════════════════════

_fp_self="${BASH_SOURCE[0]:-$0}"
_fp_root="$(cd "$(dirname "$_fp_self")/../.." && pwd)"
_fp_env="$_fp_root/.env"

# 读根 .env 的键值: 取最后一次赋值, 去引号与首尾空白; 无 .env 或未配置时用缺省值
_fp_read_env() {
    local key="$1" default="$2" value=""
    if [ -f "$_fp_env" ]; then
        value=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$_fp_env" | tail -1 \
            | cut -d= -f2- | tr -d "\"'" \
            | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    fi
    if [ -n "$value" ]; then printf '%s' "$value"; else printf '%s' "$default"; fi
}

# 环境变量优先（便于单次覆盖调试）: FRONTEND_PORT=4000 ./start-all.sh
FRONTEND_PORT="${FRONTEND_PORT:-$(_fp_read_env FRONTEND_PORT 3000)}"

if ! [[ "$FRONTEND_PORT" =~ ^[0-9]+$ ]]; then
    echo "[WARN] FRONTEND_PORT='$FRONTEND_PORT' 不是数字, 回退 3000" >&2
    FRONTEND_PORT=3000
fi

# 直接执行时打印端口
if [ "${BASH_SOURCE[0]:-}" = "$0" ]; then
    echo "$FRONTEND_PORT"
fi
