"""Shared utilities extracted from backend API files.

These functions were originally defined inline in backend/api/chat.py and other
API modules. They are extracted here so that multiple services can reuse them
without depending on the backend package.

Functions:
  - _sanitize_for_json: Make objects JSON-serializable (handles NaN, Decimal, datetime, bytes, set)
  - _parse_llm_json: Parse LLM JSON responses, handling markdown fences and edge cases
  - _now: Return current datetime as ISO string
  - _generate_id: Generate a microsecond-precision timestamp ID string
"""

import json
import math
import re
from datetime import datetime
from decimal import Decimal


def _sanitize_for_json(obj):
    """Make object JSON-serializable: handle NaN/inf, Decimal, datetime, bytes, set."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


def _parse_llm_json(raw: str) -> dict:
    """Parse LLM JSON response, handling markdown fences and edge cases.

    Attempts multiple strategies:
    1. Direct JSON parse
    2. Strip markdown code fences and parse
    3. Extract JSON object with "success" field
    4. Extract any JSON object
    5. Fallback: wrap raw text in a minimal dict
    """
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from text (sometimes LLM wraps in explanation)
    # First try to find a JSON object with "success" field
    match = re.search(r'\{[^{}]*"success"\s*:\s*(true|false)[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object (more permissive)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Fallback: return raw text wrapped in a minimal dict
    return {"success": True, "sql": text, "tables": [], "chart-type": "table"}


def _now() -> str:
    """Return current datetime as ISO format string."""
    return datetime.now().isoformat()


def _generate_id() -> str:
    """Generate a microsecond-precision timestamp ID string.

    Useful as a lightweight unique ID for records that don't use
    database auto-increment.
    """
    return str(int(datetime.now().timestamp() * 1_000_000))
