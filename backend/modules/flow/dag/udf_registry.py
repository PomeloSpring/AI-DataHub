"""UDF 注册中心 — SQL 表达式 UDF（标量）的校验、版本与 AST 展开。

表达式 UDF 的实现层是 **SQL AST 宏展开**（sqlglot），不是引擎逐行 UDF：
函数体是纯表达式，调用处把实参表达式代入参数位后内联展开，
展开后的 SQL 只含内置函数，可直接下推 DataFusion / 数据源执行。

安全与一致性口径：
- 表达式必须是**纯表达式**：禁子查询、表引用、DDL/DML、多语句；
  表达式中的列引用必须 ⊆ 参数名集合（纯函数无表列捕获，展开无歧义）；
- UDF 调 UDF 仅允许引用**已启用**的其他 UDF，展开递归深度受限（防环）；
- 生效版本 = adh_udfs 中 is_current=1 的行；编辑产生新版本行，
  历史 run 的 node_config 锁定版本号，变更不回溯影响历史运行；
- 校验失败一律显式报错（fail-loud），不静默跳过 UDF 调用。
"""

import json
import logging
import re
import time as _time
from typing import Optional

import sqlglot
from sqlglot import exp

from backend.common.db import execute_query, execute_write

logger = logging.getLogger(__name__)

UDF_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
MAX_EXPAND_DEPTH = 5

# 允许的 UDF 形态：scalar_expr 纯表达式宏；lookup 查表型（只读标量子查询模板，如 ip2geo）
UDF_KINDS = ("scalar_expr", "lookup")

# sqlglot 不认识的引擎内置函数白名单（MySQL 方言，以 Anonymous 形态出现）：
# 在此名单内的视为内置函数放行；否则视为未注册 UDF 调用拒绝（拦 typo）。
_KNOWN_BUILTIN_FUNCS = {
    "inet_aton", "inet_ntoa", "inet6_aton", "inet6_ntoa",
    "str_to_date", "date_format", "unix_timestamp", "from_unixtime",
    "substring_index", "substring", "mid", "locate", "instr",
    "lpad", "rpad", "repeat", "replace", "trim", "ltrim", "rtrim",
    "abs", "round", "ceil", "floor", "ceiling", "mod", "pow", "power",
    "greatest", "least", "coalesce", "ifnull", "nullif", "if",
    "cast", "convert", "char", "concat_ws", "group_concat",
    "json_extract", "json_unquote", "md5", "sha", "sha2",
}

from backend.semantics.sql_guard import parse_query  # noqa: E402  只读校验共用


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(_time.time() * 1000000)


class UdfValidationError(ValueError):
    """UDF 定义/引用不合法（对外可展示的错误信息）。"""


# ── 表达式校验 ─────────────────────────────────────────────────────


