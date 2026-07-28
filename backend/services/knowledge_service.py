"""Knowledge Management Service — CRUD and retrieval for knowledge items.

Supports 4 knowledge types:
- instruction: directives for LLM behavior
- sql_pair: question-SQL pairs for few-shot learning
- recommend_question: suggested questions for users
- followup_case: follow-up question chains
"""

import json
import logging
import time
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn
from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal

logger = logging.getLogger(__name__)


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _gen_id():
    return int(time.time() * 1000000)


class KnowledgeService:
    """Knowledge management service."""

    VALID_TYPES = ("instruction", "sql_pair", "recommend_question", "followup_case")

    # ── CRUD ───────────────────────────────────────────────────────

    def list_items(self, workspace_id: int, knowledge_type: str = None,
                   datasource_id: int = None, search: str = None,
                   page: int = 1, size: int = 20) -> dict:
        """List knowledge items with optional filters."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where = ["workspace_id = %s"]
                params = [workspace_id]
                if knowledge_type:
                    where.append("knowledge_type = %s")
                    params.append(knowledge_type)
                if datasource_id:
                    where.append("datasource_id = %s")
                    params.append(datasource_id)
                if search:
                    where.append("(title LIKE %s OR content LIKE %s)")
                    params.extend([f"%{search}%", f"%{search}%"])

                where_clause = " AND ".join(where)
                cur.execute(f"SELECT COUNT(*) as total FROM adh_knowledge_items WHERE {where_clause}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_knowledge_items WHERE {where_clause} ORDER BY priority DESC, updated_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset]
                )
                items = cur.fetchall()
                # Parse JSON fields
                for item in items:
                    if isinstance(item.get("metadata"), str):
                        try:
                            item["metadata"] = json.loads(item["metadata"])
                        except Exception:
                            pass
                return {"total": total, "items": items}
        finally:
            conn.close()

    def get_item(self, item_id: int) -> Optional[dict]:
        """Get a single knowledge item by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_knowledge_items WHERE id = %s", (item_id,))
                item = cur.fetchone()
                if item and isinstance(item.get("metadata"), str):
                    try:
                        item["metadata"] = json.loads(item["metadata"])
                    except Exception:
                        pass
                return item
        finally:
            conn.close()

    def create_item(self, data: dict) -> int:
        """Create a new knowledge item with auto-embedding."""
        item_id = _gen_id()
        content = data.get("content", "")
        title = data.get("title", "")
        related_tables = data.get("related_tables", "")

        # Generate embedding
        embed_text = f"{title} {content} {related_tables}".strip()
        embedding = None
        try:
            vec = generate_embedding(embed_text)
            embedding = embedding_to_sql_literal(vec)
        except Exception as e:
            logger.warning("Embedding generation failed: %s", e)

        metadata_json = None
        if data.get("metadata"):
            metadata_json = json.dumps(data["metadata"], ensure_ascii=False) if isinstance(data["metadata"], dict) else data["metadata"]

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_knowledge_items
                       (id, workspace_id, datasource_id, knowledge_type, title, content,
                        metadata, related_tables, priority, is_active, embedding, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (item_id,
                     data.get("workspace_id", 0),
                     data.get("datasource_id", 0),
                     data["knowledge_type"],
                     title,
                     content,
                     metadata_json,
                     related_tables,
                     data.get("priority", 0),
                     data.get("is_active", 1),
                     embedding,
                     data.get("created_by"))
                )
                conn.commit()

                # Handle followup cases if provided
                if data["knowledge_type"] == "followup_case" and data.get("followups"):
                    self._save_followups(cur, item_id, data["followups"])
                    conn.commit()

                return item_id
        finally:
            conn.close()

    def update_item(self, item_id: int, data: dict) -> bool:
        """Update a knowledge item."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fields = []
                params = []
                for key in ["title", "content", "related_tables", "priority", "is_active",
                            "datasource_id", "knowledge_type"]:
                    if key in data:
                        fields.append(f"{key} = %s")
                        params.append(data[key])

                if "metadata" in data:
                    fields.append("metadata = %s")
                    meta = data["metadata"]
                    params.append(json.dumps(meta, ensure_ascii=False) if isinstance(meta, dict) else meta)

                # Re-generate embedding if content changed
                if "content" in data or "title" in data:
                    item = self.get_item(item_id)
                    if item:
                        embed_text = f"{data.get('title', item.get('title', ''))} {data.get('content', item.get('content', ''))} {data.get('related_tables', item.get('related_tables', ''))}".strip()
                        try:
                            vec = generate_embedding(embed_text)
                            fields.append("embedding = %s")
                            params.append(embedding_to_sql_literal(vec))
                        except Exception as e:
                            logger.warning("Embedding re-generation failed: %s", e)

                if not fields:
                    return False
                params.append(item_id)
                cur.execute(f"UPDATE adh_knowledge_items SET {', '.join(fields)} WHERE id = %s", params)

                # Handle followups
                if "followups" in data:
                    cur.execute("DELETE FROM adh_followup_cases WHERE knowledge_id = %s", (item_id,))
                    self._save_followups(cur, item_id, data["followups"])

                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_item(self, item_id: int) -> bool:
        """Delete a knowledge item and its followups."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_followup_cases WHERE knowledge_id = %s", (item_id,))
                cur.execute("DELETE FROM adh_knowledge_items WHERE id = %s", (item_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def toggle_active(self, item_id: int) -> bool:
        """Toggle the is_active flag."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE adh_knowledge_items SET is_active = 1 - is_active WHERE id = %s", (item_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Followup Cases ─────────────────────────────────────────────

    def get_followups(self, knowledge_id: int) -> list:
        """Get followup cases for a knowledge item."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_followup_cases WHERE knowledge_id = %s ORDER BY followup_order",
                    (knowledge_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def _save_followups(self, cur, knowledge_id: int, followups: list):
        """Save followup cases for a knowledge item."""
        for i, fu in enumerate(followups):
            cur.execute(
                """INSERT INTO adh_followup_cases
                   (id, knowledge_id, followup_order, followup_question, expected_sql, expected_result)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (_gen_id(), knowledge_id, i,
                 fu.get("followup_question", ""),
                 fu.get("expected_sql", ""),
                 json.dumps(fu.get("expected_result"), ensure_ascii=False) if fu.get("expected_result") else None)
            )

    # ── Search / Retrieval ─────────────────────────────────────────

    def search(self, workspace_id: int, query: str, knowledge_types: list = None,
               datasource_id: int = None, top_k: int = 5) -> list:
        """Search knowledge items by vector similarity.

        Returns the most relevant knowledge items for the given query.
        """
        from backend.common.vector import get_vector_store

        store = get_vector_store()
        query_embedding = generate_embedding(query)

        filters = {"is_active": 1, "workspace_id": workspace_id}
        if datasource_id:
            filters["_raw"] = f"(datasource_id = {datasource_id} OR datasource_id = 0)"

        results = store.search(
            table="adh_knowledge_items",
            query_embedding=query_embedding,
            limit=top_k * 2,  # fetch more, then filter by type
            filters=filters,
            output_columns=["id", "knowledge_type", "title", "content", "metadata",
                            "related_tables", "priority", "usage_count"],
        )

        # Filter by type if specified
        if knowledge_types:
            results = [r for r in results if r.get("knowledge_type") in knowledge_types]

        # Sort by priority + relevance
        results.sort(key=lambda x: x.get("priority", 0), reverse=True)

        # Increment usage count for matched items
        if results:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    for r in results[:top_k]:
                        try:
                            cur.execute(
                                "UPDATE adh_knowledge_items SET usage_count = usage_count + 1 WHERE id = %s",
                                (r["id"],)
                            )
                        except Exception:
                            pass
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

        return results[:top_k]

    def get_recommend_questions(self, workspace_id: int, datasource_id: int = None,
                                limit: int = 5) -> list:
        """Get recommended questions for the workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where = ["workspace_id = %s", "knowledge_type = 'recommend_question'", "is_active = 1"]
                params = [workspace_id]
                if datasource_id:
                    where.append("(datasource_id = %s OR datasource_id = 0)")
                    params.append(datasource_id)

                cur.execute(
                    f"SELECT id, title, content FROM adh_knowledge_items WHERE {' AND '.join(where)} ORDER BY priority DESC, usage_count DESC LIMIT %s",
                    params + [limit]
                )
                return cur.fetchall()
        finally:
            conn.close()

    def get_followup_suggestions(self, workspace_id: int, question: str,
                                  datasource_id: int = None, top_k: int = 3) -> list:
        """Get followup suggestions based on the current question."""
        results = self.search(workspace_id, question,
                              knowledge_types=["followup_case"],
                              datasource_id=datasource_id, top_k=top_k)
        suggestions = []
        for r in results:
            followups = self.get_followups(r["id"])
            for fu in followups:
                suggestions.append(fu.get("followup_question", ""))
        return suggestions[:top_k]

    # ── Statistics ─────────────────────────────────────────────────

    def get_stats(self, workspace_id: int) -> dict:
        """Get knowledge statistics for a workspace."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Count by type
                cur.execute(
                    """SELECT knowledge_type, COUNT(*) as cnt,
                       SUM(usage_count) as total_usage,
                       SUM(positive_count) as total_positive,
                       SUM(negative_count) as total_negative
                       FROM adh_knowledge_items
                       WHERE workspace_id = %s
                       GROUP BY knowledge_type""",
                    (workspace_id,)
                )
                by_type = cur.fetchall()

                # Total count
                cur.execute(
                    "SELECT COUNT(*) as total FROM adh_knowledge_items WHERE workspace_id = %s",
                    (workspace_id,)
                )
                total = cur.fetchone()["total"]

                return {"total": total, "by_type": by_type}
        finally:
            conn.close()

    # ── Feedback ───────────────────────────────────────────────────

    def record_feedback(self, item_id: int, is_positive: bool) -> None:
        """Record feedback for a knowledge item."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if is_positive:
                    cur.execute(
                        "UPDATE adh_knowledge_items SET positive_count = positive_count + 1 WHERE id = %s",
                        (item_id,)
                    )
                else:
                    cur.execute(
                        "UPDATE adh_knowledge_items SET negative_count = negative_count + 1 WHERE id = %s",
                        (item_id,)
                    )
                conn.commit()
        finally:
            conn.close()


# Singleton instance
knowledge_service = KnowledgeService()
