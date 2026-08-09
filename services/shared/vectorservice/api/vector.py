"""Vector REST API routes.

Provides endpoints for vector similarity search, upsert,
batch upsert, delete, and table listing.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.shared.vectorservice.vector_db import get_vector_connection

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request / Response Models ─────────────────────────────────────────

class SearchRequest(BaseModel):
    """Vector similarity search request."""
    table: str = Field(..., description="Table name with embedding column")
    query_embedding: list[float] = Field(..., description="Query vector")
    limit: int = Field(default=20, ge=1, le=1000, description="Max results")
    filters: Optional[dict[str, Any]] = Field(default=None, description="Column filters")
    output_columns: Optional[list[str]] = Field(default=None, description="Columns to return")


class UpsertRequest(BaseModel):
    """Single record upsert request."""
    table: str = Field(..., description="Table name")
    id_column: str = Field(..., description="Primary key column name")
    id_value: Any = Field(..., description="Primary key value")
    data: dict[str, Any] = Field(..., description="Column values (must include embedding)")


class UpsertBatchRequest(BaseModel):
    """Batch upsert request."""
    table: str = Field(..., description="Table name")
    id_column: str = Field(..., description="Primary key column name")
    records: list[dict[str, Any]] = Field(..., description="Records to upsert")


class SearchResponse(BaseModel):
    """Vector search response."""
    results: list[dict[str, Any]]
    count: int
    table: str


class UpsertResponse(BaseModel):
    """Upsert response."""
    success: bool
    table: str
    id_column: str
    id_value: Any


class BatchUpsertResponse(BaseModel):
    """Batch upsert response."""
    success: bool
    table: str
    upserted: int


class DeleteResponse(BaseModel):
    """Delete response."""
    success: bool
    table: str
    id_column: str
    id_value: Any


class TableInfo(BaseModel):
    """Table with embedding column info."""
    table_name: str
    embedding_column: str
    embedding_dim: Optional[int] = None


# ── Helper ────────────────────────────────────────────────────────────

def _embedding_to_sql_literal(embedding: list[float]) -> str:
    """Convert embedding list to SQL array literal for Doris HNSW."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


# ── Routes ────────────────────────────────────────────────────────────

@router.post("/search", response_model=SearchResponse)
async def vector_search(req: SearchRequest):
    """Vector similarity search using Doris HNSW index.

    Uses l2_distance_approximate() for nearest neighbor search.
    Returns results sorted by distance (ascending).
    """
    try:
        vec_literal = _embedding_to_sql_literal(req.query_embedding)

        # Build SELECT clause
        if req.output_columns:
            select_cols = ", ".join(f"`{c}`" for c in req.output_columns)
        else:
            select_cols = "*"

        # Build WHERE clause
        where_parts = []
        params = []
        if req.filters:
            for key, value in req.filters.items():
                if key == "_raw":
                    where_parts.append(value)
                elif isinstance(value, (list, tuple)):
                    placeholders = ", ".join(["%s"] * len(value))
                    where_parts.append(f"`{key}` IN ({placeholders})")
                    params.extend(value)
                else:
                    where_parts.append(f"`{key}` = %s")
                    params.append(value)

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        sql = f"""
            SELECT {select_cols},
                   l2_distance_approximate(embedding, {vec_literal}) AS distance
            FROM {req.table}
            WHERE {where_clause}
            ORDER BY distance ASC
            LIMIT %s
        """
        params.append(req.limit)

        with get_vector_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                results = cur.fetchall()

        return SearchResponse(
            results=results,
            count=len(results),
            table=req.table,
        )
    except Exception as e:
        logger.error("Vector search failed on table %s: %s", req.table, e)
        raise HTTPException(status_code=500, detail=f"Vector search failed: {e}")


