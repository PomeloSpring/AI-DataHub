"""LLM 可观测查询服务（管理端只读视图 · O1/O2）。

数据源：MySQL 元库 `adh_llm_traces` / `adh_llm_spans` / `adh_message_feedback`
（由 backend/observability 写入）。本模块仅做**读取与聚合**，不改主链路。

安全：仅管理员经 api/observability.py 的 require_admin 调用；此处展示"权限改写前后
SQL / 工具产物"属受控管理端审计视图（护城河 §7 的脱敏约束针对 LLM/终端用户输出，
不适用于管理员可观测视图）。所有 SQL 走参数化查询。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from backend.common.db import execute_query


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
    "handled_by": "handled_by",   # agent=走了执行层模型 / intent_gate=前置意图层本地直答
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
                   COALESCE(SUM(cost_amount),0) AS cost_amount,
                   MAX(cost_currency) AS cost_currency,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                   SUM(CASE WHEN handled_by='intent_gate' THEN 1 ELSE 0 END) AS intercepted,
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
    # 处理方式分桶（前置意图漏斗的拦截率就靠这一列说话）
    by_handled = _ser_rows(execute_query(
        f"""SELECT handled_by,
                   COUNT(*) AS turns,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(credits),0) AS credits
            FROM adh_llm_traces t {where}
            GROUP BY handled_by ORDER BY turns DESC""",
        tuple(args),
    ) or [])
    summary["by_handled_by"] = by_handled
    _turns = int(summary.get("turns") or 0)
    _hits = int(summary.get("intercepted") or 0)
    summary["intercept_rate"] = round(_hits / _turns, 4) if _turns else 0.0
    # 节省量是**估算**，不是实测：Qoder 不回报单回合美元成本，也无法对"没发生的回合"采样。
    # 口径：拦截回合数 × 同时间窗内 agent 回合的平均 credit（只取 credits 非空的行）。
    _avg_agent_credit = execute_query(
        f"""SELECT COALESCE(AVG(credits),0) AS avg_credits
            FROM adh_llm_traces t {where} AND handled_by = 'agent' AND credits IS NOT NULL"""
        if where else
        """SELECT COALESCE(AVG(credits),0) AS avg_credits
           FROM adh_llm_traces t WHERE handled_by = 'agent' AND credits IS NOT NULL""",
        tuple(args) if where else (), fetchone=True,
    ) or {}
    summary["avg_agent_turn_credits"] = round(float(_avg_agent_credit.get("avg_credits") or 0), 6)
    summary["estimated_saved_credits"] = round(_hits * float(summary["avg_agent_turn_credits"]), 6)
    summary["saved_credits_is_estimate"] = True   # 前端必须按"估算"展示，不假装实测
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
                   COALESCE(SUM(cost_amount),0) AS cost_amount,
                   MAX(cost_currency) AS cost_currency
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
                   t.entrypoint, t.handled_by, t.model_ref, t.status, t.question, t.error_message,
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
                  billable, cost_amount, input_text, output_text, error_text, started_at
           FROM adh_llm_spans WHERE trace_id = %s ORDER BY started_at ASC""",
        (trace_id,),
    ) or []
    return {"trace": _ser(t), "spans": _ser_rows(spans)}


# ── 意图漏斗判定流水（adh_intent_intercepts）───────────────────────────
# 为什么单独一套查询而不是只看 trace：OBSERVABILITY_ENABLED=false 时 trace 整条
# 不落库，而"拦截率/误拦复盘"是切 enforce 的唯一依据（fde-evolution §1：没分桶数据
# 不做策略取舍）。表不存在时**显式返回 available=false + 原因**，不返回空数据假能用。

_FUNNEL_FILTERS = {
    "workspace_id": "i.workspace_id",
    "user_id": "i.user_id",
    "datasource_id": "i.datasource_id",
    "conversation_id": "i.conversation_id",
    "as_bot_key": "i.as_bot_key",
    "mode": "i.mode",
    "label": "i.label",
    "decision": "i.decision",
}


def _funnel_where(params: dict) -> tuple[str, list]:
    clauses: list[str] = []
    args: list[Any] = []
    for key, col in _FUNNEL_FILTERS.items():
        val = params.get(key)
        if val is None or val == "" or val == 0:
            continue
        clauses.append(f"{col} = %s")
        args.append(val)
    start, end = params.get("start"), params.get("end")
    if start:
        clauses.append("i.dt >= %s")
        args.append(f"{start} 00:00:00" if len(str(start)) == 10 else start)
    if end:
        clauses.append("i.dt <= %s")
        args.append(f"{end} 23:59:59" if len(str(end)) == 10 else end)
    return (("WHERE " + " AND ".join(clauses)) if clauses else ""), args


