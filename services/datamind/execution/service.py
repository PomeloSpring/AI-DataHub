"""Execution Layer Service — adh_execution_layers / adh_workspace_execution_layers CRUD."""

import json
import logging
from datetime import datetime

from services.shared.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize(row: dict) -> dict:
    """解析 config / allowed_tools JSON、datetime 转 ISO 字符串."""
    if isinstance(row.get("config"), str):
        try:
            row["config"] = json.loads(row["config"])
        except (json.JSONDecodeError, TypeError):
            row["config"] = {}
    elif row.get("config") is None:
        row["config"] = {}
    if isinstance(row.get("allowed_tools"), str):
        try:
            row["allowed_tools"] = json.loads(row["allowed_tools"])
        except (json.JSONDecodeError, TypeError):
            row["allowed_tools"] = []
    elif row.get("allowed_tools") is None:
        row["allowed_tools"] = []
    for k in ("created_at", "updated_at", "health_check_at"):
        if hasattr(row.get(k), "isoformat"):
            row[k] = row[k].isoformat()
    return row


# ── 执行层 CRUD ────────────────────────────────────────────────────

def list_layers() -> list[dict]:
    rows = execute_query("SELECT * FROM adh_execution_layers ORDER BY id")
    return [_normalize(r) for r in rows]


def get_layer(layer_id: int) -> dict | None:
    row = execute_query(
        "SELECT * FROM adh_execution_layers WHERE id = %s", (layer_id,), fetchone=True
    )
    return _normalize(row) if row else None


def get_layer_by_name(name: str) -> dict | None:
    row = execute_query(
        "SELECT * FROM adh_execution_layers WHERE name = %s", (name,), fetchone=True
    )
    return _normalize(row) if row else None


def create_layer(data: dict) -> int:
    now = _now()
    return execute_insert(
        """INSERT INTO adh_execution_layers
           (name, display_name, description, layer_type, config, status, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            data["name"],
            data.get("display_name", ""),
            data.get("description", ""),
            data["layer_type"],
            json.dumps(data.get("config") or {}, ensure_ascii=False),
            data.get("status", "active"),
            now, now,
        ),
    )


def update_layer(layer_id: int, data: dict) -> bool:
    updates = ["updated_at = %s"]
    params = [_now()]
    for field in ("name", "display_name", "description", "layer_type", "status"):
        if field in data and data[field] is not None:
            updates.append(f"{field} = %s")
            params.append(data[field])
    if "config" in data and data["config"] is not None:
        updates.append("config = %s")
        params.append(json.dumps(data["config"], ensure_ascii=False))
    params.append(layer_id)
    execute_write(
        f"UPDATE adh_execution_layers SET {', '.join(updates)} WHERE id = %s", params
    )
    return True


def record_health(layer_id: int, status: str, message: str):
    """记录健康检查结果."""
    execute_write(
        """UPDATE adh_execution_layers
           SET health_check_at = %s, last_test_status = %s, last_test_message = %s
           WHERE id = %s""",
        (_now(), status, message[:2000], layer_id),
    )


def delete_layer(layer_id: int) -> bool:
    execute_write(
        "DELETE FROM adh_workspace_execution_layers WHERE execution_layer_id = %s",
        (layer_id,),
    )
    execute_write("DELETE FROM adh_execution_layers WHERE id = %s", (layer_id,))
    return True


# ── 工作空间绑定 ───────────────────────────────────────────────────

def get_workspace_layers(workspace_id: int) -> list[dict]:
    rows = execute_query(
        """SELECT l.*, b.is_default, b.priority, b.allowed_tools
           FROM adh_workspace_execution_layers b
           JOIN adh_execution_layers l ON l.id = b.execution_layer_id
           WHERE b.workspace_id = %s
           ORDER BY b.priority DESC, l.id""",
        (workspace_id,),
    )
    return [_normalize(r) for r in rows]


def set_workspace_layers(workspace_id: int, bindings: list[dict]):
    """全量替换工作空间的执行层绑定.

    Args:
        bindings: [{"execution_layer_id": int, "is_default": bool,
                    "priority": int, "allowed_tools": [str]}]
    """
    execute_write(
        "DELETE FROM adh_workspace_execution_layers WHERE workspace_id = %s",
        (workspace_id,),
    )
    default_set = False
    for b in bindings:
        is_default = bool(b.get("is_default")) and not default_set
        default_set = default_set or is_default
        allowed = b.get("allowed_tools") or []
        execute_write(
            """INSERT INTO adh_workspace_execution_layers
               (workspace_id, execution_layer_id, is_default, priority, allowed_tools)
               VALUES (%s, %s, %s, %s, %s)""",
            (workspace_id, b["execution_layer_id"], 1 if is_default else 0,
             int(b.get("priority", 0)),
             json.dumps(allowed, ensure_ascii=False) if allowed else None),
        )
