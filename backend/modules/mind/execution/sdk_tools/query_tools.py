"""query 工具组 — execute_sql(校验 + 权限 + 审计).

handler 为 SDK 无关的纯函数,由 build_query_server(backend) 包装。
"""

import asyncio
import logging
import re
from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text
from backend.semantics.contract import (SemanticError, classify_exec_failure,
                                        safe_governance_notes as _safe_governance_notes)

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


ANNOTATED_SITES_BATCH = Annotated[
    Optional[list],
    "FAN-OUT: list of site business names/codes (e.g. ['中国','国际','日本']) to run THIS SAME SQL "
    "on, concurrently server-side, in ONE tool call. Use it instead of issuing one execute_sql call "
    "per site: tool calls issued in the same round are serialized by the dispatch layer, so N calls "
    "stack N latencies onto the turn wall-clock (that is how turns hit the 300s cap). Each site is "
    "resolved within your authorized sources; the result is bucketed per site. Sites are independent "
    "measures — present them per site and do NOT sum them into a 'global' value.",
]

ANNOTATED_DATASOURCES_BATCH = Annotated[
    Optional[list],
    "FAN-OUT by datasource business name (as shown by list_datasources, e.g. ['prd-sls-cn','prd-sls-jp']) "
    "when one site hosts several sources or the target is a specific source rather than a site. Same "
    "semantics as 'sites' (one call, server-side concurrency, per-source bucketed results). Do NOT pass "
    "numeric ids; do NOT guess names — ask the user if unsure.",
]


