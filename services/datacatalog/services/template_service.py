"""SQL模板服务 — 管理 adh_sql_templates 表的 CRUD 操作。"""

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


class TemplateService:
    """SQL模板服务 — 管理 SQL 模板的增删改查。"""

    @staticmethod
    def list_templates(page: int = 1, size: int = 50, search: str = "",
                       datasource_id: Optional[int] = None) -> dict:
        """分页查询 SQL 模板。"""
        conn = get_metadata_conn()
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

    @staticmethod
    def get_template(row_id: int) -> Optional[dict]:
        """获取单条 SQL 模板。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, template_id, template_name, category, intent_keywords, "
                    "sql_template, variables, description, rules, usage_count, is_active, created_at, updated_at "
                    "FROM adh_sql_templates WHERE id = %s", (row_id,),
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
    def create_template(data: dict) -> dict:
        """创建 SQL 模板。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 检查 template_id 重复
                cur.execute("SELECT id FROM adh_sql_templates WHERE template_id = %s", (data["template_id"],))
                if cur.fetchone():
                    return {"success": False, "message": f"模板ID '{data['template_id']}' 已存在"}

                now = _now()
                row_id = int(_time.time() * 1000000)
                embed_text = f"{data['template_name']} {data.get('intent_keywords', '')} {data.get('description', '')}"
                vec_literal = _make_embedding(embed_text)

                cur.execute(
                    "INSERT INTO adh_sql_templates "
                    "(id, template_id, template_name, category, intent_keywords, "
                    "sql_template, variables, description, rules, usage_count, is_active, "
                    "datasource_id, created_at, updated_at, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s)",
                    (row_id, data["template_id"], data["template_name"], data.get("category", ""),
                     data.get("intent_keywords", ""), data["sql_template"], data.get("variables") or "",
                     data.get("description") or "", data.get("rules") or "", 1 if data.get("is_active", True) else 0,
                     data.get("datasource_id") or 0, now, now, vec_literal),
                )
            conn.commit()
            return {"success": True, "id": row_id}
        finally:
            conn.close()

    @staticmethod
    def update_template(row_id: int, data: dict) -> dict:
        """更新 SQL 模板。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT template_id, template_name, category, intent_keywords, sql_template, "
                    "variables, description, rules, usage_count, is_active, datasource_id FROM adh_sql_templates WHERE id = %s",
                    (row_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "message": "模板不存在"}

                # 合并更新
                fields = {}
                for f in ("template_name", "category", "intent_keywords", "sql_template",
                           "variables", "description", "rules"):
                    val = data.get(f)
                    if val is not None:
                        fields[f] = val
                    else:
                        fields[f] = row.get(f) or ""
                is_active = data.get("is_active") if data.get("is_active") is not None else bool(row["is_active"])

                embed_text = f"{fields['template_name']} {fields['intent_keywords']} {fields['description']}"
                vec_literal = _make_embedding(embed_text)
                now = _now()

                datasource_id = data.get("datasource_id") if data.get("datasource_id") is not None else row.get("datasource_id", 0)

                # Doris DUPLICATE KEY 表不支持 UPDATE，使用 DELETE + INSERT
                cur.execute("DELETE FROM adh_sql_templates WHERE id = %s", (row_id,))
                conn.commit()  # DELETE 必须先提交，Doris 同一主键才能 INSERT
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

    @staticmethod
    def delete_template(row_id: int) -> dict:
        """删除 SQL 模板。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_sql_templates WHERE id = %s", (row_id,))
            conn.commit()
            return {"success": True}
        finally:
            conn.close()
