"""Session Properties Builder — builds session properties for engine-server-rust RLS.

Session properties are passed as HTTP headers to the engine server,
which uses them to evaluate RLS (Row-Level Security) conditions.

Usage:
    from services.datamind.nl2sql.sql.session_properties import build_session_properties

    props = build_session_properties(user_context={
        "user_id": 123,
        "username": "alice",
        "role": "viewer",
        "workspace_id": 1,
    })
    # Returns: {"session_user_id": "123", "session_role": "viewer", ...}
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def build_session_properties(user_context: Optional[dict] = None) -> dict:
    """Build session properties from user context.

    Args:
        user_context: Dict with user info, typically from JWT token:
            - user_id: int
            - username: str
            - role: str (admin / editor / viewer)
            - workspace_id: int

    Returns:
        Dict of session properties to pass as x-wren-* headers.
    """
    props = {}

    if not user_context:
        return props

    # User ID — used in RLS conditions like "user_id = @session_user_id"
    if user_id := user_context.get("user_id"):
        props["session_user_id"] = str(user_id)

    # Username
    if username := user_context.get("username"):
        props["session_username"] = str(username)

    # Role — used in RLS conditions like "role = @session_role"
    if role := user_context.get("role"):
        props["session_role"] = str(role)

        # Admin bypass — skip all RLS policies
        if role == "admin":
            props["rls_policy_ignore"] = "true"

    # Workspace ID — used for multi-tenant RLS
    if ws_id := user_context.get("workspace_id"):
        props["session_workspace_id"] = str(ws_id)

    return props


def build_session_properties_for_headers(user_context: Optional[dict] = None) -> dict:
    """Build session properties formatted as HTTP headers.

    Returns dict with x-wren-variable-* header keys.
    """
    props = build_session_properties(user_context)
    headers = {}
    for key, value in props.items():
        headers[f"x-wren-variable-{key}"] = str(value)
    return headers
