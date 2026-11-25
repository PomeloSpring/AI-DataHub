"""血缘持久化服务 — 解析 SQL 并写入血缘节点/边.

供两处复用：
- datagov API /api/lineage/parse-sql（手动解析）
- dataflow 执行器（定时任务 SQL 执行成功后自动采集）

纯 SELECT 语句无写目标，extract_sql_lineage 返回空 edges，直接跳过不记录。
"""

import json
import logging

from backend.common.db import (
    execute_query, execute_insert, execute_write, get_datasource_by_id, get_metadata_conn,
)
from backend.modules.gov.services.sql_lineage import extract_sql_lineage, resolve_dialect

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 节点标识口径：表 =「数据源业务名.表名」，字段 =「数据源业务名.表名.列名」
# ═══════════════════════════════════════════════════════════════════
# 为什么必须统一（历史缺陷）：同一张物理表曾有三种写法——SQL 解析出的
# `stardb.t_case_records`（stardb 是 **database_name**，不是数据源业务名）、
# 资产血缘写的裸表名 `t_case_records`、物化血缘写的 `prd-cn.t_case_records`。
# 结果是同一张表在图上出现多个节点、边接不上，血缘看起来「不对且很旧」。
# 口径：库名是数据源的配置属性（一个源对应一个库），不是身份维度，
# 因此节点 id 只保留「数据源业务名.表」；跨源同名表自然区分（prd-cn.t / prd-eu.t）。


def _load_source_index() -> dict:
    """数据源索引：id/业务名/库名/站点/类型，用于把任意写法归一到业务名。

    - `by_id`：id → 业务名
    - `by_name`：业务名 → 数据源行
    - `alias`：小写业务名 / 小写 database_name → 业务名（库名只作兜底别名，业务名优先）
    - `ambiguous_db`：属于多个数据源的库名（不得按库名猜源，只能靠 datasource_id）
    """
    rows = execute_query(
        "SELECT id, name, database_name, site_code, db_type FROM adh_datasources") or []
    by_id: dict[int, str] = {}
    by_name: dict[str, dict] = {}
    alias: dict[str, str] = {}
    clean = []
    for r in rows:
        nm = str(r.get("name") or "").strip()
        if not nm:
            continue
        clean.append({"id": int(r["id"]), "name": nm,
                      "db_type": str(r.get("db_type") or ""),
                      "site_code": str(r.get("site_code") or ""),
                      "database_name": str(r.get("database_name") or "")})
        by_id[int(r["id"])] = nm
        by_name[nm] = clean[-1]
        alias[nm.lower()] = nm
    # 第二轮才登记库名别名：避免某源的库名与另一源的业务名重名时覆盖业务名。
    # 同一库名属于多个数据源时**不登记**（如 4 个 MySQL 源都叫 stardb）：
    # 按库名归一只能猜，猜错就是把 prd-eu 的表挂到 prd-cn 名下——错数比无答案危害大。
    owners: dict[str, set] = {}
    for item in clean:
        db = (item["database_name"] or "").lower()
        if db and db not in alias:  # 业务名优先占位的不算库名别名
            owners.setdefault(db, set()).add(item["name"])
    ambiguous_db = {db for db, names in owners.items() if len(names) > 1}
    for db, names in owners.items():
        if len(names) == 1:
            alias.setdefault(db, next(iter(names)))
    return {"by_id": by_id, "by_name": by_name, "alias": alias,
            "ambiguous_db": ambiguous_db, "rows": clean}


def _load_catalog_coverage(index: dict | None = None) -> dict:
    """按数据源汇总 catalog 覆盖面，用于判定「这张表还在不在源上」。

    返回 ``{数据源业务名: {"tables": set(表名), "synced": bool}}``。

    为什么血缘要用它把关：表节点是链路的端点，端点指向一个已经不存在的表，
    整条上游就是假链路（历史缺陷：Doris 旧 `dws_*_metric` 拆表后物理表已删，
    但血缘仍挂着指向它的产品与执行期边，LLM 据此下钻会得到不存在的依赖）。

    判据依据：`metadata_sync` 在源上表消失时会 DELETE `adh_table_info` 行，
    所以「该源有同步记录 + 该表无记录」= 表已不在源上。
    ``synced=False`` 表示该源在 catalog 里一行都没有——此时无法区分
    「表全删了」与「从未同步」，调用方必须跳过校验并显式报缺口。
    """
    index = index or _load_source_index()
    rows = execute_query(
        "SELECT datasource_id, table_name FROM adh_table_info") or []
    by_id = index.get("by_id") or {}
    cov: dict[str, dict] = {}
    for r in rows:
        ds_name = by_id.get(int(r.get("datasource_id") or 0), "")
        if not ds_name:
            continue
        slot = cov.setdefault(ds_name, {"tables": set(), "synced": False})
        tbl = str(r.get("table_name") or "").strip()
        if tbl:
            slot["tables"].add(tbl)
            slot["synced"] = True
    return cov


def canonical_table_node_id(raw: str, datasource_id: int = 0,
                            index: dict | None = None) -> tuple[str, str | None]:
    """把表节点标识归一到「数据源业务名.表名」。

    返回 (canonical_id, unresolved_reason)。可解析输入：`t`、`stardb.t`、`prd-cn.t`。
    解析不到数据源时退回裸表名并带回原因（不静默当已归一）。
    """
    index = index or _load_source_index()
    text = str(raw or "").strip().strip("`")
    if not text:
        return "", "空表名"
    parts = text.split(".")
    table = parts[-1].strip().strip("`")
    prefix = parts[0].strip().strip("`").lower() if len(parts) >= 2 else ""
    ds = index["alias"].get(prefix, "") if prefix else ""
    if not ds:
        ds = index["by_id"].get(int(datasource_id or 0), "")
        if not ds and prefix and prefix in index.get("ambiguous_db", ()):
            return table, (f"表「{raw}」的库名「{parts[0]}」属于多个数据源，"
                           f"仅凭库名无法确定来源（须带 datasource_id）")
    if not ds:
        return table, (f"表「{raw}」无法归一到数据源（节点缺 datasource_id，"
                       f"且前缀不是已知数据源业务名/库名）")
    return f"{ds}.{table}", None


def canonical_column_node_id(raw: str, datasource_id: int = 0,
                             index: dict | None = None) -> tuple[str, str | None]:
    """把字段节点标识归一到「数据源业务名.表名.列名」。"""
    text = str(raw or "").strip().strip("`")
    if "." not in text:
        return text, f"字段「{raw}」缺少表限定，无法归一到数据源"
    table_part, col = text.rsplit(".", 1)
    canon, reason = canonical_table_node_id(table_part, datasource_id, index)
    return (f"{canon}.{col}" if canon else ""), reason


