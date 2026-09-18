"""LLM 可观测查询服务（管理端只读视图 · O1/O2）。

数据源：MySQL 元库 `adh_llm_traces` / `adh_llm_spans` / `adh_message_feedback`
（由 services/shared/observability 写入）。本模块仅做**读取与聚合**，不改主链路。

安全：仅管理员经 api/observability.py 的 require_admin 调用；此处展示"权限改写前后
SQL / 工具产物"属受控管理端审计视图（护城河 §7 的脱敏约束针对 LLM/终端用户输出，
不适用于管理员可观测视图）。所有 SQL 走参数化查询。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from services.shared.common.db import execute_query


# ── 序列化助手：MySQL 返回的 Decimal / datetime 转 JSON 友好值 ──────────
def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return None


def _ser(row: dict) -> dict:
    """把单行里的 Decimal/datetime/date 转成可 JSON 化的值。"""
    out: dict = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            out[k] = v.decode("utf-8", "replace")
        else:
            out[k] = v
    return out


def _ser_rows(rows: list) -> list:
    return [_ser(r) for r in (rows or [])]


# ── 过滤条件构造（参数化，白名单列名） ──────────────────────────────
_ALLOWED_FILTERS = {
    "user_id": "user_id",
    "workspace_id": "workspace_id",
    "datasource_id": "datasource_id",
    "conversation_id": "conversation_id",
    "entrypoint": "entrypoint",
    "status": "status",
    "model_ref": "model_ref",
}


def _build_where(params: dict, *, alias: str = "t") -> tuple[str, list]:
    clauses: list[str] = []
    args: list[Any] = []
    for key, col in _ALLOWED_FILTERS.items():
        val = params.get(key)
        # None / 空串 / 0 一律视为"未选择",不加入过滤
        if val is None or val == "" or val == 0:
            continue
        clauses.append(f"{alias}.{col} = %s")
        args.append(val)
    start, end = params.get("start"), params.get("end")
    if start:
        clauses.append(f"{alias}.started_at >= %s")
        args.append(f"{start} 00:00:00" if len(str(start)) == 10 else start)
    if end:
        clauses.append(f"{alias}.started_at <= %s")
        args.append(f"{end} 23:59:59" if len(str(end)) == 10 else end)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, args


def _paged(page: int, size: int) -> tuple[int, int]:
    page = max(1, int(page or 1))
    size = min(200, max(1, int(size or 20)))
    return (page - 1) * size, size


# ── 概览 KPI + 趋势 ────────────────────────────────────────────────
def usage_summary(params: dict) -> dict:
    where, args = _build_where(params)
    overall = execute_query(
        f"""SELECT COUNT(*) AS turns,
                   COUNT(DISTINCT conversation_id) AS sessions,
                   COUNT(DISTINCT user_id) AS users,
                   COALESCE(SUM(llm_call_count),0) AS llm_calls,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(credits),0) AS credits,
                   COALESCE(SUM(cost_usd),0) AS cost_usd,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                   COALESCE(AVG(duration_ms),0) AS avg_duration_ms
            FROM adh_llm_traces t {where}""",
        tuple(args), fetchone=True,
    ) or {}
    daily = execute_query(
        f"""SELECT DATE(started_at) AS dt,
                   COUNT(*) AS turns,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(credits),0) AS credits,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors
            FROM adh_llm_traces t {where}
            GROUP BY DATE(started_at) ORDER BY dt ASC""",
        tuple(args),
    ) or []
    # 满意度（赞踩）统计：来自统一反馈表
    fb = execute_query(
        """SELECT SUM(CASE WHEN satisfied=1 THEN 1 ELSE 0 END) AS up,
                  SUM(CASE WHEN satisfied=0 THEN 1 ELSE 0 END) AS down
           FROM adh_message_feedback""",
        fetchone=True,
    ) or {}
    summary = _ser({**overall, "thumbs_up": (fb or {}).get("up"), "thumbs_down": (fb or {}).get("down")})
    return {"summary": summary, "daily": _ser_rows(daily)}


def usage_by_model(params: dict) -> list:
    where, args = _build_where(params)
    rows = execute_query(
        f"""SELECT model_ref,
                   COUNT(*) AS turns,
                   COALESCE(SUM(llm_call_count),0) AS llm_calls,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(credits),0) AS credits,
                   COALESCE(SUM(cost_usd),0) AS cost_usd
            FROM adh_llm_traces t {where}
            GROUP BY model_ref ORDER BY credits DESC, turns DESC""",
        tuple(args),
    )
    return _ser_rows(rows)


def usage_by_user(params: dict) -> list:
    where, args = _build_where(params)
    rows = execute_query(
        f"""SELECT user_id, MAX(username) AS username, MAX(user_role) AS user_role,
                   COUNT(*) AS turns,
                   COUNT(DISTINCT conversation_id) AS sessions,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(credits),0) AS credits,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                   MAX(started_at) AS last_active
            FROM adh_llm_traces t {where}
            GROUP BY user_id ORDER BY turns DESC LIMIT 200""",
        tuple(args),
    )
    return _ser_rows(rows)


# ── 会话清单（按 conversation 聚合，联表取标题） ────────────────────
def list_sessions(params: dict) -> dict:
    where, args = _build_where(params)
    offset, limit = _paged(params.get("page", 1), params.get("size", 20))
    # 会话数以分组结果计
    cnt = execute_query(
        f"""SELECT COUNT(DISTINCT conversation_id) AS c
            FROM adh_llm_traces t {where}""",
        tuple(args), fetchone=True,
    ) or {}
    rows = execute_query(
        f"""SELECT t.conversation_id,
                   MAX(c.title) AS title,
                   MAX(t.user_id) AS user_id,
                   MAX(t.username) AS username,
                   MAX(t.workspace_id) AS workspace_id,
                   MAX(t.datasource_id) AS datasource_id,
                   COUNT(*) AS turns,
                   COALESCE(SUM(t.total_tokens),0) AS total_tokens,
                   COALESCE(SUM(t.credits),0) AS credits,
                   MAX(t.session_credits) AS session_credits,
                   SUM(CASE WHEN t.status='error' THEN 1 ELSE 0 END) AS errors,
                   MIN(t.started_at) AS first_active,
                   MAX(t.started_at) AS last_active
            FROM adh_llm_traces t
            LEFT JOIN adh_conversations c ON c.id = t.conversation_id
            {where}
            GROUP BY t.conversation_id
            ORDER BY last_active DESC
            LIMIT %s OFFSET %s""",
        tuple(args + [limit, offset]),
    ) or []
    return {"items": _ser_rows(rows), "total": int(cnt.get("c") or 0)}


# ── Trace 列表（回合级，联反馈） ───────────────────────────────────
def list_traces(params: dict) -> dict:
    where, args = _build_where(params)
    offset, limit = _paged(params.get("page", 1), params.get("size", 20))
    cnt = execute_query(
        f"SELECT COUNT(*) AS c FROM adh_llm_traces t {where}", tuple(args), fetchone=True,
    ) or {}
    rows = execute_query(
        f"""SELECT t.trace_id, t.message_uuid, t.conversation_id, t.user_id, t.username,
                   t.entrypoint, t.model_ref, t.status, t.question, t.error_message,
                   t.started_at, t.duration_ms, t.total_tokens, t.credits,
                   t.session_credits, t.llm_call_count, t.span_count,
                   f.satisfied AS feedback_satisfied
            FROM adh_llm_traces t
            LEFT JOIN adh_message_feedback f ON f.message_uuid = t.message_uuid
            {where}
            ORDER BY t.started_at DESC
            LIMIT %s OFFSET %s""",
        tuple(args + [limit, offset]),
    ) or []
    return {"items": _ser_rows(rows), "total": int(cnt.get("c") or 0)}


def get_trace(trace_id: str) -> Optional[dict]:
    t = execute_query(
        """SELECT t.*, f.satisfied AS feedback_satisfied, f.reason AS feedback_reason,
                  f.expected_table AS feedback_expected_table, f.tags AS feedback_tags
           FROM adh_llm_traces t
           LEFT JOIN adh_message_feedback f ON f.message_uuid = t.message_uuid
           WHERE t.trace_id = %s""",
        (trace_id,), fetchone=True,
    )
    if not t:
        return None
    spans = execute_query(
        """SELECT span_id, trace_id, kind, name, status, duration_ms, model_ref,
                  input_tokens, output_tokens, total_tokens, credits, original_credits,
                  billable, cost_usd, input_text, output_text, error_text, started_at
           FROM adh_llm_spans WHERE trace_id = %s ORDER BY started_at ASC""",
        (trace_id,),
    ) or []
    return {"trace": _ser(t), "spans": _ser_rows(spans)}