@router.post("/upsert", response_model=UpsertResponse)
async def upsert_record(req: UpsertRequest):
    """Insert or update a single record.

    Uses DELETE + INSERT pattern for Doris DUPLICATE KEY table compatibility.
    """
    try:
        with get_vector_connection() as conn:
            with conn.cursor() as cur:
                # Delete existing record
                cur.execute(
                    f"DELETE FROM {req.table} WHERE `{req.id_column}` = %s",
                    (req.id_value,),
                )

                # Insert new record
                cols = ", ".join(f"`{k}`" for k in req.data.keys())
                placeholders = ", ".join(["%s"] * len(req.data))
                cur.execute(
                    f"INSERT INTO {req.table} ({cols}) VALUES ({placeholders})",
                    list(req.data.values()),
                )

        return UpsertResponse(
            success=True,
            table=req.table,
            id_column=req.id_column,
            id_value=req.id_value,
        )
    except Exception as e:
        logger.error("Upsert failed on table %s for %s=%s: %s",
                     req.table, req.id_column, req.id_value, e)
        raise HTTPException(status_code=500, detail=f"Upsert failed: {e}")


@router.post("/upsert-batch", response_model=BatchUpsertResponse)
async def upsert_batch(req: UpsertBatchRequest):
    """Batch insert or update records.

    Uses DELETE + INSERT pattern for Doris DUPLICATE KEY table compatibility.
    """
    if not req.records:
        return BatchUpsertResponse(success=True, table=req.table, upserted=0)

    try:
        with get_vector_connection() as conn:
            with conn.cursor() as cur:
                # Delete existing records
                id_values = [r[req.id_column] for r in req.records]
                placeholders = ", ".join(["%s"] * len(id_values))
                cur.execute(
                    f"DELETE FROM {req.table} WHERE `{req.id_column}` IN ({placeholders})",
                    id_values,
                )

                # Insert new records
                cols = ", ".join(f"`{k}`" for k in req.records[0].keys())
                col_placeholders = ", ".join(["%s"] * len(req.records[0]))
                for record in req.records:
                    cur.execute(
                        f"INSERT INTO {req.table} ({cols}) VALUES ({col_placeholders})",
                        list(record.values()),
                    )

        return BatchUpsertResponse(
            success=True,
            table=req.table,
            upserted=len(req.records),
        )
    except Exception as e:
        logger.error("Batch upsert failed on table %s: %s", req.table, e)
        raise HTTPException(status_code=500, detail=f"Batch upsert failed: {e}")


@router.delete("/{table}/{id_column}/{id_value}", response_model=DeleteResponse)
async def delete_record(table: str, id_column: str, id_value: str):
    """Delete a single record by primary key."""
    try:
        with get_vector_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {table} WHERE `{id_column}` = %s",
                    (id_value,),
                )
                affected = cur.rowcount

        if affected == 0:
            raise HTTPException(status_code=404, detail="Record not found")

        return DeleteResponse(
            success=True,
            table=table,
            id_column=id_column,
            id_value=id_value,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Delete failed on table %s for %s=%s: %s",
                     table, id_column, id_value, e)
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")


@router.get("/tables", response_model=list[TableInfo])
async def list_vector_tables():
    """List tables that have an 'embedding' column.

    Queries the Doris information_schema to discover vector-enabled tables.
    """
    try:
        sql = """
            SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE
            FROM information_schema.columns
            WHERE COLUMN_NAME = 'embedding'
              AND TABLE_SCHEMA = DATABASE()
            ORDER BY TABLE_NAME
        """
        with get_vector_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()

        tables = []
        for row in rows:
            # Try to extract dimension from column type like 'ARRAY<FLOAT>'
            dim = None
            col_type = row.get("COLUMN_TYPE", "")
            # Doris array type doesn't encode dimension, so we leave it None
            tables.append(TableInfo(
                table_name=row["TABLE_NAME"],
                embedding_column=row["COLUMN_NAME"],
                embedding_dim=dim,
            ))

        return tables
    except Exception as e:
        logger.error("Failed to list vector tables: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to list tables: {e}")
