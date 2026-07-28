"""Prompt Loader — load prompts from files with DB fallback.

Loading priority:
1. Database (adh_prompts table) — for dynamic updates
2. File system (config/ directory) — for version control

Usage:
    from backend.config.loader import load_prompt

    # Load a specific prompt
    system_prompt = load_prompt("nl2sql", "system")
    rules = load_prompt("nl2sql", "rules")
    
    # Load with dialect
    dialect_rules = load_prompt("nl2sql", "dialects/mysql")
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Base directory for prompt files
PROMPTS_DIR = Path(__file__).parent


def load_prompt(skill: str, component: str, dialect: str = None) -> Optional[str]:
    """Load a prompt from file system.

    Args:
        skill: Skill name (e.g., 'nl2sql', 'chart', 'analysis')
        component: Component name (e.g., 'system', 'rules', 'examples')
        dialect: Optional dialect name (e.g., 'mysql', 'doris')

    Returns:
        Prompt text or None if not found
    """
    # Build path
    if dialect:
        path = PROMPTS_DIR / "skills" / skill / "dialects" / f"{dialect}.md"
    elif component:
        path = PROMPTS_DIR / "skills" / skill / f"{component}.md"
    else:
        return None

    # Try to load from file
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
            logger.debug("Loaded prompt from %s", path)
            return content
        except Exception as e:
            logger.warning("Failed to load prompt from %s: %s", path, e)

    # Fallback to rules directory for shared rules
    if component and not dialect:
        rules_path = PROMPTS_DIR / "rules" / f"{component}.md"
        if rules_path.exists():
            try:
                content = rules_path.read_text(encoding="utf-8")
                logger.debug("Loaded rule from %s", rules_path)
                return content
            except Exception as e:
                logger.warning("Failed to load rule from %s: %s", rules_path, e)

    logger.debug("Prompt not found: skill=%s, component=%s, dialect=%s", skill, component, dialect)
    return None


def load_nl2sql_prompt(dialect: str = None) -> dict:
    """Load all NL2SQL prompt components.

    Args:
        dialect: Database dialect (e.g., 'mysql', 'doris')

    Returns:
        Dict with 'system', 'rules', 'examples', 'dialect' keys
    """
    return {
        "system": load_prompt("nl2sql", "system"),
        "rules": load_prompt("nl2sql", "rules"),
        "examples": load_prompt("nl2sql", "examples"),
        "dialect": load_prompt("nl2sql", None, dialect=dialect) if dialect else None,
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
