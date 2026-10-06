#!/bin/bash
# qmind CLI 安装脚本 — 下载 qmind 二进制到 runtime/qmind/
#
# 用法: bash runtime/qmind/setup.sh
# 新机器部署或二进制丢失时执行; 已存在则跳过。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/qmind"
OSS_BASE="https://qoder-ide-cn.oss-accelerate.aliyuncs.com/qmind/cli"

# ── 平台检测（与 qmind_retriever._platform_slug 一致）──────────────────
uname_os=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$uname_os" in
  darwin)  os_name="darwin" ;;
  linux)   os_name="linux" ;;
  msys*|mingw*|cygwin*) os_name="windows" ;;
  *)       os_name="linux" ;;
esac

uname_m=$(uname -m)
case "$uname_m" in
  x86_64|amd64)  arch="amd64" ;;
  aarch64|arm64) arch="arm64" ;;
  *)             arch="$uname_m" ;;
esac

ext=""
[ "$os_name" = "windows" ] && ext=".exe"
binary_name="qmind-${os_name}-${arch}${ext}"

# ── 安装 ──────────────────────────────────────────────────────────────
if [ -x "$TARGET" ]; then
    echo "[qmind] 已存在: $TARGET ($("$TARGET" --version 2>&1 | head -1))"
    exit 0
fi

echo "[qmind] 下载 $binary_name → runtime/qmind/qmind"
curl -fsSL "${OSS_BASE}/${binary_name}" -o "$TARGET"
chmod +x "$TARGET"

echo "[qmind] 完成: $TARGET"
echo "[qmind] 验证: $TARGET --version"
