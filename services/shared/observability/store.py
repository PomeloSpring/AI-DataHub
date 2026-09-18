"""可观测数据落库(MySQL 元库;O0 先只用 MySQL)。

沿用现有 `get_metadata_conn`(pymysql + DictCursor + autocommit)。所有写操作
best-effort:失败仅记日志,不抛出。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("observability.store")


def _truncate(text: Any, limit: int) -> str:
    if text is None:
        return ""
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…[truncated]"


def _span_text_limit() -> int:
    """span 输入/输出落库截断长度(工具产物如 SQL/结果集可能远超默认 4096)。"""
    try:
        from services.shared.common.config import OBSERVABILITY_SPAN_MAX_CHARS
        return max(1024, int(OBSERVABILITY_SPAN_MAX_CHARS))
    except Exception:  # noqa: BLE001
        return 4096


def save_trace(summary: dict, spans: list[dict]) -> None:
    """写入一条 trace 摘要 + 其 span 明细(单事务内多次 INSERT)。"""
    from services.shared.common.db.metadata_db import get_metadata_conn

    _lim = _span_text_limit()
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO adh_llm_traces
                   (trace_id, message_uuid, conversation_id, user_id, username, user_role,
                    workspace_id, datasource_id, entrypoint, model_ref, question, final_answer,
                    status, error_message, started_at, duration_ms, input_tokens, output_tokens,
                    total_tokens, credits, cost_usd, session_credits, llm_call_count, span_count)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     final_answer=VALUES(final_answer), status=VALUES(status),
                     error_message=VALUES(error_message), duration_ms=VALUES(duration_ms),
                     input_tokens=VALUES(input_tokens), output_tokens=VALUES(output_tokens),
                     total_tokens=VALUES(total_tokens), credits=VALUES(credits),
                     cost_usd=VALUES(cost_usd), session_credits=VALUES(session_credits),
                     llm_call_count=VALUES(llm_call_count), span_count=VALUES(span_count)""",
                (
                    summary["trace_id"], summary.get("message_uuid"), summary.get("conversation_id", 0),
                    summary.get("user_id", 0), summary.get("username", ""), summary.get("user_role", ""),
                    summary.get("workspace_id", 0), summary.get("datasource_id", 0),
                    summary.get("entrypoint", "chat"), summary.get("model_ref", ""),
                    _truncate(summary.get("question", ""), 8000), _truncate(summary.get("final_answer", ""), 8000),
                    summary.get("status", "success"), _truncate(summary.get("error_message", ""), 2000),
                    summary.get("started_at") or datetime.now(timezone.utc).replace(tzinfo=None),
                    summary.get("duration_ms", 0), summary.get("input_tokens", 0),
                    summary.get("output_tokens", 0), summary.get("total_tokens", 0),
                    summary.get("credits"), summary.get("cost_usd"), summary.get("session_credits"),
                    summary.get("llm_call_count", 0), summary.get("span_count", len(spans)),
                ),
            )
            for sp in spans:
                cur.execute(
                    """INSERT INTO adh_llm_spans
                       (span_id, trace_id, kind, name, status, duration_ms, model_ref,
                        input_tokens, output_tokens, total_tokens, credits, original_credits,
                        billable, cost_usd, input_text, output_text, error_text, dt, started_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE status=VALUES(status), duration_ms=VALUES(duration_ms)""",
                    (
                        sp["span_id"], sp["trace_id"], sp.get("kind", "llm_call"), sp.get("name", ""),
                        sp.get("status", "success"), sp.get("duration_ms", 0), sp.get("model_ref", ""),
                        sp.get("input_tokens", 0), sp.get("output_tokens", 0), sp.get("total_tokens", 0),
                        sp.get("credits"), sp.get("original_credits"),
                        (1 if sp.get("billable") else 0) if sp.get("billable") is not None else None,
                        sp.get("cost_usd"), _truncate(sp.get("input_text", ""), _lim),
                        _truncate(sp.get("output_text", ""), _lim), _truncate(sp.get("error_text", ""), 2000),
                        sp.get("dt") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        sp.get("started_at") or datetime.now(timezone.utc).replace(tzinfo=None),
                    ),
                )
    except Exception as e:  # noqa: BLE001 — 落库失败不得影响主链路
        logger.warning("save_trace failed (trace_id=%s): %s", summary.get("trace_id"), e)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
