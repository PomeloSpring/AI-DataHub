"""治理联邦读取 — 同步/SQL 任务的取数通道（统一治理入口）。

所有取数一律经 `execute_query_with_permission`（护栏 §1，不可旁路）：
- 敏感基线 / RBAC / RLS / 审计全程生效，身份由服务端注入（任务 owner）；
- 跨源 SQL：三段式限定名 `ds.db.table`，附加数据源由服务端按 name 解析后
  随请求下发给 dataengine 联邦 session（凭据仅服务端内部通道，不进 LLM/前端）；
- 解析失败/拒绝一律显式抛出，不静默切换数据源（no-silent-degradation）。
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def governed_federated_read(sql: str, source_datasource_name: str,
                            user_context: dict, workspace_id: int = 0,
                            database: str = "") -> tuple:
    """执行治理读取，返回 (DataFrame, rows_read)。

    source_datasource_name: 主数据源名（会话生效源）；
    SQL 中三段式限定名引用的其他源自动解析为联邦源。
    """
    from services.shared.common.db import get_datasource_by_name
    from services.datamind.nl2sql.sql.query_executor import execute_query_with_permission

    source = get_datasource_by_name(source_datasource_name)
    if not source:
        raise ValueError(f"数据源 '{source_datasource_name}' 不存在")

    # 从 SQL 限定名自动推导联邦源（三段式 ds.db.table 的首段 = 数据源名），
    # 主源自身不重复列入
    from services.shared.semantics.sql_guard import extract_tables
    federated_names = []
    for table in extract_tables(sql):
        parts = table.split(".")
        if len(parts) == 3 and parts[0] != source_datasource_name:
            federated_names.append(parts[0])

    df, _elapsed_ms, rows = execute_query_with_permission(
        sql=sql,
        datasource_id=int(source["id"]),
        user_context=user_context,
        workspace_id=workspace_id,
        database=database,
        federated_names=federated_names or None,
    )
    if df is None:
        df = pd.DataFrame()
    return df, int(rows or 0)


def governed_read_dataframe(sql: str, datasource_name: str, user_context: dict,
                            workspace_id: int = 0) -> pd.DataFrame:
    """单源治理读取（表级同步用）。"""
    df, _rows = governed_federated_read(sql, datasource_name, user_context, workspace_id)
    return df
