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
from backend.common.auth import log_audit
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
        log_audit(admin.id, admin.username, "sync_metadata",
                  target_type="datasource", target_id=datasource_id,
                  detail=f"同步元数据 ds_id={datasource_id}", module="metadata")
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
    size: int = Query(50, ge=1, le=9999),
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
        log_audit(admin.id, admin.username, "update_metadata",
                  target_type="metadata", target_id=row_id,
                  detail=f"更新字段元数据 {row['table_name']}.{row['column_name']}", module="metadata")
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
        log_audit(admin.id, admin.username, "create_metadata",
                  target_type="metadata", target_id=row_id,
                  detail=f"创建字段元数据 {req.table_name}.{req.column_name}", module="metadata")
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
        log_audit(admin.id, admin.username, "delete_metadata",
                  target_type="metadata", target_id=row_id,
                  detail=f"删除字段元数据 id={row_id}", module="metadata")
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
        log_audit(admin.id, admin.username, "create_table_info",
                  target_type="table_info", target_id=row_id,
                  detail=f"创建表信息 {req.table_name}", module="metadata")
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
        log_audit(admin.id, admin.username, "update_table_info",
                  target_type="table_info", target_id=row_id,
                  detail=f"更新表信息 {row['table_name']}", module="metadata")
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
        log_audit(admin.id, admin.username, "delete_table_info",
                  target_type="table_info", target_id=row_id,
                  detail=f"删除表信息 id={row_id}", module="metadata")
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
        log_audit(admin.id, admin.username, "clear_metadata_by_datasource",
                  target_type="datasource", target_id=datasource_id,
                  detail=f"清理数据源元数据: {table_count}表 + {col_count}字段 + {rel_count}关联", module="metadata")
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
        log_audit(admin.id, admin.username, "clear_metadata_by_table",
                  target_type="table", target_id=datasource_id,
                  detail=f"清理表 {table_name} 元数据: {table_count}表 + {col_count}字段 + {rel_count}关联", module="metadata")
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
        log_audit(admin.id, admin.username, "create_template",
                  target_type="template", target_id=row_id,
                  detail=f"创建SQL模板 {req.template_name}", module="metadata")
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
        log_audit(admin.id, admin.username, "update_template",
                  target_type="template", target_id=row_id,
                  detail=f"更新SQL模板 {fields['template_name']}", module="metadata")
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
        log_audit(admin.id, admin.username, "delete_template",
                  target_type="template", target_id=row_id,
                  detail=f"删除SQL模板 id={row_id}", module="metadata")
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
        log_audit(admin.id, admin.username, "create_term",
                  target_type="term", target_id=row_id,
                  detail=f"创建业务术语 {req.term_cn}", module="metadata")
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
        log_audit(admin.id, admin.username, "update_term",
                  target_type="term", target_id=row_id,
                  detail=f"更新业务术语 {fields['term_cn']}", module="metadata")
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
        log_audit(admin.id, admin.username, "delete_term",
                  target_type="term", target_id=row_id,
                  detail=f"删除业务术语 id={row_id}", module="metadata")
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
        log_audit(admin.id, admin.username, "create_relation",
                  target_type="relation", target_id=row_id,
                  detail=f"创建关联关系 {req.source_table}.{req.source_column} → {req.target_table}.{req.target_column}", module="metadata")
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
        log_audit(admin.id, admin.username, "update_relation",
                  target_type="relation", target_id=row_id,
                  detail=f"更新关联关系 {row['source_table']}.{source_column} → {row['target_table']}.{target_column}", module="metadata")
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
        log_audit(admin.id, admin.username, "delete_relation",
                  target_type="relation", target_id=row_id,
                  detail=f"删除关联关系 id={row_id}", module="metadata")
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
        log_audit(admin.id, admin.username, "sync_relations",
                  target_type="datasource", target_id=datasource_id,
                  detail=f"同步表关联: 新增{result['inserted']} 更新{result['updated']} 删除{result['deleted']}", module="metadata")
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
    log_audit(admin.id, admin.username, "update_brand_settings",
              target_type="system", detail="更新品牌设置", module="system")
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


# ═══════════════════════════════════════════════════════════════════
# Skills 管理
# ═══════════════════════════════════════════════════════════════════

