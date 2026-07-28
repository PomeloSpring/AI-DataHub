"""Deep Pipeline — Full RAG + Loop Engineering.

Wraps the existing Loop Engine with RAG metadata retrieval,
LLM-driven metadata analysis, and result interpretation.
Budget: ~30s for complex queries.
"""

import asyncio
import logging
import math
import re
from decimal import Decimal
from typing import Optional

from backend.nl2sql.orchestrator.workflow.loop_engine import execute_loop

logger = logging.getLogger(__name__)


def _sanitize_for_json(obj):
    """Make object JSON-serializable: handle NaN/inf, Decimal, datetime, bytes."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


async def deep_generate(
    question: str,
    history: list[dict] = None,
    datasource_id: int = 0,
    model_id: Optional[int] = None,
    workflow_id: Optional[int] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    retrieval_strategy: str = None,
):
    """Deep pipeline: full RAG + Loop Engineering via existing execute_loop.

    Yields:
        (event_type, data) tuples matching SSE format:
        - ("progress", {"stage": str, "message": str, "mode": "deep"})
        - ("thinking", {"text": str})
        - ("token", {"text": str})
        - ("done", {result payload})
    """
    event_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def progress_callback(stage: str, message: str, elapsed: float = None):
        """Bridge loop_engine progress to SSE events."""
        event_data = {"stage": stage, "message": message, "mode": "deep"}
        if elapsed is not None:
            event_data["elapsed"] = elapsed
        loop.call_soon_threadsafe(
            event_queue.put_nowait,
            ("progress", event_data),
        )

    def stream_callback(event_type: str, data: str):
        """Bridge loop_engine token/thinking streams to SSE events."""
        loop.call_soon_threadsafe(
            event_queue.put_nowait,
            (event_type, {"text": data}),
        )

    def run_execute_loop():
        """Run execute_loop in thread pool."""
        import asyncio as _asyncio

        async def _collect():
            final = None
            async for event_type, data in execute_loop(
                question=question,
                history=history or [],
                datasource_id=datasource_id,
                model_id=model_id,
                workflow_id=workflow_id,
                user_id=user_id,
                username=username,
                progress_callback=progress_callback,
                stream_callback=stream_callback,
                retrieval_strategy=retrieval_strategy,
            ):
                if event_type == "done":
                    final = data
                else:
                    loop.call_soon_threadsafe(event_queue.put_nowait, (event_type, data))
            return final

        try:
            result = _asyncio.run(_collect())
            loop.call_soon_threadsafe(event_queue.put_nowait, ("_done", result or {"success": False, "message": "无结果"}))
        except Exception as e:
            logger.error("deep_pipeline run_execute_loop crashed: %s", e, exc_info=True)
            loop.call_soon_threadsafe(
                event_queue.put_nowait,
                ("_done", {"success": False, "message": f"执行异常: {e}"}),
            )

    # Run in thread pool to avoid blocking
    loop.run_in_executor(None, run_execute_loop)

    # Yield events as they arrive
    result = None
    while True:
        try:
            event_type, data = await asyncio.wait_for(event_queue.get(), timeout=300)
        except asyncio.TimeoutError:
            yield "error", {"message": "深度模式执行超时"}
            break

        if event_type == "_done":
            result = data
            break
        elif event_type in ("thinking", "token"):
            yield event_type, data
        elif event_type == "progress":
            yield "progress", data
        else:
            yield event_type, data

    # Process result
    if result and result.get("success"):
        metadata = result.get("metadata_context", {})
        generated_sql = result.get("sql", "")
        sql_upper = generated_sql.upper() if generated_sql else ""

        # Extract table names from SQL for RAG filtering
        used_tables = set()
        if generated_sql:
            table_matches = re.findall(r'(?:FROM|JOIN)\s+`?(\w+)`?', sql_upper)
            used_tables = {t.lower() for t in table_matches}

        # Filter metadata to tables used in SQL
        filtered_table_info = [
            t for t in (metadata.get("table_info") or [])
            if not used_tables or t["table_name"].lower() in used_tables
        ]
        filtered_column_metadata = [
            c for c in (metadata.get("column_metadata") or [])
            if not used_tables or c["table_name"].lower() in used_tables
        ]
        filtered_relations = [
            r for r in (metadata.get("table_relations") or [])
            if not used_tables or
               r["source_table"].lower() in used_tables or
               r["target_table"].lower() in used_tables
        ]
        filtered_terms = [
            t for t in (metadata.get("business_terms") or [])
            if t.get("target_table") and t["target_table"].lower() in used_tables
        ] if used_tables else (metadata.get("business_terms") or [])[:5]

        rag_payload = {
            "rag_source": "deep_pipeline",
            "table_info": filtered_table_info,
            "table_info_count": len(filtered_table_info),
            "column_metadata": filtered_column_metadata,
            "column_metadata_count": len(filtered_column_metadata),
            "table_relations": filtered_relations,
            "table_relations_count": len(filtered_relations),
            "business_terms": filtered_terms,
            "business_terms_count": len(filtered_terms),
            "sql_templates": metadata.get("sql_templates", []),
            "sql_templates_count": len(metadata.get("sql_templates", [])),
        }

        # Extract analysis summary as displayable text
        analysis_raw = result.get("analysis")
        analysis_text = ""
        if isinstance(analysis_raw, dict):
            analysis_text = analysis_raw.get("summary", "") or analysis_raw.get("reason", "")
            if not analysis_text and analysis_raw.get("insights"):
                analysis_text = "\n".join(analysis_raw["insights"])
        elif isinstance(analysis_raw, str):
            analysis_text = analysis_raw

        yield "done", _sanitize_for_json({
            "intent": "query",
            "sql": generated_sql,
            "warnings": [
                f"工作流: {result.get('workflow', {}).get('name', '默认')}",
                f"使用轮数: {result.get('workflow', {}).get('rounds_used', 0)}",
                f"Loop次数: {result.get('workflow', {}).get('loop_count', 0)}",
            ],
            "chart_type": result.get("chart_type", "table"),
            "brief": "",
            "thinking": result.get("thinking"),
            "tokens": result.get("tokens", {}),
            "timings": {"total": result.get("elapsed_ms", 0) / 1000},
            "result": result.get("result", {}),
            "analysis": analysis_text,
            "rag": rag_payload,
            "workflow_info": result.get("workflow", {}),
            "log_id": result.get("log_id"),
            "mode": "deep",
        })
    elif result:
        yield "done", _sanitize_for_json({
            "intent": "query",
            "reply": result.get("message", "处理失败"),
            "sql": None,
            "warnings": [],
            "error": result.get("message"),
            "log_id": result.get("log_id"),
            "mode": "deep",
        })
    else:
        yield "done", _sanitize_for_json({
            "intent": "query",
            "reply": "深度模式执行异常",
            "sql": None,
            "warnings": [],
            "error": "未收到执行结果",
            "mode": "deep",
        })
