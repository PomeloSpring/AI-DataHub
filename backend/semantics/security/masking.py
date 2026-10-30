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
函数可用性由实机探针取证：`backend/scripts/probe_sls_mask_functions.py`
（2026-10-30 @ arms-prd-en）——
  OK   LEFT / RIGHT / LENGTH / CHAR_LENGTH / CONCAT / REPEAT / GREATEST / CASE /
       CAST(x AS VARCHAR) / MD5(varchar)
  ERR  to_utf8 / VARBINARY / SHA2(x,256) / TO_HEX / HEX / SHA1 / SHA256(varchar)
  陷阱 `SUBSTR(x, -2)` **不报错但返回整串**（负数下标不支持）→ 取尾一律用 RIGHT，
       否则 partial mask 会把原值整串带出去（等于不脱敏）。
"""

from __future__ import annotations

_PG_FAMILY = ("postgres", "postgresql", "pg")
_MYSQL_FAMILY = ("mysql", "doris", "datafusion")
_SLS_ENGINES = ("sls",)


def _partial_case(col_text: str, len_fn: str = "LENGTH") -> str:
    """首尾各保 2 字符、中间打星（长度<=4 全打星）—— 与结果侧 _partial_mask 一致。

    入参 `col_text` 已是文本型表达式（各分支负责先 CAST），故此处只依赖
    LEFT/RIGHT/REPEAT/CONCAT/GREATEST + `len_fn`，这组在 mysql / pg / sls 都取证通过。
    `len_fn`：mysql 要 `CHAR_LENGTH`（`LENGTH` 在 MySQL 是**字节**数，中文会多打星），
    pg/sls 用 `LENGTH`（SLS 实测 OK）。
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

    if is_sls:
        # SLS(Presto 子集)：CAST 到 VARCHAR、哈希只能 MD5、取尾只能 RIGHT（见模块 docstring）
        if kind in ("null", "full", "block"):
            return "NULL"
        if kind == "hash":
            return f"SUBSTR(MD5(CAST({col} AS VARCHAR)), 1, 16)"
        if kind == "partial":
            return _partial_case(f"CAST({col} AS VARCHAR)", "LENGTH")
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
