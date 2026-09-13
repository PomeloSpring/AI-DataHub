#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# AI-DataHub 数据中台 — 重启脚本
# 用法: ./restart-all.sh [选项] [服务名]
#   ./restart-all.sh          # 停止所有服务，然后后台启动
#   ./restart-all.sh -d       # 同上（显式后台模式）
#   ./restart-all.sh datamind # 重启指定服务（后台）
# ═══════════════════════════════════════════════════════════════

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "═══════════════════════════════════════════"
echo "  停止服务..."
echo "═══════════════════════════════════════════"
"$PROJECT_ROOT/stop-all.sh" "$@"

echo ""
echo "═══════════════════════════════════════════"
echo "  启动服务（后台模式）..."
echo "═══════════════════════════════════════════"

# 重启始终使用后台模式
if [ "$#" -eq 0 ]; then
    "$PROJECT_ROOT/start-all.sh" -d
else
    # 如果传了服务名，确保加 -d 标志
    args=()
    has_daemon=false
    for arg in "$@"; do
        [ "$arg" = "-d" ] || [ "$arg" = "--daemon" ] && has_daemon=true
        args+=("$arg")
    done
    if [ "$has_daemon" = false ]; then
        "$PROJECT_ROOT/start-all.sh" "${args[@]}" -d
    else
        "$PROJECT_ROOT/start-all.sh" "${args[@]}"
    fi
fi
