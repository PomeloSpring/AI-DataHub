"""Postgres 远程连接器（ADBC，Arrow 原生拉取）—— 远程 SQL 源库执行拉回 Arrow。

计划 §6.2 远程连接器（Postgres 走 ADBC）：`statement_timeout` 执行超时强制
（护栏 §5），行数上限拉回后截断兜底；连接凭据复用 `datasource_db` 配置
（解密后拼 URI），亦支持 `config` 直传（跨源联邦随请求下发的连接配置）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional
from urllib.parse import quote_plus

import pyarrow as pa

from backend.semantics.execution.connectors.base import dedup_columns, truncate_rows

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 30


class PostgresConnector:
    """Postgres 远程下推执行（ADBC）。"""

    db_type = "postgres"

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
            raise ValueError(f"Postgres 连接配置缺失: datasource_id={self._datasource_id}")

        uri = "postgresql://{}:{}@{}:{}/{}".format(
            quote_plus(str(row.get("user") or row.get("username") or "")),
            quote_plus(str(row.get("password") or "")),
            row.get("host"),
            int(row.get("port") or 5432),
            row.get("database") or row.get("database_name") or "",
        )
        timeout_ms = int((timeout_sec or _DEFAULT_TIMEOUT_SEC) * 1000)
        import adbc_driver_postgresql.dbapi as pg_adbc

        return pg_adbc.connect(
            uri,
            conn_kwargs={"options": f"-c statement_timeout={timeout_ms}"},
            autocommit=True,
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
                table = cur.fetch_arrow_table()
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — 关闭失败不影响结果返回
                logger.warning("[postgres-connector] connection close failed", exc_info=True)
        return truncate_rows(dedup_columns(table), max_rows)
