"""Knowledge Base Management API — Support multiple knowledge bases with various source types.

Supports:
- QMind (Qoder 云端知识库/笔记本):列表与检索均来自 qmind CLI,
  通过 `GET /knowledge-bases/qmind/notebooks` 实时检索、`POST /knowledge-bases/qmind/import`
  将选定的 notebook 落地为可被 AS-BOT 绑定的知识库行(kb_type='qmind')。
- Local data directory
- Vector databases (Doris, Milvus, Pinecone, etc.)
- Cloud RAG services (Aliyun, Tencent, Baidu, etc.)
"""

import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from services.shared.common.auth import get_current_user, require_admin

from services.shared.common.db import get_metadata_conn

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

# 阻塞型 CLI/DB 调用统一放到线程池,避免阻塞事件循环
_to_thread = asyncio.to_thread


# ── Pydantic Models ────────────────────────────────────────────────

class KnowledgeBasePayload(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_workspace_binding(cls, data):
        if isinstance(data, dict) and {"workspace_id", "workspace_ids"}.intersection(data):
            raise ValueError("知识库使用范围请在 AS-BOT 中绑定，不再支持工作空间绑定")
        return data


class KnowledgeBaseCreate(KnowledgeBasePayload):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    kb_type: str = Field(..., pattern="^(local|vector_db|cloud_rag|qmind)$")
    source_config: dict = Field(default_factory=dict)


class KnowledgeBaseUpdate(KnowledgeBasePayload):
    name: Optional[str] = None
    description: Optional[str] = None
    source_config: Optional[dict] = None
    status: Optional[str] = None


class KnowledgeBaseResponse(BaseModel):
    id: int
    name: str
    description: str
    kb_type: str
    source_config: Optional[dict] = None
    status: str
    document_count: int
    chunk_count: int
    last_sync_at: Optional[str]
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


class QMindImportRequest(BaseModel):
    notebook_ids: Optional[List[str]] = None  # 为空表示导入全部检索到的 notebook


# Initialize table on module load
_ensure_table()


# ── API Endpoints ──────────────────────────────────────────────────

@router.get("/knowledge-bases", response_model=List[KnowledgeBaseResponse], response_model_exclude_none=True)
async def list_knowledge_bases(
    kb_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    workspace_id: Optional[int] = Query(None),
    user: dict = Depends(get_current_user),
):
    """系统管理目录或从当前身份可用 AS-BOT 派生的只读资源目录。"""
    allowed_ids = None
    if workspace_id is not None:
        from services.datamind.execution.as_bots import visible_resource_ids
        allowed_ids = set(visible_resource_ids(user, workspace_id, "knowledge_base_ids"))
        if not allowed_ids:
            return []
    elif user.get("role") != "admin":
        raise HTTPException(403, "系统知识库目录仅管理员可访问，请选择已授权工作空间")
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

            if allowed_ids is not None:
                sql += " AND status='active' AND id IN (" + ','.join(['%s'] * len(allowed_ids)) + ")"
                params.extend(sorted(allowed_ids))
            sql += " ORDER BY created_at DESC"
            cursor.execute(sql, params)
            rows = cursor.fetchall()

            results = []
            for row in rows:
                if allowed_ids is not None and (row["id"] not in allowed_ids or row["status"] != "active"):
                    continue

                results.append(KnowledgeBaseResponse(
                    id=row["id"],
                    name=row["name"],
                    description=row.get("description") or "",
                    kb_type=row["kb_type"],
                    source_config=json.loads(row.get("source_config") or "{}") if user.get("role") == "admin" else None,
                    status=row["status"],
                    document_count=row.get("document_count") or 0,
                    chunk_count=row.get("chunk_count") or 0,
                    last_sync_at=row["last_sync_at"].isoformat() if row.get("last_sync_at") else None,
                    created_at=row["created_at"].isoformat(),
                    updated_at=row["updated_at"].isoformat(),
                ))

            return results
    finally:
        conn.close()


@router.get("/knowledge-bases/qmind/notebooks", dependencies=[Depends(require_admin)])
async def list_qmind_notebooks():
    """从 Qoder 实时检索 QMind 知识库(笔记本)列表.

    返回每个 notebook 及其是否已导入为本地知识库(imported / kb_id),
    供后台展示“可绑定的 Qoder 知识库”。qmind CLI 不可用时返回空列表。
    """
    from services.datamind.rag.qmind_retriever import list_notebooks

    notebooks = await _to_thread(list_notebooks)
    # 已导入的 notebook_id → 本地 kb 行
    imported: dict[str, int] = {}
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, source_config FROM adh_knowledge_bases WHERE kb_type = 'qmind'"
            )
            for row in cursor.fetchall():
                try:
                    cfg = json.loads(row.get("source_config") or "{}")
                except (json.JSONDecodeError, TypeError):
                    cfg = {}
                nb = cfg.get("notebook_id")
                if nb:
                    imported[nb] = row["id"]
    finally:
        conn.close()

    return {
        "available": bool(notebooks),
        "notebooks": [
            {**nb, "imported": nb["notebook_id"] in imported, "kb_id": imported.get(nb["notebook_id"])}
            for nb in notebooks
        ],
    }


