#!/bin/bash
# qmind CLI 预装脚本 — 用项目内 npm 把 qodercn 体系的 qmind-cli 缓存到 runtime/qmind-cli/
#
# 用法: bash runtime/qmind/setup.sh
# 新机器部署或 runtime/qmind-cli 缺失时执行; 已存在则跳过。
#
# 口径(2026-10 qodercn 切换): CLI 为 npm 包 @qoder-ai/qmind-cli(Node 版),
# 由 runtime/node 驱动; 目标 qoder.cn 网关由调用方成对 --sash/--dashboard 指定
# (见 services/datamind/rag/qmind_retriever.py)。凭据/缓存落在 runtime/qmind/home/。

set -euo pipefail

RUNTIME_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NODE_DIR="$RUNTIME_DIR/node"
QMIND_CLI_DIR="$RUNTIME_DIR/qmind-cli"
ENTRY="$QMIND_CLI_DIR/node_modules/@qoder-ai/qmind-cli/bin/cli.js"

# ── 1. 依赖项目内 Node.js ───────────────────────────────────────────
if [ ! -x "$NODE_DIR/bin/node" ]; then
    echo "[qmind] 缺少 $NODE_DIR/bin/node, 请先执行: bash runtime/qodercli/setup.sh(或 runtime/node 安装脚本)"
    exit 1
fi

# ── 2. 缓存 npm 版 qmind-cli(项目内, 不依赖宿主机 npm/缓存) ──────────
if [ -f "$ENTRY" ]; then
    echo "[qmind] 已缓存: $ENTRY"
else
    echo "[qmind] 缓存 @qoder-ai/qmind-cli → runtime/qmind-cli/"
    # --no-bin-links: 项目运行目录不支持 symlink 时也能装;
    # --cache: npm 缓存收敛到 runtime/.npm-cache, 不碰宿主机 ~/.npm。
    "$NODE_DIR/bin/npm" install --prefix "$QMIND_CLI_DIR" \
        --no-bin-links --no-audit --no-fund --save=false \
        --cache "$RUNTIME_DIR/.npm-cache" \
        @qoder-ai/qmind-cli
fi

# ── 3. 验证 ─────────────────────────────────────────────────────────
mkdir -p "$RUNTIME_DIR/qmind/home"
if [ -f "$ENTRY" ]; then
    echo "[qmind] 完成: $ENTRY"
    echo "[qmind] 验证: $NODE_DIR/bin/node $ENTRY --help"
else
    echo "[qmind] 安装失败: $ENTRY 不存在"
    exit 1
fi
