#!/bin/bash
# 初始化 SQLite 数据库
# 用法: ./docker/sqlite/init.sh

set -e

DB_DIR="data"
DB_FILE="$DB_DIR/metadata.db"
INIT_SQL="docker/sqlite/init.sql"

# 创建数据目录
mkdir -p "$DB_DIR"

# 检查 init.sql 是否存在
if [ ! -f "$INIT_SQL" ]; then
    echo "Error: $INIT_SQL not found"
    exit 1
fi

# 初始化数据库
echo "Initializing SQLite database at $DB_FILE..."
sqlite3 "$DB_FILE" < "$INIT_SQL"

echo "Done! Database initialized at $DB_FILE"
echo "Tables created:"
sqlite3 "$DB_FILE" ".tables"