@router.get("/skills")
def list_skills(
    category: str = Query(None),
    user: UserInfo = Depends(require_admin),
):
    """List all skills (file system + DB merged)."""
    from backend.config.skill_loader import list_skills as _list_skills
    skills = _list_skills(category=category)
    return skills


@router.get("/skills/{name}")
def get_skill(name: str, user: UserInfo = Depends(require_admin)):
    """Get a single skill by name (includes system_prompt)."""
    from backend.config.skill_loader import load_skill
    skill = load_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return skill


@router.post("/skills")
def create_skill(req: dict, admin: UserInfo = Depends(require_admin)):
    """Create a user-defined skill."""
    name = req.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Skill name is required")

    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Check for duplicate name
            cur.execute("SELECT id FROM adh_skills WHERE name = %s", (name,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail=f"Skill '{name}' already exists")

            row_id = int(_time.time() * 1000000)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            workspace_id = req.get("workspace_id", 0)
            skill_config = req.get("skill_config", "")
            if isinstance(skill_config, dict):
                import json
                skill_config = json.dumps(skill_config, ensure_ascii=False)
            cur.execute(
                "INSERT INTO adh_skills "
                "(id, workspace_id, name, display_name, description, category, "
                "system_prompt, skill_config, source_type, source_skill, is_active, "
                "created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (row_id, workspace_id, name,
                 req.get("display_name", ""), req.get("description", ""),
                 req.get("category", "analysis"),
                 req.get("system_prompt", ""), skill_config,
                 req.get("source_type", "user"), req.get("source_skill", ""),
                 req.get("is_active", 1), now, now),
            )
        conn.commit()
        return {"id": row_id, "success": True}
    finally:
        conn.close()


