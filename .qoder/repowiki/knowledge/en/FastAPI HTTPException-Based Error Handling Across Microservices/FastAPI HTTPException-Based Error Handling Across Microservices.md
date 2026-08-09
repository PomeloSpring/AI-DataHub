---
kind: error_handling
name: FastAPI HTTPException-Based Error Handling Across Microservices
category: error_handling
scope:
    - '**'
source_files:
    - services/shared/common/auth.py
    - services/aiplatform/api/agents.py
    - services/dataflow/api/sync.py
    - services/aiplatform/api/mcp_market.py
    - services/aiplatform/api/mcp_servers.py
    - services/shared/common/db/datasource_db.py
    - frontend/src/api/client.ts
---

## Overview

The AI-DataHub monorepo uses a straightforward, FastAPI-native error handling strategy across all nine Python microservices. There is no centralized exception hierarchy, custom error classes, or global exception handler — instead, each service raises `fastapi.HTTPException` directly from its API layer with explicit `status_code` and `detail` fields, while lower-level utility functions log errors via the standard `logging` module and return safe fallback values.

## Backend Pattern: Per-Endpoint try/except + HTTPException

Every FastAPI route follows the same shape:

```python
try:
    result = execute_query(...)
    if not result:
        raise HTTPException(status_code=404, detail="Resource not found")
    return result
except HTTPException:
    raise          # re-raise known errors unchanged
except Exception as e:
    logger.error("Operation failed: %s", e)
    raise HTTPException(status_code=500, detail=str(e))
```

This pattern appears consistently in `services/aiplatform/api/agents.py`, `services/dataflow/api/sync.py`, `services/aiplatform/api/mcp_market.py`, `services/aiplatform/api/mcp_servers.py`, and similar files. The `except HTTPException: raise` guard ensures that business-layer 4xx errors are not swallowed by the catch-all 500 block.

### Status code conventions observed

| Code | Meaning | Example usage |
|------|---------|---------------|
| 400 | Bad request / invalid input | `sync_mode must be 'full' or 'incremental'`, `No DAG configured for this task` |
| 401 | Unauthorized (invalid/expired JWT) | Handled centrally in `services/shared/common/auth.py` via `jwt.ExpiredSignatureError` → `HTTPException(401)` |
| 403 | Forbidden (insufficient role) | `require_admin` dependency raises 403 when user role ≠ `admin` |
| 404 | Resource not found | "Agent not found", "Sync task not found", "MCP server not found" |
| 500 | Internal server error | Catch-all for unexpected exceptions in every endpoint |
| 502 | Bad gateway (downstream failure) | Used in `dataflow/api/sync.py` when Airflow trigger fails |

There is **no** unified error-code enum, no structured error response envelope, and no mapping of database exceptions to domain-specific codes. Errors bubble up as raw strings in the `detail` field.

## Shared Authentication Error Handling

`services/shared/common/auth.py` centralizes auth-related errors:

- `decode_token` raises `jwt.ExpiredSignatureError` on expired tokens.
- `get_current_user` catches `ExpiredSignatureError` and `InvalidTokenError`, converting them to `HTTPException(401)` with human-readable messages.
- `require_admin` raises `HTTPException(403, "Admin access required")` when the current user lacks admin role.
- All other operations in this file use `try/except Exception` blocks that `logger.warning`/`logger.error` and return safe defaults (`None`, empty lists, `False`) rather than raising — this allows callers to decide how to surface failures.

## Service-Level Utility Functions: Fail-Silent Pattern

Non-API modules (e.g., `auth.py`, `db/datasource_db.py`, `crypto.py`) follow a fail-silent convention: they wrap I/O in `try/except Exception`, log via `logger.warning`/`logger.error`, and return `None`, empty collections, or masked values instead of propagating exceptions. Examples:

- `_encrypt_field` returns plaintext if encryption fails.
- `_decrypt_field` returns `"***"` if decryption fails.
- `log_audit` silently drops audit writes on DB errors.
- `get_datasource_by_id` logs a warning on password decryption failure and continues.

This means only the **API layer** is responsible for translating failures into HTTP responses; internal helpers never raise to callers.

## Frontend Error Handling

The frontend (`frontend/src/api/client.ts`) uses an Axios interceptor pattern:

- **Request interceptor**: attaches `Authorization: Bearer <token>` from `localStorage`.
- **Response interceptor**: intercepts `401` responses, attempts token refresh via `/api/auth/refresh`, retries the original request once, and redirects to `/login` if refresh fails.
- Other components handle errors locally using `try/catch` and store error state in React hooks (e.g., `useComponentData` exposes an `error: string | null` field).

There is no global error toast or centralized error display component — errors are surfaced per-component.

## What Is NOT Present

- No custom exception class hierarchy (no `class ServiceError(Exception)` or similar).
- No global FastAPI `exception_handler` registration — the default FastAPI JSON error response format is used.
- No structured error response model (e.g., `{ code, message, trace }`).
- No `panic`/`recover` equivalent — Python's `try/except` is the sole mechanism.
- No middleware-based error transformation — each route handles its own errors.
- No shared error-code constants beyond what individual services define inline.

## Conventions Summary

1. **Raise `HTTPException` at the API boundary only.** Lower layers return values or log and swallow exceptions.
2. **Use `except HTTPException: raise`** before a catch-all `except Exception` to preserve 4xx semantics.
3. **Log before raising 500** so stack traces are captured even though the HTTP response body contains only `str(e)`.
4. **Return safe defaults from shared utilities** rather than raising, so callers can choose whether to treat a failure as fatal.
5. **Frontend treats 401 specially** via interceptor retry; other status codes are propagated to calling components.