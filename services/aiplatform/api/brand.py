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
}


class BrandSettingsUpdate(BaseModel):
    app_name: Optional[str] = None
    logo_url: Optional[str] = None
    show_icon: Optional[bool] = None
    show_text: Optional[bool] = None


def _load_brand_settings() -> dict:
    """Load brand settings from MySQL (adh_brand_settings single-row table)."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT app_name, logo_url, show_icon, show_text "
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
    }


def _save_brand_settings(settings: dict):
    """Save brand settings to MySQL (upsert single-row)."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_brand_settings (id, app_name, logo_url, show_icon, show_text) "
                "VALUES (1, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "app_name = VALUES(app_name), logo_url = VALUES(logo_url), "
                "show_icon = VALUES(show_icon), show_text = VALUES(show_text)",
                (
                    settings.get("app_name", _DEFAULT_BRAND["app_name"]),
                    settings.get("logo_url", ""),
                    int(settings.get("show_icon", True)),
                    int(settings.get("show_text", True)),
                ),
            )
        conn.commit()
    finally:
        conn.close()


@router.get("/")
def get_brand_settings():
    """Get brand settings (public, no auth required)."""
    return _load_brand_settings()


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
        _save_brand_settings(current)
        return current
    except Exception as e:
        logger.error("Update brand settings failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
