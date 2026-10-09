"""MySQL 协议远程连接器（MySQL/Doris 通用）—— 远程 SQL 源库执行，拉回 Arrow。

连接复用 `backend.common.db.datasource_db`（连接池/凭据解密）；执行超时走
read_timeout（护栏 §5 timeout 执行前强制），行数上限拉回后截断兜底。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pyarrow as pa

from backend.semantics.execution.connectors.base import rows_to_arrow, truncate_rows

logger = logging.getLogger(__name__)

_DEFAULT_READ_TIMEOUT = 30


class MySQLConnector:
    """MySQL/Doris（MySQL 协议）远程下推执行。"""

    db_type = "mysql"

    def __init__(
        self,
        datasource_id: int = 0,
        *,
        conn_factory: Optional[Callable[[], Any]] = None,
    ):
        self._datasource_id = int(datasource_id or 0)
        self._conn_factory = conn_factory  # 测试注入：返回已就绪连接

    def _connect(self, timeout_sec: Optional[int]) -> Any:
        if self._conn_factory is not None:
            return self._conn_factory()
        from backend.common.db.datasource_db import (
            get_datasource_by_id,
            get_datasource_conn,
        )

        row = get_datasource_by_id(self._datasource_id)
        if not row:
            raise ValueError(f"数据源不存在: datasource_id={self._datasource_id}")
        return get_datasource_conn(
            row.get("db_type") or "mysql",
            row.get("host"), int(row.get("port") or 3306),
            row.get("user") or row.get("username") or "",
            row.get("password") or "",
            row.get("database"),
            ssl=bool(row.get("ssl")), ssl_mode=row.get("ssl_mode"),
            read_timeout=int(timeout_sec or _DEFAULT_READ_TIMEOUT),
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
                cur.execute(sql)
                rows = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
                columns = [d[0] for d in (cur.description or [])]
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — 关闭失败不影响结果返回
                logger.warning("[mysql-connector] connection close failed", exc_info=True)
        table = rows_to_arrow(columns, [tuple(r) for r in rows])
        return truncate_rows(table, max_rows)
