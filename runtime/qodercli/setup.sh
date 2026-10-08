#!/bin/bash
# Runtime 环境初始化脚本
# 在部署到新机器时运行，安装 Node.js 和 qodercn 执行层 CLI(@qodercn-ai/qoderclicn)
#
# 用法: bash runtime/qodercli/setup.sh
#
# 口径(2026-10 qodercn 切换): 执行层 CLI 为 npm 包 @qodercn-ai/qoderclicn(qoder.cn
# 体系, 配置目录 ~/.qoder-cn、凭据变量 QODERCN_PERSONAL_ACCESS_TOKEN), 由 runtime/node
# 驱动; wrapper bin/qodercli 文件名保持不变(DB cli_path/测试契约的稳定接口)。
# 注意 npm 包 @qoder-ai/qoderclicn 是占位空包, 正式 CN 分发在 scope @qodercn-ai 下。

set -e

RUNTIME_DIR="$(cd "$(dirname "$0")/.." && pwd)"
QODERCLI_DIR="$RUNTIME_DIR/qodercli"

echo "=== Runtime Setup (qodercn) ==="

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

# ── 2. 缓存 qodercn 执行层 CLI ──────────────────────────────────────
cd "$QODERCLI_DIR"
mkdir -p node_modules/@qodercn-ai

if [ -f "node_modules/@qodercn-ai/qoderclicn/bundle/qoderclicn.js" ]; then
    echo "qoderclicn (qodercn) already installed."
else
    echo "Installing @qodercn-ai/qoderclicn..."
    # --no-bin-links: 运行目录不支持 symlink 时也能装; --cache 收敛到 runtime/.npm-cache
    "$NODE_DIR/bin/npm" install --prefix . @qodercn-ai/qoderclicn --save=false \
        --no-bin-links --no-audit --no-fund \
        --cache "$RUNTIME_DIR/.npm-cache" 2>/dev/null || {
        echo "ERROR: @qodercn-ai/qoderclicn install failed."
        echo "  手动兜底: cp -r <qoderclicn-path> runtime/qodercli/node_modules/@qodercn-ai/"
        exit 1
    }
fi

# 确保 bin/qodercli 可执行(内部指向 @qodercn-ai/qoderclicn/bundle/qoderclicn.js)
chmod +x bin/qodercli

# ── 3. 验证 ─────────────────────────────────────────────────────────
echo ""
echo "Verifying installation..."
VERSION=$(bin/qodercli --version 2>&1 | head -1)
if [ -n "$VERSION" ] && [ "$VERSION" != "" ]; then
    echo "OK: qoderclicn $VERSION"
else
    echo "WARN: qoderclicn may not be working correctly"
fi

echo ""
echo "=== Setup Complete ==="
echo "CLI path: $QODERCLI_DIR/bin/qodercli (→ @qodercn-ai/qoderclicn)"
echo "Node.js:  $NODE_DIR/bin/node"
