"""Knowledge Graph API — Oxigraph SPARQL query interface."""

import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

from services.shared.common.rdf.sparql_client import get_sparql_client
from services.shared.common.rdf.namespaces import ADH_NS, SPARQL_PREFIXES

logger = logging.getLogger(__name__)
router = APIRouter()


class SparqlQuery(BaseModel):
    query: str
    format: Optional[str] = "json"


class NodeCreate(BaseModel):
    node_type: str  # e.g. "Table", "Column", "Term"
    properties: dict
    datasource_id: int = 0


class RelationCreate(BaseModel):
    source_iri: str
    target_iri: str
    relation_type: str  # e.g. "join", "mapsTo", "defines"
    datasource_id: int = 0


@router.post("/query")
async def execute_sparql(req: SparqlQuery):
    """执行 SPARQL 查询."""
    try:
        client = get_sparql_client()
        # Determine query type
        query_stripped = req.query.strip().upper()
        if query_stripped.startswith("SELECT") or query_stripped.startswith("ASK"):
            results = client.query(req.query)
            return {"records": results, "count": len(results)}
        elif query_stripped.startswith("CONSTRUCT") or query_stripped.startswith("DESCRIBE"):
            triples = client.construct(req.query)
            return {"triples": [{"s": str(t[0]), "p": str(t[1]), "o": str(t[2])} for t in triples],
                    "count": len(triples)}
        else:
            # UPDATE query
            client.update(req.query)
            return {"success": True, "message": "Update executed"}
    except Exception as e:
        logger.error("SPARQL query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/query")
async def get_graph_visualization(
    graph_type: str = Query("table-relation", description="table-relation | business-knowledge | data-lineage"),
    datasource_id: int = Query(0, ge=0),
    node_types: Optional[List[str]] = Query(None),
    max_depth: int = Query(2, ge=1, le=5),
    center_node: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=500),
):
    """获取图谱可视化数据（节点 + 边 + 统计），供前端 React Flow 渲染。"""
    try:
        from services.graphservice.graph_service import GraphService
        from services.shared.models.graph import GraphType, NodeType

        try:
            gt = GraphType(graph_type)
        except ValueError:
            gt = GraphType.TABLE_RELATION

        nts: Optional[List[NodeType]] = None
        if node_types:
            parsed = []
            for nt in node_types:
                try:
                    parsed.append(NodeType(nt))
                except ValueError:
                    continue
            nts = parsed or None

        service = GraphService()
        return service.get_graph_data(
            graph_type=gt,
            datasource_id=datasource_id,
            node_types=nts,
            max_depth=max_depth,
            center_node=center_node,
            search=search,
            limit=limit,
        )
    except Exception as e:
        logger.error("Graph visualization query failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_graph_nodes(
    query: str = Query(..., description="搜索关键词"),
    node_types: Optional[List[str]] = Query(None),
    datasource_id: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=500),
):
    """按标签搜索图谱节点，返回 GraphNode 列表。"""
    try:
        from services.graphservice.graph_service import GraphService
        from services.shared.models.graph import NodeType

        nts: Optional[List[NodeType]] = None
        if node_types:
            parsed = []
            for nt in node_types:
                try:
                    parsed.append(NodeType(nt))
                except ValueError:
                    continue
            nts = parsed or None

        service = GraphService()
        return service.search_nodes(
            query=query,
            node_types=nts,
            limit=limit,
            datasource_id=datasource_id,
        )
    except Exception as e:
        logger.error("Graph node search failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes")
