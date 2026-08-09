"""Brand API — Brand settings management.

Migrated from backend/api/admin.py (brand section).
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

# Brand settings file path
_BRAND_SETTINGS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "brand_settings.json"

_DEFAULT_BRAND = {
    "app_name": "ChatBI",
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
    """Load brand settings from file."""
    if _BRAND_SETTINGS_PATH.exists():
        try:
            with open(_BRAND_SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            return {**_DEFAULT_BRAND, **saved}
        except Exception:
            pass
    return dict(_DEFAULT_BRAND)


def _save_brand_settings(settings: dict):
    """Save brand settings to file."""
    _BRAND_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_BRAND_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


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
