"""Unified Configuration for all microservices and the backend.

Merges ADH_*, METADATA_DB_*, Oxigraph, service ports, and
backward-compatible legacy aliases into a single source of truth.

New configuration layer with backward-compatible fallbacks.
Old CHATBI_* / DORIS_* variables still work but are deprecated.

Usage:
    from backend.common.config import (
        ADH_SECRET_KEY, ADH_DEFAULT_ADMIN_PASSWORD,
        METADATA_DB_HOST, METADATA_DB_PORT, METADATA_DB_USER, METADATA_DB_PASSWORD, METADATA_DB_DATABASE,
        OXIGRAPH_URL,
        WEB_PORT, SEMANTIC_SERVICE_URL,
    )
"""

import logging
import os
from pathlib import Path

# Load .env —— 唯一来源 backend/.env（Phase 7：旧 services/.env 已并入）
# load_dotenv 之前留存真实进程环境：需要区分“用户显式 export”与“backend/.env 注入”，
# 否则下方 FRONTEND_PORT 会把 backend/.env 里的残留同名键当成环境变量优先项，
# 与 frontend_port.sh / vite（只认根 .env）的口径分叉。
_PRELOAD_ENV = dict(os.environ)


def _manual_load_env(path: Path) -> None:
    """无 python-dotenv 时的兜底解析:KEY=VALUE(忽略注释/空行,去包裹引号,不覆盖已有环境变量)."""
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)
    except OSError:
        pass


try:
    from dotenv import load_dotenv
    _backend_env = Path(__file__).resolve().parent.parent / ".env"
    if _backend_env.exists():
        load_dotenv(_backend_env, override=True)
except ImportError:
    # python-dotenv 未安装:手动解析,避免静默回退到 localhost
    _backend_env = Path(__file__).resolve().parent.parent / ".env"
    if _backend_env.exists():
        _manual_load_env(_backend_env)

# ══════════════════════════════════════════════════════════════════════════
# Metadata Database (MySQL) — stores table/column/term metadata
# ══════════════════════════════════════════════════════════════════════════

METADATA_DB_TYPE = os.getenv("METADATA_DB_TYPE", "mysql")
METADATA_DB_HOST = os.getenv("METADATA_DB_HOST", os.getenv("DORIS_HOST", "localhost"))
METADATA_DB_PORT = int(os.getenv("METADATA_DB_PORT", os.getenv("DORIS_PORT", "9030")))
METADATA_DB_USER = os.getenv("METADATA_DB_USER", os.getenv("DORIS_USER", "root"))
METADATA_DB_PASSWORD = os.getenv("METADATA_DB_PASSWORD", os.getenv("DORIS_PASSWORD", ""))
METADATA_DB_DATABASE = os.getenv("METADATA_DB_DATABASE", "adh")

# ══════════════════════════════════════════════════════════════════════════
# Redis — Celery Broker + Result Backend + Distributed Lock
# ══════════════════════════════════════════════════════════════════════════

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ══════════════════════════════════════════════════════════════════════════
# Application (ADH_* replaces CHATBI_*)
# ══════════════════════════════════════════════════════════════════════════

ADH_SECRET_KEY = os.getenv("ADH_SECRET_KEY", os.getenv("CHATBI_SECRET_KEY", ""))
ADH_DEFAULT_ADMIN_PASSWORD = os.getenv("ADH_DEFAULT_ADMIN_PASSWORD", "")
# 严格门禁：置 1 时，占位/空 ADH_SECRET_KEY 直接拒绝启动（fail-closed）。
# 默认不阻断：现网存量密文是用占位值加出来的，硬拦会把服务打死；密钥轮换需先重加密
# 存量行，那是一步人的决策（告警常驻 /health 与启动日志，不静默）。
ADH_SECRET_KEY_STRICT = os.getenv("ADH_SECRET_KEY_STRICT", "").strip().lower() in ("1", "true", "yes", "on")

