"""Agent Prompt Loader — loads agent configs and prompts from files.

Reads from config/agents/ directory structure:
  {agent_name}/skill.yaml  — metadata (name, description, datasource_type, route_patterns, max_retries)
  {agent_name}/system.md   — system prompt

Loading priority (per field):
  max_retries:  DB config override > skill.yaml > rules.md default
  description:  skill.yaml > DB
  system_prompt: system.md > DB
  route_patterns: skill.yaml only (not in DB)
  is_active, datasource_ids, mcp_server_ids: DB only
"""

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

AGENTS_DIR = Path(__file__).parent / "agents"

# Global defaults from rules.md
_DEFAULT_MAX_RETRIES = 2


def load_agent_skill(agent_name: str) -> Optional[dict]:
    """Load agent skill.yaml from file."""
    path = AGENTS_DIR / agent_name / "skill.yaml"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.warning("Failed to load agent skill %s: %s", agent_name, e)
        return None


def load_agent_prompt(agent_name: str) -> Optional[str]:
    """Load agent system.md from file."""
    path = AGENTS_DIR / agent_name / "system.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to load agent prompt %s: %s", agent_name, e)
        return None


def load_orchestrator_rules() -> str:
    """Load orchestrator rules.md."""
    path = AGENTS_DIR / "orchestrator" / "rules.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to load orchestrator rules: %s", e)
        return ""


def get_max_retries(agent_name: str, db_override: int = None) -> int:
    """Get max_retries with priority: DB override > skill.yaml > rules.md default.

    Args:
        agent_name: Agent name for loading skill.yaml
        db_override: Value from DB config (None = not set)

    Returns:
        Effective max_retries value
    """
    # DB override takes highest priority
    if db_override is not None:
        return db_override

    # skill.yaml
    skill = load_agent_skill(agent_name)
    if skill and "max_retries" in skill:
        return skill["max_retries"]

    # Global default
    return _DEFAULT_MAX_RETRIES


def get_route_patterns(agent_name: str) -> list[str]:
    """Get route_patterns from skill.yaml."""
    skill = load_agent_skill(agent_name)
    if skill:
        return skill.get("route_patterns", [])
    return []


def list_agent_dirs() -> list[str]:
    """List all agent directory names (excluding orchestrator)."""
    if not AGENTS_DIR.exists():
        return []
    return [d.name for d in AGENTS_DIR.iterdir() if d.is_dir() and d.name != "orchestrator"]


def build_agent_graph(agent_tools_map: dict, current_ds_info: dict = None) -> str:
    """Build agent graph from file configs, including input requirements."""
    lines = ["### 可用子 Agent"]
    lines.append("")

    for tname, agent in agent_tools_map.items():
        agent_name = tname.replace("agent__", "")
        skill = load_agent_skill(agent_name)
        description = skill.get("description", agent.description) if skill else agent.description
        ds_type = skill.get("datasource_type", "全部") if skill else "全部"

        lines.append(f"#### {tname}")
        lines.append(f"- **能力**: {description}")
        lines.append(f"- **数据源类型**: {ds_type}")

        # Show input requirements from skill.yaml
        input_schema = skill.get("input_schema", {}) if skill else {}
        required = input_schema.get("required", [])
        optional = input_schema.get("optional", [])

        if required or optional:
            lines.append(f"- **入参要求**:")
            for field in required:
                fmt = field.get("format", "")
                desc = field.get("description", "")
                lines.append(f"  - **{field['field']}**（必须）: {desc}，格式：`{fmt}`")
            for field in optional:
                fmt = field.get("format", "")
                desc = field.get("description", "")
                lines.append(f"  - {field['field']}（可选）: {desc}，格式：`{fmt}`")
        lines.append("")

    if current_ds_info:
        lines.append(f"**当前数据源**: {current_ds_info.get('name', '未指定')} ({current_ds_info.get('db_type', '')})")
    else:
        lines.append("**当前数据源**: 未指定")

    return "\n".join(lines)
