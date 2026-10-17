"""数据集（SQL 形态）注册校验 —— "可信任预制 SQL" 的信任边界在保存这一关落地。

数据集正文由建模人员编写、由取数主链路按声明渲染参数后原样下发，因此**保存即校验**，
不通过直接拒绝（fail-loud），不允许"先存下来上线再说"（no-silent-degradation）：

1. **只读**：每段方言正文都是单条 SELECT/WITH 查询，禁 DDL/DML/多语句；
   与执行层 `validate_sql` 共用同一解析入口，保证"存得进 = 跑得通"不漂移；
2. **方言键合法**：`sql_bodies` 的键必须是已知引擎或方言族——拼错的键永不命中，
   是"写了但永远不生效"的死键（历史缺陷同 `formula_dialects` 的 doris 键）；
3. **参数声明可执行**：`variables` 类型在受控集合内、enum 必须有候选值、default
   必须能通过同一套渲染校验（注册即试渲染，不留到运行期才炸）；
4. **字段声明可信任**：`field_config` 的度量要么有受控 agg、要么有只读 expr，
   且 expr 只能引用已声明列（防止把字段声明当成第二个 SQL 注入口）；
5. **碰了哪些表可静态回答**：`touched_tables` 由服务端从正文提取，供语义层 RLS
   改写与审计消费（护栏 §4/§10），不接受请求体自报。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.semantics import dialect as sem_dialect
from backend.semantics.sql_params import (
    FORBIDDEN_VALUE_FRAG, PLACEHOLDER_RE, VARIABLE_TYPES, parse_json_obj,
    parse_variables, render_value,
)

# 正文可用方言标注：generic=各方言均可（由建模人员保证），其余为方言族或引擎名
DATASET_DIALECTS = frozenset({"", "generic", "mysql", "postgres", "doris", "sls"})
FIELD_ROLES = frozenset({"dimension", "measure"})
_MEASURE_AGGS = frozenset({"", "NONE", "SUM", "COUNT", "COUNT_DISTINCT", "MAX", "MIN", "AVG"})

# 正文标注的引擎/方言族 -> sqlglot 解析方言（先试本引擎方言，再走宽容尝试序）
_PROBE_DIALECT = {
    "mysql": "mysql", "doris": "doris", "starrocks": "starrocks",
    "postgres": "postgres", "pg": "postgres", "sls": "presto",
    "presto": "presto", "trino": "trino", "generic": "mysql", "": "mysql",
}

_PARSE_HINT = (
    "提示：治理层解析器（sqlglot）认不了部分引擎专有写法（如 Doris 柯里式 "
    "window_funnel(W,'strict')(ts, c1, c2)）。执行前会过同一道只读门，所以不要保存"
    "解不动的正文：请改用可移植的两级聚合写法（内层按实体取各步 "
    "MIN(CASE WHEN ci THEN ts END)，外层比较时序与窗口后计数），MySQL 8 / PG / "
    "Doris / SLS 同构可用。"
)


def _dummy_for(decl: dict) -> str:
    """按声明类型给占位符填一个安全字面量，只为让 SQL 能被解析器读通。"""
    vtype = str((decl or {}).get("type") or "").lower()
    if vtype in ("date",):
        return "'2024-01-01'"
    if vtype in ("datetime", "timestamp"):
        return "'2024-01-01 00:00:00'"
    if vtype in ("string", "enum"):
        return "'x'"
    return "1"


def probe_sql(sql: str, variables: dict, dialect_label: str) -> tuple[list[str], list[str]]:
    """把正文渲染成可解析形态后做只读校验。返回 (错误清单, 触及物理表)。

    解析/只读不通过即报错（不回退"存下来再说"）；治理解析器不可用时同样报错。
    """
    errors: list[str] = []
    decl = variables if isinstance(variables, dict) else {}
    probe = PLACEHOLDER_RE.sub(lambda m: _dummy_for(decl.get(m.group(1)) or {}), sql or "")
    if "${" in probe:
        errors.append(f"[{dialect_label}] 正文存在写法非法的占位符（应为 ${{name}}）")
    try:
        from backend.semantics.sql_guard import extract_tables, parse_readonly
    except Exception as e:  # noqa: BLE001 — 治理依赖缺失不得静默放行
        return [f"[{dialect_label}] SQL 只读校验不可用: {e}"], []
    label = str(dialect_label or "").strip().lower()
    fam = _PROBE_DIALECT.get(label, "mysql")
    try:
        parse_readonly(probe, fam)
    except Exception as e:  # noqa: BLE001 — 校验失败必须逐条报出（带可操作改法）
        errors.append(f"[{dialect_label}] 正文未通过只读校验: {str(e)[:160]}。{_PARSE_HINT}")
        return errors, []
    try:
        tables = extract_tables(probe, fam)
    except Exception as e:  # noqa: BLE001
        errors.append(f"[{dialect_label}] 正文表引用无法静态解析: {str(e)[:160]}")
        tables = []
    return errors, tables


def validate_variables(raw: Any) -> tuple[list[str], dict]:
    """`variables` 声明校验（类型受控、enum 必须有候选值、default 必须可渲染）。"""
    errors: list[str] = []
    v = raw if isinstance(raw, (dict, list)) else parse_json_obj(raw, None)
    if v in (None, "", [], {}):
        return [], {}
    decls = parse_variables(v)
    if not decls and v:
        return ["variables 必须是对象或数组（{name: {type,...}} 或 [{name,type}]）"], {}
    for name, decl in decls.items():
        vtype = str((decl or {}).get("type") or "").lower()
        if vtype not in VARIABLE_TYPES:
            errors.append(f"参数 {name!r} 类型 {vtype!r} 不在受控集合内")
        if vtype == "enum" and not ((decl or {}).get("values") or []):
            errors.append(f"参数 {name!r} 声明为 enum 但无 values 候选值")
        if "default" in (decl or {}):
            _out, err = render_value(name, decl["default"], decl)
            if err:
                errors.append(f"参数 {name!r} 的 default 无法渲染: {err}")
    return errors, decls


def validate_field_config(raw: Any) -> tuple[list[str], list[dict]]:
    """`field_config` 校验：角色受控、度量 agg/expr 合法、expr 只引用已声明列。"""
    cols = raw if isinstance(raw, list) else parse_json_obj(raw, None)
    if cols in (None, "", []):
        return [], []
    if not isinstance(cols, list):
        return ["field_config 必须是数组（[{field,label,role,agg|expr}]）"], []
    errors: list[str] = []
    names = {str((c or {}).get("field") or "").strip()
             for c in cols if isinstance(c, dict)}
    seen: set[str] = set()
    for i, c in enumerate(cols):
        if not isinstance(c, dict):
            errors.append(f"field_config[{i}] 不是对象")
            continue
        field = str(c.get("field") or "").strip()
        role = str(c.get("role") or "dimension").strip().lower()
        if not field:
            errors.append(f"field_config[{i}] 缺 field（数据集 SQL 的输出列名）")
            continue
        if field in seen:
            errors.append(f"field_config 列名重复: {field}")
            continue
        seen.add(field)
        if role not in FIELD_ROLES:
            errors.append(f"字段 {field!r} 的 role={role!r} 非法（只支持 {sorted(FIELD_ROLES)}）")
            continue
        agg = str(c.get("agg") or "").strip().upper()
        expr = str(c.get("expr") or "").strip()
        if role == "measure" and agg not in _MEASURE_AGGS:
            errors.append(f"度量列 {field!r} 的 agg={agg!r} 不在受控聚合函数集合内")
        if expr:
            err = _check_measure_expr(field, expr, names)
            if err:
                errors.append(err)
    return errors, cols


def _check_measure_expr(col: str, expr: str, allowed_cols: set) -> Optional[str]:
    """度量派生表达式只允许引用已声明列（防把字段声明当裸 SQL 注入面）。"""
    for frag in FORBIDDEN_VALUE_FRAG:
        if frag in expr:
            return f"字段 {col!r} 的 expr 含非法片段 {frag!r}"
    try:
        from backend.semantics.sql_guard import parse_readonly
        from sqlglot import exp as _exp

        tree = parse_readonly(f"SELECT {expr} FROM _probe", "mysql")
        refs = {n.name for n in tree.find_all(_exp.Column)}
    except Exception:  # noqa: BLE001 — 解不动时退回保守的逐词校验
        allowed_words = {"SUM", "COUNT", "MAX", "MIN", "AVG", "NULLIF", "DISTINCT",
                         "CASE", "WHEN", "THEN", "ELSE", "END", "CAST", "IF",
                         "COALESCE", "AS", "AND", "OR", "NOT", "INTERVAL", "DAY", "DATE"}
        unknown = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)
                   if w.upper() not in allowed_words and not w.isdigit()
                   and w not in allowed_cols]
        if unknown:
            return f"字段 {col!r} 的 expr 引用了未声明列: {sorted(set(unknown))}"
        return None
    bad = sorted(r for r in refs if r and r not in allowed_cols)
    if bad:
        return f"字段 {col!r} 的 expr 引用了未声明列: {bad}"
    return None


def validate_definition(req: dict) -> tuple[list[str], dict]:
    """校验一份数据集定义。返回 (错误清单, 服务端归一化产物)。

    归一化产物含 `touched_tables`（JSON 文本）——提表结果由服务端计算，
    不接受请求体自报（护栏 §2）。
    """
    errors: list[str] = []
    sql_main = str(req.get("sql_query") or "").strip()
    dialect_label = str(req.get("dialect") or "").strip().lower()
    if dialect_label not in DATASET_DIALECTS:
        errors.append(f"dialect={dialect_label!r} 非法（只支持 {sorted(DATASET_DIALECTS - {''})}）")

    var_errors, decls = validate_variables(req.get("variables"))
    errors += var_errors

    bodies = req.get("sql_bodies")
    bodies = bodies if isinstance(bodies, dict) else parse_json_obj(bodies, None)
    if bodies is not None and not isinstance(bodies, dict):
        errors.append("sql_bodies 必须是对象（{引擎|方言族: SQL 正文}）")
        bodies = None
    if bodies:
        bad_keys = sem_dialect.unknown_variant_keys(bodies, extra_allowed=["generic"])
        if bad_keys:
            errors.append(f"sql_bodies 存在永不命中的方言键: {bad_keys}"
                          f"（{sem_dialect.variant_key_hint()}）")
        # 编译取用序是 engine → family → generic：既写多方言正文又写 sql_query 时，
        # 未声明 generic 会让"本引擎无实现时是否回退那段正文"变得不确定 → 要求自洽。
        if "generic" not in bodies and sql_main:
            errors.append(
                "sql_bodies 未声明 generic 键时 sql_query 必须为空"
                "（否则本引擎无实现时无法确定是否回退那段正文；"
                "要么把通用写法放 generic，要么只留 sql_bodies）")

    if not sql_main and not bodies:
        return errors + ["数据集作为预制 SQL 使用时必须有正文（sql_query 或 sql_bodies）"], {}

    touched: list[str] = []
    if sql_main:
        errs, tabs = probe_sql(sql_main, decls, dialect_label or "generic")
        errors += errs
        touched += tabs
    for key, body in (bodies or {}).items():
        label = str(key).strip().lower()
        if not str(body or "").strip():
            errors.append(f"sql_bodies[{label!r}] 正文为空（要显式不支持请删掉该键）")
            continue
        errs, tabs = probe_sql(str(body), decls, label)
        errors += errs
        touched += tabs

    fc_errors, _fields = validate_field_config(req.get("field_config"))
    errors += fc_errors

    dedup = sorted({t for t in touched if t})
    stored = {
        "touched_tables": (json.dumps(dedup, ensure_ascii=False) if dedup else None),
    }
    if bodies is not None:
        stored["sql_bodies"] = json.dumps(bodies, ensure_ascii=False)
    return errors, stored
