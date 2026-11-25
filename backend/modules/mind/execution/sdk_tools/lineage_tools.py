"""lineage 工具组 — 血缘链路查询(get_lineage).

为什么模型需要这个工具，而不是让它在回答里自己画 ```graph:
- 血缘的事实源是「DAG graph_json + 执行期 SQL 采集 + 元数据关系」三处，
  由 `gov.services.lineage_service` 派生落库（`adh_lineage_nodes/edges`）。
  模型凭空产出的图谱只是「看起来像」的示意：节点名、依赖方向、上游站点
  都可能编错，而用户会拿它当下钻依据 —— 错血缘比没有血缘危害大得多。
- 本工具把**平台算好的链路**按模型能用的形状投影出来，回答三类问题：
  ① 「这个数从哪来」= upstream（表 → 数据产品 → 本体对象 → 指标 → 数据集 → 图表）；
  ② 「改这张表会影响谁」= downstream；
  ③ 「这份数据新不新」= freshness（catalog 同步时间 / 最近一次物化完成时间）。

护栏 §7（数据源黑盒）: 只回业务名形态（数据源业务名.表 / 资产名），
**不返回** datasource_id、host/账号、生成 SQL、`transform_expr` 原文、节点内部 id。

权限: 表节点按角色数据源授权过滤（`role_service.get_user_allowed_datasources`，
空授权 fail-closed）——未授权的表不出名、只计数并在 notes 显式说明；
业务资产节点（产品/对象/指标/数据集/图表）是治理资产，不受数据源授权影响。
"""

import asyncio
import json
import logging
from typing import Annotated, Optional

logger = logging.getLogger(__name__)

# 节点类型 → 业务可读名（与前端图例同口径，见 graph-visualization 规范）
_KIND_LABELS = {
    "table": "表",
    "column": "字段",
    "product": "数据产品",
    "ontology_object": "本体对象",
    "metric": "指标",
    "dimension": "维度",
    "dataset": "数据集",
    "chart": "图表",
    "etl_job": "ETL 作业",
    "report": "报表",
}
# 入参 kind → 内部 node_type（对外用 object 比 ontology_object 好说）
_KIND_ALIASES = {
    "object": "ontology_object",
    "ontology_object": "ontology_object",
    "table": "table",
    "product": "product",
    "metric": "metric",
    "dimension": "dimension",
    "dataset": "dataset",
    "chart": "chart",
    "column": "column",
}

_MAX_DEPTH = 3          # 表 → 产品 → 对象 → 指标 至少 3 跳，再多是噪音
_MAX_NODES = 60         # 单次遍历节点上限（防大图拖死会话）
_MAX_CHAINS = 12


def _text(data, is_error: bool = False) -> dict:
    text = data if isinstance(data, str) else json.dumps(
        data, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}],
            **({"isError": True} if is_error else {})}


def _label(node_type: str) -> str:
    return _KIND_LABELS.get(str(node_type or ""), str(node_type or ""))


def _display_name(node: dict) -> str:
    """对外显示名：表用限定名「数据源业务名.表」，其余用资产名。

    为什么必须限定：多站物化下 `t_case_records` 在 cn/en/eu/jp/uk 五个源里同名，
    只用裸表名时 5 个上游在输出里长得完全一样（用户与模型都无法判断「哪几个区域」），
    下钻分析最需要的正是这层区分（graph-visualization §2「标题显示可读限定名」）。
    """
    ntype = str(node.get("node_type") or "")
    if ntype == "table":
        return str(node.get("node_id") or node.get("node_name") or "")
    return str(node.get("node_name") or node.get("node_id") or "")


def _ref(node: dict) -> str:
    """对外句柄（不用内部数字 id）：`类型:名字`，供模型在正文里指代节点。"""
    return f"{_label(node.get('node_type'))}:{_display_name(node)}"


