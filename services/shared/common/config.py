"""Unified Configuration for all microservices and the backend.

Merges ADH_*, METADATA_DB_*, Oxigraph, service ports, and
backward-compatible legacy aliases into a single source of truth.

New configuration layer with backward-compatible fallbacks.
Old CHATBI_* / DORIS_* variables still work but are deprecated.

Usage:
    from services.shared.common.config import (
        ADH_SECRET_KEY, ADH_DEFAULT_ADMIN_PASSWORD,
        METADATA_DB_HOST, METADATA_DB_PORT, METADATA_DB_USER, METADATA_DB_PASSWORD, METADATA_DB_DATABASE,
        OXIGRAPH_URL,
        SERVICE_PORTS, MCP_PORTS,
    )
"""

import os
from pathlib import Path

# Load .env — try services/.env first, then backend/.env as fallback
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
    _services_env = Path(__file__).resolve().parent.parent.parent / ".env"
    _backend_env = Path(__file__).resolve().parent.parent.parent.parent / "backend" / ".env"
    if _services_env.exists():
        load_dotenv(_services_env, override=True)
    elif _backend_env.exists():
        load_dotenv(_backend_env, override=True)
except ImportError:
    # python-dotenv 未安装:手动解析,避免静默回退到 localhost
    _services_env = Path(__file__).resolve().parent.parent.parent / ".env"
    _backend_env = Path(__file__).resolve().parent.parent.parent.parent / "backend" / ".env"
    if _services_env.exists():
        _manual_load_env(_services_env)
    elif _backend_env.exists():
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

# 聊天附件(多模态文件)存储根目录,默认项目根目录下 data/chat_attachments
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ADH_UPLOAD_DIR = os.getenv("ADH_UPLOAD_DIR", str(_PROJECT_ROOT / "data" / "chat_attachments"))

# ══════════════════════════════════════════════════════════════════════════
# LLM (Anthropic)
# ══════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

# ══════════════════════════════════════════════════════════════════════════
# Observability — LLM 交互可观测(O0: credit/token 用量落库, MySQL)
# ══════════════════════════════════════════════════════════════════════════

OBSERVABILITY_ENABLED = os.getenv("OBSERVABILITY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
OBSERVABILITY_SPAN_MAX_CHARS = int(os.getenv("OBSERVABILITY_SPAN_MAX_CHARS", "4096"))

# ══════════════════════════════════════════════════════════════════════════
# Oxigraph — RDF Triple Store (SPARQL 1.1)
# ══════════════════════════════════════════════════════════════════════════

OXIGRAPH_URL = os.getenv("OXIGRAPH_URL", "http://localhost:7878")

# ══════════════════════════════════════════════════════════════════════════
# Service & MCP Ports
# ══════════════════════════════════════════════════════════════════════════

SERVICE_PORTS = {
    "datamind": 8001,
    "datagov": 8002,
    "dataflow": 8003,
    "dataviz": 8004,
    "datacatalog": 8005,
    "authservice": 8006,
    "graphservice": 8011,
    "semanticservice": 8012,
}

MCP_PORTS = {
    "datamind": 31001,
    "datagov": 31002,
    "dataflow": 31003,
    "dataviz": 31004,
    "datacatalog": 31005,
    "authservice": 31006,
}

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

ENGINE_SERVER_URL = os.getenv("ENGINE_SERVER_URL", "http://localhost:8082")
ENGINE_TIMEOUT = int(os.getenv("ENGINE_TIMEOUT", "60"))
ENGINE_ENABLED = os.getenv("ENGINE_ENABLED", "true").lower() == "true"

# ══════════════════════════════════════════════════════════════════════════
# SemanticLayer — semanticservice (:8012) 声明式查询入口 (ChatBI/大屏同源)
# ══════════════════════════════════════════════════════════════════════════

SEMANTIC_SERVICE_URL = os.getenv("SEMANTIC_SERVICE_URL", "http://localhost:8012")
SEMANTIC_TIMEOUT = int(os.getenv("SEMANTIC_TIMEOUT", "60"))

# Backward compatibility aliases (deprecated)
GATEWAY_URL = ENGINE_SERVER_URL
GATEWAY_TIMEOUT = ENGINE_TIMEOUT
GATEWAY_ENABLED = ENGINE_ENABLED


