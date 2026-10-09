"""mask 表达式按方言生成（Phase 6.3）—— 裁决产物只带 mask 类型，表达式在引擎层生成。

enforcer 裁决产物 `masked_columns: {col: mask_type}`，mask_type ∈ `null`（完全遮蔽置空，
敏感基线 full 映射而来）/ `partial`（部分遮蔽）/ `hash`（哈希）。列表达式由本模块按
执行方言生成（远程下推转目标方言、内嵌执行 DataFusion 方言），策略层不携带 SQL 片段。
"""

from __future__ import annotations

_PG_FAMILY = ("postgres", "postgresql", "pg")
_MYSQL_FAMILY = ("mysql", "doris", "datafusion")


def mask_expr(column_sql: str, mask_type: str, dialect: str = "mysql") -> str:
    """按方言生成列的脱敏表达式；未知类型 fail-loud（绝不放行原列）。"""
    kind = (mask_type or "null").strip().lower()
    dialect = (dialect or "mysql").lower()
    col = str(column_sql)

    if kind in ("null", "full", "block"):
        return "NULL"
    if kind == "hash":
        if dialect in _PG_FAMILY:
            return f"MD5(CAST({col} AS TEXT))"
        return f"MD5({col})"
    if kind == "partial":
        if dialect in _PG_FAMILY:
            return f"CONCAT(LEFT(CAST({col} AS TEXT), 2), '***')"
        return f"CONCAT(LEFT({col}, 2), '***')"
    raise ValueError(f"未知 mask 类型 {mask_type!r}（fail-loud，不得静默放行原列）")


def is_known_mask(mask_type: str) -> bool:
    return (mask_type or "").strip().lower() in ("null", "full", "block", "hash", "partial")
