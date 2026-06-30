"""Admin API — Metadata, SQL Templates, Business Terms CRUD with auto-embedding."""

import json
import logging
import time as _time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.auth import require_admin
from backend.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)
from backend.common.db.metadata_db import get_metadata_conn
from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal
from backend.common.ttl_cache import datasource_cache, menu_cache, dashboard_cache, brand_cache, metadata_cache
from backend.models.schemas import (
    UserInfo, SyncResponse,
    MetadataCreate, MetadataUpdate, TableInfoCreate, TableInfoUpdate,
    TemplateCreate, TemplateUpdate, TermCreate, TermUpdate,
    RelationCreate, RelationUpdate,
    BrandSettings, BrandSettingsUpdate,
)

router = APIRouter()

# Brand settings file path
_BRAND_SETTINGS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "brand_settings.json"

_DEFAULT_BRAND = {
    "app_name": "ChatBI",
    "logo_url": "",
    "show_icon": True,
    "show_text": True,
}


def _load_brand_settings() -> dict:
    if _BRAND_SETTINGS_PATH.exists():
        try:
            with open(_BRAND_SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            return {**_DEFAULT_BRAND, **saved}
        except Exception:
            pass
    return dict(_DEFAULT_BRAND)


def _save_brand_settings(settings: dict):
    _BRAND_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_BRAND_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _get_metadata_conn():
    """Get a connection from the pool."""
    return get_metadata_conn()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _make_embedding(text: str) -> str:
    vec = generate_embedding(text)
    return embedding_to_sql_literal(vec)


def _table_embed_text(table_name: str, table_comment: str = "", keywords: str = "",
                      region_tag: str = "", domain_tag: str = "") -> str:
    """Generate concise embedding text for table_info.

    Uses keywords (short focused terms) for vector search quality.
    Business description is NOT included — it's sent to LLM separately.
    """
    parts = [table_name, table_comment or "", keywords or ""]
    return " ".join(p for p in parts if p).strip()


def _col_embed_text(table_name: str, column_name: str, data_type: str = "",
                    column_comment: str = "", keywords: str = "") -> str:
    """Generate concise embedding text for column_metadata.

    Uses keywords for vector search quality. Business desc is sent to LLM separately.
    """
    parts = [table_name, column_name, data_type or "", column_comment or "", keywords or ""]
    return " ".join(p for p in parts if p).strip()


# ── Table Metadata ─────────────────────────────────────────────────────

@router.post("/sync/metadata", response_model=SyncResponse)
def sync_metadata(req: dict = {}, admin: UserInfo = Depends(require_admin)):
    datasource_id = req.get("datasource_id", 0) if req else 0
    if not datasource_id:
        return SyncResponse(success=False, message="请选择要同步的数据源")
    try:
        from sync.metadata_sync import sync_metadata as _sync
        _sync(datasource_id)
        return SyncResponse(success=True, message="元数据同步完成")
    except Exception as e:
        return SyncResponse(success=False, message=str(e))


@router.post("/sync/metadata/columns")
def sync_table_columns(req: dict, admin: UserInfo = Depends(require_admin)):
    """Sync column metadata for a single table."""
    datasource_id = req.get("datasource_id", 0)
    table_name = req.get("table_name", "").strip()
    if not datasource_id:
        return {"success": False, "message": "请选择要同步的数据源"}
    if not table_name:
        return {"success": False, "message": "请输入要同步的表名"}
    try:
        from sync.metadata_sync import sync_table_columns as _sync_cols
        result = _sync_cols(datasource_id, table_name)
        return {
            "success": True,
            "message": f"表 {table_name} 字段同步完成：共 {result['total_columns']} 个字段"
                        f"（新增 {result['inserted']}，更新 {result['updated']}，删除 {result['deleted']}）",
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.get("/metadata")
def list_metadata(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    table_name: str = Query("", description="搜索表名"),
    column_name: str = Query("", description="搜索字段名"),
    datasource_id: int = Query(None, description="按数据源筛选"),
    admin: UserInfo = Depends(require_admin),
):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = []
            if datasource_id:
                conditions.append("datasource_id = %s")
                params.append(datasource_id)
            if table_name:
                conditions.append("table_name LIKE %s")
                params.append(f"%{table_name}%")
            if column_name:
                conditions.append("column_name LIKE %s")
                params.append(f"%{column_name}%")
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_column_metadata {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, table_name, column_name, data_type, "
                f"column_comment, business_desc, keywords, is_key, is_nullable, is_active, sync_time, datasource_id "
                f"FROM adh_column_metadata {where} "
                f"ORDER BY table_name, column_name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("sync_time",):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
            return {"total": total, "items": rows}
    finally:
        conn.close()


@router.get("/metadata/{row_id}")
def get_metadata(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, column_name, data_type, "
                "column_comment, business_desc, keywords, is_key, is_nullable, is_active, sync_time, datasource_id "
                "FROM adh_column_metadata WHERE id = %s", (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="记录不存在")
            if hasattr(row.get("sync_time"), "isoformat"):
                row["sync_time"] = row["sync_time"].isoformat()
            return row
    finally:
        conn.close()


@router.put("/metadata/{row_id}")
def update_metadata(row_id: int, req: MetadataUpdate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, column_name, data_type, "
                "column_comment, business_desc, keywords, is_key, is_nullable, is_active, datasource_id "
                "FROM adh_column_metadata WHERE id = %s", (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="记录不存在")

            # Merge updates
            column_comment = req.column_comment if req.column_comment is not None else (row["column_comment"] or "")
            business_desc = req.business_desc if req.business_desc is not None else (row["business_desc"] or "")
            keywords = req.keywords if req.keywords is not None else (row.get("keywords") or "")
            is_active = req.is_active if req.is_active is not None else bool(row["is_active"])

            # Re-generate embedding using keywords (not business_desc)
            embed_text = _col_embed_text(row['table_name'], row['column_name'], row["data_type"], column_comment, keywords)
            vec_literal = _make_embedding(embed_text)
            now = _now()

            # Doris DUPLICATE KEY table doesn't support UPDATE, use DELETE + INSERT
            cur.execute("DELETE FROM adh_column_metadata WHERE id = %s", (row_id,))
            cur.execute(
                "INSERT INTO adh_column_metadata "
                "(id, table_name, column_name, data_type, column_comment, "
                "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding, datasource_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, row["table_name"], row["column_name"], row["data_type"],
                 column_comment, business_desc, keywords, row["is_key"], row["is_nullable"],
                 1 if is_active else 0, now, vec_literal, row.get("datasource_id", 0)),
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.post("/metadata")
def create_metadata(req: MetadataCreate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Check duplicate column within same table
            cur.execute(
                "SELECT id FROM adh_column_metadata WHERE table_name = %s AND column_name = %s",
                (req.table_name, req.column_name),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"字段 '{req.column_name}' 在表 '{req.table_name}' 中已存在",
                )

            now = _now()
            row_id = int(_time.time() * 1000000)
            embed_text = _col_embed_text(req.table_name, req.column_name, req.data_type or "", req.column_comment, req.keywords or "")
            vec_literal = _make_embedding(embed_text)

            cur.execute(
                "INSERT INTO adh_column_metadata "
                "(id, table_name, column_name, data_type, column_comment, "
                "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding, datasource_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, req.table_name, req.column_name, req.data_type,
                 req.column_comment or "", req.business_desc or "", req.keywords or "",
                 req.is_key or "false", req.is_nullable or "true",
                 1 if req.is_active else 0, now, vec_literal, req.datasource_id or 0),
            )
        conn.commit()
        return {"success": True, "id": row_id}
    finally:
        conn.close()


@router.delete("/metadata/{row_id}")
def delete_metadata(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_column_metadata WHERE id = %s", (row_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


# ── Table Info ─────────────────────────────────────────────────────────

@router.get("/table-info")
def list_table_info(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=9999),
    table_name: str = Query("", description="搜索表名"),
    datasource_id: int = Query(None, description="按数据源筛选"),
    admin: UserInfo = Depends(require_admin),
):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = []
            if datasource_id:
                conditions.append("datasource_id = %s")
                params.append(datasource_id)
            if table_name:
                conditions.append("table_name LIKE %s")
                params.append(f"%{table_name}%")
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_table_info {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, table_name, table_comment, table_business_desc, keywords, "
                f"region_tag, domain_tag, is_active, datasource_id, sync_time "
                f"FROM adh_table_info {where} "
                f"ORDER BY table_name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                if hasattr(r.get("sync_time"), "isoformat"):
                    r["sync_time"] = r["sync_time"].isoformat()
            return {"total": total, "items": rows}
    finally:
        conn.close()


@router.get("/table-info/{row_id}")
def get_table_info(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, table_comment, table_business_desc, keywords, "
                "region_tag, domain_tag, is_active, datasource_id, sync_time "
                "FROM adh_table_info WHERE id = %s", (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="表信息不存在")
            if hasattr(row.get("sync_time"), "isoformat"):
                row["sync_time"] = row["sync_time"].isoformat()
            return row
    finally:
        conn.close()


@router.post("/table-info")
def create_table_info(req: TableInfoCreate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Check duplicate table_name within same datasource
            cur.execute(
                "SELECT id FROM adh_table_info WHERE table_name = %s AND datasource_id = %s",
                (req.table_name, req.datasource_id or 0),
            )
            if cur.fetchone():
                raise HTTPException(status_code=400, detail=f"表 '{req.table_name}' 在该数据源中已存在")

            now = _now()
            row_id = int(_time.time() * 1000000)
            embed_text = _table_embed_text(req.table_name, req.table_comment, req.keywords or "", "", req.domain_tag)
            vec_literal = _make_embedding(embed_text)

            cur.execute(
                "INSERT INTO adh_table_info "
                "(id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, "
                "is_active, sync_time, embedding, datasource_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, req.table_name, req.table_comment or "", req.table_business_desc or "",
                 req.keywords or "", req.region_tag or "", req.domain_tag or "", 1 if req.is_active else 0,
                 now, vec_literal, req.datasource_id or 0),
            )
        conn.commit()
        return {"success": True, "id": row_id}
    finally:
        conn.close()


@router.put("/table-info/{row_id}")
def update_table_info(row_id: int, req: TableInfoUpdate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, table_comment, table_business_desc, region_tag, domain_tag, is_active, datasource_id "
                "FROM adh_table_info WHERE id = %s", (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="表信息不存在")

            # Merge updates
            table_comment = req.table_comment if req.table_comment is not None else (row["table_comment"] or "")
            table_business_desc = req.table_business_desc if req.table_business_desc is not None else (row["table_business_desc"] or "")
            keywords = req.keywords if req.keywords is not None else (row.get("keywords") or "")
            domain_tag = req.domain_tag if req.domain_tag is not None else (row["domain_tag"] or "")
            region_tag = req.region_tag if req.region_tag is not None else (row["region_tag"] or "")
            is_active = req.is_active if req.is_active is not None else bool(row["is_active"])

            # Re-generate embedding using keywords (not business_desc)
            embed_text = _table_embed_text(row['table_name'], table_comment, keywords, region_tag, domain_tag)
            vec_literal = _make_embedding(embed_text)
            now = _now()

            # Doris DUPLICATE KEY table doesn't support UPDATE, use DELETE + INSERT
            cur.execute("DELETE FROM adh_table_info WHERE id = %s", (row_id,))
            cur.execute(
                "INSERT INTO adh_table_info "
                "(id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, is_active, sync_time, embedding, datasource_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, row["table_name"], table_comment, table_business_desc, keywords,
                 region_tag, domain_tag, 1 if is_active else 0, now, vec_literal, row.get("datasource_id", 0)),
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/table-info/{row_id}")
def delete_table_info(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_table_info WHERE id = %s", (row_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


# ── Metadata Clear ──────────────────────────────────────────────────────

@router.post("/metadata/clear-by-datasource")
def clear_metadata_by_datasource(req: dict, admin: UserInfo = Depends(require_admin)):
    """Clear all metadata for a datasource (table_info + column_metadata + table_relations)."""
    datasource_id = req.get("datasource_id", 0)
    if not datasource_id:
        return {"success": False, "message": "请选择要清理的数据源"}

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Count before delete
            cur.execute("SELECT COUNT(*) AS cnt FROM adh_table_info WHERE datasource_id = %s", (datasource_id,))
            table_count = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM adh_column_metadata WHERE datasource_id = %s", (datasource_id,))
            col_count = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM adh_table_relations WHERE datasource_id = %s", (datasource_id,))
            rel_count = cur.fetchone()["cnt"]

            total = table_count + col_count + rel_count
            if total == 0:
                return {"success": True, "message": "该数据源下没有元数据"}

            # Delete
            cur.execute("DELETE FROM adh_table_info WHERE datasource_id = %s", (datasource_id,))
            cur.execute("DELETE FROM adh_column_metadata WHERE datasource_id = %s", (datasource_id,))
            cur.execute("DELETE FROM adh_table_relations WHERE datasource_id = %s", (datasource_id,))

        conn.commit()
        return {
            "success": True,
            "message": f"已清理 {table_count} 条表信息、{col_count} 条字段元数据、{rel_count} 条关联关系",
        }
    finally:
        conn.close()


@router.post("/metadata/clear-by-table")
def clear_metadata_by_table(req: dict, admin: UserInfo = Depends(require_admin)):
    """Clear metadata for a specific table (table_info + column_metadata + related table_relations)."""
    datasource_id = req.get("datasource_id", 0)
    table_name = req.get("table_name", "").strip()
    if not datasource_id:
        return {"success": False, "message": "请选择要清理的数据源"}
    if not table_name:
        return {"success": False, "message": "请输入要清理的表名"}

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Count before delete
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM adh_table_info WHERE datasource_id = %s AND table_name = %s",
                (datasource_id, table_name),
            )
            table_count = cur.fetchone()["cnt"]
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM adh_column_metadata WHERE datasource_id = %s AND table_name = %s",
                (datasource_id, table_name),
            )
            col_count = cur.fetchone()["cnt"]
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM adh_table_relations "
                "WHERE datasource_id = %s AND (source_table = %s OR target_table = %s)",
                (datasource_id, table_name, table_name),
            )
            rel_count = cur.fetchone()["cnt"]

            total = table_count + col_count + rel_count
            if total == 0:
                return {"success": True, "message": f"表 {table_name} 没有元数据"}

            # Delete
            cur.execute(
                "DELETE FROM adh_table_info WHERE datasource_id = %s AND table_name = %s",
                (datasource_id, table_name),
            )
            cur.execute(
                "DELETE FROM adh_column_metadata WHERE datasource_id = %s AND table_name = %s",
                (datasource_id, table_name),
            )
            cur.execute(
                "DELETE FROM adh_table_relations "
                "WHERE datasource_id = %s AND (source_table = %s OR target_table = %s)",
                (datasource_id, table_name, table_name),
            )

        conn.commit()
        return {
            "success": True,
            "message": f"已清理表 {table_name} 的 {table_count} 条表信息、{col_count} 条字段元数据、{rel_count} 条关联关系",
        }
    finally:
        conn.close()


# ── SQL Templates ──────────────────────────────────────────────────────

@router.get("/templates")
def list_templates(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: str = Query("", description="搜索模板名称或分类"),
    datasource_id: int = Query(None, description="按数据源筛选"),
    admin: UserInfo = Depends(require_admin),
):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = []
            if datasource_id:
                conditions.append("(datasource_id = %s OR datasource_id = 0)")
                params.append(datasource_id)
            if search:
                conditions.append("(template_name LIKE %s OR category LIKE %s)")
                params.extend([f"%{search}%", f"%{search}%"])
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_sql_templates {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, template_id, template_name, category, intent_keywords, "
                f"sql_template, variables, description, rules, usage_count, is_active, datasource_id, created_at, updated_at "
                f"FROM adh_sql_templates {where} "
                f"ORDER BY usage_count DESC, template_name LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
            return {"total": total, "items": rows}
    finally:
        conn.close()


@router.get("/templates/{row_id}")
def get_template(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, template_id, template_name, category, intent_keywords, "
                "sql_template, variables, description, rules, usage_count, is_active, created_at, updated_at "
                "FROM adh_sql_templates WHERE id = %s", (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="模板不存在")
            for k in ("created_at", "updated_at"):
                if hasattr(row.get(k), "isoformat"):
                    row[k] = row[k].isoformat()
            return row
    finally:
        conn.close()


@router.post("/templates")
def create_template(req: TemplateCreate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Check duplicate template_id
            cur.execute("SELECT id FROM adh_sql_templates WHERE template_id = %s", (req.template_id,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail=f"模板ID '{req.template_id}' 已存在")

            now = _now()
            row_id = int(_time.time() * 1000000)
            embed_text = f"{req.template_name} {req.intent_keywords} {req.description}"
            vec_literal = _make_embedding(embed_text)

            cur.execute(
                "INSERT INTO adh_sql_templates "
                "(id, template_id, template_name, category, intent_keywords, "
                "sql_template, variables, description, rules, usage_count, is_active, "
                "datasource_id, created_at, updated_at, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s)",
                (row_id, req.template_id, req.template_name, req.category,
                 req.intent_keywords, req.sql_template, req.variables or "",
                 req.description or "", req.rules or "", 1 if req.is_active else 0,
                 req.datasource_id or 0, now, now, vec_literal),
            )
        conn.commit()
        return {"success": True, "id": row_id}
    finally:
        conn.close()


@router.put("/templates/{row_id}")
def update_template(row_id: int, req: TemplateUpdate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT template_id, template_name, category, intent_keywords, sql_template, "
                "variables, description, rules, usage_count, is_active, datasource_id FROM adh_sql_templates WHERE id = %s",
                (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="模板不存在")

            # Merge updates
            fields = {}
            for f in ("template_name", "category", "intent_keywords", "sql_template",
                       "variables", "description", "rules"):
                val = getattr(req, f, None)
                if val is not None:
                    fields[f] = val
                else:
                    fields[f] = row.get(f) or ""
            is_active = req.is_active if req.is_active is not None else bool(row["is_active"])

            embed_text = f"{fields['template_name']} {fields['intent_keywords']} {fields['description']}"
            vec_literal = _make_embedding(embed_text)
            now = _now()

            datasource_id = req.datasource_id if req.datasource_id is not None else row.get("datasource_id", 0)

            # Doris DUPLICATE KEY table doesn't support UPDATE, use DELETE + INSERT
            cur.execute("DELETE FROM adh_sql_templates WHERE id = %s", (row_id,))
            conn.commit()  # DELETE must commit before INSERT with same PK in Doris
            cur.execute(
                "INSERT INTO adh_sql_templates "
                "(id, template_id, template_name, category, intent_keywords, "
                "sql_template, variables, description, rules, usage_count, is_active, "
                "datasource_id, created_at, updated_at, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, row.get("template_id", ""), fields["template_name"], fields["category"],
                 fields["intent_keywords"], fields["sql_template"], fields["variables"],
                 fields["description"], fields.get("rules", ""), row.get("usage_count", 0),
                 1 if is_active else 0, datasource_id, now, now, vec_literal),
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/templates/{row_id}")
def delete_template(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_sql_templates WHERE id = %s", (row_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


# ── Business Terms ─────────────────────────────────────────────────────

@router.get("/terms")
def list_terms(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: str = Query("", description="搜索术语名称"),
    datasource_id: int = Query(None, description="按数据源筛选"),
    admin: UserInfo = Depends(require_admin),
):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = []
            if datasource_id:
                conditions.append("(datasource_id = %s OR datasource_id = 0)")
                params.append(datasource_id)
            if search:
                conditions.append("(term_cn LIKE %s OR term_en LIKE %s OR term_aliases LIKE %s)")
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_business_terms {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, term_cn, term_en, term_aliases, term_type, "
                f"target_table, target_column, calculation, description, usage_count, "
                f"is_active, created_at, updated_at "
                f"FROM adh_business_terms {where} "
                f"ORDER BY usage_count DESC, term_cn LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
            return {"total": total, "items": rows}
    finally:
        conn.close()


@router.get("/terms/{row_id}")
def get_term(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, term_cn, term_en, term_aliases, term_type, "
                "target_table, target_column, calculation, description, usage_count, "
                "is_active, created_at, updated_at "
                "FROM adh_business_terms WHERE id = %s", (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="术语不存在")
            for k in ("created_at", "updated_at"):
                if hasattr(row.get(k), "isoformat"):
                    row[k] = row[k].isoformat()
            return row
    finally:
        conn.close()


@router.post("/terms")
def create_term(req: TermCreate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Check duplicate term_cn
            cur.execute("SELECT id FROM adh_business_terms WHERE term_cn = %s", (req.term_cn,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail=f"术语 '{req.term_cn}' 已存在")

            now = _now()
            row_id = int(_time.time() * 1000000)
            embed_text = f"{req.term_cn} {req.term_en or ''} {req.term_aliases or ''} {req.description or ''}"
            vec_literal = _make_embedding(embed_text)

            cur.execute(
                "INSERT INTO adh_business_terms "
                "(id, term_cn, term_en, term_aliases, term_type, "
                "target_table, target_column, calculation, description, "
                "usage_count, is_active, created_at, updated_at, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 1, %s, %s, %s)",
                (row_id, req.term_cn, req.term_en or "", req.term_aliases or "",
                 req.term_type, req.target_table or "", req.target_column or "",
                 req.calculation or "", req.description or "", now, now, vec_literal),
            )
        conn.commit()
        return {"success": True, "id": row_id}
    finally:
        conn.close()


@router.put("/terms/{row_id}")
def update_term(row_id: int, req: TermUpdate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT term_cn, term_en, term_aliases, term_type, "
                "target_table, target_column, calculation, description, usage_count, is_active "
                "FROM adh_business_terms WHERE id = %s", (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="术语不存在")

            # Merge updates
            fields = {}
            for f in ("term_cn", "term_en", "term_aliases", "term_type",
                       "target_table", "target_column", "calculation", "description"):
                val = getattr(req, f, None)
                if val is not None:
                    fields[f] = val
                else:
                    fields[f] = row[f] or ""

            embed_text = f"{fields['term_cn']} {fields['term_en']} {fields['term_aliases']} {fields['description']}"
            vec_literal = _make_embedding(embed_text)
            now = _now()

            # Doris DUPLICATE KEY table doesn't support UPDATE, use DELETE + INSERT
            cur.execute("DELETE FROM adh_business_terms WHERE id = %s", (row_id,))
            cur.execute(
                "INSERT INTO adh_business_terms "
                "(id, term_cn, term_en, term_aliases, term_type, "
                "target_table, target_column, calculation, description, "
                "usage_count, is_active, created_at, updated_at, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, fields["term_cn"], fields["term_en"], fields["term_aliases"],
                 fields["term_type"], fields["target_table"], fields["target_column"],
                 fields["calculation"], fields["description"],
                 row.get("usage_count", 0), row.get("is_active", 1), now, now, vec_literal),
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/terms/{row_id}")
def delete_term(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_business_terms WHERE id = %s", (row_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.put("/terms/{row_id}/toggle")
def toggle_term(row_id: int, admin: UserInfo = Depends(require_admin)):
    """Toggle is_active status of a business term."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Get current is_active value
            cur.execute("SELECT is_active FROM adh_business_terms WHERE id = %s", (row_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="术语不存在")

            # Toggle: if is_active is NULL or 1 -> set to 0, otherwise set to 1
            current = row.get("is_active")
            new_val = 0 if (current is None or current == 1) else 1

            # Doris DUPLICATE KEY doesn't support UPDATE, use DELETE + INSERT
            cur.execute(
                "SELECT id, term_cn, term_en, term_aliases, term_type, "
                "target_table, target_column, calculation, description, usage_count, "
                "created_at, updated_at, datasource_id "
                "FROM adh_business_terms WHERE id = %s", (row_id,),
            )
            full_row = cur.fetchone()

            # Get embedding
            cur.execute("SELECT embedding FROM adh_business_terms WHERE id = %s", (row_id,))
            emb_row = cur.fetchone()

            cur.execute("DELETE FROM adh_business_terms WHERE id = %s", (row_id,))

            now = _now()
            # Build embedding literal from existing data
            embed_text = f"{full_row['term_cn']} {full_row.get('term_en') or ''} {full_row.get('term_aliases') or ''} {full_row.get('description') or ''}"
            vec_literal = _make_embedding(embed_text)

            cur.execute(
                "INSERT INTO adh_business_terms "
                "(id, term_cn, term_en, term_aliases, term_type, "
                "target_table, target_column, calculation, description, "
                "usage_count, is_active, created_at, updated_at, datasource_id, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, full_row["term_cn"], full_row.get("term_en") or "",
                 full_row.get("term_aliases") or "", full_row.get("term_type") or "",
                 full_row.get("target_table") or "", full_row.get("target_column") or "",
                 full_row.get("calculation") or "", full_row.get("description") or "",
                 full_row.get("usage_count") or 0, new_val,
                 full_row.get("created_at") or now, now,
                 full_row.get("datasource_id") or 0, vec_literal),
            )
        conn.commit()
        return {"success": True, "is_active": new_val}
    finally:
        conn.close()


# ── Brand Settings ──────────────────────────────────────────────────────

@router.get("/brand")
def get_brand_settings():
    """Get brand settings — public, no auth required."""
    return _load_brand_settings()


# ── Table Relations (ER) ────────────────────────────────────────────────

@router.get("/relations")
def list_relations(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    table_name: str = Query("", description="搜索源表或目标表"),
    datasource_id: int = Query(None, description="按数据源筛选"),
    admin: UserInfo = Depends(require_admin),
):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = []
            if datasource_id:
                conditions.append("datasource_id = %s")
                params.append(datasource_id)
            if table_name:
                conditions.append("(source_table LIKE %s OR target_table LIKE %s)")
                params.extend([f"%{table_name}%", f"%{table_name}%"])
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_table_relations {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT id, datasource_id, source_table, source_column, "
                f"target_table, target_column, relation_type, join_type, "
                f"description, is_active, created_at, updated_at "
                f"FROM adh_table_relations {where} "
                f"ORDER BY source_table, target_table LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                for k in ("created_at", "updated_at"):
                    if hasattr(r.get(k), "isoformat"):
                        r[k] = r[k].isoformat()
            return {"total": total, "items": rows}
    finally:
        conn.close()


@router.get("/relations/{row_id}")
def get_relation(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, datasource_id, source_table, source_column, "
                "target_table, target_column, relation_type, join_type, "
                "description, is_active, created_at, updated_at "
                "FROM adh_table_relations WHERE id = %s", (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="关联关系不存在")
            for k in ("created_at", "updated_at"):
                if hasattr(row.get(k), "isoformat"):
                    row[k] = row[k].isoformat()
            return row
    finally:
        conn.close()


@router.post("/relations")
def create_relation(req: RelationCreate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Check duplicate relation
            cur.execute(
                "SELECT id FROM adh_table_relations "
                "WHERE source_table = %s AND source_column = %s "
                "AND target_table = %s AND target_column = %s AND datasource_id = %s",
                (req.source_table, req.source_column,
                 req.target_table, req.target_column, req.datasource_id or 0),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"关联关系 {req.source_table}.{req.source_column} → {req.target_table}.{req.target_column} 已存在",
                )

            now = _now()
            row_id = int(_time.time() * 1000000)
            embed_text = (
                f"{req.source_table}.{req.source_column} → {req.target_table}.{req.target_column} "
                f"{req.relation_type} {req.description or ''}"
            )
            vec_literal = _make_embedding(embed_text)

            cur.execute(
                "INSERT INTO adh_table_relations "
                "(id, datasource_id, source_table, source_column, target_table, target_column, "
                "relation_type, join_type, description, is_active, created_at, updated_at, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, req.datasource_id or 0, req.source_table, req.source_column,
                 req.target_table, req.target_column, req.relation_type, req.join_type,
                 req.description or "", 1 if req.is_active else 0, now, now, vec_literal),
            )
        conn.commit()
        return {"success": True, "id": row_id}
    finally:
        conn.close()


@router.put("/relations/{row_id}")
def update_relation(row_id: int, req: RelationUpdate, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, datasource_id, source_table, source_column, "
                "target_table, target_column, relation_type, join_type, "
                "description, is_active "
                "FROM adh_table_relations WHERE id = %s", (row_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="关联关系不存在")

            # Merge updates
            source_column = req.source_column if req.source_column is not None else row["source_column"]
            target_column = req.target_column if req.target_column is not None else row["target_column"]
            relation_type = req.relation_type if req.relation_type is not None else row["relation_type"]
            join_type = req.join_type if req.join_type is not None else row["join_type"]
            description = req.description if req.description is not None else (row["description"] or "")
            is_active = req.is_active if req.is_active is not None else bool(row["is_active"])

            embed_text = (
                f"{row['source_table']}.{source_column} → {row['target_table']}.{target_column} "
                f"{relation_type} {description}"
            )
            vec_literal = _make_embedding(embed_text)
            now = _now()

            # Doris DUPLICATE KEY table doesn't support UPDATE, use DELETE + INSERT
            cur.execute("DELETE FROM adh_table_relations WHERE id = %s", (row_id,))
            cur.execute(
                "INSERT INTO adh_table_relations "
                "(id, datasource_id, source_table, source_column, target_table, target_column, "
                "relation_type, join_type, description, is_active, created_at, updated_at, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, row.get("datasource_id", 0), row["source_table"], source_column,
                 row["target_table"], target_column, relation_type, join_type,
                 description, 1 if is_active else 0, now, now, vec_literal),
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/relations/{row_id}")
def delete_relation(row_id: int, admin: UserInfo = Depends(require_admin)):
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_table_relations WHERE id = %s", (row_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.post("/sync/relations")
def sync_relations(req: dict = {}, admin: UserInfo = Depends(require_admin)):
    """Auto-detect and sync table relations from MySQL foreign keys."""
    datasource_id = req.get("datasource_id", 0) if req else 0
    if not datasource_id:
        return {"success": False, "message": "请选择要同步的数据源"}
    try:
        from sync.metadata_sync import sync_table_relations as _sync_rels
        result = _sync_rels(datasource_id)
        return {
            "success": True,
            "message": f"表关联同步完成：新增 {result['inserted']}，更新 {result['updated']}，删除 {result['deleted']}",
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.put("/brand")
def update_brand_settings(req: BrandSettingsUpdate, admin: UserInfo = Depends(require_admin)):
    """Update brand settings — admin only."""
    current = _load_brand_settings()
    if req.app_name is not None:
        current["app_name"] = req.app_name
    if req.logo_url is not None:
        current["logo_url"] = req.logo_url
    if req.show_icon is not None:
        current["show_icon"] = req.show_icon
    if req.show_text is not None:
        current["show_text"] = req.show_text
    _save_brand_settings(current)
    return current


# ── Embedding Model ────────────────────────────────────────────────────

@router.get("/embedding")
def get_embedding_model(admin: UserInfo = Depends(require_admin)):
    """Get current embedding model info."""
    from backend.common.llm.embedding import get_model_info
    return get_model_info()


@router.put("/embedding")
def update_embedding_model(req: dict, admin: UserInfo = Depends(require_admin)):
    """Switch embedding model with dimension validation.

    Body: {"model_path": "BAAI/bge-base-zh-v1.5"}
    Loads the model, validates 768-dim output, then activates.
    """
    model_path = req.get("model_path", "").strip()
    if not model_path:
        raise HTTPException(status_code=400, detail="模型路径不能为空")

    try:
        from backend.common.llm.model_manager import set_active_model
        info = set_active_model(model_path)
        return {"success": True, **info}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模型切换失败: {e}")


@router.get("/embedding/models")
def list_embedding_models(admin: UserInfo = Depends(require_admin)):
    """List preset + locally downloaded models (deduped)."""
    from backend.common.llm.model_manager import list_models
    return list_models()


@router.post("/embedding/search")
def search_embedding_models(req: dict, admin: UserInfo = Depends(require_admin)):
    """Search HuggingFace API for models (opt-in, with timeout).

    Body: {"timeout": 15}  — max seconds to wait
    """
    timeout = req.get("timeout", 15)
    try:
        from backend.common.llm.model_manager import search_online_models
        models = search_online_models(timeout=timeout)
        return {"models": models, "count": len(models)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/embedding/install")
def install_embedding_model(req: dict, admin: UserInfo = Depends(require_admin)):
    """Start installing an embedding model in background (non-blocking).

    Body: {
        "model_id": "BAAI/bge-base-zh-v1.5",
        "hf_endpoint": "https://hf-mirror.com"  // optional custom mirror
    }
    Returns immediately. Use GET /embedding/install/progress/{model_id} to poll.
    """
    model_id = req.get("model_id", "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="模型 ID 不能为空")

    hf_endpoint = req.get("hf_endpoint", "").strip() or None

    try:
        from backend.common.llm.model_manager import start_install_async
        start_install_async(model_id, hf_endpoint=hf_endpoint)
        return {"success": True, "model_id": model_id, "message": "安装已开始"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/embedding/install/progress/{model_id:path}")
def get_install_progress(model_id: str, admin: UserInfo = Depends(require_admin)):
    """Get installation progress for a model.

    Returns: {"status": "downloading|done|error", "percent": 0-100, "message": "..."}
    Returns 404 if no installation is in progress.
    """
    from backend.common.llm.model_manager import get_install_progress
    progress = get_install_progress(model_id)
    if not progress:
        return {"status": "idle", "percent": 0, "message": ""}
    return progress


@router.post("/embedding/uninstall")
def uninstall_embedding_model(req: dict, admin: UserInfo = Depends(require_admin)):
    """Uninstall a downloaded embedding model.

    Body: {"model_id": "BAAI/bge-base-zh-v1.5"}
    """
    model_id = req.get("model_id", "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="模型 ID 不能为空")

    from backend.common.llm.model_manager import uninstall_model
    result = uninstall_model(model_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "卸载失败"))
    return result

_ANALYSIS_MENU_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "analysis_menu.json"


def _load_analysis_menu() -> list:
    if _ANALYSIS_MENU_PATH.exists():
        try:
            with open(_ANALYSIS_MENU_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_analysis_menu(items: list):
    _ANALYSIS_MENU_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_ANALYSIS_MENU_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


@router.get("/analysis-menu")
def get_analysis_menu():
    """Get analysis menu items — public, no auth required."""
    return _load_analysis_menu()


@router.put("/analysis-menu")
def update_analysis_menu(req: dict, admin: UserInfo = Depends(require_admin)):
    """Update analysis menu items — admin only. Expects {items: [...]}."""
    items = req.get("items", [])
    # Validate: each item must have name and dashboard_id
    for item in items:
        if not item.get("name"):
            raise HTTPException(status_code=400, detail="菜单项名称不能为空")
        if not item.get("dashboard_id"):
            raise HTTPException(status_code=400, detail="请选择关联的仪表盘")
    _save_analysis_menu(items)
    return {"success": True, "items": items}


# ── Screen Menu ──────────────────────────────────────────────────────────

_SCREEN_MENU_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "screen_menu.json"


def _load_screen_menu() -> list:
    if _SCREEN_MENU_PATH.exists():
        try:
            with open(_SCREEN_MENU_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_screen_menu(items: list):
    _SCREEN_MENU_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_SCREEN_MENU_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


@router.get("/screen-menu")
def get_screen_menu():
    """Get screen menu items — public, no auth required."""
    return _load_screen_menu()


@router.put("/screen-menu")
def update_screen_menu(req: dict, admin: UserInfo = Depends(require_admin)):
    """Update screen menu items — admin only. Expects {items: [...]}."""
    items = req.get("items", [])
    for item in items:
        if not item.get("name"):
            raise HTTPException(status_code=400, detail="菜单项名称不能为空")
        if not item.get("dashboard_id"):
            raise HTTPException(status_code=400, detail="请选择关联的仪表盘")
    _save_screen_menu(items)
    return {"success": True, "items": items}


# ── Cache Stats ─────────────────────────────────────────────────────────

@router.get("/cache/stats")
def get_cache_stats(admin: UserInfo = Depends(require_admin)):
    """Get cache statistics — admin only."""
    return {
        "caches": [
            datasource_cache.stats(),
            menu_cache.stats(),
            dashboard_cache.stats(),
            brand_cache.stats(),
            metadata_cache.stats(),
        ]
    }


@router.post("/cache/clear")
def clear_all_caches(admin: UserInfo = Depends(require_admin)):
    """Clear all caches — admin only."""
    datasource_cache.invalidate()
    menu_cache.invalidate()
    dashboard_cache.invalidate()
    brand_cache.invalidate()
    metadata_cache.invalidate()
    return {"success": True, "message": "所有缓存已清除"}


# ── MCP Server CRUD ────────────────────────────────────────────────────

@router.get("/mcp-servers")
def list_mcp_servers(user: UserInfo = Depends(require_admin), workspace_id: int = 0):
    """List MCP servers, optionally filtered by workspace."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if workspace_id:
                cur.execute(
                    "SELECT id, name, description, transport, url, command, args, "
                    "`env`, tools_config, discovered_tools, is_active, datasource_id, workspace_id, "
                    "last_test_at, last_test_status, last_test_message, created_at, updated_at "
                    "FROM adh_mcp_servers WHERE workspace_id = %s ORDER BY id DESC",
                    (workspace_id,)
                )
            else:
                cur.execute(
                    "SELECT id, name, description, transport, url, command, args, "
                    "`env`, tools_config, discovered_tools, is_active, datasource_id, workspace_id, "
                    "last_test_at, last_test_status, last_test_message, created_at, updated_at "
                    "FROM adh_mcp_servers ORDER BY id DESC"
                )
            rows = cur.fetchall()
        return rows
    finally:
        conn.close()


@router.post("/mcp-servers")
def create_mcp_server(req: dict, admin: UserInfo = Depends(require_admin)):
    """Create a new MCP server."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            row_id = int(_time.time() * 1000000)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "INSERT INTO adh_mcp_servers "
                "(id, name, description, transport, url, command, args, `env`, tools_config, "
                "is_active, datasource_id, workspace_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (row_id, req.get("name", ""), req.get("description", ""),
                 req.get("transport", "sse"), req.get("url", ""),
                 req.get("command", ""), req.get("args", ""),
                 req.get("env", ""), req.get("tools_config", ""),
                 req.get("is_active", 1), req.get("datasource_id", 0),
                 req.get("workspace_id", 0), now, now),
            )
        conn.commit()
        return {"id": row_id, "success": True}
    finally:
        conn.close()


@router.put("/mcp-servers/{row_id}")
async def update_mcp_server(row_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update an MCP server. Disconnects client if disabling (is_active=0)."""
    # If disabling, disconnect active client first
    if req.get("is_active") == 0:
        try:
            from backend.mcp_client.registry import get_mcp_registry
            registry = get_mcp_registry()
            client = registry._clients.pop(row_id, None)
            if client:
                await client.disconnect()
                logger.info("[MCP] Disconnected disabled client %d", row_id)
        except Exception as e:
            logger.warning("[MCP] Error disconnecting client %d: %s", row_id, e)

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            fields = []
            params = []
            for key in ("name", "description", "transport", "url", "command",
                        "args", "env", "tools_config", "is_active", "datasource_id", "workspace_id"):
                if key in req:
                    fields.append(f"{key} = %s")
                    params.append(req[key])
            if not fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            fields.append("updated_at = %s")
            params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            params.append(row_id)
            cur.execute(f"UPDATE adh_mcp_servers SET {', '.join(fields)} WHERE id = %s", params)
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/mcp-servers/{row_id}")
async def delete_mcp_server(row_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete an MCP server and disconnect its active client (kills stdio process)."""
    # 1. Disconnect active client (kills stdio subprocess if running)
    try:
        from backend.mcp_client.registry import get_mcp_registry
        registry = get_mcp_registry()
        client = registry._clients.pop(row_id, None)
        if client:
            await client.disconnect()
            logger.info("[MCP] Disconnected client for server %d", row_id)
    except Exception as e:
        logger.warning("[MCP] Error disconnecting client %d: %s", row_id, e)

    # 2. Delete from database
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_mcp_servers WHERE id = %s", (row_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.post("/mcp-servers/{row_id}/test")
async def test_mcp_server(row_id: int, admin: UserInfo = Depends(require_admin)):
    """Test MCP server connection and discover tools."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, transport, url, command, args, `env` "
                "FROM adh_mcp_servers WHERE id = %s",
                (row_id,),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="MCP server not found")

        # Parse args
        args = row.get("args", "")
        if isinstance(args, str):
            args = [a.strip() for a in args.split(",") if a.strip()]

        # Parse env
        env_raw = row.get("env", "")
        env = {}
        if isinstance(env_raw, str) and env_raw.strip():
            try:
                env = json.loads(env_raw)
            except json.JSONDecodeError:
                for line in env_raw.strip().split("\n"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip()

        from backend.mcp_client.client import MCPClient
        client = MCPClient(
            server_id=row["id"],
            name=row["name"],
            transport=row.get("transport", "sse"),
            url=row.get("url", ""),
            command=row.get("command", ""),
            args=args,
            env=env,
        )

        result = await client.test_connection()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Update test results in database
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_mcp_servers SET "
                "last_test_at = %s, last_test_status = %s, last_test_message = %s, "
                "discovered_tools = %s, updated_at = %s WHERE id = %s",
                (
                    now,
                    "success" if result["success"] else "failed",
                    result["message"],
                    json.dumps(result["tools"], ensure_ascii=False) if result["tools"] else "",
                    now,
                    row_id,
                ),
            )
        conn.commit()

        return result
    finally:
        conn.close()


@router.get("/mcp-servers/{row_id}/tools")
def get_mcp_server_tools(row_id: int, user: UserInfo = Depends(require_admin)):
    """Get discovered tools for an MCP server."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT discovered_tools, tools_config FROM adh_mcp_servers WHERE id = %s",
                (row_id,),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="MCP server not found")

        tools = []
        if row.get("discovered_tools"):
            try:
                tools = json.loads(row["discovered_tools"])
            except json.JSONDecodeError:
                pass

        # Fallback to manually configured tools
        if not tools and row.get("tools_config"):
            try:
                tools = json.loads(row["tools_config"])
            except json.JSONDecodeError:
                pass

        return {"tools": tools}
    finally:
        conn.close()


# ── Agent CRUD ─────────────────────────────────────────────────────────

@router.get("/agents")
def list_agents(user: UserInfo = Depends(require_admin), workspace_id: int = 0):
    """List all agents, optionally filtered by workspace."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if workspace_id:
                cur.execute(
                    "SELECT id, name, display_name, description, agent_type, "
                    "system_prompt, mcp_server_ids, datasource_ids, tools, config, "
                    "route_patterns, is_active, is_default, created_at, updated_at "
                    "FROM adh_agents WHERE workspace_id = %s ORDER BY id DESC",
                    (workspace_id,)
                )
            else:
                cur.execute(
                    "SELECT id, name, display_name, description, agent_type, "
                    "system_prompt, mcp_server_ids, datasource_ids, tools, config, "
                    "route_patterns, is_active, is_default, created_at, updated_at "
                    "FROM adh_agents ORDER BY id DESC"
                )
            rows = cur.fetchall()
        return rows
    finally:
        conn.close()


@router.post("/agents")
def create_agent(req: dict, admin: UserInfo = Depends(require_admin)):
    """Create a new agent."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            row_id = int(_time.time() * 1000000)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            workspace_id = req.get("workspace_id", 0)
            cur.execute(
                "INSERT INTO adh_agents "
                "(id, name, display_name, description, agent_type, system_prompt, "
                "mcp_server_ids, datasource_ids, tools, config, route_patterns, "
                "is_active, is_default, workspace_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (row_id, req.get("name", ""), req.get("display_name", ""),
                 req.get("description", ""), req.get("agent_type", "custom"),
                 req.get("system_prompt", ""), req.get("mcp_server_ids", ""),
                 req.get("datasource_ids", ""),
                 req.get("tools", ""), req.get("config", ""),
                 req.get("route_patterns", ""),
                 req.get("is_active", 1), req.get("is_default", 0),
                 workspace_id, now, now),
            )
        conn.commit()
        # Reload route patterns cache
        try:
            from backend.agent.router import reload_route_patterns
            reload_route_patterns()
        except Exception:
            pass
        return {"id": row_id, "success": True}
    finally:
        conn.close()


@router.put("/agents/{row_id}")
def update_agent(row_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update an agent."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            fields = []
            params = []
            for key in ("name", "display_name", "description", "agent_type",
                        "system_prompt", "mcp_server_ids", "datasource_ids", "tools", "config",
                        "route_patterns", "is_active", "is_default", "workspace_id"):
                if key in req:
                    fields.append(f"{key} = %s")
                    params.append(req[key])
            if not fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            fields.append("updated_at = %s")
            params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            params.append(row_id)
            cur.execute(f"UPDATE adh_agents SET {', '.join(fields)} WHERE id = %s", params)
        conn.commit()
        # Reload route patterns cache
        try:
            from backend.agent.router import reload_route_patterns
            reload_route_patterns()
        except Exception:
            pass
        return {"success": True}
    finally:
        conn.close()


@router.delete("/agents/{row_id}")
def delete_agent(row_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete an agent."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_agents WHERE id = %s", (row_id,))
        conn.commit()
        # Reload route patterns cache
        try:
            from backend.agent.router import reload_route_patterns
            reload_route_patterns()
        except Exception:
            pass
        return {"success": True}
    finally:
        conn.close()