def _resolve_start(asset: str, kind: str) -> tuple[list, list, str]:
    """定位起始节点 → (精确命中, 候选, 错误说明)。

    宁缺勿错（ontology-modeling §7）：模糊命中**只回候选**，不替模型选；
    同名跨类型（如产品名与表限定名同形）也回候选，让调用方用 kind 收敛。
    """
    from backend.common.db import execute_query

    asset = str(asset or "").strip()
    if not asset:
        return [], [], "asset 不能为空"
    sql = ("SELECT id, node_type, node_id, node_name, datasource_id "
           "FROM adh_lineage_nodes WHERE workspace_id = 0 "
           "  AND (node_name = %s OR node_id = %s)")
    params = [asset, asset]
    if kind:
        ntype = _KIND_ALIASES.get(str(kind).lower())
        if not ntype:
            return [], [], f"kind 只能是 {sorted(_KIND_ALIASES)} 之一"
        sql += " AND node_type = %s"
        params.append(ntype)
    rows = execute_query(sql, tuple(params)) or []
    if rows:
        return rows, [], ""
    # 模糊：只回候选（包含式匹配，绝不静默绑定）
    like_sql = ("SELECT id, node_type, node_id, node_name, datasource_id "
                "FROM adh_lineage_nodes WHERE workspace_id = 0 "
                "  AND (node_name LIKE %s OR node_id LIKE %s)")
    like_params = [f"%{asset}%", f"%{asset}%"]
    if kind and _KIND_ALIASES.get(str(kind).lower()):
        like_sql += " AND node_type = %s"
        like_params.append(_KIND_ALIASES[str(kind).lower()])
    candidates = execute_query(like_sql + " LIMIT 10", tuple(like_params)) or []
    if candidates:
        return [], candidates, (f"「{asset}」没有精确命中，以下为候选；"
                                "请用返回的名字之一重新查询（不要猜）")
    return [], [], (f"血缘图里没有「{asset}」这个节点：可能是尚未登记的名称，"
                    "或它在图里的名字与你的写法不同（表请用「数据源名.表名」）")


def _adjacency(node_ids: set, direction: str) -> tuple[dict, dict]:
    """按方向构建邻接表 → (out/in 邻接, 节点信息)。"""
    from backend.common.db import execute_query

    if not node_ids:
        return {"out": {}, "in": {}}, {}
    ids = sorted(node_ids)
    ph = ",".join(["%s"] * len(ids))
    rows = execute_query(
        f"SELECT e.id, e.source_node_id AS s, e.target_node_id AS t, e.edge_type, "
        f"       e.confidence, sn.node_type AS stype, sn.node_name AS sname, "
        f"       sn.node_id AS sid, sn.datasource_id AS sds, "
        f"       tn.node_type AS ttype, tn.node_name AS tname, "
        f"       tn.node_id AS tid, tn.datasource_id AS tds "
        f"FROM adh_lineage_edges e "
        f"JOIN adh_lineage_nodes sn ON sn.id = e.source_node_id "
        f"JOIN adh_lineage_nodes tn ON tn.id = e.target_node_id "
        f"WHERE e.workspace_id = 0 AND (e.source_node_id IN ({ph}) "
        f"  OR e.target_node_id IN ({ph}))", tuple(ids) * 2) or []
    adjacency: dict = {"out": {}, "in": {}}
    nodes: dict = {}
    for r in rows:
        nodes[int(r["s"])] = {"node_type": r["stype"], "node_name": r["sname"],
                              "node_id": r["sid"], "datasource_id": int(r["sds"] or 0)}
        nodes[int(r["t"])] = {"node_type": r["ttype"], "node_name": r["tname"],
                              "node_id": r["tid"], "datasource_id": int(r["tds"] or 0)}
        edge = {"src": int(r["s"]), "dst": int(r["t"]),
                "edge_type": str(r["edge_type"] or ""),
                "confidence": float(r["confidence"] or 0)}
        if direction in ("downstream", "both"):
            adjacency["out"].setdefault(int(r["s"]), []).append((int(r["t"]), edge))
        if direction in ("upstream", "both"):
            adjacency["in"].setdefault(int(r["t"]), []).append((int(r["s"]), edge))
    return adjacency, nodes


