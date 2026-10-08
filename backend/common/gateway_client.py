"""DataEngine Client — backward-compatible wrapper.

This module is kept for backward compatibility.
New code should use backend.common.engine_client directly.

Usage:
    from backend.common.gateway_client import gateway_client
    # or preferably:
    from backend.common.engine_client import engine_client
"""

import logging

logger = logging.getLogger(__name__)

# Re-export from engine_client for backward compatibility
from backend.common.engine_client import (
    engine_client as gateway_client,
    EngineClient as GatewayClient,
    EngineError as GatewayError,
    ENGINE_ENABLED as GATEWAY_ENABLED,
    ENGINE_SERVER_URL as GATEWAY_URL,
    ENGINE_TIMEOUT as GATEWAY_TIMEOUT,
)

__all__ = [
    "gateway_client",
    "GatewayClient",
    "GatewayError",
    "GATEWAY_ENABLED",
    "GATEWAY_URL",
    "GATEWAY_TIMEOUT",
]
