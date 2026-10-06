#!/bin/bash
# 薄壳：统一实现见 services/shared/scripts/start-service.sh
# 端口/模块登记在 services/shared/scripts/services.conf
DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="$(basename "$DIR")" SERVICE_DIR="$DIR" \
    exec "$DIR/../shared/scripts/start-service.sh" "$@"