def table_node_id(datasource_name: str, table: str,
                  index: dict | None = None) -> tuple[str, str | None]:
    """按数据源业务名 + 物理表构造表节点标识（剥掉表名自带的库名前缀）。"""
    index = index or _load_source_index()
    ds = str(datasource_name or "").strip()
    tbl = str(table or "").strip().strip("`")
    if not tbl:
        return "", "空表名"
    if "." in tbl:
        tbl = tbl.rsplit(".", 1)[-1].strip().strip("`")
    if ds:
        ds = index["alias"].get(ds.lower(), ds)
        return f"{ds}.{tbl}", None
    return canonical_table_node_id(tbl, 0, index)


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
    index = _load_source_index()

    def normalize(node_type: str, raw_id: str, sink: list) -> str:
        """节点 id 归一到「数据源.表[.列]」；归一不了退回原值并记入 gaps。"""
        if node_type == "table":
            canon, reason = canonical_table_node_id(raw_id, datasource_id, index)
        else:
            canon, reason = canonical_column_node_id(raw_id, datasource_id, index)
        if reason:
            sink.append(reason)
        return canon or raw_id

    gaps: list[str] = []

    def ensure_node(node_type: str, raw_id: str) -> int:
        """查找或创建血缘节点，返回主键 id（node_id 一律按统一口径落库）."""
        node_id = normalize(node_type, raw_id, gaps)
        row = execute_query(
            "SELECT id FROM adh_lineage_nodes WHERE node_id=%s AND node_type=%s AND workspace_id=%s LIMIT 1",
            (node_id, node_type, workspace_id),
            fetchone=True,
        )
        if row:
            return row["id"]
        metadata = None
        if node_type == "table" and raw_id in parsed["table_types"]:
            metadata = {"kind": parsed["table_types"][raw_id]}
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
    # 注：gaps 用于带回「归一失败/解析失败」的显式原因，调用方可见，不静默吞掉

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
        "gaps": gaps,
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


def _as_list(value) -> list:
    """JSON 列（list / JSON 字符串 / NULL）统一成 list。"""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


# gaps 明细上限：全量逐条会把「158 个空图表」这类聚合问题淹成噪音，
# 因此明细截断到 N 条、同类别只汇总计数（不静默，只是不刷屏）。
_GAPS_DETAIL_LIMIT = 60


def _add_gap(bucket: dict, gaps: list, reason: str) -> None:
    """记录缺口：明细限量 + 按类别计数。"""
    key = reason[:80]
    bucket[key] = int(bucket.get(key, 0)) + 1
    if len(gaps) < _GAPS_DETAIL_LIMIT:
        gaps.append(reason)


class _LineageStore:
    """血缘写入会话：一次读入现有节点与边，新增在内存攒够后批量落库。

    为什么（历史缺陷）：远端元数据库单次往返百毫秒级，逐节点/逐边先 SELECT
    「是否已存在」、再逐条 INSERT + commit，会把一次全量重建拖到 10 分钟以上
    （实测 6 分 20 秒 / 3000 行）。这里做两件事：① 命中判定全走内存；
    ② 新增走单条多行 INSERT（executemany），把往返数从「行数」降到「块数」。
    """

    def __init__(self, workspace_id: int = 0):
        self.workspace_id = int(workspace_id or 0)
        self.nodes: dict[tuple[str, str], int] = {}   # 已落库 / 占位（负数）
        self.bare_tables: dict[str, int] = {}
        self.edges: set[tuple[int, int, str]] = set()
        self.transform_nodes: set[int] = set()
        self.nodes_created = 0
        self.edges_created = 0
        self._pending_nodes: list[tuple] = []
        self._pending_edges: list[tuple] = []
        self._tmp_keys: dict[int, tuple[str, str]] = {}
        self.reload()

    def reload(self) -> None:
        """重新加载本工作空间的节点与边（合并/删除后调用）。"""
        self._reload_nodes()
        rows = execute_query(
            "SELECT source_node_id, target_node_id, edge_type FROM adh_lineage_edges "
            "WHERE workspace_id=%s", (self.workspace_id,)) or []
        self.edges = {(int(e["source_node_id"]), int(e["target_node_id"]),
                       str(e.get("edge_type") or "")) for e in rows}
        self.edges.update((s, t, et) for _ws, s, t, et, _expr in self._pending_edges)
        self.transform_nodes = set()
        for src, dst, etype in self.edges:
            if etype == "transform":
                self.transform_nodes.add(src)
                self.transform_nodes.add(dst)

    def _reload_nodes(self) -> None:
        rows = execute_query(
            "SELECT id, node_type, node_id FROM adh_lineage_nodes WHERE workspace_id=%s",
            (self.workspace_id,)) or []
        self.nodes = {}
        self.bare_tables = {}
        for r in rows:
            ntype, nid = str(r.get("node_type")), str(r.get("node_id") or "")
            pk = int(r["id"])
            self.nodes[(ntype, nid)] = pk
            if ntype == "table" and "." not in nid:
                self.bare_tables[nid] = pk

    def node(self, node_type: str, node_id: str, node_name: str,
             datasource_id: int = 0, metadata: dict = None) -> int:
        """查或建节点，返回句柄（未落库的新节点先给负占位，flush 后换真实主键）。"""
        key = (node_type, node_id)
        pk = self.nodes.get(key)
        if pk:
            return pk
        tmp = -(len(self._pending_nodes) + 1)
        self._pending_nodes.append((
            self.workspace_id, node_type, node_id, node_name or node_id, datasource_id,
            json.dumps(metadata, ensure_ascii=False) if metadata else None))
        self.nodes[key] = tmp
        self._tmp_keys[tmp] = key
        if node_type == "table" and "." not in node_id:
            self.bare_tables[node_id] = tmp
        self.nodes_created += 1
        return tmp

    def edge(self, source_id: int, target_id: int, edge_type: str,
             transform_expr: str = None) -> bool:
        """查或建边，返回是否新建。transform_expr 记来源（DAG 节点 / 数据集 名）。"""
        key = (int(source_id), int(target_id), edge_type)
        if key in self.edges:
            return False
        self._pending_edges.append((
            self.workspace_id, int(source_id), int(target_id), edge_type,
            ((transform_expr or "")[:500] or None)))
        self.edges.add(key)
        if edge_type == "transform":
            self.transform_nodes.add(int(source_id))
            self.transform_nodes.add(int(target_id))
        self.edges_created += 1
        return True

    def flush(self) -> None:
        """批量落库：节点 executemany → 回读真实主键 → 边 executemany。"""
        if not self._pending_nodes and not self._pending_edges:
            return
        remap: dict[int, int] = {}
        if self._pending_nodes:
            _bulk_executemany(
                "INSERT INTO adh_lineage_nodes (workspace_id, node_type, node_id, "
                "node_name, datasource_id, metadata) VALUES (%s,%s,%s,%s,%s,%s)",
                self._pending_nodes)
            self._pending_nodes = []
            self._reload_nodes()
            for tmp, key in self._tmp_keys.items():
                real = self.nodes.get(key)
                if real and real > 0:
                    remap[tmp] = real
            self._tmp_keys = {}
            self.bare_tables = {k: remap.get(v, v) for k, v in self.bare_tables.items()}
            if remap:
                self.edges = {(remap.get(s, s), remap.get(t, t), et)
                              for s, t, et in self.edges}
                self.transform_nodes = {remap.get(p, p) for p in self.transform_nodes}
        if self._pending_edges:
            # confidence 必须走占位符：pymysql 只在 VALUES 全为 %s 时合成多行
            # INSERT，夹字面量会退回逐行执行（千行边 = 分钟级往返）。
            rows = [(ws, remap.get(s, s), remap.get(t, t), et, expr, 1.0)
                    for ws, s, t, et, expr in self._pending_edges]
            _bulk_executemany(
                "INSERT INTO adh_lineage_edges (workspace_id, source_node_id, "
                "target_node_id, edge_type, transform_expr, confidence) "
                "VALUES (%s,%s,%s,%s,%s,%s)", rows)
            self._pending_edges = []

    def pk_of(self, node_type: str, node_id: str) -> int | None:
        pk = self.nodes.get((node_type, node_id))
        return int(pk) if pk else None

    def has_transform_edge(self, pk: int) -> bool:
        return int(pk) in self.transform_nodes

    def bare_table(self, name: str) -> int | None:
        return self.bare_tables.get(name)


