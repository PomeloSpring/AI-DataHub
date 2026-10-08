#!/bin/bash
# 薄壳：统一实现见 backend/scripts/start-service.sh
# 端口/模块登记在 backend/scripts/services.conf
DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="datacatalog" SERVICE_DIR="$DIR" \
    exec "$DIR/../../scripts/start-service.sh" "$@"