def validate_expression(expression: str, params: list, known_udfs: set = None,
                        udf_kind: str = "scalar_expr") -> exp.Expression:
    """校验表达式 UDF 定义，返回解析后的 AST。

    规则（违反即 UdfValidationError）：
    1. 必须是单个纯表达式（非 SELECT 顶层/DDL/DML/多语句）；
    2. scalar_expr：不得含子查询、表引用；lookup：允许**只读标量子查询 + 表引用**
       （查表型函数如 ip2geo，模板经只读校验，子查询内列不参与参数检查）；
    3. 非子查询区域的列引用必须 ⊆ 参数名（纯函数不捕获表列）；
    4. 函数调用只能是内置函数或 known_udfs 中已启用的 UDF。
    """
    params = params or []
    param_names = {str(p.get("name", "")).lower() for p in params if p.get("name")}
    if not param_names and params:
        raise UdfValidationError("UDF 参数必须包含 name 字段")
    for name in param_names:
        if not UDF_NAME_RE.match(name):
            raise UdfValidationError(f"UDF 参数名不合法: {name}")

    try:
        parsed = sqlglot.parse(expression, read="mysql")
    except Exception as exc:
        raise UdfValidationError(f"表达式语法错误: {exc}") from exc
    statements = [s for s in parsed if s is not None]
    if len(statements) != 1:
        raise UdfValidationError("UDF 表达式必须是单条表达式")
    tree = statements[0]
    allow_lookup = udf_kind == "lookup"
    if isinstance(tree, exp.Query):
        # lookup 型允许“带括号标量子查询”作为表达式体（如 ip2geo）；
        # 裸 SELECT 语句仍拒（那是查询不是表达式）
        if not (allow_lookup and isinstance(tree, exp.Subquery)):
            raise UdfValidationError("UDF 表达式不允许顶层 SELECT")
    if any(isinstance(node, (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock))
           for node in tree.walk()):
        raise UdfValidationError("UDF 表达式不允许写操作或命令")

    known = {n.lower() for n in (known_udfs or set())}
    if allow_lookup:
        # lookup 型：严格三元组模板（SELECT <结果表达式> FROM <映射表> WHERE <匹配条件>），
        # 展开时改写为 LEFT JOIN（表数据下推扫描，表达式在 DataFusion 计算，
        # 不使用标量子查询——DataFusion 物理层不支持 ScalarSubquery）
        tpl = _extract_lookup_template(expression)
        tpl_alias = tpl["table"].alias_or_name
        for region in (tpl["result"], tpl["match"]):
            for node in region.walk():
                if isinstance(node, (exp.Subquery, exp.Query)):
                    raise UdfValidationError("lookup 模板不允许嵌套子查询")
                if isinstance(node, exp.Anonymous):
                    fname = (node.name or "").lower()
                    if fname and fname not in known and fname not in _KNOWN_BUILTIN_FUNCS:
                        raise UdfValidationError(f"UDF 表达式调用了未注册函数 '{node.name}'")
                if isinstance(node, exp.Column):
                    col = node.name.lower()
                    if col in param_names:
                        continue
                    if node.table and node.table.lower() != tpl_alias.lower():
                        raise UdfValidationError(
                            f"lookup 模板引用了未知限定列 '{node.name}'")
        return tree

    for node in tree.walk():
        if isinstance(node, exp.Table):
            raise UdfValidationError("UDF 表达式不允许表引用")
        if isinstance(node, exp.Subquery):
            raise UdfValidationError("UDF 表达式不允许子查询")
        if isinstance(node, exp.Column):
            col = node.name.lower()
            if col and col not in param_names:
                raise UdfValidationError(
                    f"UDF 表达式引用了非参数列 '{node.name}'（纯表达式 UDF 不得引用表列）")
        if isinstance(node, exp.Anonymous):
            fname = (node.name or "").lower()
            # 白名单内置函数放行；其余匿名函数视为未注册 UDF（拦 typo/未声明引用）
            if fname and fname not in known and fname not in _KNOWN_BUILTIN_FUNCS:
                raise UdfValidationError(f"UDF 表达式调用了未注册函数 '{node.name}'")
    return tree


def _extract_lookup_template(expression: str) -> dict:
    """解析 lookup 型模板：(SELECT <结果表达式> FROM <映射表> [AS a] WHERE <匹配条件> [LIMIT n])。

    返回 {table: exp.Table, result: Expression, match: Expression}。
    形态不合规显式报错（模板是受控结构，展开时改写为 LEFT JOIN）。
    """
    tree = sqlglot.parse_one(expression, read="mysql")
    if not isinstance(tree, exp.Subquery) or not isinstance(tree.this, exp.Select):
        raise UdfValidationError(
            "lookup 型 UDF 必须是带括号的单条 SELECT 子查询模板")
    sel = tree.this
    if len(sel.expressions) != 1:
        raise UdfValidationError("lookup 模板必须恰有一个结果表达式")
    frm = sel.args.get("from_") or sel.args.get("from")
    if not frm or not isinstance(frm.this, exp.Table):
        raise UdfValidationError("lookup 模板必须有单张映射表")
    if sel.args.get("joins"):
        raise UdfValidationError("lookup 模板不允许自带 JOIN")
    where = sel.args.get("where")
    if where is None or where.this is None:
        raise UdfValidationError("lookup 模板必须有匹配条件（WHERE）")
    return {"table": frm.this, "result": sel.expressions[0], "match": where.this}


def _params_signature(params: list) -> list:
    return [{"name": str(p.get("name", "")).lower(),
             "type": str(p.get("type") or "any")} for p in (params or [])]


# ── 注册表访问 ─────────────────────────────────────────────────────


