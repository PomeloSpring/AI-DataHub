"""Planner — 把 SemanticQuery + ResolvedBinding 编译成 PlannedExecution。

职责(确定性、无 LLM):
  ① 选路:按 binding.guardrail.query_mode -> materialized / federated / raw_source
  ② 编译基线 SQL:SELECT cols/measures FROM table WHERE ... GROUP BY ... ORDER ... LIMIT
  ③ 护栏:huge 且 allow_full_scan=False 时, 无过滤/无 LIMIT -> 直接拒绝;
          force_limit 生效时, 若未指定 LIMIT -> 补默认值
  ④ provenance / warnings 输出, 供 Playground 与审计面板展示

Phase 1 只做**单对象**编译;跨对象 join 在 Phase 4 与语义层查询合并时引入。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from services.shared.common.db.metadata_db import get_metadata_conn
from services.shared.semantics.models import (
    PlannedExecution, ResolvedBinding, SemanticFilter, SemanticQuery,
)

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 1000
_HUGE_BLOCK_MESSAGE = (
    "huge table without filter/limit is blocked by guardrail "
    "(set allow_full_scan=1 or add filters or specify LIMIT)"
)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_AGG_FNS = {"SUM", "COUNT", "AVG", "MIN", "MAX", "COUNT_DISTINCT"}
_TIME_GRAIN_FMT_MYSQL = {
    "second": "%Y-%m-%d %H:%i:%s",
    "minute": "%Y-%m-%d %H:%i",
    "hour": "%Y-%m-%d %H",
    "day": "%Y-%m-%d",
    "week": "%x-%v",
    "month": "%Y-%m",
    "quarter": "%Y-Q%q",
    "year": "%Y",
}
# PG to_char 模板(week 用 ISO 年-周, quarter 把 Q 当字面量引号输出)
_TIME_GRAIN_FMT_PG = {
    "second": "YYYY-MM-DD HH24:MI:SS",
    "minute": "YYYY-MM-DD HH24:MI",
    "hour": "YYYY-MM-DD HH24",
    "day": "YYYY-MM-DD",
    "week": "IYYY-IW",
    "month": "YYYY-MM",
    "quarter": 'YYYY-"Q"Q',
    "year": "YYYY",
}
# db_type -> 编译方言族: mysql/doris 同一族(MySQL 协议/语法), postgres 家族含 sls(PG 协议)
_DIALECT_OF_DBTYPE = {
    "mysql": "mysql", "doris": "mysql",
    "postgres": "postgres", "postgresql": "postgres", "pg": "postgres", "sls": "postgres",
}
# time_window 单位 -> (MySQL INTERVAL 单位, PG INTERVAL 单位)
_WINDOW_UNIT = {
    "s": ("SECOND", "seconds"),
    "m": ("MINUTE", "minutes"),
    "h": ("HOUR", "hours"),
    "d": ("DAY", "days"),
    "w": ("WEEK", "weeks"),
    "M": ("MONTH", "months"),
    "y": ("YEAR", "years"),
}
# 与 models.SemanticQuery.time_window 的 pattern 保持一致: ^\d{1,4}[smhdwMy]$
_WINDOW_RE = re.compile(r"^(\d{1,4})([smhdwMy])$")
# SQL 模板占位符与危险片段(参数值内容检查)
_TPL_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_FORBIDDEN_VALUE_FRAG = (";", "--", "/*", "*/")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")


def _dialect_of(db_type: str) -> str:
    """数据源 db_type -> SQL 编译方言。未知类型保守回落 mysql(与旧行为一致)并出 warning。"""
    return _DIALECT_OF_DBTYPE.get((db_type or "mysql").lower(), "mysql")


# ── public API ───────────────────────────────────────────────────

def plan(query: SemanticQuery, binding: ResolvedBinding) -> PlannedExecution:
    """编译 SemanticQuery -> PlannedExecution。

    失败(违反护栏)时, warnings 非空且 sql 为空;调用方据此拒绝执行。
    """
    warnings: list[str] = []
    g = binding.guardrail
    dialect = _dialect_of(binding.db_type)
    if (binding.db_type or "").lower() not in _DIALECT_OF_DBTYPE:
        warnings.append(
            f"unknown db_type '{binding.db_type}', compiled with mysql dialect fallback")

    # SQL 模板对象: 绑定的是参数化模板而非单表聚合, 走独立编译分支
    if binding.bind_kind == "sql_template":
        return _plan_template(query, binding, dialect, warnings)

    # ① 护栏预检: huge + raw_source 且未开全表扫 -> 必须有 filters 或 LIMIT
    if g.size_class == "huge" and not g.allow_full_scan:
        if not query.filters and not query.limit and not query.time_window:
            return PlannedExecution(
                sql="", secured_sql="", route=g.query_mode, dialect=dialect,
                datasource_id=binding.datasource_id,
                resolved_tables=[binding.physical_table],
                guardrail=g, access=binding.access,
                warnings=[_HUGE_BLOCK_MESSAGE],
                provenance=_provenance(binding, query),
            )

    # ② 解析: measure/dim 名 -> 物理列/表达式
    resolver = _ColumnResolver(binding, dialect=dialect)
    select_items: list[tuple[str, str]] = []   # (expr, alias)
    group_by: list[str] = []
    col_roles: dict[str, str] = {}

    for d in query.dimensions:
        expr, role = resolver.resolve_dimension(d, time_grain=query.time_grain)
        if expr is None:
            warnings.append(
                f"无法解析维度「{d}」, 已跳过。本对象可用维度: "
                f"{resolver.suggest_dimensions()}; 请用字典名或其别名重试")
            continue
        select_items.append((expr, d))
        col_roles[d] = role
        group_by.append(expr)

    for m in query.metrics:
        expr, agg_type, unit, unsupported = resolver.resolve_measure(m)
        if unsupported:
            warnings.append(
                f"指标「{m}」未配置 {dialect} 方言表达式(formula_dialects), 已跳过")
        if expr is None:
            warnings.append(
                f"无法解析指标「{m}」, 已跳过。本对象可用指标: "
                f"{resolver.suggest_metrics()}; 请用字典名或其别名重试")
            continue
        select_items.append((expr, m))
        col_roles[m] = "measure"
        # 附带单位与聚合信息供溯源
    if not select_items:
        # 至少给一个 "*" 的兜底, 但更常见是调用方忘了 metrics/dimensions
        warnings.append("no valid measure/dimension selected; returning row preview only")
        select_items = [("*", "_preview")]

    # ③ WHERE
    where_parts: list[str] = []
    for f in query.filters:
        cond = resolver.render_filter(f)
        if cond:
            where_parts.append(cond)
        else:
            warnings.append(
                f"无法解析过滤维度「{f.dim}」。本对象可用维度: "
                f"{resolver.suggest_dimensions()}")

    # ③.5 相对时间窗: time_window -> 源库方言的 rolling interval 谓词, 与 filters AND 叠加
    if query.time_window:
        win_cond = resolver.render_time_window(
            query.time_window, query.time_column, warnings)
        if win_cond:
            where_parts.append(win_cond)

    # ④ ORDER BY
    order_parts: list[str] = []
    for o in query.order:
        if o.by in col_roles:
            order_parts.append(f"{_quote_ident(o.by, dialect)} {'DESC' if o.desc else 'ASC'}")
        else:
            warnings.append(f"order-by references unknown field: {o.by}")

    # ⑤ LIMIT (护栏兜底)
    limit_val = query.limit
    if limit_val is None:
        if g.force_limit:
            limit_val = g.max_rows or _DEFAULT_LIMIT
            warnings.append(f"guardrail: force_limit applied ({limit_val})")
        elif g.max_rows:
            limit_val = g.max_rows
    elif g.max_rows and limit_val > g.max_rows:
        limit_val = g.max_rows
        warnings.append(f"guardrail: limit clamped to max_rows ({limit_val})")

    sql = _render_sql(
        table_ref=_exec_table_ref(binding, dialect),
        select_items=select_items,
        where_parts=where_parts,
        group_by=group_by,
        order_parts=order_parts,
        limit=limit_val,
        dialect=dialect,
    )

    prov = _provenance(binding, query)
    # 服务端诊断用(工具侧 provenance 已剥离): 供 gates/Playground 在解析失败时给出候选
    prov["available_dimensions"] = resolver.available_dimension_names()

    return PlannedExecution(
        sql=sql,
        secured_sql=sql,   # Phase 4 RLS 改写会覆盖 secured_sql
        dialect=dialect,
        route=g.query_mode,
        datasource_id=binding.datasource_id,
        resolved_tables=[binding.physical_table],
        guardrail=g,
        access=binding.access,
        provenance=prov,
        warnings=warnings,
    )


# ── SQL renderer (简单确定性, 不依赖 sqlglot) ─────────────────

def _render_sql(
    table_ref: str,
    select_items: list[tuple[str, str]],
    where_parts: list[str],
    group_by: list[str],
    order_parts: list[str],
    limit: Optional[int],
    dialect: str = "mysql",
) -> str:
    # 别名引用符随方言: MySQL 反引号 / PG 双引号(否则 PG 下 `AS \`x\`` 非法)
    proj = ", ".join(
        "*" if expr == "*" else f"{expr} AS {_quote_ident(alias, dialect)}"
        for expr, alias in select_items
    )
    parts = [f"SELECT {proj}", f"FROM {table_ref}"]
    if where_parts:
        parts.append("WHERE " + " AND ".join(where_parts))
    if group_by:
        parts.append("GROUP BY " + ", ".join(group_by))
    if order_parts:
        parts.append("ORDER BY " + ", ".join(order_parts))
    if limit is not None:
        parts.append(f"LIMIT {int(limit)}")
    return "\n".join(parts)


def _is_agg(expr: str) -> bool:
    return any(re.match(rf"\b{fn}\s*\(", expr.upper()) for fn in _AGG_FNS)


def _quote_ident(name: str, dialect: str = "mysql") -> str:
    s = str(name or "").strip()
    if not s:
        return s
    # 含空白/括号/引号/逗号等运算结构的表达式原样透传; 其余(含中文词)一律视为标识符加引号,
    # 否则中文维度别名在 ORDER BY/AS 处裸奔(PG 直接语法错)。
    if not _IDENT_RE.match(s) and _EXPR_CHAR_RE.search(s):
        return s
    if dialect == "postgres":
        return '"' + s.replace('"', '""') + '"'
    return f"`{s}`"


_EXPR_CHAR_RE = re.compile(r"[\s(),;'\".\[}=+*/-]")


def _norm_name(s: Any) -> str:
    """名称归一化: 全角→半角、去所有空白、casefold。

    让"创建时间/创立 时间/CREATE_TIME/create_time"这类写法归一到同一键。
    """
    t = str(s or "")
    # 全角 ASCII(FF01-FF5E) -> 半角
    t = "".join(chr(ord(c) - 0xFEE0) if "\uFF01" <= c <= "\uFF5E" else c for c in t)
    t = t.replace("\u3000", "")
    return re.sub(r"\s+", "", t).casefold()


def _exec_table_ref(b: ResolvedBinding, dialect: str = "mysql") -> str:
    """执行侧表引用。

    MySQL/Doris 路: 只用 `db.table` 二段式。DataFusion 把数据源注册到默认
    catalog(`datafusion`), MySQL 直连则连到数据源自身 database;两条路径都不认
    元数据层 catalog 名(如 "adh"), 三段式会 `catalog not found`/`Unknown database`。

    PG 路: 不拼 `database.table` —— psycopg 连接已定 dbname, 二段式会被当成
    `schema.table` 报 schema 不存在;DataFusion 侧同样按注册的默认 schema 解析,
    故用裸表名(由 search_path/默认 schema 解析)。
    """
    if dialect == "postgres":
        return _quote_ident(b.physical_table, dialect)
    parts = [p for p in (b.db_name, b.physical_table) if p]
    return ".".join(parts) or b.physical_table


# ── 列/指标/维度解析 ────────────────────────────────────────────

class _ColumnResolver:
    """把 SemanticQuery 的名字解析为物理列/表达式。

    解析优先级: 维度/指标字典精确名 → 字典别名(aliases/name_en/属性中文名) →
    binding.column_map → 物理列(含列业务名 business_desc) → 唯一包含式模糊。
    字典行携带 value_labels(枚举码→业务标签)时, SELECT/GROUP BY 用同一 CASE 表达式,
    WHERE 则支持 label→code 反查(过滤可传业务名也可传码值)。
    dialect 决定标识符引用与时间函数(MySQL 系 DATE_FORMAT/反引号 vs PG to_char/双引号)。
    """

    _TIME_TYPES = {"date", "datetime", "timestamp", "time"}

    def __init__(self, binding: ResolvedBinding, dialect: str = "mysql"):
        self.binding = binding
        self.dialect = dialect
        self._phys: dict[str, dict[str, Any]] | None = None
        self._metrics: dict[str, dict[str, Any]] | None = None
        self._dims: dict[str, dict[str, Any]] | None = None
        self._index: dict[str, dict[str, Any]] | None = None

    def physical_columns(self) -> set[str]:
        return set(self._load_phys())

    def _load_phys(self) -> dict[str, dict[str, Any]]:
        """物理列名 -> {data_type, business_desc}(供类型判定与中文业务名索引)。"""
        if self._phys is None:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "SELECT column_name, data_type, "
                            "COALESCE(NULLIF(business_desc, ''), column_comment) AS business_desc "
                            "FROM adh_column_metadata "
                            "WHERE datasource_id = %s AND table_name = %s AND is_active = 1",
                            (self.binding.datasource_id, self.binding.physical_table),
                        )
                    except Exception:
                        # 旧库无 business_desc/column_comment 列时回落
                        cur.execute(
                            "SELECT column_name, data_type, '' AS business_desc "
                            "FROM adh_column_metadata "
                            "WHERE datasource_id = %s AND table_name = %s AND is_active = 1",
                            (self.binding.datasource_id, self.binding.physical_table),
                        )
                    self._phys = {r["column_name"]: r for r in cur.fetchall()}
            finally:
                conn.close()
        return self._phys

    @staticmethod
    def _parse_json(v: Any, fallback):
        if isinstance(v, (str, bytes)):
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return fallback
        return v if v is not None else fallback

    def _load_metrics(self) -> dict[str, dict[str, Any]]:
        if self._metrics is None:
            self._metrics = {}
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    extra = self._extra_cols(cur, 'adh_metrics',
                                             ('formula_dialects', 'aliases'))
                    cur.execute(
                        "SELECT name, name_en, formula, formula_dsl, agg_type, default_agg, unit, "
                        "       description"
                        + (", formula_dialects" if 'formula_dialects' in extra else '')
                        + (", aliases" if 'aliases' in extra else '')
                        + " FROM adh_metrics WHERE is_active = 1 AND target_table = %s",
                        (self.binding.physical_table,),
                    )
                    rows = cur.fetchall()
                for r in rows:
                    r["formula_dialects"] = self._parse_json(r.get("formula_dialects"), None)
                    r["aliases"] = self._parse_json(r.get("aliases"), [])
                self._metrics = {r["name"]: r for r in rows}
                # name_en 也接受
                for r in list(self._metrics.values()):
                    if r.get("name_en"):
                        self._metrics.setdefault(r["name_en"], r)
            finally:
                conn.close()
        return self._metrics

    @staticmethod
    def _extra_cols(cur, table: str, candidates: tuple) -> set[str]:
        """探测可选扩展列是否存在(旧库未迁移时自动降级)。"""
        try:
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                f"AND COLUMN_NAME IN ({', '.join(['%s'] * len(candidates))})",
                (table, *candidates),
            )
            return {r["COLUMN_NAME"] for r in cur.fetchall()}
        except Exception:  # noqa: BLE001 — 探测失败视为无扩展列
            return set()

    def _load_dims(self) -> dict[str, dict[str, Any]]:
        if self._dims is None:
            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "SELECT name, name_en, target_column, category, "
                            "       aliases, value_labels FROM adh_dimensions "
                            "WHERE is_active = 1 AND target_table = %s ORDER BY id ASC",
                            (self.binding.physical_table,),
                        )
                    except Exception:
                        # aliases/value_labels 为字典增强列; 迁移未执行时回落
                        cur.execute(
                            "SELECT name, name_en, target_column, category, "
                            "       NULL AS aliases, NULL AS value_labels FROM adh_dimensions "
                            "WHERE is_active = 1 AND target_table = %s ORDER BY id ASC",
                            (self.binding.physical_table,),
                        )
                    rows = cur.fetchall()
                for r in rows:
                    r["aliases"] = self._parse_json(r.get("aliases"), [])
                    r["value_labels"] = {str(k): str(v) for k, v in
                                         (self._parse_json(r.get("value_labels"), {}) or {}).items()}
                self._dims = {r["name"]: r for r in rows}
                for r in list(self._dims.values()):
                    if r.get("name_en"):
                        self._dims.setdefault(r["name_en"], r)
            finally:
                conn.close()
        return self._dims

    # ── 统一别名索引 ──────────────────────────────────────

    def _dim_index(self) -> dict[str, dict[str, Any]]:
        """norm(任意写法) -> {col, time, labels, name, kind}。

        kind: 'dim'=字典行(可展示给用户) / 'phys'=物理列(严禁出现在提示文案)。
        """
        if self._index is None:
            idx: dict[str, dict[str, Any]] = {}
            phys = self._load_phys()

            def add(key: str, entry: dict, prefer: bool = False):
                k = _norm_name(key)
                if not k:
                    return
                if prefer or k not in idx:
                    idx[k] = entry

            # 1) 物理列本体名(最低优先, 可被字典覆盖)
            for col, meta in phys.items():
                dt = str(meta.get("data_type") or "").split("(")[0].lower()
                add(col, {"col": col, "time": dt in self._TIME_TYPES,
                          "labels": {}, "name": col, "kind": "phys"})
            # 2) 字典行: name/name_en/aliases 全部入索引; 字典优先于物理同名列
            for _k, d in self._load_dims().items():
                col = d.get("target_column") or ""
                if not col:
                    continue
                pm = phys.get(col) or {}
                dt = str(pm.get("data_type") or "").split("(")[0].lower()
                entry = {
                    "col": col,
                    "time": (d.get("category") or "") == "时间" or dt in self._TIME_TYPES,
                    "labels": d["value_labels"],
                    "name": d["name"], "kind": "dim",
                }
                add(d["name"], entry, prefer=True)
                if d.get("name_en"):
                    add(d["name_en"], entry, prefer=True)
                for a in (d["aliases"] or []):
                    add(a, entry, prefer=True)
            # 3) 物理列中文业务名(business_desc) → 指回物理列(不对外展示)
            for col, meta in phys.items():
                bd = str(meta.get("business_desc") or "").strip()
                if bd:
                    e = idx.get(_norm_name(col))
                    if e and e["kind"] == "dim":
                        continue  # 该列已登记为字典维度, 中文业务名指向字典条目
                    add(bd, {"col": col, "time": str(meta.get("data_type") or "")
                             .split("(")[0].lower() in self._TIME_TYPES,
                             "labels": {}, "name": col, "kind": "phys"})
            # 4) binding.column_map(本体属性名 → 物理列)
            for alias, col in (self.binding.column_map or {}).items():
                if col:
                    e = idx.get(_norm_name(col)) or {}
                    add(alias, {"col": col, "time": bool(e.get("time")),
                                "labels": e.get("labels") or {},
                                "name": e.get("name") if e.get("kind") == "dim" else col,
                                "kind": e.get("kind") or "phys"})
            self._index = idx
        return self._index

    def lookup_dimension(self, name: str) -> Optional[dict[str, Any]]:
        """任意写法 → 维度条目; 精确/别名命中优先, 其次唯一包含式模糊。"""
        idx = self._dim_index()
        n = _norm_name(name)
        if not n:
            return None
        hit = idx.get(n)
        if hit:
            return hit
        # 唯一模糊: "X包含Y"或"Y包含X"且只指向一个 distinct 列才接受(防误绑)
        cands = {e["col"]: e for k, e in idx.items() if (n in k or k in n)}
        if len(cands) == 1:
            return next(iter(cands.values()))
        return None

    def suggest_dimensions(self, limit: int = 10) -> list[str]:
        """对象可用维度提示(只列字典名+主要别名, 不暴露物理列)。"""
        out: list[str] = []
        seen: set[str] = set()
        for _k, d in self._load_dims().items():
            if d["name"] in seen or not d.get("target_column"):
                continue
            seen.add(d["name"])
            al = [str(a) for a in (d.get("aliases") or []) if a][:3]
            item = d["name"] + (f"(别名: {'/'.join(al)})" if al else "")
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def available_dimension_names(self) -> list[str]:
        return sorted({d["name"] for d in self._load_dims().values()
                       if d.get("target_column")})

    # ── dimension ─────────────────────────────────────────────
    
    def resolve_dimension(
        self, name: str, time_grain: Optional[str] = None,
    ) -> tuple[Optional[str], str]:
        """返回 SELECT/GROUP BY 用的表达式(枚举维度翻成业务标签 CASE)。"""
        entry = self.lookup_dimension(name)
        if not entry:
            return None, ""
        if entry.get("labels"):
            return self._label_case_expr(entry["col"], entry["labels"]), "dimension"
        if entry.get("time"):
            # time_grain 只对时间类维度生效(枚举/类别维度不受全局粒度影响)
            return self._maybe_wrap_time(entry["col"], time_grain), "time"
        return _quote_ident(entry["col"], self.dialect), "dimension"
    
    def _label_case_expr(self, col: str, labels: dict[str, str]) -> str:
        """枚举码→业务标签的 CASE 表达式; 未命中码值原样输出(转文本避免混型)。"""
        q = _quote_ident(col, self.dialect)
        whens = " ".join(
            f"WHEN {_lit(str(code))} THEN {_lit(label)}"
            for code, label in sorted(labels.items()))
        else_ = f"{q}::text" if self.dialect == "postgres" else f"CAST({q} AS CHAR)"
        return f"CASE {q} {whens} ELSE {else_} END"
    
    @staticmethod
    def _label_to_code(entry: dict[str, Any], value: Any) -> Any:
        """WHERE 值反查: 业务标签 → 码值; 非标签(含已是码值)原样保留。"""
        labels = entry.get("labels") or {}
        if not labels or isinstance(value, (list, dict)):
            return value
        rev = {_norm_name(v): str(k) for k, v in labels.items()}
        return rev.get(_norm_name(value), value)
    
    def _maybe_wrap_time(self, col: str, grain: Optional[str]) -> str:
        q = _quote_ident(col, self.dialect)
        if not grain:
            return q
        if self.dialect == "postgres":
            fmt = _TIME_GRAIN_FMT_PG.get(grain)
            if not fmt:
                return q
            return f"to_char({q}, '{fmt}')"
        fmt = _TIME_GRAIN_FMT_MYSQL.get(grain)
        if not fmt:
            return q
        return f"DATE_FORMAT({q}, '{fmt}')"
    
    # ── measure ───────────────────────────────────────────────
    
    def resolve_measure(
        self, name: str,
    ) -> tuple[Optional[str], Optional[str], str, bool]:
        """返回 (表达式, agg, 单位, 方言不支持)。支持别名/归一化匹配。"""
        m = self._lookup_metric(name)
        if m:
            expr, unsupported = self._pick_dialect_expr(m)
            if unsupported:
                return None, None, m.get("unit") or "", True
            agg = (m.get("default_agg") or m.get("agg_type") or "").upper() or None
            return expr or None, agg, m.get("unit") or "", False
        # 兑底: 直接把 name 当物理列 SUM(归一化比较)
        phys = {_norm_name(c): c for c in self._load_phys()}
        col = phys.get(_norm_name(name))
        if col:
            return f"SUM({_quote_ident(col, self.dialect)})", "SUM", "", False
        return None, None, "", False
    
    def _lookup_metric(self, name: str) -> Optional[dict[str, Any]]:
        ms = self._load_metrics()
        m = ms.get(name)
        if m:
            return m
        n = _norm_name(name)
        for _k, row in ms.items():
            cands = {_norm_name(row["name"]), _norm_name(row.get("name_en") or "")}
            cands |= {_norm_name(a) for a in (row.get("aliases") or []) if a}
            if n in cands:
                return row
        return None
    
    def suggest_metrics(self, limit: int = 10) -> list[str]:
        seen: list[str] = []
        for _k, row in self._load_metrics().items():
            if row["name"] not in seen:
                seen.append(row["name"])
            if len(seen) >= limit:
                break
        return seen
    
    def _pick_dialect_expr(self, m: dict[str, Any]) -> tuple[str, bool]:
        """取式优先级: formula_dialects[当前dialect] -> formula_dsl/formula。
    
        formula_dialects 含当前 dialect 键时: 值为空/null 表示"显式声明不支持本方言"
        (unsupported=True); 键不存在则继续回落通用 formula。
        """
        fd = m.get("formula_dialects") or {}
        if self.dialect in fd:
            expr = str(fd.get(self.dialect) or "").strip()
            return expr, (not expr)
        return (m.get("formula_dsl") or m.get("formula") or "").strip(), False
    
    # ── filter ──────────────────────────────────────────────
    
    def render_filter(self, f: SemanticFilter) -> Optional[str]:
        entry = self.lookup_dimension(f.dim)
        if not entry:
            return None
        # WHERE 永远打在原始列上(标签只在结果集展示), 值支持业务名→码值反查
        expr = _quote_ident(entry["col"], self.dialect)
        if entry.get("time") and f.grain:
            expr = self._maybe_wrap_time(entry["col"], f.grain)
        if entry.get("labels"):
            value = self._label_to_code(entry, f.value)
            values = ([self._label_to_code(entry, v) for v in f.values]
                      if f.values is not None else None)
        else:
            value, values = f.value, f.values
        return _render_condition(expr, f.op, value, values)
    
    # time window (相对滚动窗口)
    
    def render_time_window(
        self, window: str, time_column: Optional[str], warnings: list[str],
    ) -> Optional[str]:
        """把 time_window('7d' 等) 编译为滚动区间谓词; 无法定位时间列时告警并跳过。"""
        mm = _WINDOW_RE.match(str(window or "").strip())
        if not mm:
            warnings.append(f"invalid time_window: {window!r} (expect e.g. '7d'/'24h'/'2w'/'1M')")
            return None
        n, unit_key = int(mm.group(1)), mm.group(2)
        my_unit, pg_unit = _WINDOW_UNIT[unit_key]
    
        # 目标列: 显式 time_column(走同一别名解析) -> 字典时间维度 -> 唯一 datetime 物理列
        col_expr: Optional[str] = None
        if time_column:
            entry = self.lookup_dimension(time_column)
            if entry is None:
                warnings.append(
                    f"时间维度「{time_column}」无法解析, 可用时间维度: "
                    f"{self.suggest_time_dimensions() or '无'}")
                return None
            col_expr = _quote_ident(entry["col"], self.dialect)
        else:
            for d in self._load_dims().values():
                if (d.get("category") or "") == "时间" and d.get("target_column"):
                    col_expr = _quote_ident(d["target_column"], self.dialect)
                    break
            if col_expr is None:
                tcols = [c for c, meta in self._load_phys().items()
                         if str(meta.get("data_type") or "").split("(")[0].lower()
                         in self._TIME_TYPES]
                if len(tcols) == 1:
                    col_expr = _quote_ident(tcols[0], self.dialect)
            if col_expr is None:
                warnings.append(
                    "time_window 需要显式 time_column: 本对象未登记时间类维度"
                    f"(可用维度: {self.suggest_dimensions()})")
                return None
    
        if self.dialect == "postgres":
            return f"{col_expr} >= now() - INTERVAL '{n} {pg_unit}'"
        return f"{col_expr} >= DATE_SUB(NOW(), INTERVAL {n} {my_unit})"
    
    def suggest_time_dimensions(self) -> list[str]:
        out = []
        for _k, d in self._load_dims().items():
            if (d.get("category") or "") == "时间" and d.get("target_column"):
                out.append(d["name"])
        return sorted(set(out))


# ── SQL 模板编译 (bind_kind='sql_template') ───────────────────────────

def _empty_plan(
    binding: ResolvedBinding, query: SemanticQuery,
    dialect: str, warnings: list[str],
) -> PlannedExecution:
    g = binding.guardrail
    return PlannedExecution(
        sql="", secured_sql="", route=g.query_mode, dialect=dialect,
        datasource_id=binding.datasource_id,
        resolved_tables=[p for p in [binding.physical_table] if p],
        guardrail=g, access=binding.access,
        warnings=warnings, provenance=_provenance(binding, query),
    )


def _plan_template(
    query: SemanticQuery, binding: ResolvedBinding, dialect: str, warnings: list[str],
) -> PlannedExecution:
    """模板对象编译: 载模板 -> 参数校验/渲染 -> 包一层 guardrail LIMIT。

    信任边界: 模板 SQL 由建模人员注册(同 adh_metrics.formula 待遇), 参数值则按
    声明类型严格校验 + 拒绝危险片段, 不接受任何未声明键。
    """
    g = binding.guardrail
    if not binding.template_ref:
        warnings.append("sql_template binding missing template_ref")
        return _empty_plan(binding, query, dialect, warnings)

    tpl = _load_template_row(binding.template_ref, warnings)
    if not tpl:
        return _empty_plan(binding, query, dialect, warnings)
    tpl_dialect = str(tpl.get("tpl_dialect") or "generic").lower()
    if tpl_dialect not in ("generic", dialect):
        warnings.append(
            f"template dialect '{tpl_dialect}' 与数据源方言 '{dialect}' 不匹配, 拒绝执行")
        return _empty_plan(binding, query, dialect, warnings)
    sql_text = str(tpl.get("sql_template") or "").strip()
    if not sql_text:
        warnings.append(f"template '{binding.template_ref}' 无 SQL 内容")
        return _empty_plan(binding, query, dialect, warnings)

    declared = _parse_variables(tpl.get("variables"))
    names_in_tpl = set(_TPL_PLACEHOLDER_RE.findall(sql_text))
    known = set(declared) | names_in_tpl

    if query.metrics or query.dimensions:
        warnings.append("模板对象忽略 metrics/dimensions(结果形状由模板 SQL 决定)")
    if query.order:
        warnings.append("模板对象忽略 order")
    if query.time_window:
        warnings.append("模板对象忽略 time_window(时间窗请用模板参数传递)")

    params: dict[str, Any] = dict(query.params or {})
    # filters 语法糖: dim 与模板参数同名且 params 未显式给出时, 折算为参数值
    for f in query.filters:
        if f.dim in known and f.dim not in params:
            params[f.dim] = (f.values
                             if f.op in ("in", "nin", "between") and f.values
                             else f.value)
    for k in params:
        if k not in known:
            warnings.append(
                f"params 键 '{k}' 未在模板声明(可用参数: {sorted(names_in_tpl) or '无'})")
            return _empty_plan(binding, query, dialect, warnings)

    errors: list[str] = []

    def _sub(m: "re.Match[str]") -> str:
        name = m.group(1)
        decl = declared.get(name) or {}
        if name in params:
            val = params[name]
        elif "default" in decl:
            val = decl["default"]
        else:
            errors.append(f"缺少模板参数: {name}")
            return m.group(0)
        out, err = _render_tpl_value(name, val, decl)
        if err:
            errors.append(err)
            return m.group(0)
        return out

    rendered = _TPL_PLACEHOLDER_RE.sub(_sub, sql_text)
    if errors:
        warnings.extend(errors)
        return _empty_plan(binding, query, dialect, warnings)
    if query.params and not names_in_tpl:
        warnings.append("模板无任何 ${} 占位符, params 未生效")

    # LIMIT 沿用既有 guardrail 钳制逻辑(⑤)
    limit_val = query.limit
    if limit_val is None:
        if g.force_limit:
            limit_val = g.max_rows or _DEFAULT_LIMIT
            warnings.append(f"guardrail: force_limit applied ({limit_val})")
        elif g.max_rows:
            limit_val = g.max_rows
    elif g.max_rows and limit_val > g.max_rows:
        limit_val = g.max_rows
        warnings.append(f"guardrail: limit clamped to max_rows ({limit_val})")

    wrapped = f"SELECT * FROM (\n{rendered}\n) tpl_result"
    if limit_val is not None:
        wrapped += f"\nLIMIT {int(limit_val)}"

    provenance = _provenance(binding, query)
    provenance["template_ref"] = binding.template_ref
    provenance["template_name"] = tpl.get("template_name") or ""
    return PlannedExecution(
        sql=wrapped,
        secured_sql=wrapped,
        dialect=dialect,
        route=g.query_mode,
        datasource_id=binding.datasource_id,
        resolved_tables=[],
        guardrail=g, access=binding.access,
        provenance=provenance,
        warnings=warnings,
    )


def _load_template_row(template_ref: str, warnings: list[str]) -> Optional[dict[str, Any]]:
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT template_id, template_name, sql_template, variables, "
                    "       dialect AS tpl_dialect "
                    "FROM adh_sql_templates WHERE template_id = %s AND is_active = 1 LIMIT 1",
                    (template_ref,),
                )
            except Exception:
                # dialect 列为迁移新增; 旧库上视同为 generic(各方言均可用, 由建模人员保证)
                cur.execute(
                    "SELECT template_id, template_name, sql_template, variables, "
                    "       'generic' AS tpl_dialect "
                    "FROM adh_sql_templates WHERE template_id = %s AND is_active = 1 LIMIT 1",
                    (template_ref,),
                )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        warnings.append(f"SQL 模板 '{template_ref}' 不存在或未启用")
    return row


def _parse_variables(raw: Any) -> dict[str, dict[str, Any]]:
    """adh_sql_templates.variables -> {name: decl}。解析失败视为"无声明"(返回 {})。"""
    v = raw
    if isinstance(v, bytes):
        try:
            v = v.decode()
        except Exception:
            return {}
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return {}
    out: dict[str, dict[str, Any]] = {}
    if isinstance(v, list):
        for item in v:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = item
    elif isinstance(v, dict):
        for k, item in v.items():
            if isinstance(item, dict):
                out[str(k)] = {**item, "name": k}
            else:
                out[str(k)] = {"name": k, "type": str(item)}
    return out


def _render_tpl_value(
    name: str, val: Any, decl: dict[str, Any],
) -> tuple[str, Optional[str]]:
    """按声明类型渲染模板参数; 返回 (SQL 片段, 错误)。list 渲染为逗号序列(供 IN ${x})。"""
    vtype = str(decl.get("type") or "").lower()
    if isinstance(val, (list, tuple)):
        if not val:
            return "", f"模板参数 '{name}' 不接受空列表"
        # 元素类型: 优先声明的 item_type; list 本身不是元素类型, 不能直接下传
        declared_item = str(decl.get("item_type") or "").lower()
        if declared_item:
            item_type = declared_item
        elif vtype and vtype != "list":
            item_type = vtype
        else:
            item_type = "string"
        parts: list[str] = []
        for it in val:
            s, err = _render_tpl_scalar(name, it, {**decl, "type": item_type})
            if err:
                return "", err
            parts.append(s)
        return ", ".join(parts), None
    return _render_tpl_scalar(name, val, {**decl, "type": vtype if vtype != "list" else "string"})


def _render_tpl_scalar(
    name: str, val: Any, decl: dict[str, Any],
) -> tuple[str, Optional[str]]:
    vtype = str(decl.get("type") or "string").lower()

    if vtype in ("int", "integer", "long", "bigint"):
        if isinstance(val, bool):
            return "", f"模板参数 '{name}' 需为整数"
        s = str(val).strip()
        if not re.match(r"^-?\d+$", s):
            return "", f"模板参数 '{name}' 需为整数, 实得 {val!r}"
        return s, None
    if vtype in ("float", "number", "decimal", "double"):
        if isinstance(val, bool):
            return "", f"模板参数 '{name}' 需为数值"
        try:
            f = float(str(val).strip())
        except ValueError:
            return "", f"模板参数 '{name}' 需为数值, 实得 {val!r}"
        if f != f or f in (float("inf"), float("-inf")):
            return "", f"模板参数 '{name}' 需为有限数值"
        return repr(f), None
    if vtype in ("bool", "boolean"):
        if isinstance(val, bool):
            return ("1" if val else "0"), None
        if str(val).strip().lower() in ("true", "1"):
            return "1", None
        if str(val).strip().lower() in ("false", "0"):
            return "0", None
        return "", f"模板参数 '{name}' 需为布尔值, 实得 {val!r}"
    if vtype == "date":
        s = str(val).strip()
        if not _DATE_RE.match(s):
            return "", f"模板参数 '{name}' 需为日期 YYYY-MM-DD, 实得 {val!r}"
        return _lit(s), None
    if vtype in ("datetime", "timestamp"):
        s = str(val).strip().replace("T", " ")
        if not _DATETIME_RE.match(str(val).strip()) and not _DATETIME_RE.match(s):
            return "", f"模板参数 '{name}' 需为日期时间, 实得 {val!r}"
        return _lit(s), None
    if vtype == "enum":
        allowed = [str(x) for x in (decl.get("values") or [])]
        if allowed and str(val) in allowed:
            return _lit(str(val)), None
        return "", f"模板参数 '{name}' 必须为 {allowed} 之一"
    # string / 未知类型: 严格拒绝危险片段后转义
    s = str(val)
    for frag in _FORBIDDEN_VALUE_FRAG:
        if frag in s:
            return "", f"模板参数 '{name}' 含非法片段 '{frag}'"
    return _lit(s), None


def _lit(v: Any) -> str:
    """SQL 字面量渲染(双方言一致: 单引号翻倍转义)。"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _render_condition(lhs: str, op: str, value: Any, values: Optional[list[Any]]) -> Optional[str]:
    lit = _lit

    if op == "eq":       return f"{lhs} = {lit(value)}"
    if op == "ne":       return f"{lhs} <> {lit(value)}"
    if op == "gt":       return f"{lhs} > {lit(value)}"
    if op == "gte":      return f"{lhs} >= {lit(value)}"
    if op == "lt":       return f"{lhs} < {lit(value)}"
    if op == "lte":      return f"{lhs} <= {lit(value)}"
    if op == "like":     return f"{lhs} LIKE {lit(value)}"
    if op == "is_null":  return f"{lhs} IS NULL"
    if op == "is_not_null": return f"{lhs} IS NOT NULL"
    if op == "in":
        arr = values if values is not None else (value if isinstance(value, list) else [])
        if not arr: return None
        return f"{lhs} IN (" + ", ".join(lit(v) for v in arr) + ")"
    if op == "nin":
        arr = values if values is not None else (value if isinstance(value, list) else [])
        if not arr: return None
        return f"{lhs} NOT IN (" + ", ".join(lit(v) for v in arr) + ")"
    if op == "between":
        arr = values if values is not None else (value if isinstance(value, list) else [])
        if not arr or len(arr) < 2: return None
        return f"{lhs} BETWEEN {lit(arr[0])} AND {lit(arr[1])}"
    return None


# ── provenance ──────────────────────────────────────────────────

def _provenance(b: ResolvedBinding, q: SemanticQuery) -> dict[str, Any]:
    return {
        "object": b.object_key,
        "model_id": b.model_id,
        "datasource_id": b.datasource_id,
        "binding_source": b.source,
        "sync_state": b.sync_state,
        "catalog_ref": b.catalog_ref,
        "route": b.guardrail.query_mode,
        "size_class": b.guardrail.size_class,
        "allow_full_scan": b.guardrail.allow_full_scan,
        "requested_metrics": q.metrics,
        "requested_dimensions": q.dimensions,
    }
