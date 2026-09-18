"""Skills Admin API — 以 Qoder 规范「文件夹 + SKILL.md」管理技能.

数据来源(全部本地文件夹,合并 DB 覆盖项):
- 系统内置:services/datamind/config/skills/{name}/(随仓库发布,只读展示)
- 自定义:  通过本 API 创建,落盘为 config/skills/{name}/SKILL.md(frontmatter source: custom)

供前端 Skills 独立菜单与 Waker「按名勾选绑定技能」使用:
- GET    /admin/skills           列表(含 is_builtin,不含正文)
- GET    /admin/skills/{name}    详情(含正文与 SKILL.md 原文)
- POST   /admin/skills           新建自定义技能(写 SKILL.md 文件夹)
- PUT    /admin/skills/{name}    更新自定义技能
- DELETE /admin/skills/{name}    删除自定义技能(内置拒绝)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.datamind.config import skill_loader

logger = logging.getLogger(__name__)
router = APIRouter()


class SkillUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    display_name: str = ""
    description: str = ""
    category: str = "custom"
    system_prompt: str = ""  # 即 SKILL.md 正文


def _brief(skill: dict) -> dict:
    return {
        "name": skill.get("name"),
        "display_name": skill.get("display_name") or skill.get("name"),
        "description": skill.get("description") or "",
        "category": skill.get("category") or "",
        "source_type": skill.get("source_type") or "system",
        "is_builtin": bool(skill.get("is_builtin", skill.get("source_type") == "system")),
        "format": skill.get("format") or "",
    }


@router.get("/admin/skills")
def list_admin_skills(category: str = Query("")):
    """列出全部技能(系统内置 + 自定义),供 Skills 菜单与 Waker 绑定下拉."""
    try:
        skills = skill_loader.list_skills(category=category or None)
        items = [_brief(s) for s in skills]
        # 内置优先、再按名称排序
        items.sort(key=lambda s: (not s["is_builtin"], s["name"] or ""))
        return items
    except Exception as e:  # noqa: BLE001
        logger.error("List admin skills failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/skills/{name}")
def get_admin_skill(name: str):
    skill = skill_loader.load_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 '{name}' 不存在")
    out = _brief(skill)
    out["system_prompt"] = skill.get("system_prompt") or ""
    out["markdown"] = skill_loader.read_skill_markdown(name) or ""
    return out


@router.post("/admin/skills")
def create_admin_skill(req: SkillUpsert):
    if not skill_loader.is_valid_skill_name(req.name):
        raise HTTPException(status_code=400, detail="技能名仅允许字母/数字/下划线/连字符")
    if skill_loader.skill_exists(req.name):
        raise HTTPException(status_code=400, detail=f"技能 '{req.name}' 已存在")
    try:
        skill_loader.save_skill_folder(
            req.name, display_name=req.display_name, description=req.description,
            category=req.category, body=req.system_prompt, custom=True,
        )
        return {"name": req.name, "success": True}
    except Exception as e:  # noqa: BLE001
        logger.error("Create skill failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/admin/skills/{name}")
def update_admin_skill(name: str, req: SkillUpsert):
    skill = skill_loader.load_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 '{name}' 不存在")
    if skill.get("is_builtin"):
        raise HTTPException(status_code=400, detail="系统内置技能不可直接编辑,请复制为自定义后修改")
    try:
        skill_loader.save_skill_folder(
            name, display_name=req.display_name, description=req.description,
            category=req.category, body=req.system_prompt, custom=True,
        )
        return {"name": name, "success": True}
    except Exception as e:  # noqa: BLE001
        logger.error("Update skill failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/admin/skills/{name}")
def delete_admin_skill(name: str):
    try:
        deleted = skill_loader.delete_skill_folder(name)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 同步清理可能存在的 DB 覆盖行
    _delete_db_skill(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"技能 '{name}' 不存在")
    return {"name": name, "success": True}


def _delete_db_skill(name: str) -> None:
    try:
        from services.shared.common.db import execute_write

        execute_write("UPDATE adh_skills SET is_active = 0 WHERE name = %s", (name,))
    except Exception as e:  # noqa: BLE001  表缺失等情况不影响文件夹删除
        logger.debug("soft-delete DB skill %s skipped: %s", name, e)
