"""Knowledge Graph API — Neo4j 图查询接口."""

import json
import logging
import os
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

logger = logging.getLogger(__name__)
router = APIRouter()

# Neo4j connection (lazy init)
_driver = None


def get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


class CypherQuery(BaseModel):
    query: str
    parameters: Optional[dict] = None


class NodeCreate(BaseModel):
    labels: List[str]
    properties: dict


class RelationCreate(BaseModel):
    source_id: str
    target_id: str
    relation_type: str
    properties: Optional[dict] = None


@router.post("/query")
async def execute_cypher(req: CypherQuery):
    """执行 Cypher 查询."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(req.query, req.parameters or {})
            records = [dict(r) for r in result]
        return {"records": records, "count": len(records)}
    except Exception as e:
        logger.error("Cypher query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes")
async def list_nodes(
    label: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
):
    """列出节点."""
    try:
        driver = get_driver()
        with driver.session() as session:
            if label:
                result = session.run(f"MATCH (n:{label}) RETURN n LIMIT $limit", {"limit": limit})
            else:
                result = session.run("MATCH (n) RETURN n LIMIT $limit", {"limit": limit})
            nodes = []
            for r in result:
                node = r["n"]
                nodes.append({
                    "id": node.element_id,
                    "labels": list(node.labels),
                    "properties": dict(node),
                })
        return {"nodes": nodes, "count": len(nodes)}
    except Exception as e:
        logger.error("List nodes failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_id}")
async def get_node(node_id: str):
    """获取节点详情."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run("MATCH (n) WHERE elementId(n) = $id RETURN n", {"id": node_id})
            record = result.single()
            if not record:
                raise HTTPException(status_code=404, detail="Node not found")
            node = record["n"]
            return {
                "id": node.element_id,
                "labels": list(node.labels),
                "properties": dict(node),
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get node failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_id}/neighbors")
async def get_neighbors(node_id: str, depth: int = Query(1, ge=1, le=3)):
    """获取邻居节点."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(
                f"MATCH (n)-[*1..{depth}]-(m) WHERE elementId(n) = $id RETURN DISTINCT m",
                {"id": node_id},
            )
            neighbors = []
            for r in result:
                node = r["m"]
                neighbors.append({
                    "id": node.element_id,
                    "labels": list(node.labels),
                    "properties": dict(node),
                })
        return {"center_id": node_id, "depth": depth, "neighbors": neighbors}
    except Exception as e:
        logger.error("Get neighbors failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes")
async def create_node(req: NodeCreate):
    """创建节点."""
    try:
        driver = get_driver()
        labels_str = ":".join(req.labels)
        with driver.session() as session:
            result = session.run(
                f"CREATE (n:{labels_str} $props) RETURN elementId(n) as id",
                {"props": req.properties},
            )
            record = result.single()
        return {"id": record["id"], "success": True}
    except Exception as e:
        logger.error("Create node failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/relations")
async def create_relation(req: RelationCreate):
    """创建关系."""
    try:
        driver = get_driver()
        with driver.session() as session:
            session.run(
                f"MATCH (a), (b) WHERE elementId(a) = $src AND elementId(b) = $tgt CREATE (a)-[r:{req.relation_type} $props]->(b)",
                {"src": req.source_id, "tgt": req.target_id, "props": req.properties or {}},
            )
        return {"success": True}
    except Exception as e:
        logger.error("Create relation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def graph_stats():
    """图统计信息."""
    try:
        driver = get_driver()
        with driver.session() as session:
            node_count = session.run("MATCH (n) RETURN count(n) as cnt").single()["cnt"]
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) as cnt").single()["cnt"]
            labels = session.run("CALL db.labels()").data()
            rel_types = session.run("CALL db.relationshipTypes()").data()
        return {
            "node_count": node_count,
            "relationship_count": rel_count,
            "labels": [l["label"] for l in labels],
            "relationship_types": [r["relationshipType"] for r in rel_types],
        }
    except Exception as e:
        logger.error("Graph stats failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/shortest-path")
async def shortest_path(source_id: str, target_id: str, max_depth: int = Query(10)):
    """最短路径."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(
                f"MATCH path = shortestPath((a)-[*..{max_depth}]-(b)) WHERE elementId(a) = $src AND elementId(b) = $tgt RETURN path",
                {"src": source_id, "tgt": target_id},
            )
            record = result.single()
            if not record:
                return {"path": None, "message": "No path found"}
            path = record["path"]
            nodes = [{"id": n.element_id, "labels": list(n.labels), "properties": dict(n)} for n in path.nodes]
            relationships = [{"type": r.type, "properties": dict(r)} for r in path.relationships]
            return {"path": {"nodes": nodes, "relationships": relationships}}
    except Exception as e:
        logger.error("Shortest path failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