class UdfRegistry:
    """adh_udfs 多版本注册表访问（is_current=1 为生效版本）。"""

    def get_active(self, name: str) -> Optional[dict]:
        row = execute_query(
            "SELECT * FROM adh_udfs WHERE name = %s AND is_current = 1",
            (name,), fetchone=True)
        if row:
            row["params"] = json.loads(row["params"]) if isinstance(row.get("params"), str) else (row.get("params") or [])
        return row

    def get_version(self, name: str, version: int) -> Optional[dict]:
        row = execute_query(
            "SELECT * FROM adh_udfs WHERE name = %s AND version = %s",
            (name, version), fetchone=True)
        if row:
            row["params"] = json.loads(row["params"]) if isinstance(row.get("params"), str) else (row.get("params") or [])
        return row

    def list_udfs(self, include_history: bool = False) -> list:
        where = "" if include_history else "WHERE is_current = 1"
        rows = execute_query(
            f"SELECT id, name, udf_kind, expression, params, return_type, description, "
            f" is_active, is_current, version, owner_id, workspace_id, usage_count, "
            f" created_at, updated_at FROM adh_udfs {where} ORDER BY name ASC, version DESC") or []
        for row in rows:
            row["params"] = json.loads(row["params"]) if isinstance(row.get("params"), str) else (row.get("params") or [])
        return rows

    def create_udf(self, data: dict, owner_id: int, workspace_id: int = 0) -> int:
        name = str(data.get("name", "")).strip().lower()
        if not UDF_NAME_RE.match(name):
            raise UdfValidationError("UDF 名称须为小写字母/数字/下划线，且不以数字开头")
        udf_kind = str(data.get("udf_kind") or "scalar_expr")
        if udf_kind not in UDF_KINDS:
            raise UdfValidationError(f"UDF 类型不合法: {udf_kind}")
        params = _params_signature(data.get("params"))
        known = {r["name"] for r in self.list_udfs()}
        validate_expression(data["expression"], params, known_udfs=known, udf_kind=udf_kind)
        if self.get_active(name):
            raise UdfValidationError(f"UDF '{name}' 已存在（请用 update 产生新版本）")
        udf_id = _generate_id()
        execute_write(
            "INSERT INTO adh_udfs (id, name, udf_kind, expression, params, return_type, "
            " description, is_active, is_current, version, owner_id, workspace_id, "
            " created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 1, 1, %s, %s, %s, %s)",
            (udf_id, name, udf_kind, data["expression"], json.dumps(params),
             data.get("return_type") or "auto", data.get("description", ""),
             owner_id, workspace_id, _now(), _now()))
        return udf_id

    def update_udf(self, name: str, data: dict) -> int:
        """编辑 = 新版本行 + 旧行 is_current=0（历史 run 不受影响）。"""
        current = self.get_active(name)
        if not current:
            raise UdfValidationError(f"UDF '{name}' 不存在")
        params = _params_signature(data.get("params", current.get("params")))
        expression = data.get("expression", current["expression"])
        udf_kind = str(data.get("udf_kind") or current.get("udf_kind") or "scalar_expr")
        known = {r["name"] for r in self.list_udfs()}
        validate_expression(expression, params, known_udfs=known, udf_kind=udf_kind)
        new_version = int(current.get("version") or 1) + 1
        execute_write("UPDATE adh_udfs SET is_current = 0 WHERE name = %s AND is_current = 1", (name,))
        udf_id = _generate_id()
        execute_write(
            "INSERT INTO adh_udfs (id, name, udf_kind, expression, params, return_type, "
            " description, is_active, is_current, version, owner_id, workspace_id, "
            " usage_count, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s)",
            (udf_id, name, udf_kind, expression,
             json.dumps(params), data.get("return_type", current.get("return_type") or "auto"),
             data.get("description", current.get("description") or ""),
             1 if data.get("is_active", current.get("is_active", True)) else 0,
             new_version, current.get("owner_id") or 0, current.get("workspace_id") or 0,
             current.get("usage_count") or 0, _now(), _now()))
        return udf_id

    def set_active(self, name: str, is_active: bool) -> bool:
        return execute_write(
            "UPDATE adh_udfs SET is_active = %s WHERE name = %s AND is_current = 1",
            (1 if is_active else 0, name)) == 1

    def delete_udf(self, name: str) -> dict:
        """删除保护：被 DAG 引用的 UDF 禁删只可停用（引用一致性，宁阻断勿悬空）。"""
        dependents = self.dependents(name)
        if dependents:
            raise ValueError(
                f"UDF '{name}' 仍被 {len(dependents)} 个工作流引用，不能删除；请改为停用")
        return {"deleted": execute_write("DELETE FROM adh_udfs WHERE name = %s", (name,))}

    def dependents(self, name: str) -> list:
        """引用该 UDF 的工作流（graph_json LIKE 保守匹配 + udf_refs 精确复核）。"""
        rows = execute_query(
            "SELECT id, name, graph_json FROM adh_dag_workflows "
            "WHERE graph_json LIKE %s", (f'%{name}%',)) or []
        found = []
        for row in rows:
            graph = row.get("graph_json")
            if isinstance(graph, str):
                try:
                    graph = json.loads(graph)
                except (json.JSONDecodeError, TypeError):
                    continue
            for node in (graph or {}).get("nodes", []):
                refs = (node.get("config") or {}).get("udf_refs") or []
                if any(str(r).split(":")[0] == name for r in refs):
                    found.append({"workflow_id": row["id"], "workflow_name": row["name"],
                                  "node_key": node.get("key")})
                    break
        return found

    def bump_usage(self, names: list) -> None:
        for name in set(names or []):
            execute_write("UPDATE adh_udfs SET usage_count = usage_count + 1 "
                          "WHERE name = %s AND is_current = 1", (name,))

    def known_udf_names(self) -> set:
        """当前注册的 UDF 名集合（未声明引用检查用）。"""
        rows = execute_query("SELECT name FROM adh_udfs WHERE is_current = 1") or []
        return {row["name"] for row in rows}