async def list_nodes(
    node_type: Optional[str] = None,
    datasource_id: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    """列出节点."""
    try:
        client = get_sparql_client()
        graph = f"{ADH_NS}ds:{datasource_id}"

        if node_type:
            type_iri = f"{ADH_NS}{node_type}"
            sparql = f"""
                {SPARQL_PREFIXES}
                SELECT ?iri ?label ?comment WHERE {{
                    GRAPH <{graph}> {{
                        ?iri a <{type_iri}> ;
                            rdfs:label ?label .
                        OPTIONAL {{ ?iri adh:comment ?comment }}
                    }}
                }}
                LIMIT {limit}
            """
        else:
            sparql = f"""
                {SPARQL_PREFIXES}
                SELECT ?iri ?type ?label ?comment WHERE {{
                    GRAPH <{graph}> {{
                        ?iri a ?type ;
                            rdfs:label ?label .
                        OPTIONAL {{ ?iri adh:comment ?comment }}
                    }}
                }}
                LIMIT {limit}
            """

        results = client.query(sparql)
        nodes = []
        for r in results:
            nodes.append({
                "iri": r.get("iri", ""),
                "type": r.get("type", "").replace(ADH_NS, "") if r.get("type") else (node_type or "Unknown"),
                "label": r.get("label", ""),
                "comment": r.get("comment", "") if r.get("comment") else "",
            })
        return {"nodes": nodes, "count": len(nodes)}
    except Exception as e:
        logger.error("List nodes failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_iri:path}")
async def get_node(node_iri: str):
    """获取节点详情 (IRI as path param)."""
    try:
        client = get_sparql_client()
        sparql = f"""
            {SPARQL_PREFIXES}
            SELECT ?p ?o WHERE {{
                <{node_iri}> ?p ?o .
            }}
            LIMIT 100
        """
        results = client.query(sparql)
        if not results:
            raise HTTPException(status_code=404, detail="Node not found")

        properties = {}
        node_type = "Unknown"
        label = ""
        for r in results:
            pred = r.get("p", "")
            obj = r.get("o", "")
            short_pred = pred.replace(ADH_NS, "adh:")
            if pred == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
                node_type = obj.replace(ADH_NS, "")
            elif pred == "http://www.w3.org/2000/01/rdf-schema#label":
                label = obj
            else:
                properties[short_pred] = obj

        return {
            "iri": node_iri,
            "type": node_type,
            "label": label,
            "properties": properties,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get node failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_iri:path}/neighbors")
async def get_neighbors(node_iri: str, depth: int = Query(1, ge=1, le=3),
                        datasource_id: int = Query(0, ge=0)):
    """获取邻居节点."""
    try:
        client = get_sparql_client()
        graph = f"{ADH_NS}ds:{datasource_id}"

        # Build property path for depth
        path_expr = f"<{ADH_NS}join>"
        if depth > 1:
            path_expr = f"(<{ADH_NS}join>){{1,{depth}}}"

        sparql = f"""
            {SPARQL_PREFIXES}
            SELECT DISTINCT ?iri ?type ?label ?comment WHERE {{
                GRAPH <{graph}> {{
                    <{node_iri}> {path_expr} ?iri .
                    ?iri a ?type ;
                        rdfs:label ?label .
                    OPTIONAL {{ ?iri adh:comment ?comment }}
                }}
            }}
            LIMIT 50
        """
        results = client.query(sparql)
        neighbors = []
        for r in results:
            neighbors.append({
                "iri": r.get("iri", ""),
                "type": r.get("type", "").replace(ADH_NS, ""),
                "label": r.get("label", ""),
                "comment": r.get("comment", "") if r.get("comment") else "",
            })
        return {"center_iri": node_iri, "depth": depth, "neighbors": neighbors}
    except Exception as e:
        logger.error("Get neighbors failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes")
async def create_node(req: NodeCreate):
    """创建节点."""
    try:
        from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore
        store = OxigraphStore()

        name = req.properties.get("name", req.properties.get("label", "unnamed"))
        comment = req.properties.get("comment", "")

        type_lower = req.node_type.lower()
        if type_lower == "table":
            store.create_table_node(name, comment=comment,
                                    business_desc=req.properties.get("business_desc", ""),
                                    datasource_id=req.datasource_id)
        elif type_lower == "column":
            table = req.properties.get("table_name", "")
            store.create_column_node(table, name,
                                     data_type=req.properties.get("data_type", ""),
                                     comment=comment,
                                     datasource_id=req.datasource_id)
        elif type_lower == "term":
            store.create_term_node(name_cn=name,
                                   description=req.properties.get("description", ""),
                                   datasource_id=req.datasource_id)
        elif type_lower == "metric":
            store.create_metric_node(name, description=comment,
                                     datasource_id=req.datasource_id)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported node type: {req.node_type}")

        return {"success": True, "iri": f"{ADH_NS}{type_lower}:{name}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Create node failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/relations")
async def create_relation(req: RelationCreate):
    """创建关系."""
    try:
        from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore
        store = OxigraphStore()

        rel_lower = req.relation_type.lower()
        if rel_lower == "join":
            # Extract table names from IRIs
            src_table = req.source_iri.split("table:")[-1] if "table:" in req.source_iri else req.source_iri
            tgt_table = req.target_iri.split("table:")[-1] if "table:" in req.target_iri else req.target_iri
            store.create_join_relation(src_table, tgt_table, datasource_id=req.datasource_id)
        elif rel_lower == "maps_to":
            term_name = req.source_iri.split("term:")[-1] if "term:" in req.source_iri else req.source_iri
            col_part = req.target_iri.split("col:")[-1] if "col:" in req.target_iri else req.target_iri
            parts = col_part.split(".", 1)
            table = parts[0] if len(parts) > 1 else ""
            column = parts[1] if len(parts) > 1 else col_part
            store.create_term_mapping(term_name, table, column, datasource_id=req.datasource_id)
        else:
            # Generic triple insertion
            client = get_sparql_client()
            graph = f"{ADH_NS}ds:{req.datasource_id}"
            pred_iri = f"{ADH_NS}{req.relation_type}"
            client.update(f"""
                INSERT DATA {{
                    GRAPH <{graph}> {{
                        <{req.source_iri}> <{pred_iri}> <{req.target_iri}> .
                    }}
                }}
            """)

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Create relation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def graph_stats(datasource_id: int = Query(0, ge=0)):
    """图统计信息."""
    try:
        client = get_sparql_client()
        graph = f"{ADH_NS}ds:{datasource_id}"

        triple_count = client.count_triples(graph)

        # Count by type
        sparql = f"""
            {SPARQL_PREFIXES}
            SELECT ?type (COUNT(?s) as ?cnt) WHERE {{
                GRAPH <{graph}> {{
                    ?s a ?type .
                }}
            }}
            GROUP BY ?type
        """
        type_counts = client.query(sparql)
        labels = []
        for r in type_counts:
            type_val = r.get("type", "").replace(ADH_NS, "")
            labels.append(type_val)

        return {
            "triple_count": triple_count,
            "node_types": labels,
            "connected": client.health(),
        }
    except Exception as e:
        logger.error("Graph stats failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/shortest-path")
async def shortest_path(source_iri: str, target_iri: str,
                        max_depth: int = Query(10),
                        datasource_id: int = Query(0, ge=0)):
    """最短路径 (SPARQL property path)."""
    try:
        client = get_sparql_client()
        graph = f"{ADH_NS}ds:{datasource_id}"

        sparql = f"""
            {SPARQL_PREFIXES}
            SELECT ?iri ?type ?label WHERE {{
                GRAPH <{graph}> {{
                    <{source_iri}> (<{ADH_NS}join>)/(<{ADH_NS}join>)* ?mid .
                    ?mid (<{ADH_NS}join>)/(<{ADH_NS}join>)* ?iri .
                    ?iri a ?type ;
                        rdfs:label ?label .
                }}
            }}
            LIMIT 50
        """
        results = client.query(sparql)
        if not results:
            return {"path": None, "message": "No path found"}

        nodes = []
        seen = set()
        for r in results:
            iri = r.get("iri", "")
            if iri not in seen:
                seen.add(iri)
                nodes.append({
                    "iri": iri,
                    "type": r.get("type", "").replace(ADH_NS, ""),
                    "label": r.get("label", ""),
                })

        return {"path": {"nodes": nodes, "count": len(nodes)}}
    except Exception as e:
        logger.error("Shortest path failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync")
async def sync_graph(datasource_id: int = Query(0, ge=0)):
    """从元数据重建知识图谱（clear + rebuild）。

    一次性把表/字段/业务术语/指标/JOIN 关系/SQL 模板 materialize 到 Oxigraph，
    供 GraphRAG 检索路线通过 SPARQL 接地。返回构建统计。
    """
    try:
        from services.graphservice.graph_service import GraphService
        service = GraphService()
        return service.sync_from_metadata(datasource_id)
    except Exception as e:
        logger.error("Graph sync failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Metrics & Dimensions CRUD (MySQL: adh_metrics / adh_dimensions) ───
# 支撑“指标管理 / 维度管理” tab 与 GraphEntities 页的 CRUD。
# 表列名已核对 docker/mysql/graph_entities_migration.sql，与前端 form 字段一致。

_METRICS_EDITABLE = [
    "name", "name_en", "formula", "unit", "agg_type",
    "target_table", "target_column", "description", "category", "datasource_id",
]
_DIMENSIONS_EDITABLE = [
    "name", "name_en", "hierarchy", "level",
    "target_table", "target_column", "description", "category", "datasource_id",
]

_METRICS_SELECT = (
    "id, COALESCE(name, '') AS name, COALESCE(name_en, '') AS name_en, "
    "COALESCE(formula, '') AS formula, COALESCE(unit, '') AS unit, "
    "COALESCE(agg_type, '') AS agg_type, COALESCE(target_table, '') AS target_table, "
    "COALESCE(target_column, '') AS target_column, COALESCE(description, '') AS description, "
    "COALESCE(category, '') AS category, COALESCE(datasource_id, 0) AS datasource_id, "
    "created_at, updated_at"
)
_DIMENSIONS_SELECT = (
    "id, COALESCE(name, '') AS name, COALESCE(name_en, '') AS name_en, "
    "COALESCE(hierarchy, '') AS hierarchy, COALESCE(level, 0) AS level, "
    "COALESCE(target_table, '') AS target_table, COALESCE(target_column, '') AS target_column, "
    "COALESCE(description, '') AS description, COALESCE(category, '') AS category, "
    "COALESCE(datasource_id, 0) AS datasource_id, created_at, updated_at"
)


def _normalize_numeric(payload: dict, numeric_cols: set) -> dict:
    """把数字列的空字符串/None 归一为 0，避免写入 INT 列时报错。"""
    p = dict(payload)
    for c in numeric_cols:
        if c in p and (p[c] == "" or p[c] is None):
            p[c] = 0
    return p


def _list_entities(table: str, select_sql: str) -> list:
    from services.shared.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {select_sql} FROM {table} WHERE is_active = 1 ORDER BY id")
            return cur.fetchall()
    finally:
        conn.close()


def _create_entity(table: str, editable: list, payload: dict) -> int:
    from services.shared.common.db.metadata_db import get_metadata_conn
    cols = [c for c in editable if c in payload]
    if not cols:
        raise HTTPException(status_code=400, detail="没有可保存的字段")
    placeholders = ", ".join(["%s"] * len(cols))
    values = [payload[c] for c in cols]
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            new_id = cur.lastrowid
        conn.commit()
        return new_id
    finally:
        conn.close()


def _update_entity(table: str, editable: list, entity_id: int, payload: dict) -> int:
    from services.shared.common.db.metadata_db import get_metadata_conn
    cols = [c for c in editable if c in payload]
    if not cols:
        return 0
    set_sql = ", ".join(f"{c} = %s" for c in cols)
    values = [payload[c] for c in cols] + [entity_id]
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            affected = cur.execute(f"UPDATE {table} SET {set_sql} WHERE id = %s", values)
        conn.commit()
        return affected
    finally:
        conn.close()


def _delete_entity(table: str, entity_id: int) -> int:
    from services.shared.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            affected = cur.execute(f"DELETE FROM {table} WHERE id = %s", (entity_id,))
        conn.commit()
        return affected
    finally:
        conn.close()


@router.get("/metrics")
async def list_metrics():
    """指标列表（仅启用）。"""
    try:
        return _list_entities("adh_metrics", _METRICS_SELECT)
    except Exception as e:
        logger.error("List metrics failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/metrics")
async def create_metric(payload: dict):
    """新建指标。"""
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="指标名称不能为空")
    try:
        data = _normalize_numeric({**payload, "name": name}, {"datasource_id"})
        new_id = _create_entity("adh_metrics", _METRICS_EDITABLE, data)
        return {"success": True, "id": new_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Create metric failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/metrics/{metric_id}")
async def update_metric(metric_id: int, payload: dict):
    """更新指标。"""
    try:
        data = _normalize_numeric(payload, {"datasource_id"})
        affected = _update_entity("adh_metrics", _METRICS_EDITABLE, metric_id, data)
        return {"success": True, "affected": affected}
    except Exception as e:
        logger.error("Update metric failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/metrics/{metric_id}")
async def delete_metric(metric_id: int):
    """删除指标。"""
    try:
        affected = _delete_entity("adh_metrics", metric_id)
        return {"success": True, "affected": affected}
    except Exception as e:
        logger.error("Delete metric failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dimensions")
async def list_dimensions():
    """维度列表（仅启用）。"""
    try:
        return _list_entities("adh_dimensions", _DIMENSIONS_SELECT)
    except Exception as e:
        logger.error("List dimensions failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dimensions")
async def create_dimension(payload: dict):
    """新建维度。"""
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="维度名称不能为空")
    try:
        data = _normalize_numeric({**payload, "name": name}, {"level", "datasource_id"})
        new_id = _create_entity("adh_dimensions", _DIMENSIONS_EDITABLE, data)
        return {"success": True, "id": new_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Create dimension failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/dimensions/{dimension_id}")
async def update_dimension(dimension_id: int, payload: dict):
    """更新维度。"""
    try:
        data = _normalize_numeric(payload, {"level", "datasource_id"})
        affected = _update_entity("adh_dimensions", _DIMENSIONS_EDITABLE, dimension_id, data)
        return {"success": True, "affected": affected}
    except Exception as e:
        logger.error("Update dimension failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/dimensions/{dimension_id}")
async def delete_dimension(dimension_id: int):
    """删除维度。"""
    try:
        affected = _delete_entity("adh_dimensions", dimension_id)
        return {"success": True, "affected": affected}
    except Exception as e:
        logger.error("Delete dimension failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