# ── 对象存储 (S3 兼容: MinIO / AWS S3 / 阿里云 OSS 等) ──────────
# 托管两类对象, 支持分布式多实例共享:
#   1) 归档资产(用户资产清单, 资产跟随用户跨工作空间);
#   2) 云空间会话产物镜像(ADH_WORKSPACES_DIR 是工作副本, 对象存储是可靠副本,
#      见 backend/modules/mind/execution/session_cloud.py —— 换节点/清卷后按需回落)
OBJECT_STORAGE_ENDPOINT = os.getenv("OBJECT_STORAGE_ENDPOINT", "")  # 如 http://minio:9000
OBJECT_STORAGE_ACCESS_KEY = os.getenv("OBJECT_STORAGE_ACCESS_KEY", "")
OBJECT_STORAGE_SECRET_KEY = os.getenv("OBJECT_STORAGE_SECRET_KEY", "")
OBJECT_STORAGE_BUCKET = os.getenv("OBJECT_STORAGE_BUCKET", "adh-attachments")
OBJECT_STORAGE_REGION = os.getenv("OBJECT_STORAGE_REGION", "")
# 对象 key 统一前缀(资产布局: {prefix}/{user_id}/assets/..., 见 object_storage.build_asset_key)
OBJECT_STORAGE_PREFIX = os.getenv("OBJECT_STORAGE_PREFIX", "ai-datahub").strip("/") or "ai-datahub"
# 未配置 endpoint 时回退本地磁盘(开发环境兼容)
OBJECT_STORAGE_ENABLED = bool(OBJECT_STORAGE_ENDPOINT and OBJECT_STORAGE_ACCESS_KEY)
# 本地回退目录(仅 OBJECT_STORAGE_ENABLED=False 时生效)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# 项目根锚点(仓库根): 供执行层等把相对路径配置解析为绝对路径,
# 使配置随代码库迁移/容器部署保持有效(不写死机器绝对路径)。
PROJECT_ROOT = _PROJECT_ROOT
ADH_OBJECT_FALLBACK_DIR = os.getenv("ADH_OBJECT_FALLBACK_DIR", str(_PROJECT_ROOT / "data" / "object_storage"))

# ── Agent 工作空间沙箱根目录(Docker 部署时挂载 named volume) ─────
# 目录布局: {ADH_WORKSPACES_DIR}/{user_id}/workspace/{workspace_id}/sessions/{key}/...
ADH_WORKSPACES_DIR = os.getenv("ADH_WORKSPACES_DIR", str(_PROJECT_ROOT / "data" / "user"))

# ── 工作空间配额缺省（无登记行时的生效值）──────────────────────────────
# 唯一取数点是 `core/role_service.get_user_workspace_quota`，各读路径不得再各写一份数字。
# 历史缺陷：`adh_user_workspace_quota` 无行时「建空间」按 5 个/5GB 缺省，而「新建会话」的
# 磁盘门把空读成 0=不限制 —— 管理员页显示“每空间 5GB”却从不拦（配了没用的双口径）。
# 显式登记行仍是权威；行里 disk_quota_bytes=0 保留为管理员“该用户不设限”的逃生口
# （有行 ≠ 缺省，所以缺省改动不会覆盖既有配置，也不给批量插默认行——那会把缺省伪装成配置）。
WORKSPACE_QUOTA_DEFAULT_MAX_WORKSPACES = int(os.getenv("WORKSPACE_QUOTA_DEFAULT_MAX_WORKSPACES", "5") or 0)
# 每工作空间磁盘配额缺省（字节）：500MB 量级依据 2026-11 存储审计，
# 单会话稳态占用 ~1.7MB（其中 99% 是执行层 CLI 过程数据），500MB 够数千会话。
WORKSPACE_DISK_QUOTA_DEFAULT_BYTES = int(os.getenv(
    "WORKSPACE_DISK_QUOTA_DEFAULT_BYTES", str(500 * 1024 ** 2)) or 0)