def _collect_chains(start_ids: list[int], adjacency: dict, nodes: dict,
                    reverse: bool, depth: int) -> list[str]:
    """按数据流方向拼人话链路（上游在前），最多 _MAX_CHAINS 条。"""
    chains: list[str] = []

    def dfs(nid: int, path: list[int], hops: int):
        if len(chains) >= _MAX_CHAINS or hops > depth:
            return
        nbrs = adjacency.get(nid) or []
        if not nbrs:
            if len(path) > 1:
                seq = list(reversed(path)) if reverse else list(path)
                chains.append(" → ".join(
                    f"{_label(nodes[i].get('node_type'))}「{_display_name(nodes[i])}」"
                    for i in seq if i in nodes))
            return
        for other, _edge in nbrs:
            if other in path:      # 防环：同名资产经多路径成环时不重复展开
                continue
            dfs(other, path + [other], hops + 1)

    for sid in start_ids:
        dfs(sid, [sid], 1)
    # 去重保序（多起点时同一条链可能被走两次）
    seen, out = set(), []
    for c in chains:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:_MAX_CHAINS]


def _freshness(table_rows: list[dict]) -> list[dict]:
    """表节点的物理存在性 + 新鲜度（三个口径分开回答）。

    - `physical_status`：**源上还有没有这张表**。catalog（adh_table_info）是
      事实源，元数据同步在表消失时会删行；`missing` 时链路上的上游一律是历史
      记录，不能当下钻依据（历史缺陷：旧 `dws_*_metric` 删表后仍返回上游链）；
    - `catalog_synced_at`：平台「认识」这张表结构的时间；
    - `last_materialized_at`：数据真正刷新的时间。

    `unregistered` = 该数据源整体没有 catalog 记录（尚未同步过），
    此时无法区分「表已删」与「还没同步」，只能如实说无法判断。
    """
    from backend.common.db import execute_query
    # 延迟 import：工具层依赖服务层的派生口径，不在模块顶部固定（避免循环导入）。
    from backend.modules.gov.services.lineage_service import (
        _load_catalog_coverage, _load_source_index)

    rows = [r for r in table_rows if r.get("node_type") == "table"]
    if not rows:
        return []
    index = _load_source_index()
    by_id = index.get("by_id") or {}
    coverage = _load_catalog_coverage(index)

    # 按（数据源业务名, 表名）取结构同步时间：跨源同名表必须分开，
    # 不能按裸表名取 MAX（会把别的源的同步时间当成自己的）。
    sync_rows = execute_query(
        "SELECT datasource_id, table_name, MAX(sync_time) AS sync_time "
        "FROM adh_table_info GROUP BY datasource_id, table_name") or []
    synced = {(by_id.get(int(r.get("datasource_id") or 0), ""),
               str(r.get("table_name") or "")): str(r.get("sync_time") or "")
              for r in sync_rows}

    bare_names = sorted({str(r.get("node_name") or "") for r in rows
                         if r.get("node_name")})
    # 物化：编排定义里 type=materialize 且 target_table 命中的节点 → 取最近成功运行
    materialized: dict[str, str] = {}
    try:
        workflows = execute_query(
            "SELECT id, name, graph_json, cron_expression FROM adh_dag_workflows") or []
        wf_hits: dict[str, tuple] = {}
        for wf in workflows:
            graph = wf.get("graph_json")
            if isinstance(graph, str):
                try:
                    graph = json.loads(graph)
                except (json.JSONDecodeError, TypeError):
                    continue
            for n in (graph or {}).get("nodes") or []:
                if str(n.get("type") or "") != "materialize":
                    continue
                cfg = n.get("config") or {}
                tbl = str(cfg.get("target_table") or "").strip()
                if tbl in bare_names:
                    wf_hits[tbl] = (int(wf.get("id") or 0), str(n.get("key") or ""),
                                    str(wf.get("cron_expression") or ""))
        if wf_hits:
            wf_ids = sorted({v[0] for v in wf_hits.values()})
            ph2 = ",".join(["%s"] * len(wf_ids))
            last = execute_query(
                f"SELECT workflow_id, node_key, MAX(finished_at) AS finished_at "
                f"FROM adh_dag_node_runs WHERE workflow_id IN ({ph2}) "
                f"  AND status = 'success' AND node_type = 'materialize' "
                f"GROUP BY workflow_id, node_key", tuple(wf_ids)) or []
            done = {(int(r["workflow_id"]), str(r.get("node_key") or "")):
                    str(r.get("finished_at") or "") for r in last}
            for tbl, (wf_id, node_key, cron) in wf_hits.items():
                hit = done.get((wf_id, node_key))
                if hit:
                    materialized[tbl] = hit
    except Exception as exc:  # noqa: BLE001 — 新鲜度是辅助信息，缺了要说明而不是假装有
        logger.warning("[lineage] 物化时间反查失败: %s", exc)

    out, seen = [], set()
    for r in rows:
        node_id = str(r.get("node_id") or r.get("node_name") or "")
        if "." in node_id:
            ds, bare = node_id.rsplit(".", 1)
        else:
            ds, bare = "", node_id
        if (ds, bare) in seen:
            continue
        seen.add((ds, bare))
        item = {"table": node_id, "catalog_synced_at": synced.get((ds, bare), ""),
                "last_materialized_at": materialized.get(bare, "")}
        cov = coverage.get(ds) or {}
        if item["catalog_synced_at"]:
            item["physical_status"] = "ok"
            if not item["last_materialized_at"]:
                item["note"] = "未找到成功物化记录：可能是源表直查，或加工链尚未跑过"
        elif cov.get("synced"):
            item["physical_status"] = "missing"
            item["note"] = ("该表在当前数据源的元数据里查不到（catalog 无记录即源上已无此表）："
                            "这条链路来自历史执行/编排记录，不能当作当前有效依赖；"
                            "请先确认它是否已改名或下线")
        else:
            item["physical_status"] = "unregistered"
            item["note"] = ("该数据源尚未同步过元数据，无法判断该表是否存在；"
                            "列名与来源请另行确认")
        out.append(item)
    return out


