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


# ═══════════════════════════════════════════════════════════════════
# 资产血缘贯通（P5）：物理表→数据产品→本体对象→口径→数据集→图表
# ═══════════════════════════════════════════════════════════════════
# 与 SQL 血缘（persist_sql_lineage 从 SQL 文本解析表依赖）互补：本节从**元数据关系**
# 生成语义层资产链，回答「一个指标的口径追溯到哪张表、被哪些看板消费」。
#
# 纪律：
# - 幂等（distributed-first）：同节点/同边不重复创建，重复跑不产生重复副作用；
# - 生成即显式：某段链断裂（如数据集没绑对象）不静默跳过，记入 gaps 由调用方可见；
# - 只读元数据生成边，不改任何资产本身（血缘是派生视图，不是事实源）。

ASSET_NODE_TYPES = ("table", "product", "ontology_object", "metric", "dimension", "dataset", "chart")


def _ensure_asset_node(workspace_id: int, node_type: str, node_id: str,
                       node_name: str, datasource_id: int = 0, metadata: dict = None) -> int:
    """查找或创建血缘节点（幂等，按 node_id + node_type + workspace_id）。"""
    row = execute_query(
        "SELECT id FROM adh_lineage_nodes WHERE node_id=%s AND node_type=%s AND workspace_id=%s LIMIT 1",
        (node_id, node_type, workspace_id), fetchone=True)
    if row:
        return int(row["id"])
    return int(execute_insert(
        """INSERT INTO adh_lineage_nodes
           (workspace_id, node_type, node_id, node_name, datasource_id, metadata)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (workspace_id, node_type, node_id, node_name or node_id, datasource_id,
         json.dumps(metadata, ensure_ascii=False) if metadata else None)))


def _ensure_asset_edge(workspace_id: int, source_id: int, target_id: int, edge_type: str) -> bool:
    """建边（幂等）。返回是否新建。"""
    exists = execute_query(
        "SELECT id FROM adh_lineage_edges WHERE source_node_id=%s AND target_node_id=%s "
        "AND edge_type=%s AND workspace_id=%s LIMIT 1",
        (source_id, target_id, edge_type, workspace_id), fetchone=True)
    if exists:
        return False
    execute_insert(
        """INSERT INTO adh_lineage_edges
           (workspace_id, source_node_id, target_node_id, edge_type, confidence)
           VALUES (%s,%s,%s,%s,1.0)""",
        (workspace_id, source_id, target_id, edge_type))
    return True


def build_asset_lineage(workspace_id: int = 0) -> dict:
    """从元数据关系生成资产血缘链（幂等）。

    链路（左→右）：
      table → product → ontology_object → metric/dimension
                                     ↘ dataset → chart

    Returns:
        {"nodes_created": n, "edges_created": n, "gaps": [...]}
        gaps = 断裂的链（如数据集没绑对象、图表没绑数据集），显式带回不静默。
    """
    nodes_created = edges_created = 0
    gaps: list[str] = []

    def node(node_type, node_id, node_name, datasource_id=0, metadata=None):
        nonlocal nodes_created
        existed = execute_query(
            "SELECT id FROM adh_lineage_nodes WHERE node_id=%s AND node_type=%s AND workspace_id=%s LIMIT 1",
            (node_id, node_type, workspace_id), fetchone=True)
        nid = _ensure_asset_node(workspace_id, node_type, node_id, node_name, datasource_id, metadata)
        if not existed:
            nodes_created += 1
        return nid

    def edge(src, dst, edge_type):
        nonlocal edges_created
        if _ensure_asset_edge(workspace_id, src, dst, edge_type):
            edges_created += 1

    # ① 物理表 → 数据产品（product 是表的治理身份）
    for p in execute_query(
            "SELECT product_name, product_class, site, datasource_name, physical_table, status "
            "FROM adh_data_products WHERE status <> 'retired'") or []:
        tbl = str(p.get("physical_table") or "").strip()
        pname = str(p.get("product_name") or "").strip()
        if not tbl or not pname:
            continue
        t_node = node("table", tbl, tbl)
        p_node = node("product", pname, pname, metadata={
            "product_class": p.get("product_class") or "", "site": p.get("site") or "",
            "datasource_name": p.get("datasource_name") or "", "status": p.get("status") or ""})
        edge(t_node, p_node, "produces")

    # ② 数据产品 → 本体对象（绑定：对象绑产品）
    for b in execute_query(
            "SELECT object_key, product_ref, model_id FROM adh_ontology_bindings "
            "WHERE status='active' AND COALESCE(product_ref,'')<>''") or []:
        okey = str(b.get("object_key") or "").strip()
        pref = str(b.get("product_ref") or "").strip()
        if not okey or not pref:
            continue
        p_node = node("product", pref, pref)
        o_node = node("ontology_object", okey, okey, metadata={"model_id": b.get("model_id")})
        edge(p_node, o_node, "binds_to")

    # ③ 本体对象 → 口径（字典 bound_object_key 指向对象）
    for tbl, ntype, name_col in (("adh_metrics", "metric", "name"), ("adh_dimensions", "dimension", "name")):
        for r in execute_query(
                f"SELECT {name_col} AS nm, bound_object_key FROM {tbl} "
                f"WHERE is_active=1 AND COALESCE(bound_object_key,'')<>''") or []:
            okey = str(r.get("bound_object_key") or "").strip()
            nm = str(r.get("nm") or "").strip()
            if not okey or not nm:
                continue
            o_node = node("ontology_object", okey, okey)
            c_node = node(ntype, nm, nm)
            edge(o_node, c_node, "defines")

    # ④ 本体对象 → 数据集（BI 数据集消费对象）
    for d in execute_query(
            "SELECT name, object_key FROM adh_datasets "
            "WHERE status='active' AND COALESCE(object_key,'')<>''") or []:
        okey = str(d.get("object_key") or "").strip()
        dname = str(d.get("name") or "").strip()
        if not okey or not dname:
            continue
        o_node = node("ontology_object", okey, okey)
        d_node = node("dataset", dname, dname)
        edge(o_node, d_node, "consumed_by")

    # ⑤ 数据集 → 图表（看板可视化）。图表经 source_id/source_type 关联资产（无 dataset_id 列）。
    # 现实：多数图表以 query/snapshot 直连 SQL 未经数据集，这些记为「数据集层断裂」显式暴露。
    for c in execute_query("SELECT name, source_type, source_id FROM adh_charts") or []:
        cname = str(c.get("name") or "").strip()
        if str(c.get("source_type") or "") == "dataset" and c.get("source_id"):
            drow = execute_query("SELECT name FROM adh_datasets WHERE id=%s", (c.get("source_id"),), fetchone=True)
            if not drow:
                gaps.append(f"图表「{cname}」引用的数据集已不存在（source_id={c.get('source_id')}）")
                continue
            d_node = node("dataset", str(drow["name"]), str(drow["name"]))
            c_node = node("chart", cname, cname)
            edge(d_node, c_node, "visualizes")
        else:
            # 不静默：绕过数据集的图表血缘断在数据集层，需人决定是否改接语义层
            gaps.append(
                f"图表「{cname}」以 {c.get('source_type') or '直连 SQL'} 取数未经数据集"
                f"（血缘在数据集层断裂）")

    # ⑥ 断裂检测：没有 product_ref 的 active 绑定（对象无治理身份）
    for b in execute_query(
            "SELECT object_key FROM adh_ontology_bindings WHERE status='active' AND COALESCE(product_ref,'')=''") or []:
        gaps.append(f"本体对象「{b.get('object_key')}」未绑定数据产品（血缘在产品层断裂）")

    return {"nodes_created": nodes_created, "edges_created": edges_created, "gaps": gaps}
