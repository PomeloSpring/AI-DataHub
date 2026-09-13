"""Execution Layer Service — adh_execution_layers / adh_workspace_execution_layers CRUD."""

import json
import logging
import os
from datetime import datetime

from services.shared.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize(row: dict) -> dict:
    """解析 config / allowed_tools / capabilities / tools JSON、datetime 转 ISO 字符串."""
    for json_field, default in (
        ("config", {}),
        ("allowed_tools", []),
        ("capabilities", []),
        ("tools", []),
    ):
        val = row.get(json_field)
        if isinstance(val, str):
            try:
                row[json_field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                row[json_field] = default
        elif val is None:
            row[json_field] = default
    for k in ("created_at", "updated_at", "health_check_at", "last_heartbeat_at", "registered_at"):
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


# ── 注册与发现 (Phase 3) ─────────────────────────────────────────

#: 自注册执行层心跳超时窗口(秒)。超过此窗口未上报心跳视为不健康。
HEARTBEAT_TIMEOUT = int(os.getenv("EXEC_LAYER_HEARTBEAT_TIMEOUT", "90"))


def _parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _row_healthy(row: dict) -> bool:
    """判断执行层是否健康可用。

    - 非 active 一律不健康
    - builtin / 手工配置的层:active 即健康(进程内可达)
    - 自注册层(source=self):需在心跳窗口内上报过心跳
    """
    if row.get("status") != "active":
        return False
    if row.get("source") != "self":
        return True
    hb = _parse_dt(row.get("last_heartbeat_at"))
    if hb is None:
        return False
    return (datetime.now() - hb).total_seconds() <= HEARTBEAT_TIMEOUT


def register_layer(data: dict) -> int:
    """自注册执行层(按 name upsert),刷新心跳与能力/工具目录."""
    now = _now()
    name = data["name"]
    capabilities = json.dumps(data.get("capabilities") or [], ensure_ascii=False)
    tools = json.dumps(data.get("tools") or [], ensure_ascii=False)
    endpoint_url = data.get("endpoint_url", "")
    config = json.dumps(data.get("config") or {}, ensure_ascii=False)
    display_name = data.get("display_name", "")
    description = data.get("description", "")
    layer_type = data.get("layer_type", "remote")

    existing = get_layer_by_name(name)
    if existing:
        execute_write(
            """UPDATE adh_execution_layers
               SET display_name=%s, description=%s, layer_type=%s, config=%s,
                   capabilities=%s, tools=%s, endpoint_url=%s,
                   source='self', status='active', last_heartbeat_at=%s, updated_at=%s
               WHERE id=%s""",
            (display_name or existing.get("display_name") or "",
             description or existing.get("description") or "",
             layer_type, config, capabilities, tools, endpoint_url, now, now,
             existing["id"]),
        )
        return existing["id"]

    return execute_insert(
        """INSERT INTO adh_execution_layers
           (name, display_name, description, layer_type, config, status,
            capabilities, tools, endpoint_url, source, last_heartbeat_at, registered_at,
            created_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,'active',%s,%s,%s,'self',%s,%s,%s,%s)""",
        (name, display_name, description, layer_type, config,
         capabilities, tools, endpoint_url, now, now, now, now),
    )


def heartbeat(name: str = "", layer_id: int = 0, tools: list | None = None) -> bool:
    """刷新心跳;可选同时更新工具目录."""
    row = get_layer(layer_id) if layer_id else (get_layer_by_name(name) if name else None)
    if not row:
        return False
    now = _now()
    if tools is not None:
        execute_write(
            "UPDATE adh_execution_layers SET status='active', last_heartbeat_at=%s, tools=%s, updated_at=%s WHERE id=%s",
            (now, json.dumps(tools, ensure_ascii=False), now, row["id"]),
        )
    else:
        execute_write(
            "UPDATE adh_execution_layers SET status='active', last_heartbeat_at=%s, updated_at=%s WHERE id=%s",
            (now, now, row["id"]),
        )
    return True


def deregister(name: str = "", layer_id: int = 0) -> bool:
    """注销自注册执行层(置为 inactive)。仅对 source=self 生效,避免误下线手工层."""
    row = get_layer(layer_id) if layer_id else (get_layer_by_name(name) if name else None)
    if not row or row.get("source") != "self":
        return False
    execute_write(
        "UPDATE adh_execution_layers SET status='inactive', updated_at=%s WHERE id=%s",
        (_now(), row["id"]),
    )
    return True


def record_tools(layer_id: int, tools: list) -> bool:
    execute_write(
        "UPDATE adh_execution_layers SET tools=%s, updated_at=%s WHERE id=%s",
        (json.dumps(tools or [], ensure_ascii=False), _now(), layer_id),
    )
    return True


def is_healthy_layer(row: dict) -> bool:
    """公开健康判定(供调度层过滤 stale 的自注册执行层)."""
    normalized = _normalize(dict(row))
    return _row_healthy(normalized)


def discover_layers(capability: str = "") -> list[dict]:
    """返回健康可用的执行层列表,可按能力标签过滤."""
    rows = list_layers()
    result = []
    for r in rows:
        if not _row_healthy(r):
            continue
        if capability:
            caps = r.get("capabilities") or []
            if capability not in caps:
                continue
        r["healthy"] = True
        result.append(r)
    return result


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
