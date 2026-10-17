"""SQL 数据集编译（L1）—— 参数渲染 + 方言正文 + 输出列聚合 + 静态提表。

放在 semantics 层（而非 modules/viz）的原因：语义层 planner 与数据集服务都要消费
同一份编译结果，而 `backend/semantics` 是 L1、不得反向依赖 `backend/modules/*`。
两处共用一份实现，避免"看板能查、AI 查出来的形状不一样"的口径漂移。

编译形状（两级）::

    SELECT <维度列>, <度量聚合>          ← 外层：按 field_config 声明的维度/度量聚合
    FROM (
        <数据集正文：参数已渲染，含两级聚合/窗口函数/自关联>
    ) AS _ds_base
    WHERE <字段过滤>
    GROUP BY <维度列> ORDER BY ... LIMIT ...

分工与 `no-silent-degradation` 一致：
- 人（建模者）负责 SQL 形状 —— 语义层编译不出来的两级聚合由正文自己完成；
- 机器负责确定性：未声明的参数键/缺参/类型不符/无本方言实现/未声明的输出列
  **一律显式拒绝或回抛候选**，不猜列、不回退别的方言正文、不用默认值凑一条能跑的 SQL。
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Optional

from backend.semantics import dialect as sem_dialect
from backend.semantics.sql_params import (
    declared_names, literal, parse_json_obj, parse_variables, placeholder_names,
    pick_body, render_body,
)

# 内层保底 LIMIT：外层过滤/聚合前不拉回过多行（与数据集服务同口径）
INNER_LIMIT = 5000
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000

# 过滤算子白名单（与语义层 intent filters 对齐）：比较类走符号表，其余单独渲染
_FILTER_SYMBOLS = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_FILTER_OPS = set(_FILTER_SYMBOLS) | {"in", "nin", "like", "between", "is_null", "is_not_null"}
_AGG_FNS = {"SUM", "COUNT", "COUNT_DISTINCT", "MAX", "MIN", "AVG", "NONE"}

# 时间粒度模板（与 planner 同一张表：MySQL 族 DATE_FORMAT / PG 族 to_char）
_GRAIN_MYSQL = {"second": "%Y-%m-%d %H:%i:%s", "minute": "%Y-%m-%d %H:%i",
                "hour": "%Y-%m-%d %H", "day": "%Y-%m-%d", "week": "%x-%v",
                "month": "%Y-%m", "quarter": "%Y-Q%q", "year": "%Y"}
_GRAIN_PG = {"second": "YYYY-MM-DD HH24:MI:SS", "minute": "YYYY-MM-DD HH24:MI",
             "hour": "YYYY-MM-DD HH24", "day": "YYYY-MM-DD", "week": "IYYY-IW",
             "month": "YYYY-MM", "quarter": 'YYYY-"Q"Q', "year": "YYYY"}


class DatasetCompileError(ValueError):
    """数据集编译拒绝（参数非法/无本方言正文/缺 SQL）。调用方必须原样暴露给用户。"""


@dataclass
class CompiledDataset:
    """编译产物：可执行 SQL + 治理消费件（触及物理表、告警）。"""

    sql: str = ""
    resolved_tables: list[str] = _dc_field(default_factory=list)
    warnings: list[str] = _dc_field(default_factory=list)
    fields: list[dict] = _dc_field(default_factory=list)
    # 归因：请求名 → 解析来源（供评测按档位分桶，不对外暴露物理细节）
    resolution_sources: dict[str, str] = _dc_field(default_factory=dict)
    unresolved: list[dict] = _dc_field(default_factory=list)


def _alias(name: str) -> str:
    """投影别名：与语义层同规则（非法字符换成 _），保证 ORDER BY/结果列名可预期。"""
    s = str(name or "").strip()
    return s or "col"


class _FieldMDL:
    """数据集输出列声明（`field_config` 的 config 项）= SQL 结果的 mini-MDL。

    每项：{field: SQL 输出列名, label: 业务名, role: dimension|measure, is_time?, agg?, expr?}
    度量优先 expr（跨列派生，如层间转化率），其次 agg（缺省 SUM）。
    只认声明列：未声明的名字回抛候选（unresolved），绝不猜成物理列。
    """

    def __init__(self, columns: Any, family: str):
        self.family = family
        self.by_key: dict[str, dict] = {}
        self.labels: list[str] = []
        for c in (parse_json_obj(columns, []) or []):
            if not isinstance(c, dict):
                continue
            if str(c.get("from") or "") == "semantic":
                continue          # 语义字典项描述的是本体字段，不是 SQL 输出列
            col = str(c.get("sql_column") or c.get("field") or "").strip()
            label = str(c.get("label") or c.get("field") or col).strip()
            if not col:
                continue
            role = str(c.get("role") or "dimension").strip().lower()
            if c.get("is_time") and role == "dimension":
                role = "time"
            decl = {
                "column": col, "label": label, "role": role,
                "agg": str(c.get("agg") or "").strip().upper(),
                "expr": str(c.get("expr") or c.get("calculation") or "").strip(),
                "unit": str(c.get("unit") or ""),
            }
            if decl["agg"] and decl["agg"] not in _AGG_FNS:
                decl["agg"] = "SUM"
            for key in (label, col, str(c.get("field") or "")):
                if key:
                    self.by_key.setdefault(str(key).strip().casefold(), decl)
            self.labels.append(label)

    @property
    def empty(self) -> bool:
        return not self.by_key

    def lookup(self, name: str) -> Optional[dict]:
        return self.by_key.get(str(name or "").strip().casefold())

    def ref(self, decl: dict) -> str:
        return sem_dialect.quote_ident(decl["column"], self.family)

    def dimension(self, name: str, time_grain: Optional[str] = None):
        """维度/时间列 → (表达式, 角色)；未声明或其实是度量 → (None, None)。"""
        decl = self.lookup(name)
        if not decl or decl["role"] == "measure":
            return None, None
        ref = self.ref(decl)
        if decl["role"] == "time" and time_grain:
            fmt = (_GRAIN_PG if self.family == "postgres" else _GRAIN_MYSQL).get(time_grain)
            if fmt:
                fn = "to_char" if self.family == "postgres" else "DATE_FORMAT"
                return f"{fn}({ref}, '{fmt}')", "time"
        return ref, ("time" if decl["role"] == "time" else "dimension")

    def measure(self, name: str) -> Optional[str]:
        decl = self.lookup(name)
        if not decl or decl["role"] != "measure":
            return None
        if decl["expr"]:
            return decl["expr"]
        ref = self.ref(decl)
        agg = decl["agg"] or "SUM"
        if agg == "NONE":
            return ref
        if agg == "COUNT_DISTINCT":
            return f"COUNT(DISTINCT {ref})"
        return f"{agg}({ref})"

    def filter(self, spec: dict) -> Optional[str]:
        name = str(spec.get("field") or spec.get("dim") or "").strip()
        decl = self.lookup(name)
        if not decl or decl["role"] == "measure":
            return None
        return _render_condition(self.ref(decl), str(spec.get("op") or "eq"),
                                 spec.get("value"), spec.get("values"))


def _render_condition(lhs: str, op: str, value: Any, values: Any) -> Optional[str]:
    if op == "is_null":
        return f"{lhs} IS NULL"
    if op == "is_not_null":
        return f"{lhs} IS NOT NULL"
    if op in ("in", "nin"):
        arr = values if isinstance(values, list) else (value if isinstance(value, list) else [])
        if not arr:
            return None
        joined = ", ".join(literal(v) for v in arr[:1000])
        return f"{lhs} {'IN' if op == 'in' else 'NOT IN'} ({joined})"
    if op == "between":
        arr = values if isinstance(values, list) else (value if isinstance(value, list) else [])
        if len(arr) < 2:
            return None
        return f"{lhs} BETWEEN {literal(arr[0])} AND {literal(arr[1])}"
    sym = _FILTER_SYMBOLS.get(op)
    if not sym:
        return None
    if op == "like":
        return f"{lhs} LIKE {literal('%' + str(value) + '%')}"
    return f"{lhs} {sym} {literal(value)}"


def _clamp_limit(raw: Any) -> int:
    try:
        n = int(raw) if raw not in (None, "") else DEFAULT_LIMIT
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def compile_dataset(
    ds: dict,
    *,
    args: Optional[dict] = None,
    dimensions: Optional[list] = None,
    measures: Optional[list] = None,
    filters: Optional[list] = None,
    order: Optional[list] = None,
    limit: Any = None,
    time_grain: Optional[str] = None,
    db_type: str = "mysql",
) -> CompiledDataset:
    """把 SQL 数据集编译成一条可执行查询。非法输入抛 `DatasetCompileError`（fail-loud）。

    ds: adh_datasets 行（需含 sql_query / variables / sql_bodies / field_config）。
    """
    family = sem_dialect.family_of(db_type)
    engine = sem_dialect.engine_of(db_type)
    out = CompiledDataset(fields=[])

    body, err = pick_body(ds.get("sql_bodies"), ds.get("sql_query") or "", engine, family)
    if err:
        raise DatasetCompileError(f"数据集「{ds.get('name')}」{err}")
    if not body:
        raise DatasetCompileError(f"数据集「{ds.get('name')}」缺少执行 SQL")

    variables = parse_variables(ds.get("variables"))
    known = declared_names(body, variables)
    args = dict(args or {})
    unknown = [k for k in args if k not in known]
    if unknown:
        raise DatasetCompileError(
            f"参数 {sorted(unknown)} 未在数据集声明（可用参数: "
            f"{sorted(placeholder_names(body)) or '无'}）")
    rendered, errors = render_body(body, variables, args)
    if errors:
        raise DatasetCompileError("；".join(errors[:5]))

    # 内层保底 LIMIT（正文自己没写 LIMIT 时补，避免外层聚合前拉全表）
    if "limit" not in rendered.lower():
        rendered = f"{rendered.rstrip().rstrip(';')} LIMIT {INNER_LIMIT}"

    mdl = _FieldMDL(ds.get("field_config"), family)
    inner = f"(\n{rendered}\n) AS _ds_base"
    if not mdl.empty and (dimensions or measures):
        select_items: list[tuple[str, str]] = []
        group_by: list[str] = []
        for d in dimensions or []:
            expr, role = mdl.dimension(d, time_grain)
            if expr is None:
                out.warnings.append(
                    f"无法解析维度「{d}」, 已跳过。数据集可用字段: {mdl.labels}")
                out.unresolved.append({"term": d, "type": "dimension",
                                       "reason": "dataset_field_unresolved"})
                continue
            alias = _alias(d)
            select_items.append((expr, alias))
            group_by.append(expr)
            out.resolution_sources[str(d)] = "dataset_field"
        for m in measures or []:
            expr = mdl.measure(m)
            if expr is None:
                out.warnings.append(
                    f"无法解析指标「{m}」, 已跳过。数据集可用字段: {mdl.labels}")
                out.unresolved.append({"term": m, "type": "metric",
                                       "reason": "dataset_field_unresolved"})
                continue
            alias = _alias(m)
            select_items.append((expr, alias))
            out.resolution_sources[str(m)] = "dataset_field"
        if select_items:
            where = _where_from(mdl, filters, out)
            order_by = _order_from(order, select_items, family)
            # 别名必须始终包引号：业务名可能含 ()/% 等字符（如「层间转化率(%)」）
            parts = [f"SELECT {', '.join(f'{e} AS {sem_dialect.quote_alias(a, family)}' for e, a in select_items)}",
                     f"FROM {inner}"]
            if where:
                parts.append(f"WHERE {where}")
            if group_by:
                parts.append(f"GROUP BY {', '.join(group_by)}")
            if order_by:
                parts.append(f"ORDER BY {order_by}")
            parts.append(f"LIMIT {_clamp_limit(limit)}")
            out.sql = "\n".join(parts)
        else:
            out.warnings.append("数据集字段声明上未解析出任何维度/度量, 回落明细形状")
            out.sql = f"SELECT * FROM {inner}" + f" LIMIT {_clamp_limit(limit)}"
    else:
        if not mdl.empty and not (dimensions or measures):
            out.warnings.append("数据集未声明维度/度量, 返回明细行（要聚合请传 dimensions/measures）")
        elif mdl.empty and (dimensions or measures):
            out.warnings.append(
                "数据集未声明字段定义(field_config), 忽略 dimensions/measures: "
                "聚合形状由正文决定, 请在字段定义里声明维度/度量")
        where = _where_from(mdl, filters, out) if not mdl.empty else None
        out.sql = f"SELECT * FROM {inner}" + (f"\nWHERE {where}" if where else "") \
            + f"\nLIMIT {_clamp_limit(limit)}"

    out.resolved_tables = _touched(rendered, family, out)
    return out


def _where_from(mdl: _FieldMDL, filters: Optional[list], out: CompiledDataset) -> str:
    parts: list[str] = []
    for f in filters or []:
        if not isinstance(f, dict):
            continue
        cond = mdl.filter(f)
        if cond:
            parts.append(cond)
        else:
            name = str(f.get("field") or f.get("dim") or "")
            out.warnings.append(f"无法解析过滤字段「{name}」。数据集可用字段: {mdl.labels}")
            out.unresolved.append({"term": name, "type": "dimension",
                                   "reason": "dataset_field_unresolved"})
    return " AND ".join(parts)


def _order_from(order: Optional[list], select_items: list[tuple[str, str]], family: str) -> str:
    aliases = {a for _e, a in select_items}
    parts: list[str] = []
    for o in order or []:
        if not isinstance(o, dict):
            continue
        by = str(o.get("by") or o.get("field") or "").strip()
        alias = by if by in aliases else _alias(by)
        if alias in aliases:
            parts.append(f"{sem_dialect.quote_alias(alias, family)} "
                         f"{'DESC' if o.get('desc') else 'ASC'}")
    return ", ".join(parts)


def _touched(rendered_sql: str, family: str, out: CompiledDataset) -> list[str]:
    """静态提取正文触及的物理表：供 RLS/敏感策略加载（护栏 §4/§10）。

    提不动时显式告警而不是当作"没有表"——语义层不得谎称已施加行级策略。
    """
    try:
        from backend.semantics.sql_guard import extract_tables

        return extract_tables(rendered_sql, family)
    except Exception as e:  # noqa: BLE001
        out.warnings.append(f"数据集正文表引用无法静态解析(行级策略未在语义层施加, 由执行层治理兜底): {e}")
        return []
