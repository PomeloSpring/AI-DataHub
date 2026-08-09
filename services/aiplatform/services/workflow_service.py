"""Workflow Service — Workflow + Prompt CRUD.

Migrated from backend/api/admin_workflow.py.
"""

import json
import logging
import time as _time
from datetime import datetime
from typing import Optional

from services.shared.common.db import DBConnection, execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(_time.time() * 1000000)


# ════════════════════════════════════════════════════════════════════
# Prompt Management
# ════════════════════════════════════════════════════════════════════

def list_prompts(page: int = 1, size: int = 50, search: str = "") -> dict:
    """List prompts with pagination."""
    conditions = []
    params = []
    if search:
        conditions.append("(prompt_key LIKE %s OR prompt_name LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_prompts {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
                f"description, version, is_active, created_at, updated_at, created_by, change_log "
                f"FROM adh_prompts {where} "
                f"ORDER BY prompt_key, version DESC LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
                r["is_active"] = bool(r.get("is_active"))

    return {"total": total, "items": rows}


def get_prompt(prompt_key: str) -> Optional[dict]:
    """Get the active version of a prompt."""
    row = execute_query(
        "SELECT id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
        "description, version, is_active, created_at, updated_at, created_by, change_log "
        "FROM adh_prompts WHERE prompt_key = %s AND is_active = 1",
        (prompt_key,), fetchone=True,
    )
    if row:
        for k in ("created_at", "updated_at"):
            if hasattr(row.get(k), "isoformat"):
                row[k] = row[k].isoformat()
        row["is_active"] = bool(row.get("is_active"))
    return row


def create_prompt(data: dict, username: str = "") -> dict:
    """Create a new prompt (initial version)."""
    prompt_key = data.get("prompt_key", "")
    if not prompt_key:
        raise ValueError("prompt_key is required")

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM adh_prompts WHERE prompt_key = %s AND is_active = 1",
                (prompt_key,),
            )
            if cur.fetchone():
                raise ValueError(f"Prompt '{prompt_key}' already exists")

            now = _now()
            prompt_id = _generate_id()

            cur.execute(
                "INSERT INTO adh_prompts "
                "(id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
                "description, version, is_active, created_at, updated_at, created_by, change_log) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1, 1, %s, %s, %s, %s)",
                (prompt_id, prompt_key, data.get("prompt_name", ""),
                 data.get("system_prompt", ""), data.get("user_prompt_template", ""),
                 data.get("description", ""), now, now,
                 username, data.get("change_log", "Initial version")),
            )

            version_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompt_versions "
                "(id, prompt_id, prompt_key, version, system_prompt, user_prompt_template, "
                "change_log, created_at, created_by, is_current) "
                "VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, 1)",
                (version_id, prompt_id, prompt_key,
                 data.get("system_prompt", ""), data.get("user_prompt_template", ""),
                 data.get("change_log", "Initial version"), now, username),
            )

    return {"id": prompt_id, "version": 1}


