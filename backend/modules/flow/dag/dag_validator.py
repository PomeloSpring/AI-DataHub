"""DAG 校验 — 保存/发布/运行前的结构与配置校验。

校验项（任一不过即显式报错并定位到节点，fail-loud）：
- 结构：节点 key 唯一、边引用存在、无环、无悬空边；
- sync 节点：源/目标数据源 name 可解析、表名非空、增量模式必带增量列；
- sql_task 节点：SQL 只读单条（sql_guard.parse_query）、UDF 引用已注册且启用、
  目标写入配置完整；
- control 节点：动作合法。

数据源一律按 name 解析（waker-datasource-domain §2），解析失败给出可操作错误。
"""

import logging
import re

logger = logging.getLogger(__name__)

NODE_TYPES = ("sync", "sql_task", "control")
WRITE_MODES = ("append", "overwrite")
SYNC_MODES = ("full", "incremental")
CONTROL_ACTIONS = ("pass", "fail")

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$")


class DagValidationError(ValueError):
    """DAG 不合法；message 面向用户可展示（不含凭据/内部 id）。"""

    def __init__(self, message: str, node_key: str = ""):
        self.node_key = node_key
        super().__init__(f"[{node_key}] {message}" if node_key else message)


def _resolve_datasource(name: str) -> dict:
    from backend.common.db import get_datasource_by_name
    source = get_datasource_by_name(name or "")
    if not source:
        raise DagValidationError(f"数据源 '{name}' 不存在，请从数据源清单中选择")
    return source


def _validate_table_name(name: str, field: str, node_key: str) -> None:
    if not name or not IDENT_RE.match(name):
        raise DagValidationError(f"{field} 不合法: {name}", node_key)


def validate_graph(graph: dict, check_datasources: bool = True,
                   check_udfs: bool = True) -> dict:
    """校验 graph_json = {nodes: [...], edges: [{from, to}]}，返回规范化图。"""
    if not isinstance(graph, dict):
        raise DagValidationError("DAG 定义必须是对象 {nodes, edges}")
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        raise DagValidationError("DAG 至少需要一个节点")

    seen_keys = set()
    node_map = {}
    for node in nodes:
        key = str(node.get("key") or "").strip()
        if not key:
            raise DagValidationError("节点缺少 key")
        if key in seen_keys:
            raise DagValidationError("节点 key 重复", key)
        seen_keys.add(key)
        node_map[key] = node

    for edge in edges:
        src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
        if src not in node_map:
            raise DagValidationError(f"边引用了不存在的节点 '{src}'")
        if dst not in node_map:
            raise DagValidationError(f"边引用了不存在的节点 '{dst}'")
        if src == dst:
            raise DagValidationError("节点不能依赖自身", src)

    # 无环校验（Kahn 拓扑）
    order = topological_order(node_map, edges)
    if len(order) != len(node_map):
        cyclic = sorted(set(node_map) - set(order))
        raise DagValidationError(f"存在循环依赖: {', '.join(cyclic)}")

    for key, node in node_map.items():
        ntype = node.get("type")
        if ntype not in NODE_TYPES:
            raise DagValidationError(f"未知节点类型: {ntype}", key)
        config = node.get("config") or {}
        if ntype == "sync":
            _validate_sync_config(config, key, check_datasources, check_udfs)
        elif ntype == "sql_task":
            _validate_sql_config(config, key, check_datasources, check_udfs)
        elif ntype == "control":
            if config.get("action", "pass") not in CONTROL_ACTIONS:
                raise DagValidationError(f"control 动作不合法: {config.get('action')}", key)

    return {"nodes": nodes, "edges": edges}