# ── UDF 展开（AST 宏展开） ──────────────────────────────────────────


def _load_definitions(udf_refs: list) -> dict:
    """按 udf_refs 解析生效版本定义：['name'] 或 ['name:version']。

    引用不存在/未启用/版本缺失 → 显式报错（不静默跳过）。
    """
    defs = {}
    for ref in udf_refs or []:
        ref = str(ref).strip()
        name, _, ver = ref.partition(":")
        if ver:
            row = udf_registry.get_version(name, int(ver))
            if not row:
                raise UdfValidationError(f"UDF 引用不存在: {ref}")
        else:
            row = udf_registry.get_active(name)
            if not row:
                raise UdfValidationError(f"UDF '{name}' 不存在或未启用")
            if not row.get("is_active"):
                raise UdfValidationError(f"UDF '{name}' 已停用")
        defs[name.lower()] = row
    return defs


def expand_udfs(sql: str, udf_refs: list) -> tuple:
    """展开 SQL 中的 UDF 调用为内联表达式。

    返回 (expanded_sql, used_versions)；used_versions = [{name, version, id}]
    供运行实例锁定 UDF 版本。引用了未在 udf_refs 声明的 UDF → 报错
    （声明式引用，血缘可追溯）。
    """
    defs = _load_definitions(udf_refs)
    try:
        statements = [s for s in sqlglot.parse(sql, read="mysql") if s is not None]
    except Exception as exc:
        raise UdfValidationError(f"SQL 语法错误: {exc}") from exc
    if not statements:
        raise UdfValidationError("SQL 为空")

    used: dict = {}
    expanded: list = []
    for tree in statements:
        expanded.append(_expand_tree(tree, defs, used, depth=0))
    return ";\n".join(t.sql(dialect="mysql") for t in expanded), list(used.values())


def _expand_tree(tree: exp.Expression, defs: dict, used: dict, depth: int) -> exp.Expression:
    if depth > MAX_EXPAND_DEPTH:
        raise UdfValidationError("UDF 嵌套展开层数超限（可能存在递归定义）")
    # 先递归展开内层 UDF（UDF 调 UDF）
    for node in list(tree.find_all(exp.Anonymous)):
        fname = (node.name or "").lower()
        if fname in defs:
            _expand_call(node, defs, used, depth)
            continue
        # 声明式引用：名称命中注册表但未在 udf_refs 声明 → 拒绝（血缘可追溯）；
        # 内置函数不在注册表，正常跳过
        if fname and fname in udf_registry.known_udf_names():
            raise UdfValidationError(
                f"SQL 引用了未声明的 UDF '{fname}'，请在 udf_refs 中声明")
    return tree


