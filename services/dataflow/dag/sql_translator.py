"""SQL⇄DAG 双向转换 — SQL 脚本拆解为 DAG，DAG 导出为顺序 SQL。

SQL→DAG（sql_to_dag）：
- 支持多语句脚本；语句形态：
  a) `INSERT INTO [ds.db.]t <query>` / `CREATE TABLE [ds.db.]t AS <query>`
     → sql_task 节点 {sql: <query>, target: {datasource, table, write_mode}};
  b) 纯 `<query>`（SELECT/WITH）→ sql_task 节点 {sql}（无 target，可后补）;
- 语句级拆解（CTE 保留在节点内，不逐 CTE 拆）；
- 依赖构建：A 产出表 t、B 消费 t ⇒ 边 A→B；同一表被多个语句产出 =
  歧义，显式报错（不猜）。

DAG→SQL（dag_to_sql）：
- 拓扑序导出顺序 SQL 脚本，每节点前注释（节点名/依赖/目标表）；
- 导出物仅供人审阅/外带，canonical 事实源仍是 graph_json。

转换不改变治理口径：拆出的每个节点独立过 validate_sql + 治理执行。
"""

import logging
import re

import sqlglot
from sqlglot import exp

from services.dataflow.dag.dag_validator import topological_order, DagValidationError

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$")


def _table_ref_parts(table: exp.Table) -> tuple:
    """表引用 → (datasource_name or '', db or '', bare_name)。"""
    parts = [p.name for p in table.parts if p.name]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return "", parts[0], parts[1]
    return "", "", parts[-1] if parts else ""


def _consumed_tables(tree: exp.Expression) -> list:
    """语句消费的物理表（排除 CTE/派生表），返回 [(ds, db, bare)]。"""
    from services.shared.semantics.sql_guard import physical_references
    refs = []
    for _scope, table in physical_references(tree):
        refs.append(_table_ref_parts(table))
    return refs


def sql_to_dag(sql_script: str, default_datasource: str = "",
               target_datasource: str = "") -> dict:
    """把 SQL 脚本拆解为 DAG 图 {nodes, edges}。

    default_datasource：未带限定名的表所属主数据源（节点 source_datasource）；
    target_datasource：INSERT 目标未带限定名时的目标数据源。
    """
    try:
        statements = [s for s in sqlglot.parse(sql_script, read="mysql") if s is not None]
    except Exception as exc:
        raise DagValidationError(f"SQL 脚本解析失败: {exc}") from exc
    if not statements:
        raise DagValidationError("SQL 脚本为空")

    nodes, edges = [], []
    producers: dict = {}   # 产出表 key -> node_key（歧义检测）
    consumers: list = []   # [(node_key, 消费表 key 列表)]

    for index, statement in enumerate(statements, start=1):
        node_key = f"step_{index}"
        produced_key = None
        target_config = None
        query = statement

        if isinstance(statement, exp.Insert):
            insert_table = statement.this
            if not isinstance(insert_table, exp.Table):
                raise DagValidationError(f"步骤 {index}: INSERT 目标表不受支持", node_key)
            ds, db, bare = _table_ref_parts(insert_table)
            produced_key = ".".join(p for p in (ds, db, bare) if p)
            target_config = {
                "datasource": ds or target_datasource or default_datasource,
                "table": bare,
                "write_mode": "overwrite",
            }
            query = statement.expression
            if query is None:
                raise DagValidationError(f"步骤 {index}: INSERT 缺少 SELECT", node_key)
        elif (isinstance(statement, exp.Create)
              and statement.args.get("kind", "").upper() in ("TABLE", "VIEW")):
            create_table = statement.this
            if not isinstance(create_table, exp.Table):
                raise DagValidationError(f"步骤 {index}: CREATE 目标表不受支持", node_key)
            ds, db, bare = _table_ref_parts(create_table)
            produced_key = ".".join(p for p in (ds, db, bare) if p)
            target_config = {
                "datasource": ds or target_datasource or default_datasource,
                "table": bare,
                "write_mode": "overwrite",
            }
            query = statement.expression
            if query is None:
                raise DagValidationError(f"步骤 {index}: CREATE TABLE AS 缺少 SELECT", node_key)
        elif not isinstance(statement, exp.Query):
            raise DagValidationError(
                f"步骤 {index}: 仅支持 SELECT/WITH/INSERT INTO...SELECT/CREATE TABLE AS", node_key)

        if produced_key:
            if produced_key in producers:
                raise DagValidationError(
                    f"表 '{produced_key}' 被多个语句产出（歧义），请拆分为独立工作流", node_key)
            producers[produced_key] = node_key

        consumed = []
        for tds, tdb, tbare in _consumed_tables(query):
            key = ".".join(p for p in (tds, tdb, tbare) if p)
            if key != produced_key:
                consumed.append(key)
        consumers.append((node_key, consumed))

        node = {
            "key": node_key,
            "name": (produced_key or f"查询步骤 {index}"),
            "type": "sql_task",
            "config": {
                "source_datasource": default_datasource,
                "sql": query.sql(dialect="mysql"),
            },
        }
        if target_config and target_config.get("datasource"):
            node["config"]["target"] = target_config
        nodes.append(node)

    # 依赖构建：A 产出 t、B 消费 t ⇒ A→B
    for node_key, consumed in consumers:
        for key in consumed:
            producer = producers.get(key)
            if producer and producer != node_key:
                edges.append({"from": producer, "to": node_key})

    # 去重边
    edges = list({(e["from"], e["to"]): e for e in edges}.values())
    return {"nodes": nodes, "edges": edges}