def _funnel_missing(exc: Exception) -> dict:
    """迁移未执行/表不可达：把原因面告知管理员，而不是给个空报表。"""
    return {
        "available": False,
        "reason": ("adh_intent_intercepts 不可达（请执行 docker/mysql/intent_funnel_migration.sql）: "
                   f"{type(exc).__name__}: {str(exc)[:200]}"),
    }


def funnel_stats(params: dict) -> dict:
    where, args = _funnel_where(params)
    try:
        overall = execute_query(
            f"""SELECT COUNT(*) AS judged,
                       SUM(CASE WHEN decision='local' THEN 1 ELSE 0 END) AS local,
                       SUM(CASE WHEN decision='shadow_local' THEN 1 ELSE 0 END) AS shadow_local,
                       SUM(CASE WHEN decision='agent' THEN 1 ELSE 0 END) AS to_agent,
                       SUM(CASE WHEN gate_error<>'' THEN 1 ELSE 0 END) AS gate_errors,
                       SUM(CASE WHEN force_agent=1 THEN 1 ELSE 0 END) AS forced_agent,
                       COALESCE(AVG(latency_ms),0) AS avg_latency_ms
                FROM adh_intent_intercepts i {where}""",
            tuple(args), fetchone=True,
        ) or {}
        by_label = execute_query(
            f"""SELECT i.label, i.decision, COUNT(*) AS cnt,
                       COALESCE(AVG(i.latency_ms),0) AS avg_latency_ms
                FROM adh_intent_intercepts i {where}
                GROUP BY i.label, i.decision ORDER BY cnt DESC""",
            tuple(args),
        ) or []
        daily = execute_query(
            f"""SELECT DATE(i.dt) AS dt, COUNT(*) AS judged,
                       SUM(CASE WHEN i.decision='local' THEN 1 ELSE 0 END) AS local,
                       SUM(CASE WHEN i.decision='shadow_local' THEN 1 ELSE 0 END) AS shadow_local,
                       SUM(CASE WHEN i.gate_error<>'' THEN 1 ELSE 0 END) AS gate_errors
                FROM adh_intent_intercepts i {where}
                GROUP BY DATE(i.dt) ORDER BY dt ASC""",
            tuple(args),
        ) or []
    except Exception as exc:  # noqa: BLE001
        return _funnel_missing(exc)
    judged = int(overall.get("judged") or 0)
    local = int(overall.get("local") or 0)
    shadow = int(overall.get("shadow_local") or 0)
    return {
        "available": True,
        "overall": _ser(overall),
        # 两个口径分开：enforce 下的真实拦截率 vs 判定可拦比例（shadow 期只能看后者）
        "intercept_rate": round(local / judged, 4) if judged else 0.0,
        "eligible_rate": round((local + shadow) / judged, 4) if judged else 0.0,
        "by_label": _ser_rows(by_label),
        "daily": _ser_rows(daily),
    }


def funnel_intercepts(params: dict) -> dict:
    """判定流水列表（误拦复盘用）。显示用 name：JOIN 出用户名与 AS-BOT 显示名。"""
    where, args = _funnel_where(params)
    offset, limit = _paged(params.get("page", 1), params.get("size", 20))
    try:
        cnt = execute_query(
            f"SELECT COUNT(*) AS c FROM adh_intent_intercepts i {where}",
            tuple(args), fetchone=True,
        ) or {}
        rows = execute_query(
            f"""SELECT i.id, i.dt, i.trace_id, i.conversation_id, i.user_id,
                       COALESCE(u.username, '') AS username,
                       i.workspace_id, i.datasource_id, i.as_bot_key,
                       COALESCE(NULLIF(b.display_name, ''), NULLIF(b.name, ''), i.as_bot_key, '') AS as_bot_name,
                       i.mode, i.layer, i.label, i.decision, i.reason, i.signals,
                       i.question_digest, i.latency_ms, i.gate_error, i.force_agent,
                       i.reviewed, i.review_note
                FROM adh_intent_intercepts i
                LEFT JOIN adh_users u ON u.id = i.user_id
                LEFT JOIN adh_as_bots b ON b.as_bot_key = i.as_bot_key
                {where}
                ORDER BY i.dt DESC, i.id DESC
                LIMIT %s OFFSET %s""",
            tuple(args + [limit, offset]),
        ) or []
    except Exception as exc:  # noqa: BLE001
        return {**_funnel_missing(exc), "items": [], "total": 0}
    return {"available": True, "items": _ser_rows(rows), "total": int(cnt.get("c") or 0)}


def review_intercept(intercept_id: int, reviewed: bool, note: str = "") -> None:
    from backend.common.db import execute_write

    execute_write(
        "UPDATE adh_intent_intercepts SET reviewed=%s, review_note=%s WHERE id=%s",
        (1 if reviewed else 0, (note or "")[:500], int(intercept_id)))
