"""Knowledge Base API — Document management, upload, sync.

Delegates to existing backend knowledge/ai_assistant service logic.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel

from services.shared.common.auth import get_current_user
from services.shared.models.schemas import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request Models ────────────────────────────────────────────────────

class DocumentUploadRequest(BaseModel):
    title: str
    content: str
    doc_type: str = "custom_text"
    workspace_id: Optional[int] = None
    tags: Optional[list[str]] = None


# ── List Documents ────────────────────────────────────────────────────

@router.get("/documents")
def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    doc_type: Optional[str] = Query(None, description="Filter by document type"),
    workspace_id: Optional[int] = Query(None, description="Filter by workspace"),
    user: UserInfo = Depends(get_current_user),
):
    """List knowledge base documents with pagination."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Build where clause
            where_clauses = ["status = 'active'"]
            params = []

            if doc_type:
                where_clauses.append("doc_type = %s")
                params.append(doc_type)
            if workspace_id:
                where_clauses.append("workspace_id = %s")
                params.append(workspace_id)

            where_sql = " AND ".join(where_clauses)

            # Count total
            cur.execute(f"SELECT COUNT(*) as total FROM adh_knowledge_documents WHERE {where_sql}", params)
            total = cur.fetchone()["total"]

            # Fetch page
            offset = (page - 1) * page_size
            cur.execute(
                f"SELECT id, title, doc_type, source, file_path, status, "
                f"chunk_count, has_embedding, workspace_id, tags, "
                f"created_at, updated_at "
                f"FROM adh_knowledge_documents WHERE {where_sql} "
                f"ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                params + [page_size, offset]
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
                # Parse tags JSON
                if isinstance(r.get("tags"), str):
                    import json
                    try:
                        r["tags"] = json.loads(r["tags"])
                    except:
                        r["tags"] = []

            return {
                "documents": rows,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
    finally:
        conn.close()


# ── Upload Document (JSON) ───────────────────────────────────────────

@router.post("/documents")
@router.post("/documents/upload")
def upload_document_json(
    req: DocumentUploadRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Upload a document to the knowledge base (JSON body)."""
    from services.datamind.services.knowledge_service import KnowledgeService

    service = KnowledgeService()
    result = service.upload_document(
        title=req.title,
        content=req.content,
        doc_type=req.doc_type,
        tags=req.tags or [],
        user_id=user["user_id"],
        workspace_id=req.workspace_id,
    )
    return result


# ── Upload Document (File) ───────────────────────────────────────────

@router.post("/documents/upload-file")
async def upload_document_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    doc_type: str = Form("guide"),
    tags: Optional[str] = Form(None),
    user: UserInfo = Depends(get_current_user),
):
    """Upload a file to the knowledge base."""
    allowed_extensions = {".md", ".txt", ".rst", ".json", ".yaml", ".yml"}
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Allowed: {', '.join(allowed_extensions)}",
        )

    try:
        content_bytes = await file.read()
        content_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File encoding error. Please use UTF-8.")

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    doc_title = title or file.filename

    from services.datamind.services.knowledge_service import KnowledgeService

    service = KnowledgeService()
    result = service.upload_document(
        title=doc_title,
        content=content_str,
        doc_type=doc_type,
        tags=tag_list,
        user_id=user["user_id"],
    )
    return result


# ── Delete Document ──────────────────────────────────────────────────

@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """Delete a document from the knowledge base."""
    from services.datamind.services.knowledge_service import KnowledgeService

    service = KnowledgeService()
    return service.delete_document(doc_id, user_id=user["user_id"])


@router.post("/documents/{doc_id}/vectorize")
def vectorize_document(
    doc_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """Vectorize a specific document."""
    from services.datamind.services.knowledge_service import KnowledgeService

    service = KnowledgeService()
    return service.vectorize_document(doc_id)


# ── Sync Knowledge Base ──────────────────────────────────────────────

@router.post("/sync")
def sync_knowledge_base(
    req: dict = {},
    user: UserInfo = Depends(get_current_user),
):
    """Trigger a knowledge base synchronization.

    Rebuilds vector embeddings for all active documents and metadata.
    """
    from services.datamind.services.knowledge_service import KnowledgeService

    force = req.get("force", False) if req else False
    service = KnowledgeService()
    return service.sync_knowledge_base(force=force)
