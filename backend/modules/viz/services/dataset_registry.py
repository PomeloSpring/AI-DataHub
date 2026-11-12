"""数据集注册校验 —— 三种定义类型各自的"存得进 = 跑得通"信任边界。

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

**ES 型数据集（source_type='es'）另走一套校验入口**（`validate_es_definition`）：
正文不是平台治理域 SQL，不能过 sqlglot（无 elasticsearch 方言，`[a.b]` 字段路径与
受限语法会被误判成"结构不受支持"），改由 `core/es_client.parse_select` 做只读/单表/
强制 LIMIT/索引白名单四件套 —— 与运行期同一个实现，两侧不会漂移。
"""

from __future__ import annotations

import fnmatch
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
# 字段显示格式令牌（时间列怎么给人看）。与前端 `lib/fieldFormat.ts` 的
# FIELD_DISPLAY_FORMATS 一一对应，新增令牌必须两侧同名改（否则前端认不出、配了不生效）。
# 空 = 不格式化（原样下发）。**数据层恒存 ISO 原值**，格式只作用于显示层：
# 取数出口 `df_to_columns_rows` 把 DATE/DATETIME 统一序列化成 `2026-10-12T00:00:00.000`
# （实测 pandas 连 `datetime.date` 也补零点+毫秒），轴标签直接拿这串会长到被截成 `2026⋯`。
FIELD_DISPLAY_FORMATS = frozenset({
    "", "date", "datetime", "datetime_s", "time", "month", "year", "md",
})

# ES 型数据集的定义类型标识（与 semantic / sql 并列的第三种）
ES_SOURCE_TYPE = "es"
# 实机校准（2026-11-06，集群 ES 8.9.1，探针 logs/probe_es_sql_shapes{,2,3,4}.py）：
# `DATE_FORMAT("@timestamp", 'yyyy-MM')` **语法通过但把格式串当常量返回**（所有行聚成
# 一桶、桶值就是 'yyyy-MM'），参数换成 ('yyyy-MM', ts) 则直接 400。这类"不报错但错数"
# 的写法静默危害最大，故在保存这一关硬拦，日期桶一律引导到实测可用的 DATE_TRUNC。
_ES_SILENTLY_WRONG_FUNCS = ("DATE_FORMAT",)
# 实机确认受 bucket 的粒度（提示语用；其余粒度交给 ES 自行报错，不做无凭据的白名单）
_ES_TRUNC_GRAINS_VERIFIED = ("MINUTE", "HOUR", "DAY", "WEEK", "MONTH", "QUARTER")

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


def is_es_definition(raw: dict) -> bool:
    """该定义是否 ES 型：显式 `source_type='es'`，或登记了索引范围 `es_pattern`。

    两个入口都要认：更新时 patch 常只带 `es_pattern` 不带 source_type（与看板图表
    挂载校验回读现有行拼有效口径的做法一致）。
    """
    body = raw or {}
    if str(body.get("source_type") or "").strip().lower() == ES_SOURCE_TYPE:
        return True
    return bool(str(body.get("es_pattern") or "").strip())


def pattern_covers(pattern: str, target: str) -> bool:
    """数据集声明的索引范围是否覆盖正文 FROM 目标（fnmatch，与 `es_client` 同口径）。

    这一步是数据集级**收窄**：白名单决定全平台能查什么，`es_pattern` 决定这份数据集
    能查什么。不校这一条，数据集写着 `logs-*`、正文却能查同白名单内的其它模式，
    图表复用时范围就隐形扩大了。
    """
    return fnmatch.fnmatchcase(str(target or ""), str(pattern or ""))


def probe_es_sql(sql: str, variables: dict, pattern: str) -> tuple[list[str], list[str]]:
    """ES 正文校验：渲染占位符 → `es_client.parse_select`（与执行期同一个实现）。

    返回 (错误清单, 正文触及的索引名)。不用 sqlglot（无 ES 方言会误杀合法写法），
    也不做"解不动就当合法"的宽松回落：解析不通过一律拒。
    """
    from backend.core import es_client

    errors: list[str] = []
    text = str(sql or "").strip()
    if not text:
        return ["ES 数据集必须有执行正文（ES SQL）"], []

    silently_wrong = next((f for f in _ES_SILENTLY_WRONG_FUNCS
                           if re.search(r"\b" + f + r"\(", text, re.IGNORECASE)), "")
    if silently_wrong:
        grains = "|".join(_ES_TRUNC_GRAINS_VERIFIED)
        errors.append(
            f"{silently_wrong}() 在本集群实测会静默给出错数（语法通过但把格式串当常量返回，"
            f"所有行被聚成一桶）；日期桶请改用 DATE_TRUNC('{{{grains}}}', \"字段名\")")

    decl = variables if isinstance(variables, dict) else {}
    probe = PLACEHOLDER_RE.sub(lambda m: _dummy_for(decl.get(m.group(1)) or {}), text)
    if "${" in probe:
        errors.append("正文存在写法非法的占位符（应为 ${name}）")
    try:
        es_client.parse_select(probe)
    except es_client.EsError as exc:
        # EsError 文案按约定只含索引名/字段名与业务原因，可安全回给配置人
        errors.append(f"ES 正文未通过校验: {str(exc)[:220]}")
        return errors, []
    except Exception as exc:  # noqa: BLE001 — 校验器自身异常也不得默认放行
        return [f"ES 正文校验不可用: {str(exc)[:160]}"], []

    indexes = es_client.from_indexes(probe)
    want = str(pattern or "").strip()
    for idx in indexes:
        if want and not pattern_covers(want, idx):
            errors.append(
                f"正文 FROM 的索引「{idx}」超出本数据集已选范围「{want}」；"
                f"请改成范围内的模式，或重新选择索引范围（不要靠改范围括大可查面）")
    return errors, indexes


