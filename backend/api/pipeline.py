"""Pipeline API — Quick-Deep dual-mode NL2SQL with unified SSE streaming.

Endpoints:
- POST /api/pipeline/send/stream — Unified pipeline with quick/deep mode selection
- GET  /api/pipeline/metrics     — Pipeline performance metrics summary
"""

import json
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from backend.api.auth import get_current_user
from backend.models.schemas import ChatRequest, UserInfo
from backend.nl2sql.orchestrator.pipeline_orchestrator import execute_pipeline

logger = logging.getLogger(__name__)
router = APIRouter()


def _sse_event(event: str, data: dict) -> bytes:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


def _record_pipeline_metric(
    user_id: int,
    username: str,
    question: str,
    requested_mode: str,
    resolved_mode: str,
    success: bool,
    elapsed_ms: int,
    stage_timings: dict = None,
    token_count: dict = None,
    error: str = None,
):
    """Record pipeline execution metrics to database."""
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                metric_id = int(time.time() * 1000000)
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    "INSERT INTO adh_pipeline_metrics "
                    "(id, user_id, username, question, requested_mode, resolved_mode, "
                    "fallback_used, success, elapsed_ms, stage_timings, token_count, "
                    "error_message, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        metric_id, user_id, username, question[:200],
                        requested_mode, resolved_mode,
                        0,  # fallback_used no longer applies
                        1 if success else 0,
                        elapsed_ms,
                        json.dumps(stage_timings or {}, ensure_ascii=False),
                        json.dumps(token_count or {}, ensure_ascii=False),
                        (error or "")[:500],
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to record pipeline metric: %s", e)


@router.post("/send/stream")
async def pipeline_send_stream(req: ChatRequest, request: Request, user: UserInfo = Depends(get_current_user)):
    """Unified pipeline endpoint with Quick/Deep mode selection.

    Pipeline modes:
    - "quick": Fast direct LLM call with RAG metadata
    - "deep": Full RAG + Loop Engineering
    - "agent": LLM autonomous tool calling

    Events: progress, thinking, token, done, error.
    """
    question = req.question
    history = req.history or []
    datasource_id = req.datasource_id or 0
    model_id = req.model_id
    pipeline_mode = getattr(req, 'pipeline_mode', 'quick') or 'quick'
    workflow_id = req.workflow_id
    retrieval_strategy = getattr(req, 'retrieval_strategy', None) or None
    workspace_id = getattr(req, 'workspace_id', 0) or 0

    start_time = time.time()
    success = False
    error_message = None

    async def event_generator():
        nonlocal success, error_message
        try:
            async for event_type, data in execute_pipeline(
                question=question,
                history=history,
                datasource_id=datasource_id,
                model_id=model_id,
                pipeline_mode=pipeline_mode,
                workflow_id=workflow_id,
                user_id=user.id,
                username=user.username,
                retrieval_strategy=retrieval_strategy,
                workspace_id=workspace_id,
            ):
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info("Client disconnected, stopping pipeline (mode=%s)", pipeline_mode)
                    break

                if event_type == "done":
                    success = not bool(data.get("error"))
                    error_message = data.get("error")

                yield _sse_event(event_type, data)

        except Exception as e:
            logger.error("Pipeline stream error: %s", e, exc_info=True)
            error_message = str(e)
            yield _sse_event("error", {"message": str(e)})
            yield _sse_event("done", {
                "intent": "query",
                "reply": f"处理出错: {str(e)}",
                "sql": None,
                "warnings": [],
                "error": str(e),
                "mode": pipeline_mode,
            })

        finally:
            # Record metrics
            elapsed_ms = int((time.time() - start_time) * 1000)
            _record_pipeline_metric(
                user_id=user.id,
                username=user.username,
                question=question,
                requested_mode=pipeline_mode,
                resolved_mode=pipeline_mode,
                success=success,
                elapsed_ms=elapsed_ms,
                error=error_message,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/ask/respond")
async def respond_to_ask(req: Request):
    """Respond to an agent's ask_user question, resuming the paused agent loop."""
    from backend.nl2sql.orchestrator.agent_pipeline import submit_user_response
    body = await req.json()
    request_id = body.get("request_id", "")
    response = body.get("response", "")
    if not request_id:
        return {"success": False, "error": "request_id is required"}
    submit_user_response(request_id, response)
    return {"success": True}


@router.get("/metrics")
def get_pipeline_metrics(user: UserInfo = Depends(get_current_user)):
    """Get pipeline performance metrics summary.

    Returns mode distribution, P95 latency, and fallback rates.
    """
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            # Overall stats
            cur.execute(
                "SELECT COUNT(*) as total, "
                "AVG(elapsed_ms) as avg_ms, "
                "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count, "
                "SUM(CASE WHEN fallback_used = 1 THEN 1 ELSE 0 END) as fallback_count "
                "FROM adh_pipeline_metrics "
                "WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)"
            )
            overall = cur.fetchone() or {}

            # Per-mode stats
            cur.execute(
                "SELECT resolved_mode, "
                "COUNT(*) as count, "
                "AVG(elapsed_ms) as avg_ms, "
                "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count, "
                "SUM(CASE WHEN fallback_used = 1 THEN 1 ELSE 0 END) as fallback_count "
                "FROM adh_pipeline_metrics "
                "WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) "
                "GROUP BY resolved_mode"
            )
            by_mode = cur.fetchall() or []

            # P95 per mode (approximate using percentile)
            mode_p95 = {}
            for mode_row in by_mode:
                mode = mode_row["resolved_mode"]
                cur.execute(
                    "SELECT elapsed_ms FROM adh_pipeline_metrics "
                    "WHERE resolved_mode = %s AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) "
                    "ORDER BY elapsed_ms LIMIT 1 OFFSET %s",
                    (mode, max(0, int(mode_row["count"] * 0.95) - 1)),
                )
                p95_row = cur.fetchone()
                mode_p95[mode] = p95_row["elapsed_ms"] if p95_row else 0

            return {
                "total": overall.get("total", 0),
                "avg_ms": round(overall.get("avg_ms", 0) or 0),
                "success_rate": round((overall.get("success_count", 0) or 0) / max(overall.get("total", 1), 1), 3),
                "fallback_rate": round((overall.get("fallback_count", 0) or 0) / max(overall.get("total", 1), 1), 3),
                "by_mode": {
                    row["resolved_mode"]: {
                        "count": row["count"],
                        "avg_ms": round(row["avg_ms"] or 0),
                        "p95_ms": mode_p95.get(row["resolved_mode"], 0),
                        "success_rate": round((row["success_count"] or 0) / max(row["count"], 1), 3),
                        "fallback_rate": round((row["fallback_count"] or 0) / max(row["count"], 1), 3),
                    }
                    for row in by_mode
                },
            }
    except Exception as e:
        logger.error("Failed to fetch pipeline metrics: %s", e)
        return {"total": 0, "by_mode": {}, "error": str(e)}
    finally:
        conn.close()
