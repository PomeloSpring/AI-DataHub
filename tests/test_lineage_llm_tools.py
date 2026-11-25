"""血缘赋能 LLM 的回归：定义层指纹与对账、多站扇出、落地登记 catalog、get_lineage 投影与授权面。

锁住的四类缺陷（都是「血缘看起来对、其实旧/错/不可用」的来源）：
1. **指纹含分隔符**：`dag:<工作流名>/<节点key>` 里的名字常含 `/`（日/月/年），
   解析会落在名字内部 → 每次重建删了又建，永不幂等；改用 `dag:<id>:<key>|<名字>`。
2. **只补不退**：DAG 改名/删节点后旧边留在图上，与当前编编排并存两条矛盾链。
3. **只连单站**：多站物化按 `sites` 逐站取数写同一目标表，血缘只连 `site` 会少算上游。
4. **落地不登记 catalog**：写入产物「有列无表」，目录/列级血缘/新鲜度全看不见。

另锁 `get_lineage` 的三条纪律：候选回抛不猜、未授权源隐去要显式说明、表用限定名。
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.gov.services import lineage_service as ls


# ═══════════════════════════════════════════════════════════════════
# 1. 定义层指纹（解析必须与「名字里有没有斜杠」无关）
# ═══════════════════════════════════════════════════════════════════
class TestDagTraceFingerprint:
    def test_new_format_parses_id_key_readable(self):
        assert ls._parse_dag_trace("dag:42:mat_d|5 站合并 · 日/月/年") == (
            "42", "mat_d", "5 站合并 · 日/月/年")

    def test_slash_in_workflow_name_cannot_shift_the_boundary(self):
        """名字含 `/` 时节点 key 不能被切错（历史缺陷的直接成因）。"""
        wf_id, key, readable = ls._parse_dag_trace("dag:7:a|日/月/年")
        assert (wf_id, key) == ("7", "a")
        assert readable == "日/月/年"

    def test_legacy_name_based_trace_is_unparseable(self):
        """旧格式没有 id：一律视为失效段落（派生边重建即恢复），不得猜。"""
        wf_id, key, _ = ls._parse_dag_trace("dag:5站合并·日/月/年/mat_d")
        assert wf_id == ""
        assert key == "5站合并·日/月/年/mat_d"

    def test_empty_trace_is_safe(self):
        assert ls._parse_dag_trace("") == ("", "", "")


# ═══════════════════════════════════════════════════════════════════
# 2. 对账清理（只删定义层失效段落，不碰执行期采集/手工登记）
# ═══════════════════════════════════════════════════════════════════
class FakePruneDB:
    def __init__(self):
        self.deleted_edges = []
        self.deleted_nodes = []
        self.workflows = [
            {"id": 42, "name": "物化 · 案例域（5 站合并 · 日/月/年）",
             "graph_json": json.dumps({"nodes": [{"key": "mat_d"}, {"key": "mat_m"}]})},
            {"id": 50, "name": "坏编排", "graph_json": "{not json"},
        ]
        self.edges = [
            # 仍有效（编排 42 里真有 mat_d）
            {"id": 1, "source_node_id": 11, "target_node_id": 12,
             "transform_expr": "dag:42:mat_d|物化 · 案例域"},
            # 节点已被删
            {"id": 2, "source_node_id": 11, "target_node_id": 13,
             "transform_expr": "dag:42:mat_gone|物化 · 案例域"},
            # 编排已被删
            {"id": 3, "source_node_id": 11, "target_node_id": 14,
             "transform_expr": "dag:99:mat_d|旧编排"},
            # 旧格式：无法可靠对账 → 失效
            {"id": 4, "source_node_id": 15, "target_node_id": 16,
             "transform_expr": "dag:5站合并·日/月/年/mat_d"},
            # 编排在但 graph_json 坏：保留（不把坏数据当“已删除”）
            {"id": 5, "source_node_id": 17, "target_node_id": 18,
             "transform_expr": "dag:50:any|x"},
        ]

    def query(self, sql, params=None, fetchone=False):
        s = " ".join(str(sql).split())
        if "FROM adh_dag_workflows" in s:
            return self.workflows
        if "FROM adh_lineage_edges" in s and "transform_expr LIKE" in s:
            return self.edges
        if "UNION" in s:                      # 删边后仍被使用的端点
            ids = set(params or ())
            used = {e["source_node_id"] for e in self.edges if e["id"] in (1, 5)}
            return [{"node_id": n} for n in (ids & (used | {11, 17}))]
        return []

    def write(self, sql, params=None):
        s = " ".join(str(sql).split())
        if s.startswith("DELETE FROM adh_lineage_edges"):
            self.deleted_edges.append(int(params[0]))
        elif s.startswith("DELETE FROM adh_lineage_nodes"):
            self.deleted_nodes.append(int(params[0]))
        return 1


def _patch_service(monkeypatch, db):
    monkeypatch.setattr(ls, "execute_query",
                        lambda sql, params=None, fetchone=False: db.query(sql, params, fetchone))
    monkeypatch.setattr(ls, "execute_write",
                        lambda sql, params=None: db.write(sql, params))


class TestPruneStale:
    def test_prunes_only_stale_definition_segments(self, monkeypatch):
        db = FakePruneDB()
        _patch_service(monkeypatch, db)
        pruned = ls.prune_stale_dag_lineage(0)
        assert pruned == 3
        assert sorted(db.deleted_edges) == [2, 3, 4]
        assert 1 not in db.deleted_edges, "有效段落被误删"
        assert 5 not in db.deleted_edges, "graph_json 解析失败的编排不得当成已删除"

    def test_second_run_is_noop(self, monkeypatch):
        """幂等：清理后再跑一次必须 0 变更（派生视图要可反复执行）。"""
        db = FakePruneDB()
        _patch_service(monkeypatch, db)
        ls.prune_stale_dag_lineage(0)
        db.edges = [e for e in db.edges if e["id"] not in db.deleted_edges]
        assert ls.prune_stale_dag_lineage(0) == 0

    def test_ghost_table_nodes_are_collected(self, monkeypatch):
        """删边后无任何连接的 table 节点要收敛，否则图上留下不存在的表。"""
        db = FakePruneDB()
        _patch_service(monkeypatch, db)
        ls.prune_stale_dag_lineage(0)
        assert db.deleted_nodes, "幽灵 table 节点未被清理"


# ═══════════════════════════════════════════════════════════════════
# 3. 多站物化扇出（血缘与 materialize_runner 的执行口径同源）
# ═══════════════════════════════════════════════════════════════════
class FakeDagDB:
    def __init__(self):
        self.nodes = {}
        self.edges = []
        self.pk = 0

    def query(self, sql, params=None, fetchone=False):
        s = " ".join(str(sql).split())
        if "FROM adh_datasets WHERE status='active'" in s:
            return [{"id": 3, "name": "案例明细", "source_type": "sql",
                     "datasource_id": 1, "touched_tables": '["t_case_records"]',
                     "sql_query": ""}]
        if "FROM adh_dag_workflows" in s:
            return [{"id": 42, "name": "物化 · 案例域（5 站合并 · 日/月/年）", "is_active": 1,
                     "graph_json": json.dumps({"nodes": [
                         {"key": "mat_d", "type": "materialize", "config": {
                             "dataset": "案例明细", "target_datasource": "Doris",
                             "target_table": "dws_case_metric_d",
                             "sites": ["cn", "en"]}}]})}]
        if "FROM adh_lineage_nodes" in s:
            return [{"id": pk, "node_type": t, "node_id": nid}
                    for (t, nid), pk in self.nodes.items()]
        if "FROM adh_lineage_edges" in s:
            return [{"source_node_id": src, "target_node_id": dst, "edge_type": et}
                    for src, dst, et, _expr in self.edges]
        return []

    def insert_many(self, sql, rows, chunk=500):
        s = " ".join(str(sql).split())
        if "INTO adh_lineage_nodes" in s:
            for _ws, ntype, nid, _name, _ds, _meta in rows:
                if (ntype, nid) not in self.nodes:
                    self.pk += 1
                    self.nodes[(ntype, nid)] = self.pk
            return len(rows)
        if "INTO adh_lineage_edges" in s:
            for _ws, src, dst, etype, expr, _conf in rows:
                self.edges.append((src, dst, etype, expr))
            return len(rows)
        return len(rows)


def _site_index():
    rows = [
        {"name": "prd-cn", "site_code": "cn", "db_type": "mysql", "id": 1,
         "database_name": "stardb"},
        {"name": "prd-en", "site_code": "en", "db_type": "mysql", "id": 2,
         "database_name": "stardb"},
        {"name": "Doris", "site_code": "", "db_type": "doris", "id": 9,
         "database_name": "dws"},
    ]
    return {"by_name": {r["name"]: r for r in rows},
            "by_id": {r["id"]: r["name"] for r in rows},
            "alias": {r["name"].lower(): r["name"] for r in rows},
            "ambiguous_db": {"stardb"}, "rows": rows}


class TestMultiSiteFanout:
    def test_each_site_contributes_an_upstream_edge(self, monkeypatch):
        db = FakeDagDB()
        _patch_service(monkeypatch, db)
        monkeypatch.setattr(ls, "_bulk_executemany",
                            lambda sql, rows, chunk=500: db.insert_many(sql, rows, chunk))
        result = ls.build_dag_lineage(0, index=_site_index(), include_inactive=True)

        nid = db.nodes
        srcs = {nid[("table", "prd-cn.t_case_records")], nid[("table", "prd-en.t_case_records")]}
        dst = nid[("table", "Doris.dws_case_metric_d")]
        assert {(s, dst, "transform") for s in srcs} <= {(e[0], e[1], e[2]) for e in db.edges}, \
            "多站物化只连了部分站点，血缘比执行少算上游"
        assert result["edges_created"] == 2

    def test_fingerprint_uses_workflow_id(self, monkeypatch):
        db = FakeDagDB()
        _patch_service(monkeypatch, db)
        monkeypatch.setattr(ls, "_bulk_executemany",
                            lambda sql, rows, chunk=500: db.insert_many(sql, rows, chunk))
        ls.build_dag_lineage(0, index=_site_index(), include_inactive=True)
        assert all(e[3].startswith("dag:42:mat_d|") for e in db.edges), [e[3] for e in db.edges]

    def test_rebuild_is_idempotent(self, monkeypatch):
        """同一编排重算两次不得新增边（指纹稳定 → 命中内存集合）。"""
        db = FakeDagDB()
        _patch_service(monkeypatch, db)
        monkeypatch.setattr(ls, "_bulk_executemany",
                            lambda sql, rows, chunk=500: db.insert_many(sql, rows, chunk))
        ls.build_dag_lineage(0, index=_site_index(), include_inactive=True)
        store = ls._LineageStore(0)
        again = ls.build_dag_lineage(0, index=_site_index(), include_inactive=True, store=store)
        assert again["edges_created"] == 0 and again["nodes_created"] == 0


# ═══════════════════════════════════════════════════════════════════
# 4. 写入落地登记 catalog（否则产物「有列无表」）
# ═══════════════════════════════════════════════════════════════════
class TestSyncCatalogAfterWrite:
    def test_registers_and_reports_columns(self, monkeypatch):
        from backend.modules.flow.dag import node_runners
        from backend.common import db as common_db
        from backend.modules.catalog.services import metadata_sync

        monkeypatch.setattr(common_db, "get_datasource_by_name",
                            lambda name: {"id": 9, "name": name}, raising=False)
        monkeypatch.setattr(metadata_sync, "sync_table_columns",
                            lambda ds_id, table: {"total_columns": 9, "created": 9},
                            raising=False)
        out = node_runners._sync_catalog_after_write("Doris", "dws_case_metric_d", {})
        assert out["synced"] is True
        assert out["columns"] == 9
        assert out["table"] == "Doris.dws_case_metric_d"

    def test_missing_target_is_explicit(self):
        from backend.modules.flow.dag import node_runners
        out = node_runners._sync_catalog_after_write("", "", {})
        assert out["synced"] is False
        assert out["error"], "空目标必须显式说明，不能假装已登记"

    def test_unknown_datasource_is_fail_loud(self, monkeypatch):
        from backend.modules.flow.dag import node_runners
        from backend.common import db as common_db

        monkeypatch.setattr(common_db, "get_datasource_by_name",
                            lambda name: None, raising=False)
        out = node_runners._sync_catalog_after_write("nosuch", "t_x", {})
        assert out["synced"] is False
        assert "不存在" in out["error"]


# ═══════════════════════════════════════════════════════════════════
# 5. get_lineage 投影（候选回抛 / 未授权隐去 / 限定名 / 新鲜度口径）
# ═══════════════════════════════════════════════════════════════════
def _run_tool(monkeypatch, args, *, matched, adjacency=None, nodes=None,
              allowed=(1,), fresh=None):
    from backend.modules.mind.execution.sdk_tools import lineage_tools as lt
    from backend.modules.mind.execution.sdk_tools import context as ctx_mod
    from backend.modules.mind.execution.models import ExecutionContext

    ctx_mod.set_execution_context(ExecutionContext(user_id=7, workspace_id=0))
    monkeypatch.setattr(lt, "_resolve_start", lambda asset, kind: (matched, [], ""))
    monkeypatch.setattr(lt, "_adjacency",
                        lambda ids, d: (adjacency or {"out": {}, "in": {}}, nodes or {}))
    monkeypatch.setattr(lt, "_freshness", lambda rows: fresh or [])
    monkeypatch.setattr(lt, "_allowed_source_ids", lambda uid, ws: set(allowed))
    payload = asyncio.run(lt.get_lineage(args))
    return json.loads(payload["content"][0]["text"]), payload


class TestGetLineageProjection:
    def test_table_uses_qualified_name(self, monkeypatch):
        """5 站同名表必须能区分：表节点对外用「数据源.表」限定名。"""
        seed = [{"id": 1, "node_type": "table", "node_id": "prd-cn.t_case_records",
                 "node_name": "t_case_records", "datasource_id": 1}]
        adj = {"out": {}, "in": {1: [(2, {"edge_type": "transform", "src": 2, "dst": 1})]}}
        nodes = {2: {"node_type": "table", "node_id": "prd-en.t_case_records",
                     "node_name": "t_case_records", "datasource_id": 2}}
        out, _ = _run_tool(monkeypatch, {"asset": "prd-cn.t_case_records"},
                           matched=seed, adjacency=adj, nodes=nodes, allowed=(1, 2))
        names = [h["name"] for h in out["upstream"]]
        assert "prd-en.t_case_records" in names
        assert "t_case_records" not in names, "裸表名无法区分站点"

    def test_unauthorized_source_is_hidden_but_declared(self, monkeypatch):
        """未授权数据源的表不出名，但必须显式说明（不能让人以为“没有上游”）。"""
        seed = [{"id": 1, "node_type": "table", "node_id": "prd-cn.t_a",
                 "node_name": "t_a", "datasource_id": 1}]
        adj = {"out": {}, "in": {1: [(2, {"edge_type": "transform", "src": 2, "dst": 1})]}}
        nodes = {2: {"node_type": "table", "node_id": "prd-eu.t_secret",
                     "node_name": "t_secret", "datasource_id": 4}}
        out, _ = _run_tool(monkeypatch, {"asset": "prd-cn.t_a"},
                           matched=seed, adjacency=adj, nodes=nodes, allowed=(1,))
        assert out["upstream"] == []
        assert any("未授权" in n for n in out["notes"])
        assert "t_secret" not in json.dumps(out, ensure_ascii=False), "未授权表名泄露"

    def test_no_source_grant_is_fail_closed(self, monkeypatch):
        seed = [{"id": 1, "node_type": "table", "node_id": "prd-cn.t_a",
                 "node_name": "t_a", "datasource_id": 1}]
        out, _ = _run_tool(monkeypatch, {"asset": "prd-cn.t_a"}, matched=seed, allowed=())
        assert any("没有任何数据源授权" in n for n in out["notes"])

    def test_no_downstream_does_not_imply_no_impact(self, monkeypatch):
        seed = [{"id": 1, "node_type": "table", "node_id": "Doris.t_d",
                 "node_name": "t_d", "datasource_id": 9}]
        out, _ = _run_tool(monkeypatch, {"asset": "Doris.t_d"}, matched=seed, allowed=(9,))
        assert any("不能推断" in n for n in out["notes"])

    def test_never_exposes_internal_ids_or_sql(self, monkeypatch):
        seed = [{"id": 1, "node_type": "table", "node_id": "Doris.t_d",
                 "node_name": "t_d", "datasource_id": 9}]
        adj = {"out": {1: [(2, {"edge_type": "transform", "src": 1, "dst": 2})]}, "in": {}}
        nodes = {2: {"node_type": "product", "node_id": "Doris.t_d",
                     "node_name": "Doris.t_d", "datasource_id": 0}}
        text = json.dumps(_run_tool(monkeypatch, {"asset": "Doris.t_d", "direction": "downstream"},
                                    matched=seed, adjacency=adj, nodes=nodes, allowed=(9,))[0],
                          ensure_ascii=False)
        assert "datasource_id" not in text
        assert "SELECT" not in text.upper()


class TestResolveStartCandidates:
    """名字没精确命中只回候选，绝不静默绑定（宁缺勿错）。"""

    def test_fuzzy_returns_candidates(self, monkeypatch):
        from backend.modules.mind.execution.sdk_tools import lineage_tools as lt

        calls = []

        def fake_query(sql, params=None, fetchone=False):
            calls.append(sql)
            if "LIKE" in sql:
                return [{"id": 1, "node_type": "table", "node_id": "Doris.dws_case_metric_d",
                         "node_name": "dws_case_metric_d", "datasource_id": 9}]
            return []

        monkeypatch.setattr("backend.common.db.execute_query", fake_query)
        matched, candidates, err = lt._resolve_start("case_metric", "")
        assert matched == [] and candidates and "候选" in err

    def test_unknown_name_says_how_to_write_it(self, monkeypatch):
        from backend.modules.mind.execution.sdk_tools import lineage_tools as lt

        monkeypatch.setattr("backend.common.db.execute_query",
                            lambda sql, params=None, fetchone=False: [])
        matched, candidates, err = lt._resolve_start("根本不存在", "")
        assert matched == [] and candidates == []
        assert "数据源名.表名" in err


# ═══════════════════════════════════════════════════════════════════
# 6. 授权面（组注册 → 编译 → 精确工具名；迁移只做加法）
# ═══════════════════════════════════════════════════════════════════
class TestLineageAuthorization:
    def test_group_is_registered(self):
        from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_TOOLS, TOOL_SERVER_BUILDERS
        assert TOOL_SERVER_TOOLS["lineage"] == ("datahub_lineage", ["get_lineage"])
        assert "lineage" in TOOL_SERVER_BUILDERS

    def test_compile_policy_registers_exact_tool_name(self):
        from backend.modules.mind.execution.tool_policy import compile_policy
        policy = compile_policy({"as_bot_key": "t", "tools": {
            "mcp": {"lineage": ["get_lineage"]},
            "groups": ["lineage"], "external": {}, "standard": ["read"]}})
        assert "mcp__datahub_lineage__get_lineage" in policy.allowed

    def test_manifest_has_business_description(self):
        from backend.modules.mind.execution.tool_policy import compile_policy
        policy = compile_policy({"as_bot_key": "t", "tools": {
            "mcp": {"lineage": ["get_lineage"]}, "groups": ["lineage"],
            "external": {}, "standard": ["read"]}})
        desc = next(t["description"] for t in policy.manifest()["tools"]
                    if t["name"].endswith("get_lineage"))
        assert len(desc) > 40 and desc != "get_lineage", "面板会显示裸英文名"

    def test_unknown_group_still_rejected(self):
        from backend.modules.mind.execution.tool_policy import normalize_tools
        with pytest.raises(ValueError):
            normalize_tools({"mcp": {"lineage_typo": ["get_lineage"]}})

    def test_migration_is_add_only(self):
        """迁移必须是「只加不覆盖」：已有 lineage 键的行（含显式空数组）不得被改。"""
        path = os.path.join(os.path.dirname(__file__), "..",
                            "docker/mysql/lineage_tool_grant_migration.sql")
        sql = open(path, encoding="utf-8").read()
        assert "JSON_SET(tools, '$.mcp.lineage'" in sql
        assert "JSON_CONTAINS_PATH(tools, 'one', '$.mcp.lineage') = 0" in sql
        assert "JSON_CONTAINS_PATH(tools, 'one', '$.mcp') = 1" in sql


# ═══════════════════════════════════════════════════════════════════
# 7. 物理存在性对账（表在源上被 DROP 后，血缘不得继续当有效上游）
#    历史缺陷：Doris `dws_*_metric` 拆成 `_d/_m/_y` 后旧表已删，
#    `prune_stale_dag_lineage` 只对账 graph_json（定义没改 → 永远发现不了），
#    执行期采集边与产品节点一直挂着，LLM 下钻会拿到不存在的依赖。
# ═══════════════════════════════════════════════════════════════════
class FakeMissingDB:
    """workspace 0 的四类表节点：已消失 / 仍在 / 裸表名 / 源未同步。

    `manual_edge=True` 模拟该表另有一条手工登记血缘（edge_type=manual）：
    那条边不属派生资产层，删完 transform/produces 后节点仍有连接 → 保留。
    """

    def __init__(self, manual_edge: bool = False):
        self.nodes = [
            {"id": 180, "node_id": "Doris.dws_case_metric"},      # 源上已删
            {"id": 181, "node_id": "Doris.dws_case_metric_d"},    # 仍在
            {"id": 182, "node_id": "t_bare"},                     # 裸表名定不了源
            {"id": 183, "node_id": "prd-eu.t_x"},                 # 该源无同步记录
        ]
        self.edges = [
            {"id": 134, "workspace_id": 0, "source_node_id": 1,
             "target_node_id": 180, "edge_type": "transform"},
            {"id": 8554, "workspace_id": 0, "source_node_id": 180,
             "target_node_id": 8614, "edge_type": "produces"},
        ]
        if manual_edge:
            self.edges.append(
                {"id": 901, "workspace_id": 0, "source_node_id": 180,
                 "target_node_id": 9001, "edge_type": "manual"})
        self.deleted_edges: list[int] = []
        self.deleted_nodes: list[int] = []

    def query(self, sql, params=None, fetchone=False):
        s = " ".join(str(sql).split())
        if "FROM adh_table_info" in s:
            return [{"datasource_id": 9, "table_name": "dws_case_metric_d"}]
        if "FROM adh_lineage_nodes" in s:
            return list(self.nodes)
        if "UNION" in s:                       # 删边后仍被引用的端点
            ids = set(params or ())
            used = {e["source_node_id"] for e in self.edges}
            used |= {e["target_node_id"] for e in self.edges}
            return [{"node_id": n} for n in ids & used]
        if "FROM adh_lineage_edges" in s:
            ids = set(params or ())
            return [e for e in self.edges
                    if (e["source_node_id"] in ids or e["target_node_id"] in ids)
                    and e["edge_type"] in ("transform", "produces")]
        return []

    def write(self, sql, params=None):
        s = " ".join(str(sql).split())
        if s.startswith("DELETE FROM adh_lineage_edges"):
            eid = int(params[0])
            self.deleted_edges.append(eid)
            self.edges = [e for e in self.edges if e["id"] != eid]
            return 1
        if s.startswith("DELETE FROM adh_lineage_nodes"):
            nid = int(params[0])
            self.deleted_nodes.append(nid)
            self.nodes = [n for n in self.nodes if n["id"] != nid]
            return 1
        return 1


class TestPruneMissingTables:
    def test_dry_run_reports_without_writing(self, monkeypatch):
        db = FakeMissingDB()
        _patch_service(monkeypatch, db)
        out = ls.prune_missing_table_lineage(0, index=_site_index())
        assert out["dry_run"] is True
        assert out["missing_nodes"] == 1
        assert out["nodes"] == ["Doris.dws_case_metric"]
        assert out["edge_count"] == 2, "transform + produces 两条端点边"
        assert out["edges_deleted"] == 0 and out["nodes_deleted"] == 0
        assert db.deleted_edges == [] and db.deleted_nodes == [], "dry-run 不得写库"

    def test_apply_removes_edges_and_orphan_node(self, monkeypatch):
        db = FakeMissingDB()
        _patch_service(monkeypatch, db)
        out = ls.prune_missing_table_lineage(0, dry_run=False, index=_site_index())
        assert sorted(db.deleted_edges) == [134, 8554], "只删执行期/产出边，不碰资产层边"
        assert db.deleted_nodes == [180], "边删完后无连接的 table 节点必须收敛"
        assert out["edges_deleted"] == 2 and out["nodes_deleted"] == 1

    def test_manual_edge_keeps_node(self, monkeypatch):
        """手工登记血缘不属派生资产层：不删该边，节点仍有连接 → 不收敛。"""
        db = FakeMissingDB(manual_edge=True)
        _patch_service(monkeypatch, db)
        out = ls.prune_missing_table_lineage(0, dry_run=False, index=_site_index())
        assert sorted(db.deleted_edges) == [134, 8554], "手工登记的 manual 边不得被删"
        assert db.deleted_nodes == [], "节点仍连着手工边，不能当孤立点删除"
        assert out["nodes_deleted"] == 0

    def test_second_run_is_noop(self, monkeypatch):
        """幂等：重建可反复执行，第二次必须 0 变更。"""
        db = FakeMissingDB()
        _patch_service(monkeypatch, db)
        ls.prune_missing_table_lineage(0, dry_run=False, index=_site_index())
        again = ls.prune_missing_table_lineage(0, dry_run=False, index=_site_index())
        assert again["missing_nodes"] == 0 and again["edges_deleted"] == 0

    def test_unverifiable_nodes_are_skipped_with_gaps(self, monkeypatch):
        """宁缺勿错：裸表名/未同步源不能凭猜判定消失，必须跳过并报缺口。"""
        db = FakeMissingDB()
        _patch_service(monkeypatch, db)
        out = ls.prune_missing_table_lineage(0, index=_site_index())
        text = "\n".join(out["gaps"])
        assert "t_bare" in text and "裸表名" in text
        assert "prd-eu.t_x" in text and "没有元数据同步记录" in text
        assert out["nodes"] == ["Doris.dws_case_metric"], "无法判定的表不得被列入清理"


# ═══════════════════════════════════════════════════════════════════
# 8. 资产层的 catalog 闸（表没了，产品身份也不该继续出现在图面）
# ═══════════════════════════════════════════════════════════════════
class FakeAssetDB(FakeDagDB):
    def __init__(self, tables):
        super().__init__()
        self.tables = tables
        self.products = [
            {"product_name": "案例指标（旧）", "product_class": "metric", "site": "cn",
             "datasource_name": "Doris", "physical_table": "dws_case_metric",
             "status": "draft"},
            {"product_name": "案例指标日", "product_class": "metric", "site": "cn",
             "datasource_name": "Doris", "physical_table": "dws_case_metric_d",
             "status": "certified"},
            {"product_name": "已退役", "product_class": "metric", "site": "cn",
             "datasource_name": "Doris", "physical_table": "dws_case_metric_d",
             "status": "retired"},
        ]

    def query(self, sql, params=None, fetchone=False):
        s = " ".join(str(sql).split())
        if "FROM adh_table_info" in s:
            return self.tables
        if "FROM adh_data_products" in s:
            return self.products
        if "FROM adh_ontology_bindings" in s:
            return []
        if ("FROM adh_datasets" in s or "FROM adh_charts" in s
                or "FROM adh_metrics" in s or "FROM adh_dimensions" in s):
            return []
        return super().query(sql, params, fetchone)


def _build_assets(monkeypatch, tables):
    db = FakeAssetDB(tables)
    _patch_service(monkeypatch, db)
    monkeypatch.setattr(ls, "_bulk_executemany",
                        lambda sql, rows, chunk=500: db.insert_many(sql, rows, chunk))
    result = ls.build_asset_lineage(0, index=_site_index())
    return db, result


class TestCatalogGateInAssetLineage:
    def test_product_on_dropped_table_is_not_built(self, monkeypatch):
        db, result = _build_assets(monkeypatch, [
            {"datasource_id": 9, "table_name": "dws_case_metric_d"}])
        nodes = db.nodes
        assert ("table", "Doris.dws_case_metric") not in nodes
        assert ("product", "案例指标（旧）") not in nodes, "表没了，产品不得留孤立点"
        assert ("table", "Doris.dws_case_metric_d") in nodes
        assert ("product", "案例指标日") in nodes
        assert ("product", "已退役") not in nodes, "retired 产品必须退场"
        assert any("在源上已不存在" in g and "dws_case_metric" in g for g in result["gaps"])

    def test_existing_table_keeps_produces_edge(self, monkeypatch):
        db, _ = _build_assets(monkeypatch, [
            {"datasource_id": 9, "table_name": "dws_case_metric_d"}])
        t = db.nodes[("table", "Doris.dws_case_metric_d")]
        p = db.nodes[("product", "案例指标日")]
        assert (t, p, "produces") in {(e[0], e[1], e[2]) for e in db.edges}

    def test_unsynced_source_is_not_gated(self, monkeypatch):
        """该源 catalog 整份为空 = 还没同步过，不能当「表全删了」（宁缺勿错）。"""
        db, _ = _build_assets(monkeypatch, [])
        assert ("table", "Doris.dws_case_metric") in db.nodes
        assert ("product", "案例指标（旧）") in db.nodes


# ═══════════════════════════════════════════════════════════════════
# 9. 物理存在性投影进 get_lineage（三个口径分开回答，缺失上升到顶层）
# ═══════════════════════════════════════════════════════════════════
class TestPhysicalStatus:
    def _patch_freshness(self, monkeypatch, coverage, sync_rows):
        import backend.common.db as common_db
        from backend.modules.gov.services import lineage_service as lsvc
        from backend.modules.mind.execution.sdk_tools import lineage_tools as lt

        def fake_query(sql, params=None, fetchone=False):
            s = " ".join(str(sql).split())
            if "FROM adh_table_info" in s:
                return sync_rows
            if "FROM adh_dag_workflows" in s:
                return []
            return []

        monkeypatch.setattr(common_db, "execute_query", fake_query)
        monkeypatch.setattr(lsvc, "_load_source_index", lambda: _site_index())
        monkeypatch.setattr(lsvc, "_load_catalog_coverage", lambda index=None: coverage)
        return lt

    def test_three_states(self, monkeypatch):
        lt = self._patch_freshness(
            monkeypatch,
            {"Doris": {"tables": {"dws_case_metric_d"}, "synced": True}},
            [{"datasource_id": 9, "table_name": "dws_case_metric_d",
              "sync_time": "2026-11-25 12:01:00"}])
        rows = [
            {"node_type": "table", "node_id": "Doris.dws_case_metric_d",
             "node_name": "dws_case_metric_d"},
            {"node_type": "table", "node_id": "Doris.dws_case_metric",
             "node_name": "dws_case_metric"},
            {"node_type": "table", "node_id": "prd-eu.t_x", "node_name": "t_x"},
        ]
        out = {i["table"]: i for i in lt._freshness(rows)}
        assert out["Doris.dws_case_metric_d"]["physical_status"] == "ok"
        assert out["Doris.dws_case_metric_d"]["catalog_synced_at"] == "2026-11-25 12:01:00"
        assert out["Doris.dws_case_metric"]["physical_status"] == "missing"
        assert "不能当作当前有效依赖" in out["Doris.dws_case_metric"]["note"]
        assert out["prd-eu.t_x"]["physical_status"] == "unregistered"

    def test_cross_source_same_name_does_not_borrow_sync_time(self, monkeypatch):
        """跨源同名表必须按 (源, 表) 取同步时间，不能拿别的源的 MAX 充数。"""
        lt = self._patch_freshness(
            monkeypatch,
            {"Doris": {"tables": set(), "synced": True},
             "prd-cn": {"tables": {"t_case"}, "synced": True}},
            [{"datasource_id": 1, "table_name": "t_case",
              "sync_time": "2026-11-20 08:00:00"}])
        out = lt._freshness([{"node_type": "table", "node_id": "Doris.t_case",
                              "node_name": "t_case"}])[0]
        assert out["physical_status"] == "missing"
        assert out["catalog_synced_at"] == "", "不得借用 prd-cn 同名表的同步时间"

    def test_missing_table_is_raised_to_top_level_notes(self, monkeypatch):
        seed = [{"id": 1, "node_type": "table", "node_id": "Doris.dws_case_metric",
                 "node_name": "dws_case_metric", "datasource_id": 9}]
        out, _ = _run_tool(
            monkeypatch, {"asset": "Doris.dws_case_metric"}, matched=seed, allowed=(9,),
            fresh=[{"table": "Doris.dws_case_metric", "physical_status": "missing",
                    "catalog_synced_at": ""}])
        assert any("已不在源上" in n and "dws_case_metric" in n for n in out["notes"]), \
            "逐表新鲜度模型容易跳过，缺失必须上升到顶层备注"


# ═══════════════════════════════════════════════════════════════════
# 10. 产品退役入口（状态流转 + 权限落点）
# ═══════════════════════════════════════════════════════════════════
class TestProductStatus:
    def _patch(self, monkeypatch, products):
        from backend.modules.catalog.services import data_product_service as dps

        written: list[tuple] = []
        monkeypatch.setattr(dps, "get_product", lambda name: products.get(name))
        monkeypatch.setattr(dps, "execute_write",
                            lambda sql, params=None: written.append((sql, params)) or 1)
        return dps, written

    def test_invalid_status_is_rejected(self, monkeypatch):
        dps, written = self._patch(monkeypatch, {"p": {"status": "draft"}})
        with pytest.raises(ValueError):
            dps.set_product_status("p", "deleted")
        assert written == [], "白名单之外的语义不得写库"

    def test_unknown_product_raises_keyerror(self, monkeypatch):
        dps, _ = self._patch(monkeypatch, {})
        with pytest.raises(KeyError):
            dps.set_product_status("nosuch", "retired")

    def test_retire_writes_only_status_columns(self, monkeypatch):
        dps, written = self._patch(monkeypatch, {"p": {"status": "draft"}})
        out = dps.set_product_status("p", "retired", updated_by="admin", reason="源表已删")
        assert out["changed"] is True and out["previous"] == "draft"
        sql, params = written[0]
        assert "schema_version" not in sql and "contract" not in sql, \
            "状态流转不得碰契约与版本（历史版本仍可回溯）"
        assert params[0] == "retired"

    def test_same_status_is_idempotent(self, monkeypatch):
        dps, written = self._patch(monkeypatch, {"p": {"status": "retired"}})
        out = dps.set_product_status("p", "retired")
        assert out["changed"] is False and written == []

    def test_endpoint_is_admin_gated(self):
        import inspect
        from backend.modules.catalog.api import data_products as api
        src = inspect.getsource(api.set_data_product_status)
        assert "@router.patch" in src and "require_admin" in src

    def test_migration_registers_patch_idempotently(self):
        """catalog:manage 原 api_method 不含 PATCH，不补则新端点对非 admin 一律 403。"""
        path = os.path.join(os.path.dirname(__file__), "..",
                            "docker/mysql/data_product_retire_migration.sql")
        sql = open(path, encoding="utf-8").read()
        assert "perm_code = 'catalog:manage'" in sql
        assert "FIND_IN_SET('PATCH'" in sql and ",PATCH" in sql