def _expand_call(call: exp.Anonymous, defs: dict, used: dict, depth: int) -> None:
    fname = (call.name or "").lower()
    defn = defs[fname]
    params = defn.get("params") or []
    args = list(call.args.get("expressions") or [])
    if len(args) != len(params):
        raise UdfValidationError(
            f"UDF '{fname}' 需要 {len(params)} 个参数，实际 {len(args)} 个")
    substitutions = {str(p.get("name", "")).lower(): a.copy()
                     for p, a in zip(params, args)}

    # lookup 型（查表映射）：改写为 LEFT JOIN（表数据下推扫描，
    # 表达式在 DataFusion 计算；不使用标量子查询，其物理执行不受支持）
    if defn.get("udf_kind") == "lookup":
        _expand_lookup_call(call, fname, defn, substitutions, used)
        return

    try:
        body = sqlglot.parse_one(defn["expression"], read="mysql")
    except Exception as exc:
        raise UdfValidationError(f"UDF '{fname}' 定义解析失败: {exc}") from exc
    if body is None:
        raise UdfValidationError(f"UDF '{fname}' 定义为空")

    # 递归展开定义体内部的 UDF 调用
    for inner in list(body.find_all(exp.Anonymous)):
        iname = (inner.name or "").lower()
        if iname in defs:
            _expand_call(inner, defs, used, depth + 1)

    # 实参代入参数位（body 中非子查询列引用已被 validate 约束为 ⊆ 参数名）
    for col in list(body.find_all(exp.Column)):
        target = substitutions.get(col.name.lower())
        if target is None:
            raise UdfValidationError(f"UDF '{fname}' 定义引用了未声明参数 '{col.name}'")
        col.replace(target.copy())

    used[fname] = {"name": fname, "version": defn.get("version"),
                   "id": defn.get("id")}
    call.replace(body)


def _expand_lookup_call(call: exp.Anonymous, fname: str, defn: dict,
                        substitutions: dict, used: dict) -> None:
    """lookup 展开：`f(x)` → 所在查询 LEFT JOIN 映射表（别名 _udf_f*）
    ON <匹配条件 x→实参>，调用处替换为 <结果表达式 列→别名列>。

    架构口径：映射表与主表的数据扫描经 RemoteSqlTable 下推拉取，
    JOIN 匹配与结果表达式在 DataFusion 层计算（引擎级 UDF + 表达式）。
    """
    tpl = _extract_lookup_template(defn["expression"])
    tpl_table = tpl["table"]
    outer = call.find_ancestor(exp.Select)
    if outer is None:
        raise UdfValidationError(f"lookup UDF '{fname}' 只能出现在 SELECT 查询中")

    # 生成唯一 join 别名（与已有表别名/既有 join 别名不冲突）
    taken = set()
    frm = outer.args.get("from_") or outer.args.get("from")
    if frm and isinstance(frm.this, exp.Table):
        taken.add(frm.this.alias_or_name.lower())
    for j in outer.args.get("joins") or []:
        taken.add(j.alias_or_name.lower())
    base = f"_udf_{fname}"
    alias, idx = base, 0
    while alias.lower() in taken:
        idx += 1
        alias = f"{base}_{idx}"

    match = tpl["match"].copy()
    result = tpl["result"].copy()
    for region in (match, result):
        for col in list(region.find_all(exp.Column)):
            target = substitutions.get(col.name.lower())
            if target is not None:
                col.replace(target.copy())
            else:
                # 映射表列：统一挂到生成的 join 别名下
                col.set("table", exp.to_identifier(alias))
                col.set("db", None)
                col.set("catalog", None)

    join_table = exp.table_(tpl_table.name, alias=alias)
    # copy=False 就地改写原树（默认 copy=True 返回新树不会回写）
    outer.join(join_table, on=match, join_type="LEFT", copy=False)
    used[fname] = {"name": fname, "version": defn.get("version"),
                   "id": defn.get("id")}
    call.replace(result)

    # JOIN 后映射表同名列会使主表裸列歧义（如 city）：
    # 统一把本层无表限定的列挂到主表名下（原语义不变，消歧义）；
    # 嵌套子查询内部的列属于内层表，不动
    if frm and isinstance(frm.this, exp.Table):
        main_name = frm.this.alias_or_name
        for col in list(outer.find_all(exp.Column)):
            if not col.table and col.find_ancestor(exp.Select) is outer:
                col.set("table", exp.to_identifier(main_name))


udf_registry = UdfRegistry()