def dag_to_sql(graph: dict) -> str:
    """DAG 导出为拓扑序 SQL 脚本（人审阅用；非事实源）。"""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    node_map = {str(n.get("key")): n for n in nodes}
    order = topological_order(node_map, edges)
    if len(order) != len(node_map):
        raise DagValidationError("DAG 存在循环依赖，无法导出")

    upstream: dict = {key: [] for key in node_map}
    for edge in edges:
        src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
        if dst in upstream and src in node_map:
            upstream[dst].append(src)

    lines = [
        "-- AI-DataHub DAG 导出脚本（人审阅用，canonical 事实源为工作流 graph_json）",
        "-- 按拓扑序执行；每步骤标注节点名/依赖/目标表。",
        "",
    ]
    for key in order:
        node = node_map[key]
        ntype = node.get("type")
        config = node.get("config") or {}
        deps = upstream.get(key) or []
        lines.append(f"-- ── 节点 [{key}] {node.get('name') or key} ({ntype}) ──")
        if deps:
            lines.append(f"-- 依赖: {', '.join(deps)}")
        if ntype == "sql_task":
            target = config.get("target") or {}
            if target:
                qualifier = f"{target['datasource']}." if target.get("datasource") else ""
                lines.append(f"-- 目标表: {qualifier}{target.get('table')} "
                             f"({target.get('write_mode', 'append')})")
            udf_refs = config.get("udf_refs") or []
            if udf_refs:
                lines.append(f"-- UDF: {', '.join(udf_refs)}")
            sql = (config.get("sql") or "").strip().rstrip(";")
            if target:
                qualifier = f"`{target['datasource']}`." if target.get("datasource") else ""
                lines.append(f"INSERT INTO {qualifier}`{target.get('table')}`")
            lines.append(sql + ";")
        elif ntype == "sync":
            src = f"{config.get('source_datasource', '')}.{config.get('source_table', '')}"
            dst = f"{config.get('target_datasource', '')}.{config.get('target_table', '')}"
            lines.append(f"-- 同步: {src} → {dst} ({config.get('sync_mode', 'full')})")
            where = ""
            if config.get("sync_mode") == "incremental" and config.get("incremental_column"):
                where = f" WHERE {config['incremental_column']} > :watermark"
            lines.append(f"INSERT INTO `{config.get('target_table')}` "
                         f"SELECT * FROM `{config.get('source_table')}`{where};")
        elif ntype == "control":
            lines.append(f"-- 控制节点: {config.get('action', 'pass')}")
        lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 数据流解析（Dataflow）：源表 → 转换 → 目标表