# ══════════════════════════════════════════════════════════════════════════
# 会话存储 —— 拆行入库投影与保留策略
# ══════════════════════════════════════════════════════════════════════════
# 投影上限：哪些「未经展示治理的中间产物」以多大尺度落库（0 = 该类完全不落）。
# 实现处（唯一口径）：backend/core/conversation_store.py。
CONV_TOOL_RESULT_MAX_CHARS = int(os.getenv("CONV_TOOL_RESULT_MAX_CHARS", "4000") or 0)   # tool_calls[].result
CONV_THINKING_MAX_CHARS = int(os.getenv("CONV_THINKING_MAX_CHARS", "4000") or 0)          # 模型思考链
CONV_PROCESS_STEP_MAX_CHARS = int(os.getenv("CONV_PROCESS_STEP_MAX_CHARS", "2000") or 0)  # 执行过程单步
CONV_PROCESS_MAX_STEPS = int(os.getenv("CONV_PROCESS_MAX_STEPS", "120") or 0)
CONV_RAW_RESPONSE_MAX_CHARS = int(os.getenv("CONV_RAW_RESPONSE_MAX_CHARS", "4000") or 0)  # 与正文同值时直接不落
CONV_PROGRESS_STAGE_MAX = int(os.getenv("CONV_PROGRESS_STAGE_MAX", "60") or 0)

# 保留策略（内置任务 conversation_history_retention，每天凌晨执行）：
#   - 观测流水（adh_llm_spans / adh_llm_traces）超期滚动删除；
#   - 长期未活跃会话只剥「明细大字段」（工具返回体/思考链/过程步骤），**不删正文**：
#     删用户资产是产品决策，不是为回收容量而擅自做的工程决策。
RETENTION_LLM_SPAN_DAYS = int(os.getenv("RETENTION_LLM_SPAN_DAYS", "90") or 90)
RETENTION_CONVERSATION_TRIM_DAYS = int(os.getenv("RETENTION_CONVERSATION_TRIM_DAYS", "180") or 180)
# 剥明细时保留的结果行数（报表渲染只取前 200 行，与 render_conversation_report 同口径）
RETENTION_RESULT_ROWS_KEEP = int(os.getenv("RETENTION_RESULT_ROWS_KEEP", "200") or 0)

# ══════════════════════════════════════════════════════════════════════════
# Observability — LLM 交互可观测(O0: credit/token 用量落库, MySQL)
# ══════════════════════════════════════════════════════════════════════════