def validate_es_definition(req: dict) -> tuple[list[str], dict]:
    """ES 型数据集定义校验：范围在清单内 + 正文可解析且在范围内 + 参数/字段可信任。

    与 sql/semantic 型的差异是**有意**的，不是简版实现：
    - `sql_bodies`/`dialect` 必须为空：ES SQL 只有一种方言，按引擎分正文没有命中可能；
    - `object_key` 必须为空：ES 不进本体/治理域，双定义在此不成立；
    - `field_config` 的度量不得带 `expr`：字段层派生表达式靠 sqlglot 解析并拼回 SQL，
      ES 上不可用；聚合请写在正文里（宁拒不误存下一段永不生效的声明）。
    """
    from backend.core import es_client

    errors: list[str] = []
    pattern = str(req.get("es_pattern") or "").strip()
    sql = str(req.get("sql_query") or "").strip()

    if str(req.get("dialect") or "").strip():
        errors.append("ES 数据集不支持 dialect 标注（ES SQL 只有一种方言）")
    # 先归一形状再判空：更新路径会把存量行的 sql_bodies 以 JSON **字符串** 合进来，
    # 直接拿值比 `{}` 会把空定义误判成“不支持”（历史缺陷：ES 数据集改正文保存即被拒）。
    bodies = req.get("sql_bodies")
    if isinstance(bodies, str):
        bodies = parse_json_obj(bodies, {}) or {}
    if bodies not in (None, "", {}, []):
        errors.append("ES 数据集不支持 sql_bodies（按引擎分正文在 ES 上永不命中）")
    if str(req.get("object_key") or "").strip():
        errors.append("ES 数据集不能同时绑定本体对象：ES 不进治理域，语义对象与它不是一种定义")

    if not pattern:
        errors.append("ES 数据集必须选择索引范围（es_pattern）")
    elif not es_client.index_allowed(pattern):
        errors.append(
            f"索引范围「{pattern}」不在当前部署开放的范围内；当前可查: "
            f"{', '.join(es_client.allowed_patterns()) or '（无，部署侧尚未登记）'}")

    var_errors, decls = validate_variables(req.get("variables"))
    errors += var_errors

    body_errors, indexes = probe_es_sql(sql, decls, pattern)
    errors += body_errors

    fc_errors, _cols = validate_field_config(req.get("field_config"))
    errors += fc_errors
    cols = req.get("field_config")
    cols = cols if isinstance(cols, list) else parse_json_obj(cols, None)
    for c in (cols or []):
        if isinstance(c, dict) and str(c.get("expr") or "").strip():
            errors.append(f"字段 {c.get('field')!r} 的 expr 在 ES 数据集不可用："
                          f"请把聚合写进正文（如 SUM(x)、COUNT(DISTINCT y)）")

    dedup = sorted({t for t in indexes if t})
    return errors, {
        "touched_tables": (json.dumps(dedup, ensure_ascii=False) if dedup else None),
        "sql_bodies": "{}",
    }


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
        # 悬空格式令牌是"配了但永远渲染不出来"的死键（同 sql_bodies 拼错键那一类），存前就拒
        fmt = str(c.get("display_format") or "").strip()
        if fmt not in FIELD_DISPLAY_FORMATS:
            errors.append(f"字段 {field!r} 的 display_format={fmt!r} 非法（只支持 "
                          f"{sorted(FIELD_DISPLAY_FORMATS - {''})}）")
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

    分派：ES 型走 `validate_es_definition`（不能过 sqlglot，也不能走只读治理校验），
    其余保持原有的方言正文 + 参数 + 字段校验链。
    """
    if is_es_definition(req):
        return validate_es_definition(req)

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
