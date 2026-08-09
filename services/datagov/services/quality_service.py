"""Quality Review Service — objective metrics + optional manual LLM review.

Design:
- Auto-collect: execution status, row count, elapsed time, retry count, pipeline mode, user feedback
- Manual LLM review: admin triggers on-demand, not automatic
- Statistics: aggregate metrics for quality dashboard
"""

import json
import logging
import time
from datetime import datetime, date
from typing import Optional

from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _gen_id():
    return int(time.time() * 1000000)


class QualityService:
    """Quality review service."""

    # ── Create Review (auto-collect objective metrics) ──────────────

    def create_review(self, workspace_id: int, user_id: int, username: str,
                      question: str, sql: str = "", result: dict = None,
                      datasource_id: int = 0, conversation_id: int = 0,
                      message_id: int = 0, pipeline_mode: str = "",
                      retry_count: int = 0, elapsed_ms: int = 0) -> int:
        """Create a quality review record with objective metrics."""
        review_id = _gen_id()

        # Extract objective metrics from result
        execution_status = "success"
        row_count = 0
        error_message = ""
        if result:
            row_count = result.get("row_count", 0)
            if result.get("error"):
                execution_status = "error"
                error_message = str(result.get("error", ""))[:500]
            elif row_count == 0:
                execution_status = "empty"

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_quality_reviews
                       (id, workspace_id, conversation_id, message_id, user_id, username,
                        question, generated_sql, datasource_id,
                        execution_status, row_count, elapsed_ms, retry_count,
                        pipeline_mode, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'recorded')""",
                    (review_id, workspace_id, conversation_id, message_id,
                     user_id, username, question, sql, datasource_id,
                     execution_status, row_count, elapsed_ms, retry_count,
                     pipeline_mode)
                )
                conn.commit()
                return review_id
        finally:
            conn.close()

    def update_feedback(self, review_id: int, satisfied: bool) -> bool:
        """Update user feedback for a review."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_quality_reviews SET user_feedback = %s WHERE id = %s",
                    (1 if satisfied else 0, review_id)
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def update_feedback_by_question(self, user_id: int, question: str, satisfied: bool) -> bool:
        """Update feedback by matching user_id + question (for cases where review_id not available)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE adh_quality_reviews SET user_feedback = %s
                       WHERE user_id = %s AND question = %s AND user_feedback IS NULL
                       ORDER BY created_at DESC LIMIT 1""",
                    (1 if satisfied else 0, user_id, question)
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Manual LLM Review ──────────────────────────────────────────

    async def manual_llm_review(self, review_id: int) -> dict:
        """Trigger LLM review on-demand for a specific review.

        Returns scores and analysis text. Called manually by admin, not automatically.
        """
        from services.shared.common.llm.llm_client import async_generate_sql

        review = self.get_review(review_id)
        if not review:
            return {"error": "Review not found"}

        question = review.get("question", "")
        sql = review.get("generated_sql", "")
        exec_status = review.get("execution_status", "")
        row_count = review.get("row_count", 0)

        prompt = f"""请分析以下 NL2SQL 查询的质量，给出简洁的评审意见。

## 用户问题
{question}

## 生成的 SQL
{sql or '(无)'}

## 执行状态
{exec_status}, 返回 {row_count} 行

请用中文回答，包含：
1. SQL 是否正确回答了用户问题（是/否/部分）
2. 主要问题（如有）
3. 改进建议（如有）

简洁回答，不超过 200 字。"""

        try:
            messages = [
                {"role": "system", "content": "你是数据查询质量评审专家。简洁分析，不要废话。"},
                {"role": "user", "content": prompt}
            ]
            result = await async_generate_sql(messages, model_id=None)
            analysis = result.get("sql", "")

            # Save to database
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE adh_quality_reviews SET
                           llm_review = %s, llm_reviewed_at = %s
                           WHERE id = %s""",
                        (analysis, datetime.now(), review_id)
                    )
                    conn.commit()
            finally:
                conn.close()

            return {"analysis": analysis}

        except Exception as e:
            logger.warning("LLM review failed for review %s: %s", review_id, e)
            return {"error": str(e)}

    # ── Query ──────────────────────────────────────────────────────

    def get_review(self, review_id: int) -> Optional[dict]:
        """Get a single review."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_quality_reviews WHERE id = %s", (review_id,))
                return cur.fetchone()
        finally:
            conn.close()

    def list_reviews(self, workspace_id: int, status: str = None,
                     execution_status: str = None, has_feedback: bool = None,
                     pipeline_mode: str = None, page: int = 1, size: int = 20) -> dict:
        """List quality reviews with filters."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where = ["workspace_id = %s"]
                params = [workspace_id]
                if status:
                    where.append("status = %s")
                    params.append(status)
                if execution_status:
                    where.append("execution_status = %s")
                    params.append(execution_status)
                if has_feedback is not None:
                    if has_feedback:
                        where.append("user_feedback IS NOT NULL")
                    else:
                        where.append("user_feedback IS NULL")
                if pipeline_mode:
                    where.append("pipeline_mode = %s")
                    params.append(pipeline_mode)

                where_clause = " AND ".join(where)
                cur.execute(f"SELECT COUNT(*) as total FROM adh_quality_reviews WHERE {where_clause}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_quality_reviews WHERE {where_clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset]
                )
                return {"total": total, "items": cur.fetchall()}
        finally:
            conn.close()

    # ── Statistics ─────────────────────────────────────────────────

    def get_stats(self, workspace_id: int, date_from: str = None, date_to: str = None) -> dict:
        """Get quality statistics."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where = ["workspace_id = %s"]
                params = [workspace_id]
                if date_from:
                    where.append("created_at >= %s")
                    params.append(date_from)
                if date_to:
                    where.append("created_at <= %s")
                    params.append(date_to + " 23:59:59")

                where_clause = " AND ".join(where)

                # Overall counts
                cur.execute(
                    f"""SELECT
                       COUNT(*) as total,
                       SUM(CASE WHEN execution_status = 'success' THEN 1 ELSE 0 END) as success_count,
                       SUM(CASE WHEN execution_status = 'error' THEN 1 ELSE 0 END) as error_count,
                       SUM(CASE WHEN execution_status = 'empty' THEN 1 ELSE 0 END) as empty_count,
                       AVG(elapsed_ms) as avg_elapsed_ms,
                       AVG(row_count) as avg_row_count,
                       AVG(retry_count) as avg_retry_count,
                       SUM(CASE WHEN user_feedback = 1 THEN 1 ELSE 0 END) as thumbs_up,
                       SUM(CASE WHEN user_feedback = 0 THEN 1 ELSE 0 END) as thumbs_down,
                       SUM(CASE WHEN llm_review IS NOT NULL THEN 1 ELSE 0 END) as llm_reviewed
                       FROM adh_quality_reviews WHERE {where_clause}""",
                    params
                )
                overview = cur.fetchone()

                # By pipeline mode
                cur.execute(
                    f"""SELECT pipeline_mode, COUNT(*) as cnt,
                       SUM(CASE WHEN execution_status = 'success' THEN 1 ELSE 0 END) as success,
                       AVG(elapsed_ms) as avg_ms
                       FROM adh_quality_reviews
                       WHERE {where_clause}
                       GROUP BY pipeline_mode""",
                    params
                )
                by_mode = cur.fetchall()

                # Daily trend
                cur.execute(
                    f"""SELECT DATE(created_at) as dt, COUNT(*) as cnt,
                       SUM(CASE WHEN execution_status = 'success' THEN 1 ELSE 0 END) as success
                       FROM adh_quality_reviews
                       WHERE {where_clause}
                       GROUP BY DATE(created_at)
                       ORDER BY dt DESC LIMIT 30""",
                    params
                )
                daily_trend = cur.fetchall()

                total = overview.get("total", 0)
                success_rate = (overview.get("success_count", 0) / total * 100) if total > 0 else 0
                satisfaction_rate = 0
                fb_total = (overview.get("thumbs_up", 0) or 0) + (overview.get("thumbs_down", 0) or 0)
                if fb_total > 0:
                    satisfaction_rate = (overview.get("thumbs_up", 0) or 0) / fb_total * 100

                return {
                    "total": total,
                    "success_count": overview.get("success_count", 0),
                    "error_count": overview.get("error_count", 0),
                    "empty_count": overview.get("empty_count", 0),
                    "success_rate": round(success_rate, 1),
                    "avg_elapsed_ms": round(overview.get("avg_elapsed_ms", 0) or 0),
                    "avg_row_count": round(overview.get("avg_row_count", 0) or 0, 1),
                    "avg_retry_count": round(overview.get("avg_retry_count", 0) or 0, 2),
                    "thumbs_up": overview.get("thumbs_up", 0),
                    "thumbs_down": overview.get("thumbs_down", 0),
                    "satisfaction_rate": round(satisfaction_rate, 1),
                    "llm_reviewed": overview.get("llm_reviewed", 0),
                    "by_mode": by_mode,
                    "daily_trend": daily_trend,
                }
        finally:
            conn.close()

    def generate_daily_stats(self, workspace_id: int, stat_date: str = None) -> None:
        """Generate daily quality statistics snapshot."""
        if not stat_date:
            stat_date = date.today().isoformat()

        stats = self.get_stats(workspace_id, date_from=stat_date, date_to=stat_date)

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Delete existing snapshot for this date
                cur.execute(
                    "DELETE FROM adh_quality_stats WHERE workspace_id = %s AND stat_date = %s",
                    (workspace_id, stat_date)
                )
                cur.execute(
                    """INSERT INTO adh_quality_stats
                       (id, workspace_id, stat_date, total_queries, avg_score, score_distribution, issue_top_tags)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (_gen_id(), workspace_id, stat_date, stats.get("total", 0),
                     stats.get("success_rate", 0),
                     json.dumps({"success": stats.get("success_count", 0),
                                 "error": stats.get("error_count", 0),
                                 "empty": stats.get("empty_count", 0)}),
                     json.dumps({"satisfaction": stats.get("satisfaction_rate", 0)}))
                )
                conn.commit()
        finally:
            conn.close()


# Singleton instance
quality_service = QualityService()
