"""Prompt Loader — load prompts from DB with file fallback.

Loading priority:
1. Database (adh_prompts table) — for dynamic updates (prompt_key = '<skill>:<component>[:<dialect>]')
2. File system (config/ directory) — for version control

A TTL cache (default 300s) fronts both sources so DB edits take effect within
5 minutes without a restart while hot read paths stay fast.

Usage:
    from services.datamind.config.loader import load_prompt

    # Load a specific prompt
    system_prompt = load_prompt("nl2sql", "system")
    rules = load_prompt("nl2sql", "rules")

    # Load with dialect
    dialect_rules = load_prompt("nl2sql", "dialects/mysql")
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Base directory for prompt files
PROMPTS_DIR = Path(__file__).parent

# ── TTL cache ──────────────────────────────────────────────────────────
_PROMPT_CACHE_TTL = float(os.getenv("PROMPT_CACHE_TTL", "300"))  # seconds
_prompt_cache: dict[str, tuple[float, Optional[str]]] = {}


def clear_prompt_cache():
    """Invalidate the prompt TTL cache (e.g. right after a DB prompt update)."""
    _prompt_cache.clear()


def _cache_get(key: str) -> tuple[bool, Optional[str]]:
    item = _prompt_cache.get(key)
    if item is None:
        return False, None
    ts, value = item
    if time.time() - ts > _PROMPT_CACHE_TTL:
        _prompt_cache.pop(key, None)
        return False, None
    return True, value


def _cache_set(key: str, value: Optional[str]):
    _prompt_cache[key] = (time.time(), value)


def _load_prompt_from_db(prompt_key: str, workspace_id: int = 0) -> Optional[str]:
    """Try to load a prompt override from the adh_prompts table.

    Workspace-aware: a workspace-specific row wins over the global (workspace_id=0)
    default. See PromptVersionStore.get_active().
    """
    try:
        from services.shared.common.versioned_config import get_prompt_store

        row = get_prompt_store().get_active(prompt_key, workspace_id)
        if row:
            content = row.get("system_prompt") or row.get("user_prompt_template")
            if content:
                return content
    except Exception as e:
        logger.debug("DB prompt lookup failed for %s (ws=%s): %s", prompt_key, workspace_id, e)
    return None


def load_prompt(skill: str, component: str, dialect: str = None, workspace_id: int = 0) -> Optional[str]:
    """Load a prompt: DB override first (cached), then file system.

    Args:
        skill: Skill name (e.g., 'nl2sql', 'chart', 'analysis', 'guardrail')
        component: Component name (e.g., 'system', 'rules', 'examples')
        dialect: Optional dialect name (e.g., 'mysql', 'doris')
        workspace_id: Workspace scope for DB override (0 = global default).
            A workspace-specific DB row takes precedence over the global one.

    Returns:
        Prompt text or None if not found
    """
    # Build a stable cache/DB key
    if dialect:
        prompt_key = f"{skill}:dialects/{dialect}"
        path = PROMPTS_DIR / "skills" / skill / "dialects" / f"{dialect}.md"
    elif component:
        prompt_key = f"{skill}:{component}"
        path = PROMPTS_DIR / "skills" / skill / f"{component}.md"
    else:
        return None

    # Cache key is workspace-scoped so a global and an override never collide
    cache_key = f"{prompt_key}@{workspace_id or 0}"

    # 0. TTL cache
    hit, cached = _cache_get(cache_key)
    if hit:
        return cached

    result: Optional[str] = None

    # 1. Database override (workspace -> global fallback inside get_active)
    result = _load_prompt_from_db(prompt_key, workspace_id)

    # 2. File system fallback
    if result is None and path.exists():
        try:
            result = path.read_text(encoding="utf-8")
            logger.debug("Loaded prompt from %s", path)
        except Exception as e:
            logger.warning("Failed to load prompt from %s: %s", path, e)

    # 3. Shared rules fallback
    if result is None and component and not dialect:
        rules_path = PROMPTS_DIR / "rules" / f"{component}.md"
        if rules_path.exists():
            try:
                result = rules_path.read_text(encoding="utf-8")
                logger.debug("Loaded rule from %s", rules_path)
            except Exception as e:
                logger.warning("Failed to load rule from %s: %s", rules_path, e)

    if result is None:
        logger.debug("Prompt not found: skill=%s, component=%s, dialect=%s", skill, component, dialect)

    _cache_set(cache_key, result)
    return result


def load_nl2sql_prompt(dialect: str = None, workspace_id: int = 0) -> dict:
    """Load all NL2SQL prompt components.

    Args:
        dialect: Database dialect (e.g., 'mysql', 'doris')
        workspace_id: Workspace scope for DB override (0 = global default)

    Returns:
        Dict with 'system', 'rules', 'examples', 'dialect' keys
    """
    return {
        "system": load_prompt("nl2sql", "system", workspace_id=workspace_id),
        "rules": load_prompt("nl2sql", "rules", workspace_id=workspace_id),
        "examples": load_prompt("nl2sql", "examples", workspace_id=workspace_id),
        "dialect": load_prompt("nl2sql", None, dialect=dialect, workspace_id=workspace_id) if dialect else None,
    }


def load_shared_rules() -> dict:
    """Load all shared rules from rules/ directory.

    Returns:
        Dict with rule name as key and rule content as value
    """
    rules_dir = PROMPTS_DIR / "rules"
    rules = {}

    if rules_dir.exists():
        for path in rules_dir.glob("*.md"):
            name = path.stem
            try:
                rules[name] = path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to load rule %s: %s", name, e)

    return rules


def list_skills() -> list[str]:
    """List available skills.

    Returns:
        List of skill names
    """
    skills_dir = PROMPTS_DIR / "skills"
    if not skills_dir.exists():
        return []
    return [d.name for d in skills_dir.iterdir() if d.is_dir()]


def list_components(skill: str) -> list[str]:
    """List components for a skill.

    Args:
        skill: Skill name

    Returns:
        List of component names (without .md extension)
    """
    skill_dir = PROMPTS_DIR / "skills" / skill
    if not skill_dir.exists():
        return []
    return [f.stem for f in skill_dir.glob("*.md")]
