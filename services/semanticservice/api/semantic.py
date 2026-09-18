"""Semantic Layer HTTP endpoints (Phase 1).

契约:
- POST /api/semantic/resolve : SemanticQuery -> {binding, plan} 预览 (无执行)
- POST /api/semantic/query   : SemanticQuery -> SemanticResult
  * Phase 1: 编译并返回 baseSql/securedSql + provenance, rows 为空 (executor stub)
  * Phase 4: 由 planner 选路 + DataFusion RLS 路径执行, 同 contract 不变
- GET  /api/semantic/health  : 语义层依赖健康检查 (metadata_db / engine_client / oxigraph)

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

from services.shared.semantics.binding_resolver import resolve_binding
from services.shared.semantics.intent import parse_intent
from services.shared.semantics.models import (
    ResolvedBinding, SemanticQuery, SemanticResult,
)
from services.shared.semantics.planner import plan

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
    from services.shared.semantics.gates import execute_semantic

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
    from services.shared.common.db.metadata_db import get_metadata_conn
    out: dict[str, Any] = {"ok": True}
    # metadata db
    try:
        c = get_metadata_conn(); cur = c.cursor()
        cur.execute("SELECT 1"); cur.fetchone(); c.close()
        out["metadata_db"] = "ok"
    except Exception as e:
        out["metadata_db"] = f"error: {e}"; out["ok"] = False
    # engine
    try:
        from services.shared.common.engine_client import EngineClient
        out["dataengine"] = "ok" if EngineClient().health() else "unreachable"
    except Exception as e:
        out["dataengine"] = f"error: {e}"
    # oxigraph
    try:
        from services.shared.common.rdf.sparql_client import get_sparql_client
        out["oxigraph"] = "ok" if get_sparql_client().health() else "unreachable"
    except Exception as e:
        out["oxigraph"] = f"error: {e}"
    return out


# ── executor adapter (Phase 4 will replace this stub) ─────────

def _execute_via_engine(p, q: SemanticQuery) -> SemanticResult | None:
    """尝试走 DataEngine(Rust DataFusion); 不可达时返回 None, 上层回退 preview。

    Phase 1 stub: 目前只识别"能连通 + datasource_id 已在 engine 注册为 UUID"的情况;
    未注册的 datasource_id 会返回 None, 让 /query 保持 compile-only 语义。
    Phase 4 会补齐 UUID 映射 / RLS sidecar / 方言。
    """
    try:
        from services.shared.common.engine_client import EngineClient, EngineError
    except Exception:
        return None
    ec = EngineClient()
    if not ec.health():
        return None
    ds_uuid = _resolve_engine_datasource_uuid(q.datasource_id)
    if not ds_uuid:
        return None
    try:
        res = ec.query(sql=p.secured_sql or p.sql, datasource_id=ds_uuid)
    except EngineError as e:
        logger.warning("[semantic/query] engine execution failed: %s", e)
        return None
    cols = [{"name": c, "role": "plain", "data_type": ""} for c in res.columns]
    return SemanticResult(
        columns=cols, rows=res.data, row_count=res.row_count,
        applied_rls=res.rls_applied,
        resolved_tables=list(p.resolved_tables),
        provenance=p.provenance,
        warnings=list(p.warnings),
        elapsed_ms=res.execution_time_ms,
        debug={
            "baseSql": p.sql, "securedSql": p.secured_sql,
            "route": p.route, "engine_datasource": ds_uuid,
        },
    )


def _resolve_engine_datasource_uuid(datasource_id: int) -> str | None:
    """把平台 datasource_id -> DataEngine 的 UUID。

    DataFusion 侧目前用 name 注册;这里先按 id 直查缓存, 拿不到就返回 None。
    Phase 4 会补 register_datasource 流程, 让 semanticservice 自维护映射。
    """
    try:
        from services.shared.common.engine_client import EngineClient
        ec = EngineClient()
        cache = getattr(ec, "_datasource_cache", {}) or {}
        for name, uuid in cache.items():
            if str(datasource_id) in str(name):
                return uuid
    except Exception:
        return None
    return None
