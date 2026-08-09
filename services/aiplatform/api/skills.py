"""Skills Template API — Skills CRUD with scripts and versioning.

Tables: adh_skill_templates, adh_skill_template_versions, adh_skill_scripts
"""

import json
import logging
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request models ────────────────────────────────────────────────────

class SkillCreate(BaseModel):
    skill_key: str
    skill_name: str
    description: str = ""
    category: str = "custom"
    system_prompt: str = ""
    skill_config: Optional[dict] = None
    tools_json: Optional[list] = None
    examples_json: Optional[list] = None
    change_log: str = ""
    workspace_id: Optional[int] = None


class SkillUpdate(BaseModel):
    skill_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    system_prompt: Optional[str] = None
    skill_config: Optional[dict] = None
    tools_json: Optional[list] = None
    examples_json: Optional[list] = None
    change_log: str = ""


class ScriptCreate(BaseModel):
    script_name: str
    script_type: str = "python"
    script_content: str = ""


class ScriptUpdate(BaseModel):
    script_name: Optional[str] = None
    script_type: Optional[str] = None
    script_content: Optional[str] = None


class RollbackRequest(BaseModel):
    version: int


# ── Helper functions ──────────────────────────────────────────────────

def _row_to_dict(row):
    """Convert database row to dict, handling JSON fields."""
    if not row:
        return None
    d = dict(row)
    for field in ['skill_config', 'tools_json', 'examples_json', 'source_columns']:
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ── Skills CRUD ───────────────────────────────────────────────────────

