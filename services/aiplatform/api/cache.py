"""Cache API — Cache stats and management.

Migrated from backend/api/admin.py (cache section).
"""

import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stats")
def get_cache_stats():
    """Get cache statistics."""
    try:
        from services.shared.common.ttl_cache import (
            datasource_cache, menu_cache, dashboard_cache, brand_cache, metadata_cache,
        )
        return {
            "caches": [
                datasource_cache.stats(),
                menu_cache.stats(),
                dashboard_cache.stats(),
                brand_cache.stats(),
                metadata_cache.stats(),
            ]
        }
    except Exception as e:
        logger.error("Get cache stats failed: %s", e)
        return {"caches": [], "error": str(e)}


@router.post("/clear")
def clear_all_caches():
    """Clear all caches."""
    try:
        from services.shared.common.ttl_cache import (
            datasource_cache, menu_cache, dashboard_cache, brand_cache, metadata_cache,
        )
        datasource_cache.invalidate()
        menu_cache.invalidate()
        dashboard_cache.invalidate()
        brand_cache.invalidate()
        metadata_cache.invalidate()
        return {"success": True, "message": "All caches cleared"}
    except Exception as e:
        logger.error("Clear caches failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
