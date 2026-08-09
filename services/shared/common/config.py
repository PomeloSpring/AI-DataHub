"""Unified Configuration for all microservices and the backend.

Merges ADH_*, METADATA_DB_*, VECTOR_DB_*, Neo4j, service ports, and
backward-compatible legacy aliases into a single source of truth.

New configuration layer with backward-compatible fallbacks.
Old CHATBI_* / DORIS_* variables still work but are deprecated.

Usage:
    from services.shared.common.config import (
        ADH_SECRET_KEY, ADH_DEFAULT_ADMIN_PASSWORD,
        METADATA_DB_HOST, METADATA_DB_PORT, METADATA_DB_USER, METADATA_DB_PASSWORD, METADATA_DB_DATABASE,
        VECTOR_DB_HOST, VECTOR_DB_PORT, VECTOR_DB_USER, VECTOR_DB_PASSWORD, VECTOR_DB_DATABASE,
        VECTOR_DIM, VECTOR_DISTANCE,
        NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
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
# Vector Database (Doris) — stores embeddings for RAG retrieval
# ══════════════════════════════════════════════════════════════════════════

VECTOR_DB_TYPE = os.getenv("VECTOR_DB_TYPE", "default")
VECTOR_DB_HOST = os.getenv("VECTOR_DB_HOST", os.getenv("DORIS_HOST", "localhost"))
VECTOR_DB_PORT = int(os.getenv("VECTOR_DB_PORT", os.getenv("DORIS_PORT", "9030")))
VECTOR_DB_USER = os.getenv("VECTOR_DB_USER", os.getenv("DORIS_USER", "root"))
VECTOR_DB_PASSWORD = os.getenv("VECTOR_DB_PASSWORD", os.getenv("DORIS_PASSWORD", ""))
VECTOR_DB_DATABASE = os.getenv("VECTOR_DB_DATABASE", os.getenv("METADATA_DB_DATABASE", "adh"))

# Vector search parameters
VECTOR_DIM = int(os.getenv("VECTOR_DIM", os.getenv("EMBEDDING_DIM", "768")))
VECTOR_DISTANCE = os.getenv("VECTOR_DISTANCE", "l2")

# ══════════════════════════════════════════════════════════════════════════
# Redis — Celery Broker + Result Backend + Distributed Lock
# ══════════════════════════════════════════════════════════════════════════

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ══════════════════════════════════════════════════════════════════════════
# Langfuse — LLM Observability
# ══════════════════════════════════════════════════════════════════════════

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
LANGFUSE_ENABLED = bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)

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
# Embedding
# ══════════════════════════════════════════════════════════════════════════

EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "shibing624/text2vec-base-chinese")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
EMBEDDING_HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
EMBEDDING_MODEL_CACHE_DIR = os.getenv("EMBEDDING_MODEL_CACHE_DIR", "")

# ══════════════════════════════════════════════════════════════════════════
# Qdrant — Vector Database (optional, alternative to Doris vectors)
# ══════════════════════════════════════════════════════════════════════════

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_COLLECTION_PREFIX = os.getenv("QDRANT_COLLECTION_PREFIX", "")

# ══════════════════════════════════════════════════════════════════════════
# Neo4j — Graph Database
# ══════════════════════════════════════════════════════════════════════════

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

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
    "vectorservice": 8010,
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
# Backward Compatibility Aliases (deprecated — use ADH_* / METADATA_DB_* / VECTOR_DB_*)
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

# ══════════════════════════════════════════════════════════════════════════
# OpenLDAP — Unified Authentication
# ══════════════════════════════════════════════════════════════════════════

LDAP_ENABLED = os.getenv("LDAP_ENABLED", "false").lower() == "true"
LDAP_SERVER_URL = os.getenv("LDAP_SERVER_URL", "ldap://localhost:389")
LDAP_USE_SSL = os.getenv("LDAP_USE_SSL", "false").lower() == "true"
LDAP_STARTTLS = os.getenv("LDAP_STARTTLS", "false").lower() == "true"
LDAP_BASE_DN = os.getenv("LDAP_BASE_DN", "dc=example,dc=com")
LDAP_BIND_DN = os.getenv("LDAP_BIND_DN", "cn=admin,dc=example,dc=com")
LDAP_BIND_PASSWORD = os.getenv("LDAP_BIND_PASSWORD", "")
LDAP_USER_SEARCH_BASE = os.getenv("LDAP_USER_SEARCH_BASE", "ou=users")
LDAP_USER_SEARCH_FILTER = os.getenv("LDAP_USER_SEARCH_FILTER", "(uid={username})")
LDAP_GROUP_SEARCH_BASE = os.getenv("LDAP_GROUP_SEARCH_BASE", "ou=groups")
LDAP_GROUP_SEARCH_FILTER = os.getenv("LDAP_GROUP_SEARCH_FILTER", "(member={user_dn})")
LDAP_USER_ATTR_USERNAME = os.getenv("LDAP_USER_ATTR_USERNAME", "uid")
LDAP_USER_ATTR_EMAIL = os.getenv("LDAP_USER_ATTR_EMAIL", "mail")
LDAP_USER_ATTR_CN = os.getenv("LDAP_USER_ATTR_CN", "cn")
LDAP_CONNECT_TIMEOUT = int(os.getenv("LDAP_CONNECT_TIMEOUT", "5"))
LDAP_DEFAULT_ROLE = os.getenv("LDAP_DEFAULT_ROLE", "viewer")

# ══════════════════════════════════════════════════════════════════════════
# Apache Ranger — Unified Authorization
# ══════════════════════════════════════════════════════════════════════════

RANGER_ENABLED = os.getenv("RANGER_ENABLED", "false").lower() == "true"
RANGER_ADMIN_URL = os.getenv("RANGER_ADMIN_URL", "http://localhost:6080")
RANGER_USERNAME = os.getenv("RANGER_USERNAME", "admin")
RANGER_PASSWORD = os.getenv("RANGER_PASSWORD", "admin")
RANGER_SERVICE_NAME = os.getenv("RANGER_SERVICE_NAME", "ai-datahub")
RANGER_CACHE_TTL = int(os.getenv("RANGER_CACHE_TTL", "300"))

# ══════════════════════════════════════════════════════════════════════════
# Kerberos — SSO Authentication (optional)
# ══════════════════════════════════════════════════════════════════════════

KERBEROS_ENABLED = os.getenv("KERBEROS_ENABLED", "false").lower() == "true"
KERBEROS_KEYTAB_PATH = os.getenv("KERBEROS_KEYTAB_PATH", "")
KERBEROS_SERVICE_PRINCIPAL = os.getenv("KERBEROS_SERVICE_PRINCIPAL", "HTTP/ai-datahub.example.com")
KERBEROS_REALM = os.getenv("KERBEROS_REALM", "EXAMPLE.COM")
