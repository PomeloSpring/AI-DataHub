"""DataEngine Client — backward-compatible wrapper.

This module is kept for backward compatibility.
New code should use services.shared.common.engine_client directly.

Usage:
    from services.shared.common.gateway_client import gateway_client
    # or preferably:
    from services.shared.common.engine_client import engine_client
"""

import logging

logger = logging.getLogger(__name__)

# Re-export from engine_client for backward compatibility
from services.shared.common.engine_client import (
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
