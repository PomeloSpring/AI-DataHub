"""Model Lab API — Embedding model testing and vector search debugging.

Migrated from backend/api/model_lab.py
"""

import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.db import DBConnection, execute_query

logger = logging.getLogger(__name__)
router = APIRouter()


class EmbedRequest(BaseModel):
    text: str


class SearchRequest(BaseModel):
    text: str
    datasource_id: int = 0
    limit: int = 10


@router.get("/info")
def model_info():
    """Get current embedding model status."""
    try:
        from services.shared.common.llm.embedding import get_model_info, _EMBED_CACHE
        info = get_model_info()
        info["cache_size"] = len(_EMBED_CACHE)
        return info
    except Exception as e:
        logger.error("Get model info failed: %s", e)
        return {"status": "error", "message": str(e)}


@router.post("/embed")
def embed_text(req: EmbedRequest):
    """Generate embedding for input text."""
    try:
        if not req.text.strip():
            raise HTTPException(status_code=400, detail="text cannot be empty")

        from services.shared.common.llm.embedding import generate_embedding, get_model_info

        start = time.time()
        vec = generate_embedding(req.text)
        elapsed_ms = round((time.time() - start) * 1000, 2)

        nonzero_count = sum(1 for v in vec if v != 0.0)

        return {
            "vector": vec[:50],  # Preview first 50
            "vector_full": vec,
            "dim": len(vec),
            "stats": {
                "nonzero": nonzero_count,
                "min": min(vec) if vec else 0,
                "max": max(vec) if vec else 0,
                "mean": sum(vec) / len(vec) if vec else 0,
            },
            "elapsed_ms": elapsed_ms,
            "model": get_model_info(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Embed failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
def vector_search(req: SearchRequest):
    """Run vector search with execution details."""
    try:
        from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal
        from services.shared.common.db.metadata_db import get_vector_connection

        start = time.time()

        # Generate embedding
        vec = generate_embedding(req.text)
        vec_literal = embedding_to_sql_literal(vec)

        # Build query
        where = "WHERE is_active = 1"
        params = []
        if req.datasource_id:
            where += " AND datasource_id = %s"
            params.append(req.datasource_id)

        sql = f"""
            SELECT id, table_name, table_comment, keywords,
                   l2_distance_approximate(embedding, {vec_literal}) AS distance
            FROM adh_table_info
            {where}
            ORDER BY distance ASC
            LIMIT %s
        """
        params.append(req.limit)

        with get_vector_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                results = cur.fetchall()

        elapsed_ms = round((time.time() - start) * 1000, 2)

        for r in results:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif hasattr(v, "__float__"):
                    r[k] = float(v)

        return {
            "results": results,
            "count": len(results),
            "elapsed_ms": elapsed_ms,
            "query_embedding_dim": len(vec),
        }
    except Exception as e:
        logger.error("Vector search failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reload")
def reload_model():
    """Reload embedding model."""
    try:
        from services.shared.common.llm.embedding import reload_model as _reload
        _reload()
        from services.shared.common.llm.embedding import get_model_info
        return {"success": True, "model": get_model_info()}
    except Exception as e:
        logger.error("Reload model failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
