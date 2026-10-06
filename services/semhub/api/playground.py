"""SQL Playground (Phase 6) — 语义层/中台: AST / RLS-diff / EXPLAIN / 溯源。

归属决策(见 plan Phase 6): Playground 归语义层, `datamind/api/playground.py`
降级为薄代理转发到这里, datamind 只留 ChatBI 对话。

单一 RLS 事实源: Playground 展示的 `securedSql` 与实跑的 `securedSql` 由同一段
语义层改写 (`shared/semantics/rls`) 产出 —— 所见即所执行;绝不在别处重解语义。

能力(仅静态/预览分析, 不返回任何数据行 — 本项目不是数据库连接工具):
  ① POST /ast        : sqlglot.parse_one -> JSON AST + 引用表 + 列谓词
  ② POST /lineage    : sqlglot.lineage 列级血缘
  ③ POST /rls-diff   : baseSql vs securedSql 行级+列级改写 diff (身份取自可信内部头)
  ④ POST /provenance : object -> binding / query_mode / size_class 溯源面板

注: 会返回数据行的 /execute 与实连数据源的 /explain 已移除 —— 取数只能走治理路径
(统一语义层 run_semantic_query / execute_query_with_permission), 护城河不可绕过。
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from services.shared.semantics import rls
from services.shared.semantics.binding_resolver import resolve_binding
from services.shared.semantics.models import SemanticQuery

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/semantic/playground", tags=["semantic-playground"])

try:  # sqlglot 是 Phase4+ 硬依赖; 缺失时相关端点显式降级(不 500)
    import sqlglot
    from sqlglot import exp

    _HAS_SQLGLOT = True
except Exception:  # pragma: no cover
    _HAS_SQLGLOT = False

# 只读语句前缀(Playground 护栏)
_READ_ONLY = re.compile(r"^\s*(SELECT|WITH|SHOW|DESC|DESCRIBE|EXPLAIN)\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)


# ── helpers ────────────────────────────────────────────────────

def _sqlglot_or_503() -> None:
    if not _HAS_SQLGLOT:  # pragma: no cover
        raise HTTPException(status_code=503, detail="sqlglot unavailable on semantic layer")


def _require_sql(body: dict[str, Any]) -> str:
    sql = (body.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL cannot be empty")
    return sql


def _as_int(value: Any, field: str) -> int:
    """数值型 body 字段安全转换; 非法值 -> 干净 400(避免未捕获 ValueError 裸 500)."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} 必须是数字")


def _assert_read_only(sql: str) -> str:
    if not _READ_ONLY.match(sql):
        raise HTTPException(status_code=400, detail="Only SELECT/SHOW/DESC/WITH/EXPLAIN queries allowed")
    return sql


def _referenced_tables(sql: str, dialect: str = "mysql") -> list[str]:
    """从 SQL 抽取被引用的物理表名(去重, 保持出现顺序)。解析失败返回空。"""
    if not _HAS_SQLGLOT:
        return []
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:  # noqa: BLE001
        logger.debug("[playground] parse for tables failed: %s", e)
        return []
    seen: list[str] = []
    for node in tree.find_all(exp.Table):
        name = node.name
        if name and name not in seen:
            seen.append(name)
    return seen


def _table_guardrail(datasource_id: Optional[int], table: str) -> dict[str, Any]:
    """读 adh_table_info 的 size_class / allow_full_scan / query_mode(缺失即空)。"""
    from services.shared.common.db.metadata_db import get_metadata_conn

    out: dict[str, Any] = {}
    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                sql = (
                    "SELECT size_class, allow_full_scan, query_mode, est_rows "
                    "FROM adh_table_info WHERE table_name = %s"
                )
                params: list[Any] = [table]
                if datasource_id:
                    sql += " AND datasource_id = %s"
                    params.append(datasource_id)
                cur.execute(sql + " LIMIT 1", params)
                row = cur.fetchone()
                if row:
                    out = dict(row)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — 元数据缺列/不可达不阻断, 按无护栏处理
        logger.debug("[playground] guardrail lookup failed for %s: %s", table, e)
    return out


def _guardrail_check(sql: str, datasource_id: Optional[int], tables: list[str]) -> list[str]:
    """huge 且未开全表扫 -> 必须带 LIMIT, 否则拒绝。返回拒绝原因(空=通过)。"""
    reasons: list[str] = []
    has_limit = bool(_LIMIT_RE.search(sql))
    for t in tables:
        g = _table_guardrail(datasource_id, t)
        if str(g.get("size_class") or "").lower() == "huge" and not bool(g.get("allow_full_scan", 1)):
            if not has_limit:
                reasons.append(
                    f"huge 表 '{t}' 未开全表扫描(allow_full_scan=0), 请加 LIMIT 或过滤谓词后再执行"
                )
    return reasons


