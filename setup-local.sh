#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# setup-local.sh — AI-DataHub 本地运行环境一键准备
#
# 做四件事: 环境检查 -> Python venv+全部服务依赖 -> 前端 npm 依赖
#           -> 配置文件占位(数据源凭证不在此脚本管理, 需自行填写)
#
# 用法:
#   ./setup-local.sh                 完整安装(幂等, 可重复执行)
#   ./setup-local.sh --check-only    仅检查环境, 不安装任何东西
#   ./setup-local.sh --skip-frontend 只装 Python 依赖
#   ./setup-local.sh --force         前端强制重装(node_modules 推倒重来)
#   ./setup-local.sh --with-engine   额外编译 Rust dataengine(需 cargo, 耗时)
#   ./setup-local.sh --start         安装完成后启动全部服务(= ./start-all.sh -d)
# ═══════════════════════════════════════════════════════════════

set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "\n${BLUE}━━━ $1 ━━━${NC}"; }

SKIP_FRONTEND=0; FORCE=0; WITH_ENGINE=0; START=0; CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --check-only)    CHECK_ONLY=1 ;;
        --skip-frontend) SKIP_FRONTEND=1 ;;
        --force)         FORCE=1 ;;
        --with-engine)   WITH_ENGINE=1 ;;
        --start)         START=1 ;;
        -h|--help)       sed -n '2,16p' "$0"; exit 0 ;;
        *) log_error "未知参数: $arg (见 --help)"; exit 1 ;;
    esac
done

FAILURES=0

# pip 镜像: 国内直连 pypi 极慢, 默认用阿里云源; 外网环境可用环境变量覆盖:
#   PIP_MIRROR=https://pypi.org/simple ./setup-local.sh
PIP_MIRROR="${PIP_MIRROR:-https://mirrors.aliyun.com/pypi/simple}"
PIP_ARGS=(-i "$PIP_MIRROR")

# ═══════════════ 1. 环境检查 ═══════════════
log_step "1/5 环境检查"

