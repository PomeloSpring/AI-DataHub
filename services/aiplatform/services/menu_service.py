"""Menu Service — Menu tree management (analysis + screen menus).

Migrated from backend/api/admin.py (menu sections).
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Menu file paths — relative to project root
_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
_ANALYSIS_MENU_PATH = _DATA_DIR / "analysis_menu.json"
_SCREEN_MENU_PATH = _DATA_DIR / "screen_menu.json"


def _load_json(path: Path) -> list:
    """Load a JSON file, return empty list on failure."""
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_json(path: Path, items: list):
    """Save items to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


# ── Analysis Menu ──────────────────────────────────────────────────

def get_analysis_menu() -> list:
    """Get analysis menu items."""
    return _load_json(_ANALYSIS_MENU_PATH)


def update_analysis_menu(items: list) -> list:
    """Update analysis menu items. Validates each item has name and dashboard_id."""
    for item in items:
        if not item.get("name"):
            raise ValueError("Menu item name cannot be empty")
        if not item.get("dashboard_id"):
            raise ValueError("Please select an associated dashboard")
    _save_json(_ANALYSIS_MENU_PATH, items)
    return items


# ── Screen Menu ────────────────────────────────────────────────────

def get_screen_menu() -> list:
    """Get screen menu items."""
    return _load_json(_SCREEN_MENU_PATH)


def update_screen_menu(items: list) -> list:
    """Update screen menu items. Validates each item has name and dashboard_id."""
    for item in items:
        if not item.get("name"):
            raise ValueError("Menu item name cannot be empty")
        if not item.get("dashboard_id"):
            raise ValueError("Please select an associated dashboard")
    _save_json(_SCREEN_MENU_PATH, items)
    return items