def update_prompt(prompt_key: str, data: dict, username: str = "") -> dict:
    """Update prompt (creates a new version)."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, prompt_name, system_prompt, user_prompt_template, version "
                "FROM adh_prompts WHERE prompt_key = %s AND is_active = 1",
                (prompt_key,),
            )
            current = cur.fetchone()
            if not current:
                raise ValueError(f"Prompt '{prompt_key}' not found")

            now = _now()
            new_version = current["version"] + 1

            prompt_name = data.get("prompt_name") or current["prompt_name"]
            system_prompt = data.get("system_prompt") if data.get("system_prompt") is not None else current["system_prompt"]
            user_prompt_template = data.get("user_prompt_template") if data.get("user_prompt_template") is not None else current["user_prompt_template"]
            description = data.get("description", "")
            change_log = data.get("change_log") or f"Version {new_version}"

            new_prompt_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompts "
                "(id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
                "description, version, is_active, created_at, updated_at, created_by, change_log) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s)",
                (new_prompt_id, prompt_key, prompt_name, system_prompt, user_prompt_template,
                 description, new_version, now, now, username, change_log),
            )

            cur.execute(
                "UPDATE adh_prompts SET is_active = 0 WHERE prompt_key = %s AND id != %s",
                (prompt_key, new_prompt_id),
            )
            cur.execute(
                "UPDATE adh_prompt_versions SET is_current = 0 WHERE prompt_key = %s",
                (prompt_key,),
            )

            version_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompt_versions "
                "(id, prompt_id, prompt_key, version, system_prompt, user_prompt_template, "
                "change_log, created_at, created_by, is_current) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)",
                (version_id, new_prompt_id, prompt_key, new_version, system_prompt,
                 user_prompt_template, change_log, now, username),
            )

    return {"id": new_prompt_id, "version": new_version}


def get_prompt_versions(prompt_key: str) -> list:
    """Get prompt version history."""
    rows = execute_query(
        "SELECT id, prompt_id, prompt_key, version, system_prompt, user_prompt_template, "
        "change_log, created_at, created_by, is_current "
        "FROM adh_prompt_versions WHERE prompt_key = %s ORDER BY version DESC",
        (prompt_key,),
    )
    for r in rows:
        if hasattr(r.get("created_at"), "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
        r["is_current"] = bool(r.get("is_current"))
    return rows


def rollback_prompt(prompt_key: str, version: int, username: str = "") -> dict:
    """Rollback prompt to a specific version."""
    target = execute_query(
        "SELECT id, system_prompt, user_prompt_template, change_log "
        "FROM adh_prompt_versions WHERE prompt_key = %s AND version = %s",
        (prompt_key, version), fetchone=True,
    )
    if not target:
        raise ValueError(f"Version {version} not found")

    now = _now()

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(version) as max_version FROM adh_prompts WHERE prompt_key = %s",
                (prompt_key,),
            )
            max_version = cur.fetchone()["max_version"] or 0
            new_version = max_version + 1

            cur.execute(
                "SELECT prompt_name, description FROM adh_prompts WHERE prompt_key = %s AND is_active = 1",
                (prompt_key,),
            )
            current = cur.fetchone() or {"prompt_name": prompt_key, "description": ""}

            new_prompt_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompts "
                "(id, prompt_key, prompt_name, system_prompt, user_prompt_template, "
                "description, version, is_active, created_at, updated_at, created_by, change_log) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s)",
                (new_prompt_id, prompt_key, current["prompt_name"],
                 target["system_prompt"], target["user_prompt_template"],
                 current["description"], new_version, now, now, username,
                 f"Rollback to version {version}"),
            )

            cur.execute(
                "UPDATE adh_prompts SET is_active = 0 WHERE prompt_key = %s AND id != %s",
                (prompt_key, new_prompt_id),
            )
            cur.execute(
                "UPDATE adh_prompt_versions SET is_current = 0 WHERE prompt_key = %s",
                (prompt_key,),
            )

            version_id = _generate_id()
            cur.execute(
                "INSERT INTO adh_prompt_versions "
                "(id, prompt_id, prompt_key, version, system_prompt, user_prompt_template, "
                "change_log, created_at, created_by, is_current) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)",
                (version_id, new_prompt_id, prompt_key, new_version,
                 target["system_prompt"], target["user_prompt_template"],
                 f"Rollback to version {version}", now, username),
            )

    return {"id": new_prompt_id, "version": new_version, "rollback_from": version}


# ════════════════════════════════════════════════════════════════════
# Workflow Configuration Management
# ════════════════════════════════════════════════════════════════════

def list_workflows(page: int = 1, size: int = 50, search: str = "") -> dict:
    """List workflows with pagination."""
    conditions = []
    params = []
    if search:
        conditions.append("name LIKE %s")
        params.append(f"%{search}%")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_workflow_configs {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, name, description, is_active, is_default, workflow_type, dag_config, "
                f"created_at, updated_at, created_by "
                f"FROM adh_workflow_configs {where} "
                f"ORDER BY is_default DESC, name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            workflows = cur.fetchall()

            result = []
            for wf in workflows:
                for k in ("created_at", "updated_at"):
                    if hasattr(wf.get(k), "isoformat"):
                        wf[k] = wf[k].isoformat()
                wf["is_active"] = bool(wf.get("is_active"))
                wf["is_default"] = bool(wf.get("is_default"))

                cur.execute(
                    "SELECT id, workflow_id, step_type, step_name, step_order, "
                    "max_rounds, is_enabled, prompt_key, config, "
                    "position_x, position_y, node_type, dependencies, "
                    "created_at, updated_at "
                    "FROM adh_workflow_steps WHERE workflow_id = %s ORDER BY step_order",
                    (wf["id"],),
                )
                steps = cur.fetchall()
                for step in steps:
                    for k in ("created_at", "updated_at"):
                        if hasattr(step.get(k), "isoformat"):
                            step[k] = step[k].isoformat()
                    step["is_enabled"] = bool(step.get("is_enabled"))
                    if step.get("config"):
                        try:
                            step["config"] = json.loads(step["config"])
                        except Exception:
                            pass
                wf["steps"] = steps

                try:
                    cur.execute(
                        "SELECT id, workflow_id, source_step_id, target_step_id, edge_type, "
                        "condition_expr, label, created_at "
                        "FROM adh_workflow_edges WHERE workflow_id = %s",
                        (wf["id"],),
                    )
                    edges = cur.fetchall()
                    for e in edges:
                        if hasattr(e.get("created_at"), "isoformat"):
                            e["created_at"] = e["created_at"].isoformat()
                    wf["edges"] = edges
                except Exception:
                    wf["edges"] = []

                result.append(wf)

    return {"total": total, "items": result}


def get_workflow(workflow_id: int) -> Optional[dict]:
    """Get workflow details."""
    wf = execute_query(
        "SELECT id, name, description, is_active, is_default, workflow_type, dag_config, "
        "created_at, updated_at, created_by "
        "FROM adh_workflow_configs WHERE id = %s",
        (workflow_id,), fetchone=True,
    )
    if not wf:
        return None

    for k in ("created_at", "updated_at"):
        if hasattr(wf.get(k), "isoformat"):
            wf[k] = wf[k].isoformat()
    wf["is_active"] = bool(wf.get("is_active"))
    wf["is_default"] = bool(wf.get("is_default"))

    steps = execute_query(
        "SELECT id, workflow_id, step_type, step_name, step_order, "
        "max_rounds, is_enabled, prompt_key, config, "
        "position_x, position_y, node_type, dependencies, "
        "created_at, updated_at "
        "FROM adh_workflow_steps WHERE workflow_id = %s ORDER BY step_order",
        (workflow_id,),
    )
    for step in steps:
        for k in ("created_at", "updated_at"):
            if hasattr(step.get(k), "isoformat"):
                step[k] = step[k].isoformat()
        step["is_enabled"] = bool(step.get("is_enabled"))
        if step.get("config"):
            try:
                step["config"] = json.loads(step["config"])
            except Exception:
                pass
    wf["steps"] = steps

    try:
        edges = execute_query(
            "SELECT id, workflow_id, source_step_id, target_step_id, edge_type, "
            "condition_expr, label, created_at "
            "FROM adh_workflow_edges WHERE workflow_id = %s",
            (workflow_id,),
        )
        for e in edges:
            if hasattr(e.get("created_at"), "isoformat"):
                e["created_at"] = e["created_at"].isoformat()

        valid_step_ids = {s["id"] for s in steps}
        valid_edges = [e for e in edges
                       if e["source_step_id"] in valid_step_ids
                       and e["target_step_id"] in valid_step_ids]
        wf["edges"] = valid_edges
    except Exception:
        wf["edges"] = []

    return wf


def create_workflow(data: dict, username: str = "") -> int:
    """Create a new workflow."""
    now = _now()
    workflow_id = _generate_id()

    with DBConnection() as conn:
        with conn.cursor() as cur:
            if data.get("is_default"):
                cur.execute("UPDATE adh_workflow_configs SET is_default = 0 WHERE is_default = 1")

            cur.execute(
                "INSERT INTO adh_workflow_configs "
                "(id, name, description, is_active, is_default, workflow_type, dag_config, "
                "created_at, updated_at, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (workflow_id, data.get("name", ""), data.get("description", ""),
                 1 if data.get("is_active", True) else 0,
                 1 if data.get("is_default", False) else 0,
                 data.get("workflow_type", "linear"),
                 data.get("dag_config"), now, now, username),
            )

            if data.get("steps"):
                for step in data["steps"]:
                    step_id = _generate_id()
                    config_json = json.dumps(step.get("config", {}), ensure_ascii=False) if step.get("config") else None
                    cur.execute(
                        "INSERT INTO adh_workflow_steps "
                        "(id, workflow_id, step_type, step_name, step_order, "
                        "max_rounds, is_enabled, prompt_key, config, "
                        "position_x, position_y, node_type, dependencies, "
                        "created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (step_id, workflow_id, step.get("step_type", ""),
                         step.get("step_name", ""), step.get("step_order", 0),
                         step.get("max_rounds", 1),
                         1 if step.get("is_enabled", True) else 0,
                         step.get("prompt_key"), config_json,
                         step.get("position_x", 0), step.get("position_y", 0),
                         step.get("node_type", "step"), step.get("dependencies"),
                         now, now),
                    )

            if data.get("edges"):
                for edge in data["edges"]:
                    edge_id = _generate_id()
                    cur.execute(
                        "INSERT INTO adh_workflow_edges "
                        "(id, workflow_id, source_step_id, target_step_id, edge_type, "
                        "condition_expr, label, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (edge_id, workflow_id, edge.get("source_step_id"),
                         edge.get("target_step_id"), edge.get("edge_type", "normal"),
                         edge.get("condition_expr"), edge.get("label"), now),
                    )

    return workflow_id


def update_workflow(workflow_id: int, data: dict, username: str = "") -> bool:
    """Update a workflow: config fields plus full steps/edges replacement."""
    existing = execute_query(
        "SELECT id FROM adh_workflow_configs WHERE id = %s",
        (workflow_id,), fetchone=True,
    )
    if not existing:
        return False

    now = _now()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            sets, params = [], []
            for field in ("name", "description", "workflow_type", "dag_config"):
                if data.get(field) is not None:
                    sets.append(f"{field} = %s")
                    params.append(data[field])
            if "is_active" in data:
                sets.append("is_active = %s")
                params.append(1 if data["is_active"] else 0)
            if data.get("is_default"):
                cur.execute("UPDATE adh_workflow_configs SET is_default = 0 WHERE is_default = 1")
                sets.append("is_default = 1")
            if sets:
                sets.append("updated_at = %s")
                params.extend([now, workflow_id])
                cur.execute(
                    f"UPDATE adh_workflow_configs SET {', '.join(sets)} WHERE id = %s", params,
                )

            if data.get("steps") is not None:
                cur.execute("DELETE FROM adh_workflow_steps WHERE workflow_id = %s", (workflow_id,))
                for step in data["steps"]:
                    step_id = step.get("id") or _generate_id()
                    config_json = json.dumps(step.get("config", {}), ensure_ascii=False) if step.get("config") else None
                    cur.execute(
                        "INSERT INTO adh_workflow_steps "
                        "(id, workflow_id, step_type, step_name, step_order, "
                        "max_rounds, is_enabled, prompt_key, config, "
                        "position_x, position_y, node_type, dependencies, "
                        "created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (step_id, workflow_id, step.get("step_type", ""),
                         step.get("step_name", ""), step.get("step_order", 0),
                         step.get("max_rounds", 1),
                         1 if step.get("is_enabled", True) else 0,
                         step.get("prompt_key"), config_json,
                         step.get("position_x", 0), step.get("position_y", 0),
                         step.get("node_type", "step"), step.get("dependencies"),
                         now, now),
                    )

            if data.get("edges") is not None:
                cur.execute("DELETE FROM adh_workflow_edges WHERE workflow_id = %s", (workflow_id,))
                for edge in data["edges"]:
                    cur.execute(
                        "INSERT INTO adh_workflow_edges "
                        "(id, workflow_id, source_step_id, target_step_id, edge_type, "
                        "condition_expr, label, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (_generate_id(), workflow_id, edge.get("source_step_id"),
                         edge.get("target_step_id"), edge.get("edge_type", "normal"),
                         edge.get("condition_expr"), edge.get("label"), now),
                    )

    return True


def delete_workflow(workflow_id: int) -> bool:
    """Delete a workflow (cannot delete default)."""
    wf = execute_query(
        "SELECT is_default FROM adh_workflow_configs WHERE id = %s",
        (workflow_id,), fetchone=True,
    )
    if not wf:
        return False
    if wf["is_default"]:
        raise ValueError("Cannot delete the default workflow")

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_workflow_steps WHERE workflow_id = %s", (workflow_id,))
            cur.execute("DELETE FROM adh_workflow_edges WHERE workflow_id = %s", (workflow_id,))
            cur.execute("DELETE FROM adh_workflow_configs WHERE id = %s", (workflow_id,))
    return True


# ════════════════════════════════════════════════════════════════════
# Workflow Execution Logs
# ════════════════════════════════════════════════════════════════════

def list_workflow_logs(
    page: int = 1, size: int = 50,
    workflow_id: Optional[int] = None, status: Optional[str] = None,
) -> dict:
    """List workflow execution logs."""
    conditions = []
    params = []
    if workflow_id:
        conditions.append("workflow_id = %s")
        params.append(workflow_id)
    if status:
        conditions.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM adh_workflow_logs {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, workflow_id, workflow_name, session_id, user_id, username, "
                f"question, current_step, current_round, "
                f"generated_sql, chart_type, status, error_message, "
                f"started_at, completed_at, elapsed_ms "
                f"FROM adh_workflow_logs {where} "
                f"ORDER BY started_at DESC LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("started_at", "completed_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()

    return {"total": total, "items": rows}


def get_workflow_log(log_id: int) -> Optional[dict]:
    """Get workflow log details."""
    row = execute_query("SELECT * FROM adh_workflow_logs WHERE id = %s", (log_id,), fetchone=True)
    if not row:
        return None

    for k in ("started_at", "completed_at"):
        if hasattr(row.get(k), "isoformat"):
            row[k] = row[k].isoformat()

    for field in ("metadata_context", "metadata_requested", "metadata_supplemented", "execution_result"):
        if row.get(field):
            try:
                row[field] = json.loads(row[field])
            except Exception:
                pass

    return row
