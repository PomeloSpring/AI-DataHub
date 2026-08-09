"""Data Lineage API — 表/字段级血缘查询和管理."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from services.shared.common.db import (
    execute_query, execute_insert, execute_write,
)
from services.datagov.services.lineage_service import persist_sql_lineage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["数据血缘"])


class LineageNodeCreate(BaseModel):
    node_type: str
    node_id: str
    node_name: Optional[str] = None
    datasource_id: Optional[int] = None
    metadata: Optional[dict] = None


class LineageEdgeCreate(BaseModel):
    source_node_id: int
    target_node_id: int
    edge_type: str = "transform"
    transform_expr: Optional[str] = None
    confidence: float = 1.0


class SQLParseRequest(BaseModel):
    sql: str
    datasource_id: int
    workspace_id: int = 0


@router.get("/tables/{table_name}")
def get_table_lineage(
    table_name: str,
    workspace_id: int = Query(0),
    direction: str = Query("both", regex="^(upstream|downstream|both)$"),
):
    """获取表级血缘（上游 + 下游）."""
    try:
        # Find the node
        node = execute_query(
            "SELECT * FROM adh_lineage_nodes WHERE node_id LIKE %s AND workspace_id = %s AND node_type='table' LIMIT 1",
            (f"%{table_name}%", workspace_id),
            fetchone=True,
        )
        if not node:
            return {"node": None, "upstream": [], "downstream": []}

        node_id = node["id"]
        upstream = []
        downstream = []

        if direction in ("upstream", "both"):
            upstream = execute_query(
                """SELECT n.*, e.edge_type, e.transform_expr, e.confidence
                   FROM adh_lineage_edges e
                   JOIN adh_lineage_nodes n ON n.id = e.source_node_id
                   WHERE e.target_node_id = %s AND e.workspace_id = %s""",
                (node_id, workspace_id),
            )

        if direction in ("downstream", "both"):
            downstream = execute_query(
                """SELECT n.*, e.edge_type, e.transform_expr, e.confidence
                   FROM adh_lineage_edges e
                   JOIN adh_lineage_nodes n ON n.id = e.target_node_id
                   WHERE e.source_node_id = %s AND e.workspace_id = %s""",
                (node_id, workspace_id),
            )

        return {"node": node, "upstream": upstream, "downstream": downstream}
    except Exception as e:
        logger.error("Get table lineage failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/columns/{table_name}.{column_name}")
def get_column_lineage(
    table_name: str,
    column_name: str,
    workspace_id: int = Query(0),
):
    """获取字段级血缘."""
    try:
        node_id_pattern = f"{table_name}.{column_name}"
        node = execute_query(
            "SELECT * FROM adh_lineage_nodes WHERE node_id LIKE %s AND workspace_id = %s AND node_type='column' LIMIT 1",
            (f"%{node_id_pattern}%", workspace_id),
            fetchone=True,
        )
        if not node:
            return {"node": None, "upstream": [], "downstream": []}

        upstream = execute_query(
            """SELECT n.*, e.edge_type, e.transform_expr
               FROM adh_lineage_edges e JOIN adh_lineage_nodes n ON n.id = e.source_node_id
               WHERE e.target_node_id = %s""",
            (node["id"],),
        )
        downstream = execute_query(
            """SELECT n.*, e.edge_type, e.transform_expr
               FROM adh_lineage_edges e JOIN adh_lineage_nodes n ON n.id = e.target_node_id
               WHERE e.source_node_id = %s""",
            (node["id"],),
        )
        return {"node": node, "upstream": upstream, "downstream": downstream}
    except Exception as e:
        logger.error("Get column lineage failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes")
def create_lineage_node(req: LineageNodeCreate, workspace_id: int = Query(0)):
    """创建血缘节点."""
    try:
        node_id = execute_insert(
            """INSERT INTO adh_lineage_nodes (workspace_id, node_type, node_id, node_name, datasource_id, metadata)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (workspace_id, req.node_type, req.node_id, req.node_name, req.datasource_id, json.dumps(req.metadata) if req.metadata else None),
        )
        return {"id": node_id, "success": True}
    except Exception as e:
        logger.error("Create lineage node failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/edges")
def create_lineage_edge(req: LineageEdgeCreate, workspace_id: int = Query(0)):
    """创建血缘边."""
    try:
        edge_id = execute_insert(
            """INSERT INTO adh_lineage_edges (workspace_id, source_node_id, target_node_id, edge_type, transform_expr, confidence)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (workspace_id, req.source_node_id, req.target_node_id, req.edge_type, req.transform_expr, req.confidence),
        )
        return {"id": edge_id, "success": True}
    except Exception as e:
        logger.error("Create lineage edge failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/graph")
def get_lineage_graph(workspace_id: int = Query(0)):
    """获取完整血缘图（用于可视化）."""
    try:
        nodes = execute_query(
            "SELECT id, node_type, node_id, node_name, metadata FROM adh_lineage_nodes WHERE workspace_id = %s",
            (workspace_id,),
        )
        edges = execute_query(
            "SELECT id, source_node_id, target_node_id, edge_type, transform_expr, confidence FROM adh_lineage_edges WHERE workspace_id = %s",
            (workspace_id,),
        )
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        logger.error("Get lineage graph failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/impact/{node_id}")
def impact_analysis(node_id: int, workspace_id: int = Query(0)):
    """影响分析：如果这个节点变更，哪些下游会受影响."""
    try:
        visited = set()
        queue = [node_id]
        impact_path = []

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            downstream = execute_query(
                """SELECT e.target_node_id, n.node_name, n.node_type, e.edge_type
                   FROM adh_lineage_edges e
                   JOIN adh_lineage_nodes n ON n.id = e.target_node_id
                   WHERE e.source_node_id = %s""",
                (current,),
            )
            for d in downstream:
                impact_path.append({
                    "from_node_id": current,
                    "to_node_id": d["target_node_id"],
                    "to_node_name": d["node_name"],
                    "to_node_type": d["node_type"],
                    "edge_type": d["edge_type"],
                })
                queue.append(d["target_node_id"])

        return {"root_node_id": node_id, "impact_count": len(visited) - 1, "impact_path": impact_path}
    except Exception as e:
        logger.error("Impact analysis failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parse-sql")
def parse_sql_lineage(req: SQLParseRequest):
    """解析SQL提取血缘关系（sqlglot，支持 CTE/子查询/JOIN/视图，表级 + 字段级）."""
    try:
        result = persist_sql_lineage(req.sql, req.datasource_id, req.workspace_id)
        if result["parse_error"]:
            raise HTTPException(status_code=400, detail=f"SQL 解析失败: {result['parse_error']}")
        return {
            "nodes_created": result["nodes_created"],
            "edges_created": result["edges_created"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Parse SQL lineage failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