def _open_datasource(ds_id: int):
    from services.shared.common.db import get_datasource_by_id, get_datasource_conn

    row = get_datasource_by_id(ds_id)
    if not row:
        raise HTTPException(status_code=404, detail="Datasource not found")
    db_type = row.get("db_type", "mysql")
    conn = get_datasource_conn(
        db_type, row["host"], row["port"], row.get("username") or "",
        row.get("password") or "", row.get("database_name") or None,
    )
    return conn, db_type


def _fetch_table_columns(ds_id: Optional[int], table: str) -> Optional[list[str]]:
    """表列清单: 优先实连数据源 information_schema(mysql/doris), 兜底元数据库同步快照。"""
    if ds_id:
        try:
            conn, db_type = _open_datasource(ds_id)
            try:
                if db_type in ("mysql", "doris"):
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                            "ORDER BY ORDINAL_POSITION", (table,),
                        )
                        cols = [list(r.values())[0] for r in (cur.fetchall() or [])]
                    if cols:
                        return cols
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            logger.warning("[playground] schema fetch from ds=%s failed: %s", ds_id, e)
    try:
        from services.shared.common.db import execute_query
        rows = execute_query(
            "SELECT column_name FROM adh_column_metadata "
            "WHERE LOWER(table_name) = %s AND is_active = 1 "
            "ORDER BY (datasource_id = %s) DESC, id", (table, ds_id or 0),
        ) or []
        seen: set[str] = set()
        cols: list[str] = []
        for r in rows:
            c = str(r.get("column_name") or "")
            if c and c.lower() not in seen:
                seen.add(c.lower())
                cols.append(c)
        return cols or None
    except Exception as e:  # noqa: BLE001
        logger.warning("[playground] metadata column fallback failed for %s: %s", table, e)
        return None


def _make_column_fetcher(ds_id: Optional[int]):
    cache: dict[str, Optional[list[str]]] = {}

    def get_columns(table: str) -> Optional[list[str]]:
        key = (table or "").lower()
        if not key:
            return None
        if key not in cache:
            cache[key] = _fetch_table_columns(ds_id, key)
        return cache[key]

    return get_columns


def _resolve_rls(sql: str, datasource_id: Optional[int], user_id: int, workspace_id: int,
                 dialect: str = "mysql") -> tuple[str, list[str], list[str], dict[str, Any]]:
    """baseSql -> securedSql (语义层唯一可见改写)。无 user 上下文则不改写。

    行级: apply_rls 表包裹谓词; 列级: apply_column_restriction 展开 */剔除隐藏列/掩码替换
    (与 permission_enforcer 结果侧语义一致)。两步共用同一策略事实源。

    Returns (secured_sql, applied_tables, policies, column_restrictions)。
    """
    if not user_id:
        return sql, [], [], {}
    tables = _referenced_tables(sql, dialect)
    if not tables:
        return sql, [], [], {}
    try:
        table_filters, col_restr, policies = rls.resolve_table_filters(
            user_id=user_id, workspace_id=workspace_id,
            datasource_id=datasource_id or 0, tables=tables,
        )
    except Exception as e:  # noqa: BLE001 — 策略解析异常保守不改写, 上层标注
        logger.warning("[playground] RLS resolve failed: %s", e)
        return sql, [], [f"rls_resolve_error: {e}"], {}
    col_summary = {
        t: {"hidden": list((spec or {}).get("hidden") or []),
            "masked": dict((spec or {}).get("masked") or {})}
        for t, spec in (col_restr or {}).items()
        if (spec or {}).get("hidden") or (spec or {}).get("masked")
    }
    secured, applied, err = rls.apply_rls(sql, table_filters, dialect=dialect)
    if err:
        # 改写失败绝不静默回退未过滤 SQL: 标注 error, execute 会拒绝真实执行
        return "", [], [f"rls_rewrite_error: {err}"], col_summary
    secured, col_applied, cerr = rls.apply_column_restriction(
        secured, col_restr, _make_column_fetcher(datasource_id), dialect=dialect,
    )
    if cerr:
        return "", [], [f"column_restriction_error: {cerr}"], col_summary
    applied = list(dict.fromkeys(applied + col_applied))
    return secured, applied, list(policies), col_summary


# ── ② ast ──────────────────────────────────────────────────────

@router.post("/ast", summary="sqlglot parse -> JSON AST + referenced tables + column predicates")
def ast(body: dict[str, Any]) -> dict[str, Any]:
    _sqlglot_or_503()
    sql = _require_sql(body)
    dialect = body.get("dialect") or "mysql"
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"error": f"parse failed: {e}"})
    return {
        "sql": tree.sql(dialect=dialect),
        "ast": _ast_json(tree),
        "tables": _referenced_tables(sql, dialect),
        "command": (tree.key if hasattr(tree, "key") else type(tree).__name__),
    }


def _ast_json(tree) -> Any:
    """sqlglot AST -> JSON 可序列化结构(dump 优先, 回退 json())。"""
    try:
        return tree.dump()
    except Exception:  # noqa: BLE001  # pragma: no cover
        import json
        try:
            return json.loads(tree.json())
        except Exception:  # noqa: BLE001
            return {"error": "ast serialization failed"}


