"""SLS（阿里云日志服务，PG 协议接入）远程连接器 —— psycopg2 simple query 协议。

SLS 的 PG 兼容端点**不支持 prepared statement**（parse/bind/describe/execute 扩展协议），
只支持 simple query。ADBC 驱动取结果 schema 时强制走 describe prepared statement，
对 SLS 会报 `INVALID_ARGUMENT: [libpq] Failed to describe prepared statement:
unnamed prepared statement does not exist ... SQLSTATE: 26000`（连 `SELECT 1` 都失败）。

因此 SLS 不复用 ADBC 的 PostgresConnector，改走 psycopg2：无绑定参数的
`cursor.execute(sql)` 即 libpq simple 协议（PQexec），SLS 可正常执行。连接凭据/SSL
复用 `datasource_db.get_datasource_conn`（与元数据探查同一 PG 工厂），结果按
MySQL 连接器同口径转 Arrow（dict 行 → 列元组 → 无损消歧）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pyarrow as pa

from backend.semantics.execution.connectors.base import (
    dedup_columns,
    rows_to_arrow,
    truncate_rows,
)

logger = logging.getLogger(__name__)


class SlsConnector:
    """SLS（PG 协议）远程下推执行：psycopg2 simple 协议，拉回 Arrow。"""

    db_type = "sls"

    def __init__(
        self,
        datasource_id: int = 0,
        *,
        config: Optional[dict[str, Any]] = None,
        conn_factory: Optional[Callable[[], Any]] = None,
    ):
        self._datasource_id = int(datasource_id or 0)
        self._config = config  # 跨源联邦随请求下发的连接配置（凭据不进 LLM/前端）
        self._conn_factory = conn_factory  # 测试注入：返回已就绪连接

    def _connect(self, timeout_sec: Optional[int]) -> Any:
        if self._conn_factory is not None:
            return self._conn_factory()
        row = dict(self._config or {})
        if not row:
            from backend.common.db.datasource_db import get_datasource_by_id

            row = get_datasource_by_id(self._datasource_id) or {}
        if not row.get("host"):
            raise ValueError(f"SLS 连接配置缺失: datasource_id={self._datasource_id}")

        from backend.common.db.datasource_db import get_datasource_conn

        return get_datasource_conn(
            "sls",
            row.get("host"),
            int(row.get("port") or 5432),
            row.get("user") or row.get("username") or "",
            row.get("password") or "",
            row.get("database") or row.get("database_name"),
            ssl=bool(row.get("ssl")),
            ssl_mode=row.get("ssl_mode"),
        )

    def execute_pushdown(
        self,
        sql: str,
        *,
        timeout_sec: Optional[int] = None,
        max_rows: Optional[int] = None,
    ) -> pa.Table:
        conn = self._connect(timeout_sec)
        try:
            with conn.cursor() as cur:
                # 无参数 execute → psycopg2 走 libpq simple 协议（SLS 不支持 prepared statement）
                cur.execute(sql)
                rows = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
                columns = [d[0] for d in (cur.description or [])]
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — 关闭失败不影响结果返回
                logger.warning("[sls-connector] connection close failed", exc_info=True)
        # datasource_db PG 工厂为 RealDictCursor（fetchall 返回 dict 行）；兼容 tuple 行
        tuples = [
            tuple(r.get(c) for c in columns) if isinstance(r, dict) else tuple(r)
            for r in rows
        ]
        table = rows_to_arrow(columns, tuples)
        return truncate_rows(dedup_columns(table), max_rows)
