"""统一语义执行门面 —— `execute(query|sql, policy_ctx)`（Phase 6 契约先行）。

Phase 6.4 起全平台取数统一改经本门面（护栏 §1 口径升级为"`semantics.execute` 是
全平台唯一取数执行口"，语义/nl2sql 双轨执行就此统一）。当前实现复用既有受治理链路：

    intent → binding_resolver → planner → 七闸门(gates: RLS 改写 + 审计)
           → execute_query_with_permission(core: DataFusion RLS 二次校验)

Phase 6.2/6.3 将执行内核换为 `semantics/execution/`（datafusion-python 内嵌引擎）与
`semantics/security/`（RLS/mask 视图化强制），本门面对外契约不变（no-silent-degradation）。

- `execute(query|dict, policy_ctx)`: 声明式意图 → SemanticResult；拒绝/失败抛 SemanticError。
- `execute_sql(sql, policy_ctx)`: 受治理 SQL 执行口（nl2sql/Playground 等 SQL 形状的唯一
  出口，必经 `execute_query_with_permission`，不得直连数据源）。
- `policy_ctx` = 可信身份 + enforcer 裁决产物（`policy` 槽留给 Phase 6.3 的 RLS/mask
  视图化强制消费）；身份/数据源只信 policy_ctx（护栏 §2，不信 query 内声明字段）。
- 引擎原始错误仅进服务端日志，对外一律 `contract.EXEC_FAIL_HINT`（护栏 §7）；
  结果序列化必经 `df_to_columns_rows` 无损消歧（护栏 §8）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.common.df_serialize import df_to_columns_rows
from backend.semantics.binding_resolver import resolve_binding
from backend.semantics.contract import (
    EXEC_FAIL_HINT,
    ErrorCode,
    SemanticError,
    block_code,
    safe_warnings,
    sanitize_execute_reason,
)
from backend.semantics.gates import SemanticExecution, execute_semantic
from backend.semantics.intent import parse_intent
from backend.semantics.models import (
    PlannedExecution,
    ResolvedBinding,
    ResultColumn,
    SemanticQuery,
    SemanticResult,
)
from backend.semantics.planner import plan

logger = logging.getLogger(__name__)


class PolicyContext(BaseModel):
    """执行策略上下文：可信身份 + enforcer 裁决产物。

    身份字段由服务端注入（JWT/AK/报表创建者），绝不从请求体取值（护栏 §2）；
    `policy` 为 enforcer 裁决产物，Phase 6.3 的 RLS/mask 视图化强制按它注册会话视图。
    """
    model_config = ConfigDict(extra="forbid")

    user_id: int = 0
    username: str = ""
    role: str = ""
    workspace_id: int = 0
    datasource_id: int = 0
    policy: dict[str, Any] = Field(
        default_factory=dict,
        description="enforcer 裁决产物（RLS 行策略/列掩码等，Phase 6.3 视图化强制消费）",
    )


PolicyLike = Union[PolicyContext, dict[str, Any], None]


def _policy_ctx(policy_ctx: PolicyLike) -> PolicyContext:
    if policy_ctx is None:
        return PolicyContext()
    if isinstance(policy_ctx, PolicyContext):
        return policy_ctx
    return PolicyContext(**dict(policy_ctx))


def _user_context(ctx: PolicyContext) -> dict[str, Any]:
    return {
        "user_id": ctx.user_id,
        "username": ctx.username,
        "workspace_id": ctx.workspace_id,
    }


def _as_query(query: Union[SemanticQuery, dict[str, Any]], ctx: PolicyContext) -> SemanticQuery:
    """dict intent 走 parse_intent（拒 SQL 字段的关键护栏）；身份/源由 policy_ctx 权威覆盖。"""
    if isinstance(query, SemanticQuery):
        q = query
    else:
        q, err, _notes = parse_intent(dict(query or {}))
        if err:
            raise SemanticError(ErrorCode.INVALID_INTENT, err, blocked_at="intent")

    # policy_ctx 非零值权威覆盖（与 run_semantic_query 的会话口径一致）；
    # ctx 全零时保留 query 内声明值（服务内显式程序化调用）。
    update: dict[str, Any] = {}
    if ctx.user_id:
        update["user_id"] = ctx.user_id
    if ctx.workspace_id:
        update["workspace_id"] = ctx.workspace_id
    if ctx.datasource_id:
        update["datasource_id"] = ctx.datasource_id
    return q.model_copy(update=update) if update else q


def _result_column(name: str, q: SemanticQuery) -> ResultColumn:
    if name in (q.metrics or []):
        return ResultColumn(name=name, role="measure")
    if name == (q.time_column or ""):
        return ResultColumn(name=name, role="time")
    if name in (q.dimensions or []):
        return ResultColumn(name=name, role="dimension")
    return ResultColumn(name=name)


def _to_result(
    q: SemanticQuery,
    binding: ResolvedBinding,
    p: PlannedExecution,
    se: SemanticExecution,
    bind_warnings: list[str],
    elapsed_ms: int,
) -> SemanticResult:
    rr = se.result or {}
    names = [str(c) for c in (rr.get("columns") or [])]
    records = rr.get("rows") or []
    rows: list[list[Any]] = []
    for rec in records:
        if isinstance(rec, dict):
            rows.append([rec.get(n) for n in names])
        elif isinstance(rec, (list, tuple)):
            rows.append(list(rec))

    return SemanticResult(
        columns=[_result_column(n, q) for n in names],
        rows=rows,
        row_count=int(rr.get("row_count") or len(rows)),
        applied_rls=list(se.applied_rls),
        masked_columns=list(se.masked_columns),
        resolved_tables=list(p.resolved_tables),
        provenance=dict(se.provenance),
        warnings=safe_warnings(p.warnings, bind_warnings),
        elapsed_ms=int(rr.get("execution_ms") or elapsed_ms),
    )


def execute(
    query: Union[SemanticQuery, dict[str, Any]],
    policy_ctx: PolicyLike = None,
) -> SemanticResult:
    """声明式意图 → 受治理执行 → SemanticResult。

    拒绝/失败一律抛 SemanticError（code + 对外通用文案）；引擎原始错误仅进日志。
    """
    ctx = _policy_ctx(policy_ctx)
    q = _as_query(query, ctx)
    t0 = time.monotonic()

    binding, bind_warnings = resolve_binding(q.object, datasource_id=q.datasource_id)
    if binding is None:
        raise SemanticError(
            ErrorCode.UNBOUND, f"object '{q.object}' 未绑定到任何物理表", blocked_at="binding",
        )

    p = plan(q, binding)
    if not p.sql:
        raise SemanticError(
            ErrorCode.GUARDRAIL_BLOCKED, "语义层护栏拒绝了本次查询", blocked_at="preflight",
            detail={"reason": safe_warnings(p.warnings)},
        )

    se = execute_semantic(q, binding, p, _user_context(ctx), "")
    if not se.allowed:
        code = block_code(se.blocked_at)
        message = sanitize_execute_reason(se.reason, se.blocked_at)
        detail: dict[str, Any] = {}
        if se.needs_approval:
            detail["proposed_edit"] = se.proposed_edit
        raise SemanticError(
            code, message, blocked_at=se.blocked_at or "execute", detail=detail,
        )

    return _to_result(
        q, binding, p, se, bind_warnings,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )


def execute_sql(
    sql: str,
    policy_ctx: PolicyLike = None,
) -> SemanticResult:
    """受治理 SQL 执行口（nl2sql/Playground/raw_sql 等 SQL 形状的唯一出口）。

    必经 `execute_query_with_permission`（护栏 §1）：权限拒绝在执行前中止，
    序列化经 `df_to_columns_rows` 无损消歧（护栏 §8）。
    """
    ctx = _policy_ctx(policy_ctx)
    t0 = time.monotonic()
    # 函数内经模块取（与 gates 同风格）：护栏测试可对 query_executor 模块属性打桩
    from backend.core import query_executor

    try:
        df, exec_ms, row_count = query_executor.execute_query_with_permission(
            sql,
            datasource_id=ctx.datasource_id or None,
            user_context={"user_id": ctx.user_id, "username": ctx.username},
            workspace_id=ctx.workspace_id,
        )
    except PermissionError as e:
        raise SemanticError(ErrorCode.FORBIDDEN, str(e), blocked_at="permission") from e
    except Exception as e:  # noqa: BLE001 — 引擎错误对外通用文案（护栏 §7）
        logger.error("[semantics.execute] execute_sql failed (server-side only): %s", e)
        raise SemanticError(ErrorCode.EXEC_FAILED, EXEC_FAIL_HINT, blocked_at="execute") from e

    names, records = df_to_columns_rows(df)
    rows = [[rec.get(n) for n in names] for rec in records]
    return SemanticResult(
        columns=[ResultColumn(name=n) for n in names],
        rows=rows,
        row_count=int(row_count or len(rows)),
        elapsed_ms=int(exec_ms or ((time.monotonic() - t0) * 1000)),
    )
