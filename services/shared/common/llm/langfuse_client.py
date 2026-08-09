"""Langfuse Client — LLM Observability integration.

Provides a singleton Langfuse client. Must be initialized BEFORE any Anthropic
client is created, so that Langfuse can monkey-patch the Anthropic SDK for
automatic tracing of all LLM calls (including streaming + thinking blocks).

Usage:
    from services.shared.common.llm.langfuse_client import get_langfuse, flush

    # In llm_client.py, just use @observe decorator:
    from langfuse.decorators import observe

    @observe(as_type="generation")
    def my_llm_call(...):
        ...
"""

import logging
from services.shared.common.config import (
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST, LANGFUSE_ENABLED,
)

logger = logging.getLogger(__name__)

# Singleton Langfuse client (lazy initialization)
_client = None


def get_langfuse():
    """Get or create the Langfuse client singleton.

    MUST be called before any Anthropic client is created so that the
    Langfuse SDK can monkey-patch the Anthropic module for auto-tracing.
    """
    global _client
    if _client is not None:
        return _client

    if not LANGFUSE_ENABLED:
        return None

    try:
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )
        logger.info("[Langfuse] Initialized (host=%s)", LANGFUSE_HOST)
        return _client
    except Exception as e:
        logger.warning("[Langfuse] Failed to initialize: %s", e)
        return None


def flush():
    """Flush pending Langfuse events."""
    lf = get_langfuse()
    if lf:
        try:
            lf.flush()
        except Exception:
            pass


# Eagerly initialize on import so Anthropic SDK is patched before any client is created.
get_langfuse()
