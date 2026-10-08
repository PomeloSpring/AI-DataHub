"""Menu item database model — adjacency list tree."""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

# Reuse the existing engine/session from the app
# This model is used by the menu API via raw SQL (same pattern as other admin APIs)
# No ORM migration needed — table created by create_tables.sql

MENU_ITEM_COLUMNS = (
    "id", "parent_id", "name", "icon", "page_id",
    "is_system", "sort_order", "created_at", "updated_at",
)