def _bulk_executemany(sql: str, rows: list[tuple], chunk: int = 500) -> int:
    """批量写入（每块一条多行 INSERT，一次往返）。返回写入行数。"""
    if not rows:
        return 0
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            for i in range(0, len(rows), chunk):
                cur.executemany(sql, rows[i:i + chunk])
    finally:
        conn.close()  # 池化连接：必须归还，否则 max_connections 耗尽
    return len(rows)


def _merge_node_edges(src_id: int, dst_id: int, workspace_id: int) -> None:
    """节点合并时迁移边：指向 src 的边改指 dst，已存在等价边则删重复。"""
    rows = execute_query(
        "SELECT id, source_node_id, target_node_id, edge_type FROM adh_lineage_edges "
        "WHERE source_node_id=%s OR target_node_id=%s", (src_id, src_id)) or []
    for e in rows:
        s = dst_id if int(e["source_node_id"]) == int(src_id) else int(e["source_node_id"])
        t = dst_id if int(e["target_node_id"]) == int(src_id) else int(e["target_node_id"])
        if s == t:  # 合并后成自环（同一张表被合并）：丢弃该边
            execute_write("DELETE FROM adh_lineage_edges WHERE id=%s", (int(e["id"]),))
            continue
        dup = execute_query(
            "SELECT id FROM adh_lineage_edges WHERE source_node_id=%s AND target_node_id=%s "
            "AND edge_type=%s AND workspace_id=%s AND id<>%s LIMIT 1",
            (s, t, e["edge_type"], workspace_id, int(e["id"])), fetchone=True)
        if dup:
            execute_write("DELETE FROM adh_lineage_edges WHERE id=%s", (int(e["id"]),))
        else:
            execute_write(
                "UPDATE adh_lineage_edges SET source_node_id=%s, target_node_id=%s WHERE id=%s",
                (s, t, int(e["id"])))


def reconcile_table_node_ids(workspace_id: int = 0, dry_run: bool = False,
                             index: dict | None = None) -> dict:
    """把历史表/字段节点 id 归一到「数据源业务名.表[.列]」（可重复执行）。

    历史数据有两种偏差：裸表名（无数据源维度）与库名前缀（`stardb.t_x`，
    stardb 是 database_name 而非数据源业务名）。归一后若目标节点已存在则
    **合并节点并迁移边**，避免同一张表在图上留两个节点、边接不上。
    归一不了的（缺 datasource_id 且前缀不认识）原样保留并回带原因，绝不猜源。
    """
    index = index or _load_source_index()
    rows = execute_query(
        "SELECT id, node_id, node_type, datasource_id FROM adh_lineage_nodes "
        "WHERE node_type IN ('table','column') AND workspace_id=%s", (workspace_id,)) or []
    existing: dict[tuple[str, str], int] = {}
    for r in execute_query(
            "SELECT id, node_type, node_id FROM adh_lineage_nodes WHERE workspace_id=%s",
            (workspace_id,)) or []:
        existing[(str(r.get("node_type")), str(r.get("node_id") or ""))] = int(r["id"])
    renamed = merged = unresolved = conflicts = 0
    gaps: list[str] = []
    for r in rows:
        raw = str(r.get("node_id") or "")
        ds_id = int(r.get("datasource_id") or 0)
        ntype = str(r.get("node_type"))
        # 节点名与 datasource_id 指向不同数据源：persist_sql_lineage 把「执行连接」
        # 的 datasource_id 写到了 SQL 里每张表上，跨源引用的表会被打错源。
        # 前缀是解析出的表身份、更可信 → 保留原名并把冲突报出来（不静默改源）。
        prefix = raw.split(".")[0].lower() if "." in raw else ""
        ds_by_id = index["by_id"].get(ds_id, "")
        if prefix and ds_by_id and index["alias"].get(prefix, "") != ds_by_id:
            conflicts += 1
            gaps.append(f"节点「{raw}」的表名前缀与 datasource_id={ds_by_id} 不一致，"
                        f"保留表名前缀待人工确认（不猜源改归属）")
        if ntype == "table":
            canon, reason = canonical_table_node_id(raw, ds_id, index)
        else:
            canon, reason = canonical_column_node_id(raw, ds_id, index)
        if reason:
            unresolved += 1
            gaps.append(reason)
            continue
        if not canon or canon == raw:
            continue
        target_pk = existing.get((ntype, canon))
        if dry_run:
            renamed += 1
            continue
        if target_pk and target_pk != int(r["id"]):
            _merge_node_edges(int(r["id"]), target_pk, workspace_id)
            execute_write("DELETE FROM adh_lineage_nodes WHERE id=%s", (int(r["id"]),))
            existing.pop((ntype, raw), None)
            merged += 1
        else:
            execute_write("UPDATE adh_lineage_nodes SET node_id=%s WHERE id=%s",
                          (canon, int(r["id"])))
            existing.pop((ntype, raw), None)
            existing[(ntype, canon)] = int(r["id"])
            renamed += 1
    return {"renamed": renamed, "merged": merged, "unresolved": unresolved,
            "conflicts": conflicts, "gaps": gaps[:60]}


def _absorb_bare_table_node(store, canonical_id: str, canonical_pk: int,
                            gaps: list, bucket: dict, where: str) -> None:
    """吸收同名的「裸表名」旧节点（同步元数据口径前的资产血缘遗留）。

    旧口径把表节点写成裸 `t_x`（datasource_id=0），新口径是 `prd-cn.t_x`；
    两者指同一张表 → 若不合并，图上就是一个产品两条 produces 入边。
    仅在旧节点**没有 transform 边**时合并（有 SQL 采集边的裸名可能是另一个源
    的同名表，合并会造成错误血缘），否则记缺口留给人工分类。
    """
    if "." not in canonical_id:
        return
    bare = canonical_id.rsplit(".", 1)[-1]
    legacy_pk = store.bare_table(bare)
    if not legacy_pk:
        return
    if int(canonical_pk) < 0:
        # 新节点还在内存里（负占位主键）：先落库拿真实 pk，否则合并会迁错点。
        store.flush()
        canonical_pk = store.pk_of("table", canonical_id)
        if not canonical_pk:
            return
    if int(legacy_pk) == int(canonical_pk):
        return
    if store.has_transform_edge(legacy_pk):
        _add_gap(bucket, gaps,
                 f"{where}：保留裸表名节点「{bare}」（已有 transform 边，可能属另一数据源）")
        return
    _merge_node_edges(int(legacy_pk), int(canonical_pk), store.workspace_id)
    execute_write("DELETE FROM adh_lineage_nodes WHERE id=%s", (int(legacy_pk),))
    store.reload()


