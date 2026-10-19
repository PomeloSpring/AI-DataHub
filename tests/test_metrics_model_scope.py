"""指标/维度模型作用域 + 未归属资产池单测(离线, 假 DB 连接)。

锁定模型工作区地基行为:
- scope=model → bound_object_key IN 模型对象集(来自 adh_ontology_objects);
- scope=unbound → 只查空绑定; 缺省 → 旧全局行为不变;
- 模型无对象 → 短路空集(不得回退成"不过滤");
- 指标字典不按 workspace 分域(列表不过滤/查重全局/写入恒 ws=0);
- asset_pool → unbound/orphan 分类 + suggestions 未迁移容错。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.modules.catalog.services import metrics_service as ms


class _Cur:
    def __init__(self, script):
        self.script = script   # list[(sql_sub, rows)] 按调用顺序消费
        self.rows = []
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, list(params or [])))
        if "COUNT(*)" in sql:          # 分页总数查询统一回 0, 不影响 SQL 断言
            self.rows = [{"total": 0}]
            return
        for sub, rows in self.script:
            if sub in sql and not getattr(self, "_used", {}).get(sub, False):
                self._used = getattr(self, "_used", {})
                self._used[sub] = True
                self.rows = rows
                return
        self.rows = []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, script):
        self.cur = _Cur(script)

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


OBJ_ROWS = [{"object_key": "case"}, {"object_key": "hospital"}]


@pytest.fixture
def scoped_conn(monkeypatch):
    """第一次 SELECT object_key 返回对象集; 后续业务查询回放空列表但记录 SQL。"""
    script = [("FROM adh_ontology_objects", OBJ_ROWS)]
    conn = _Conn(script)
    monkeypatch.setattr(ms, "DBConnection", lambda: conn)
    return conn


class TestModelScope:
    def test_list_metrics_model_scope_injects_in_clause(self, scoped_conn):
        ms.list_metrics(size=100, model_id=7, scope="model")
        biz = [c for c in scoped_conn.cur.calls if "FROM adh_metrics" in c[0]]
        assert biz, "应执行指标查询"
        sql, params = biz[0]
        assert "bound_object_key IN (%s, %s)" in sql
        assert "case" in params and "hospital" in params and 7 not in params

    def test_unbound_scope_uses_empty_binding(self, scoped_conn):
        ms.list_metrics(size=100, scope="unbound")
        sql, params = [c for c in scoped_conn.cur.calls if "FROM adh_metrics"][0]
        assert "bound_object_key IS NULL OR bound_object_key = ''" in sql
        # unbound 不触发对象集查询
        assert not any("adh_ontology_objects" in s for s, _ in scoped_conn.cur.calls
                       if "FROM adh_metrics" not in s and "SELECT object_key" in s)

    def test_default_scope_keeps_legacy(self, scoped_conn):
        ms.list_metrics(size=100)
        sql, _ = [c for c in scoped_conn.cur.calls if "FROM adh_metrics"][0]
        assert "bound_object_key" not in sql

    def test_empty_model_short_circuits(self, monkeypatch):
        conn = _Conn([("FROM adh_ontology_objects", [])])
        monkeypatch.setattr(ms, "DBConnection", lambda: conn)
        out = ms.list_metrics(model_id=99, scope="model")
        assert out == {"total": 0, "items": []}   # 不得回退为不过滤
        assert not any("FROM adh_metrics" in s for s, _ in conn.cur.calls)


class TestMetricsNotWorkspaceScoped:
    """指标字典不按 workspace 分域(全局唯一口径)。

    历史缺陷: list_metrics 叠了 `workspace_id = %s` 精确等值, 而存量字典行全在
    ws=0(全局)/ws=1(旧默认空间), 个人工作站(ws=301)进指标中心看到“暂无指标数据”,
    同页维度 Tab 因不过滤而正常 —— 两个 Tab 口径分叉。下列用例把“不得再引入
    workspace 过滤/写入”钉住, 以免同类回归。
    """

    def test_list_metrics_signature_has_no_workspace_param(self):
        import inspect
        assert "workspace_id" not in inspect.signature(ms.list_metrics).parameters, \
            "指标列表不得重新引入 workspace 过滤入口"

    def test_list_metrics_sql_carries_no_workspace_predicate(self, scoped_conn):
        # 带 search 让 WHERE 子句真实存在, 否则“无过滤”会真空通过
        ms.list_metrics(size=100, search="GMV")
        sql, params = [c for c in scoped_conn.cur.calls
                       if "FROM adh_metrics" in c[0] and "COUNT(*)" not in c[0]][0]
        assert "WHERE" in sql and "name LIKE" in sql
        # workspace_id 只允许作为 SELECT 列清单出现, 不得进 WHERE 谓词
        assert "workspace_id =" not in sql.split("WHERE", 1)[1]
        assert 301 not in params

    def test_model_scope_still_unaffected_by_workspace(self, scoped_conn):
        """模型作用域只看 bound_object_key 对象集, 与 workspace 无关。"""
        ms.list_metrics(size=100, model_id=7, scope="model")
        sql, _ = [c for c in scoped_conn.cur.calls
                  if "FROM adh_metrics" in c[0] and "COUNT(*)" not in c[0]][0]
        assert "bound_object_key IN" in sql
        assert "workspace_id =" not in sql.split("WHERE", 1)[1]

    def test_duplicate_check_is_global(self, monkeypatch):
        """同名指标跨空间也算重复: 语义层按 name 解析, 两条同名=口径歧义。"""
        conn = _Conn([("FROM adh_metrics", [{"id": 1}])])
        monkeypatch.setattr(ms, "DBConnection", lambda: conn)
        with pytest.raises(ValueError, match="already exists"):
            ms.create_metric({"display_name": "GMV", "workspace_id": 301})
        dup_sql = [c for c in conn.cur.calls if "FROM adh_metrics" in c[0]][0]
        assert "workspace_id" not in dup_sql[0]
        assert 301 not in dup_sql[1]

    def test_created_row_is_written_as_global_dict_entry(self, monkeypatch):
        """新建指标恒落 workspace_id=0, 不从调用方(UI/AS-BOT 会话)继承工作空间。"""
        conn = _Conn([("MAX(id)", [{"nid": 99}]), ("FROM adh_metrics", [])])
        monkeypatch.setattr(ms, "DBConnection", lambda: conn)
        out = ms.create_metric({
            "display_name": "案例数量", "name": "case_count",
            "calculation_type": "count", "expression": "COUNT(pc_case.id)",
            "target_table": "pc_case", "workspace_id": 301,   # 应被忽略
        })
        assert out == {"id": 99, "success": True}
        ins = [c for c in conn.cur.calls if c[0].startswith("INSERT INTO adh_metrics")]
        assert ins, "应执行插入"
        assert ins[0][1][1] == 0, "workspace_id 列必须落 0(全局字典行)"


class TestAssetPoolDelete:
    """未归属资产池删除: delete_dimension 连同关系行一并清掉(与 delete_metric 同口径)。"""

    def test_delete_dimension_removes_links_and_row(self, monkeypatch):
        conn = _Conn([("FROM adh_dimensions WHERE id", [{"id": 5}])])
        monkeypatch.setattr(ms, "DBConnection", lambda: conn)
        assert ms.delete_dimension(5) is True
        deletes = [(s, p) for s, p in conn.cur.calls if s.startswith("DELETE")]
        assert ("DELETE FROM adh_metric_dimensions WHERE dimension_id = %s", [5]) in deletes
        assert ("DELETE FROM adh_dimensions WHERE id = %s", [5]) in deletes

    def test_delete_dimension_missing_returns_false_without_deleting(self, monkeypatch):
        conn = _Conn([])
        monkeypatch.setattr(ms, "DBConnection", lambda: conn)
        assert ms.delete_dimension(999) is False
        assert not any(s.startswith("DELETE") for s, _ in conn.cur.calls)

    def test_delete_dimension_route_registered(self):
        from backend.modules.catalog.api import metrics as metrics_api
        routes = {(r.path, tuple(sorted(r.methods))) for r in metrics_api.router.routes}
        assert ("/dimensions/{dim_id}", ("DELETE",)) in routes

    def test_dimensions_share_scope(self, monkeypatch):
        conn = _Conn([("FROM adh_ontology_objects", OBJ_ROWS),
                      ("FROM adh_dimensions", [{"name": "案例状态", "bound_object_key": "case"}])])
        monkeypatch.setattr(ms, "DBConnection", lambda: conn)
        out = ms.list_all_dimensions(model_id=7, scope="model")
        assert out["total"] == 1
        dims_sql = [c for c in conn.cur.calls if "FROM adh_dimensions" in c[0]][0]
        assert "bound_object_key IN" in dims_sql[0]


class TestAssetPool:
    def test_pool_classifies_and_counts(self, monkeypatch):
        script = [
            ("FROM adh_metrics", [{"name": "退号数", "bound_object_key": "", "pool_reason": "unbound"}]),
            ("FROM adh_dimensions", [{"name": "旧维度", "bound_object_key": "ward", "pool_reason": "orphan"}]),
            ("FROM adh_business_terms", [{"term_cn": "活跃用户", "target_table": ""}]),
        ]  # 候选表未迁移 → 查询异常路径
        conn = _Conn(script)
        monkeypatch.setattr(ms, "DBConnection", lambda: conn)
        out = ms.asset_pool()
        assert out["counts"]["metrics"] == 1 and out["counts"]["dimensions"] == 1
        assert out["counts"]["terms"] == 1
        assert out["counts"]["total"] == 3   # suggestions 容错为空
        assert out["metrics"][0]["pool_reason"] == "unbound"
        msql = [c for c in conn.cur.calls if "FROM adh_metrics"][0][0]
        assert "NOT IN" in msql and "adh_ontology_objects" in msql
        # 候选必须走本函数自己的 DBConnection，不能委托给另开连接的 service，
        # 否则下面这个"表未迁移"容错用例会在真库上跑、永远测不到异常分支
        assert any("adh_evolution_candidates" in c[0] for c in conn.cur.calls), \
            "资产池未在本连接上查候选表"

    def test_suggestions_table_missing_is_tolerated(self, monkeypatch):
        class _Boom(_Conn):
            def cursor(self):
                c = super().cursor()
                orig = c.execute
                def boom(sql, params=None):
                    # 自进化候选表（旧名 adh_alias_suggestions 已退役为冻结表）
                    if "adh_evolution_candidates" in sql:
                        raise RuntimeError("table doesn't exist")
                    return orig(sql, params)
                c.execute = boom
                return c
        conn = _Boom([("FROM adh_metrics", []), ("FROM adh_dimensions", []),
                      ("FROM adh_business_terms", [])])
        monkeypatch.setattr(ms, "DBConnection", lambda: conn)
        out = ms.asset_pool()   # 不抛异常
        assert out["suggestions"] == []
