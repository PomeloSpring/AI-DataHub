"""Knowledge Base Management API — Support multiple knowledge bases with various source types.

Supports:
- Local data directory
- Vector databases (Doris, Milvus, Pinecone, etc.)
- Cloud RAG services (Aliyun, Tencent, Baidu, etc.)
"""

import json
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from services.shared.common.db import get_metadata_conn

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic Models ────────────────────────────────────────────────

class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    kb_type: str = Field(..., pattern="^(local|vector_db|cloud_rag)$")
    source_config: dict = Field(default_factory=dict)


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source_config: Optional[dict] = None
    status: Optional[str] = None
    workspace_ids: Optional[List[int]] = None


class KnowledgeBaseResponse(BaseModel):
    id: int
    name: str
    description: str
    kb_type: str
    source_config: dict
    status: str
    document_count: int
    chunk_count: int
    last_sync_at: Optional[str]
    workspace_ids: List[int]
    created_at: str
    updated_at: str


# ── Table Initialization ───────────────────────────────────────────

def _ensure_table():
    """Create knowledge_bases table if it doesn't exist."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS adh_knowledge_bases (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    kb_type VARCHAR(20) NOT NULL DEFAULT 'local',
                    source_config JSON,
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    document_count INT NOT NULL DEFAULT 0,
                    chunk_count INT NOT NULL DEFAULT 0,
                    last_sync_at DATETIME,
                    workspace_ids JSON,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_kb_type (kb_type),
                    INDEX idx_status (status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            conn.commit()
    finally:
        conn.close()


# Initialize table on module load
_ensure_table()


# ── API Endpoints ──────────────────────────────────────────────────

@router.get("/knowledge-bases", response_model=List[KnowledgeBaseResponse])
async def list_knowledge_bases(
    kb_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    workspace_id: Optional[int] = Query(None),
):
    """List all knowledge bases with optional filters."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            sql = "SELECT * FROM adh_knowledge_bases WHERE 1=1"
            params = []

            if kb_type:
                sql += " AND kb_type = %s"
                params.append(kb_type)

            if status:
                sql += " AND status = %s"
                params.append(status)

            sql += " ORDER BY created_at DESC"
            cursor.execute(sql, params)
            rows = cursor.fetchall()

            results = []
            for row in rows:
                workspace_ids = json.loads(row.get("workspace_ids") or "[]")

                # Filter by workspace_id if specified
                if workspace_id is not None and workspace_id not in workspace_ids:
                    continue

                results.append(KnowledgeBaseResponse(
                    id=row["id"],
                    name=row["name"],
                    description=row.get("description") or "",
                    kb_type=row["kb_type"],
                    source_config=json.loads(row.get("source_config") or "{}"),
                    status=row["status"],
                    document_count=row.get("document_count") or 0,
                    chunk_count=row.get("chunk_count") or 0,
                    last_sync_at=row["last_sync_at"].isoformat() if row.get("last_sync_at") else None,
                    workspace_ids=workspace_ids,
                    created_at=row["created_at"].isoformat(),
                    updated_at=row["updated_at"].isoformat(),
                ))

            return results
    finally:
        conn.close()


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(kb_id: int):
    """Get a specific knowledge base by ID."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="知识库不存在")

            return KnowledgeBaseResponse(
                id=row["id"],
                name=row["name"],
                description=row.get("description") or "",
                kb_type=row["kb_type"],
                source_config=json.loads(row.get("source_config") or "{}"),
                status=row["status"],
                document_count=row.get("document_count") or 0,
                chunk_count=row.get("chunk_count") or 0,
                last_sync_at=row["last_sync_at"].isoformat() if row.get("last_sync_at") else None,
                workspace_ids=json.loads(row.get("workspace_ids") or "[]"),
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
    finally:
        conn.close()


@router.post("/knowledge-bases", response_model=KnowledgeBaseResponse)
async def create_knowledge_base(request: KnowledgeBaseCreate):
    """Create a new knowledge base."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO adh_knowledge_bases (name, description, kb_type, source_config, status, workspace_ids)
                   VALUES (%s, %s, %s, %s, 'active', '[]')""",
                (
                    request.name,
                    request.description,
                    request.kb_type,
                    json.dumps(request.source_config),
                )
            )
            conn.commit()
            kb_id = cursor.lastrowid

            # Fetch the created record
            cursor.execute("SELECT * FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            return KnowledgeBaseResponse(
                id=row["id"],
                name=row["name"],
                description=row.get("description") or "",
                kb_type=row["kb_type"],
                source_config=json.loads(row.get("source_config") or "{}"),
                status=row["status"],
                document_count=0,
                chunk_count=0,
                last_sync_at=None,
                workspace_ids=[],
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
    finally:
        conn.close()


@router.put("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(kb_id: int, request: KnowledgeBaseUpdate):
    """Update a knowledge base."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            # Check existence
            cursor.execute("SELECT id FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="知识库不存在")

            # Build update SQL dynamically
            updates = []
            params = []

            if request.name is not None:
                updates.append("name = %s")
                params.append(request.name)

            if request.description is not None:
                updates.append("description = %s")
                params.append(request.description)

            if request.source_config is not None:
                updates.append("source_config = %s")
                params.append(json.dumps(request.source_config))

            if request.status is not None:
                updates.append("status = %s")
                params.append(request.status)

            if request.workspace_ids is not None:
                updates.append("workspace_ids = %s")
                params.append(json.dumps(request.workspace_ids))

            if updates:
                sql = f"UPDATE adh_knowledge_bases SET {', '.join(updates)} WHERE id = %s"
                params.append(kb_id)
                cursor.execute(sql, params)
                conn.commit()

            # Return updated record
            cursor.execute("SELECT * FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            return KnowledgeBaseResponse(
                id=row["id"],
                name=row["name"],
                description=row.get("description") or "",
                kb_type=row["kb_type"],
                source_config=json.loads(row.get("source_config") or "{}"),
                status=row["status"],
                document_count=row.get("document_count") or 0,
                chunk_count=row.get("chunk_count") or 0,
                last_sync_at=row["last_sync_at"].isoformat() if row.get("last_sync_at") else None,
                workspace_ids=json.loads(row.get("workspace_ids") or "[]"),
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
    finally:
        conn.close()


@router.delete("/knowledge-bases/{kb_id}")
async def delete_knowledge_base(kb_id: int):
    """Delete a knowledge base."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, name FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="知识库不存在")

            cursor.execute("DELETE FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            conn.commit()

            return {"message": f'已删除知识库 "{row["name"]}"'}
    finally:
        conn.close()


@router.post("/knowledge-bases/{kb_id}/sync")
async def sync_knowledge_base(kb_id: int):
    """Sync a knowledge base (triggers background sync process)."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            row = cursor.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="知识库不存在")

            kb_type = row["kb_type"]
            source_config = json.loads(row.get("source_config") or "{}")

            # TODO: Implement actual sync logic based on kb_type
            # For now, just update the last_sync_at timestamp
            cursor.execute(
                "UPDATE adh_knowledge_bases SET last_sync_at = NOW() WHERE id = %s",
                (kb_id,)
            )
            conn.commit()

            return {
                "message": f'正在同步知识库 "{row["name"]}"',
                "kb_type": kb_type,
                "status": "syncing",
            }
    finally:
        conn.close()


@router.get("/knowledge-bases/{kb_id}/documents")
async def list_knowledge_base_documents(
    kb_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List documents in a knowledge base."""
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            # Check knowledge base exists
            cursor.execute("SELECT id, name FROM adh_knowledge_bases WHERE id = %s", (kb_id,))
            kb = cursor.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

            # Get documents from knowledge_chunks table (if exists)
            offset = (page - 1) * page_size

            try:
                cursor.execute(
                    """SELECT id, title, content, doc_type, source, created_at, updated_at
                       FROM adh_knowledge_chunks
                       WHERE knowledge_base_id = %s
                       ORDER BY created_at DESC
                       LIMIT %s OFFSET %s""",
                    (kb_id, page_size, offset)
                )
                documents = cursor.fetchall()

                cursor.execute(
                    "SELECT COUNT(*) as total FROM adh_knowledge_chunks WHERE knowledge_base_id = %s",
                    (kb_id,)
                )
                total = cursor.fetchone()["total"]
            except Exception:
                # Table might not exist yet
                documents = []
                total = 0

            return {
                "knowledge_base": kb["name"],
                "documents": documents,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
    finally:
        conn.close()