def build_asset_lineage(workspace_id: int = 0, index: dict | None = None,
                        store=None) -> dict:
    """从元数据关系生成资产血缘链（幂等）。

    链路（左→右）：
      table → product → ontology_object → metric/dimension
      table → dataset → chart

    Returns:
        {"nodes_created": n, "edges_created": n, "gaps": [...], "gap_summary": {...}}
        gaps/gap_summary = 断裂的链（数据集没绑对象、图表绕过数据集等），显式带回不静默。
    """
    index = index or _load_source_index()
    store = store or _LineageStore(workspace_id)
    # catalog 覆盖面在建节点前算一次：表节点只允许落在「源上真实存在」的表上，
    # 否则会给已删除的表画上游（表都删了，链路的结论必然是假的）。
    coverage = _load_catalog_coverage(index)
    base_nodes, base_edges = store.nodes_created, store.edges_created
    gaps: list[str] = []
    gap_bucket: dict[str, int] = {}

    def node(node_type, node_id, node_name, datasource_id=0, metadata=None):
        return store.node(node_type, node_id, node_name, datasource_id, metadata)

    def edge(src, dst, edge_type, expr=None):
        store.edge(src, dst, edge_type, expr)

    def tbl_node(ds_name: str, physical_table: str, where: str):
        """按口径产出表节点；定不了源则记缺口（不猜源）。

        建节点前还要过一道 catalog 闸：该源已有同步记录、但表不在清单里 →
        源上已无此表，跳过建节点并记缺口（宁缺勿错，不给出不存在的端点）。
        该源整份 catalog 为空时说明还没同步过，不能当「表全删了」，按旧口径建。

        建节点后顺手吸收同名的裸表名旧节点（仅当其无 transform 边），
        消除「同一张表两个节点」的历史遗留。
        """
        node_id, reason = table_node_id(ds_name, physical_table, index)
        if reason or not node_id:
            # 定不了源就不建节点：裸表名节点会与实际表分家（图上出现同名双节点），
            # 且随数据集行序时而新建、时而被合并掉（节点 id 不稳定、重建不幂等）。
            if reason:
                _add_gap(gap_bucket, gaps, f"{where}：{reason}")
            return None
        ds_part, bare = node_id.rsplit(".", 1)
        cov = coverage.get(ds_part) or {}
        if cov.get("synced") and bare not in (cov.get("tables") or set()):
            _add_gap(gap_bucket, gaps,
                     f"{where}：表「{node_id}」在源上已不存在（catalog 无记录），跳过建链")
            return None
        ds_id = int(index["by_name"].get(node_id.split(".")[0], {}).get("id", 0))
        pk = node("table", node_id, node_id.rsplit(".", 1)[-1], ds_id)
        _absorb_bare_table_node(store, node_id, pk, gaps, gap_bucket, where)
        return pk

    # ① 数据产品 → 表（产品是表的治理身份；表节点按数据源业务名限定）
    products = execute_query(
        "SELECT product_name, product_class, site, datasource_name, physical_table, status "
        "FROM adh_data_products") or []
    known_products = {str(p.get("product_name") or "").strip() for p in products}
    known_products.discard("")
    for p in products:
        pname = str(p.get("product_name") or "").strip()
        if not pname or str(p.get("status") or "") == "retired":
            continue
        t_node = tbl_node(str(p.get("datasource_name") or ""),
                          str(p.get("physical_table") or ""), f"数据产品「{pname}」")
        if not t_node:
            # 表已不在源上（或定不了源）：连产品节点也不建。产品是表的治理身份，
            # 表没了就只剩一个无入边的孤立点，图上会误导成「产品还在产出」。
            # 缺口已由 tbl_node 记进 gaps（含具体表名与原因），不静默。
            continue
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
        if pref not in known_products:
            # 绑定指向的产品在 adh_data_products 里不存在（多为旧测试源 test-alb 被删后
            # 遗留的引用）：产品无 produces 入边，产品层已断，必须显式暴露而非静默留孤儿节点。
            _add_gap(gap_bucket, gaps,
                     f"本体对象「{okey}」绑定的数据产品「{pref}」不在数据产品清单"
                     f"（产品层断裂，须重新登记治理身份）")

    # ③ 本体对象 → 口径（字典 bound_object_key 指向对象）
    for tbl, ntype, name_col in (("adh_metrics", "metric", "name"),
                                 ("adh_dimensions", "dimension", "name")):
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

    # ④ 本体对象 → 数据集；⑤ 表 → 数据集（touched_tables 优先，SQL 解析兜底）
    for d in execute_query(
            "SELECT id, name, source_type, datasource_id, object_key, touched_tables, sql_query "
            "FROM adh_datasets WHERE status='active'") or []:
        dname = str(d.get("name") or "").strip()
        if not dname:
            continue
        ds_id = int(d.get("datasource_id") or 0)
        d_node = node("dataset", dname, dname, ds_id,
                      metadata={"source_type": d.get("source_type") or ""})
        okey = str(d.get("object_key") or "").strip()
        if okey:
            edge(node("ontology_object", okey, okey), d_node, "consumed_by")

        src_ds_name = index["by_id"].get(ds_id, "")
        tables = [str(t) for t in _as_list(d.get("touched_tables")) if str(t or "").strip()]
        if not tables and str(d.get("sql_query") or "").strip():
            try:
                dialect = resolve_dialect(
                    index["by_name"].get(src_ds_name, {}).get("db_type", "")) or "mysql"
                tables = [t for t in extract_sql_lineage(
                    str(d.get("sql_query") or ""), dialect=dialect)["tables"] if t]
            except Exception as exc:  # noqa: BLE001 解析失败显式记缺口
                _add_gap(gap_bucket, gaps, f"数据集「{dname}」SQL 解析失败：{exc}")
        if not tables:
            _add_gap(gap_bucket, gaps,
                     f"数据集「{dname}」无 touched_tables 且 SQL 解析不出源表（表层断裂）")
        for t in tables:
            t_node = tbl_node(src_ds_name, t, f"数据集「{dname}」")
            if t_node:
                edge(t_node, d_node, "derive", f"dataset:{dname}")

    # ⑥ 数据集 → 图表（看板可视化）。图表经 source_type/source_id 关联资产。
    # 数据集名先一次性取全：图表 800+ 条逐条查数据集是远程库的主要耗时来源。
    #
    # 图表节点标识用 `chart:<id>`（不是名称）：同一看板群里「月度打开次数」等名称
    # 在 25 个看板上重复，按名称建节点会把不同图表合并成一个（历史缺陷）。
    # 下列三类不以数据集取数，单独计数、不混进 gaps 噪音：
    #   - widget_*：参数控件（日期区间/查询按钮/下拉），本身不取数；
    #   - template：演示看板（config.demoData），数据不是真实取数链；
    #   - empty：尚未取数的空图表。
    dataset_names = {int(r["id"]): str(r.get("name") or "")
                     for r in execute_query("SELECT id, name FROM adh_datasets") or []
                     if r.get("id")}
    empty_charts = widget_charts = template_charts = 0
    for c in execute_query(
            "SELECT id, name, chart_type, dashboard_id, source_type, source_id, sql_query, config "
            "FROM adh_charts") or []:
        cname = str(c.get("name") or "").strip()
        cid = c.get("id")
        if not cname or not cid:
            continue
        ctype = str(c.get("chart_type") or "")
        st = str(c.get("source_type") or "").strip()
        if ctype.startswith("widget_"):
            widget_charts += 1
            continue
        chart_meta = {"chart_type": ctype, "source_type": st,
                      "dashboard_id": c.get("dashboard_id")}
        if st == "dataset" and c.get("source_id"):
            dname = dataset_names.get(int(c.get("source_id")))
            if not dname:
                _add_gap(gap_bucket, gaps,
                         f"图表「{cname}」引用的数据集已不存在（source_id={c.get('source_id')}）")
                continue
            d_node = node("dataset", dname, dname)
            edge(d_node, node("chart", f"chart:{cid}", cname, metadata=chart_meta),
                 "visualizes")
        elif st in ("", "empty"):
            # 空图表还没取数，不构成断裂；计数在 summary 里（不混进 gaps 噪音）
            empty_charts += 1
        elif st == "template":
            template_charts += 1
        else:
            # 不静默：图表自带裸 SQL 的，按 config.datasource 静态解析源表；
            # 定不了源或绕过数据集就直接记缺口，请人决定是否改接语义层。
            sql_text = str(c.get("sql_query") or "").strip()
            cfg = c.get("config")
            if isinstance(cfg, str) and cfg.strip():
                try:
                    cfg = json.loads(cfg)
                except (json.JSONDecodeError, TypeError):
                    cfg = {}
            ds_name = str((cfg or {}).get("datasource") or "") if isinstance(cfg, dict) else ""
            parsed_tables: list[str] = []
            if sql_text and ds_name:
                try:
                    parsed_tables = [t for t in extract_sql_lineage(
                        sql_text,
                        dialect=resolve_dialect(
                            index["by_name"].get(ds_name, {}).get("db_type", "")) or "mysql"
                    )["tables"] if t]
                except Exception as exc:  # noqa: BLE001 解析失败显式记缺口
                    _add_gap(gap_bucket, gaps, f"图表「{cname}」SQL 解析失败：{exc}")
            if parsed_tables:
                c_node = node("chart", f"chart:{cid}", cname, metadata=chart_meta)
                for t in parsed_tables:
                    sid = tbl_node(ds_name, t, f"图表「{cname}」")
                    if sid:
                        edge(sid, c_node, "transform", f"chart:{cname}")
            else:
                _add_gap(gap_bucket, gaps,
                         f"图表「{cname}」以 {st} 取数未经数据集（血缘在数据集层断裂）")
    if empty_charts:
        gap_bucket["图表尚未取数（source_type=empty）"] = empty_charts
    if widget_charts:
        gap_bucket["参数控件（widget_*，不取数）"] = widget_charts
    if template_charts:
        gap_bucket["演示看板图表（template，非真实取数）"] = template_charts

    # ⑦ 断裂检测：没有 product_ref 的 active 绑定（对象无治理身份）
    for b in execute_query(
            "SELECT object_key FROM adh_ontology_bindings "
            "WHERE status='active' AND COALESCE(product_ref,'')=''") or []:
        _add_gap(gap_bucket, gaps,
                 f"本体对象「{b.get('object_key')}」未绑定数据产品（血缘在产品层断裂）")

    store.flush()  # 批量落库（测试与 API 直接调 build 时同样可见）
    return {"nodes_created": store.nodes_created - base_nodes,
            "edges_created": store.edges_created - base_edges,
            "gaps": gaps, "gap_summary": gap_bucket}


