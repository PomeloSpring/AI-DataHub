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
- 引擎原始错误仅进服务端日志，对外回 `contract.classify_exec_failure` 分过类的
  可操作文案（护栏 §7）：**不得把确定性的方言/缺表失败笼统说成“连接抖动、稍后
  重试”**（历史缺陷：会话 1783069459277，1064 在此处被换成 EXEC_FAIL_HINT 后下发，
  上层拿到的已是遮掩后的文案，再怎么分类也分不出来）；
  结果序列化必经 `df_to_columns_rows` 无损消歧（护栏 §8）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.common.df_serialize import df_to_columns_rows
from backend.semantics.binding_resolver import lookup_db_type, resolve_binding
from backend.semantics.contract import (
    ErrorCode,
    SemanticError,
    block_code,
    classify_exec_failure,
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


def _shadow_check(sql: str, *, tag: str, columns: list[str], records_fn, **kwargs: Any) -> None:
    """shadow 双跑对拍钩子（Phase 7.1，`SEMANTIC_ENGINE_SHADOW` 开启时生效）。

    fail-safe：任何异常（含 records 构造）不影响主链路返回；对拍差异在
    `execution.shadow` 内结构化落日志。
    """
    try:
        from backend.semantics.execution import shadow

        if not shadow.SHADOW_ENABLED:
            return
        shadow.shadow_check(sql, main_columns=columns, main_rows=records_fn(), tag=tag, **kwargs)
    except Exception:  # noqa: BLE001 — 对拍钩子绝不影响主链路
        logger.debug("[semantics.execute] shadow check skipped", exc_info=True)


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
    # 本轮用户问题：仅服务端注入，只用于落 adh_query_audit 的 question 列（护栏 §9），
    # 不参与身份/权限裁决，也绝不下发给 LLM/前端。
    question: str = ""
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


def _sql_audit(ctx: PolicyContext, sql: str, *, status: str, row_count: int = 0,
               time_ms: int = 0, error: str = "") -> None:
    """SQL 形状取数的查询审计落库（护栏 §9：成功与拒绝均须留痕）。

    历史缺陷：`adh_query_audit` 只有语义路经 `gates._audit` 写入，`execute_sql`
    （nl2sql / Playground / 看板 raw_sql）一条不落 —— 管理员在「查询历史」页只看到
    语义路的语句，无法核对实际执行的是哪张表，直接制造了“AI 说查 A 表、审计里只有
    B 表”的无从对证（会话 1783069459151 的争议根源）。

    口径：
    - 仅真实用户调用落审计行（`user_id=0` 的系统/内部调用与
      `query_executor._log_permission_audit` 同口径不落）；
    - 审计是**服务端留痕**，不构成对 LLM/前端的 SQL 下发（护栏 §7 不变）；
    - 审计写失败不阻断取数主链路，但必须可诊断（warning 日志带错因）。
    """
    if int(ctx.user_id or 0) <= 0:
        return
    try:
        from backend.core.query_executor import log_audit
        log_audit(
            user_id=int(ctx.user_id),
            username=ctx.username or "",
            role=ctx.role or "user",
            question=ctx.question or "[raw_sql] 无问题上下文",
            sql=sql or "",
            status=status,
            row_count=int(row_count or 0),
            time_ms=int(time_ms or 0),
            error=error,
            datasource_id=int(ctx.datasource_id or 0),
            query_type="sql",
        )
    except Exception as e:  # noqa: BLE001 — 审计旁路不得影响取数主链路（FDE no-op 纪律）
        logger.warning("[semantics.execute] sql audit write failed (status=%s): %s", status, e)


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


def apply_output_header(result: dict[str, Any], header_map: dict[str, str]) -> dict[str, Any]:
    """把 SLS/Presto 的 ASCII 槽位列名（o0/o1…）映回业务名（不就地改原 dict）。

    必须是单一口径：`_to_result` 以 list 行重新投影，而报告/定时分析等非 Agent 入口直接
    消费 `SemanticExecution.result`（dict 行）。旧写法让同一份编译产物在 Chat 里显示
    「省份」、在报告正文里印成「o0」——给用户看的是错的东西。
    非 SLS 时 header_map 为空 → 行为零变化。
    """
    if not header_map:
        return result
    out = dict(result or {})
    out["columns"] = [str(header_map.get(str(c), str(c))) for c in (out.get("columns") or [])]
    rows: list[Any] = []
    for rec in out.get("rows") or []:
        if isinstance(rec, dict):
            rows.append({header_map.get(str(k), str(k)): v for k, v in rec.items()})
        else:
            rows.append(rec)
    out["rows"] = rows
    return out


def _to_result(
    q: SemanticQuery,
    binding: ResolvedBinding,
    p: PlannedExecution,
    se: SemanticExecution,
    bind_warnings: list[str],
    elapsed_ms: int,
) -> SemanticResult:
    rr = se.result or {}
    raw_names = [str(c) for c in (rr.get("columns") or [])]
    # SLS/Presto 下 SQL 别名为 ASCII 槽位(o0..)，映回请求的业务名后作为对外列名与角色标注依据；
    # 非 SLS output_header_map 为空 -> disp==raw，行为不变。取数仍按 raw_names 从 dict 行取值。
    hmap = p.output_header_map or {}
    disp_names = [hmap.get(n, n) for n in raw_names]
    records = rr.get("rows") or []
    rows: list[list[Any]] = []
    for rec in records:
        if isinstance(rec, dict):
            rows.append([rec.get(n) for n in raw_names])
        elif isinstance(rec, (list, tuple)):
            rows.append(list(rec))

    return SemanticResult(
        columns=[_result_column(n, q) for n in disp_names],
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

    return execute_plan(q, binding, p, ctx, "", bind_warnings=bind_warnings)


def execute_plan(
    q: SemanticQuery,
    binding: ResolvedBinding,
    p: PlannedExecution,
    policy_ctx: PolicyLike = None,
    question: str = "",
    *,
    bind_warnings: Optional[list[str]] = None,
) -> SemanticResult:
    """已解析绑定/计划的**执行段**（七闸门 + 执行 + 审计）→ SemanticResult。

    供需要在 bind/plan 间插入自身逻辑（资源护栏/别名回流等 L2 职责）的调用方
    （如 run_semantic_query）复用同一执行口；拒绝/失败抛 SemanticError
    （detail 含 applied_rls/masked_columns/needs_approval 等服务内消费产物，
    不入对外投影）。
    """
    ctx = _policy_ctx(policy_ctx)
    t0 = time.monotonic()
    se = execute_semantic(q, binding, p, _user_context(ctx), question)
    if not se.allowed:
        code = block_code(se.blocked_at)
        # db_type 取自已解析的绑定（方言已知，不必再回元库）：让“目标源类型”提示只对
        # 真查到的情形出现（宁缺勿错）。
        db_type = str(getattr(binding, "db_type", "") or "")
        message = sanitize_execute_reason(se.reason, se.blocked_at, db_type)
        detail: dict[str, Any] = {
            "applied_rls": list(se.applied_rls),
            "masked_columns": list(se.masked_columns),
            "needs_approval": bool(se.needs_approval),
        }
        if (se.blocked_at or "") == "execute":
            # 类别一并放进 detail（与下面 execute_sql 分支同口径）：`message` 已是中文文案，
            # 下游（看板/报表/数据集）拿它再分类必失配。这里多跑一次纯函数分类，
            # 就为把错因类别交出去，不再把确定性缺表失败掩成“稍后重试”。
            detail["failure_category"] = classify_exec_failure(str(se.reason or ""), db_type)[0]
        if se.needs_approval and se.proposed_edit:
            detail["proposed_edit"] = se.proposed_edit
        raise SemanticError(
            code, message, blocked_at=se.blocked_at or "execute", detail=detail,
        )

    result = _to_result(
        q, binding, p, se, list(bind_warnings or []),
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )
    res_names = [c.name for c in result.columns]
    _shadow_check(
        p.secured_sql or p.sql, tag="execute_plan",
        columns=res_names,
        records_fn=lambda: [dict(zip(res_names, row)) for row in result.rows],
        datasource_id=binding.datasource_id or q.datasource_id,
        db_type=binding.db_type, guardrail=p.guardrail,
    )
    return result


def execute_sql(
    sql: str,
    policy_ctx: PolicyLike = None,
) -> SemanticResult:
    """受治理 SQL 执行口（nl2sql/Playground/raw_sql 等 SQL 形状的唯一出口）。

    必经 `execute_query_with_permission`（护栏 §1）：权限拒绝在执行前中止，
    序列化经 `df_to_columns_rows` 无损消歧（护栏 §8），成功/拒绝/失败均落查询
    审计（护栏 §9，`_sql_audit`）。
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
        _sql_audit(ctx, sql, status="denied", error=str(e),
                   time_ms=int((time.monotonic() - t0) * 1000))
        raise SemanticError(ErrorCode.FORBIDDEN, str(e), blocked_at="permission") from e
    except Exception as e:  # noqa: BLE001 — 引擎错误按错因分类（护栏 §7）
        logger.error("[semantics.execute] execute_sql failed (server-side only): %s", e)
        # 对内审计留真实错因（否则失败行无因可查）
        _sql_audit(ctx, sql, status="error", error=str(e)[:1000],
                   time_ms=int((time.monotonic() - t0) * 1000))
        # 分类必须在**原始异常还在手上**的时候做：旧写法直接抛 EXEC_FAIL_HINT，把
        # 1064（方言不符）与 2002（连不上）归为同一句“系统侧问题，建议稍后重试”，
        # 模型据此向用户报“数据源执行阶段连接失败”——重试永远复现的缺陷被当成抖动。
        # 原始细节仍只进日志/审计（message 永远不含它，护栏 §7）。
        category, hint = classify_exec_failure(str(e), lookup_db_type(int(ctx.datasource_id or 0)))
        raise SemanticError(ErrorCode.EXEC_FAILED, hint, blocked_at="execute",
                            detail={"failure_category": category}) from e

    names, records = df_to_columns_rows(df)
    rows = [[rec.get(n) for n in names] for rec in records]
    _shadow_check(sql, tag="execute_sql", columns=names, records_fn=lambda: records,
                  datasource_id=ctx.datasource_id)
    total = int(row_count or len(rows))
    _sql_audit(ctx, sql, status="success", row_count=total,
               time_ms=int(exec_ms or ((time.monotonic() - t0) * 1000)))
    return SemanticResult(
        columns=[ResultColumn(name=n) for n in names],
        rows=rows,
        row_count=total,
        elapsed_ms=int(exec_ms or ((time.monotonic() - t0) * 1000)),
    )
