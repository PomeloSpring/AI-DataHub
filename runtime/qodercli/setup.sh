#!/bin/bash
# Runtime 环境初始化脚本
# 在部署到新机器时运行，安装 Node.js 和 qodercli
#
# 用法: bash runtime/qodercli/setup.sh

set -e

RUNTIME_DIR="$(cd "$(dirname "$0")/.." && pwd)"
QODERCLI_DIR="$RUNTIME_DIR/qodercli"

echo "=== Runtime Setup ==="

# ── 1. 安装 Node.js 20 ──────────────────────────────────────────────
NODE_DIR="$RUNTIME_DIR/node"
NODE_VERSION="v20.18.0"
NODE_URL="https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz"

if [ -x "$NODE_DIR/bin/node" ]; then
    echo "Node.js already installed: $($NODE_DIR/bin/node --version)"
else
    echo "Installing Node.js ${NODE_VERSION}..."
    cd /tmp
    curl -sL "$NODE_URL" -o node.tar.xz
    tar -xf node.tar.xz
    mv "node-${NODE_VERSION}-linux-x64" "$NODE_DIR"
    rm -f node.tar.xz
    echo "Node.js installed: $($NODE_DIR/bin/node --version)"
fi

# ── 2. 安装 qodercli ────────────────────────────────────────────────
cd "$QODERCLI_DIR"
mkdir -p node_modules/@qoder-ai

if [ -f "node_modules/@qoder-ai/qodercli/bundle/qodercli.js" ]; then
    echo "qodercli already installed."
else
    echo "Installing qodercli..."
    # 使用内置 npm 安装
    "$NODE_DIR/bin/npm" install --prefix . @qoder-ai/qodercli --save=false 2>/dev/null || {
        echo "npm install failed, trying global copy..."
        GLOBAL_PATH=$(npm root -g)/@qoder-ai/qodercli 2>/dev/null || true
        if [ -d "$GLOBAL_PATH" ]; then
            cp -r "$GLOBAL_PATH" node_modules/@qoder-ai/
            echo "Copied from global npm."
        else
            echo "ERROR: qodercli not found. Please install manually:"
            echo "  cp -r <qodercli-path> runtime/qodercli/node_modules/@qoder-ai/"
            exit 1
        fi
    }
fi

# 确保 bin/qodercli 可执行
chmod +x bin/qodercli

# ── 3. 验证 ─────────────────────────────────────────────────────────
echo ""
echo "Verifying installation..."
VERSION=$(bin/qodercli --version 2>&1 | head -1)
if [ -n "$VERSION" ] && [ "$VERSION" != "" ]; then
    echo "OK: qodercli $VERSION"
else
    echo "WARN: qodercli may not be working correctly"
fi

echo ""
echo "=== Setup Complete ==="
echo "CLI path: $QODERCLI_DIR/bin/qodercli"
echo "Node.js:  $NODE_DIR/bin/node"