# ── ②b lineage ─────────────────────────────────────────────────

def _node_to_dict(node) -> dict[str, Any]:
    ds = getattr(node, "dataset", None) or getattr(node, "source", "")
    return {
        "name": getattr(node, "name", ""),
        "expression": str(getattr(node, "expression", "")),
        "dataset": str(ds),
        "kind": type(node).__name__,
        "downstream": [_node_to_dict(c) for c in (getattr(node, "downstream", []) or [])],
    }


@router.post("/lineage", summary="column-level lineage via sqlglot.lineage")
def lineage(body: dict[str, Any]) -> dict[str, Any]:
    _sqlglot_or_503()
    sql = _require_sql(body)
    dialect = body.get("dialect") or "mysql"
    column = body.get("column")
    schema = body.get("schema") or None
    try:
        from sqlglot.lineage import lineage as _lineage
    except Exception as e:  # noqa: BLE001  # pragma: no cover
        raise HTTPException(status_code=503, detail=f"sqlglot.lineage unavailable: {e}")

    def _one(col: str) -> dict[str, Any]:
        try:
            node = _lineage(col, sql, dialect=dialect, schema=schema)
            return {"column": col, "lineage": _node_to_dict(node), "error": None}
        except Exception as e:  # noqa: BLE001
            return {"column": col, "lineage": None, "error": str(e)}

    if column:
        return {"tables": _referenced_tables(sql, dialect), "columns": [_one(column)]}

    # 未指定列: 对 SELECT 输出别名逐一求血缘
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
        out_cols: list[str] = []
        for sel in tree.expressions:
            alias = sel.alias_or_name
            if alias and alias != "*" and alias not in out_cols:
                out_cols.append(alias)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"error": f"parse failed: {e}"})
    return {"tables": _referenced_tables(sql, dialect), "columns": [_one(c) for c in out_cols]}


# ── ③ rls-diff ─────────────────────────────────────────────────

@router.post("/rls-diff", summary="baseSql vs securedSql 行级+列级 RLS 改写 diff")
def rls_diff(body: dict[str, Any], request: Request) -> dict[str, Any]:
    _sqlglot_or_503()
    from services.shared.common.auth import verify_internal_identity
    sql = _assert_read_only(_require_sql(body))
    ds_id = body.get("datasource_id")
    ds_id = _as_int(ds_id, "datasource_id") if ds_id is not None else None
    # 身份只信上游入口服务(datamind)签发的内部头(I2/I4), 忽略请求体里的 user_id/workspace_id
    ident = verify_internal_identity(request.headers.get("X-Internal-Identity", "")) or {}
    user_id = _as_int(ident.get("user_id"), "user_id")
    ws_id = _as_int(ident.get("workspace_id"), "workspace_id")
    secured, applied, policies, col_restr = _resolve_rls(sql, ds_id, user_id, ws_id)
    error = None
    if secured == "":
        error = (policies[0] if policies else "RLS rewrite failed")
        secured = sql  # diff 面板仍展示基线, 但标注 error
    diff = list(difflib.unified_diff(
        sql.splitlines(), secured.splitlines(),
        fromfile="baseSql", tofile="securedSql", lineterm="",
    ))
    changed = any(l.startswith("+") and not l.startswith("+++") for l in diff)
    return {
        "baseSql": sql, "securedSql": secured,
        "appliedRls": applied, "rlsPolicies": policies,
        "columnRestrictions": col_restr,
        "changed": changed, "diff": diff, "error": error,
        "rewritten": bool(applied) and not bool(error),
    }


# ── ⑤ provenance ───────────────────────────────────────────────

@router.post("/provenance", summary="object -> binding / query_mode / size_class 溯源")
def provenance(body: dict[str, Any]) -> dict[str, Any]:
    obj = (body.get("object") or "").strip()
    if not obj:
        raise HTTPException(status_code=400, detail="object is required")
    ds_id = _as_int(body.get("datasource_id"), "datasource_id")
    binding, warnings = resolve_binding(obj, datasource_id=ds_id)
    if binding is None:
        raise HTTPException(status_code=404, detail={
            "error": f"object '{obj}' not bound to any physical table", "warnings": warnings,
        })
    metrics = body.get("metrics") or []
    dims = body.get("dimensions") or []
    prov: dict[str, Any] = {"binding": binding.model_dump(), "warnings": warnings}
    # 可选: 附带编译(不执行)以给出 baseSql + provenance, 帮助 Playground 对齐"意图->SQL"
    if metrics or dims:
        try:
            from services.shared.semantics.planner import plan
            q = SemanticQuery(object=obj, metrics=metrics, dimensions=dims,
                              datasource_id=ds_id, dry_run=True)
            p = plan(q, binding)
            prov["plan"] = {"sql": p.sql, "route": p.route, "provenance": p.provenance,
                            "warnings": p.warnings}
        except Exception as e:  # noqa: BLE001
            prov["plan_error"] = str(e)
    return prov