PY_BIN=""
for cand in python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        ver=$("$cand" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null)
        maj=${ver%%.*}; min=${ver#*.}
        if [ "$maj" = "3" ] && [ "$min" -ge 10 ] 2>/dev/null; then PY_BIN="$cand"; break; fi
    fi
done
if [ -n "$PY_BIN" ]; then log_info "Python: $($PY_BIN --version 2>&1) (>=3.10 ✓)"
else log_error "未找到 Python >= 3.10 (微服务无法运行)"; FAILURES=$((FAILURES+1)); fi

if command -v node >/dev/null 2>&1; then
    NODE_MAJ=$(node -v | sed 's/v//' | cut -d. -f1)
    if [ "$NODE_MAJ" -ge 18 ] 2>/dev/null; then log_info "Node: $(node -v) (>=18 ✓)"
    else log_error "Node $(node -v) 过低, Vite6 需要 >=18"; FAILURES=$((FAILURES+1)); fi
    command -v npm >/dev/null 2>&1 && log_info "npm: $(npm -v)" || { log_error "缺少 npm"; FAILURES=$((FAILURES+1)); }
else log_error "未找到 Node.js (前端无法运行)"; FAILURES=$((FAILURES+1)); fi

command -v git >/dev/null 2>&1 && log_info "git: 可用" || log_warn "git 不可用(拉依赖不受影响)"

if [ $WITH_ENGINE -eq 1 ]; then
    command -v cargo >/dev/null 2>&1 && log_info "cargo: $(cargo --version)" \
        || { log_error "--with-engine 需要 Rust 工具链 cargo"; FAILURES=$((FAILURES+1)); }
fi

if [ $CHECK_ONLY -eq 1 ]; then
    [ $FAILURES -eq 0 ] && log_info "环境检查通过" || log_error "存在 $FAILURES 项问题"
    exit $FAILURES
fi

# ═══════════════ 2. Python venv + 依赖 ═══════════════
log_step "2/5 Python venv 与服务依赖"

if [ ! -x "venv/bin/pip" ]; then
    if [ -z "$PY_BIN" ]; then log_error "无可用 Python, 跳过 venv 创建"; FAILURES=$((FAILURES+1));
    else log_info "创建 venv ($PY_BIN -m venv venv)..."; "$PY_BIN" -m venv venv || { log_error "venv 创建失败"; FAILURES=$((FAILURES+1)); }; fi
else log_info "venv 已存在: venv/bin/python $(venv/bin/python --version 2>&1 | awk '{print $2}')"; fi

PIP="venv/bin/pip"
if [ -x "$PIP" ]; then
    $PIP install "${PIP_ARGS[@]}" -q -U pip setuptools wheel || log_warn "pip 基础工具链升级失败(继续)"
    # 本地非容器模式下所有服务共用一个 venv: 先装 shared, 再逐服务安装
    # (清单与 services/*/requirements.txt 对齐; datagov/dataviz 复用 shared 依赖)
    REQ_FILES=(
        services/shared/requirements.txt
        services/shared/common/requirements.txt
        services/authservice/requirements.txt
        services/datacatalog/requirements.txt
        services/datagov/requirements.txt
        services/datamind/requirements.txt
        services/dataflow/requirements.txt
        services/dataviz/requirements.txt
        services/aiplatform/requirements.txt
        services/graphservice/requirements.txt
        services/semanticservice/requirements.txt
    )
    for req in "${REQ_FILES[@]}"; do
        if [ -f "$req" ]; then
            log_info "pip install -r $req"
            $PIP install "${PIP_ARGS[@]}" -q -r "$req" || { log_error "依赖安装失败: $req"; FAILURES=$((FAILURES+1)); }
        else
            log_warn "跳过(不存在): $req"
        fi
    done
else
    log_error "venv 不可用, Python 依赖未安装"
fi

# ═══════════════ 3. 前端依赖 ═══════════════
log_step "3/5 前端依赖 (frontend + sdk)"

install_npm() {
    local dir="$1" label="$2"
    [ $SKIP_FRONTEND -eq 1 ] && { log_warn "跳过 $label (--skip-frontend)"; return 0; }
    if [ ! -f "$dir/package.json" ]; then log_warn "$dir 不存在, 跳过"; return 0; fi
    (
        cd "$dir" || exit 1
        if [ $FORCE -eq 1 ] && [ -d node_modules ]; then
            log_info "$label: --force 清空 node_modules"
            rm -rf node_modules
        fi
        if [ -d node_modules ] && [ $FORCE -eq 0 ]; then
            log_info "$label: node_modules 已存在, 跳过 (需重装请加 --force)"
            return 0
        fi
        if [ -f package-lock.json ]; then
            log_info "$label: npm ci (按 lockfile 精确安装)"
            npm ci --no-audit --no-fund || exit 1
        else
            log_info "$label: npm install"
            npm install --no-audit --no-fund || exit 1
        fi
    ) && log_info "$label 完成" || { log_error "$label 安装失败"; FAILURES=$((FAILURES+1)); }
}
install_npm frontend "前端 frontend"
install_npm frontend/sdk "前端 sdk"

# ═══════════════ 4. 配置文件占位 ═══════════════
log_step "4/5 配置文件 (数据源/密钥请自行填写, 本脚本不代管)"

mkdir -p logs pids

if [ ! -f .env ] && [ -f .env.example ]; then
    cp .env.example .env && log_info ".env: 已从 .env.example 创建(容器编排用)"
else log_info ".env: 已存在或无需创建"; fi

if [ ! -f services/.env ]; then
    cat > services/.env <<'TPL'
# ═══ 微服务运行时配置 (各服务 start.sh 会 source 本文件) ═══
# 注意: 数据源/密钥为占位值, 必须自行填写后服务才能连库与调用 LLM。

# --- 元数据库 (MySQL, 必填) ---
METADATA_DB_HOST=
METADATA_DB_PORT=3306
METADATA_DB_USER=
METADATA_DB_PASSWORD=
METADATA_DB_DATABASE=adh2

# --- 平台密钥 (必填: 生成一个随机长字符串) ---
ADH_SECRET_KEY=

# --- Qoder 执行层 / QMind 知识库检索 (必填 PAT: pt- 开头) ---
QODER_PERSONAL_ACCESS_TOKEN=
QODER_MODEL=
QODER_CLI_PATH=
QODER_RUNTIME_CWD=

# --- LLM (Anthropic / 兼容网关) ---
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=
ANTHROPIC_MODEL=
TPL
    log_warn "services/.env: 已生成占位模板 —— 数据源与密钥需自行填写(服务连库依赖它)"
else
    log_info "services/.env: 已存在, 不覆盖"
fi

# ═══════════════ 5. 可选: Rust dataengine ═══════════════
log_step "5/5 可选组件"

if [ $WITH_ENGINE -eq 1 ] && command -v cargo >/dev/null 2>&1; then
    log_info "编译 dataengine (cargo build --release, 首次较慢)..."
    (cd services/dataengine && cargo build --release) && log_info "dataengine 编译完成" \
        || { log_error "dataengine 编译失败"; FAILURES=$((FAILURES+1)); }
else
    log_info "跳过 dataengine 编译 (需要时加 --with-engine)"
fi

# ═══════════════ 汇总 ═══════════════
echo ""
if [ $FAILURES -eq 0 ]; then
    log_info "环境准备完成 ✔"
else
    log_error "完成, 但有 $FAILURES 项失败(见上方)"
fi
echo -e "${BLUE}端口约定:${NC} frontend 3000 | datamind 8001 | datagov 8002 | dataflow 8003 \
| dataviz 8004 | datacatalog 8005 | authservice 8006 | aiplatform 8007 | graphservice 8011 \
| semanticservice 8012 | dataengine 8082"
echo -e "${BLUE}下一步:${NC}"
echo "  1) 填写 services/.env 的数据源与密钥"
echo "  2) ./start-all.sh -d      启动全部后端   |   cd frontend && npm run dev   启动前端"
echo "  3) 端口被残留进程占用时: ./kill-ports.sh"

if [ $START -eq 1 ] && [ $FAILURES -eq 0 ]; then
    log_info "--start: 启动全部服务..."
    exec ./start-all.sh -d
fi
exit $FAILURES