@router.get("/skills")
def api_list_skills(
    category: str = Query(""),
    workspace_id: int = Query(0),
):
    """List all skills."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                sql = """
                    SELECT id, skill_key, skill_name, description, category,
                           skill_config, tools_json, examples_json,
                           version, is_active, created_at, updated_at, created_by
                    FROM adh_skill_templates
                    WHERE is_active = 1
                """
                params = []
                if category:
                    sql += " AND category = %s"
                    params.append(category)
                if workspace_id:
                    sql += " AND (workspace_id = %s OR workspace_id IS NULL)"
                    params.append(workspace_id)
                sql += " ORDER BY category, skill_name"
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error("List skills failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/{skill_key}")
def api_get_skill(skill_key: str):
    """Get skill by key."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_skill_templates WHERE skill_key = %s",
                    (skill_key,)
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"Skill '{skill_key}' not found")
                return _row_to_dict(row)
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get skill failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills")
def api_create_skill(req: SkillCreate):
    """Create a new skill."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Check if skill_key already exists
                cur.execute("SELECT id FROM adh_skill_templates WHERE skill_key = %s", (req.skill_key,))
                if cur.fetchone():
                    raise HTTPException(status_code=400, detail=f"Skill key '{req.skill_key}' already exists")

                # Insert skill
                cur.execute(
                    """INSERT INTO adh_skill_templates
                       (skill_key, skill_name, description, category, system_prompt,
                        skill_config, tools_json, examples_json, version, is_active,
                        workspace_id, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, 1, %s, %s)""",
                    (
                        req.skill_key, req.skill_name, req.description, req.category,
                        req.system_prompt,
                        json.dumps(req.skill_config) if req.skill_config else None,
                        json.dumps(req.tools_json) if req.tools_json else None,
                        json.dumps(req.examples_json) if req.examples_json else None,
                        req.workspace_id,
                        'admin',  # TODO: get from auth
                    )
                )
                skill_id = cur.lastrowid

                # Create initial version
                cur.execute(
                    """INSERT INTO adh_skill_template_versions
                       (skill_id, skill_key, version, system_prompt, skill_config,
                        tools_json, examples_json, change_log, created_by, is_current)
                       VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, 1)""",
                    (
                        skill_id, req.skill_key, req.system_prompt,
                        json.dumps(req.skill_config) if req.skill_config else None,
                        json.dumps(req.tools_json) if req.tools_json else None,
                        json.dumps(req.examples_json) if req.examples_json else None,
                        req.change_log or 'Initial version',
                        'admin',
                    )
                )

                conn.commit()
                return {"id": skill_id, "skill_key": req.skill_key, "message": "Skill created"}
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Create skill failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/skills/{skill_key}")
def api_update_skill(skill_key: str, req: SkillUpdate):
    """Update a skill (creates new version)."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Get current skill
                cur.execute("SELECT * FROM adh_skill_templates WHERE skill_key = %s", (skill_key,))
                skill = cur.fetchone()
                if not skill:
                    raise HTTPException(status_code=404, detail=f"Skill '{skill_key}' not found")

                skill = dict(skill)
                new_version = skill['version'] + 1

                # Build update fields
                updates = {}
                if req.skill_name is not None:
                    updates['skill_name'] = req.skill_name
                if req.description is not None:
                    updates['description'] = req.description
                if req.category is not None:
                    updates['category'] = req.category
                if req.system_prompt is not None:
                    updates['system_prompt'] = req.system_prompt
                if req.skill_config is not None:
                    updates['skill_config'] = json.dumps(req.skill_config)
                if req.tools_json is not None:
                    updates['tools_json'] = json.dumps(req.tools_json)
                if req.examples_json is not None:
                    updates['examples_json'] = json.dumps(req.examples_json)

                if updates:
                    updates['version'] = new_version
                    set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
                    values = list(updates.values()) + [skill_key]
                    cur.execute(f"UPDATE adh_skill_templates SET {set_clause} WHERE skill_key = %s", values)

                    # Mark old version as not current
                    cur.execute(
                        "UPDATE adh_skill_template_versions SET is_current = 0 WHERE skill_key = %s",
                        (skill_key,)
                    )

                    # Create new version
                    cur.execute(
                        """INSERT INTO adh_skill_template_versions
                           (skill_id, skill_key, version, system_prompt, skill_config,
                            tools_json, examples_json, change_log, created_by, is_current)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)""",
                        (
                            skill['id'], skill_key, new_version,
                            req.system_prompt if req.system_prompt is not None else skill.get('system_prompt'),
                            json.dumps(req.skill_config) if req.skill_config is not None else skill.get('skill_config'),
                            json.dumps(req.tools_json) if req.tools_json is not None else skill.get('tools_json'),
                            json.dumps(req.examples_json) if req.examples_json is not None else skill.get('examples_json'),
                            req.change_log or f'Updated to version {new_version}',
                            'admin',
                        )
                    )

                    conn.commit()

                return {"skill_key": skill_key, "version": new_version, "message": "Skill updated"}
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Update skill failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/skills/{skill_key}")
def api_delete_skill(skill_key: str):
    """Delete a skill (soft delete)."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_skill_templates SET is_active = 0 WHERE skill_key = %s",
                    (skill_key,)
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail=f"Skill '{skill_key}' not found")
                conn.commit()
                return {"skill_key": skill_key, "message": "Skill deleted"}
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Delete skill failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Version Management ───────────────────────────────────────────────

@router.get("/skills/{skill_key}/versions")
def api_get_versions(skill_key: str):
    """Get version history for a skill."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, skill_id, skill_key, version, system_prompt,
                              skill_config, tools_json, examples_json,
                              change_log, created_at, created_by, is_current
                       FROM adh_skill_template_versions
                       WHERE skill_key = %s
                       ORDER BY version DESC""",
                    (skill_key,)
                )
                rows = cur.fetchall()
                return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error("Get versions failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_key}/rollback")
def api_rollback_skill(skill_key: str, req: RollbackRequest):
    """Rollback skill to a specific version."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Get the target version
                cur.execute(
                    "SELECT * FROM adh_skill_template_versions WHERE skill_key = %s AND version = %s",
                    (skill_key, req.version)
                )
                version = cur.fetchone()
                if not version:
                    raise HTTPException(status_code=404, detail=f"Version {req.version} not found")

                version = dict(version)

                # Update skill template
                cur.execute(
                    """UPDATE adh_skill_templates
                       SET system_prompt = %s, skill_config = %s, tools_json = %s,
                           examples_json = %s, version = version + 1
                       WHERE skill_key = %s""",
                    (
                        version['system_prompt'],
                        version['skill_config'],
                        version['tools_json'],
                        version['examples_json'],
                        skill_key,
                    )
                )

                # Mark all versions as not current
                cur.execute(
                    "UPDATE adh_skill_template_versions SET is_current = 0 WHERE skill_key = %s",
                    (skill_key,)
                )

                # Mark target version as current
                cur.execute(
                    "UPDATE adh_skill_template_versions SET is_current = 1 WHERE id = %s",
                    (version['id'],)
                )

                conn.commit()
                return {"skill_key": skill_key, "version": req.version, "message": "Rollback successful"}
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Rollback failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Scripts Management ───────────────────────────────────────────────

