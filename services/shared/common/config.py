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
try:
    from dotenv import load_dotenv
    _services_env = Path(__file__).resolve().parent.parent.parent / ".env"
    _backend_env = Path(__file__).resolve().parent.parent.parent.parent / "backend" / ".env"
    if _services_env.exists():
        load_dotenv(_services_env, override=True)
    elif _backend_env.exists():
        load_dotenv(_backend_env, override=True)
except ImportError:
    pass

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

# Backward compatibility aliases (deprecated)
GATEWAY_URL = ENGINE_SERVER_URL
GATEWAY_TIMEOUT = ENGINE_TIMEOUT
GATEWAY_ENABLED = ENGINE_ENABLED


