"""血缘持久化服务 — 解析 SQL 并写入血缘节点/边.

供两处复用：
- datagov API /api/lineage/parse-sql（手动解析）
- dataflow 执行器（定时任务 SQL 执行成功后自动采集）

纯 SELECT 语句无写目标，extract_sql_lineage 返回空 edges，直接跳过不记录。
"""

import json
import logging

from services.shared.common.db import execute_query, execute_insert, get_datasource_by_id
from services.datagov.services.sql_lineage import extract_sql_lineage, resolve_dialect

logger = logging.getLogger(__name__)


def persist_sql_lineage(sql: str, datasource_id: int, workspace_id: int = 0) -> dict:
    """解析 SQL 并持久化血缘节点与边（幂等：同节点/同边不重复创建）.

    Returns:
        {"tables": [...], "edges": [...], "column_edges": [...],
         "nodes_created": [...], "edges_created": [...], "parse_error": None|str}
    """
    # 根据数据源类型选择 SQL 方言
    dialect = "mysql"
    try:
        ds = get_datasource_by_id(datasource_id)
        if ds:
            dialect = resolve_dialect(ds.get("db_type", "")) or "mysql"
    except Exception:
        pass

    parsed = extract_sql_lineage(sql, dialect=dialect)

    def ensure_node(node_type: str, node_id: str) -> int:
        """查找或创建血缘节点，返回主键 id."""
        row = execute_query(
            "SELECT id FROM adh_lineage_nodes WHERE node_id=%s AND node_type=%s AND workspace_id=%s LIMIT 1",
            (node_id, node_type, workspace_id),
            fetchone=True,
        )
        if row:
            return row["id"]
        metadata = None
        if node_type == "table" and node_id in parsed["table_types"]:
            metadata = {"kind": parsed["table_types"][node_id]}
        return execute_insert(
            """INSERT INTO adh_lineage_nodes
               (workspace_id, node_type, node_id, node_name, datasource_id, metadata)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                workspace_id, node_type, node_id,
                node_id.split(".")[-1], datasource_id,
                json.dumps(metadata) if metadata else None,
            ),
        )

    def ensure_edge(source_id: int, target_id: int, expr: str, confidence: float):
        """同一对节点不重复建边."""
        exists = execute_query(
            """SELECT id FROM adh_lineage_edges
               WHERE source_node_id=%s AND target_node_id=%s AND workspace_id=%s LIMIT 1""",
            (source_id, target_id, workspace_id),
            fetchone=True,
        )
        if exists:
            return exists["id"]
        return execute_insert(
            """INSERT INTO adh_lineage_edges
               (workspace_id, source_node_id, target_node_id, edge_type, transform_expr, confidence)
               VALUES (%s,%s,%s,'transform',%s,%s)""",
            (workspace_id, source_id, target_id, expr[:500], confidence),
        )

    nodes_created = []
    edges_created = []
    transform_expr = sql

    # 表级节点与边
    table_ids = {}
    for table_name in parsed["tables"]:
        tid = ensure_node("table", table_name)
        table_ids[table_name] = tid
        nodes_created.append({"id": tid, "node_id": table_name, "node_type": "table"})
    for edge in parsed["edges"]:
        eid = ensure_edge(table_ids[edge["source"]], table_ids[edge["target"]], transform_expr, 1.0)
        edges_created.append({
            "id": eid,
            "from": table_ids[edge["source"]],
            "to": table_ids[edge["target"]],
            "level": "table",
        })

    # 字段级节点与边（尽力而为）
    col_ids = {}
    for edge in parsed["column_edges"]:
        for col_fqn in (edge["source"], edge["target"]):
            if col_fqn not in col_ids:
                cid = ensure_node("column", col_fqn)
                col_ids[col_fqn] = cid
                nodes_created.append({"id": cid, "node_id": col_fqn, "node_type": "column"})
        eid = ensure_edge(col_ids[edge["source"]], col_ids[edge["target"]], transform_expr, 0.9)
        edges_created.append({
            "id": eid,
            "from": col_ids[edge["source"]],
            "to": col_ids[edge["target"]],
            "level": "column",
        })

    return {
        "tables": parsed["tables"],
        "edges": parsed["edges"],
        "column_edges": parsed["column_edges"],
        "parse_error": parsed["parse_error"],
        "nodes_created": nodes_created,
        "edges_created": edges_created,
    }