def _allowed_source_ids(user_id: int, workspace_id: int) -> set:
    from backend.core.role_service import role_service
    try:
        return {int(x) for x in (role_service.get_user_allowed_datasources(
            int(user_id or 0), int(workspace_id or 0)) or []) if x}
    except Exception as exc:  # noqa: BLE001 — 裁决失败必须 fail-closed
        logger.error("[lineage] 数据源授权裁决失败，按无授权处理: %s", exc)
        return set()


async def get_lineage(args):
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()

    def run() -> dict:
        asset = str(args.get("asset") or "").strip()
        kind = str(args.get("kind") or "").strip()
        direction = str(args.get("direction") or "both").strip().lower()
        if direction not in ("upstream", "downstream", "both"):
            direction = "both"
        try:
            depth = int(args.get("depth") or 2)
        except (TypeError, ValueError):
            depth = 2
        depth = max(1, min(_MAX_DEPTH, depth))

        matched, candidates, err = _resolve_start(asset, kind)
        if err:
            payload: dict = {"asset": asset, "error": err}
            if candidates:
                payload["candidates"] = [
                    {"name": c.get("node_name") or c.get("node_id"),
                     "kind": _label(c.get("node_type")),
                     "type": str(c.get("node_type") or "")}
                    for c in candidates]
            return payload

        allowed = _allowed_source_ids(ctx.user_id, ctx.workspace_id)
        start_ids = [int(r["id"]) for r in matched]
        seed = {int(r["id"]): {"node_type": r.get("node_type"),
                               "node_name": r.get("node_name"),
                               "node_id": r.get("node_id"),
                               "datasource_id": int(r.get("datasource_id") or 0)}
                for r in matched}

        def _visible(node: dict) -> bool:
            """表节点按数据源授权过滤（fail-closed）：未授权源不出名。

            资产节点（产品/对象/指标/数据集/图表）是治理资产不是数据行，不受源授权影响。
            """
            if node.get("node_type") != "table":
                return True
            ds = int(node.get("datasource_id") or 0)
            return ds == 0 or ds in allowed

        collected: dict[int, dict] = dict(seed)
        hops_by_dir: dict[str, list] = {}
        step_edges: dict[str, dict] = {"upstream": {}, "downstream": {}}
        hidden: set = set()
        # 链路上真正出现的节点（不含「共享上游的兄弟节点」：扩边查询会把它们一起读出来，
        # 但它们并不在本次链路里，混进新鲜度会让人以为目标表也从它们来）
        on_path: set = set(start_ids)

        # 逐层扩边：每层只查当前 frontier 的边（血缘图小，depth<=3，查询数可控）。
        # 不能一次只查起点邻居——那样 depth>1 就形同虚设（表→产品→对象→指标 要 3 跳）。
        for d in ("upstream", "downstream"):
            if direction not in (d, "both"):
                continue
            key = "in" if d == "upstream" else "out"
            visited = set(start_ids)
            frontier = list(start_ids)
            hops: list = []
            for hop in range(1, depth + 1):
                if not frontier or len(visited) >= _MAX_NODES:
                    break
                adj, layer = _adjacency(set(frontier), d)
                collected.update(layer)
                nxt: list[int] = []
                for nid in frontier:
                    for other, edge in adj[key].get(nid, []):
                        node = layer.get(other) or collected.get(other) or {}
                        if not _visible(node):
                            hidden.add(other)
                            continue
                        step_edges[d].setdefault(nid, []).append((other, edge))
                        on_path.add(other)
                        if other in visited:
                            continue
                        visited.add(other)
                        nxt.append(other)
                        item = {"depth": hop,
                                "kind": _label(node.get("node_type")),
                                "name": _display_name(node),
                                "via": edge["edge_type"]}
                        if item in hops:
                            continue
                        hops.append(item)
                frontier = nxt
            hops_by_dir[d] = hops

        notes: list[str] = []
        if hidden:
            notes.append(
                f"有 {len(hidden)} 个表节点属于当前角色未授权的数据源，已从链路中隐去"
                "（需要看请让管理员为该角色开放对应数据源）")
        if not allowed:
            notes.append("当前角色没有任何数据源授权，表级链路为空（这是权限口径，"
                         "不是数据缺失）；业务资产链路（产品/对象/指标/数据集/图表）仍照常返回")

        payload = {
            "asset": asset,
            "matched": [{"ref": _ref(n), "kind": _label(n.get("node_type")),
                         "type": str(n.get("node_type") or "")} for n in matched],
            "depth": depth,
            "direction": direction,
        }
        for d in ("upstream", "downstream"):
            if d not in hops_by_dir:
                continue
            payload[d] = hops_by_dir[d]
            # 人话链路：upstream 按「最上游 → … → 起点」，downstream 按「起点 → … → 下游」
            chains = _collect_chains(start_ids, step_edges[d], collected,
                                     reverse=(d == "upstream"), depth=depth)
            if chains:
                payload["chains"] = payload.get("chains", []) + chains

        touched_tables: dict[str, dict] = {}
        for nid in on_path:
            node = collected.get(nid) or seed.get(nid) or {}
            if node.get("node_type") != "table":
                continue
            if not _visible(node):
                continue
            touched_tables[str(node.get("node_id") or node.get("node_name") or "")] = node
        fresh = _freshness(list(touched_tables.values()))
        if fresh:
            payload["freshness"] = fresh
            # 物理消失的表必须上升到顶层提示：新鲜度只是逐表细节，
            # 模型很容易跳过；而「链路里有不存在的表」直接决定这条链路能不能用。
            missing_tables = [f["table"] for f in fresh
                              if f.get("physical_status") == "missing"]
            if missing_tables:
                notes.append(
                    f"链路包含已不在源上的表（{('、').join(missing_tables)}）："
                    "指向它们的上游/下游来自历史执行记录，不能当作当前有效依赖；"
                    "下钻前先确认这些表是否已改名或下线（目录里查不到即载体已不存在）")
        home_sources = {str(n.get("node_id") or "").split(".", 1)[0]
                        for n in seed.values() if n.get("node_type") == "table"}
        src_names = sorted({str(n.get("node_id") or "").split(".", 1)[0]
                            for n in touched_tables.values()
                            if n.get("node_id")} - home_sources)
        if len(src_names) > 1:
            payload["upstream_sources"] = src_names
            notes.append(f"该链路汇聚了 {len(src_names)} 个数据源（{('、').join(src_names)}），"
                         "跨区域口径下应先确认是否要分站呈现再合计")
        if not payload.get("downstream") and payload.get("direction") in ("downstream", "both"):
            notes.append("下游没有资产引用这张表：可能还没人基于它建数据集/看板，"
                         "也可能下游登记缺失（此时不能推断「没有影响面」）")
        if notes:
            payload["notes"] = notes
        return payload

    try:
        return _text(await asyncio.to_thread(run))
    except Exception as e:  # noqa: BLE001 — 错因可诊断，但不回原始栈给模型
        logger.exception("[lineage] get_lineage 失败 asset=%s", args.get("asset"))
        return _text({"error": f"血缘查询失败：{type(e).__name__}"}, is_error=True)


