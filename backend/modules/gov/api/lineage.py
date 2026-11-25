"""Data Lineage API — 表/字段级血缘查询和管理."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from backend.common.db import (
    execute_query, execute_insert, execute_write,
)
from backend.modules.gov.services.lineage_service import (
    persist_sql_lineage, rebuild_lineage,
)

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


class LineageRebuildRequest(BaseModel):
    """全量重建血缘。dry_run 只统计待归一节点，不写库。"""
    workspace_id: int = 0
    include_inactive_dag: bool = True
    dry_run: bool = False
    reset_asset: bool = False


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
            "SELECT * FROM adh_lineage_nodes WHERE node_id LIKE %s AND workspace_id IN (%s, 0) AND node_type='table' LIMIT 1",
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
                   WHERE e.target_node_id = %s AND e.workspace_id IN (%s, 0)""",
                (node_id, workspace_id),
            )

        if direction in ("downstream", "both"):
            downstream = execute_query(
                """SELECT n.*, e.edge_type, e.transform_expr, e.confidence
                   FROM adh_lineage_edges e
                   JOIN adh_lineage_nodes n ON n.id = e.target_node_id
                   WHERE e.source_node_id = %s AND e.workspace_id IN (%s, 0)""",
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
            "SELECT * FROM adh_lineage_nodes WHERE node_id LIKE %s AND workspace_id IN (%s, 0) AND node_type='column' LIMIT 1",
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


def _parse_metadata(value) -> dict:
    """metadata 归一为对象：DB 存 JSON 字符串，必须解析后再下发（
    否则前端 Object.entries 会把字符串按字符展开）。解析失败降级为空对象。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


@router.get("/graph")
def get_lineage_graph(
    workspace_id: int = Query(0),
    datasource: str = Query("", description="按数据源业务名过滤（空=全部）"),
    node_types: str = Query("", description="按节点类型过滤，逗号分隔（空=全部）"),
):
    """获取血缘图（用于可视化）。

    输出对齐前端图表契约：节点带 name（node_name 优先、node_id 兜底，
    不得回退裸类型标签）；边带 source_id/target_id（与内部 source_node_id
    并存，两套字段同值）；metadata 归一为对象（DB 存 JSON 字符串，
    不解析会让前端按字符展开）。

    `datasources` 返回图中实际出现的数据源业务名（来自 `数据源.表` 节点前缀），
    供前端筛选下拉；表节点的 `datasource_name` 同样按前缀回填，前端不拼 id。

    数据源筛选 = 该源的表 + 沿血缘边**正向可达**的资产（产品/本体对象/口径/数据集/图表）。
    只留表节点会把下游整段链截掉（图上只剩孤立表，看起来像“血缘断了”）。
    `datasources` 始终按全量节点计算：源码过滤后仍需能切回其他源。
    """
    try:
        rows = execute_query(
            "SELECT id, node_type, node_id, node_name, datasource_id, metadata "
            "FROM adh_lineage_nodes WHERE workspace_id IN (%s, 0)",
            (workspace_id,),
        )
        wanted = {t.strip() for t in str(node_types or "").split(",") if t.strip()}
        ds_filter = str(datasource or "").strip()
        all_nodes = []
        for n in (rows or []):
            node_id = str(n.get("node_id") or "")
            ds_name = node_id.split(".")[0] if n.get("node_type") == "table" and "." in node_id else ""
            all_nodes.append({**n,
                              "name": n.get("node_name") or node_id,
                              "datasource_name": ds_name,
                              "metadata": _parse_metadata(n.get("metadata"))})
        datasources = sorted({n["datasource_name"] for n in all_nodes if n["datasource_name"]})

        edge_rows = execute_query(
            "SELECT id, source_node_id, target_node_id, edge_type, transform_expr, confidence "
            "FROM adh_lineage_edges WHERE workspace_id IN (%s, 0)",
            (workspace_id,),
        )
        all_edges = [{**e, "source_id": e.get("source_node_id"),
                      "target_id": e.get("target_node_id")}
                     for e in (edge_rows or [])]

        if ds_filter:
            keep = {n["id"] for n in all_nodes if n["datasource_name"] == ds_filter}
            adj: dict = {}
            for e in all_edges:
                adj.setdefault(e["source_id"], []).append(e["target_id"])
            stack = list(keep)
            while stack:
                for nxt in adj.get(stack.pop(), ()):
                    if nxt not in keep:
                        keep.add(nxt)
                        stack.append(nxt)
        else:
            keep = {n["id"] for n in all_nodes}

        nodes = [n for n in all_nodes if n["id"] in keep
                 and (not wanted or n.get("node_type") in wanted)]
        node_ids = {n["id"] for n in nodes}
        edges = [e for e in all_edges
                 if e.get("source_id") in node_ids and e.get("target_id") in node_ids]
        return {"nodes": nodes, "edges": edges, "datasources": datasources}
    except Exception as e:
        logger.error("Get lineage graph failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rebuild")
def rebuild_lineage_graph(req: LineageRebuildRequest):
    """全量重建血缘（幂等，可重复执行）。

    先归一历史表/字段节点 id 到「数据源业务名.表[.列]」（合并同名双节点、迁移边），
    再重算资产链（产品/本体/口径/数据集/图表）与 DAG 定义层表级边。
    `dry_run=true` 只统计待归一数量，不写库；`reset_asset=true` 先清空派生的
    资产层（节点+边，不动表/字段与 transform 边）再重算，仅初始化/口径变更时用。
    """
    try:
        return rebuild_lineage(
            workspace_id=req.workspace_id,
            include_inactive_dag=req.include_inactive_dag,
            dry_run=req.dry_run,
            reset_asset=req.reset_asset,
        )
    except Exception as e:
        logger.error("Rebuild lineage failed: %s", e)
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
