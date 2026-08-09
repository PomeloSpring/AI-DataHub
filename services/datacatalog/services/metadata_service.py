"""元数据服务 — 表信息、字段元数据的 CRUD 及同步操作。"""

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


def _table_embed_text(table_name: str, table_comment: str = "", keywords: str = "",
                      region_tag: str = "", domain_tag: str = "") -> str:
    """生成表级别的嵌入文本。

    使用 keywords（短聚焦词）提升向量检索质量。
    业务描述不包含在内——由 LLM 单独处理。
    """
    parts = [table_name, table_comment or "", keywords or ""]
    return " ".join(p for p in parts if p).strip()


def _col_embed_text(table_name: str, column_name: str, data_type: str = "",
                    column_comment: str = "", keywords: str = "") -> str:
    """生成字段级别的嵌入文本。

    使用 keywords 提升向量检索质量。业务描述由 LLM 单独处理。
    """
    parts = [table_name, column_name, data_type or "", column_comment or "", keywords or ""]
    return " ".join(p for p in parts if p).strip()


class MetadataService:
    """元数据服务 — 管理表信息（adh_table_info）和字段元数据（adh_column_metadata）。"""

    # ── 同步操作 ──────────────────────────────────────────────────────

    @staticmethod
    def sync_metadata(datasource_id: int) -> dict:
        """同步指定数据源的元数据。"""
        if not datasource_id:
            return {"success": False, "message": "请选择要同步的数据源"}
        try:
            from sync.metadata_sync import sync_metadata as _sync
            _sync(datasource_id)
            return {"success": True, "message": "元数据同步完成"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    @staticmethod
    def sync_table_columns(datasource_id: int, table_name: str) -> dict:
        """同步指定表的字段元数据。"""
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

    # ── 字段元数据 CRUD ──────────────────────────────────────────────

    @staticmethod
    def list_metadata(page: int = 1, size: int = 50, table_name: str = "",
                      column_name: str = "", datasource_id: Optional[int] = None) -> dict:
        """分页查询字段元数据。"""
        conn = get_metadata_conn()
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

    @staticmethod
    def get_metadata(row_id: int) -> Optional[dict]:
        """获取单条字段元数据。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, table_name, column_name, data_type, "
                    "column_comment, business_desc, keywords, is_key, is_nullable, is_active, sync_time, datasource_id "
                    "FROM adh_column_metadata WHERE id = %s", (row_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if hasattr(row.get("sync_time"), "isoformat"):
                    row["sync_time"] = row["sync_time"].isoformat()
                return row
        finally:
            conn.close()

    @staticmethod
    def create_metadata(data: dict) -> dict:
        """创建字段元数据。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 检查同表内字段重复
                cur.execute(
                    "SELECT id FROM adh_column_metadata WHERE table_name = %s AND column_name = %s",
                    (data["table_name"], data["column_name"]),
                )
                if cur.fetchone():
                    return {"success": False, "message": f"字段 '{data['column_name']}' 在表 '{data['table_name']}' 中已存在"}

                now = _now()
                row_id = int(_time.time() * 1000000)
                embed_text = _col_embed_text(
                    data["table_name"], data["column_name"],
                    data.get("data_type") or "", data.get("column_comment", ""),
                    data.get("keywords") or "",
                )
                vec_literal = _make_embedding(embed_text)

                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding, datasource_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (row_id, data["table_name"], data["column_name"], data.get("data_type", "VARCHAR"),
                     data.get("column_comment") or "", data.get("business_desc") or "", data.get("keywords") or "",
                     data.get("is_key") or "false", data.get("is_nullable") or "true",
                     1 if data.get("is_active", True) else 0, now, vec_literal, data.get("datasource_id") or 0),
                )
            conn.commit()
            return {"success": True, "id": row_id}
        finally:
            conn.close()

    @staticmethod
    def update_metadata(row_id: int, data: dict) -> dict:
        """更新字段元数据。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, table_name, column_name, data_type, "
                    "column_comment, business_desc, keywords, is_key, is_nullable, is_active, datasource_id "
                    "FROM adh_column_metadata WHERE id = %s", (row_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "message": "记录不存在"}

                # 合并更新
                column_comment = data.get("column_comment") if data.get("column_comment") is not None else (row["column_comment"] or "")
                business_desc = data.get("business_desc") if data.get("business_desc") is not None else (row["business_desc"] or "")
                keywords = data.get("keywords") if data.get("keywords") is not None else (row.get("keywords") or "")
                is_active = data.get("is_active") if data.get("is_active") is not None else bool(row["is_active"])

                # 重新生成嵌入向量
                embed_text = _col_embed_text(row['table_name'], row['column_name'], row["data_type"], column_comment, keywords)
                vec_literal = _make_embedding(embed_text)
                now = _now()

                # Doris DUPLICATE KEY 表不支持 UPDATE，使用 DELETE + INSERT
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

    @staticmethod
    def delete_metadata(row_id: int) -> dict:
        """删除字段元数据。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_column_metadata WHERE id = %s", (row_id,))
            conn.commit()
            return {"success": True}
        finally:
            conn.close()

    # ── 表信息 CRUD ──────────────────────────────────────────────────

    @staticmethod
    def list_table_info(page: int = 1, size: int = 50, table_name: str = "",
                        datasource_id: Optional[int] = None) -> dict:
        """分页查询表信息。"""
        conn = get_metadata_conn()
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

    @staticmethod
    def get_table_info(row_id: int) -> Optional[dict]:
        """获取单条表信息。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, table_name, table_comment, table_business_desc, keywords, "
                    "region_tag, domain_tag, is_active, datasource_id, sync_time "
                    "FROM adh_table_info WHERE id = %s", (row_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if hasattr(row.get("sync_time"), "isoformat"):
                    row["sync_time"] = row["sync_time"].isoformat()
                return row
        finally:
            conn.close()

    @staticmethod
    def create_table_info(data: dict) -> dict:
        """创建表信息。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 检查同数据源内表名重复
                cur.execute(
                    "SELECT id FROM adh_table_info WHERE table_name = %s AND datasource_id = %s",
                    (data["table_name"], data.get("datasource_id") or 0),
                )
                if cur.fetchone():
                    return {"success": False, "message": f"表 '{data['table_name']}' 在该数据源中已存在"}

                now = _now()
                row_id = int(_time.time() * 1000000)
                embed_text = _table_embed_text(
                    data["table_name"], data.get("table_comment", ""),
                    data.get("keywords") or "", "", data.get("domain_tag", ""),
                )
                vec_literal = _make_embedding(embed_text)

                cur.execute(
                    "INSERT INTO adh_table_info "
                    "(id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, "
                    "is_active, sync_time, embedding, datasource_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (row_id, data["table_name"], data.get("table_comment") or "", data.get("table_business_desc") or "",
                     data.get("keywords") or "", data.get("region_tag") or "", data.get("domain_tag") or "",
                     1 if data.get("is_active", True) else 0, now, vec_literal, data.get("datasource_id") or 0),
                )
            conn.commit()
            return {"success": True, "id": row_id}
        finally:
            conn.close()

    @staticmethod
    def update_table_info(row_id: int, data: dict) -> dict:
        """更新表信息。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, table_name, table_comment, table_business_desc, region_tag, domain_tag, is_active, datasource_id "
                    "FROM adh_table_info WHERE id = %s", (row_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "message": "表信息不存在"}

                # 合并更新
                table_comment = data.get("table_comment") if data.get("table_comment") is not None else (row["table_comment"] or "")
                table_business_desc = data.get("table_business_desc") if data.get("table_business_desc") is not None else (row["table_business_desc"] or "")
                keywords = data.get("keywords") if data.get("keywords") is not None else (row.get("keywords") or "")
                domain_tag = data.get("domain_tag") if data.get("domain_tag") is not None else (row["domain_tag"] or "")
                region_tag = data.get("region_tag") if data.get("region_tag") is not None else (row["region_tag"] or "")
                is_active = data.get("is_active") if data.get("is_active") is not None else bool(row["is_active"])

                # 重新生成嵌入向量
                embed_text = _table_embed_text(row['table_name'], table_comment, keywords, region_tag, domain_tag)
                vec_literal = _make_embedding(embed_text)
                now = _now()

                # Doris DUPLICATE KEY 表不支持 UPDATE，使用 DELETE + INSERT
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

    @staticmethod
    def delete_table_info(row_id: int) -> dict:
        """删除表信息。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_table_info WHERE id = %s", (row_id,))
            conn.commit()
            return {"success": True}
        finally:
            conn.close()

    # ── 批量清理 ─────────────────────────────────────────────────────

    @staticmethod
    def clear_metadata_by_datasource(datasource_id: int) -> dict:
        """清理指定数据源的所有元数据（表信息 + 字段元数据 + 关联关系）。"""
        if not datasource_id:
            return {"success": False, "message": "请选择要清理的数据源"}

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 统计删除数量
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_table_info WHERE datasource_id = %s", (datasource_id,))
                table_count = cur.fetchone()["cnt"]
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_column_metadata WHERE datasource_id = %s", (datasource_id,))
                col_count = cur.fetchone()["cnt"]
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_table_relations WHERE datasource_id = %s", (datasource_id,))
                rel_count = cur.fetchone()["cnt"]

                total = table_count + col_count + rel_count
                if total == 0:
                    return {"success": True, "message": "该数据源下没有元数据"}

                # 删除
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

    @staticmethod
    def clear_metadata_by_table(datasource_id: int, table_name: str) -> dict:
        """清理指定表的元数据（表信息 + 字段元数据 + 相关关联关系）。"""
        if not datasource_id:
            return {"success": False, "message": "请选择要清理的数据源"}
        if not table_name:
            return {"success": False, "message": "请输入要清理的表名"}

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 统计删除数量
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

                # 删除
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
