"""query 工具组 — execute_sql(校验 + 权限 + 审计).

handler 为 SDK 无关的纯函数,由 build_query_server(backend) 包装。
"""

import asyncio
import logging
import re
from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text
from backend.semantics.contract import safe_governance_notes as _safe_governance_notes

logger = logging.getLogger(__name__)

# 结果行数上限,避免超大结果撑爆上下文
MAX_ROWS = 200


ANNOTATED_SITE = Annotated[
    Optional[str],
    "Target site by its business name or code (e.g. '中国' / 'cn'), as listed in the session "
    "'数据源拓扑' context. Resolved server-side to that site's datasource within your authorized "
    "sources; preferred over 'datasource' for site-scoped questions. Omit to use the session site. "
    "Do NOT guess — if unsure which site, ask the user.",
]


def _resolve_datasource_arg(args, ctx) -> int | None:
    """查询工具的生效数据源解析(waker-datasource-domain §2):

    - LLM 传 `site`(站点业务名/代码) → 服务端在角色授权集内解析为源(优先于源名,
      站点内多源时用模型 deploy_sites 路由目标消歧), 未命中抛含候选名的可操作错误;
    - LLM 传 `datasource`(业务名)→ 在角色授权集内按 name 解析为 id(唯一裁决
      = resource_guard._resolve_authorized_source), 未命中抛含候选名的可操作错误;
    - 未传 → 用会话生效源 ctx.datasource_id(已由 resource_guard 校验∈授权集);
    - 禁止消费 LLM 传的数值 id(黑盒, 不得诱导猜值)。
    """
    site = str(args.get("site") or "").strip()
    available = (ctx.extra.get("available_datasource_ids") if ctx else None) or []
    if site:
        from backend.core.site_service import resolve_site_to_source

        model_id = int(getattr(ctx, "model_id", 0) or 0) if ctx else 0
        return resolve_site_to_source(site, available, model_id=model_id)
    name = (args.get("datasource") or "").strip()
    if not name:
        return ctx.datasource_id if ctx else None
    from backend.modules.mind.execution.resource_guard import _resolve_authorized_source

    return _resolve_authorized_source(name, available)


async def execute_sql(args):
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.core.query_executor import validate_sql
    from backend.semantics.contract import EXEC_FAIL_HINT, SemanticError

    ctx = get_execution_context()
    sql = (args.get("sql") or "").strip()
    if not sql:
        return _text({"error": "sql is required"}, is_error=True)

    # Phase 4 护栏: execute_sql 已降级为"受控探索旁路"(agent 主路走 run_semantic_query)。
    #   1) 消除 require_limit=False 旁路: 缺 LIMIT 的扫描自动补默认 LIMIT 后再校验。
    #   2) huge + raw_source 且无谓词时直接拒绝(与 planner 同口径, 不静默)。
    ok, msg = validate_sql(sql, require_limit=False)  # 先做安全性(DDL/DML/多语句)校验, 不含 LIMIT
    if not ok:
        return _text({"error": f"SQL 校验失败: {msg}"}, is_error=True)

    try:
        from backend.core.sql_validator import add_limit
        sql = add_limit(sql)
    except Exception:
        pass
    ok, msg = validate_sql(sql, require_limit=True)
    if not ok:
        return _text({"error": f"护栏: {msg}"}, is_error=True)

    try:
        ds_id = _resolve_datasource_arg(args, ctx)
    except Exception as e:
        # 名解析失败的可操作错误(含候选名)原样返回给 LLM，引导询问用户，不掩盖
        return _text({"error": str(e)}, is_error=True)
    guardrail_err = _huge_scan_guard(sql, ds_id)
    if guardrail_err:
        return _text({"error": guardrail_err, "hint": "为 large/huge 表加过滤谓词, 或改用 run_semantic_query。"}, is_error=True)

    try:
        # Phase 7.2：执行统一经 semantics.execute.execute_sql（护栏 §1 全平台唯一
        # 取数执行口，内部经 execute_query_with_permission 治理+审计）；
        # 对外错误文案已脱敏（权限拒绝保留声明式文案，执行失败回通用文案，护栏 §7）。
        from backend.semantics.execute import PolicyContext, execute_sql

        result = await asyncio.to_thread(
            execute_sql,
            sql,
            PolicyContext(
                user_id=int(ctx.user_id or 0),
                username=ctx.username or "",
                workspace_id=int(ctx.workspace_id or 0),
                datasource_id=int(ds_id or 0),
            ),
        )
    except SemanticError as e:
        logger.error("execute_sql denied/failed: %s", e.message)
        return _text({"error": e.message}, is_error=True)
    except Exception as e:
        logger.error("execute_sql error: %s", e)
        return _text({"error": EXEC_FAIL_HINT}, is_error=True)

    names = [c.name for c in result.columns]
    row_count = int(result.row_count)
    truncated = row_count > MAX_ROWS
    records = [dict(zip(names, row)) for row in result.rows[:MAX_ROWS]]
    return _text({
        "row_count": row_count,
        "execution_ms": result.elapsed_ms,
        "truncated": truncated,
        "columns": names,
        "rows": records,
    })


