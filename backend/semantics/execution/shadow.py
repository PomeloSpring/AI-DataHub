"""shadow 双跑对拍（Phase 7.1）—— dataengine 留作对照后端，新引擎通道对拍。

主通道（现役 `execute_query_with_permission`，可经 dataengine/直连）结果为准返回；
新引擎通道（`semantics.execution` 远程下推）同参执行并**对拍**（列名序列/行数/行值），
差异落结构化日志供 eval + 护城河回归归因；任何不一致**先修根因再切**
（no-silent-degradation）。shadow 侧异常仅记日志，绝不影响主链路（fail-safe）。

- 开关：`SEMANTIC_ENGINE_SHADOW=true` 开启双跑（默认关）；
- 行数对拍口径 `min(len, 5000)`（与 gates 执行截断边界对齐）、行值对拍前 100 行；
- Phase 7.2 切流开关 `SEMANTIC_ENGINE_ENABLED` 落地后本通道转正、dataengine 退役。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

SHADOW_ENABLED = os.getenv("SEMANTIC_ENGINE_SHADOW", "false").lower() == "true"

_COMPARE_ROW_LIMIT = 100   # 行值对拍上限（大结果只拍头部）
_ROW_CAP = 5000            # 行数对拍口径（与 gates head(5000) 截断边界对齐）
_MAX_DIFFS = 20            # 单次对拍差异条数上限（防日志爆炸）


def _norm(v: Any) -> str:
    return "NULL" if v is None else str(v)


def compare_results(
    main_columns: Sequence[str],
    main_rows: Sequence[dict],
    shadow_columns: Sequence[str],
    shadow_rows: Sequence[dict],
) -> list[str]:
    """两通道结果对拍，返回差异描述（空列表 = 一致）。"""
    diffs: list[str] = []
    if list(main_columns) != list(shadow_columns):
        diffs.append(f"columns: main={list(main_columns)} shadow={list(shadow_columns)}")

    main_n, shadow_n = min(len(main_rows), _ROW_CAP), min(len(shadow_rows), _ROW_CAP)
    if main_n != shadow_n:
        diffs.append(f"row_count: main={main_n} shadow={shadow_n}")

    for i in range(min(len(main_rows), len(shadow_rows), _COMPARE_ROW_LIMIT)):
        mr, sr = main_rows[i], shadow_rows[i]
        for c in main_columns:
            mv, sv = _norm(mr.get(c)), _norm(sr.get(c))
            if mv != sv:
                diffs.append(f"row[{i}].{c}: main={mv!r} shadow={sv!r}")
                if len(diffs) >= _MAX_DIFFS:
                    diffs.append("...(diffs truncated)")
                    return diffs
    return diffs


def shadow_check(
    sql: str,
    *,
    main_columns: Sequence[str],
    main_rows: Sequence[dict],
    datasource_id: int = 0,
    db_type: Optional[str] = None,
    dialect: str = "mysql",
    guardrail: Any = None,
    max_rows: Optional[int] = None,
    tag: str = "",
) -> Optional[list[str]]:
    """新引擎通道同参执行并对拍；返回差异列表（None = 未启用/跳过）。

    fail-safe：shadow 侧任何异常（缺连接器/超时/表未建模…）仅记日志，
    绝不影响主链路结果返回；对拍差异以 WARNING 结构化落日志。
    """
    if not SHADOW_ENABLED:
        return None
    try:
        if not db_type:
            from backend.common.db.datasource_db import get_datasource_by_id

            row = get_datasource_by_id(int(datasource_id or 0)) or {}
            db_type = row.get("db_type") or "mysql"

        from backend.semantics.execution.engine import SemanticEngine

        table = SemanticEngine().execute_pushdown(
            sql,
            datasource_id=int(datasource_id or 0),
            db_type=db_type,
            guardrail=guardrail,
            dialect=dialect,
            max_rows=max_rows,
        )
        shadow_columns = list(table.column_names)
        shadow_rows = table.to_pylist()
        diffs = compare_results(main_columns, main_rows, shadow_columns, shadow_rows)
        if diffs:
            logger.warning(
                "[shadow] 通道对拍不一致 tag=%s ds=%s db=%s diffs=%s",
                tag, datasource_id, db_type, diffs,
            )
        else:
            logger.info(
                "[shadow] 通道对拍一致 tag=%s ds=%s rows=%s",
                tag, datasource_id, len(main_rows),
            )
        return diffs
    except Exception as e:  # noqa: BLE001 — shadow 绝不影响主链路
        logger.warning("[shadow] 新引擎通道执行失败（不影响主链路）tag=%s: %s", tag, e)
        return None
