"""表关联关系服务 — 管理 adh_table_relations 表的 CRUD 操作，支持图谱同步。"""

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


class RelationService:
    """表关联关系服务 — 管理表间关联关系的增删改查及图谱同步。"""

    @staticmethod
    def list_relations(page: int = 1, size: int = 50, table_name: str = "",
                       datasource_id: Optional[int] = None) -> dict:
        """分页查询表关联关系。"""
        conn = get_metadata_conn()
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

    @staticmethod
    def get_relation(row_id: int) -> Optional[dict]:
        """获取单条表关联关系。"""
        conn = get_metadata_conn()
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
                    return None
                for k in ("created_at", "updated_at"):
                    if hasattr(row.get(k), "isoformat"):
                        row[k] = row[k].isoformat()
                return row
        finally:
            conn.close()

    @staticmethod
    def create_relation(data: dict) -> dict:
        """创建表关联关系。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 检查重复关联关系
                cur.execute(
                    "SELECT id FROM adh_table_relations "
                    "WHERE source_table = %s AND source_column = %s "
                    "AND target_table = %s AND target_column = %s AND datasource_id = %s",
                    (data["source_table"], data["source_column"],
                     data["target_table"], data["target_column"], data.get("datasource_id") or 0),
                )
                if cur.fetchone():
                    return {
                        "success": False,
                        "message": f"关联关系 {data['source_table']}.{data['source_column']} "
                                   f"→ {data['target_table']}.{data['target_column']} 已存在",
                    }

                now = _now()
                row_id = int(_time.time() * 1000000)
                embed_text = (
                    f"{data['source_table']}.{data['source_column']} → "
                    f"{data['target_table']}.{data['target_column']} "
                    f"{data.get('relation_type', '1:N')} {data.get('description') or ''}"
                )
                vec_literal = _make_embedding(embed_text)

                cur.execute(
                    "INSERT INTO adh_table_relations "
                    "(id, datasource_id, source_table, source_column, target_table, target_column, "
                    "relation_type, join_type, description, is_active, created_at, updated_at, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (row_id, data.get("datasource_id") or 0, data["source_table"], data["source_column"],
                     data["target_table"], data["target_column"],
                     data.get("relation_type", "1:N"), data.get("join_type", "INNER"),
                     data.get("description") or "", 1 if data.get("is_active", True) else 0,
                     now, now, vec_literal),
                )
            conn.commit()

            # 同步到图谱
            _emit_graph_sync("relation_created", {
                "source_table": data["source_table"],
                "source_column": data["source_column"],
                "target_table": data["target_table"],
                "target_column": data["target_column"],
                "relation_type": data.get("relation_type", "1:N"),
                "join_type": data.get("join_type", "INNER"),
                "description": data.get("description"),
            })

            return {"success": True, "id": row_id}
        finally:
            conn.close()

    @staticmethod
    def update_relation(row_id: int, data: dict) -> dict:
        """更新表关联关系。"""
        conn = get_metadata_conn()
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
                    return {"success": False, "message": "关联关系不存在"}

                # 合并更新
                source_column = data.get("source_column") if data.get("source_column") is not None else row["source_column"]
                target_column = data.get("target_column") if data.get("target_column") is not None else row["target_column"]
                relation_type = data.get("relation_type") if data.get("relation_type") is not None else row["relation_type"]
                join_type = data.get("join_type") if data.get("join_type") is not None else row["join_type"]
                description = data.get("description") if data.get("description") is not None else (row["description"] or "")
                is_active = data.get("is_active") if data.get("is_active") is not None else bool(row["is_active"])

                embed_text = (
                    f"{row['source_table']}.{source_column} → {row['target_table']}.{target_column} "
                    f"{relation_type} {description}"
                )
                vec_literal = _make_embedding(embed_text)
                now = _now()

                # Doris DUPLICATE KEY 表不支持 UPDATE，使用 DELETE + INSERT
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

    @staticmethod
    def delete_relation(row_id: int) -> dict:
        """删除表关联关系。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 先获取关系信息用于图谱同步
                cur.execute(
                    "SELECT source_table, source_column, target_table, target_column "
                    "FROM adh_table_relations WHERE id = %s", (row_id,)
                )
                relation = cur.fetchone()

                cur.execute("DELETE FROM adh_table_relations WHERE id = %s", (row_id,))
            conn.commit()

            # 同步到图谱
            if relation:
                _emit_graph_sync("relation_deleted", {"id": row_id, **relation})

            return {"success": True}
        finally:
            conn.close()

    @staticmethod
    def sync_relations(datasource_id: int) -> dict:
        """自动检测并同步 MySQL 外键作为表关联关系。"""
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
