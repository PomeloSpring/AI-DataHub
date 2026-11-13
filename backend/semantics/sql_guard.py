"""SQL 安全解析与按作用域注入 RLS；无法识别时拒绝，不回退裸查询。

方言约定（与 semantics.planner._DIALECT_OF_DBTYPE 同一口径，修改需两处同步）：
mysql/doris 同族（MySQL 协议/语法），postgres 家族含 sls（PG 协议接入）；
**es 单独立档**（见 `semantics/es_dialect.py` 的实机取证）：语法骨架走 Presto、函数渲染
改回 ES 形态，行策略走「AND 进顶层 WHERE」专支（ES 不认包裹子查询形态）。
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from backend.semantics.es_dialect import ES_DIALECT, ES_ENGINE, assert_es_renderable

# db_type -> sqlglot 方言：未知类型保守回落 mysql（与旧行为一致）。
# es 必须显式登记：落回 mysql 会把 `"appCode.keyword"` 变成字符串字面量、
# `DATE_TRUNC` 重写成 `STR_TO_DATE(CONCAT(...))` —— 语法能过、结果全错的静默错数。
_DIALECT_OF_DBTYPE = {
    "mysql": "mysql", "doris": "mysql",
    "postgres": "postgres", "postgresql": "postgres", "pg": "postgres", "sls": "postgres",
    "elasticsearch": ES_DIALECT, "es": ES_DIALECT,
}


def dialect_of_db_type(db_type: str) -> str:
    """数据源 db_type -> sqlglot 解析/回写方言。

    治理层任何对目标源 SQL 的解析回写（LIMIT 强制、RLS 注入）都必须用该源自己的
    方言；用错方言（如对 sls/presto 形状走 mysql 生成器）会把 `"quoted_id"` 变成字符串
    字面量、ARRAY[] 变 ARRAY()、BIGINT 变 SIGNED 等，导致目标源 “failed to parse query”。
    """
    return _DIALECT_OF_DBTYPE.get((db_type or "mysql").lower(), "mysql")


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


# 只读校验/结构提取的方言尝试序（SLS/Presto 形状在 mysql 下解不动，需多方言兼容）。
# doris/starrocks 入序：它们有独立语法形状（如 DISTRIBUTE BY / 专有函数列表），
# 仅靠 mysql 解析会把合法正文误判为“不受支持”。
_READONLY_DIALECT_ORDER = (
    "mysql", "postgres", "doris", "starrocks", "presto", "trino", "hive", "spark",
)


def parse_readonly(sql: str, dialect: str = None) -> "exp.Expression":
    """只读校验/结构提取用的宽容解析：优先给定方言，失败再试常见方言族。

    安全语义仍 fail-closed：单语句/无 DDL-DML/无外部表函数由每次 `parse_query` 强制
    （DDL/DML 在任一方言下都解成 DDL/DML 节点而被拒）；只有当所有方言都解不动时，
    才因“无法解析”而拒绝并透出真实原因。
    """
    order = ([dialect] if dialect else []) + [d for d in _READONLY_DIALECT_ORDER if d != dialect]
    last = None
    for d in order:
        try:
            return parse_query(sql, d)
        except Exception as e:  # noqa: BLE001 — 逐方言尝试，留最后一个错因用于报错
            last = e
    if last is not None:
        raise last
    raise PermissionError("查询结构不受支持，已拒绝执行")


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
    for _scope, table in physical_references(parse_readonly(sql, dialect)):
        key = table_key(table)
        if key.lower() not in seen:
            seen.add(key.lower())
            tables.append(key)
    return tables


def _row_predicate_condition(predicate: str, dialect: str) -> exp.Condition:
    """策略谓词→AST，并拒子查询/写操作（ES 实机不支持 IN (SELECT)/EXISTS，全引擎同样拒）。"""
    condition = sqlglot.parse_one(predicate, read=dialect, into=exp.Condition)
    if any(isinstance(item, (exp.Query, exp.DDL, exp.DML, exp.Command)) for item in condition.walk()):
        raise ValueError("行策略不能包含子查询或写操作")
    return condition


def _inject_filters_es(sql: str, filters: dict[str, str], dialect: str) -> tuple[str, list[str]]:
    """ES 形态的行策略注入：谓词 **AND 进顶层 WHERE**，不包裹子查询。

    为什么不能走通用支路（取证 2026-11-13）：通用实现把 `FROM t` 换成
    `FROM (SELECT * FROM t WHERE p) AS t`，而 ES 派生表的 `SELECT *` **不透传 `.keyword`
    多字段**（实机 400：`Unknown column [businessType.keyword], did you mean
    [businessType]?`），本仓 ES 正文全部走 `.keyword` —— 包裹形态在 ES 上必跑不通。
    ES 也没有 JOIN/派生表，所以只支持「单表单层 SELECT」；不满足即拒，
    不得只在一侧套过滤、也不得回退到未过滤正文。

    策略未命中表引用时**显式拒**（列键与 FROM 实际引用）：静默原句返回等于“看起来过了
    治理口、一条策略也没套”的假治理，是本类问题里代价最高的一种。
    """
    tree = parse_query(sql, dialect)
    refs = physical_references(tree)
    if len(refs) != 1:
        raise PermissionError(
            f"ES 行级策略只支持单表查询（ES SQL 无 JOIN/派生表），当前正文引用 {len(refs)} 张表，已拒绝执行")
    _scope, node = refs[0]
    key = table_key(node)
    predicate = filters.get(key.lower())
    if not predicate:
        # 兼容同一目标的两种写法（索引别名 ↔ `t-{别名}*` 模式）：策略按哪个登都能命中，
        # 但两个都不匹配时必须拒（而不是原句放行）。
        # 多键归一后指向同一目标时 **AND 合并**，不得拿第一个命中就当结果（那会按字典序
        # 静默丢掉一条策略）；与通用支路的同表多策略 AND 口径一致。
        from backend.core.es_client import es_alias_of

        alias = es_alias_of(key).lower()
        matched = [v for k, v in filters.items() if es_alias_of(k).lower() == alias and v]
        predicate = "(" + ") AND (".join(matched) + ")" if len(matched) > 1 else (matched[0] if matched else None)
    if not predicate:
        raise PermissionError(
            f"行级策略未能应用到 ES 查询：策略登记的表 {sorted(filters)} 与正文 FROM 引用「{key}」"
            f"不一致（ES 的 FROM 目标是索引别名或其 `t-别名*` 模式写法，必须逐字对齐），已拒绝执行")
    try:
        condition = _row_predicate_condition(predicate, dialect)
    except Exception as exc:
        raise PermissionError("行级策略无法安全应用") from exc

    where = tree.args.get("where")
    if where is not None:
        where.set("this", exp.and_(where.this, condition, copy=False))
    else:
        tree.set("where", exp.Where(this=condition))
    return assert_es_renderable(tree.sql(dialect=dialect)), [key]


def inject_filters(sql: str, filters: dict[str, str], dialect: str = "mysql",
                   engine: str = "") -> tuple[str, list[str]]:
    """按目标源注入行级过滤。

    engine='es' 走专支（顶层 WHERE AND）：ES 不认通用的包裹子查询形态。
    其余引擎保持现有行为：只改写命中策略的表引用，未命中时原句返回 + applied 为空，
    由调用方（`permission_enforcer.enforce_sql`）统一 fail-loud。
    """
    filters = {key.lower(): value for key, value in filters.items() if value}
    if not filters:
        return sql, []
    if (engine or "").strip().lower() == ES_ENGINE:
        return _inject_filters_es(sql, filters, dialect or ES_DIALECT)
    tree = parse_query(sql, dialect)
    refs = physical_references(tree)
    applied = []
    for scope, node in refs:
        key = table_key(node)
        # 先按原始引用形式（可能带 db/catalog 限定）命中，再按裸表名兼容回落：
        # 策略在元库按裸表名登记，而正文可能写 `db.t`（护栏 §10）。
        # 回落方向是"多套一层过滤"而非"少套"，与 fail-closed 一致。
        predicate = filters.get(key.lower()) or filters.get(node.name.lower())
        if not predicate:
            continue
        try:
            condition = _row_predicate_condition(predicate, dialect)
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
    """只在解析成功后补外层 LIMIT；字符串或子查询的 LIMIT 不算外层限流。

    外层已有合规 LIMIT（整数且 ≤ cap）时**原样返回不重生成**：跨方言下 sqlglot 回写
    会破坏目标源专有语法（如 sls/presto 的 ARRAY[]/双引号标识符/interval 字面量），
    无需修改就不要动 SQL。
    """
    tree = parse_query(sql, dialect)
    cap = max(1, int(limit))
    current = tree.args.get("limit")
    if current is not None:
        value = current.expression
        if not isinstance(value, exp.Literal) or not value.is_int:
            raise PermissionError("查询行数限制必须为整数")
        if int(value.this) < 0:
            raise PermissionError("查询行数限制无效")
        if int(value.this) <= cap:
            return sql
        tree = tree.limit(cap)
    else:
        tree = tree.limit(cap)
    return tree.sql(dialect=dialect)