# ══════════════════════════════════════════════════════════════

def _table_ref_id(raw: str, default_ds: str) -> str:
    """表引用 → 数据流节点 ID `table:数据源.表`（与血缘节点同构，同名表不同源不合并）。

    限定名约定（与血缘采集一致）：两段 `数据源名.表`、三段 `数据源名.库.表`
    （取首段+尾段）；裸表名归默认数据源。
    """
    parts = raw.split(".")
    if len(parts) >= 3:
        ds, table = parts[0], parts[-1]
    elif len(parts) == 2:
        ds, table = parts[0], parts[1]
    else:
        ds, table = default_ds, parts[0]
    return f"table:{ds}.{table}" if ds else f"table:{table}"


def _transform_node(node_key: str, config: dict, sql_text: str) -> dict:
    """转换节点：UDF/表达式计算（不含读写表）。"""
    udfs = [str(r).split(":")[0] for r in (config.get("udf_refs") or [])]
    digest = " ".join((sql_text or "").split())[:120]
    return {
        "id": f"transform:{node_key}",
        "kind": "transform",
        "label": "、".join(udfs) if udfs else (digest[:40] or "转换"),
        "udfs": udfs,
        "sql_digest": digest,
        "datasource": config.get("source_datasource") or "",
    }


def extract_dataflow(graph: dict) -> dict:
    """从 DAG 定义解析数据流拓扑（定义期静态解析）：源表 → 转换 → 目标表。

    语义（与血缘图同构，图可视化规范同风格）：
    - 表节点：`table:数据源.表`（同名表不同源是不同实体，不合并）；
    - 转换节点：`transform:节点key`（UDF 清单 + SQL 摘要）；
    - 流（边）：读取（源表→转换）、JOIN（映射表→转换）、写入（转换→目标表）、
      同步（源表→目标表，无转换直搬）；
    - 多节点串联：上游输出表与下游输入表同 ID 时自动合并为同一表节点。

    返回 {"nodes": [...], "flows": [{from, to, label}]}。
    """
    from services.shared.semantics.sql_guard import extract_tables

    nodes: dict = {}
    flows: dict = {}

    def add_flow(src: str, dst: str, label: str) -> None:
        if src and dst and src != dst:
            flows[(src, dst, label)] = {"from": src, "to": dst, "label": label}

    def ensure_table(raw: str, default_ds: str, role: str) -> str:
        tid = _table_ref_id(raw, default_ds)
        entry = nodes.get(tid)
        if entry is None:
            entry = {"id": tid, "kind": "table", "label": tid.split(":", 1)[1],
                     "roles": [role]}
            nodes[tid] = entry
        elif role not in entry["roles"]:
            entry["roles"].append(role)
        return tid

    for node in (graph or {}).get("nodes") or []:
        key = str(node.get("key") or "")
        ntype = node.get("type")
        config = node.get("config") or {}
        if ntype == "control":
            continue  # 控制节点不属于数据流

        source_ds = config.get("source_datasource") or ""
        transform_sql = (config.get("transform_sql") or config.get("sql") or "").strip()
        udf_refs = config.get("udf_refs") or []
        # 转换判定：sync 看 transform_sql/udf_refs；sql_task 的 SQL 本身就是转换
        has_transform = (bool(udf_refs) or bool(config.get("transform_sql"))
                         or (ntype == "sql_task" and bool(config.get("sql"))))

        # 输入表：sync 的 transform_sql / sql_task 的 sql（解析 FROM/JOIN，含映射表）
        input_refs = []
        if transform_sql:
            sql_text = transform_sql.replace("{{source}}", config.get("source_table") or "")
            try:
                input_refs = extract_tables(sql_text)
            except Exception:
                input_refs = []
        if ntype == "sync" and config.get("source_table"):
            src_ref = f"{source_ds}.{config['source_table']}"
            if src_ref not in input_refs and not any(
                    r.split(".")[-1] == config["source_table"] for r in input_refs):
                input_refs.append(src_ref)

        # 目标表
        target_ref = None
        if ntype == "sync" and config.get("target_table"):
            target_ref = f"{config.get('target_datasource') or ''}.{config['target_table']}"
        elif ntype == "sql_task" and config.get("target"):
            tgt = config["target"]
            target_ref = f"{tgt.get('datasource') or ''}.{tgt.get('table') or ''}"

        input_ids = [ensure_table(r, source_ds, "source") for r in input_refs]
        target_id = ensure_table(target_ref, config.get("target_datasource") or "", "target") if target_ref else None

        if has_transform:
            tnode = _transform_node(key, config, transform_sql)
            nodes[tnode["id"]] = tnode
            for i, tid in enumerate(input_ids):
                label = "读取" if i == 0 or len(input_ids) == 1 else "JOIN"
                add_flow(tid, tnode["id"], label)
            if target_id:
                add_flow(tnode["id"], target_id, "写入")
        else:
            for tid in input_ids:
                if target_id:
                    add_flow(tid, target_id, "同步")

    return {
        "nodes": list(nodes.values()),
        "flows": list(flows.values()),
    }