@router.get("/skills/{skill_id}/scripts")
def api_list_scripts(skill_id: int):
    """List scripts for a skill."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, skill_id, script_name, script_type,
                              script_content, file_path, created_at, updated_at
                       FROM adh_skill_scripts
                       WHERE skill_id = %s
                       ORDER BY script_name""",
                    (skill_id,)
                )
                rows = cur.fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error("List scripts failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{skill_id}/scripts")
def api_create_script(skill_id: int, req: ScriptCreate):
    """Create a script for a skill."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Verify skill exists
                cur.execute("SELECT id FROM adh_skill_templates WHERE id = %s", (skill_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Skill not found")

                cur.execute(
                    """INSERT INTO adh_skill_scripts
                       (skill_id, script_name, script_type, script_content)
                       VALUES (%s, %s, %s, %s)""",
                    (skill_id, req.script_name, req.script_type, req.script_content)
                )
                script_id = cur.lastrowid
                conn.commit()
                return {"id": script_id, "message": "Script created"}
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Create script failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/skills/{skill_id}/scripts/{script_id}")
def api_update_script(skill_id: int, script_id: int, req: ScriptUpdate):
    """Update a script."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                updates = {}
                if req.script_name is not None:
                    updates['script_name'] = req.script_name
                if req.script_type is not None:
                    updates['script_type'] = req.script_type
                if req.script_content is not None:
                    updates['script_content'] = req.script_content

                if updates:
                    set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
                    values = list(updates.values()) + [script_id, skill_id]
                    cur.execute(
                        f"UPDATE adh_skill_scripts SET {set_clause} WHERE id = %s AND skill_id = %s",
                        values
                    )
                    conn.commit()

                return {"id": script_id, "message": "Script updated"}
        finally:
            conn.close()
    except Exception as e:
        logger.error("Update script failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/skills/{skill_id}/scripts/{script_id}")
def api_delete_script(skill_id: int, script_id: int):
    """Delete a script."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_skill_scripts WHERE id = %s AND skill_id = %s",
                    (script_id, skill_id)
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Script not found")
                conn.commit()
                return {"id": script_id, "message": "Script deleted"}
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Delete script failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Export/Import ─────────────────────────────────────────────────────

@router.get("/skills/{skill_key}/export")
def api_export_skill(skill_key: str):
    """Export a skill as JSON."""
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Get skill
                cur.execute("SELECT * FROM adh_skill_templates WHERE skill_key = %s", (skill_key,))
                skill = cur.fetchone()
                if not skill:
                    raise HTTPException(status_code=404, detail=f"Skill '{skill_key}' not found")

                skill = _row_to_dict(skill)

                # Get scripts
                cur.execute(
                    "SELECT script_name, script_type, script_content FROM adh_skill_scripts WHERE skill_id = %s",
                    (skill['id'],)
                )
                scripts = [dict(r) for r in cur.fetchall()]

                # Get versions
                cur.execute(
                    """SELECT version, system_prompt, skill_config, tools_json,
                              examples_json, change_log, created_at, created_by
                       FROM adh_skill_template_versions
                       WHERE skill_key = %s
                       ORDER BY version""",
                    (skill_key,)
                )
                versions = [_row_to_dict(r) for r in cur.fetchall()]

                return {
                    "skill": skill,
                    "scripts": scripts,
                    "versions": versions,
                }
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Export skill failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
