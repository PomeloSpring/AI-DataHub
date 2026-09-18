"""Skill Loader — loads skill configs from files and DB with priority merging.

Skills are prompt templates that agents can dynamically load.
System skills live in config/skills/{name}/ (skill.yaml + system.md).
User-created skills live in adh_skills table.

Loading priority:
  DB (adh_skills, is_active=1) > File (config/skills/)
  User-created skills with the same name override system file skills.

A TTL cache (default 60s) enables dynamic hot-reload: DB skill edits take
effect within 60 seconds without restarting the service.
"""

import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent / "skills"

# ── Hot-reload TTL cache ───────────────────────────────────────────────
_SKILL_CACHE_TTL = float(os.getenv("SKILL_CACHE_TTL", "60"))  # seconds
_skill_cache: dict[str, tuple[float, Optional[dict]]] = {}
_list_cache: dict[str, tuple[float, list]] = {}


def clear_skill_cache():
    """Invalidate all skill caches (single skills + listing)."""
    _skill_cache.clear()
    _list_cache.clear()


def _cache_get(cache: dict, key: str):
    item = cache.get(key)
    if item is None:
        return False, None
    ts, value = item
    if time.time() - ts > _SKILL_CACHE_TTL:
        cache.pop(key, None)
        return False, None
    return True, value


def _parse_skill_md(text: str) -> tuple[dict, str]:
    """解析 Qoder 规范 SKILL.md:YAML frontmatter + Markdown 正文.

    Returns (meta, body);无 frontmatter 时 meta={} 且 body=原文。
    """
    if not text.startswith("---"):
        return {}, text.strip()
    # 关闭分隔线:第二个独占一行的 ---
    lines = text.splitlines()
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end < 0:
        return {}, text.strip()
    front = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:]).strip()
    try:
        meta = yaml.safe_load(front) or {}
    except Exception:  # noqa: BLE001
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, body


def _load_skill_from_file(skill_name: str) -> Optional[dict]:
    """Load skill from config/skills/{name}/ directory.

    优先 Qoder 规范 SKILL.md(frontmatter + 正文);兼容旧版 skill.yaml + system.md。
    """
    skill_dir = SKILLS_DIR / skill_name
    if not skill_dir.exists():
        return None

    result = {"name": skill_name, "source_type": "system", "is_builtin": True}

    # 1. Qoder 规范:SKILL.md
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        try:
            meta, body = _parse_skill_md(skill_md.read_text(encoding="utf-8"))
            result["display_name"] = meta.get("display_name") or meta.get("name") or skill_name
            result["description"] = meta.get("description", "")
            result["category"] = meta.get("category", "")
            result["skill_config"] = meta
            result["system_prompt"] = body
            result["format"] = "skill_md"
            # frontmatter source: custom 标记为自定义技能(非内置)
            result["is_builtin"] = (meta.get("source") or "builtin") != "custom"
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load SKILL.md %s: %s", skill_name, e)
        return result

    # 2. 兼容旧版:skill.yaml + system.md
    yaml_path = skill_dir / "skill.yaml"
    if yaml_path.exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                meta = yaml.safe_load(f) or {}
            result["display_name"] = meta.get("display_name", skill_name)
            result["description"] = meta.get("description", "")
            result["category"] = meta.get("category", "")
            result["skill_config"] = meta
            result["is_builtin"] = (meta.get("source") or "builtin") != "custom"
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
    """Load a skill by name (cached, TTL 60s). DB (user-created) beats file (system).

    Returns:
        Dict with keys: name, display_name, description, category, system_prompt,
                        skill_config, source_type
        None if not found
    """
    hit, cached = _cache_get(_skill_cache, skill_name)
    if hit:
        return cached
    result = _load_skill_uncached(skill_name)
    _skill_cache[skill_name] = (time.time(), result)
    return result


def _load_skill_uncached(skill_name: str) -> Optional[dict]:
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
    """List all skills (file + DB merged), cached with TTL 60s.

    Args:
        category: Optional filter by category (e.g., 'analysis')

    Returns:
        List of skill dicts with metadata (no system_prompt content)
    """
    cache_key = category or "__all__"
    hit, cached = _cache_get(_list_cache, cache_key)
    if hit:
        return cached
    result = _list_skills_uncached(category)
    _list_cache[cache_key] = (time.time(), result)
    return result


def _list_skills_uncached(category: str = None) -> list[dict]:
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
                if not skill:
                    continue
                # 管道内部 prompt 资产(config/skills/{nl2sql,chart,analysis,prediction}/
                # {system,rules,examples,dialects}.md 由 config.loader 按提示词组件加载)
                # 不是可绑定技能: 旧格式 skill.yaml 且无 category 的文件夹不在 Skills 列表暴露。
                if skill.get("format") != "skill_md" and not skill.get("category"):
                    continue
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


# ── 文件夹技能写操作(自定义 skills 以 Qoder 规范 SKILL.md 落盘) ──────────

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_valid_skill_name(name: str) -> bool:
    return bool(name) and bool(_NAME_RE.match(name))


def skill_exists(name: str) -> bool:
    return (SKILLS_DIR / name / "SKILL.md").exists() or (SKILLS_DIR / name / "skill.yaml").exists()


def save_skill_folder(
    name: str,
    *,
    display_name: str = "",
    description: str = "",
    category: str = "custom",
    body: str = "",
    custom: bool = True,
) -> Path:
    """创建/更新一个文件夹技能,写为 Qoder 规范 SKILL.md(frontmatter + 正文).

    custom=True 时 frontmatter 标记 source: custom(区别于系统内置)。
    """
    if not is_valid_skill_name(name):
        raise ValueError("技能名仅允许字母/数字/下划线/连字符")
    skill_dir = SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    meta = {"name": name}
    if display_name:
        meta["display_name"] = display_name
    if description:
        meta["description"] = description
    if category:
        meta["category"] = category
    if custom:
        meta["source"] = "custom"
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    content = f"---\n{front}\n---\n\n{body.strip()}\n"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    clear_skill_cache()
    return skill_dir


def delete_skill_folder(name: str) -> bool:
    """删除一个自定义(非内置)文件夹技能。内置技能拒绝删除。"""
    if not is_valid_skill_name(name):
        return False
    skill = _load_skill_from_file(name)
    if skill and skill.get("is_builtin"):
        raise PermissionError("系统内置技能不可删除")
    skill_dir = SKILLS_DIR / name
    if skill_dir.exists() and skill_dir.is_dir():
        shutil.rmtree(skill_dir)
        clear_skill_cache()
        return True
    return False


def read_skill_markdown(name: str) -> Optional[str]:
    """读取技能的 SKILL.md 原文(供编辑表单回填);若仅旧版则合成 frontmatter + system.md。"""
    skill_dir = SKILLS_DIR / name
    md = skill_dir / "SKILL.md"
    if md.exists():
        return md.read_text(encoding="utf-8")
    yaml_path, sys_path = skill_dir / "skill.yaml", skill_dir / "system.md"
    if yaml_path.exists() or sys_path.exists():
        meta = {}
        if yaml_path.exists():
            try:
                meta = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                meta = {}
        body = sys_path.read_text(encoding="utf-8") if sys_path.exists() else ""
        front = yaml.safe_dump({"name": name, **meta}, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{front}\n---\n\n{body}\n"
    return None
