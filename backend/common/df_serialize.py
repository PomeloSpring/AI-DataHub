"""DataFrame -> JSON 安全结果(columns + rows)的统一序列化助手。

背景(数据护城河 · 关联查询): 经统一取数出口 `execute_query_with_permission`
返回的 pandas DataFrame, 在 JOIN 且 `SELECT t1.*, t2.*` 这类场景下会带有
**重复列名**(两表都有 id / account_code / create_time ...)。而
`df.to_json(orient="records")` 严格要求列名唯一, 否则抛
`ValueError: DataFrame columns must be unique`, 导致 Playground 直连执行 500、
dataviz governed_execute 静默丢空。

本助手在序列化前对重复列名做**无损消歧**(第 2 次及以后追加 `__2 / __3 ...`),
保证每一列都可被前端按列名寻址, 既不崩溃也不丢列。所有取数出口应共用它,
以保持"所见即所执行"的返回契约一致。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _unique_columns(raw: list[str]) -> list[str]:
    """把可能重复的列名消歧为全局唯一(重复者追加 __{n}), 保持原始顺序。"""
    used: set[str] = set()
    seen: dict[str, int] = {}
    out: list[str] = []
    for col in raw:
        if col not in used:
            used.add(col)
            seen[col] = 0
            out.append(col)
            continue
        seen[col] += 1
        cand = f"{col}__{seen[col]}"
        while cand in used:  # 极端情况下原始列名本身已含 __n, 继续递增避免二次冲突
            seen[col] += 1
            cand = f"{col}__{seen[col]}"
        used.add(cand)
        out.append(cand)
    return out


def df_to_columns_rows(df: Any) -> tuple[list[str], list[dict]]:
    """DataFrame -> (columns, rows[list[dict]]), 对重复列名无损消歧且 JSON 安全。

    numpy / datetime / decimal 由 to_json 统一转成 JSON 安全值(NaN -> null)。
    任何序列化异常都兜底为空行而不抛出, 保证取数出口不会因此 500。
    """
    if df is None:
        return [], []
    try:
        raw_cols = [str(c) for c in df.columns]
    except Exception:  # noqa: BLE001 - 非预期对象, 视为空结果
        return [], []
    if not raw_cols:
        return [], []

    columns = _unique_columns(raw_cols)
    try:
        renamed = df.copy()
        renamed.columns = columns
        rows = json.loads(
            renamed.to_json(orient="records", force_ascii=False, date_format="iso", date_unit="ms")
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[df_to_columns_rows] to_json failed (%d cols): %s", len(columns), e)
        rows = []
    return columns, rows