@router.post("/knowledge-bases/qmind/import", dependencies=[Depends(require_admin)])
async def import_qmind_notebooks(request: QMindImportRequest):
    """将选定的 Qoder QMind notebook 导入(落地)为可绑定的知识库(kb_type='qmind').

    以 notebook_id 为幂等键:已存在则刷新标题/描述,不存在则新建。未指定
    notebook_ids 时导入检索到的全部 notebook。
    """
    from services.datamind.rag.qmind_retriever import list_notebooks

    notebooks = await _to_thread(list_notebooks)
    if not notebooks:
        raise HTTPException(status_code=502, detail="未能从 Qoder 检索到知识库(qmind CLI 不可用或未登录)")
    wanted = set(request.notebook_ids or [])
    selected = [n for n in notebooks if not wanted or n["notebook_id"] in wanted]
    if not selected:
        raise HTTPException(status_code=400, detail="选定的 notebook 不在检索结果中")

    created, updated = 0, 0
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cursor:
            for nb in selected:
                cursor.execute(
                    "SELECT id FROM adh_knowledge_bases WHERE kb_type='qmind' AND "
                    "JSON_UNQUOTE(JSON_EXTRACT(source_config, '$.notebook_id')) = %s",
                    (nb["notebook_id"],),
                )
                row = cursor.fetchone()
                cfg = json.dumps(
                    {"notebook_id": nb["notebook_id"], "org_id": nb["org_id"],
                     "title": nb["title"], "top_k": 5},
                    ensure_ascii=False,
                )
                if row:
                    cursor.execute(
                        "UPDATE adh_knowledge_bases SET name=%s, description=%s, source_config=%s "
                        "WHERE id=%s",
                        (nb["title"], nb["description"], cfg, row["id"]),
                    )
                    updated += 1
                else:
                    cursor.execute(
                        """INSERT INTO adh_knowledge_bases
                           (name, description, kb_type, source_config, status, workspace_ids)
                           VALUES (%s, %s, 'qmind', %s, 'active', '[]')""",
                        (nb["title"], nb["description"], cfg),
                    )
                    created += 1
            conn.commit()
    finally:
        conn.close()

    return {
        "message": f"已导入 {created} 个、刷新 {updated} 个 Qoder 知识库",
        "created": created,
        "updated": updated,
    }


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseResponse, dependencies=[Depends(require_admin)])
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
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
    finally:
        conn.close()


@router.post("/knowledge-bases", response_model=KnowledgeBaseResponse, dependencies=[Depends(require_admin)])
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
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
    finally:
        conn.close()


@router.put("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseResponse, dependencies=[Depends(require_admin)])
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
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
            )
    finally:
        conn.close()


@router.delete("/knowledge-bases/{kb_id}", dependencies=[Depends(require_admin)])
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


@router.post("/knowledge-bases/{kb_id}/sync", dependencies=[Depends(require_admin)])
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


@router.get("/knowledge-bases/{kb_id}/documents", dependencies=[Depends(require_admin)])
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
