"""Model Lab API — Embedding model testing and vector search debugging.

Provides endpoints to:
- Get current embedding model status
- Generate embeddings with execution step details
- Run vector search with execution step details
- Reload/switch embedding model
"""

import logging
import time
from typing import Optional

import pymysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.auth import get_current_user, require_admin
from backend.models.schemas import UserInfo
from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)
from backend.common.db.metadata_db import get_metadata_conn, get_vector_conn
from backend.common.llm.embedding import (
    generate_embedding, embedding_to_sql_literal, get_model_info, reload_model,
    _EMBED_CACHE, _get_model,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_metadata_conn():
    """Get a connection from the pool."""
    return get_metadata_conn()


# ── Request/Response models ─────────────────────────────────────────

class EmbedRequest(BaseModel):
    text: str

class SearchRequest(BaseModel):
    text: str
    datasource_id: int = 0
    limit: int = 10

class ReloadRequest(BaseModel):
    model_path: Optional[str] = None


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/info")
def model_info(user: UserInfo = Depends(get_current_user)):
    """Get current embedding model status."""
    info = get_model_info()
    info["cache_size"] = len(_EMBED_CACHE)
    return info


@router.post("/embed")
def embed_text(req: EmbedRequest, user: UserInfo = Depends(get_current_user)):
    """Generate embedding for input text with execution step details."""
    steps = []
    total_start = time.time()

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text cannot be empty")

    # Step 1: Check cache
    t0 = time.time()
    cache_hit = req.text in _EMBED_CACHE
    steps.append({
        "name": "check_cache",
        "duration_ms": round((time.time() - t0) * 1000, 2),
        "detail": "cache hit" if cache_hit else "cache miss",
    })

    # Step 2: Load model (if not cached)
    t0 = time.time()
    model = _get_model()
    model_load_ms = round((time.time() - t0) * 1000, 2)
    if model_load_ms > 0.1 or not cache_hit:
        steps.append({
            "name": "load_model",
            "duration_ms": model_load_ms,
            "detail": get_model_info()["model_path"] if model else "hash fallback",
        })

    # Step 3: Generate embedding
    t0 = time.time()
    vec = generate_embedding(req.text)
    embed_ms = round((time.time() - t0) * 1000, 2)
    steps.append({
        "name": "generate_embedding",
        "duration_ms": embed_ms,
        "detail": f"{len(vec)}-dim, {'model' if model else 'hash fallback'}",
    })

    # Step 4: Compute stats
    t0 = time.time()
    nonzero_count = sum(1 for v in vec if v != 0.0)
    vec_min = min(vec) if vec else 0
    vec_max = max(vec) if vec else 0
    vec_mean = sum(vec) / len(vec) if vec else 0
    steps.append({
        "name": "compute_stats",
        "duration_ms": round((time.time() - t0) * 1000, 2),
        "detail": f"non-zero: {nonzero_count}/{len(vec)}",
    })

    total_ms = round((time.time() - total_start) * 1000, 2)

    return {
        "vector": vec[:50],  # Return first 50 values for preview
        "vector_full": vec,  # Full vector
        "dim": len(vec),
        "stats": {
            "min": round(vec_min, 6),
            "max": round(vec_max, 6),
            "mean": round(vec_mean, 6),
            "nonzero_count": nonzero_count,
            "total_dim": len(vec),
        },
        "model_type": "sentence-transformers" if model else "hash-fallback",
        "cache_hit": cache_hit,
        "steps": steps,
        "total_ms": total_ms,
    }


@router.post("/search")
def vector_search(req: SearchRequest, user: UserInfo = Depends(get_current_user)):
    """Run vector search against adh_table_info with execution step details."""
    steps = []
    total_start = time.time()

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text cannot be empty")

    # Step 1: Generate embedding
    t0 = time.time()
    vec = generate_embedding(req.text)
    vec_literal = embedding_to_sql_literal(vec)
    steps.append({
        "name": "generate_embedding",
        "duration_ms": round((time.time() - t0) * 1000, 2),
        "detail": f"{len(vec)}-dim",
    })

    # Step 2: Build SQL
    t0 = time.time()
    ds_filter = f"AND datasource_id = {req.datasource_id}" if req.datasource_id else ""
    sql = f"""
        SELECT table_name, table_comment, table_business_desc, keywords,
               l2_distance_approximate(embedding, {vec_literal}) AS distance
        FROM adh_table_info
        WHERE is_active = 1 {ds_filter}
        ORDER BY distance ASC
        LIMIT {req.limit}
    """
    steps.append({
        "name": "build_sql",
        "duration_ms": round((time.time() - t0) * 1000, 2),
        "detail": f"datasource_id={req.datasource_id}, limit={req.limit}",
    })

    # Step 3: Execute vector search (against Doris vector DB)
    t0 = time.time()
    try:
        with get_vector_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
    except Exception as e:
        logger.error("Vector search failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Vector search failed: {e}")
    search_ms = round((time.time() - t0) * 1000, 2)
    steps.append({
        "name": "vector_search",
        "duration_ms": search_ms,
        "detail": f"L2 distance, returned {len(rows)} rows",
    })

    # Step 4: Format results
    t0 = time.time()
    results = []
    for i, row in enumerate(rows):
        results.append({
            "rank": i + 1,
            "table_name": row["table_name"],
            "table_comment": row.get("table_comment") or "",
            "table_business_desc": row.get("table_business_desc") or "",
            "keywords": row.get("keywords") or "",
            "distance": round(row["distance"], 6),
        })
    steps.append({
        "name": "format_results",
        "duration_ms": round((time.time() - t0) * 1000, 2),
        "detail": f"{len(results)} results",
    })

    total_ms = round((time.time() - total_start) * 1000, 2)

    return {
        "results": results,
        "query_text": req.text,
        "query_vector": vec,
        "datasource_id": req.datasource_id,
        "steps": steps,
        "total_ms": total_ms,
    }


@router.post("/search-columns")
def column_vector_search(req: SearchRequest, user: UserInfo = Depends(get_current_user)):
    """Two-stage column search: first find relevant tables, then search columns within those tables."""
    steps = []
    total_start = time.time()

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text cannot be empty")

    # Step 1: Generate embedding
    t0 = time.time()
    vec = generate_embedding(req.text)
    vec_literal = embedding_to_sql_literal(vec)
    steps.append({
        "name": "generate_embedding",
        "duration_ms": round((time.time() - t0) * 1000, 2),
        "detail": f"{len(vec)}-dim",
    })

    ds_filter = f"AND datasource_id = {req.datasource_id}" if req.datasource_id else ""

    # Step 2: Find relevant tables first (top 5)
    t0 = time.time()
    table_sql = f"""
        SELECT table_name, l2_distance_approximate(embedding, {vec_literal}) AS distance
        FROM adh_table_info
        WHERE is_active = 1 {ds_filter}
        ORDER BY distance ASC
        LIMIT 5
    """
    try:
        with get_vector_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(table_sql)
                table_rows = cur.fetchall()
    except Exception as e:
        logger.error("Table search failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Table search failed: {e}")

    table_names = [r["table_name"] for r in table_rows]
    table_ms = round((time.time() - t0) * 1000, 2)
    steps.append({
        "name": "find_relevant_tables",
        "duration_ms": table_ms,
        "detail": f"found {len(table_names)} tables: {', '.join(table_names[:5])}",
    })

    # Step 3: Search columns within those tables
    t0 = time.time()
    if not table_names:
        rows = []
    else:
        placeholders = ", ".join(["%s"] * len(table_names))
        col_sql = f"""
            SELECT table_name, column_name, data_type, column_comment, business_desc,
                   l2_distance_approximate(embedding, {vec_literal}) AS distance
            FROM adh_column_metadata
            WHERE is_active = 1 AND table_name IN ({placeholders}) {ds_filter}
            ORDER BY distance ASC
            LIMIT %s
        """
        try:
            with get_vector_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(col_sql, table_names + [req.limit])
                    rows = cur.fetchall()
        except Exception as e:
            logger.error("Column search failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Column search failed: {e}")

    search_ms = round((time.time() - t0) * 1000, 2)
    steps.append({
        "name": "search_columns_in_tables",
        "duration_ms": search_ms,
        "detail": f"L2 distance, returned {len(rows)} columns from {len(table_names)} tables",
    })

    # Step 3: Format results
    results = []
    for i, row in enumerate(rows):
        results.append({
            "rank": i + 1,
            "table_name": row["table_name"],
            "column_name": row["column_name"],
            "data_type": row.get("data_type") or "",
            "column_comment": row.get("column_comment") or "",
            "business_desc": row.get("business_desc") or "",
            "distance": round(row["distance"], 6),
        })

    total_ms = round((time.time() - total_start) * 1000, 2)

    return {
        "results": results,
        "query_text": req.text,
        "datasource_id": req.datasource_id,
        "steps": steps,
        "total_ms": total_ms,
    }


@router.post("/reload")
def reload_embedding_model(req: ReloadRequest, user: UserInfo = Depends(get_current_user)):
    """Reload the embedding model. Optionally switch to a different model path."""
    steps = []
    total_start = time.time()

    # Step 1: Clear cache
    t0 = time.time()
    cache_size = len(_EMBED_CACHE)
    steps.append({
        "name": "clear_cache",
        "duration_ms": round((time.time() - t0) * 1000, 2),
        "detail": f"cleared {cache_size} cached embeddings",
    })

    # Step 2: Reload model
    t0 = time.time()
    info = reload_model(req.model_path)
    reload_ms = round((time.time() - t0) * 1000, 2)
    steps.append({
        "name": "reload_model",
        "duration_ms": reload_ms,
        "detail": f"model={info['model_path']}, loaded={info['model_loaded']}",
    })

    # Step 3: Test embedding
    t0 = time.time()
    test_vec = generate_embedding("测试")
    test_ms = round((time.time() - t0) * 1000, 2)
    steps.append({
        "name": "test_embedding",
        "duration_ms": test_ms,
        "detail": f"生成测试向量: {len(test_vec)}-dim",
    })

    total_ms = round((time.time() - total_start) * 1000, 2)

    return {
        "model_info": info,
        "test_vector_dim": len(test_vec),
        "steps": steps,
        "total_ms": total_ms,
    }