@router.put("/skills/{row_id}")
def update_skill(row_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update a user-defined skill."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Only allow updating user-created skills or system skill overrides
            fields = []
            params = []
            for key in ("name", "display_name", "description", "category",
                        "system_prompt", "skill_config", "source_type",
                        "source_skill", "is_active", "workspace_id"):
                if key in req:
                    val = req[key]
                    if key == "skill_config" and isinstance(val, dict):
                        import json
                        val = json.dumps(val, ensure_ascii=False)
                    fields.append(f"{key} = %s")
                    params.append(val)
            if not fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            fields.append("updated_at = %s")
            params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            params.append(row_id)
            cur.execute(f"UPDATE adh_skills SET {', '.join(fields)} WHERE id = %s", params)
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/skills/{row_id}")
def delete_skill(row_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete a skill (only user-created skills can be deleted)."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT source_type, name FROM adh_skills WHERE id = %s", (row_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Skill not found")
            if row.get("source_type") == "system":
                raise HTTPException(status_code=403, detail="Cannot delete system skills")
            cur.execute("DELETE FROM adh_skills WHERE id = %s", (row_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.post("/skills/{name}/copy")
def copy_skill(name: str, req: dict = {}, admin: UserInfo = Depends(require_admin)):
    """Copy a system skill to create a user-editable copy."""
    from backend.config.skill_loader import load_skill

    skill = load_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"System skill '{name}' not found")

    # Generate copy name
    copy_name = f"{name}_custom"
    suffix = 1
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Check if copy name already exists, increment suffix
            while True:
                cur.execute("SELECT id FROM adh_skills WHERE name = %s", (copy_name,))
                if not cur.fetchone():
                    break
                suffix += 1
                copy_name = f"{name}_custom{suffix}"

            row_id = int(_time.time() * 1000000)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            workspace_id = req.get("workspace_id", 0)
            cur.execute(
                "INSERT INTO adh_skills "
                "(id, workspace_id, name, display_name, description, category, "
                "system_prompt, skill_config, source_type, source_skill, is_active, "
                "created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (row_id, workspace_id, copy_name,
                 f"{skill.get('display_name', name)} (副本)",
                 skill.get("description", ""),
                 skill.get("category", "analysis"),
                 skill.get("system_prompt", ""),
                 json.dumps(skill.get("skill_config", {}), ensure_ascii=False) if isinstance(skill.get("skill_config"), dict) else (skill.get("skill_config") or "{}"),
                 "user", name, 1, now, now),
            )
        conn.commit()
        return {"id": row_id, "name": copy_name, "success": True}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# RLS (行级安全) 策略管理
# ═══════════════════════════════════════════════════════════════════

@router.get("/rls-policies")
def list_rls_policies(
    workspace_id: int = Query(...),
    datasource_id: int = Query(None),
    table_name: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: UserInfo = Depends(require_admin),
):
    """List RLS policies."""
    from backend.services.rls_service import rls_service
    return rls_service.list_policies(workspace_id, datasource_id, table_name, page, size)


@router.get("/rls-policies/{policy_id}")
def get_rls_policy(policy_id: int, admin: UserInfo = Depends(require_admin)):
    """Get a single RLS policy."""
    from backend.services.rls_service import rls_service
    policy = rls_service.get_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.post("/rls-policies")
def create_rls_policy(req: dict, admin: UserInfo = Depends(require_admin)):
    """Create a new RLS policy."""
    from backend.services.rls_service import rls_service
    if not req.get("name") or not req.get("datasource_id") or not req.get("table_name"):
        raise HTTPException(status_code=400, detail="name, datasource_id, table_name are required")
    req.setdefault("workspace_id", 0)
    req["created_by"] = admin.id
    policy_id = rls_service.create_policy(req)
    return {"success": True, "id": policy_id}


@router.put("/rls-policies/{policy_id}")
def update_rls_policy(policy_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update an RLS policy."""
    from backend.services.rls_service import rls_service
    ok = rls_service.update_policy(policy_id, req)
    if not ok:
        raise HTTPException(status_code=404, detail="Policy not found or no changes")
    return {"success": True}


@router.delete("/rls-policies/{policy_id}")
def delete_rls_policy(policy_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete an RLS policy and its column policies."""
    from backend.services.rls_service import rls_service
    ok = rls_service.delete_policy(policy_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Policy not found")
    return {"success": True}


@router.get("/rls-policies/{policy_id}/columns")
def get_rls_column_policies(policy_id: int, admin: UserInfo = Depends(require_admin)):
    """Get column-level policies for a given RLS policy."""
    from backend.services.rls_service import rls_service
    return rls_service.get_column_policies(policy_id)


@router.put("/rls-policies/{policy_id}/columns")
def set_rls_column_policies(policy_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Set column-level policies (replace all)."""
    from backend.services.rls_service import rls_service
    columns = req.get("columns", [])
    ok = rls_service.set_column_policies(policy_id, columns)
    return {"success": ok}


@router.get("/rls-user-attributes/{user_id}")
def get_rls_user_attributes(
    user_id: int,
    workspace_id: int = Query(...),
    admin: UserInfo = Depends(require_admin),
):
    """Get RLS attributes for a user in a workspace."""
    from backend.services.rls_service import rls_service
    return rls_service.get_user_attributes(user_id, workspace_id)


@router.put("/rls-user-attributes/{user_id}")
def set_rls_user_attributes(
    user_id: int,
    req: dict,
    admin: UserInfo = Depends(require_admin),
):
    """Set RLS attributes for a user (replace all)."""
    from backend.services.rls_service import rls_service
    workspace_id = req.get("workspace_id", 0)
    attrs = req.get("attributes", {})
    ok = rls_service.set_user_attributes(user_id, workspace_id, attrs)
    return {"success": ok}


@router.get("/rls-audit-logs")
def list_rls_audit_logs(
    workspace_id: int = Query(...),
    user_id: int = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: UserInfo = Depends(require_admin),
):
    """List RLS audit logs."""
    from backend.services.rls_service import rls_service
    return rls_service.list_audit_logs(workspace_id, user_id, page, size)


# ═══════════════════════════════════════════════════════════════════
# 知识管理
# ═══════════════════════════════════════════════════════════════════

@router.get("/knowledge")
def list_knowledge(
    workspace_id: int = Query(0),
    knowledge_type: str = Query(None),
    datasource_id: int = Query(None),
    search: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: UserInfo = Depends(require_admin),
):
    """List knowledge items."""
    from backend.services.knowledge_service import knowledge_service
    return knowledge_service.list_items(workspace_id, knowledge_type, datasource_id, search, page, size)


@router.get("/knowledge/stats")
def get_knowledge_stats(
    workspace_id: int = Query(0),
    admin: UserInfo = Depends(require_admin),
):
    """Get knowledge statistics."""
    from backend.services.knowledge_service import knowledge_service
    return knowledge_service.get_stats(workspace_id)


@router.get("/knowledge/{item_id}")
def get_knowledge(item_id: int, admin: UserInfo = Depends(require_admin)):
    """Get a single knowledge item."""
    from backend.services.knowledge_service import knowledge_service
    item = knowledge_service.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return item


@router.post("/knowledge")
def create_knowledge(req: dict, admin: UserInfo = Depends(require_admin)):
    """Create a knowledge item."""
    from backend.services.knowledge_service import knowledge_service
    if not req.get("knowledge_type") or not req.get("title") or not req.get("content"):
        raise HTTPException(status_code=400, detail="knowledge_type, title, content are required")
    if req["knowledge_type"] not in knowledge_service.VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid type. Must be one of: {knowledge_service.VALID_TYPES}")
    req["created_by"] = admin.id
    item_id = knowledge_service.create_item(req)
    return {"success": True, "id": item_id}


@router.put("/knowledge/{item_id}")
def update_knowledge(item_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update a knowledge item."""
    from backend.services.knowledge_service import knowledge_service
    ok = knowledge_service.update_item(item_id, req)
    if not ok:
        raise HTTPException(status_code=404, detail="Knowledge item not found or no changes")
    return {"success": True}


@router.delete("/knowledge/{item_id}")
def delete_knowledge(item_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete a knowledge item."""
    from backend.services.knowledge_service import knowledge_service
    ok = knowledge_service.delete_item(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return {"success": True}


@router.patch("/knowledge/{item_id}/toggle")
def toggle_knowledge(item_id: int, admin: UserInfo = Depends(require_admin)):
    """Toggle knowledge item active status."""
    from backend.services.knowledge_service import knowledge_service
    ok = knowledge_service.toggle_active(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return {"success": True}


@router.get("/knowledge/{item_id}/followups")
def get_knowledge_followups(item_id: int, admin: UserInfo = Depends(require_admin)):
    """Get followup cases for a knowledge item."""
    from backend.services.knowledge_service import knowledge_service
    return knowledge_service.get_followups(item_id)


# ═══════════════════════════════════════════════════════════════════
# 质量评审
# ═══════════════════════════════════════════════════════════════════

@router.get("/quality-reviews")
def list_quality_reviews(
    workspace_id: int = Query(0),
    execution_status: str = Query(None),
    pipeline_mode: str = Query(None),
    has_feedback: bool = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: UserInfo = Depends(require_admin),
):
    """List quality reviews."""
    from backend.services.quality_service import quality_service
    return quality_service.list_reviews(workspace_id, execution_status=execution_status,
                                         pipeline_mode=pipeline_mode, has_feedback=has_feedback,
                                         page=page, size=size)


@router.get("/quality-reviews/{review_id}")
def get_quality_review(review_id: int, admin: UserInfo = Depends(require_admin)):
    """Get a single quality review."""
    from backend.services.quality_service import quality_service
    review = quality_service.get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


@router.post("/quality-reviews/{review_id}/llm-review")
async def trigger_llm_review(review_id: int, admin: UserInfo = Depends(require_admin)):
    """Manually trigger LLM review for a specific review."""
    from backend.services.quality_service import quality_service
    result = await quality_service.manual_llm_review(review_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"success": True, "analysis": result.get("analysis", "")}


@router.patch("/quality-reviews/{review_id}/feedback")
def update_review_feedback(review_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update user feedback for a review."""
    from backend.services.quality_service import quality_service
    satisfied = req.get("satisfied")
    if satisfied is None:
        raise HTTPException(status_code=400, detail="satisfied is required")
    ok = quality_service.update_feedback(review_id, satisfied)
    return {"success": ok}


@router.get("/quality-stats")
def get_quality_stats(
    workspace_id: int = Query(0),
    date_from: str = Query(None),
    date_to: str = Query(None),
    admin: UserInfo = Depends(require_admin),
):
    """Get quality statistics."""
    from backend.services.quality_service import quality_service
    return quality_service.get_stats(workspace_id, date_from, date_to)


# ═══════════════════════════════════════════════════════════════════
# 数据建模
# ═══════════════════════════════════════════════════════════════════

@router.get("/models")
def list_models(
    workspace_id: int = Query(0),
    datasource_id: int = Query(None),
    admin: UserInfo = Depends(require_admin),
):
    """List data models."""
    from backend.services.modeling_service import modeling_service
    return modeling_service.list_models(workspace_id, datasource_id)


@router.get("/models/{model_id}")
def get_model(model_id: int, admin: UserInfo = Depends(require_admin)):
    """Get model with tables, relations, calculated fields."""
    from backend.services.modeling_service import modeling_service
    model = modeling_service.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return model


@router.post("/models")
def create_model(req: dict, admin: UserInfo = Depends(require_admin)):
    """Create a data model."""
    from backend.services.modeling_service import modeling_service
    if not req.get("model_name") or not req.get("datasource_id"):
        raise HTTPException(status_code=400, detail="model_name and datasource_id are required")
    req["created_by"] = admin.id
    model_id = modeling_service.create_model(req)
    return {"success": True, "id": model_id}


@router.put("/models/{model_id}")
def update_model(model_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update model metadata."""
    from backend.services.modeling_service import modeling_service
    ok = modeling_service.update_model(model_id, req)
    if not ok:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"success": True}


@router.delete("/models/{model_id}")
def delete_model(model_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete a model and all related data."""
    from backend.services.modeling_service import modeling_service
    ok = modeling_service.delete_model(model_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"success": True}


@router.post("/models/{model_id}/tables")
def add_model_table(model_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Add a table to the model."""
    from backend.services.modeling_service import modeling_service
    if not req.get("table_name"):
        raise HTTPException(status_code=400, detail="table_name is required")
    table_id = modeling_service.add_table(model_id, req)
    return {"success": True, "id": table_id}


@router.put("/models/tables/{table_id}")
def update_model_table(table_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update table config (position, display name)."""
    from backend.services.modeling_service import modeling_service
    ok = modeling_service.update_table(table_id, req)
    return {"success": ok}


@router.post("/models/{model_id}/relations")
def add_model_relation(model_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Add a relation to the model."""
    from backend.services.modeling_service import modeling_service
    if not all(k in req for k in ["source_table", "source_column", "target_table", "target_column"]):
        raise HTTPException(status_code=400, detail="source/target table and column are required")
    rel_id = modeling_service.add_relation(model_id, req)
    return {"success": True, "id": rel_id}


@router.put("/models/relations/{rel_id}")
def update_model_relation(rel_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update a relation."""
    from backend.services.modeling_service import modeling_service
    ok = modeling_service.update_relation(rel_id, req)
    return {"success": ok}


@router.delete("/models/relations/{rel_id}")
def delete_model_relation(rel_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete a relation."""
    from backend.services.modeling_service import modeling_service
    ok = modeling_service.delete_relation(rel_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Relation not found")
    return {"success": True}


@router.post("/models/{model_id}/calculate-fields")
def add_calculated_field(model_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Add a calculated field."""
    from backend.services.modeling_service import modeling_service
    if not all(k in req for k in ["table_name", "field_name", "expression"]):
        raise HTTPException(status_code=400, detail="table_name, field_name, expression are required")
    field_id = modeling_service.add_calculated_field(model_id, req)
    return {"success": True, "id": field_id}


@router.put("/models/calculate-fields/{field_id}")
def update_calculated_field(field_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update a calculated field."""
    from backend.services.modeling_service import modeling_service
    ok = modeling_service.update_calculated_field(field_id, req)
    return {"success": ok}


@router.delete("/models/calculate-fields/{field_id}")
def delete_calculated_field(field_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete a calculated field."""
    from backend.services.modeling_service import modeling_service
    ok = modeling_service.delete_calculated_field(field_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Field not found")
    return {"success": True}


@router.post("/models/{model_id}/auto-detect")
def auto_detect_relations(model_id: int, admin: UserInfo = Depends(require_admin)):
    """Auto-detect relations from metadata."""
    from backend.services.modeling_service import modeling_service
    model = modeling_service.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    relations = modeling_service.auto_detect_relations(model["datasource_id"])
    return {"relations": relations}


@router.post("/models/{model_id}/generate-mschema")
def generate_mschema(model_id: int, admin: UserInfo = Depends(require_admin)):
    """Generate M-Schema string for NL2SQL."""
    from backend.services.modeling_service import modeling_service
    mschema = modeling_service.generate_mschema(model_id)
    return {"mschema": mschema}


@router.post("/models/{model_id}/sync-metadata")
def sync_model_from_metadata(model_id: int, admin: UserInfo = Depends(require_admin)):
    """Sync model from metadata (auto-populate tables + relations)."""
    from backend.services.modeling_service import modeling_service
    model = modeling_service.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    new_model_id = modeling_service.sync_from_metadata(
        model.get("workspace_id", 0), model["datasource_id"], admin.id
    )
    return {"success": True, "id": new_model_id}


# ═══════════════════════════════════════════════════════════════════
# 计算字段（独立于模型，直接管理）
# ═══════════════════════════════════════════════════════════════════

@router.get("/calculate-fields")
def list_calculate_fields(
    table_name: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(9999, ge=1, le=9999),
    admin: UserInfo = Depends(require_admin),
):
    """List all calculated fields (independent of model)."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            where = ["is_active = 1"]
            params = []
            if table_name:
                where.append("table_name = %s")
                params.append(table_name)
            where_clause = " AND ".join(where)
            cur.execute(
                f"SELECT * FROM adh_calculated_fields WHERE {where_clause} ORDER BY table_name, field_name LIMIT %s OFFSET %s",
                params + [size, (page - 1) * size]
            )
            items = cur.fetchall()
            return {"items": items}
    finally:
        conn.close()


@router.post("/calculate-fields")
def create_calculate_field(req: dict, admin: UserInfo = Depends(require_admin)):
    """Create a calculated field (standalone, no model required)."""
    import time as _time
    if not req.get("table_name") or not req.get("field_name") or not req.get("expression"):
        raise HTTPException(status_code=400, detail="table_name, field_name, expression are required")
    field_id = int(_time.time() * 1000000)
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO adh_calculated_fields
                   (id, model_id, table_name, field_name, display_name, expression, data_type, description, is_active)
                   VALUES (%s, 0, %s, %s, %s, %s, %s, %s, 1)""",
                (field_id, req["table_name"], req["field_name"],
                 req.get("display_name", req["field_name"]),
                 req["expression"], req.get("data_type", "number"),
                 req.get("description", ""))
            )
        conn.commit()
        return {"success": True, "id": field_id}
    finally:
        conn.close()


@router.put("/calculate-fields/{field_id}")
def update_calculate_field(field_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update a calculated field."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            fields = []
            params = []
            for key in ["table_name", "field_name", "display_name", "expression", "data_type", "description", "is_active"]:
                if key in req:
                    fields.append(f"{key} = %s")
                    params.append(req[key])
            if not fields:
                return {"success": False, "message": "No fields to update"}
            params.append(field_id)
            cur.execute(f"UPDATE adh_calculated_fields SET {', '.join(fields)} WHERE id = %s", params)
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.delete("/calculate-fields/{field_id}")
def delete_calculate_field(field_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete a calculated field."""
    conn = _get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_calculated_fields WHERE id = %s", (field_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# 角色权限管理
# ═══════════════════════════════════════════════════════════════════

@router.get("/roles")
def list_roles(
    workspace_id: int = Query(None),
    admin: UserInfo = Depends(require_admin),
):
    """List all roles."""
    from backend.services.role_service import role_service
    return role_service.list_roles(workspace_id)


@router.get("/roles/{role_id}")
def get_role(role_id: int, admin: UserInfo = Depends(require_admin)):
    """Get role with attributes and users."""
    from backend.services.role_service import role_service
    role = role_service.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@router.post("/roles")
def create_role(req: dict, admin: UserInfo = Depends(require_admin)):
    """Create a new role."""
    from backend.services.role_service import role_service
    if not req.get("name") or not req.get("display_name"):
        raise HTTPException(status_code=400, detail="name and display_name are required")
    role_id = role_service.create_role(req["name"], req["display_name"], req.get("description", ""))
    return {"success": True, "id": role_id}


@router.put("/roles/{role_id}")
def update_role(role_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Update role metadata."""
    from backend.services.role_service import role_service
    ok = role_service.update_role(role_id, req)
    return {"success": ok}


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, admin: UserInfo = Depends(require_admin)):
    """Delete a role (system roles cannot be deleted)."""
    from backend.services.role_service import role_service
    ok = role_service.delete_role(role_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot delete system role")
    return {"success": True}


@router.get("/roles/{role_id}/attributes")
def get_role_attributes(
    role_id: int,
    workspace_id: int = Query(0),
    admin: UserInfo = Depends(require_admin),
):
    """Get role attributes (data scope)."""
    from backend.services.role_service import role_service
    return role_service.get_role_attributes(role_id, workspace_id)


@router.put("/roles/{role_id}/attributes")
def set_role_attributes(role_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Set role attributes for a workspace."""
    from backend.services.role_service import role_service
    workspace_id = req.get("workspace_id", 0)
    attrs = req.get("attributes", {})
    ok = role_service.set_role_attributes(role_id, workspace_id, attrs)
    return {"success": ok}


@router.post("/roles/{role_id}/users")
def assign_user_to_role(role_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Assign a user to a role."""
    from backend.services.role_service import role_service
    user_id = req.get("user_id")
    workspace_id = req.get("workspace_id", 0)
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    ok = role_service.assign_user_role(user_id, role_id, workspace_id)
    return {"success": ok}


@router.delete("/roles/{role_id}/users/{user_id}")
def remove_user_from_role(
    role_id: int, user_id: int,
    workspace_id: int = Query(0),
    admin: UserInfo = Depends(require_admin),
):
    """Remove a user from a role."""
    from backend.services.role_service import role_service
    ok = role_service.remove_user_role(user_id, role_id, workspace_id)
    return {"success": ok}


@router.get("/users/{user_id}/roles")
def get_user_roles(
    user_id: int,
    workspace_id: int = Query(None),
    admin: UserInfo = Depends(require_admin),
):
    """Get roles assigned to a user."""
    from backend.services.role_service import role_service
    return role_service.get_user_roles(user_id, workspace_id)


@router.post("/workspaces/{workspace_id}/roles")
def authorize_workspace_role(workspace_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Authorize a role to access a workspace."""
    from backend.services.role_service import role_service
    role_id = req.get("role_id")
    if not role_id:
        raise HTTPException(status_code=400, detail="role_id is required")
    ok = role_service.authorize_workspace_role(workspace_id, role_id)
    return {"success": ok}


@router.delete("/workspaces/{workspace_id}/roles/{role_id}")
def revoke_workspace_role(workspace_id: int, role_id: int, admin: UserInfo = Depends(require_admin)):
    """Revoke a role's access to a workspace."""
    from backend.services.role_service import role_service
    ok = role_service.revoke_workspace_role(workspace_id, role_id)
    return {"success": ok}


# ── Role Datasource Access ─────────────────────────────────────────

@router.get("/roles/{role_id}/datasources")
def get_role_datasources(role_id: int, admin: UserInfo = Depends(require_admin)):
    """Get datasources a role can access."""
    from backend.services.role_service import role_service
    return role_service.get_role_datasources(role_id)


@router.put("/roles/{role_id}/datasources")
def set_role_datasources(role_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Set datasource access for a role (replace all)."""
    from backend.services.role_service import role_service
    ok = role_service.set_role_datasources(role_id, req.get("datasource_ids", []))
    return {"success": ok}


# ── Role Table Access ──────────────────────────────────────────────

@router.get("/roles/{role_id}/tables")
def get_role_tables(role_id: int, admin: UserInfo = Depends(require_admin)):
    """Get tables a role can access."""
    from backend.services.role_service import role_service
    return role_service.get_role_tables(role_id)


@router.put("/roles/{role_id}/tables")
def set_role_tables(role_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Set table access for a role (replace all)."""
    from backend.services.role_service import role_service
    ok = role_service.set_role_tables(role_id, req.get("tables", []))
    return {"success": ok}


# ── Role Column Access ─────────────────────────────────────────────

@router.get("/roles/{role_id}/columns")
def get_role_columns(role_id: int, admin: UserInfo = Depends(require_admin)):
    """Get column permissions for a role."""
    from backend.services.role_service import role_service
    return role_service.get_role_columns(role_id)


@router.put("/roles/{role_id}/columns")
def set_role_columns(role_id: int, req: dict, admin: UserInfo = Depends(require_admin)):
    """Set column access for a role (replace all)."""
    from backend.services.role_service import role_service
    ok = role_service.set_role_columns(role_id, req.get("columns", []))
    return {"success": ok}