# ═══════════════════════════════════════════════════════════════════
# DAG 定义静态推导：不依赖历史 run，从编排定义算出「源表 → 目标表」
# ═══════════════════════════════════════════════════════════════════
# 为什么不能只靠执行时采集：节点只在成功后写血缘，一次没跑成功就没有边；
# 多站物化同一目标表时，执行采集还会把 5 站的边各自重复一遍。静态推导
# 从 graph_json 直接得出定义层的依赖（confidence=1.0，与执行无关）。


def _source_for_site(index: dict, site: str, base_name: str) -> tuple[str, str | None]:
    """按站点解析生效数据源：同站点同类型的源唯一才切换，否则回退并回带原因。"""
    base = index["by_name"].get(base_name)
    if not base or not site or base.get("site_code") == site:
        return base_name, None
    cands = [r for r in index["rows"]
             if r["site_code"] == site and r["db_type"] == base["db_type"]]
    if len(cands) == 1:
        return cands[0]["name"], None
    return base_name, (f"站点「{site}」无唯一的 {base['db_type']} 数据源"
                       f"（候选 {len(cands)} 个），血缘回退到 {base_name}")


def _dataset_source_tables(dataset: dict, site: str, index: dict,
                           gaps: list, bucket: dict, where: str) -> list[str]:
    """数据集 → 生效源 + 触及表清单（touched_tables 优先，SQL 解析兜底）。"""
    ds_id = int(dataset.get("datasource_id") or 0)
    base_name = index["by_id"].get(ds_id, "")
    if not base_name:
        _add_gap(bucket, gaps, f"{where}：数据集「{dataset.get('name')}」未绑定数据源")
        return []
    eff_name, reason = _source_for_site(index, site, base_name)
    if reason:
        _add_gap(bucket, gaps, f"{where}：{reason}")
    tables = [str(t) for t in _as_list(dataset.get("touched_tables")) if str(t or "").strip()]
    if not tables and str(dataset.get("sql_query") or "").strip():
        try:
            dialect = resolve_dialect(
                index["by_name"].get(eff_name, {}).get("db_type", "")) or "mysql"
            tables = [t for t in extract_sql_lineage(
                str(dataset.get("sql_query") or ""), dialect=dialect)["tables"] if t]
        except Exception as exc:  # noqa: BLE001
            _add_gap(bucket, gaps, f"{where}：数据集 SQL 解析失败：{exc}")
    if not tables:
        _add_gap(bucket, gaps, f"{where}：数据集「{dataset.get('name')}」解析不出源表")
    return [f"{eff_name}.{t.rsplit('.', 1)[-1]}" for t in tables if eff_name]


