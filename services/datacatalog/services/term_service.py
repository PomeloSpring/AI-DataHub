"""业务术语服务 — 管理 adh_business_terms 表的 CRUD 操作，支持图谱同步。"""

import logging
import time as _time
from datetime import datetime
from typing import Optional

from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.common.llm.embedding import generate_embedding, embedding_to_sql_literal

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _make_embedding(text: str) -> str:
    vec = generate_embedding(text)
    return embedding_to_sql_literal(vec)


def _emit_graph_sync(event_type: str, data: dict):
    """向图谱同步服务发送事件（非关键路径，失败不影响主流程）。"""
    try:
        from services.shared.graphservice.graph_sync_service import get_graph_sync_service, SyncEventType
        import asyncio
        sync_service = get_graph_sync_service()
        asyncio.create_task(sync_service.emit_event(
            SyncEventType(event_type), data,
        ))
    except Exception as e:
        logger.warning("图谱同步失败（非关键）: %s", e)


class TermService:
    """业务术语服务 — 管理业务术语的增删改查及图谱同步。"""

    @staticmethod
    def list_terms(page: int = 1, size: int = 50, search: str = "",
                   datasource_id: Optional[int] = None) -> dict:
        """分页查询业务术语。"""
        conn = get_metadata_conn()
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

    @staticmethod
    def get_term(row_id: int) -> Optional[dict]:
        """获取单条业务术语。"""
        conn = get_metadata_conn()
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
                    return None
                for k in ("created_at", "updated_at"):
                    if hasattr(row.get(k), "isoformat"):
                        row[k] = row[k].isoformat()
                return row
        finally:
            conn.close()

    @staticmethod
    def create_term(data: dict) -> dict:
        """创建业务术语。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 检查 term_cn 重复
                cur.execute("SELECT id FROM adh_business_terms WHERE term_cn = %s", (data["term_cn"],))
                if cur.fetchone():
                    return {"success": False, "message": f"术语 '{data['term_cn']}' 已存在"}

                now = _now()
                row_id = int(_time.time() * 1000000)
                embed_text = f"{data['term_cn']} {data.get('term_en') or ''} {data.get('term_aliases') or ''} {data.get('description') or ''}"
                vec_literal = _make_embedding(embed_text)

                cur.execute(
                    "INSERT INTO adh_business_terms "
                    "(id, term_cn, term_en, term_aliases, term_type, "
                    "target_table, target_column, calculation, description, "
                    "usage_count, is_active, created_at, updated_at, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 1, %s, %s, %s)",
                    (row_id, data["term_cn"], data.get("term_en") or "", data.get("term_aliases") or "",
                     data.get("term_type", "dimension"), data.get("target_table") or "", data.get("target_column") or "",
                     data.get("calculation") or "", data.get("description") or "", now, now, vec_literal),
                )
            conn.commit()

            # 同步到图谱
            _emit_graph_sync("term_created", {
                "term_cn": data["term_cn"],
                "term_en": data.get("term_en"),
                "term_aliases": data.get("term_aliases"),
                "target_table": data.get("target_table"),
                "target_column": data.get("target_column"),
                "calculation": data.get("calculation"),
                "description": data.get("description"),
            })

            return {"success": True, "id": row_id}
        finally:
            conn.close()

    @staticmethod
    def update_term(row_id: int, data: dict) -> dict:
        """更新业务术语。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT term_cn, term_en, term_aliases, term_type, "
                    "target_table, target_column, calculation, description, usage_count, is_active "
                    "FROM adh_business_terms WHERE id = %s", (row_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "message": "术语不存在"}

                # 合并更新
                fields = {}
                for f in ("term_cn", "term_en", "term_aliases", "term_type",
                           "target_table", "target_column", "calculation", "description"):
                    val = data.get(f)
                    if val is not None:
                        fields[f] = val
                    else:
                        fields[f] = row[f] or ""

                embed_text = f"{fields['term_cn']} {fields['term_en']} {fields['term_aliases']} {fields['description']}"
                vec_literal = _make_embedding(embed_text)
                now = _now()

                # Doris DUPLICATE KEY 表不支持 UPDATE，使用 DELETE + INSERT
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

    @staticmethod
    def delete_term(row_id: int) -> dict:
        """删除业务术语。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 先获取术语信息用于图谱同步
                cur.execute("SELECT term_cn FROM adh_business_terms WHERE id = %s", (row_id,))
                term = cur.fetchone()

                cur.execute("DELETE FROM adh_business_terms WHERE id = %s", (row_id,))
            conn.commit()

            # 同步到图谱
            if term:
                _emit_graph_sync("term_deleted", {"term_cn": term["term_cn"]})

            return {"success": True}
        finally:
            conn.close()

    @staticmethod
    def toggle_term(row_id: int) -> dict:
        """切换业务术语的启用/禁用状态。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 获取当前 is_active 值
                cur.execute("SELECT is_active FROM adh_business_terms WHERE id = %s", (row_id,))
                row = cur.fetchone()
                if not row:
                    return {"success": False, "message": "术语不存在"}

                # 切换：NULL 或 1 -> 0，否则 -> 1
                current = row.get("is_active")
                new_val = 0 if (current is None or current == 1) else 1

                # 获取完整行数据
                cur.execute(
                    "SELECT id, term_cn, term_en, term_aliases, term_type, "
                    "target_table, target_column, calculation, description, usage_count, "
                    "created_at, updated_at, datasource_id "
                    "FROM adh_business_terms WHERE id = %s", (row_id,),
                )
                full_row = cur.fetchone()

                # 获取嵌入向量
                cur.execute("SELECT embedding FROM adh_business_terms WHERE id = %s", (row_id,))

                cur.execute("DELETE FROM adh_business_terms WHERE id = %s", (row_id,))

                now = _now()
                # 重建嵌入向量
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
