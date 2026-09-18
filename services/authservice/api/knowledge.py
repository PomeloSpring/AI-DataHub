"""Knowledge API — 知识条目管理（推荐问题 / SQL 对）。

挂载于 authservice（prefix=/api/admin/knowledge）。
数据表: adh_knowledge_items (MySQL 元库)。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.shared.common.auth import require_admin
from services.shared.common.db import get_metadata_conn

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────

class KnowledgeCreate(BaseModel):
    workspace_id: int = 0
    datasource_id: int = 0
    knowledge_type: str  # recommend_question / sql_pair
    title: str
    content: str
    metadata: Optional[dict] = None
    related_tables: str = ""
    priority: int = 0
    is_active: int = 1


class KnowledgeUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    metadata: Optional[dict] = None
    related_tables: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[int] = None


# ── Helpers ────────────────────────────────────────────────────────

def _serialize_row(row: dict) -> dict:
    """把 metadata JSON 字符串解析为 dict。"""
    out = dict(row)
    if isinstance(out.get("metadata"), str):
        try:
            out["metadata"] = json.loads(out["metadata"])
        except (json.JSONDecodeError, TypeError):
            out["metadata"] = None
    return out


# ── Endpoints ──────────────────────────────────────────────────────

@router.get("/knowledge")
def list_knowledge(
    workspace_id: int = Query(0),
    knowledge_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    admin: dict = None,  # require_admin 注入（可选，非 admin 也可读）
):
    """知识条目列表（分页）。"""
    where = ["1=1"]
    params: list = []

    if workspace_id:
        where.append("workspace_id = %s")
        params.append(workspace_id)
    if knowledge_type:
        where.append("knowledge_type = %s")
        params.append(knowledge_type)
    if search:
        where.append("(title LIKE %s OR content LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = " AND ".join(where)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) as total FROM adh_knowledge_items WHERE {where_sql}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"""SELECT * FROM adh_knowledge_items WHERE {where_sql}
                    ORDER BY priority DESC, created_at DESC
                    LIMIT %s OFFSET %s""",
                params + [size, offset],
            )
            rows = [_serialize_row(r) for r in cur.fetchall()]

    return {"items": rows, "total": total, "page": page, "size": size}


@router.get("/knowledge/random")
def random_knowledge(
    knowledge_type: str = Query("recommend_question"),
    limit: int = Query(3, ge=1, le=10),
    workspace_id: int = Query(0),
    datasource_id: int = Query(0),
):
    """随机获取 N 条知识条目（用于 Chat 推荐问题展示）。"""
    where = ["knowledge_type = %s", "is_active = 1"]
    params: list = [knowledge_type]

    if workspace_id:
        where.append("workspace_id = %s")
        params.append(workspace_id)
    if datasource_id:
        where.append("(datasource_id = %s OR datasource_id = 0)")
        params.append(datasource_id)

    where_sql = " AND ".join(where)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT * FROM adh_knowledge_items WHERE {where_sql}
                    ORDER BY RAND() LIMIT %s""",
                params + [limit],
            )
            rows = [_serialize_row(r) for r in cur.fetchall()]

    return {"items": rows}


@router.get("/knowledge/stats")
def knowledge_stats(
    workspace_id: int = Query(0),
    admin: dict = None,
):
    """知识条目统计（按类型分组）。"""
    where = ["1=1"]
    params: list = []
    if workspace_id:
        where.append("workspace_id = %s")
        params.append(workspace_id)

    where_sql = " AND ".join(where)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) as total FROM adh_knowledge_items WHERE {where_sql}", params)
            total = cur.fetchone()["total"]

            cur.execute(
                f"""SELECT knowledge_type, COUNT(*) as cnt,
                           SUM(usage_count) as total_usage,
                           SUM(positive_count) as total_positive,
                           SUM(negative_count) as total_negative
                    FROM adh_knowledge_items WHERE {where_sql}
                    GROUP BY knowledge_type""",
                params,
            )
            by_type = cur.fetchall()

    return {"total": total, "by_type": by_type}


@router.post("/knowledge")
def create_knowledge(body: KnowledgeCreate, admin: dict = None):
    """创建知识条目。"""
    item_id = uuid.uuid4().int & ((1 << 63) - 1)  # 正 BIGINT
    metadata_json = json.dumps(body.metadata, ensure_ascii=False) if body.metadata else None

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO adh_knowledge_items
                   (id, workspace_id, datasource_id, knowledge_type, title, content,
                    metadata, related_tables, priority, is_active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (item_id, body.workspace_id, body.datasource_id, body.knowledge_type,
                 body.title, body.content, metadata_json, body.related_tables,
                 body.priority, body.is_active),
            )

    return {"id": item_id, "message": "created"}


@router.put("/knowledge/{item_id}")
def update_knowledge(item_id: int, body: KnowledgeUpdate, admin: dict = None):
    """更新知识条目。"""
    updates = []
    params: list = []

    for field in ("title", "content", "related_tables", "priority", "is_active"):
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = %s")
            params.append(val)

    if body.metadata is not None:
        updates.append("metadata = %s")
        params.append(json.dumps(body.metadata, ensure_ascii=False))

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(item_id)
    set_sql = ", ".join(updates)

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE adh_knowledge_items SET {set_sql} WHERE id = %s", params)
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Knowledge item not found")

    return {"message": "updated"}


@router.patch("/knowledge/{item_id}/toggle")
def toggle_knowledge(item_id: int, admin: dict = None):
    """切换知识条目的启用/禁用状态。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_active FROM adh_knowledge_items WHERE id = %s", (item_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Knowledge item not found")
            new_val = 0 if row["is_active"] else 1
            cur.execute("UPDATE adh_knowledge_items SET is_active = %s WHERE id = %s", (new_val, item_id))

    return {"is_active": new_val}


@router.delete("/knowledge/{item_id}")
def delete_knowledge(item_id: int, admin: dict = None):
    """删除知识条目。"""
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_knowledge_items WHERE id = %s", (item_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Knowledge item not found")

    return {"message": "deleted"}
