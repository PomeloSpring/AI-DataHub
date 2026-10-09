"""远程连接器工厂 —— 按数据源协议分派；不支持即 fail-loud（no-silent-degradation）。"""

from __future__ import annotations

from typing import Any

from backend.semantics.execution.connectors.base import RemoteConnector


def get_connector(db_type: str, datasource_id: int = 0, **kwargs: Any) -> RemoteConnector:
    db_type = (db_type or "mysql").lower()
    if db_type in ("mysql", "doris"):
        from backend.semantics.execution.connectors.mysql import MySQLConnector

        return MySQLConnector(datasource_id=datasource_id, **kwargs)
    if db_type in ("postgres", "postgresql", "pg"):
        # 计划 §6.2：Postgres 走 ADBC 连接器（随引擎层迭代补齐），未就绪前显式失败
        raise NotImplementedError(
            "Postgres/ADBC 远程连接器尚未实现（计划 §6.2）；不得回退为直连绕过引擎层"
        )
    raise ValueError(f"不支持的远程连接器 db_type={db_type!r}")


__all__ = ["RemoteConnector", "get_connector"]
