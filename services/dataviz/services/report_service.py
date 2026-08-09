"""Report Service — Report generation and retrieval.

Handles LLM-powered report generation from templates and query data.
Tables: adh_reports, adh_report_templates, adh_saved_queries
"""

import json
import logging
import secrets
import time
from typing import Optional

from services.shared.common.db import DBConnection

logger = logging.getLogger(__name__)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(time.time() * 1000000)


def _normalize_report(row: dict) -> dict:
    """Normalize report row for JSON serialization."""
    for ts in ("created_at",):
        if hasattr(row.get(ts), "isoformat"):
            row[ts] = row[ts].isoformat()
    return row


# ── Report CRUD ─────────────────────────────────────────────────────────────


def list_reports(user_id: int, workspace_id: int = 0, page: int = 1, size: int = 20) -> dict:
    """List generated reports with pagination."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            conditions = []
            params = []

            if workspace_id:
                conditions.append("workspace_id = %s")
                params.append(workspace_id)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_reports {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            cur.execute(
                f"SELECT * FROM adh_reports {where} "
                f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            for r in rows:
                _normalize_report(r)
            return {"items": rows, "total": total}


def get_report(report_id: int, access_token: str = None) -> Optional[dict]:
    """Get a report by ID. For private reports, access_token is required."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_reports WHERE id = %s", (report_id,))
            row = cur.fetchone()
            if not row:
                return None

            if row.get("access_mode") == "private":
                if not access_token or access_token != row.get("access_token"):
                    return {"id": row["id"], "access_mode": "private", "error": "Access token required"}

            # Increment view count
            cur.execute("UPDATE adh_reports SET view_count = view_count + 1 WHERE id = %s", (report_id,))

            _normalize_report(row)
            return row


async def generate_report(
    template_key: str,
    data_query: str,
    owner_id: int,
    workspace_id: int = 0,
    title: str = "",
    access_mode: str = "private",
) -> dict:
    """Generate a report using LLM from a template and data query.

    1. Look up the report template by key/name
    2. Execute the data query to get results
    3. Call LLM to generate the report content
    4. Store the report and return it
    """
    report_id = _generate_id()
    access_token = secrets.token_urlsafe(32) if access_mode == "private" else None
    now = _now()

    # Fetch template
    template_content = _get_template_content(template_key)
    if not template_content:
        template_content = "Generate a clear, well-structured data analysis report."

    # Execute data query
    query_data = _execute_data_query(data_query)

    # Generate report content via LLM
    report_content = await _call_llm_for_report(template_content, query_data, title)

    final_title = title or f"Report - {template_key} - {now}"

    # Store report
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_reports "
                "(id, title, content, format, access_mode, access_token, "
                "workspace_id, owner_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    report_id,
                    final_title,
                    report_content,
                    "markdown",
                    access_mode,
                    access_token,
                    workspace_id,
                    owner_id,
                    now,
                ),
            )

    return {
        "id": report_id,
        "title": final_title,
        "content": report_content,
        "access_token": access_token,
        "access_mode": access_mode,
    }


def _get_template_content(template_key: str) -> Optional[str]:
    """Look up report template content by name or ID."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Try by name first
            cur.execute(
                "SELECT content FROM adh_report_templates WHERE name = %s LIMIT 1",
                (template_key,),
            )
            row = cur.fetchone()
            if row:
                return row.get("content")

            # Try by ID
            try:
                tid = int(template_key)
                cur.execute("SELECT content FROM adh_report_templates WHERE id = %s", (tid,))
                row = cur.fetchone()
                if row:
                    return row.get("content")
            except (ValueError, TypeError):
                pass

    return None


def _execute_data_query(data_query: str) -> dict:
    """Execute a SQL query and return the results.

    Supports referencing saved queries by ID (prefix with 'query:') or direct SQL.
    """
    if not data_query:
        return {"columns": [], "rows": [], "row_count": 0}

    sql = data_query

    # If it's a saved query reference
    if data_query.startswith("query:"):
        try:
            query_id = int(data_query.split(":", 1)[1])
            with DBConnection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT sql_query FROM adh_saved_queries WHERE id = %s", (query_id,))
                    row = cur.fetchone()
                    if row:
                        sql = row["sql_query"]
                    else:
                        return {"columns": [], "rows": [], "row_count": 0, "error": "Saved query not found"}
        except (ValueError, IndexError):
            return {"columns": [], "rows": [], "row_count": 0, "error": "Invalid query reference"}

    # Execute the SQL
    sql = sql.strip().rstrip(";")
    if "limit" not in sql.lower():
        sql += " LIMIT 500"

    try:
        with DBConnection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                columns = list(rows[0].keys()) if rows else []
                return {"columns": columns, "rows": rows, "row_count": len(rows)}
    except Exception as e:
        logger.warning("Data query execution failed: %s", e)
        return {"columns": [], "rows": [], "row_count": 0, "error": str(e)}


async def _call_llm_for_report(template_content: str, query_data: dict, title: str) -> str:
    """Call LLM to generate report content from template and data.

    Uses the Anthropic SDK configured in shared/common/config.py.
    Falls back to a simple formatted report if LLM is unavailable.
    """
    try:
        from shared.common import config

        if not config.ANTHROPIC_API_KEY:
            return _format_fallback_report(template_content, query_data, title)

        from anthropic import Anthropic

        client = Anthropic(
            api_key=config.ANTHROPIC_API_KEY,
            base_url=config.ANTHROPIC_BASE_URL,
        )

        # Prepare data summary for the prompt
        rows = query_data.get("rows", [])
        columns = query_data.get("columns", [])
        row_count = query_data.get("row_count", 0)

        # Limit data sent to LLM
        sample_rows = rows[:50] if len(rows) > 50 else rows
        data_section = ""
        if columns and sample_rows:
            data_section = f"\nColumns: {', '.join(columns)}\nRow count: {row_count}\n\nSample data (JSON):\n"
            data_section += json.dumps(sample_rows, ensure_ascii=False, default=str)[:8000]

        prompt = (
            f"You are a data analyst generating a report.\n\n"
            f"Report style/template:\n{template_content}\n\n"
            f"Report title: {title}\n\n"
            f"Data:{data_section}\n\n"
            f"Generate a well-structured markdown report following the template style. "
            f"Include key insights, trends, and actionable recommendations."
        )

        message = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        return message.content[0].text

    except Exception as e:
        logger.warning("LLM report generation failed: %s", e)
        return _format_fallback_report(template_content, query_data, title)


def _format_fallback_report(template_content: str, query_data: dict, title: str) -> str:
    """Format a simple report without LLM when it's unavailable."""
    columns = query_data.get("columns", [])
    rows = query_data.get("rows", [])
    row_count = query_data.get("row_count", 0)

    lines = [f"# {title}", "", f"Generated: {_now()}", ""]

    if query_data.get("error"):
        lines.append(f"**Error:** {query_data['error']}")
        return "\n".join(lines)

    lines.append(f"**Total rows:** {row_count}")
    lines.append("")

    if columns and rows:
        # Markdown table header
        lines.append("| " + " | ".join(str(c) for c in columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in rows[:100]:
            cells = [str(row.get(c, ""))[:100] for c in columns]
            lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)