TOOL_SPECS = [
    {
        "name": "get_lineage",
        "description": (
            "Query the platform's REAL lineage graph for one asset: where its data comes from "
            "(upstream) and what it affects (downstream), plus freshness. "
            "Assets can be a physical table ('datasource.table'), a data product, an ontology "
            "object, a metric, a dimension, a dataset or a chart. "
            "ALWAYS call this before explaining 'where does this number come from', "
            "'what happens if this table changes', or 'is this data up to date' — "
            "NEVER invent a lineage graph yourself. "
            "Returns readable chains plus per-table freshness (catalog sync time and last "
            "materialization). Each table also carries physical_status: 'ok' (exists in the "
            "source), 'missing' (catalog has no such table — the link is historical and MUST "
            "NOT be presented as a current dependency), or 'unregistered' (that datasource "
            "has never been synchronized, so existence is unknown). "
            "Internal ids, hosts and generated SQL are never exposed. "
            "If the name does not match exactly you get candidates back: pick one, do not guess."
        ),
        "schema": {
            "asset": Annotated[str,
                               "Asset name, e.g. 'dws_order_metric_m', 'Doris.t_order_records', "
                               "or a metric/dataset/chart/product name"],
            "kind": Annotated[Optional[str],
                              "Optional disambiguation when the name exists as several types: "
                              "table | product | object | metric | dimension | dataset | chart"],
            "direction": Annotated[Optional[str],
                                   "upstream (data sources) | downstream (impact) | both (default)"],
            "depth": Annotated[Optional[int], "Hops to walk, 1-3 (default 2)"],
        },
        "handler": get_lineage,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_lineage_server(backend: str = "qoder", tool_names=None):
    """构建 lineage 进程内 MCP server(qoder / claude)。

    tool_names 给定时只注册被选中的工具（AS-BOT 逐工具授权）；为空注册本组全部。
    """
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    specs = TOOL_SPECS
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in TOOL_SPECS if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_lineage", tools)