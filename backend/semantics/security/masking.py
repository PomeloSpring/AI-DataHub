"""mask 表达式按方言生成（Phase 6.3）—— 裁决产物只带 mask 类型，表达式在引擎层生成。

enforcer 裁决产物 `masked_columns: {col: mask_type}`，mask_type ∈ `null`（完全遮蔽置空，
敏感基线 full 映射而来）/ `partial`（部分遮蔽）/ `hash`（哈希）。列表达式由本模块按
执行方言生成（远程下推转目标方言、内嵌执行 DataFusion 方言），策略层不携带 SQL 片段。

**本模块是 SQL 侧脱敏表达式的唯一实现**（`rls.apply_column_restriction` 与
`policy_views.filtered_view_sql` 共用），避免同一列在不同入口长出两种脱敏形态。
partial 与结果侧 `permission_enforcer._partial_mask` 同口径：保首尾各 2 字符、中间打星、
长度<=4 全打星。

engine 维度（同族不同实现的函数差异）：SLS 的 PG 协议端点是 **Presto 系函数子集**，
原生 PG/MySQL 写法在那儿不是报错就是静默错数，故按 engine 单开一支。
函数可用性由实机探针取证：`backend/scripts/probe_sls_mask_functions.py`。
**探针必须带真实 FROM**（2026-11-25 教训）：早期探针用无 FROM 的常量 SELECT
（`SELECT REPEAT('*', 3)`）走的是常量折叠路径，`LEFT`/`RIGHT`/`REPEAT`/`MD5(varchar)`
全部“OK”，但真实查询里逐个失效——任何查询投影到 partial 列就整条失败
（`Function left not registered` / `Expected: repeat(double, integer)` /
`Expected: md5(varbinary)`）。真实表上的结论（prd-sls-cn @ logstash_cn）：
  OK   LENGTH / CONCAT / SUBSTR(s, start, len) / GREATEST / CASE / LPAD('', n, '*') /
       CAST(x AS VARCHAR) / TO_UTF8(x) / MD5(varbinary)
  ERR  LEFT / RIGHT（未注册）/ REPEAT（第一参须 double，且星号数会与长度脱钩）/
       MD5(varchar)（须 varbinary）/ VARBINARY→VARCHAR 转换（Cannot cast）/
       SHA2 / TO_HEX / HEX / SHA1
  陷阱 `SUBSTR(x, -2)` **不报错但返回整串**（负数下标不支持）→ 取尾一律用
       `SUBSTR(x, GREATEST(LENGTH(x)-1, 1), 2)`，否则 partial mask 会把原值整串带出去。
"""

from __future__ import annotations

_PG_FAMILY = ("postgres", "postgresql", "pg")
_MYSQL_FAMILY = ("mysql", "doris", "datafusion")
_SLS_ENGINES = ("sls",)
_ES_ENGINES = ("es",)


def _es_partial_case(col_text: str) -> str:
    """ES 引擎的 partial mask：与 `_partial_case` 同口径（首尾各 2、中间打星、<=4 全打星）。

    不直接复用 `_partial_case` 是实机取证定的三件事（2026-11-13）：
    1) ES 没有 `SUBSTR`，而 Presto/默认生成器会把 `LEFT/RIGHT/SUBSTRING` 渲染成它 ——
       所以这里直接产出 ES 规范形（`SUBSTRING` 三参），不再依赖方言映射兼容；
    2) ES 的 `CONCAT` **只收 2 个参数**（三段拼接写成嵌套）；
    3) 取尾不用 `RIGHT`（避开函数系差异），用 `SUBSTRING(x, GREATEST(LENGTH(x)-1, 1), 2)`。
    产物形状已在真实集群执行验证，且经 `semantics/es_dialect` 渲染后仍为同一形式。
    """
    return (
        f"CASE WHEN LENGTH({col_text}) <= 4 THEN REPEAT('*', LENGTH({col_text})) "
        f"ELSE CONCAT(SUBSTRING({col_text}, 1, 2), "
        f"CONCAT(REPEAT('*', GREATEST(LENGTH({col_text}) - 4, 0)), "
        f"SUBSTRING({col_text}, GREATEST(LENGTH({col_text}) - 1, 1), 2))) END"
    )