def verify_dataflow_against_lineage(flow: dict, workspace_id: int = 0) -> dict:
    """数据流 vs 血缘对照（预期 vs 实际）。

    把数据流投影到表级流（穿过 transform 节点传递），逐条对比血缘表边：
    命中 → verified=true（实际运行验证过）；未命中 → false（未跑过/未采集到）。
    返回增强后的 flows（每条加 verified）。
    """
    from services.shared.common.db import execute_query
    nodes = {n["id"]: n for n in flow.get("nodes") or []}
    flows = flow.get("flows") or []

    # 表级投影：表→transform、transform→表 传递为 表→表；直接表→表保留
    table_pairs = set()
    for f in flows:
        a, b = nodes.get(f["from"]), nodes.get(f["to"])
        if not a or not b:
            continue
        if a["kind"] == "table" and b["kind"] == "table":
            table_pairs.add((a["label"], b["label"]))
        elif a["kind"] == "table" and b["kind"] == "transform":
            for f2 in flows:
                c = nodes.get(f2["to"])
                if f2["from"] == b["id"] and c and c["kind"] == "table":
                    table_pairs.add((a["label"], c["label"]))

    # 血缘表边（node_id 带源限定，与数据流 label 同构）
    lineage_pairs = set()
    rows = execute_query(
        "SELECT n1.node_id AS s, n2.node_id AS t FROM adh_lineage_edges e "
        "JOIN adh_lineage_nodes n1 ON n1.id=e.source_node_id "
        "JOIN adh_lineage_nodes n2 ON n2.id=e.target_node_id "
        "WHERE e.workspace_id IN (%s, 0) AND n1.node_type='table' AND n2.node_type='table'",
        (workspace_id,)) or []
    for r in rows:
        lineage_pairs.add((r["s"], r["t"]))

    verified_pairs = {(s, t) for (s, t) in table_pairs if (s, t) in lineage_pairs}
    enhanced = []
    for f in flows:
        a, b = nodes.get(f["from"]), nodes.get(f["to"])
        verified = False
        if a and b:
            if a["kind"] == "table" and b["kind"] == "table":
                verified = (a["label"], b["label"]) in verified_pairs
            elif a["kind"] == "table":
                verified = any((a["label"], t) in verified_pairs for (_s, t) in verified_pairs if _s == a["label"])
            elif b["kind"] == "table":
                verified = any((s, b["label"]) in verified_pairs for (s, _t) in verified_pairs if _t == b["label"])
        enhanced.append({**f, "verified": verified})
    return {"nodes": flow.get("nodes") or [], "flows": enhanced}