def build_dag_lineage(workspace_id: int = 0, index: dict | None = None,
                      include_inactive: bool = True,
                      store=None) -> dict:
    """从 DAG 编排定义静态推导表级血缘（materialize / sync / sql_task）。

    - materialize：数据集触及表（按节点 site 路由到对应站数据源）→ 目标表；
    - sync：source_datasource.source_table → target_datasource.target_table；
    - sql_task：解析 config.sql 的源表 → target。

    边 confidence=1.0（定义层事实，对错与执行无关），transform_expr 记来源
    `dag:<workflow>/<node_key>` 供详情面板溯源。
    """
    index = index or _load_source_index()
    store = store or _LineageStore(workspace_id)
    base_nodes, base_edges = store.nodes_created, store.edges_created
    gaps: list[str] = []
    gap_bucket: dict[str, int] = {}

    def node(node_type, node_id, node_name, datasource_id=0, metadata=None):
        return store.node(node_type, node_id, node_name, datasource_id, metadata)

    def edge(src, dst, edge_type, expr=None):
        store.edge(src, dst, edge_type, expr)

    def table_node(node_id: str):
        # 裸表名（无「数据源.」前缀）定不了源：不建节点，缺口由调用方记录
        if not node_id or "." not in node_id:
            return None
        ds_name = node_id.split(".")[0]
        ds_id = int(index["by_name"].get(ds_name, {}).get("id", 0))
        return node("table", node_id, node_id.rsplit(".", 1)[-1], ds_id)

    dataset_rows = execute_query(
        "SELECT id, name, source_type, datasource_id, touched_tables, sql_query "
        "FROM adh_datasets WHERE status='active'") or []
    dataset_map = {str(r.get("name")): r for r in dataset_rows if r.get("name")}

    sql = "SELECT id, name, graph_json, is_active FROM adh_dag_workflows"
    if not include_inactive:
        sql += " WHERE is_active=1"
    for wf in execute_query(sql) or []:
        wname = str(wf.get("name") or wf.get("id"))
        graph = wf.get("graph_json")
        if isinstance(graph, str):
            try:
                graph = json.loads(graph)
            except (json.JSONDecodeError, TypeError) as exc:
                _add_gap(gap_bucket, gaps, f"DAG「{wname}」graph_json 解析失败：{exc}")
                continue
        for n in (graph or {}).get("nodes") or []:
            ntype = str(n.get("type") or "")
            cfg = n.get("config") or {}
            where = f"DAG「{wname}」节点「{n.get('key')}」"
            # 指纹用 id（主键稳定且不含 `/`），`|名字` 只为可读：工作流名常带“日/月/年”
            # 这类斜杠，用名字当分隔键会让解析落在名字内部，导致每次重建都删了又建。
            tag = f"dag:{wf.get('id')}:{n.get('key') or ''}|{wname}"
            if ntype == "materialize":
                target_tbl = str(cfg.get("target_table") or "").strip()
                target_id, reason = table_node_id(
                    str(cfg.get("target_datasource") or ""), target_tbl, index)
                if reason:
                    _add_gap(gap_bucket, gaps, f"{where}目标：{reason}")
                if not target_id:
                    continue
                tgt = table_node(target_id)
                ds_name = str(cfg.get("dataset") or "").strip()
                dataset = dataset_map.get(ds_name)
                src_tables: list[str] = []
                if not dataset:
                    _add_gap(gap_bucket, gaps,
                             f"{where}：数据集「{ds_name}」不存在或非 active（表层断裂）")
                else:
                    # 站点口径必须与执行器同源（materialize_runner）：多站合并节点
                    # 实际按 `sites` 逐站取数并写入同一目标表，只连 `site` 单站会把
                    # 目标表的上游少算 4 站（血缘与执行不一致 → 下游影响分析漏链）。
                    # `sites` 空才回退单站 `site`，再空即不路由。
                    sites = [str(x).strip() for x in (cfg.get("sites") or [])
                             if str(x).strip()]
                    if not sites:
                        sites = [str(cfg.get("site") or "")]
                    for site in sites:
                        label = f"{where}（站点 {site}）" if site else where
                        src_tables = _dataset_source_tables(
                            dataset, site, index, gaps, gap_bucket, label)
                        for sid in src_tables:
                            src = table_node(sid)
                            if src and tgt:
                                edge(src, tgt, "transform", tag)
            elif ntype == "sync":
                src_id, r1 = table_node_id(
                    str(cfg.get("source_datasource") or ""),
                    str(cfg.get("source_table") or ""), index)
                tgt_id, r2 = table_node_id(
                    str(cfg.get("target_datasource") or ""),
                    str(cfg.get("target_table") or ""), index)
                for reason in (r1, r2):
                    if reason:
                        _add_gap(gap_bucket, gaps, f"{where}：{reason}")
                if src_id and tgt_id:
                    edge(table_node(src_id), table_node(tgt_id), "transform", tag)
            elif ntype == "sql_task":
                target = cfg.get("target") or {}
                tgt_id, r2 = table_node_id(
                    str(target.get("datasource") or ""), str(target.get("table") or ""), index)
                if r2:
                    _add_gap(gap_bucket, gaps, f"{where}目标：{r2}")
                if not tgt_id:
                    continue
                src_ds = str(cfg.get("source_datasource") or "")
                try:
                    parsed = extract_sql_lineage(str(cfg.get("sql") or ""),
                                                 dialect=resolve_dialect(
                                                     index["by_name"].get(src_ds, {})
                                                     .get("db_type", "")) or "mysql")
                    src_tables = [t for t in parsed["tables"] if t]
                except Exception as exc:  # noqa: BLE001
                    _add_gap(gap_bucket, gaps, f"{where}SQL 解析失败：{exc}")
                    src_tables = []
                if not src_tables:
                    _add_gap(gap_bucket, gaps, f"{where}未解析出源表")
                for t in src_tables:
                    sid, reason = table_node_id(src_ds, t, index)
                    if reason:
                        _add_gap(gap_bucket, gaps, f"{where}：{reason}")
                    src_node = table_node(sid)
                    if src_node:
                        edge(src_node, table_node(tgt_id), "transform", tag)

    store.flush()
    return {"nodes_created": store.nodes_created - base_nodes,
            "edges_created": store.edges_created - base_edges,
            "gaps": gaps, "gap_summary": gap_bucket}


# 定义层血缘的可追溯前缀（transform_expr = `dag:<工作流id>:<节点key>|<工作流名>`）。
# 它是**派生视图的指纹**：编排改名/删节点/删工作流后，靠它精确找回旧段落，
# 不必猜、也不必清空全部 transform 边（执行期采集的 SQL 边没有这个前缀）。
# 为什么用 id 不用名字：名字本身可能含 `/`（典型：「5 站合并 · 日/月/年」），
# 用名字当分隔键会把「物化 · 案例域（5 站合并 · 日」当成另一个编排 → 每次重建都
# 删了又建、永不幂等（已实测）。id 是主键、稳定且不含分隔符；`|名字` 仅为可读。
_DAG_TRACE_PREFIX = "dag:"