OBSERVABILITY_ENABLED = os.getenv("OBSERVABILITY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
OBSERVABILITY_SPAN_MAX_CHARS = int(os.getenv("OBSERVABILITY_SPAN_MAX_CHARS", "4096"))
# credit → 成本折算单价（仅当 CLI 未上报 total_cost_usd 时用于“成本”列，单位见下方币种）。
# 默认 0.04 CNY = Qoder CN 企业资源包官方起购价 80 元 / 2,000 Credits
# （源：docs.qoder.cn/product-overview/billing-description；国际版对照 $0.02/Credit）。
# 填 0 = 不折算，成本列保持 NULL 并在前端显示 —（不臆造价格）。
OBSERVABILITY_CREDIT_COST_RATE = float(os.getenv("OBSERVABILITY_CREDIT_COST_RATE", "0.04") or 0)
OBSERVABILITY_CREDIT_COST_CURRENCY = (os.getenv("OBSERVABILITY_CREDIT_COST_CURRENCY", "CNY") or "CNY").strip().upper()

# ══════════════════════════════════════════════════════════════════════════
# Qoder 执行层默认 —— AS-BOT 未绑定模型时的兜底默认
# ══════════════════════════════════════════════════════════════════════════

# 名称对齐 qodercli 自身的环境变量约定(README 中 QODER_MODEL ↔ -m/--model)；
# secure_sdk.build_options 会剥除子进程 env，本项仅作为本侧读取入口，不直接
# 依赖子进程继承。优先级：前端选择/AS-BOT.models[0] > 执行层 adapter.model >
# QODER_MODEL > SDK 默认(Auto)。支持 `{provider}/{model}` 形式(见 llm_resources)。
QODER_MODEL = os.getenv("QODER_MODEL", "").strip()

# ════════════════════════════════════════════════════════════════════════
# 知识库外部通道（百炼 / DashScope）—— 凭据只从环境读，不得写入 source_config
# ════════════════════════════════════════════════════════════════════════
# 与 QMIND_TOKEN 同一条纪律：密钥不落元数据库、不进 LLM 视野、不回显给前端。
# `adh_knowledge_bases.source_config` 只允许出现非密项（workspace_id / index_id / top_k）
# 或凭据的**引用名** `credential_ref`（指向下面这类环境变量的名字）。
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
BAILIAN_ENDPOINT = (os.getenv("BAILIAN_ENDPOINT",
                              "https://dashscope.aliyuncs.com").strip().rstrip("/"))
# 官方文档明示检索接口响应可能较慢、建议自行设超时与重试；接口幂等，故只补一次。
BAILIAN_TIMEOUT = float(os.getenv("BAILIAN_TIMEOUT", "10") or 10)

# ════════════════════════════════════════════════════════════════════════
# 前置意图漏斗（Intent Gate）—— chat 输入进执行层之前的确定性分流
# ════════════════════════════════════════════════════════════════════════
# off    = 不判定（完全沿用旧行为）
# shadow = 只判定与度量，行为不变（所有输入仍下发 agent）—— 缺省值
# enforce= 按判定本地直答（寒暄/无效输入/能力询问/使用说明/涉密索要）
# 切 enforce 是由人根据 shadow 数据（拦截率/误拦复盘）做的决策，不由代码默认放行。
INTENT_GATE_MODE = (os.getenv("INTENT_GATE_MODE", "shadow") or "shadow").strip().lower()
# 业务实体词典（指标/维度/对象别名/术语/触发词）的缓存秒数。
# 取长了不危险：分流只看"命中的是一个业务词"，命中不到就多花一个回合、不会答错；
# 过期后先给旧值、后台刷新（stale-while-revalidate），用户不会为装载等待。
INTENT_GATE_LEXICON_TTL_SECONDS = float(os.getenv("INTENT_GATE_LEXICON_TTL_SECONDS", "600") or 600)
# 判定延迟预算（毫秒）：超预算即放行 agent 并把 gate_error 写进观测/落库（不静默）。
# 热路径只读内存/Redis 快照（目标 <30ms）；词典首次装载走后台线程，不占这个预算。
INTENT_GATE_MAX_LATENCY_MS = float(os.getenv("INTENT_GATE_MAX_LATENCY_MS", "200") or 200)
# 判定流水保留天数（内置任务 intent_intercept_retention 滚动清理；已人工复盘的行不删）。
INTENT_GATE_INTERCEPT_RETENTION_DAYS = int(os.getenv("INTENT_GATE_INTERCEPT_RETENTION_DAYS", "90") or 90)

# ══════════════════════════════════════════════════════════════════════════
# Oxigraph — RDF Triple Store (SPARQL 1.1)
# ══════════════════════════════════════════════════════════════════════════

OXIGRAPH_URL = os.getenv("OXIGRAPH_URL", "http://localhost:7878")

# ══════════════════════════════════════════════════════════════════════════
# Web 监听口 —— Phase 4 终态：后端只有一个 web 进程、一个端口
# ══════════════════════════════════════════════════════════════════════════
#
# 与 backend/scripts/services.conf（唯一权威登记）/ vite 代理 / nginx 上游
# backend:8000 / Dockerfile EXPOSE / monitoring 探测同源。旧 8 个契约端口
# （8001-8007/8012）与 SERVICE_PORTS / MCP_PORTS 映射随进程壳退役（无消费方）。
# 口径不一致会直接表现为前端 ECONNREFUSED，tests/test_merged_webapp.py 有护栏用例。

WEB_PORT = int(os.getenv("ADH_WEB_PORT", "8000"))

# ══════════════════════════════════════════════════════════════════════════
# 前端监听口 —— 口径唯一属于根目录 .env 的 FRONTEND_PORT（本处不再抄一份 3000）
# ══════════════════════════════════════════════════════════════════════════
#
# 解析优先级与 backend/scripts/frontend_port.sh 完全一致：环境变量 > 根 .env
# （同名多次赋值取最后一次）> 缺省 3000；backend/.env 的同名残留只当兜底（它不是
# 本键的配置源，否则会与只认根 .env 的脚本/vite 分叉）。该键既有消费方是
# start-all/stop-all/kill-ports（经 frontend_port.sh）、frontend/vite.config.ts（loadEnv
# 同键）、docker-compose(.full).yml 宿主映射；后端侧唯一需要前端端口的地方是系统监控的
# 探测登记表（backend/modules/auth/api/monitoring.py）。历史上那里写死 3000，
# 改端口后监控把正常在跑的前端报成 “Connection refused”——假故障，故必须同源读取。
# 护栏用例：tests/test_merged_webapp.py::test_frontend_port_single_source_of_truth

FRONTEND_PORT_DEFAULT = 3000


def _read_root_env(key: str) -> str:
    """读根目录 .env 的单个键（取最后一次赋值，去引号与首尾空白）；无文件返回空。

    只取指定键、不整体灌入 os.environ —— 根 .env 其余项属部署侧本地覆盖，
    合并进来会改变 backend/.env（本模块唯一的 .env 来源）的既有优先级口径。
    """
    try:
        lines = (_PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    value = ""
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() != key:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        value = v
    return value


def _resolve_frontend_port() -> int:
    # 优先级：真实进程环境变量（显式 export / 单次调试覆盖，取 load_dotenv 前的快照）
    # > 根 .env（本键唯一配置源）> backend/.env 残留（仅容器部署拿不到根 .env 时兜底）> 缺省
    raw = (_PRELOAD_ENV.get("FRONTEND_PORT") or "").strip()
    if not raw:
        raw = _read_root_env("FRONTEND_PORT")
    if not raw:
        raw = (os.getenv("FRONTEND_PORT") or "").strip()
    if not raw:
        return FRONTEND_PORT_DEFAULT
    try:
        port = int(raw)
    except ValueError:
        port = -1
    if not 1 <= port <= 65535:
        # 与 frontend_port.sh 同口径：回退缺省但显式告警，不静默当成“没配”
        # （告警必须让人能定位到根 .env，否则监控页的假故障无从解释）
        logging.getLogger(__name__).warning(
            "FRONTEND_PORT=%r 不是有效端口，回退 %s：系统监控的前端探测口将与实际监听口分叉，"
            "请修正根目录 .env 的 FRONTEND_PORT", raw, FRONTEND_PORT_DEFAULT)
        return FRONTEND_PORT_DEFAULT
    return port


def frontend_port() -> int:
    """现取前端监听口（每次重读根 .env）。

    给“运行中才需要知道端口”的消费方用（系统监控探测、报告链接拼接）：
    改完根 .env 的 FRONTEND_PORT 只需重启前端，刷新监控页即生效，不必重启 web。
    若在导入时冻结成常量，改端口后监控会把已改好的口继续报成 Connection refused。
    """
    return _resolve_frontend_port()


FRONTEND_PORT = _resolve_frontend_port()

# ══════════════════════════════════════════════════════════════════════════
# Backward Compatibility Aliases (deprecated — use ADH_* / METADATA_DB_*)
# ══════════════════════════════════════════════════════════════════════════

# Legacy Doris config — reads from environment with fallback
DORIS_HOST = os.getenv("DORIS_HOST", "localhost")
DORIS_PORT = int(os.getenv("DORIS_PORT", "9030"))
DORIS_USER = os.getenv("DORIS_USER", "root")
DORIS_PASSWORD = os.getenv("DORIS_PASSWORD", "")
DORIS_DATABASE = os.getenv("DORIS_DATABASE", "alliedstar")

# Legacy app config — kept for backward compatibility
# ADH_SECRET_KEY and ADH_DEFAULT_ADMIN_PASSWORD are defined above

# ══════════════════════════════════════════════════════════════════════════
# DataEngine — Rust SQL 语义引擎 (MDL/RLS/方言转译)
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
# SemanticLayer — 语义层声明式查询入口 (ChatBI/大屏同源)
# ══════════════════════════════════════════════════════════════════════════
# semhub router 已拼进 web 单进程，故默认指向本进程监听口（旧 :8012 独立服务已退役）。

SEMANTIC_SERVICE_URL = os.getenv("SEMANTIC_SERVICE_URL", f"http://localhost:{WEB_PORT}")
SEMANTIC_TIMEOUT = int(os.getenv("SEMANTIC_TIMEOUT", "60"))

# ══════════════════════════════════════════════════════════════════════════
# Elasticsearch 内置 MCP 能力（平台内置服务，非数据源）
# ══════════════════════════════════════════════════════════════════════════
#
# 定位决策（2026-11-04）：ES 作为**平台内置 MCP 服务**（adh_mcp_servers 的 is_builtin=1
# 行，在 AS-BOT「引用 MCP 服务」里绑定并逐工具授权），而不是登记成数据源。理由：
#   - 不进治理域 ⇒ 不消费 RLS/列级策略（ES SQL 不支持子查询包裹、sqlglot 无 ES
#     方言，编译期改写手法在 ES 上不成立）；数据面唯一闸门是下面的索引模式白名单。
#   - 前提是「范围内数据已脱敏」——属产品决策，不在代码层验证；若接入未脱敏索引
#     需重新评估（见 tests/test_es_builtin_mcp.py 的口径用例）。
# 凭据与访问范围**只走 .env**（本模块是唯一读取口）：内置行只存能力登记，不存任何
# 连接字段，地址/凭据/模式名永不出现在 API 响应或 UI（安全护栏 §7）。

ES_MCP_ENABLED = os.getenv("ES_MCP_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
ES_URL = os.getenv("ES_URL", "").strip()
ES_USERNAME = os.getenv("ES_USERNAME", "").strip()
ES_PASSWORD = os.getenv("ES_PASSWORD", "")
ES_API_KEY = os.getenv("ES_API_KEY", "").strip()
# 自签证书集群需显式关闭；缺省校验（不因“连不上”就悄悄关掉 TLS 验证）。
ES_VERIFY_CERTS = os.getenv("ES_VERIFY_CERTS", "true").strip().lower() not in ("0", "false", "no", "off")
ES_TIMEOUT_SEC = int(os.getenv("ES_TIMEOUT_SEC", "20"))
ES_MAX_ROWS = int(os.getenv("ES_MAX_ROWS", "1000"))
# 可查索引模式白名单（逗号分隔，每项就是一个可查范围，同 Kibana data view）。查询直接
# 拿模式作 FROM 目标（如 FROM "logs-*"），由 ES 跨时间分片合并，**不向集群展开**。
# **空 = 不开放任何范围**（fail-closed，与“空角色授权不等于全量”同口径）；
# 写法必须整体落在某个已登记模式内（带 * 收窄可以，丢掉模式里的字面字符会被当扩权拒）。
ES_ALLOWED_INDEXES = tuple(
    x.strip() for x in os.getenv("ES_ALLOWED_INDEXES", "").split(",") if x.strip()
)


