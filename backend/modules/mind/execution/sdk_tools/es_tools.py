"""Elasticsearch 内置 MCP 服务的工具 handler —— 列开放索引模式 / 看字段 / 跑只读 SQL。

注册入口：本模块只提供 handler 与 TOOL_SPECS，**不自己建 server**。
它作为 `adh_mcp_servers` 的内置行（transport='builtin', name='elasticsearch'）在
AS-BOT「引用 MCP 服务」里被绑定并逐工具授权，由 execution/builtin_mcp.py 注册、
sdk_tools/external_tools.py 分派 —— 与外部 MCP 同一个配置入口，不另开数据工具组。

与 query 组（`execute_sql`）的关系：**并列的另一条只读通道**，不是它的替代。
- query 组面向 adh_datasources 里已登记的数据源，套完整数据权限（敏感基线/RBAC/RLS/审计）；
- 本组面向 Elasticsearch 索引，按 2026-11-04 的产品决策**不进治理域**：ES SQL 不支持
  子查询包裹（RLS 注入手法不成立）、sqlglot 无 ES 方言（列级策略编译期改写不成立），
  因此数据面闸门改为「索引模式白名单」（data view 口径）——`core/es_client.py` 里未登记即拒，
  空白名单即全拒；开放单位是模式本身（如 `logs-*`），不向集群展开成具体索引。

例外必须显式（no-silent-degradation）：每次查询结果都带 `governance` 标注，说明本通道
未施加列级/行级策略、唯一闸门是白名单；同时落服务端审计。这样评测与审计能分辨
"ES 通道"与"治理通道"，不会把前者当成已治理。

前提（写在配置与代码两处）：白名单内索引的数据**应已脱敏**。若接入未脱敏索引，
需重新评估本通道的数据权限口径，不是在这里加正则兜底。
"""

import logging
from typing import Annotated

from backend.modules.mind.execution.sdk_tools.catalog_tools import (
    _text, record_execute_error,
)

logger = logging.getLogger(__name__)

# 返回行数上限（与 es_client 的 LIMIT 收紧同口径，避免把大结果塞进模型上下文）。
_MAX_ROWS = 200

# 每次对外结果都带上：让审计/评测/用户都能分辨这是未套列级策略的内置通道。
_GOVERNANCE_NOTE = {
    "channel": "builtin_elasticsearch",
    "column_policy": "not_applied",
    "row_filter": "not_applied",
    "gate": "index_allowlist",
    "note": "内置 Elasticsearch 通道：按产品决策不参与列级/行级数据权限治理，"
            "访问范围仅由已开放的索引模式决定；模式内数据应已脱敏。",
}

# 写给模型的方言规则：只放实测确认过的形状（不靠猜 ES 语法）。
# 关键一条：可查单位是**索引模式**（后缀带 *、覆盖按月分片），直接拿模式作 FROM 目标就
# 能跳分片聚合（同 Kibana data view）。展开成具体月名既慢又会把上千个名字塞进上下文。
_SQL_RULES = (
    "Elasticsearch SQL is a restricted dialect: a single SELECT over ONE FROM target "
    "(no JOIN, no multi-index FROM, no CTE), LIMIT is mandatory. "
    "The FROM target MUST be double-quoted because index names contain hyphens and dots "
    '(unquoted they are parsed as subtraction): FROM "logs-*". '
    "The open ranges are index patterns ending with *; indexes behind a pattern are "
    "time-partitioned and Elasticsearch merges every matching shard when you query the "
    "pattern itself (like a Kibana data view), so query the pattern directly and never "
    "enumerate the concrete monthly indexes. "
    'To narrow a period, filter on the time field, or append the period fragment to the '
    'pattern itself (e.g. FROM "logs-*2026.11") — keep the pattern verbatim, dropping its '
    'hyphen widens the range and gets rejected. '
)


def _not_configured() -> dict:
    """配置缺失时的可见错误（不静默返回空集，否则模型会当成"索引里没有数据"）。"""
    return _text({
        "error": "Elasticsearch 服务尚未配置或未开启，无法提供索引查询能力。"
                 "请通知管理员在部署环境变量中完成配置后重试。",
    }, is_error=True)


def _audit(args: dict, *, index: str, row_count: int, ok: bool, reason: str = "") -> None:
    """ES 查询审计：记元数据，**不记数据行**（避免审计成为敏感数据的第二副本）。"""
    try:
        from backend.common.auth import log_audit
        from backend.modules.mind.execution.sdk_tools.context import get_execution_context
        ctx = get_execution_context()
        log_audit(
            int(getattr(ctx, "user_id", 0) or 0),
            str(getattr(ctx, "username", "") or ""),
            action="es_mcp_query" if ok else "es_mcp_query_denied",
            target_type="elasticsearch", target_id=0,
            detail=f"index={index} rows={row_count}" + (f" reason={reason}" if reason else ""),
        )
    except Exception as exc:  # noqa: BLE001 — 审计不得影响主链路（护栏 §9）
        logger.debug("[es-tools] audit write skipped: %s", exc)


async def list_es_indexes(args: dict) -> dict:
    """列出已开放的索引模式（data view）：模式本身就是可查单位，不向集群展开。

    空白名单给显式错误而不返回空清单：空清单会被模型当成"索引里没数据"，
    把配置缺口答成业务结论（no-silent-degradation）。
    """
    from backend.common import config
    from backend.core import es_client

    if not config.ES_MCP_ENABLED:
        return _not_configured()
    views = es_client.list_patterns()
    if not views:
        return _text({
            "error": "当前部署未开放任何 Elasticsearch 索引范围（白名单为空时默认不开放）。"
                     "请通知管理员在部署侧登记可查索引模式后重试。",
            "governance": _GOVERNANCE_NOTE,
        }, is_error=True)
    return _text({
        "data_views": views,
        "total": len(views),
        "how_to_query": 'Query the pattern as-is: SELECT ... FROM "<pattern>" LIMIT n '
                        '(double-quoted, pattern copied verbatim). Elasticsearch resolves and '
                        'merges all time-partitioned indexes behind the pattern; do not enumerate '
                        'concrete monthly names. To restrict a period, append it to the pattern '
                        '(e.g. FROM "<pattern>2026.11") or filter on the time field.',
        "governance": _GOVERNANCE_NOTE,
        "next": "Call describe_es_index with one of these patterns to get field names before writing SQL.",
    })