def _parse_dag_trace(trace: str) -> tuple[str, str, str]:
    """解析定义链指纹 -> (工作流id 或 "", 节点key, 可读工作流名)。"""
    body = str(trace or "")[len(_DAG_TRACE_PREFIX):]
    head, _, readable = body.partition("|")
    wf_id, sep, node_key = head.partition(":")
    if sep and wf_id.isdigit():
        return wf_id, node_key, readable
    # 旧格式（dag:<工作流名>/<节点key>）：名字含 `/` 时无法可靠解析，
    # 一律视为失效段落重建（它们本来就是派生边，重建即恢复）。
    return "", head, readable


def prune_stale_dag_lineage(workspace_id: int = 0) -> int:
    """清理定义层血缘里已失效的编排段落（DAG 改名 / 删节点 / 删工作流的残留）。

    为什么必须收敛（而非只做增量 build）：`build_dag_lineage` 只看当前 graph_json
    生成边，编排改了它只补新边、不会退旧边。历史后果是同一域出现两条互相矛盾的
    上游链（旧「5 站 × 日/月/年」DAG 已删，但指向 `dws_case_metric` 的边仍在图上，
    与新的 `dws_case_metric_d/_m/_y` 并列）——血缘比实际编排“旧”，用户据此下钻
    会得到已经不存在的依赖。

    判据是**对账**：`dag:<wf>/<node>` 两段都必须在当前编排里真实存在。
    只删 `transform` 且带 `dag:` 前缀的边；执行期 SQL 采集、手工登记的边与
    资产链边一律不动（它们的事实源不在 graph_json）。
    """
    workflows = execute_query(
        "SELECT id, name, graph_json FROM adh_dag_workflows") or []
    live_by_id: dict[str, set] = {}
    for wf in workflows:
        graph = wf.get("graph_json")
        wf_id = str(wf.get("id") or "")
        if isinstance(graph, str):
            try:
                graph = json.loads(graph)
            except (json.JSONDecodeError, TypeError):
                # 编排本身坏了不能当“没有节点”：跳过清理，避免把整段血缘删空
                live_by_id[wf_id] = None
                continue
        live_by_id[wf_id] = {
            str(n.get("key") or "") for n in ((graph or {}).get("nodes") or [])}

    rows = execute_query(
        "SELECT id, source_node_id, target_node_id, transform_expr FROM adh_lineage_edges "
        "WHERE workspace_id=%s AND edge_type='transform' AND transform_expr LIKE %s",
        (int(workspace_id or 0), f"{_DAG_TRACE_PREFIX}%")) or []
    stale_ids = []
    touched_nodes: set[int] = set()
    for row in rows:
        wf_id, node_key, _readable = _parse_dag_trace(row.get("transform_expr"))
        if not wf_id:
            # 旧格式无法可靠对账（工作流名含 `/` 时解析会落在名字内部），
            # 按失效处理：它们本来就是派生边，重建即恢复成新指纹。
            stale_ids.append(int(row["id"]))
            touched_nodes.update((int(row["source_node_id"]), int(row["target_node_id"])))
            continue
        if wf_id not in live_by_id:
            # 编排已被删除：整段归属失效
            stale_ids.append(int(row["id"]))
            touched_nodes.update((int(row["source_node_id"]), int(row["target_node_id"])))
            continue
        nodes = live_by_id[wf_id]
        if nodes is None:
            # 编排行存在但 graph_json 解析失败：保留，不把坏数据当“已删除”
            continue
        if node_key not in nodes:
            stale_ids.append(int(row["id"]))
            touched_nodes.update((int(row["source_node_id"]), int(row["target_node_id"])))
    for edge_id in stale_ids:
        execute_write("DELETE FROM adh_lineage_edges WHERE id=%s", (edge_id,))
    # 表节点没有独立语义：它只作为边的端点存在。删边后若端点已无任何边，
    # 它就是旧编排的幽灵节点（图上一个无连接的孤立点），必须一并收敛，否则
    # “删了链却留着点”会让下钻结果里出现不存在的表。
    if touched_nodes:
        still_used = set()
        for chunk in [list(touched_nodes)[i:i + 200] for i in range(0, len(touched_nodes), 200)]:
            ph = ",".join(["%s"] * len(chunk))
            still_used.update(int(r["node_id"]) for r in (execute_query(
                f"SELECT source_node_id AS node_id FROM adh_lineage_edges "
                f"WHERE source_node_id IN ({ph}) UNION "
                f"SELECT target_node_id AS node_id FROM adh_lineage_edges "
                f"WHERE target_node_id IN ({ph})", tuple(chunk) * 2) or []))
        for node_id in touched_nodes - still_used:
            execute_write("DELETE FROM adh_lineage_nodes WHERE id=%s AND node_type='table'",
                          (node_id,))
    return len(stale_ids)


def prune_missing_table_lineage(workspace_id: int = 0, dry_run: bool = True,
                                index: dict | None = None) -> dict:
    """清理「物理表已不在源上」的血缘残留（表节点与其端点边）。

    与 `prune_stale_dag_lineage` 分工：那边对账的是**编排定义**（graph_json 里
    还有没有这个节点），这边对账的是**物理存在性**（源上还有没有这张表）。
    两者都必须做：编排删节点是「定义失效」，表在源上被 DROP 是「载体失效」——
    后者不会改 graph_json，所以定义层对账永远发现不了（历史缺陷：Doris
    `dws_*_metric` 拆成 `_d/_m/_y` 后旧表已删，血缘仍挂着它的执行期边与产品，
    LLM 据此下钻会得到已经不存在的依赖）。

    判据（宁缺勿错）：
      1) 只处理「数据源业务名.表名」限定名的表节点；裸表名定不了源 → 报告跳过；
      2) 该源在 catalog 里必须有同步记录（`_load_catalog_coverage` 的 synced），
         整份为空时无法区分「表全删了」与「从未同步」，一律跳过并报 gap；
      3) 该表不在该源 catalog 清单里。

    清理范围：table 节点作为端点的 **transform / produces** 边（执行期 SQL 采集
    与产品产出关系）；其余资产层边由 `reset_asset_layer` 重算，不在这里动。
    边删完后无连接的 table 节点一并删除（否则图上留孤立点，下钻仍能点到）。

    默认 dry_run 只报告；apply=True 才写库。
    """
    index = index or _load_source_index()
    coverage = _load_catalog_coverage(index)
    nodes = execute_query(
        "SELECT id, node_id FROM adh_lineage_nodes "
        "WHERE node_type='table' AND workspace_id=%s",
        (int(workspace_id or 0),)) or []
    gaps: list[str] = []
    missing: list[dict] = []
    for n in nodes:
        node_id = str(n.get("node_id") or "").strip()
        if "." not in node_id:
            gaps.append(f"表节点「{node_id}」是裸表名，无法确定数据源，跳过物理存在性对账")
            continue
        ds, bare = node_id.rsplit(".", 1)
        cov = coverage.get(ds)
        if not cov or not cov.get("synced"):
            gaps.append(f"数据源「{ds}」没有元数据同步记录，跳过「{node_id}」的存在性判定")
            continue
        if bare in (cov.get("tables") or set()):
            continue
        missing.append({"node_pk": int(n["id"]), "node_id": node_id})

    result = {"dry_run": bool(dry_run), "missing_nodes": len(missing),
              "edges_deleted": 0, "nodes_deleted": 0,
              "nodes": [m["node_id"] for m in missing][:50],
              "gaps": gaps[:50], "gap_count": len(gaps)}
    if not missing:
        return result

    pk_list = [m["node_pk"] for m in missing]
    edge_rows: list[dict] = []
    for i in range(0, len(pk_list), 200):
        chunk = pk_list[i:i + 200]
        ph = ",".join(["%s"] * len(chunk))
        edge_rows += execute_query(
            f"SELECT id, workspace_id, edge_type FROM adh_lineage_edges "
            f"WHERE (source_node_id IN ({ph}) OR target_node_id IN ({ph})) "
            f"  AND edge_type IN ('transform', 'produces')",
            tuple(chunk) * 2) or []
    edge_ids = [int(e["id"]) for e in edge_rows
                if int(e.get("workspace_id") or 0) == int(workspace_id or 0)]
    result["edge_count"] = len(edge_ids)
    if dry_run:
        return result

    for edge_id in edge_ids:
        execute_write("DELETE FROM adh_lineage_edges WHERE id=%s", (edge_id,))
    # 边删完后重新判存活：还可能连着资产层边（binds_to 等），那类由 reset 重算，
    # 这里只收「已经没有任何边」的孤立表节点。
    deleted_nodes = 0
    for i in range(0, len(pk_list), 200):
        chunk = pk_list[i:i + 200]
        ph = ",".join(["%s"] * len(chunk))
        still_used = {int(r["node_id"]) for r in (execute_query(
            f"SELECT source_node_id AS node_id FROM adh_lineage_edges "
            f"WHERE source_node_id IN ({ph}) UNION "
            f"SELECT target_node_id AS node_id FROM adh_lineage_edges "
            f"WHERE target_node_id IN ({ph})", tuple(chunk) * 2) or [])}
        for node_pk in chunk:
            if node_pk in still_used:
                continue
            deleted_nodes += int(execute_write(
                "DELETE FROM adh_lineage_nodes WHERE id=%s AND node_type='table'",
                (node_pk,)) or 0)
    result["edges_deleted"] = len(edge_ids)
    result["nodes_deleted"] = deleted_nodes
    return result