def _sls_partial_case(col_text: str) -> str:
    """SLS 真实查询下的 partial：保首尾各 2、中间补**等长**星号（长度<=4 全打星）。

    不复用 `_partial_case` 的原因（2026-11-25 带 FROM 实测，见模块 docstring）：
    `LEFT`/`RIGHT` 未注册；`REPEAT('*', n)` 的第一参必须是 double，`CAST(n AS INTEGER)`
    也救不回。改用 `LPAD('', n, '*')` 生成与隐藏段等长的星号串，口径与结果侧
    `permission_enforcer._partial_mask` 一致（星号数 = 隐藏字符数）。
    短值分支单独给 `LPAD('', n, '*')`：`'ab'` → `'**'`（同样是全打星，不泄露长度）。
    """
    length = f"LENGTH({col_text})"
    return (
        f"CASE WHEN {length} <= 4 THEN LPAD('', {length}, '*') "
        f"ELSE CONCAT(SUBSTR({col_text}, 1, 2), "
        f"SUBSTR(LPAD('', {length}, '*'), 1, GREATEST({length} - 4, 0)), "
        f"SUBSTR({col_text}, GREATEST({length} - 1, 1), 2)) END"
    )


def _partial_case(col_text: str, len_fn: str = "LENGTH") -> str:
    """首尾各保 2 字符、中间打星（长度<=4 全打星）—— 与结果侧 _partial_mask 一致。

    入参 `col_text` 已是文本型表达式（各分支负责先 CAST），故此处只依赖
    LEFT/RIGHT/REPEAT/CONCAT/GREATEST + `len_fn`，这组在 mysql / pg 取证通过
    （SLS 不用本函数：LEFT/RIGHT 未注册、REPEAT 签名不符，见 `_sls_partial_case`）。
    `len_fn`：mysql 要 `CHAR_LENGTH`（`LENGTH` 在 MySQL 是**字节**数，中文会多打星），
    pg 用 `LENGTH`。
    """
    return (
        f"CASE WHEN {len_fn}({col_text}) <= 4 THEN REPEAT('*', {len_fn}({col_text})) "
        f"ELSE CONCAT(LEFT({col_text}, 2), "
        f"REPEAT('*', GREATEST({len_fn}({col_text}) - 4, 0)), RIGHT({col_text}, 2)) END"
    )


def mask_expr(column_sql: str, mask_type: str, dialect: str = "mysql",
              engine: str | None = None) -> str:
    """按方言/引擎生成列的脱敏表达式；未知类型 fail-loud（绝不放行原列）。"""
    kind = (mask_type or "null").strip().lower()
    dialect = (dialect or "mysql").lower()
    eng = (engine or "").strip().lower()
    col = str(column_sql)
    is_sls = eng in _SLS_ENGINES or dialect in _SLS_ENGINES
    is_es = eng in _ES_ENGINES or dialect == "es"

    if is_es:
        # ES SQL（Calcite 子集）：无哈希函数、CONCAT 只两参、取子串只能 SUBSTRING 三参
        if kind in ("null", "full", "block"):
            return "NULL"
        if kind == "hash":
            raise ValueError(
                "Elasticsearch 引擎没有哈希函数（MD5/SHA1/SHA2/TO_HEX 实机均 400），"
                "hash 型脱敏在本引擎不可表达：请改用 partial，或让该数据走数据源 SQL 通道")
        if kind == "partial":
            return _es_partial_case(col)
        raise ValueError(f"未知 mask 类型 {mask_type!r}（fail-loud，不得静默放行原列）")

    if is_sls:
        # SLS(Presto 子集)：CAST 到 VARCHAR、哈希走 TO_UTF8+MD5、取子串只用三参 SUBSTR。
        # 不用 LEFT/RIGHT/REPEAT：真实查询里前两个未注册、REPEAT 第一参须 double
        # （2026-11-25 实测，详见模块 docstring 的探针结论）。
        if kind in ("null", "full", "block"):
            return "NULL"
        text = f"CAST({col} AS VARCHAR)"
        if kind == "hash":
            return f"SUBSTR(MD5(TO_UTF8({text})), 1, 16)"
        if kind == "partial":
            return _sls_partial_case(text)
        raise ValueError(f"未知 mask 类型 {mask_type!r}（fail-loud，不得静默放行原列）")

    if kind in ("null", "full", "block"):
        # 置空不依赖任何函数，写裸 NULL 对各引擎（含 DataFusion 视图）最稳
        return "NULL"
    if kind == "hash":
        if dialect in _PG_FAMILY:
            return f"MD5(CAST({col} AS TEXT))"
        return f"LEFT(SHA2({col}, 256), 16)"
    if kind == "partial":
        if dialect in _PG_FAMILY:
            return _partial_case(f"CAST({col} AS TEXT)", "LENGTH")
        return _partial_case(col, "CHAR_LENGTH")
    raise ValueError(f"未知 mask 类型 {mask_type!r}（fail-loud，不得静默放行原列）")


def is_known_mask(mask_type: str) -> bool:
    return (mask_type or "").strip().lower() in ("null", "full", "block", "hash", "partial")
