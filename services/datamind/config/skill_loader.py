"""Skill Loader — loads skill configs from files and DB with priority merging.

Skills are prompt templates that agents can dynamically load.
System skills live in config/skills/{name}/ (skill.yaml + system.md).
User-created skills live in adh_skills table.

Loading priority:
  DB (adh_skills, is_active=1) > File (config/skills/)
  User-created skills with the same name override system file skills.
"""

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent / "skills"


def _load_skill_from_file(skill_name: str) -> Optional[dict]:
    """Load skill from config/skills/{name}/ directory."""
    skill_dir = SKILLS_DIR / skill_name
    if not skill_dir.exists():
        return None

    result = {"name": skill_name, "source_type": "system"}

    # Load skill.yaml
    yaml_path = skill_dir / "skill.yaml"
    if yaml_path.exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                meta = yaml.safe_load(f) or {}
            result["display_name"] = meta.get("display_name", skill_name)
            result["description"] = meta.get("description", "")
            result["category"] = meta.get("category", "")
            result["skill_config"] = meta
        except Exception as e:
            logger.warning("Failed to load skill yaml %s: %s", skill_name, e)

    # Load system.md
    md_path = skill_dir / "system.md"
    if md_path.exists():
        try:
            result["system_prompt"] = md_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to load skill prompt %s: %s", skill_name, e)

    return result


def _load_skill_from_db(skill_name: str) -> Optional[dict]:
    """Load a single skill from adh_skills table."""
    try:
        from services.shared.common.db.metadata_db import get_metadata_conn

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, display_name, description, category, "
                    "system_prompt, skill_config, source_type, source_skill, "
                    "is_active, created_at, updated_at "
                    "FROM adh_skills WHERE name = %s AND is_active = 1",
                    (skill_name,)
                )
                row = cur.fetchone()
                if row:
                    # Parse skill_config JSON
                    config = row.get("skill_config", "")
                    if config and isinstance(config, str):
                        import json
                        try:
                            row["skill_config"] = json.loads(config)
                        except json.JSONDecodeError:
                            row["skill_config"] = {}
                    return row
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to load skill from DB %s: %s", skill_name, e)
    return None


def load_skill(skill_name: str) -> Optional[dict]:
    """Load a skill by name. DB (user-created) takes priority over file (system).

    Returns:
        Dict with keys: name, display_name, description, category, system_prompt,
                        skill_config, source_type
        None if not found
    """
    # 1. Try DB first (user-created or overridden)
    db_skill = _load_skill_from_db(skill_name)
    if db_skill and db_skill.get("system_prompt"):
        logger.debug("Loaded skill %s from DB", skill_name)
        return db_skill

    # 2. Fall back to file system
    file_skill = _load_skill_from_file(skill_name)
    if file_skill:
        logger.debug("Loaded skill %s from file", skill_name)
        return file_skill

    # 3. DB skill without system_prompt (partial override) — merge with file
    if db_skill:
        file_skill = _load_skill_from_file(skill_name) or {}
        merged = {**file_skill, **{k: v for k, v in db_skill.items() if v}}
        return merged

    return None


def list_skills(category: str = None) -> list[dict]:
    """List all skills (file + DB merged). DB entries override same-name file skills.

    Args:
        category: Optional filter by category (e.g., 'analysis')

    Returns:
        List of skill dicts with metadata (no system_prompt content)
    """
    skills = {}

    # 1. Load from file system
    if SKILLS_DIR.exists():
        for d in SKILLS_DIR.iterdir():
            if d.is_dir():
                skill = _load_skill_from_file(d.name)
                if skill:
                    skills[d.name] = skill

    # 2. Load from DB (overrides file skills with same name)
    try:
        from services.shared.common.db.metadata_db import get_metadata_conn

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, display_name, description, category, "
                    "source_type, source_skill, is_active "
                    "FROM adh_skills WHERE is_active = 1"
                )
                rows = cur.fetchall()
                for row in rows:
                    name = row["name"]
                    # Mark existing file skill as overridden
                    if name in skills:
                        skills[name]["_overridden_by_db"] = True
                    skills[name] = {**skills.get(name, {}), **row, "source_type": row.get("source_type", "user")}
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to load skills from DB: %s", e)

    # 3. Filter by category if specified
    result = list(skills.values())
    if category:
        result = [s for s in result if s.get("category") == category]

    # 4. Sort: system first, then by name
    result.sort(key=lambda s: (s.get("source_type", "") != "system", s.get("name", "")))

    # 5. Remove internal fields and system_prompt from listing
    for s in result:
        s.pop("system_prompt", None)
        s.pop("_overridden_by_db", None)

    return result


def list_analysis_skills() -> list[dict]:
    """List skills with category='analysis'."""
    return list_skills(category="analysis")


def get_analysis_skill_names() -> list[str]:
    """Get names of all analysis skills (for LLM tool enum)."""
    return [s["name"] for s in list_analysis_skills()]


def get_skill_summary_for_prompt() -> str:
    """Generate a summary of analysis skills for injection into data_analysis agent prompt.

    Returns a markdown string listing available skills with their descriptions.
    """
    skills = list_analysis_skills()
    if not skills:
        return ""

    lines = ["### 可用分析技能", ""]
    lines.append("当用户问题涉及以下分析领域时，先调用 `load_analysis_skill` 加载对应的专业提示词，再按提示词指引执行分析：")
    lines.append("")

    for s in skills:
        name = s.get("name", "")
        display = s.get("display_name", name)
        desc = s.get("description", "")
        lines.append(f"- **{name}**（{display}）：{desc}")

    lines.append("")
    lines.append("如果用户问题不属于以上任何分析领域，直接按通用数据分析流程处理。")

    return "\n".join(lines)