async def check_sql(args):
    """SQL 治理预检: 只判断安全与权限, 不执行、不返回数据行。

    供 nl2sql 路径在 execute_sql 前自检: 只读性/DDL-DML/多语句/LIMIT/huge 全扫,
    以及当前身份对引用表的表级/列级(隐藏/脱敏)/行级(RLS)权限预览。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.core.query_executor import validate_sql

    ctx = get_execution_context()
    sql = (args.get("sql") or "").strip()
    if not sql:
        return _text({"error": "sql is required"}, is_error=True)
    try:
        ds_id = _resolve_datasource_arg(args, ctx)
    except Exception as e:
        # 名解析失败的可操作错误(含候选名)原样返回给 LLM，引导询问用户，不掩盖
        return _text({"error": str(e)}, is_error=True)

    warnings: list[str] = []

    # 1) 安全: 仅 SELECT/WITH, 禁 DDL/DML/多语句(不含 LIMIT 校验)
    ok, msg = validate_sql(sql, require_limit=False)
    security = {"is_read_only": ok, "detail": msg if not ok else "仅 SELECT/WITH, 无 DDL/DML/多语句"}
    if not ok:
        warnings.append(f"安全: {msg}")

    # 2) LIMIT / huge 全扫护栏(与 execute_sql 同口径)
    has_limit = bool(re.search(r"\bLIMIT\b", sql, re.IGNORECASE))
    limit_note = "" if has_limit else "缺 LIMIT, 执行时会自动补默认 LIMIT"
    huge = _huge_scan_guard(sql, ds_id)
    if huge:
        warnings.append(huge)

    # 3) 权限预览(不执行): 逐表 check_access, 汇总隐藏/脱敏列与行过滤命中
    permission = {
        "allowed": True, "tables": [], "denied_tables": [],
        "hidden_columns": [], "masked_columns": {},
        "row_filter_tables": [], "policies_applied": [], "governance_note": "",
    }
    try:
        from backend.core.enforcer import permission_enforcer
        tables = permission_enforcer._extract_tables(sql)
        permission["tables"] = tables
        user_id = ctx.user_id if ctx else 0
        ws_id = ctx.workspace_id if ctx else 0
        only_sensitive = not user_id
        hidden: set = set()
        masked: dict = {}
        policies: list = []
        for t in tables:
            try:
                policy_table = permission_enforcer._policy_table(t, ds_id or 0)
            except Exception as exc:  # 跨库/未绑定限定名
                permission["allowed"] = False
                permission["denied_tables"].append({"table": t, "reason": str(exc)})
                continue
            res = permission_enforcer.check_access(
                user_id, ws_id, ds_id or 0, policy_table, sensitive_only=only_sensitive)
            if not res.allowed:
                permission["allowed"] = False
                permission["denied_tables"].append({"table": t, "reason": res.reason})
            hidden.update(res.hidden_columns)
            for col, m in res.masked_columns.items():
                masked[col] = m
            if res.row_filter:
                permission["row_filter_tables"].append(t)
            policies.extend(res.policies_applied)
        permission["row_filter_tables"] = sorted(set(permission["row_filter_tables"]))
        # 治理说明只回“与本次查询相关”的列：全局敏感基线/脱敏登记是按列名跨所有源
        # 在册的注册表，整包外发会把“别的表的列”当成“本查询的字段被屏蔽”
        # （历史缺陷：基线里的 activator_ip 被模型当成“RUM 的 IP 被合规屏蔽”）。
        # 口径与 semantics.contract.safe_governance_notes 同源：命中本次表/列的才列名，
        # 其余只报数量并禁止跨字段推断；原始清单仍进服务端审计。
        known = _governed_columns_in_scope(sql, ds_id, tables)
        rel_hidden = sorted(h for h in hidden if _governed_col_relevant(h, sql, known))
        rel_masked = {k: v for k, v in masked.items() if _governed_col_relevant(k, sql, known)}
        permission["hidden_columns"] = rel_hidden
        permission["masked_columns"] = rel_masked
        gov = _safe_governance_notes(sorted(set(str(p) for p in policies)), sorted(masked))
        permission["policies_applied"] = gov["applied_rls"]
        scope_hits = len(rel_hidden) + len(rel_masked)
        rest = len(hidden) + len(masked) - scope_hits
        if rest > 0:
            permission["governance_note"] = (
                f"另有 {rest} 个受管控列是其他字段（不同列名），不在本次查询的表/列中，"
                "未作用于本次结果；不得据此推断别的字段被屏蔽/不存在")
        blocked_hits = permission_enforcer._references_blocked(sql, list(hidden))
        if blocked_hits:
            permission["allowed"] = False
            # 点名的是调用方自己 SQL 里的列名（非注册表外发），保留可操作性
            warnings.append(f"权限: 查询显式点名了被屏蔽列 {blocked_hits}, 执行将被拒绝")
        if permission["row_filter_tables"]:
            warnings.append("以下表会自动施加行级过滤(RLS): " + ", ".join(permission["row_filter_tables"]))
    except Exception as exc:  # 预览失败不得阻断, 但要显式暴露
        logger.error("check_sql permission preview error: %s", exc)
        permission["preview_error"] = "权限预览暂不可用, 请谨慎执行"
        warnings.append("权限预览暂不可用")

    if not ok or not permission["allowed"]:
        verdict = "blocked"
    elif warnings:
        verdict = "warn"
    else:
        verdict = "ok"

    return _text({
        "executed": False,
        "verdict": verdict,
        "security": security,
        "limit": {"present": has_limit, "note": limit_note},
        "permission": permission,
        "warnings": warnings,
        "guidance": _check_sql_guidance(verdict),
    })


def _check_sql_guidance(verdict: str) -> str:
    return {
        "ok": "安全与权限均通过, 可用 execute_sql 执行(仍会自动施加治理)。",
        "warn": "存在提醒(如缺 LIMIT/行级过滤), 修正谓词或确认后执行。",
        "blocked": "被护栏拒绝: 含非只读语句、越权表或点名被屏蔽列, 须修正或申请权限后再试, 不得绕过。",
    }.get(verdict, "")


def _governed_columns_in_scope(sql: str, datasource_id, tables: list) -> set[str]:
    """本次查询表在元库里登记的列集合（供治理提示收敛到相关列）。"""
    cols: set[str] = set()
    if not tables:
        return cols
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                ph = ",".join(["%s"] * len(tables))
                cur.execute(
                    f"SELECT column_name FROM adh_column_metadata "
                    f"WHERE datasource_id=%s AND table_name IN ({ph}) AND is_active=1",
                    (datasource_id or 0, *tables))
                for r in cur.fetchall():
                    if r.get("column_name"):
                        cols.add(str(r["column_name"]))
    except Exception as e:  # 元库不可用: 只少列不多列(更保守), 但留痕不静默
        logger.warning("[check_sql] 表列清单不可用，治理列提示退化为只认 SQL 点名的列: %s", e)
    return cols


def _governed_col_relevant(col: str, sql: str, known_cols: set[str]) -> bool:
    """受管控列是否与本次查询相关：属于查询表的列，或被 SQL 显式点名。"""
    if not col:
        return False
    if col in known_cols:
        return True
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(col)}(?![A-Za-z0-9_])", sql or "", re.I))


def _huge_scan_guard(sql: str, datasource_id) -> str | None:
    """raw_source/huge 表若无 WHERE 则拒绝(消除无界全表扫旁路)。"""
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        from backend.core.query_executor import _extract_table_names

        tables = _extract_table_names(sql)
        if not tables:
            return None
        has_where = bool(re.search(r"\bWHERE\b", sql, re.IGNORECASE))
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fmt = ",".join(["%s"] * len(tables))
                cur.execute(
                    f"SELECT table_name, size_class, allow_full_scan FROM adh_table_info "
                    f"WHERE table_name IN ({fmt}) AND (%s OR datasource_id = %s) AND is_active = 1",
                    (*tables, datasource_id or 0, datasource_id or 0),
                )
                for r in cur.fetchall():
                    if r.get("size_class") == "huge" and not r.get("allow_full_scan") and not has_where:
                        return f"huge 表 {r['table_name']} 无过滤谓词的全表扫描被护栏拒绝"
        finally:
            conn.close()
    except Exception as e:
        logger.debug("[execute_sql] huge-scan guard skipped: %s", e)
    return None


TOOL_SPECS = [
    {
        "name": "check_sql",
        "description": (
            "Pre-flight governance check for a SQL statement WITHOUT executing it. "
            "Validates security (read-only SELECT/WITH, no DDL/DML/multi-statement, LIMIT, huge-table full scan) "
            "and previews permissions for the current identity across referenced tables "
            "(table access, hidden/masked columns, row-level RLS). Returns a verdict of ok/warn/blocked. "
            "Always call this before execute_sql; it never returns data rows."
        ),
        "schema": {
            "sql": Annotated[str, "The SELECT SQL statement to validate"],
            "site": ANNOTATED_SITE,
            "datasource": Annotated[Optional[str], "Target datasource by its business name (as shown by list_datasources); omit to use the session-selected source. Do NOT guess — if unsure which source, ask the user."],
        },
        "handler": check_sql,
    },
    {
        "name": "execute_sql",
        "description": (
            "Execute a read-only SQL query against a datasource with permission checks and audit logging. "
            "Only SELECT statements are allowed; the query is validated and auto-limited. "
            "For site-scoped questions pass the site business name via 'site' (see the session '数据源拓扑' context); "
            "to target a specific source directly, pass its business name via 'datasource' (see list_datasources). "
            "Omit both to use the session-selected source. Do NOT pass a numeric id."
        ),
        "schema": {
            "sql": Annotated[str, "The SELECT SQL statement to execute"],
            "site": ANNOTATED_SITE,
            "datasource": Annotated[Optional[str], "Target datasource by its business name (as shown by list_datasources); omit to use the session-selected source. Do NOT guess — if unsure which source, ask the user."],
        },
        "handler": execute_sql,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_query_server(backend: str = "qoder", tool_names=None):
    """构建 query 进程内 MCP server(qoder / claude).

    tool_names 给定时只注册被选中的工具(AS-BOT 粒度控制 check_sql / execute_sql 开关)。
    """
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    specs = TOOL_SPECS
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in TOOL_SPECS if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_query", tools)