def _resolve_datasource_arg(args, ctx, table_hint: str = "") -> int | None:
    """查询工具的生效数据源解析(waker-datasource-domain §2):

    - LLM 传 `site`(站点业务名/代码) → 服务端在角色授权集内解析为源(优先于源名,
      站点内多源时用模型 deploy_sites 路由目标消歧;带 `object` 时再按该对象的部署
      绑定消歧; nl2sql 旁路没有对象上下文时用 `table_hint`（SQL 里的表名）反查绑定行
      消歧 —— 否则问 ARMS/SLS 观测对象会被“本站默认业务库”启发式定到 mysql 库),
      未命中抛含候选名的可操作错误;
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
        return resolve_site_to_source(site, available, model_id=model_id,
                                      object_key=str(args.get("object") or ""),
                                      table_hint=str(table_hint or ""))
    name = (args.get("datasource") or "").strip()
    if not name:
        return ctx.datasource_id if ctx else None
    from backend.modules.mind.execution.resource_guard import _resolve_authorized_source

    return _resolve_authorized_source(name, available)


async def execute_sql(args):
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.core.query_executor import validate_sql

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

    # 多站一次调用内并发扇出（性能护栏）：SDK/MCP 工具分发层会把同一轮的并行工具调用
    # 串行执行（探针 backend/scripts/probe_tool_concurrency.py：并发墙钟≈单条最大、
    # 串行墙钟≈Σ 单条），所以"少发几条、在服务端并发"才是有效解法。
    sites_batch = _batch_names(args.get("sites"))
    sources_batch = _batch_names(args.get("datasources"))
    if sites_batch or sources_batch:
        return await _execute_sql_fanout(args, ctx, sql, sites_batch, sources_batch)

    try:
        ds_id = _resolve_datasource_arg(args, ctx, _sql_table_hint(sql))
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
                role=ctx.user_role or "",
                workspace_id=int(ctx.workspace_id or 0),
                datasource_id=int(ds_id or 0),
                # 审计同源注入：本轮问题与 SQL 同批留痕（护栏 §9），不下发 LLM/前端。
                question=str((ctx.extra or {}).get("question") or ""),
            ),
        )
    except SemanticError as e:
        logger.error("execute_sql denied/failed: %s", e.message)
        # 错因类别 + 生效源回显随错误一起下发：分类已在执行口对着**原始异常**做完（那里
        # 文本还在手上），本层只搬运。不给“本次落在哪个源/什么方言”就无法判断
        # “SLS 方言的 SQL 打到 MySQL”（历史缺陷：会话 1783069459277）。
        out = {"error": e.message, "blocked_at": e.blocked_at}
        category = str((e.detail or {}).get("failure_category") or "")
        if category:
            out["failure_category"] = category
        target = _target_echo(ds_id)
        if target:
            out["target"] = target
        return _text(out, is_error=True)
    except Exception as e:
        # 确定性的方言/缺表失败不得归成“连接抖动，建议稍后重试”（历史缺陷：会话
        # 1783069459277 的 1064 被掩成“数据源连接失败（系统侧问题）”）；
        # 原始报错仍只进服务端日志（护栏 §7）。
        logger.error("execute_sql error: %s", e)
        category, hint = classify_exec_failure(str(e), _datasource_db_type(ds_id))
        return _text({"error": hint, "failure_category": category}, is_error=True)

    names = [c.name for c in result.columns]
    row_count = int(result.row_count)
    truncated = row_count > MAX_ROWS
    records = [dict(zip(names, row)) for row in result.rows[:MAX_ROWS]]
    payload = {
        "row_count": row_count,
        "execution_ms": result.elapsed_ms,
        "truncated": truncated,
        "columns": names,
        "rows": records,
        # 生效源回显（业务名+方言）：模型能自己发现“写到 MySQL 上的其实是 SLS 方言”，
        # 而不是拿到错数或错错就当成数据问题。
        "target": _target_echo(ds_id),
    }
    # 默认窗静默截断必须显式提醒（no-silent-degradation）：行少不等于没数据。
    notice = _sls_time_window_notice(sql, ds_id)
    if notice:
        payload["warnings"] = [notice]
    # 无人值守采集点（与 run_semantic_query 同一入口）：定时任务的报告需要服务端
    # 受控结果与生效源来登记来源权限快照；交互链路没有 sink，本调用 no-op。
    from backend.modules.mind.execution.sdk_tools.context import record_unattended_query
    from backend.modules.gov.services.sql_lineage import resolve_dialect
    record_unattended_query(ctx, sql=sql, datasource_id=ds_id,
                            dialect=resolve_dialect(_datasource_db_type(ds_id)) or "mysql",
                            columns=names, rows=records, row_count=row_count,
                            truncated=truncated, source="execute_sql")
    return _text(payload)


def _batch_names(value) -> list:
    """批量选源入参归一（口径与 resource_guard 预校验同一份实现）。"""
    from backend.modules.mind.execution.resource_guard import _norm_name_list

    return _norm_name_list(value)


async def _execute_sql_fanout(args, ctx, sql: str, sites: list, sources: list):
    """同一条 SQL 在多个已授权源上并发执行，一次工具调用返回分站结果。

    为什么在服务端并发而不是让模型多发几条：SDK 的工具分发层对同轮并行调用是
    串行执行的（并发探针实测），发 N 条就把 N 段耗时线性叠到单轮墙钟上 —— 本会话
    三轮撞 300s 上限的直接推手之一。

    安全口径（不新增任何旁路）：
    - 源名一律由 resource_guard 在**角色授权集**内预演解析，这里再解析一次拿实际 id；
    - 每个源仍各走 `semantics.execute.execute_sql`（治理 + 审计，护栏 §1/§9），
      身份只取服务端 ctx，绝不信 LLM 传的 id；
    - 单站失败 = 该站显式 error 条目（含可操作提示），不静默丢站、不拿别站数充数。
    """
    available = (ctx.extra or {}).get("available_datasource_ids") or []
    model_id = int(getattr(ctx, "model_id", 0) or 0)

    targets: list[tuple[str, int]] = []
    failed: list[dict] = []
    for value in list(sites) + list(sources):
        is_site = value in sites
        try:
            if is_site:
                from backend.core.site_service import resolve_site_to_source
                # 与单发路径同口径带 table_hint：扇出的每一站同样可能是“观测源与业务库共站”，
                # 不带表名反查绑定行时，SLS 方言的 SQL 会被本站默认业务库（MySQL）抢走。
                ds_id = int(resolve_site_to_source(value, available, model_id=model_id,
                                                   object_key=str(args.get("object") or ""),
                                                   table_hint=_sql_table_hint(sql)) or 0)
            else:
                from backend.modules.mind.execution.resource_guard import _resolve_authorized_source
                ds_id = int(_resolve_authorized_source(value, available) or 0)
            if not ds_id:
                raise ValueError("未解析到生效数据源")
            targets.append((value, ds_id))
        except Exception as exc:  # 解析失败显式回报，引导 ask_user，不猜源
            failed.append({"target": value,
                           "error": str(exc),
                           "hint": "用 list_datasources 的源业务名或「数据源拓扑」里的站点名重试，不确定就问用户。"})
    if not targets:
        return _text({"error": "扇出的站点/数据源全部未能解析", "results": failed}, is_error=True)

    from backend.semantics.execute import PolicyContext, execute_sql as governed_execute_sql

    async def run_one(label: str, ds_id: int) -> dict:
        huge = _huge_scan_guard(sql, ds_id)
        if huge:
            return {"target": label, "error": huge,
                    "hint": "为 large/huge 表加过滤谓词, 或改用 run_semantic_query。"}
        try:
            result = await asyncio.to_thread(
                governed_execute_sql, sql,
                PolicyContext(user_id=int(ctx.user_id or 0), username=ctx.username or "",
                              role=ctx.user_role or "", workspace_id=int(ctx.workspace_id or 0),
                              datasource_id=int(ds_id or 0),
                              question=str((ctx.extra or {}).get("question") or "")),
            )
        except SemanticError as e:
            entry = {"target": label, "error": e.message}
            category = str((e.detail or {}).get("failure_category") or "")
            if category:
                entry["failure_category"] = category
            return entry
        except Exception as e:  # 对外文案按错因分类（原始细节只在服务端日志，护栏 §7）
            logger.error("[execute_sql fanout] %s failed: %s", label, e)
            category, hint = classify_exec_failure(str(e), _datasource_db_type(ds_id))
            return {"target": label, "error": hint, "failure_category": category}
        names = [c.name for c in result.columns]
        row_count = int(result.row_count)
        entry = {
            "target": label,
            "row_count": row_count,
            "execution_ms": result.elapsed_ms,
            "truncated": row_count > MAX_ROWS,
            "columns": names,
            "rows": [dict(zip(names, row)) for row in result.rows[:MAX_ROWS]],
            "dialect": _datasource_db_type(ds_id),
        }
        # 逐站扇出的每一站都是独立取数，无人值守报告需要逐站登记来源与快照。
        from backend.modules.mind.execution.sdk_tools.context import record_unattended_query
        from backend.modules.gov.services.sql_lineage import resolve_dialect
        record_unattended_query(ctx, sql=sql, datasource_id=ds_id,
                                dialect=resolve_dialect(_datasource_db_type(ds_id)) or "mysql",
                                columns=names, rows=entry["rows"], row_count=row_count,
                                truncated=entry["truncated"], source=f"execute_sql:{label}")
        notice = _sls_time_window_notice(sql, ds_id)
        if notice:
            entry["warnings"] = [notice]
        return entry

    results = await asyncio.gather(*[run_one(label, ds_id) for label, ds_id in targets])
    payload = {"batch": True, "targets": [label for label, _ in targets] + [f["target"] for f in failed],
               "results": list(results) + failed,
               "ok_count": sum(1 for r in results if "error" not in r),
               "failed_count": sum(1 for r in results if "error" in r) + len(failed),
               "guidance": "各站/各源独立口径分别呈现，不得把各行相加当作全局去重值；"
                           "某站 error 不等于该站无数据，照实说明并单独重试。"}
    return _text(payload)


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
        ds_id = _resolve_datasource_arg(args, ctx, _sql_table_hint(sql))
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

    # 2.5) SLS 默认扫描窗提醒：不执行也要报（verdict 降为 warn），否则模型拿到
    # “只扫了 15 分钟”的预检结论就去执行（no-silent-degradation §1）。
    sls_notice = _sls_time_window_notice(sql, ds_id)
    if sls_notice:
        warnings.append(sls_notice)

    # 2.6) 预检“能不能跑”：表存在性 + 方言形态（旧写法只看安全/权限，判了 ok 却在执行时 1064）
    warnings.extend(_precheck_warnings(sql, ds_id))

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
        "target": _target_echo(ds_id),
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


def _datasource_display_name(ds_id) -> str:
    """数据源**业务名**（与 list_datasources 同源下发，不属 §7 禁止外泄项）。

    只用于回显“本次生效的是哪个源”，让模型能自证选源与方言是否相配（历史缺陷：
    会话 1783069459277 里 SLS 方言的 SQL 被静默送到本站 MySQL 业务库，模型无从发现）。
    """
    if not ds_id:
        return ""
    from backend.common.ttl_cache import datasource_cache

    key = f"dispname:{ds_id}"
    cached = datasource_cache.get(key)
    if cached is not None:
        return str(cached or "")
    name = ""
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM adh_datasources WHERE id = %s", (int(ds_id),))
                row = cur.fetchone()
                name = str((row or {}).get("name") or "").strip()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — 提示性字段取不到不得影响取数
        logger.warning("[query_tools] 数据源业务名查询失败(ds=%s): %s", ds_id, e)
        return ""
    datasource_cache.set(key, name)
    return name


def _sql_table_hint(sql: str) -> str:
    """从 SQL 里取第一个表名，用于站点内多源消歧（只看 FROM/JOIN 后的名字，不猜）。"""
    if not sql:
        return ""
    try:
        from backend.core.enforcer import permission_enforcer

        tables = permission_enforcer._extract_tables(sql) or []
        if tables:
            return str(tables[0] or "").strip()
    except Exception as exc:  # noqa: BLE001 — 消歧辅助信息，取不到回到原有链路
        logger.debug("[query_tools] SQL 表名提取失败（不消歧）: %s", exc)
    return ""


def _target_echo(ds_id) -> dict:
    """本次生效源的可见回显（业务名 + 引擎类型）：只含业务层信息，不含 id/主机/账号。"""
    out: dict = {}
    name = _datasource_display_name(ds_id)
    if name:
        out["source_name"] = name
    dialect = _datasource_db_type(ds_id)
    if dialect:
        out["dialect"] = dialect
    return out


def _source_table_names(ds_id) -> tuple[set[str], bool]:
    """目标源已同步的表名集合 → (小写表名集, 是否拿到了清单)。

    第二个位返回 False 表示“没同步过元数据或查失败”，调用方不得把“查不到”当“表不存在”。
    """
    if not ds_id:
        return set(), False
    try:
        from backend.common.db import execute_query

        rows = execute_query(
            "SELECT table_name FROM adh_table_info WHERE datasource_id = %s AND is_active = 1",
            (int(ds_id),)) or []
    except Exception as e:  # noqa: BLE001 — 清单不可用不阻断预检，但不得据此下结论
        logger.warning("[query_tools] 目标源表清单加载失败(ds=%s): %s", ds_id, e)
        return set(), False
    names = {str((r or {}).get("table_name") or "").strip().lower() for r in rows}
    names.discard("")
    return names, bool(names)


def _precheck_warnings(sql: str, ds_id) -> list[str]:
    """预检阶段的“能跑通吗”提醒：表存在性 + 方言形态（历史缺陷补位）。

    为什么补这一层（会话 1783069459277）：`check_sql` 当时判 `verdict=ok`，`execute_sql`
    才炸 1064 —— 预检只看安全与权限，不看“这张表在这个源里到底有没有”、“语句是不是这个
    源的方言”，模型就把“预检通过”当成“这条 SQL 能跑”，失败后又归给“数据源连接”。

    口径（宁勿错）：
    - 未定出生效源（ds≤0）→ 没有“目标源”可核对，整段跳过（选源本身另有 fail-loud 口）；
    - 该源没同步表清单 → **不下结论也不提醒**：这条对模型没有可操作价值，且会让每个
      未同步源的预检都从 ok 掉到 warn（告警疲劳比缺提醒更坏）；真写错表名时执行阶段
      会由 `classify_exec_failure` 给出 missing_object 的可操作文案；
    - 只提醒不阻断（verdict 降为 warn）：清单可能滞后于物理库，不得据此拒执行。
    """
    out: list[str] = []
    dialect = _datasource_db_type(ds_id)
    tables: list[str] = []
    try:
        from backend.core.enforcer import permission_enforcer

        tables = [str(t or "").strip() for t in (permission_enforcer._extract_tables(sql) or [])
                  if str(t or "").strip()]
    except Exception as exc:  # noqa: BLE001 — 预检辅助项，取不到不影响安全裁决
        logger.debug("[query_tools] 预检表名提取失败: %s", exc)
    if tables and int(ds_id or 0) > 0:
        known, has_list = _source_table_names(ds_id)
        if has_list:
            missing = [t for t in tables
                       if t.split(".")[-1].strip('`"\'').lower() not in known]
            if missing:
                out.append(
                    f"目标源{('（' + dialect + '）') if dialect else ''}的元数据清单里没有这些表："
                    f"{', '.join(missing)}。这通常是选错数据源或把语义对象名当成了表名（两者不同名），"
                    "重试不会好；先用 get_table_schema / search_metadata 确认表名，"
                    "或用 datasource（业务名）显式指向数据真正所在的那个源。"
                    "（清单滞后于物理库时仍可能实际存在，所以不阻断执行）")
    if dialect in ("mysql", "doris", "starrocks") and re.search(r'\b(?:FROM|JOIN)\s+"', sql or "", re.I):
        out.append("目标源是 MySQL 系方言：双引号在它里面是字符串字面量而不是标识符，"
                   "表名/列名请用反引号或不加引号；重试不会好，需重写这条 SQL。")
    if dialect == "sls" and re.search(r"\b(?:FROM|JOIN)\s+`", sql or "", re.I):
        out.append("目标源是 SLS(Presto 系) 方言：标识符应用双引号而不是反引号；重试不会好，需重写这条 SQL。")
    return out


def _datasource_db_type(ds_id) -> str:
    """数据源引擎类型（小写）；口径委派 `semantics.binding_resolver.lookup_db_type`
    （唯一 SQL 实现），本层只加 TTL 缓存避免每次取数/每条提示多一趟元库。

    缓存不背配置一致性风险：数据源 db_type 在存量行上不会就地改（改类型等于重建），
    300s TTL 内最多让一两条提示口径短暂过时，不影响权限裁决。
    """
    if not ds_id:
        return ""
    from backend.common.ttl_cache import datasource_cache

    key = f"dbtype:{ds_id}"
    cached = datasource_cache.get(key)
    if cached is not None:
        return str(cached or "")
    from backend.semantics.binding_resolver import lookup_db_type

    try:
        db_type = lookup_db_type(int(ds_id))
    except Exception as e:  # noqa: BLE001 — 查不到就当非 SLS：少一条提示，不得影响取数主链路
        logger.warning("[query_tools] db_type 查询失败(ds=%s): %s", ds_id, e)
        return ""
    datasource_cache.set(key, db_type)
    return db_type


# SLS(PG 协议) 端点在 SQL 未写时间谓词时按引擎自身的默认时间窗扫描。
# 探针实测（backend/scripts/probe_sls_default_time_window.py，2026-11-04 prd-sls-cn /
# observability_rum）：无谓词 37 行 ≡ `__time__ >= now()-900` 的 37 行，而 365 天是
# 22,080,802 行 —— 默认窗就是「最近 15 分钟」。并且用 `date`/`timestamp` 列过滤
# 不会放宽该窗口（只能收窄）—— row_count/truncated 都看不出被截断，调用方会把
# 15 分钟的数当全量答出去，错数比无答案更坏（no-silent-degradation §1）。
_SLS_TIME_PREDICATE_RE = re.compile(
    r'["`\[]?__time__["`\]]?\s*(?:>=|<=|!=|>|<|=|\bBETWEEN\b|\bIN\b)', re.IGNORECASE)


def _sls_time_window_notice(sql: str, datasource_id) -> str:
    """SLS 扫描窗口提醒：仅对 SLS 源、有 FROM、且未写 `__time__` 比较谓词时给出。

    只提醒不改写 SQL：自动注入时间窗等于替用户改口径（隐式语义变更），同样违规。
    投影里的 `MIN("__time__")` 不算谓词，仍会提醒。
    """
    if not sql:
        return ""
    if _datasource_db_type(datasource_id) != "sls":
        return ""
    if not re.search(r"\bFROM\b", sql, re.IGNORECASE):
        return ""  # 常量探测/无表查询不涉及扫描窗口
    if _SLS_TIME_PREDICATE_RE.search(sql):
        return ""  # 已显式给时间谓词
    return (
        "SLS 默认时间窗陷阱：本条 SQL 没有 `__time__` 比较谓词，引擎只扫描默认窗"
        "（探针实测≈最近 15 分钟），返回的 row_count 不是全量；用 `date`/`timestamp` "
        "列过滤不会放宽该窗口。要查历史区间请显式加 "
        "`__time__ >= to_unixtime(now()) - <秒数>` 并与用户问题对齐后再下结论；"
        "确实只需最近 15 分钟可忽略本提醒。"
    )


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
            "Omit both to use the session-selected source. Do NOT pass a numeric id. "
            "SLS datasources: without an explicit `__time__` comparison predicate the engine scans only its "
            "default window (measured ~last 15 minutes), so a small row_count does NOT mean 'no data' — add "
            "`__time__ >= to_unixtime(now()) - <seconds>` for any historical window. "
            "For the same SQL across several sites/sources pass 'sites' or 'datasources' (ONE call, "
            "server-side concurrency) instead of calling this tool once per site."
        ),
        "schema": {
            "sql": Annotated[str, "The SELECT SQL statement to execute"],
            "site": ANNOTATED_SITE,
            "datasource": Annotated[Optional[str], "Target datasource by its business name (as shown by list_datasources); omit to use the session-selected source. Do NOT guess — if unsure which source, ask the user."],
            "sites": ANNOTATED_SITES_BATCH,
            "datasources": ANNOTATED_DATASOURCES_BATCH,
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
