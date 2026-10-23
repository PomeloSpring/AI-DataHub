"""Skill Loader — 技能唯一事实源是本地文件夹 config/skills/{name}/SKILL.md。

Skills are prompt templates that agents can dynamically load.
系统技能与自定义技能均落盘为 Qoder 规范 SKILL.md（YAML frontmatter + Markdown 正文）。

历史上还存在一层“DB 覆盖”（adh_skills 表，DB > File）已**退役删除**：
  * 全仓无任何建表迁移、无任何生产写入（保存走 save_skill_folder 落盘），
    旧读路径只会每次技能加载刷一条 `Table 'adh2.adh_skills' doesn't exist` WARNING；
  * 更危害的是“DB 遮蔽文件”：若某环境手建过该表并残留旧行，改 SKILL.md 会静默不生效。
单一事实源不得有隐形的第二份：残留数据由 `warn_legacy_db_skills()` 在服务启动时**显式告警**，
而不是悄悄拿它去覆盖文件（见 no-silent-degradation 规范）。

A TTL cache (default 60s) enables dynamic hot-reload: SKILL.md 修改
在 60 秒内生效，无需重启服务。

两个**可选** frontmatter 键供执行层控制正文装载量（消费方 = `prompt_composer._select_skill_bodies`，
由 `as_bots.load_skills` 透出）：
  * `requires_any: [tool_name, ...]` —— 本技能的作业细则依赖哪些工具（取 `mcp__<server>__<tool>`
    的 `tool` 段）；本会话工具清单一个都没注册时不展开正文（不向模型承诺它没有的能力）。
  * `triggers: [词, ...]` —— 用户问题命中词；一个 AS-BOT 绑定多个技能时只展开命中的那几个正文。
未命中/未展开不静默消失：技能仍以 `description`（浓缩契约）出现在提示词的「已绑定技能」清单里并标原因。
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


def legacy_db_skill_names() -> Optional[list[str]]:
    """读已退役的 adh_skills 表中仍标为生效的行名。

    Returns:
        None  —— 表不存在（标准退役态，不是故障）；
        list  —— 表存在时的生效技能名（可能为空）。
    只用于启动对账告警，**不得**被任何加载路径消费。
    """
    from backend.common.db.metadata_db import get_metadata_conn

    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM adh_skills WHERE is_active = 1 ORDER BY name")
                return [r["name"] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        if _is_missing_table_error(e):
            return None
        logger.warning("[skills] 遗留 adh_skills 对账查询失败（不影响技能加载）: %s", e)
        return None


def _is_missing_table_error(exc: Exception) -> bool:
    """MySQL 1146 (table doesn't exist) 判定：表缺失是退役后的常态，不当故障报。"""
    args = getattr(exc, "args", ())
    return (len(args) >= 1 and args[0] == 1146) or "doesn't exist" in str(exc)


def warn_legacy_db_skills() -> Optional[list[str]]:
    """服务启动对账：已退役的 DB 技能覆盖层若还残留生效数据，必须显式告警。

    这些行**不再被读取**（文件是唯一事实源），静默丢弃会让用户发现“改了 SKILL.md 不生效”
    而查不到原因，因此这里把名字列出来提醒迁移或清理。表不存在返回 None。
    """
    names = legacy_db_skill_names()
    if names:
        logger.warning(
            "[skills] 发现遗留表 adh_skills 中 %d 条生效技能 %s：DB 覆盖层已退役，"
            "它们**不再生效**；如需保留请迁移到 backend/modules/mind/config/skills/{name}/SKILL.md，"
            "否则请归档后删除该行。", len(names), names)
    return names


def load_skill(skill_name: str) -> Optional[dict]:
    """Load a skill by name (cached, TTL 60s). 唯一来源: config/skills/{name}/SKILL.md。

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
    """Load a skill by name from the file system (single source of truth)."""
    file_skill = _load_skill_from_file(skill_name)
    if file_skill:
        logger.debug("Loaded skill %s from file", skill_name)
        return file_skill
    return None


def list_skills(category: str = None) -> list[dict]:
    """List all skills (config/skills/ 文件夹), cached with TTL 60s.

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
    """List all skills from the file system (single source of truth).

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

    # 2. Filter by category if specified
    result = list(skills.values())
    if category:
        result = [s for s in result if s.get("category") == category]

    # 3. Sort: system first, then by name
    result.sort(key=lambda s: (s.get("source_type", "") != "system", s.get("name", "")))

    # 4. Remove system_prompt from listing
    for s in result:
        s.pop("system_prompt", None)

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
    覆盖前自动将旧版快照到 versions/SKILL.<ts>.md(保留最近 20 份) —— 技能版本管理唯一入口。
    """
    if not is_valid_skill_name(name):
        raise ValueError("技能名仅允许字母/数字/下划线/连字符")
    skill_dir = SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    _snapshot_skill_version(skill_dir)
    meta = {"name": name}
    # 保留旧 frontmatter 里本函数不纳管的键(如 `triggers` / `requires_any` 这类装载控制
    # 元数据): 管理页只编辑展示字段, 擅自剥掉会让技能正文静默停止展开(改了不生效且
    # 无线索), 与本模块“单一事实源不得有隐形第二份”的口径一致。
    existing_md = skill_dir / "SKILL.md"
    if existing_md.exists():
        try:
            prev_meta, _ = _parse_skill_md(existing_md.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("save_skill_folder: %s 旧 frontmatter 解析失败，本次不回带未纳管键: %s", name, e)
            prev_meta = {}
        managed = {"name", "display_name", "description", "category", "source"}
        meta.update({k: v for k, v in (prev_meta or {}).items() if k not in managed})
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


# ── 技能版本快照(versions/SKILL.<UTC ts>.md, 保留最近 20 份) ─────────

_VERSION_KEEP = 20
_VERSION_RE = re.compile(r"^\d{8}-\d{6}$")  # 路径穿越防护: 版本号仅允许时间戳格式


def _versions_dir(skill_dir: Path) -> Path:
    return skill_dir / "versions"


def _snapshot_skill_version(skill_dir: Path) -> Optional[str]:
    """覆盖保存前把现存 SKILL.md 快照进 versions/; 无现存文件返回 None。超量自动淘汰最旧。"""
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return None
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    vdir = _versions_dir(skill_dir)
    vdir.mkdir(exist_ok=True)
    shutil.copy2(md, vdir / f"SKILL.{ts}.md")
    files = sorted(vdir.glob("SKILL.*.md"))
    if len(files) > _VERSION_KEEP:
        for old in files[: len(files) - _VERSION_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
    return ts


def list_skill_versions(name: str) -> list[dict]:
    """版本列表(新→旧): version=UTC 时间戳, preview 供前端展示差异线索。"""
    if not is_valid_skill_name(name):
        return []
    vdir = _versions_dir(SKILLS_DIR / name)
    if not vdir.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(vdir.glob("SKILL.*.md"), reverse=True):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        out.append({
            "version": f.stem[len("SKILL."):],
            "size": f.stat().st_size,
            "preview": text[:200],
        })
    return out


def rollback_skill_version(name: str, version: str) -> bool:
    """回滚到指定快照: 先把当前版快照,再用历史内容覆写 SKILL.md。"""
    if not is_valid_skill_name(name) or not _VERSION_RE.match(str(version or "")):
        return False
    skill_dir = SKILLS_DIR / name
    src = _versions_dir(skill_dir) / f"SKILL.{version}.md"
    if not src.exists():
        return False
    _snapshot_skill_version(skill_dir)
    shutil.copy2(src, skill_dir / "SKILL.md")
    clear_skill_cache()
    return True


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
