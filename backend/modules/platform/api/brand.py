"""Brand API — Brand settings management.

Migrated from file-based storage (data/brand_settings.json) to MySQL
for distributed service consistency. All instances share the same row
via METADATA_DB connection pool.

Table: adh_brand_settings (single-row, id=1)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_BRAND = {
    "app_name": "AI-DataHub",
    "logo_url": "",
    "show_icon": True,
    "show_text": True,
    # 品牌标语：登录页名称下方那行字。与 app_name 不同，空串是合法取值（= 不显示），
    # 只在「库里还没有这一行/还没有这一列」时回落默认文案。
    "tagline": "企业级智能数据分析平台",
}


class BrandSettingsUpdate(BaseModel):
    app_name: Optional[str] = None
    logo_url: Optional[str] = None
    show_icon: Optional[bool] = None
    show_text: Optional[bool] = None
    tagline: Optional[str] = None


def _load_brand_settings() -> dict:
    """Load brand settings from MySQL (adh_brand_settings single-row table)."""
    from backend.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT app_name, logo_url, show_icon, show_text, tagline "
                "FROM adh_brand_settings WHERE id = 1"
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return dict(_DEFAULT_BRAND)

    return {
        "app_name": row.get("app_name") or _DEFAULT_BRAND["app_name"],
        "logo_url": row.get("logo_url") or "",
        "show_icon": bool(row.get("show_icon", 1)),
        "show_text": bool(row.get("show_text", 1)),
        # 空串按「管理员显式清空」处理，不回落默认文案（回落会把已清空的标语又顶回去）
        "tagline": row.get("tagline") if row.get("tagline") is not None else _DEFAULT_BRAND["tagline"],
    }


def _save_brand_settings(settings: dict):
    """Save brand settings to MySQL (upsert single-row)."""
    from backend.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_brand_settings (id, app_name, logo_url, show_icon, show_text, tagline) "
                "VALUES (1, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "app_name = VALUES(app_name), logo_url = VALUES(logo_url), "
                "show_icon = VALUES(show_icon), show_text = VALUES(show_text), "
                "tagline = VALUES(tagline)",
                (
                    settings.get("app_name", _DEFAULT_BRAND["app_name"]),
                    settings.get("logo_url", ""),
                    int(settings.get("show_icon", True)),
                    int(settings.get("show_text", True)),
                    settings.get("tagline", _DEFAULT_BRAND["tagline"]),
                ),
            )
        conn.commit()
    finally:
        conn.close()


@router.get("")
@router.get("/")
def get_brand_settings():
    """Get brand settings (public: 登录页在拿到身份之前就要按品牌设置显示名称/图标/标语，
    故本读接口匿名可访问，路径已登记 api_permission._PUBLIC_ROUTES；写接口仍按权限码门控)。"""
    from backend.common.ttl_cache import brand_cache
    cached = brand_cache.get("settings")
    if cached is not None:
        return cached
    result = _load_brand_settings()
    brand_cache.set("settings", result)
    return result


@router.put("")
@router.put("/")
def update_brand_settings(req: BrandSettingsUpdate):
    """Update brand settings (admin only)."""
    try:
        current = _load_brand_settings()
        if req.app_name is not None:
            current["app_name"] = req.app_name
        if req.logo_url is not None:
            current["logo_url"] = req.logo_url
        if req.show_icon is not None:
            current["show_icon"] = req.show_icon
        if req.show_text is not None:
            current["show_text"] = req.show_text
        if req.tagline is not None:
            current["tagline"] = req.tagline.strip()
        _save_brand_settings(current)
        from backend.common.ttl_cache import brand_cache
        brand_cache.invalidate("settings")
        return current
    except Exception as e:
        logger.error("Update brand settings failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