def sync_workflow_lineage(wf_id: int = 0) -> dict:
    """编排变更后同步定义层血缘（DAG 保存/删除的事件回调）。

    顺序不能反：先对账清理失效段落，再按当前编排补齐，最后才可能 0 变更。
    血缘是派生视图，这里的失败不回滚编排保存（编排是事实源），但必须把原因
    带回给调用方记日志/提示，不得静默当作已同步。
    """
    pruned = prune_stale_dag_lineage(0)
    index = _load_source_index()
    store = _LineageStore(0)
    built = build_dag_lineage(0, index, include_inactive=True, store=store)
    return {"workflow_id": int(wf_id or 0), "pruned_edges": pruned,
            "nodes_created": built["nodes_created"],
            "edges_created": built["edges_created"],
            "gaps": built["gaps"][:20], "gap_summary": built["gap_summary"]}


# 资产链条自生成的边类型（reset 时只清这些）
_ASSET_EDGE_TYPES = ("produces", "binds_to", "defines", "consumed_by", "derive", "visualizes")
# 资产层节点类型（表/字段不在内：它们的节点与 transform 边来自 SQL 采集与手工登记）
_ASSET_LAYER_NODE_TYPES = ("product", "ontology_object", "metric", "dimension", "dataset", "chart")


def reset_asset_layer(workspace_id: int = 0) -> dict:
    """清空派生资产层（初始化重建用），返回删除计数。

    为什么必须能清空：节点标识口径换过（图表曾按名称建节点，同名图跨看板被
    合并成一个；数据集改名会留旧节点）。不清掉重算，新旧两套节点会并存——正是
    「血缘看起来不对且很旧」的成因之一。

    只清 **派生** 内容：资产链条的边 + 资产层节点；表/字段节点与 transform 边
    （SQL 采集、DAG 定义、手工登记）一律不动。
    """
    edge_ph = ",".join(["%s"] * len(_ASSET_EDGE_TYPES))
    edges_deleted = execute_write(
        f"DELETE FROM adh_lineage_edges WHERE workspace_id=%s AND edge_type IN ({edge_ph})",
        (int(workspace_id or 0), *_ASSET_EDGE_TYPES)) or 0
    node_ph = ",".join(["%s"] * len(_ASSET_LAYER_NODE_TYPES))
    nodes_deleted = execute_write(
        f"DELETE n FROM adh_lineage_nodes n "
        f"LEFT JOIN adh_lineage_edges e ON e.source_node_id=n.id OR e.target_node_id=n.id "
        f"WHERE n.workspace_id=%s AND n.node_type IN ({node_ph}) AND e.id IS NULL",
        (int(workspace_id or 0), *_ASSET_LAYER_NODE_TYPES)) or 0
    return {"edges_deleted": int(edges_deleted), "nodes_deleted": int(nodes_deleted)}


def rebuild_lineage(workspace_id: int = 0, include_inactive_dag: bool = True,
                    dry_run: bool = False, reset_asset: bool = False) -> dict:
    """全量重建血缘（可重复执行）：先归一历史节点 id，再重算资产链与 DAG 定义链。

    血缘是派生视图：重算幂等（同节点/同边不重建），不做 DELETE 全表（手工登记
    的边保留）。dry_run 只统计待归一的节点数，不写库。
    `reset_asset=True` 先清空派生的资产层（节点+边）再重算——只在初始化/口径
    变更时用（清完后二次执行仍应 0 新增）。
    """
    index = _load_source_index()
    reconcile = reconcile_table_node_ids(workspace_id, dry_run, index)
    if dry_run:
        return {"dry_run": True, "reconcile": reconcile}
    reset = reset_asset_layer(workspace_id) if reset_asset else None
    # 定义层先对账清理失效编排段落：只补不退会让血缘停在「旧编排」上，
    # 与当前 DAG 并存两套互相矛盾的上游链（历史缺陷）。
    pruned = prune_stale_dag_lineage(workspace_id)
    # 物理存在性对账：表在源上被删后 graph_json 不会变，定义层对账永远发现不了；
    # 必须在 build 之前把指向已删除表的执行期边/产品边收掉，否则重建会把它们
    # 当成仍然有效的端点继续保留。
    missing = prune_missing_table_lineage(workspace_id, dry_run=False, index=index)
    store = _LineageStore(workspace_id)
    asset = build_asset_lineage(workspace_id, index, store)
    dag = build_dag_lineage(workspace_id, index, include_inactive_dag, store)
    return {
        "dry_run": False,
        "reconcile": reconcile,
        "reset": reset,
        "pruned_edges": pruned,
        "missing_tables": missing,
        "asset": asset,
        "dag": dag,
        "nodes_created": asset["nodes_created"] + dag["nodes_created"],
        "edges_created": asset["edges_created"] + dag["edges_created"],
    }
