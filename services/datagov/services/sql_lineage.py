"""SQL 血缘解析 — 基于 sqlglot 的表级/字段级血缘提取.

替代旧的正则解析，支持 CTE、子查询、JOIN、CREATE TABLE AS、CREATE VIEW、
多语句、带库名前缀的表名（db.table）。

Usage:
    from services.datagov.services.sql_lineage import extract_sql_lineage
    result = extract_sql_lineage(sql, dialect="mysql")
"""

import logging

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

# 数据源类型 → sqlglot 方言
_DIALECT_MAP = {
    "mysql": "mysql",
    "doris": "mysql",   # Doris 兼容 MySQL 语法
    "starrocks": "mysql",
    "hive": "hive",
    "postgres": "postgres",
    "postgresql": "postgres",
}


def resolve_dialect(db_type: str) -> str:
    """数据源类型映射为 sqlglot 方言，未知类型返回空串（通用解析）。"""
    return _DIALECT_MAP.get((db_type or "").lower(), "")


def _qualified_name(table: exp.Table) -> str:
    """db.table / table 形式的节点标识。"""
    if table.db:
        return f"{table.db}.{table.name}"
    return table.name


def _build_alias_map(stmt) -> dict:
    """SELECT 中的表别名 → 表全名映射（用于字段归属还原）。"""
    alias_map = {}
    for t in stmt.find_all(exp.Table):
        if t.alias:
            alias_map[t.alias] = _qualified_name(t)
    return alias_map


def _extract_stmt_lineage(stmt) -> dict:
    """提取单条语句的血缘。返回 tables/edges/column_edges。"""
    cte_names = {cte.alias_or_name for cte in stmt.find_all(exp.CTE)}

    target_table = None
    target_kind = "table"
    insert_columns = []

    if isinstance(stmt, exp.Insert):
        tgt = stmt.this
        if isinstance(tgt, exp.Schema):
            insert_columns = [c.name for c in tgt.expressions]
            tgt = tgt.this
        if isinstance(tgt, exp.Table):
            target_table = _qualified_name(tgt)
    elif isinstance(stmt, exp.Create):
        tgt = stmt.this
        if isinstance(tgt, exp.Table):
            target_table = _qualified_name(tgt)
            target_kind = "view" if (stmt.args.get("kind") or "").lower() == "view" else "table"

    # 源表：排除目标表自身与 CTE 临时表
    sources = {}
    for t in stmt.find_all(exp.Table):
        name = _qualified_name(t)
        if not name or t.name in cte_names or name == target_table:
            continue
        sources[name] = {"node_type": "table"}

    edges = []
    if target_table and sources:
        for src in sources:
            edges.append({"source": src, "target": target_table})

    # ── 字段级血缘（尽力而为：显式列清单/别名可确定时生成）──
    column_edges = []
    select = stmt.expression if isinstance(stmt, (exp.Insert, exp.Create)) else None
    if target_table and isinstance(select, exp.Select):
        alias_map = _build_alias_map(select)
        projections = select.expressions
        # 目标列名：显式列清单优先，否则取投影的别名/列名
        if insert_columns and len(insert_columns) == len(projections):
            target_cols = insert_columns
        else:
            target_cols = [
                p.alias_or_name for p in projections
                if not isinstance(p, exp.Star)
            ]
            projections = [p for p in projections if not isinstance(p, exp.Star)]

        single_source = list(sources.keys())[0] if len(sources) == 1 else None
        if len(target_cols) == len(projections):
            for tgt_col, proj in zip(target_cols, projections):
                col = proj.this if isinstance(proj, exp.Alias) else proj
                if not isinstance(col, exp.Column):
                    continue  # 表达式列（聚合/函数）无法静态归因，跳过
                col_name = col.name
                src_table = None
                if col.table:
                    src_table = alias_map.get(col.table, col.table)
                    if src_table not in sources:
                        src_table = single_source
                else:
                    src_table = single_source
                if src_table and tgt_col and col_name:
                    column_edges.append({
                        "source": f"{src_table}.{col_name}",
                        "target": f"{target_table}.{tgt_col}",
                    })

    tables = list(sources.keys())
    if target_table:
        tables.append(target_table)
    return {
        "tables": tables,
        "table_types": {target_table: target_kind} if target_table else {},
        "edges": edges,
        "column_edges": column_edges,
    }


def extract_sql_lineage(sql: str, dialect: str = "mysql") -> dict:
    """解析 SQL 提取血缘。

    Returns:
        {
            "tables": [...],          # 所有涉及的表（源 + 目标）
            "table_types": {t: "table"|"view"},  # 目标节点类型
            "edges": [{"source", "target"}],     # 表级血缘边
            "column_edges": [{"source", "target"}],  # 字段级（尽力而为）
            "parse_error": None | str,
        }
    """
    result = {
        "tables": [], "table_types": {}, "edges": [], "column_edges": [],
        "parse_error": None,
    }
    try:
        stmts = sqlglot.parse(sql, read=dialect or None)
    except sqlglot.errors.ParseError as e:
        result["parse_error"] = str(e)[:500]
        return result

    seen_tables = set()
    seen_edges = set()
    seen_col_edges = set()
    for stmt in stmts:
        if stmt is None:
            continue
        try:
            r = _extract_stmt_lineage(stmt)
        except Exception as e:  # 单条语句失败不影响其他语句
            logger.warning("lineage extract stmt failed: %s", e)
            continue
        for t in r["tables"]:
            if t not in seen_tables:
                seen_tables.add(t)
                result["tables"].append(t)
        result["table_types"].update(r["table_types"])
        for edge in r["edges"]:
            key = (edge["source"], edge["target"])
            if key not in seen_edges:
                seen_edges.add(key)
                result["edges"].append(edge)
        for edge in r["column_edges"]:
            key = (edge["source"], edge["target"])
            if key not in seen_col_edges:
                seen_col_edges.add(key)
                result["column_edges"].append(edge)
    return result