async def describe_es_index(args: dict) -> dict:
    """索引模式（data view）的字段清单，供写 SQL 前确认可用列。"""
    from backend.common import config
    from backend.core import es_client

    if not config.ES_MCP_ENABLED:
        return _not_configured()
    view = str((args or {}).get("index") or "").strip()
    if not view:
        return _text({"error": "缺少 index 参数（请传 list_es_indexes 返回的索引模式）"}, is_error=True)
    try:
        info = es_client.describe_index_fields(view)
    except es_client.EsError as exc:
        # 白名单拒绝/模式未匹配的文案含可查清单，属可操作提示，直接回给模型。
        _audit(args, index=view, row_count=0, ok=False, reason=str(exc))
        return _text({"error": str(exc)}, is_error=True)
    except Exception as exc:  # noqa: BLE001
        record_execute_error("describe_es_index", exc)
        _audit(args, index=view, row_count=0, ok=False, reason=type(exc).__name__)
        return _text({"error": "Elasticsearch 字段结构获取失败，请联系管理员查看服务端日志"},
                     is_error=True)

    fields = info["fields"]
    return _text({
        "view": info["pattern"],
        "fields": fields,
        "total": len(fields),
        "sub_fields": info["sub_fields"],
        "governance": _GOVERNANCE_NOTE,
        "hint": "Names with a dot are nested/object sub-fields: reference the sub-field itself "
                "(e.g. dep.dep_name), not its parent. A text field cannot be compared or grouped "
                "exactly — use its exact_match_field (the .keyword sub-field) in WHERE and GROUP BY. "
                "Use the view name above verbatim (double-quoted) in FROM.",
    })


async def query_es_sql(args: dict) -> dict:
    """执行只读 Elasticsearch SQL（单条 SELECT + 必须 LIMIT + FROM 目标须在开放范围内）。"""
    from backend.common import config
    from backend.core import es_client

    if not config.ES_MCP_ENABLED:
        return _not_configured()
    sql = str((args or {}).get("sql") or "").strip()
    if not sql:
        return _text({"error": "缺少 sql 参数"}, is_error=True)

    try:
        result = es_client.run_sql(sql)
    except es_client.EsError as exc:
        _audit(args, index=",".join(es_client.from_indexes(sql)) or "-",
               row_count=0, ok=False, reason=str(exc))
        return _text({"error": str(exc), "governance": _GOVERNANCE_NOTE}, is_error=True)
    except Exception as exc:  # noqa: BLE001 — ES 原始报错可能含索引表达式/集群信息
        record_execute_error("query_es_sql", exc)
        _audit(args, index=",".join(es_client.from_indexes(sql)) or "-",
               row_count=0, ok=False, reason=type(exc).__name__)
        return _text({"error": "SQL 执行失败，请联系管理员查看服务端日志",
                      "governance": _GOVERNANCE_NOTE}, is_error=True)

    columns = [c["name"] for c in result["columns"]]
    rows = result["rows"][:_MAX_ROWS]
    records = [dict(zip(columns, row)) for row in rows]
    _audit(args, index=",".join(result.get("indexes") or []) or "-",
           row_count=len(records), ok=True)

    payload = {
        "columns": columns,
        "rows": records,
        "row_count": len(records),
        "truncated": len(result["rows"]) > len(rows) or len(records) >= _MAX_ROWS,
        "governance": _GOVERNANCE_NOTE,
    }
    return _text(payload)


TOOL_SPECS = [
    {
        "name": "list_es_indexes",
        "description": (
            "List the Elasticsearch index patterns (data views) open for querying in this deployment. "
            "Call this first: only these ranges can be queried. The pattern itself is the queryable "
            "unit — indexes behind it are time-partitioned and merged by Elasticsearch, so never ask "
            "for or enumerate the concrete monthly index names. "
            "Returns business-readable range names only — connection addresses and credentials are never exposed."
        ),
        "schema": {},
        "handler": list_es_indexes,
    },
    {
        "name": "describe_es_index",
        "description": (
            "Show the fields of an Elasticsearch index pattern (name, type, aggregatable, plus dotted "
            "sub-field paths and the .keyword exact-match field of text fields) before writing SQL. "
            "Fields are the union across every time-partitioned index behind the pattern, so one call is enough. "
            "The pattern must be one returned by list_es_indexes."
        ),
        "schema": {"index": Annotated[
            str,
            "Index pattern exactly as returned by list_es_indexes (e.g. logs-*), "
            "or a concrete index name inside that pattern"]},
        "handler": describe_es_index,
    },
    {
        "name": "query_es_sql",
        "description": (
            "Run a read-only Elasticsearch SQL query and return rows. " + _SQL_RULES +
            "Always copy an open pattern from list_es_indexes and double-quote it in FROM. "
            "Anything outside the open ranges is rejected with the list of what is available. "
            "This channel does not apply column/row data policies (the open index ranges are the only "
            "gate), so treat results as governed by configuration, not by role."
        ),
        "schema": {"sql": Annotated[str, "Single SELECT over one double-quoted index pattern, with an explicit LIMIT clause"]},
        "handler": query_es_sql,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}
