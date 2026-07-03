"""Unified Configuration — ADH_* + METADATA_DB_* + VECTOR_DB_*.

New configuration layer with backward-compatible fallbacks.
Old CHATBI_* / DORIS_* variables still work but are deprecated.

Usage:
    from backend.common.config import (
        ADH_SECRET_KEY,
        METADATA_DB_HOST, METADATA_DB_PORT, METADATA_DB_USER, METADATA_DB_PASSWORD, METADATA_DB_DATABASE,
        VECTOR_DB_HOST, VECTOR_DB_PORT, VECTOR_DB_USER, VECTOR_DB_PASSWORD, VECTOR_DB_DATABASE,
        VECTOR_DIM, VECTOR_DISTANCE,
    )
"""

import os
from dotenv import load_dotenv
from pathlib import Path

# Load .env from backend directory
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=True)

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

# ══════════════════════════════════════════════════════════════════════════
# LLM (Anthropic) — unchanged
# ══════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

# ══════════════════════════════════════════════════════════════════════════
# Embedding — unchanged
# ══════════════════════════════════════════════════════════════════════════

EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "shibing624/text2vec-base-chinese")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
EMBEDDING_HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
EMBEDDING_MODEL_CACHE_DIR = os.getenv("EMBEDDING_MODEL_CACHE_DIR", "")

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
