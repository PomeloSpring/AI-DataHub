"""SQL 安全解析与按作用域注入 RLS；无法识别时拒绝，不回退裸查询。"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope


def parse_query(sql: str, dialect: str = "mysql"):
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
        if len(statements) != 1 or not isinstance(statements[0], exp.Query):
            raise ValueError("仅支持单条查询")
        tree = statements[0]
        forbidden = (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock)
        if any(isinstance(node, forbidden) for node in tree.walk()):
            raise ValueError("查询包含非只读操作")
        for node in tree.find_all(exp.Table):
            if not isinstance(node.this, exp.Identifier):
                raise ValueError("不支持外部表函数")
        return tree
    except Exception as exc:
        raise PermissionError("查询结构不受支持，已拒绝执行") from exc


def table_key(node: exp.Table) -> str:
    return ".".join(part.name for part in node.parts)


def physical_references(tree):
    """只返回真实表，CTE/派生表使用 Scope 解析，不能按名字全局排除。"""
    refs = []
    try:
        for scope in traverse_scope(tree):
            for _alias, (node, source) in scope.selected_sources.items():
                if isinstance(source, exp.Table):
                    refs.append((scope, source))
    except Exception as exc:
        raise PermissionError("查询表引用不明确") from exc
    return refs


def extract_tables(sql: str, dialect: str = "mysql") -> list[str]:
    seen = set()
    tables = []
    for _scope, table in physical_references(parse_query(sql, dialect)):
        key = table_key(table)
        if key.lower() not in seen:
            seen.add(key.lower())
            tables.append(key)
    return tables


def inject_filters(sql: str, filters: dict[str, str], dialect: str = "mysql") -> tuple[str, list[str]]:
    filters = {key.lower(): value for key, value in filters.items() if value}
    if not filters:
        return sql, []
    tree = parse_query(sql, dialect)
    refs = physical_references(tree)
    applied = []
    for scope, node in refs:
        key = table_key(node)
        predicate = filters.get(key.lower())
        if not predicate:
            continue
        try:
            condition = sqlglot.parse_one(predicate, read=dialect, into=exp.Condition)
            if any(isinstance(item, (exp.Query, exp.DDL, exp.DML, exp.Command)) for item in condition.walk()):
                raise ValueError("行策略不能包含子查询或写操作")
            alias = node.alias or node.name
            base = node.copy()
            base.set("alias", None)
            for column in scope.columns:
                if column.table == node.alias_or_name and (
                    not column.db or column.db == node.db
                ) and (not column.catalog or column.catalog == node.catalog):
                    column.set("table", exp.to_identifier(alias))
                    column.set("db", None)
                    column.set("catalog", None)
            inner = exp.select("*").from_(base).where(condition)
            node.replace(inner.subquery(alias))
            if key not in applied:
                applied.append(key)
        except Exception as exc:
            raise PermissionError("行级策略无法安全应用") from exc
    return (tree.sql(dialect=dialect), applied) if applied else (sql, [])


def bounded_query(sql: str, limit: int = 1000, dialect: str = "mysql") -> str:
    """只在解析成功后补外层 LIMIT；字符串或子查询的 LIMIT 不算外层限流。"""
    tree = parse_query(sql, dialect)
    cap = max(1, int(limit))
    current = tree.args.get("limit")
    if current is None:
        tree = tree.limit(cap)
    else:
        value = current.expression
        if not isinstance(value, exp.Literal) or not value.is_int:
            raise PermissionError("查询行数限制必须为整数")
        if int(value.this) < 0:
            raise PermissionError("查询行数限制无效")
        if int(value.this) > cap:
            tree = tree.limit(cap)
    return tree.sql(dialect=dialect)
