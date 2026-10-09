"""远程连接器协议 —— 远程表下推执行拉回 Arrow（remote_exec.rs 思路的 Python 重做）。

DataFusion 只做规划/校验/跨源联邦；远程表下推为**远程 SQL 在源库执行、拉回 Arrow**，
不在本地把大表拉进内存（护栏 §5 bounded/force_limit 执行前强制 + 计划 §10 内存风险）。
跨源联邦随连接器完善逐步启用；连接器质量不足再评估 PyO3 扩展（届时必须锁同一
DataFusion 版本 ABI），当前**不引本地 Rust 编译链**。
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

import pyarrow as pa


@runtime_checkable
class RemoteConnector(Protocol):
    """远程数据源执行协议：远程 SQL → Arrow 表。"""

    db_type: str

    def execute_pushdown(
        self,
        sql: str,
        *,
        timeout_sec: Optional[int] = None,
        max_rows: Optional[int] = None,
    ) -> pa.Table: ...


def to_pushdown_sql(sql: str, target: str) -> str:
    """执行 SQL → 目标执行方言。

    - `datafusion`：内嵌执行需 DataFusion 方言（MySQL 函数在规划层报 Invalid function）；
    - `mysql`/`doris`/`postgres`：远程下推，planner 已产目标源方言，原样下推。
    """
    target = (target or "mysql").lower()
    if target == "datafusion":
        from backend.semantics.execution.datafusion_dialect import to_datafusion

        return to_datafusion(sql)
    return sql


def rows_to_arrow(columns: list[str], rows: list[tuple]) -> pa.Table:
    """行元组列表 → Arrow 表；重复列名无损消歧（护栏 §8 同口径）。"""
    from backend.common.df_serialize import _unique_columns

    names = _unique_columns([str(c) for c in columns])
    arrays: list[pa.Array] = []
    for i, name in enumerate(names):
        arrays.append(pa.array([r[i] for r in rows]))
    return pa.Table.from_arrays(arrays, names=names)


def dedup_columns(table: pa.Table) -> pa.Table:
    """Arrow 表重复列名无损消歧（护栏 §8，col__1…）。"""
    from backend.common.df_serialize import _unique_columns

    names = [str(c) for c in table.column_names]
    unique = _unique_columns(names)
    return table.rename_columns(unique) if unique != names else table


def truncate_rows(table: pa.Table, max_rows: Optional[int]) -> pa.Table:
    """拉回行数上限（引擎层执行前强制的最后一道）。"""
    if max_rows is not None and table.num_rows > max_rows:
        return table.slice(0, max_rows)
    return table
