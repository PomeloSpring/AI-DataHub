"""Semantic Layer HTTP endpoints (Phase 1).

契约:
- POST /api/semantic/resolve : SemanticQuery -> {binding, plan} 预览 (无执行)
- POST /api/semantic/query   : SemanticQuery -> SemanticResult
  * Phase 1: 编译并返回 baseSql/securedSql + provenance, rows 为空 (executor stub)
  * Phase 4: 由 planner 选路 + DataFusion RLS 路径执行, 同 contract 不变
- GET  /api/semantic/health  : 语义层依赖健康检查 (metadata_db / engine / oxigraph)

设计原则:
- 只接受**声明式意图**, 拒绝任何 SQL 字段(intent.py 已过滤)
- 所有响应都带 provenance, 供 Playground/审计展示"所见即所执行"
- 未来 GraphQL 门面可复用同一份 SemanticQuery/SemanticResult schema
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.semantics.binding_resolver import resolve_binding
from backend.semantics.intent import parse_intent
from backend.semantics.models import (
    ResolvedBinding, SemanticQuery, SemanticResult,
)
from backend.semantics.planner import plan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/semantic", tags=["semantic"])


# ── helpers ────────────────────────────────────────────────────

def _binding_payload(b: ResolvedBinding | None) -> dict[str, Any] | None:
    return b.model_dump() if b else None


def _parse_query_or_400(body: dict[str, Any]) -> SemanticQuery:
    q, err, notes = parse_intent(body)
    if err:
        raise HTTPException(status_code=400, detail={"error": err, "notes": notes})
    return q  # type: ignore[return-value]


# ── endpoints ──────────────────────────────────────────────────

@router.get("/schema", summary="跨语言唯一契约 JSON Schema（SemanticQuery/SemanticResult）")
async def schema() -> dict[str, Any]:
    """导出语义层唯一契约的 JSON Schema（Phase 6 契约先行，护栏 §7 错误口径随附）。"""
    from backend.semantics.contract import export_schemas
    return export_schemas()


@router.post("/resolve", summary="intent -> binding + compiled plan preview (no execution)")
async def resolve(body: dict[str, Any]) -> dict[str, Any]:
    q = _parse_query_or_400(body)
    binding, bind_warnings = resolve_binding(q.object, datasource_id=q.datasource_id)
    if binding is None:
        raise HTTPException(status_code=404, detail={
            "error": f"object '{q.object}' not bound to any physical table",
            "warnings": bind_warnings,
        })
    p = plan(q, binding)
    return {
        "intent": q.model_dump(),
        "binding": _binding_payload(binding),
        "plan": {
            "sql": p.sql,
            "secured_sql": p.secured_sql,
            "route": p.route,
            "dialect": p.dialect,
            "guardrail": p.guardrail.model_dump(),
            "access": p.access.model_dump(),
            "provenance": p.provenance,
            "warnings": p.warnings + [f"binding: {w}" for w in bind_warnings],
        },
    }


@router.post("/query", response_model=SemanticResult, summary="SemanticQuery -> SemanticResult")
async def query(body: dict[str, Any]) -> SemanticResult:
    t0 = time.perf_counter()
    q = _parse_query_or_400(body)
    binding, bind_warnings = resolve_binding(q.object, datasource_id=q.datasource_id)
    if binding is None:
        raise HTTPException(status_code=404, detail={
            "error": f"object '{q.object}' not bound to any physical table",
            "warnings": bind_warnings,
        })
    p = plan(q, binding)

    # Phase 4: 统一走七闸门链(identity/permission/preflight/proposal/approval/execute/audit);
    # RLS sqlglot 改写与执行都在 gates 内, 与 ChatBI run_semantic_query 同源。
    from backend.semantics.gates import execute_semantic

    user_context = {
        "user_id": q.user_id or None,
        "workspace_id": q.workspace_id or 0,
    }
    se = await _to_thread(execute_semantic, q, binding, p, user_context, "")

    warnings: list[str] = list(p.warnings) + [f"binding: {w}" for w in bind_warnings]
    res = se.result or {}
    columns = [
        {"name": c, "role": "plain", "data_type": ""}
        for c in (res.get("columns") or [])
    ]
    if se.blocked_at:
        warnings.append(f"blocked at gate '{se.blocked_at}': {se.reason}")

    result = SemanticResult(
        columns=columns,
        rows=res.get("rows", []),
        row_count=int(res.get("row_count", 0) or 0),
        applied_rls=list(se.applied_rls),
        masked_columns=list(se.masked_columns),
        resolved_tables=list(p.resolved_tables),
        provenance={**se.provenance, "allowed": se.allowed, "blocked_at": se.blocked_at},
        warnings=warnings,
        elapsed_ms=int(res.get("execution_ms", 0) or 0),
        debug={
            "baseSql": se.base_sql or p.sql,
            "securedSql": se.secured_sql or p.secured_sql,
            "route": p.route,
            "guardrail": p.guardrail.model_dump(),
            "access": p.access.model_dump(),
            "binding": _binding_payload(binding),
            "gateTrail": se.gate_trail,
            "needsApproval": se.needs_approval,
        },
    )
    if result.elapsed_ms == 0:
        result.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return result


async def _to_thread(fn, *args):
    import asyncio
    return await asyncio.to_thread(fn, *args)


@router.get("/health", summary="semantic layer dependency health")
async def health() -> dict[str, Any]:
    from backend.common.db.metadata_db import get_metadata_conn
    out: dict[str, Any] = {"ok": True}
    # metadata db
    try:
        c = get_metadata_conn(); cur = c.cursor()
        cur.execute("SELECT 1"); cur.fetchone(); c.close()
        out["metadata_db"] = "ok"
    except Exception as e:
        out["metadata_db"] = f"error: {e}"; out["ok"] = False
    # engine（semantics.execution）
    try:
        from backend.semantics.execution import check_version

        check_version()
        out["engine"] = "ok"
    except Exception as e:
        out["engine"] = f"error: {e}"
    # oxigraph
    try:
        from backend.common.rdf.sparql_client import get_sparql_client
        out["oxigraph"] = "ok" if get_sparql_client().health() else "unreachable"
    except Exception as e:
        out["oxigraph"] = f"error: {e}"
    return out