def _validate_sync_config(config: dict, key: str, check_datasources: bool,
                          check_udfs: bool = True) -> None:
    for field, label in (("source_datasource", "源数据源"), ("target_datasource", "目标数据源")):
        if not config.get(field):
            raise DagValidationError(f"缺少{label}（须从数据源清单选择）", key)
        if check_datasources:
            _resolve_datasource(config[field])
    _validate_table_name(config.get("source_table", ""), "源表名", key)
    _validate_table_name(config.get("target_table", ""), "目标表名", key)
    sync_mode = config.get("sync_mode", "full")
    if sync_mode not in SYNC_MODES:
        raise DagValidationError(f"同步模式不合法: {sync_mode}", key)
    if sync_mode == "incremental" and not config.get("incremental_column"):
        raise DagValidationError("增量同步必须指定增量列", key)
    if config.get("incremental_column"):
        _validate_table_name(config["incremental_column"], "增量列名", key)
    if config.get("write_mode", "append") not in WRITE_MODES:
        raise DagValidationError(f"写入模式不合法: {config.get('write_mode')}", key)

    # 可选转换 SQL（可引用 UDF）：先代入 {{source}} 占位，再 UDF 展开后做只读结构校验
    transform_sql = (config.get("transform_sql") or "").strip()
    if transform_sql:
        from backend.modules.flow.dag.udf_registry import expand_udfs, UdfValidationError
        sql = transform_sql.replace("{{source}}", f"`{config.get('source_table', '')}`")
        if check_udfs:
            try:
                expanded, _used = expand_udfs(sql, config.get("udf_refs") or [])
            except UdfValidationError as exc:
                raise DagValidationError(str(exc), key) from exc
        else:
            expanded = sql
        if config.get("source_table", "").lower() not in expanded.lower():
            raise DagValidationError(
                "转换 SQL 未引用源表（用 {{source}} 占位或 FROM 源表）", key)
        from backend.semantics.sql_guard import parse_query
        try:
            parse_query(expanded)
        except PermissionError as exc:
            raise DagValidationError(f"转换 SQL 不受支持: {exc}", key) from exc


def _validate_sql_config(config: dict, key: str, check_datasources: bool, check_udfs: bool) -> None:
    sql = (config.get("sql") or "").strip()
    if not sql:
        raise DagValidationError("缺少 SQL", key)
    if not config.get("source_datasource"):
        raise DagValidationError("缺少主数据源（须从数据源清单选择）", key)
    if check_datasources:
        _resolve_datasource(config["source_datasource"])

    # UDF 展开后再做只读结构校验（展开后只剩内置函数）
    from backend.modules.flow.dag.udf_registry import expand_udfs, UdfValidationError
    if check_udfs:
        try:
            expanded, _used = expand_udfs(sql, config.get("udf_refs") or [])
        except UdfValidationError as exc:
            raise DagValidationError(str(exc), key) from exc
    else:
        expanded = sql

    from backend.semantics.sql_guard import parse_query, extract_tables
    try:
        parse_query(expanded)
        referenced = extract_tables(expanded)
    except PermissionError as exc:
        raise DagValidationError(f"SQL 不受支持: {exc}", key) from exc

    # 跨源限定名的数据源存在性（三段式首段）；解析失败即拒绝
    if check_datasources:
        for table in referenced:
            parts = table.split(".")
            if len(parts) == 3:
                _resolve_datasource(parts[0])
            elif len(parts) not in (1, 2):
                raise DagValidationError(f"表引用格式不受支持: {table}", key)

    target = config.get("target") or {}
    if target:
        if not target.get("datasource") or not target.get("table"):
            raise DagValidationError("目标写入配置不完整（需目标数据源与目标表）", key)
        if check_datasources:
            _resolve_datasource(target["datasource"])
        _validate_table_name(target["table"], "目标表名", key)
        if target.get("write_mode", "append") not in WRITE_MODES:
            raise DagValidationError(f"写入模式不合法: {target.get('write_mode')}", key)


def topological_order(node_map: dict, edges: list) -> list:
    """Kahn 拓扑排序；有环时返回已排序部分（调用方据长度判环）。"""
    indegree = {key: 0 for key in node_map}
    outgoing = {key: [] for key in node_map}
    for edge in edges:
        src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
        if src in node_map and dst in node_map:
            indegree[dst] += 1
            outgoing[src].append(dst)
    queue = sorted(key for key, deg in indegree.items() if deg == 0)
    order = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for nxt in outgoing[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return order
