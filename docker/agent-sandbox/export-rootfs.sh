#!/usr/bin/env bash
# 把 python_runtime 沙箱镜像导出成 rootfs 目录，供本地 bwrap 会话沙箱（SessionToolSandbox
# 的 bwrap 后端）以 --ro-bind 挂载。bwrap 无镜像概念，需要一份落地的 rootfs 文件系统树；
# 复用与 Docker 后端逐字节一致的镜像（docker/agent-sandbox/Dockerfile 产出），
# 保证"预装可复现 Python 环境"这条硬性要求在 bwrap 下同样成立。
#
# 用法: bash docker/agent-sandbox/export-rootfs.sh [目标目录]
#   目标目录默认 /opt/adh-python-runtime（与"系统管理-沙箱管理"里 bwrap 的 rootfs_dir 默认值一致）
#   也可用 ADH_SANDBOX_BWRAP_ROOTFS 指定；配置默认 bwrap 沙箱时应与 rootfs_dir 指向同一路径。
#
# 依赖: 本机 Docker（仅构建期用，运行期 bwrap 不再需要 Docker）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

IMAGE="${ADH_AGENT_SANDBOX_IMAGE:-adh-python-runtime:1}"
DEST="${1:-${ADH_SANDBOX_BWRAP_ROOTFS:-/opt/adh-python-runtime}}"

# 镜像不存在则先构建（与 build.sh 一致）
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[rootfs] 镜像 $IMAGE 不存在，先执行 build.sh 构建"
  bash docker/agent-sandbox/build.sh
fi

echo "[rootfs] 导出 $IMAGE → $DEST"
mkdir -p "$DEST"
cid="$(docker create --label adh.rootfs.source="$IMAGE" "$IMAGE")"
# --no-same-owner：以当前用户落盘，避免 tar 保留镜像内 uid 造成宿主权限混乱
# 清空目标内容（幂等：重跑覆盖），保留目录本身
find "$DEST" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
docker export "$cid" | tar -x --no-same-owner -C "$DEST"
docker rm -f "$cid" >/dev/null

# 把仓库当前 worker 烘进 rootfs：bwrap 以 --unshare-user 运行时无权 bind-mount 宿主文件，
# 故 worker 必须来自 rootfs 内部；每次导出都覆盖为最新源码（含 rlimit 逻辑）。
mkdir -p "$DEST/opt/agent"
cp "$ROOT/backend/modules/mind/execution/sandbox_worker.py" "$DEST/opt/agent/sandbox_worker.py"

# 校验：worker 入口与 python 可执行均在 rootfs 内
if [ ! -f "$DEST/opt/agent/sandbox_worker.py" ]; then
  echo "[rootfs] 错误：worker 入口未就位" >&2; exit 1
fi
if ! ls "$DEST"/usr/local/bin/python* >/dev/null 2>&1 && [ ! -x "$DEST/usr/bin/python3" ]; then
  echo "[rootfs] 错误：rootfs 内未找到 python 解释器" >&2; exit 1
fi

echo "[rootfs] 完成: $DEST"
echo "[rootfs] 冒烟: bwrap --unshare-all --ro-bind $DEST / --proc /proc --dev /dev --tmpfs /tmp /usr/local/bin/python -I -c 'import pandas,numpy;print(pandas.__version__)'"
