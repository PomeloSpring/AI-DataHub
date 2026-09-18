"""Seven-gate execution pipeline (Phase 4).

语义层 -> 执行层的确定性"闸门链"。LLM 只产 Intent, 这里把 Intent->binding->plan
结果过七道闸门后再执行, 任一道拒绝即返回结构化拒绝(不静默、可审计):

  1. identity   身份      —— 非 dry_run 执行需有请求者身份(JWT ctx / user_id)
  2. permission 权限      —— 数据源/表级访问 + permission_token 校验(role_service)
  3. preflight  前置校验  —— 对象已绑定(sync_state)、指标/维度已解析、binding 未漂移成孤儿
  4. proposal   提案      —— 高风险(raw_source 大表 / 无过滤 / 写回)生成 ProposedEdit 预览
  5. approval   审批      —— 复用工作空间角色; 高风险且未批准则挂起(返回 needs_approval)
  6. execute    执行      —— RLS sqlglot 改写(语义层唯一可见改写) + DataFusion 二次校验
  7. audit      审计      —— 落 adh_query_audit(成功/拒绝/错误均留痕)

RLS 单一事实源见 rls.py; 本模块只负责编排与"所见即所执行"的 securedSql 产出。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from services.shared.semantics import rls
from services.shared.semantics.models import (
    PlannedExecution, ResolvedBinding, SemanticQuery,
)

logger = logging.getLogger(__name__)

# 触发"提案 + 审批"的风险档位
_RISKY_SIZE = {"huge"}
_RISKY_MODE = {"raw_source"}


@dataclass
class GateOutcome:
    gate: str
    passed: bool
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"gate": self.gate, "passed": self.passed, "reason": self.reason,
                "detail": self.detail}


@dataclass
class SemanticExecution:
    """闸门链产物。allowed=False 时 sql/rows 为空, 带拒绝 gate 与 trail。"""
    allowed: bool
    blocked_at: str = ""
    reason: str = ""
    needs_approval: bool = False
    proposed_edit: Optional[dict[str, Any]] = None
    base_sql: str = ""
    secured_sql: str = ""
    applied_rls: list[str] = field(default_factory=list)
    masked_columns: list[str] = field(default_factory=list)
    gate_trail: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    result: Optional[dict[str, Any]] = None


def _is_admin(user_id: int, workspace_id: int) -> bool:
    try:
        from services.authservice.services.role_service import role_service
        roles = role_service.get_user_roles(user_id, workspace_id) or []
        return any((r.get("name") == "admin") for r in roles)
    except Exception as e:  # pragma: no cover — role 服务异常时按非 admin 处理
        logger.debug("[gates] role lookup failed (treat non-admin): %s", e)
        return False


# ── gate 1..5 (纯判定, 无副作用) ────────────────────────────────

def _gate_identity(q: SemanticQuery, user_context: dict) -> GateOutcome:
    uid = (user_context or {}).get("user_id")
    if q.dry_run:
        return GateOutcome("identity", True, "dry_run 免身份")
    if not uid:
        return GateOutcome("identity", False, "缺少请求者身份(user_id), 拒绝执行真实查询")
    return GateOutcome("identity", True, detail={"user_id": uid})


def _blocked_column_guard(
    q: SemanticQuery, binding: ResolvedBinding, planned: PlannedExecution,
    workspace_id: int,
) -> Optional[str]:
    """合规屏蔽列(block=不能查询出来)显式请求即拒绝。对 admin 同样生效。

    两道检测(仅限普通表绑定; sql_template 由建模人员注册, 屏蔽列交由执行层结果剔除):
      1. intent 点名: 维度/指标/过滤/排序/时间列 解析到物理列命中 block 列;
      2. SQL 文本兜底: 编译后的 SQL 词边界命中 block 列(覆盖间接聚合泄露如 AVG(salary))。
    命中返回拒绝文案, 否则 None。屏蔽基线加载失败时不在此报错(执行层仍有同底座丢弃)。
    """
    if binding.bind_kind == "sql_template" or not binding.physical_table:
        return None
    try:
        from services.datamind.permission.enforcer import permission_enforcer
        blocked = permission_enforcer.get_blocked_columns(
            workspace_id, binding.datasource_id, binding.physical_table)
    except Exception as e:  # noqa: BLE001
        logger.warning("[gates] blocked-column lookup failed (由执行层兜底): %s", e)
        return None
    if not blocked:
        return None

    # 1) intent 请求名 -> 物理列/表达式, 任一命中 block 列即拒
    requested: list[str] = (
        list(q.dimensions) + list(q.metrics)
        + [f.dim for f in q.filters] + [o.by for o in q.order]
    )
    if q.time_column:
        requested.append(q.time_column)
    tokens: list[str] = list(requested)
    try:
        from services.shared.semantics.planner import _ColumnResolver
        resolver = _ColumnResolver(binding, dialect=planned.dialect)
        for name in requested:
            expr, _role = resolver.resolve_dimension(name)
            if expr:
                tokens.append(expr)
    except Exception as e:  # noqa: BLE001 — 解析降级仅靠文本/名匹配
        logger.debug("[gates] blocked-column intent resolve degraded: %s", e)
    hay = " \n ".join(str(t) for t in tokens).lower()
    for col in blocked:
        if re.search(rf"\b{re.escape(str(col).lower())}\b", hay):
            return f"字段 '{col}' 为敏感合规屏蔽列, 不允许查询"

    # 2) SQL 文本兜底
    try:
        hits = permission_enforcer._references_blocked(planned.sql, blocked)
    except Exception:  # noqa: BLE001
        hits = []
    if hits:
        return f"字段 '{hits[0]}' 为敏感合规屏蔽列, 不允许查询"
    return None


def _gate_permission(
    q: SemanticQuery, binding: ResolvedBinding, user_context: dict,
    planned: PlannedExecution | None = None,
) -> GateOutcome:
    uid = int((user_context or {}).get("user_id") or 0)
    ws = int(q.workspace_id or (user_context or {}).get("workspace_id") or 0)
    toks = list(binding.access.permission_tokens or [])
    try:
        from services.datamind.permission.enforcer import permission_enforcer
        res = permission_enforcer.check_access(
            user_id=uid, workspace_id=ws,
            datasource_id=binding.datasource_id, table_name=binding.physical_table,
        )
        if not res.allowed:
            return GateOutcome("permission", False, res.reason or "无权访问该对象底表")
    except PermissionError as e:
        return GateOutcome("permission", False, str(e))
    except Exception as e:  # 服务异常不放宽: 记录但放行到执行层二次校验
        logger.warning("[gates] permission check degraded: %s", e)
        return GateOutcome("permission", True, reason=f"权限服务降级: {e}",
                           detail={"degraded": True, "required_tokens": toks})

    # 合规屏蔽列(block): 显式请求即拒绝(L2 护城河, 对 admin 同样生效)
    if planned is not None:
        block_msg = _blocked_column_guard(q, binding, planned, ws)
        if block_msg:
            return GateOutcome("permission", False, block_msg,
                               detail={"blocked_by_policy": True})

    return GateOutcome("permission", True, detail={"required_tokens": toks})


def _gate_preflight(q: SemanticQuery, binding: ResolvedBinding, planned: PlannedExecution) -> GateOutcome:
    if binding.sync_state == "orphaned":
        return GateOutcome("preflight", False, f"对象 '{q.object}' 绑定已孤立(orphaned), 请先修复绑定")
    if binding.sync_state == "unbound":
        return GateOutcome("preflight", False, f"对象 '{q.object}' 未绑定物理表")
    # 编译期已判定"全部指标/维度无法解析" -> 视为前置失败(兼容中/英文解析告警)
    unresolved = [w for w in planned.warnings
                  if w.startswith(("unresolved", "无法解析"))]
    if planned.sql == "" :
        return GateOutcome("preflight", False, "护栏拒绝了本次查询(见 warnings)",
                           detail={"warnings": planned.warnings})
    if unresolved and not planned.provenance.get("requested_metrics") and not q.dimensions:
        return GateOutcome("preflight", False, "无有效字段可查询", detail={"unresolved": unresolved})
    detail: dict[str, Any] = {"sync_state": binding.sync_state}
    if unresolved:
        detail["unresolved"] = unresolved
        avail = planned.provenance.get("available_dimensions")
        if avail:
            detail["available_dimensions"] = avail
    return GateOutcome("preflight", True, detail=detail)


def _risk_profile(q: SemanticQuery, binding: ResolvedBinding) -> tuple[bool, list[str]]:
    g = binding.guardrail
    reasons: list[str] = []
    if g.size_class in _RISKY_SIZE:
        reasons.append(f"size_class={g.size_class}")
    if g.query_mode in _RISKY_MODE:
        reasons.append(f"query_mode={g.query_mode}")
    if g.query_mode == "raw_source" and not q.filters:
        reasons.append("raw_source 无过滤谓词")
    return (len(reasons) > 0 and g.size_class in _RISKY_SIZE), reasons


def _gate_proposal(q: SemanticQuery, binding: ResolvedBinding, planned: PlannedExecution) -> GateOutcome:
    risky, reasons = _risk_profile(q, binding)
    if not risky:
        return GateOutcome("proposal", True, "常规风险")
    proposed = {
        "kind": "semantic_query",
        "object": q.object,
        "datasource_id": binding.datasource_id,
        "catalog_ref": binding.catalog_ref,
        "route": planned.route,
        "risk_reasons": reasons,
        "base_sql": planned.sql,
        "requested_limit": q.limit,
        "guardrail": g_dump(binding),
    }
    return GateOutcome("proposal", True, "高风险 -> 生成 ProposedEdit",
                       detail={"proposed_edit": proposed})


def _gate_approval(q: SemanticQuery, binding: ResolvedBinding, user_context: dict,
                   proposed: Optional[dict]) -> GateOutcome:
    if proposed is None:
        return GateOutcome("approval", True, "无需审批")
    uid = int((user_context or {}).get("user_id") or 0)
    ws = int(q.workspace_id or (user_context or {}).get("workspace_id") or 0)
    if q.dry_run or _is_admin(uid, ws):
        return GateOutcome("approval", True, "管理员/预演直接批准")
    return GateOutcome("approval", False, "高风险查询需工作空间审批后执行",
                       detail={"proposed_edit": proposed})


def g_dump(binding: ResolvedBinding) -> dict[str, Any]:
    try:
        return binding.guardrail.model_dump()
    except Exception:  # pragma: no cover
        return {}


# ── 编排:闸门链 ────────────────────────────────────────────────

def run_gates(
    q: SemanticQuery,
    binding: ResolvedBinding,
    planned: PlannedExecution,
    user_context: dict | None = None,
) -> SemanticExecution:
    """跑 gate 1..5(纯判定), 产出 securedSql(gate 6 的改写部分)。

    不在此执行真实查询; 由 execute_semantic() 在放行后调执行层。
    """
    user_context = user_context or {}
    trail: list[GateOutcome] = []
    provenance = dict(planned.provenance)

    def _block(o: GateOutcome) -> SemanticExecution:
        return SemanticExecution(
            allowed=False, blocked_at=o.gate, reason=o.reason,
            base_sql=planned.sql, gate_trail=[t.as_dict() for t in trail],
            provenance=provenance, warnings=list(planned.warnings),
        )

    for o in (
        _gate_identity(q, user_context),
        _gate_permission(q, binding, user_context, planned),
        _gate_preflight(q, binding, planned),
    ):
        trail.append(o)
        if not o.passed:
            return _block(o)

    # gate 4: 提案
    prop = _gate_proposal(q, binding, planned)
    trail.append(prop)
    proposed = prop.detail.get("proposed_edit")

    # gate 5: 审批
    app = _gate_approval(q, binding, user_context, proposed)
    trail.append(app)
    if not app.passed:
        return SemanticExecution(
            allowed=False, blocked_at="approval", reason=app.reason,
            needs_approval=True, proposed_edit=proposed,
            base_sql=planned.sql, gate_trail=[t.as_dict() for t in trail],
            provenance=provenance, warnings=list(planned.warnings),
        )

    # gate 6 (改写部分): 语义层 RLS sqlglot 改写 -> securedSql
    uid = int(user_context.get("user_id") or q.user_id or 0)
    ws = int(user_context.get("workspace_id") or q.workspace_id or 0)
    secured = planned.sql
    applied_rls: list[str] = []
    masked: list[str] = []
    if q.user_id or uid:
        try:
            table_filters, col_restr, policies = rls.resolve_table_filters(
                user_id=uid or q.user_id, workspace_id=ws,
                datasource_id=binding.datasource_id, tables=planned.resolved_tables,
            )
            secured, applied, err = rls.apply_rls(planned.sql, table_filters, dialect=planned.dialect)
            if err:
                o = GateOutcome("execute", False, f"RLS 改写失败, 拒绝执行未过滤查询: {err}")
                trail.append(o)
                return SemanticExecution(
                    allowed=False, blocked_at="rls", reason=o.reason,
                    base_sql=planned.sql, gate_trail=[t.as_dict() for t in trail],
                    provenance=provenance, warnings=list(planned.warnings),
                )
            applied_rls = applied + list(policies)
            masked = rls.masked_columns(col_restr)
            provenance["rls_applied_tables"] = applied
            provenance["rls_policies"] = policies
        except Exception as e:  # RLS 解析异常 -> 保守拒绝执行真实查询(dry_run 除外)
            logger.warning("[gates] RLS resolve failed: %s", e)
            if not q.dry_run:
                o = GateOutcome("execute", False, f"RLS 解析异常, 拒绝执行: {e}")
                trail.append(o)
                return SemanticExecution(
                    allowed=False, blocked_at="rls", reason=o.reason,
                    base_sql=planned.sql, gate_trail=[t.as_dict() for t in trail],
                    provenance=provenance, warnings=list(planned.warnings),
                )

    provenance["gate_trail"] = [t.gate for t in trail]
    return SemanticExecution(
        allowed=True, base_sql=planned.sql, secured_sql=secured,
        applied_rls=applied_rls, masked_columns=masked,
        gate_trail=[t.as_dict() for t in trail],
        proposed_edit=proposed, provenance=provenance,
        warnings=list(planned.warnings),
    )


# ── gate 6(执行) + gate 7(审计) ───────────────────────────────

def execute_semantic(
    q: SemanticQuery,
    binding: ResolvedBinding,
    planned: PlannedExecution,
    user_context: dict | None = None,
    question: str = "",
) -> SemanticExecution:
    """完整链路:闸门 -> 真实执行(若允许且非 dry_run) -> 审计。

    执行复用 execute_query_with_permission(带 DataFusion RLS 二次校验 + 审计旁路)。
    返回的 SemanticExecution.rows/_meta 由调用方读取(见 .result 属性)。
    """
    exec_res = run_gates(q, binding, planned, user_context=user_context)
    if not exec_res.allowed:
        _audit(q, user_context, question, exec_res.secured_sql or exec_res.base_sql,
               status="denied", error=exec_res.reason)
        return exec_res

    if q.dry_run:
        exec_res.result = {"rows": [], "row_count": 0, "columns": [], "note": "dry_run"}
        return exec_res

    try:
        import pandas as pd  # noqa: F401  (执行层依赖)
        from services.datamind.nl2sql.sql.query_executor import execute_query_with_permission
        df, exec_ms, row_count = execute_query_with_permission(
            exec_res.secured_sql or exec_res.base_sql,
            binding.datasource_id or q.datasource_id or None,
            "sql", user_context or {}, int(q.workspace_id or (user_context or {}).get("workspace_id") or 0),
        )
        exec_res.result = {
            "columns": list(df.columns),
            "rows": _df_records(df.head(5000)),
            "row_count": int(row_count),
            "execution_ms": int(exec_ms),
        }
        _audit(q, user_context, question, exec_res.secured_sql or exec_res.base_sql,
               status="success", row_count=row_count, time_ms=exec_ms)
    except Exception as e:
        exec_res.allowed = False
        exec_res.blocked_at = "execute"
        exec_res.reason = f"执行失败: {e}"
        _audit(q, user_context, question, exec_res.secured_sql or exec_res.base_sql,
               status="error", error=str(e))
    return exec_res


def _df_records(df):
    try:
        import json
        return json.loads(df.to_json(orient="records", force_ascii=False))
    except Exception:
        return []


def _audit(q: SemanticQuery, user_context: dict | None, question: str, sql: str,
           status: str, row_count: int = 0, time_ms: int = 0, error: str = "") -> None:
    try:
        from services.datamind.nl2sql.sql.query_executor import log_audit
        uc = user_context or {}
        log_audit(
            user_id=int(uc.get("user_id") or q.user_id or 0),
            username=str(uc.get("username") or ""),
            role=str(uc.get("role") or "user"),
            question=question or f"[semantic] {q.object}",
            sql=sql or "", status=status, row_count=row_count,
            time_ms=time_ms, error=error, datasource_id=q.datasource_id,
            query_type="semantic",
        )
    except Exception as e:  # 审计不得阻断主流程
        logger.warning("[gates] audit failed: %s", e)
