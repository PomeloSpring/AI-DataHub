"""数据护城河统一强制 —— 离线单测(不触碰真实数据库)。

对应 data-moat 计划的封堵点:
  S1  Playground 不再充当 SQL 客户端: 语义层/datamind 路由无返回数据行的 /execute;
      datamind 裸执行降级路径 _execute_local 已删除。
  S2  datamind 死端点 /api/query(execute/export) 已删除, 主服务不再挂载。
  S3  dataviz 取数强制过护城河: 无可信身份 -> fail-closed; 有身份 -> 经统一治理
      执行器(execute_query_with_permission); 身份取自服务端而非请求体。
  S4  签名内部身份头透传: 语义层只信可信头, 忽略 body.user_id; 签名可校验防篡改。

全部用 monkeypatch 注入假数据/假执行器, 不落真库、不真连数据源。
"""

import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

import services.shared.common.auth as auth_mod
from services.dataviz.api import chart as chart_api
from services.dataviz.services import report_service as report_svc
from services.dataviz.services.governed_query import governed_execute, NoIdentityError

# services.dataviz.services 包把同名子模块属性重绑为实例, 故显式取真模块对象
import importlib as _importlib
dash_svc = _importlib.import_module("services.dataviz.services.dashboard_service")


def _route_paths(router):
    return [getattr(r, "path", "") for r in router.routes]


def _fake_db(chart_row):
    """伪造 chart.py 里 `with DBConnection() as conn: with conn.cursor() as cur:` 的嵌套上下文。"""

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return None

        def fetchone(self):
            return chart_row

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _Cur()

    return lambda: _Conn()


def _raw_chart_row():
    return {
        "id": 1, "dashboard_id": 1, "workspace_id": 9,
        "sql_query": "SELECT * FROM t_user",
        "config": json.dumps({"datasource_id": 7}),
        "query_source": "raw_sql", "semantic_query": None,
    }


# ── S1: Playground 取数受治理(继承用户角色权限, 非删除/非裸执行) ──────

class TestPlaygroundGovernedExecution:
    def test_datamind_no_bare_executor(self):
        # 裸连数据源降级路径(DB 工具后门)必须不存在; 取数只能走护城河
        from services.datamind.api import playground as dm_pg

        assert not hasattr(dm_pg, "_execute_local")
        assert not hasattr(dm_pg, "_get_datasource_conn")

    def test_datamind_execute_is_governed_and_trusts_jwt_identity(self, monkeypatch):
        import pandas as pd
        from services.datamind.api import playground as dm_pg
        import services.datamind.nl2sql.sql.query_executor as qe

        captured = {}

        def fake_perm(sql, datasource_id=None, query_type="sql",
                      user_context=None, workspace_id=0, database=""):
            captured["user_id"] = (user_context or {}).get("user_id")
            captured["datasource_id"] = datasource_id
            captured["workspace_id"] = workspace_id
            # 模拟护城河已按当前身份剔除敏感 block 列 phone
            df = pd.DataFrame([{"id": 1, "name": "a", "phone": "13800000000"}])
            return df.drop(columns=["phone"]), 3, 1

        monkeypatch.setattr(qe, "execute_query_with_permission", fake_perm)
        # body 伪造 user_id=999 不应被采信; 实际身份取自 JWT(user 参数)
        out = dm_pg.execute_via_playground(
            {"sql": "SELECT * FROM t_user", "datasource_id": 7, "user_id": 999},
            user={"user_id": 42, "username": "bob"}, workspace_id=5)

        assert captured["user_id"] == 42
        assert captured["datasource_id"] == 7
        assert captured["workspace_id"] == 5
        assert "phone" not in out["columns"]  # 护城河(block)对 Playground 生效
        assert out["rows"] and "phone" not in out["rows"][0]

    def test_datamind_execute_fail_closed_no_identity(self):
        from fastapi import HTTPException
        from services.datamind.api import playground as dm_pg

        # 无可信身份(JWT 解析不到 user) -> 4xx, 绝不降级为不过滤执行(I5)
        with pytest.raises(HTTPException) as ei:
            dm_pg.execute_via_playground(
                {"sql": "SELECT * FROM t_user", "datasource_id": 7, "user_id": 999},
                user={"user_id": 0}, workspace_id=0)
        assert ei.value.status_code == 403

    def test_semantic_playground_has_no_data_execute_route(self):
        # 语义层只留静态/预览(不返回数据行); 取数在 datamind 侧经护城河执行
        from services.semanticservice.api import playground as sem_pg

        paths = _route_paths(sem_pg.router)
        assert not any(p.rstrip("/").endswith("/execute") for p in paths), paths
        assert not any(p.rstrip("/").endswith("/explain") for p in paths), paths
        assert any(p.endswith("/rls-diff") for p in paths)


# ── S2: datamind /api/query 死端点已删 ─────────────────────────────────

class TestQueryEndpointRemoved:
    def test_query_module_gone(self):
        assert importlib.util.find_spec("services.datamind.api.query") is None

    def test_main_does_not_mount_query(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(root, "services", "datamind", "main.py"), encoding="utf-8") as f:
            src = f.read()
        assert "/api/query" not in src
        assert "api.query" not in src


# ── S3: dataviz 取数强制过护城河 ───────────────────────────────────────

class TestGovernedExecutor:
    def test_no_identity_fail_closed(self):
        # I5: 无可信身份一律拒绝, 不回退裸执行(在触达执行器前即抛错)
        with pytest.raises(NoIdentityError):
            governed_execute("SELECT * FROM t_user", 7, user_id=0, workspace_id=0)

    def test_identity_passed_to_moat_primitive(self, monkeypatch):
        captured = {}

        def fake_exec(sql, datasource_id=None, query_type="sql",
                      user_context=None, workspace_id=0, database=""):
            captured["user_context"] = user_context
            captured["workspace_id"] = workspace_id
            captured["datasource_id"] = datasource_id
            df = pd.DataFrame([{"id": 1, "name": "a", "phone": "13800000000"}])
            # 模拟护城河已剔除 block 列 phone
            df = df.drop(columns=["phone"])
            return df, 3, 1

        import services.datamind.nl2sql.sql.query_executor as qe

        monkeypatch.setattr(qe, "execute_query_with_permission", fake_exec)
        res = governed_execute("SELECT * FROM t_user", 7, user_id=42,
                               workspace_id=5, username="bob")
        # 身份作为 user_context 传入治理原语(而非裸执行)
        assert captured["user_context"]["user_id"] == 42
        assert captured["workspace_id"] == 5
        assert captured["datasource_id"] == 7
        assert res["columns"] == ["id", "name"]
        assert "phone" not in res["columns"]  # block 列被剔除
        assert res["rows"] and "phone" not in res["rows"][0]


class TestChartRefresh:
    """chart.refresh_chart: 身份取自 JWT 注入的 user, 无身份 -> 403。"""

    def test_no_bare_executor_left(self):
        assert not hasattr(chart_api, "_execute_on_datasource")

    def test_identity_from_server_not_body(self, monkeypatch):
        captured = {}

        def fake_governed(sql, datasource_id, user_id, workspace_id, username=""):
            captured.update(datasource_id=datasource_id, user_id=user_id,
                            workspace_id=workspace_id)
            return {"columns": ["id"], "rows": [{"id": 1}], "row_count": 1}

        monkeypatch.setattr(chart_api, "DBConnection", _fake_db(_raw_chart_row()))
        monkeypatch.setattr(chart_api, "governed_execute", fake_governed)

        req = chart_api.ChartRefreshRequest()
        # 身份只来自注入的 user(服务端 JWT); 伪造 body 无 user_id 字段可用
        result = chart_api.refresh_chart(
            chart_id=1, req=req, user={"user_id": 42, "username": "bob"})

        assert captured["user_id"] == 42
        assert captured["datasource_id"] == 7
        assert captured["workspace_id"] == 9  # 图表所属工作区
        assert result["rows"] == [{"id": 1}]

    def test_no_identity_maps_to_403(self, monkeypatch):
        from fastapi import HTTPException

        def raise_no_identity(*a, **k):
            raise NoIdentityError("no identity")

        monkeypatch.setattr(chart_api, "DBConnection", _fake_db(_raw_chart_row()))
        monkeypatch.setattr(chart_api, "governed_execute", raise_no_identity)

        with pytest.raises(HTTPException) as ei:
            chart_api.refresh_chart(
                chart_id=1, req=chart_api.ChartRefreshRequest(),
                user={"user_id": 0, "username": ""})
        assert ei.value.status_code == 403


class TestDashboardRefresh:
    def test_no_bare_executor_left(self):
        assert not hasattr(dash_svc, "_execute_on_datasource")

    def test_refresh_all_no_identity_fail_closed(self):
        # 无可信身份 -> 在开库前即整体拒绝
        with pytest.raises(NoIdentityError):
            dash_svc.chart_service.refresh_all_charts(1, params={}, user_id=0)


class TestReportCreatorIdentity:
    def test_uses_creator_as_identity(self, monkeypatch):
        captured = {}

        def fake_governed(sql, datasource_id, user_id, workspace_id, username=""):
            captured.update(user_id=user_id, workspace_id=workspace_id)
            return {"columns": ["c"], "rows": [{"c": 1}], "row_count": 1}

        monkeypatch.setattr(report_svc, "governed_execute", fake_governed)
        out = report_svc._execute_data_query("SELECT c FROM t", owner_id=7, workspace_id=3)
        assert captured["user_id"] == 7  # 以创建者身份走护城河
        assert captured["workspace_id"] == 3
        assert out["rows"] == [{"c": 1}]

    def test_no_creator_fail_closed(self):
        # owner_id=0 -> 治理执行器 fail-closed, 且不返回任何数据行
        out = report_svc._execute_data_query("SELECT * FROM t_user", owner_id=0)
        assert out.get("rows", []) == []
        assert out.get("error")


# ── S4: 签名内部身份头 + 语义层只信可信头 ──────────────────────────────

class TestInternalIdentityHeader:
    def test_sign_verify_roundtrip(self, monkeypatch):
        monkeypatch.setattr(auth_mod, "ADH_SECRET_KEY", "unit-test-secret")
        val = auth_mod.sign_internal_identity(42, 3)
        assert auth_mod.verify_internal_identity(val) == {"user_id": 42, "workspace_id": 3}

    def test_tampered_or_missing_rejected(self, monkeypatch):
        monkeypatch.setattr(auth_mod, "ADH_SECRET_KEY", "unit-test-secret")
        val = auth_mod.sign_internal_identity(42, 3)
        uid, ws, sig = val.split(":")
        assert auth_mod.verify_internal_identity(f"99:{ws}:{sig}") is None  # 篡改 uid
        assert auth_mod.verify_internal_identity("") is None                 # 缺失

    def test_semantic_rls_diff_ignores_body_user_id(self, monkeypatch):
        from services.semanticservice.api import playground as sem_pg

        monkeypatch.setattr(auth_mod, "ADH_SECRET_KEY", "unit-test-secret")
        monkeypatch.setattr(sem_pg, "_sqlglot_or_503", lambda: None)
        captured = {}

        def fake_resolve(sql, ds_id, user_id, workspace_id, *a, **k):
            captured["user_id"] = user_id
            captured["workspace_id"] = workspace_id
            return sql, [], [], None

        monkeypatch.setattr(sem_pg, "_resolve_rls", fake_resolve)

        class _Req:
            headers = {"X-Internal-Identity": auth_mod.sign_internal_identity(42, 3)}

        # body 伪造 user_id=999 / workspace_id=888, 应被忽略
        sem_pg.rls_diff(
            {"sql": "SELECT id FROM t_user", "user_id": 999, "workspace_id": 888}, _Req())
        assert captured["user_id"] == 42
        assert captured["workspace_id"] == 3

    def test_datamind_forwards_signed_identity_and_strips_body(self, monkeypatch):
        from services.datamind.api import playground as dm_pg

        monkeypatch.setattr(auth_mod, "ADH_SECRET_KEY", "unit-test-secret")
        captured = {}

        def fake_proxy(path, body, headers=None):
            captured["path"] = path
            captured["body"] = body
            captured["headers"] = headers or {}
            return {"ok": True}

        monkeypatch.setattr(dm_pg, "_proxy_or_502", fake_proxy)
        dm_pg.rls_diff_via_playground(
            {"sql": "SELECT id FROM t_user", "user_id": 999, "workspace_id": 5},
            user={"user_id": 42},
        )
        # 转发的是 body 剥离 user_id/workspace_id 后的版本
        assert "user_id" not in captured["body"]
        assert "workspace_id" not in captured["body"]
        ident = auth_mod.verify_internal_identity(captured["headers"]["X-Internal-Identity"])
        assert ident and ident["user_id"] == 42


class TestJoinGovernedExecution:
    """关联查询(t1.*, t2.*)回归: 重复列名不得崩溃, RLS 注入不得破坏别名/旁路 JOIN 表。"""

    def test_df_to_columns_rows_disambiguates_duplicate_columns(self):
        """JOIN 产生的重名列应被无损消歧(全部列保留且可按列名寻址)。"""
        from services.shared.common.df_serialize import df_to_columns_rows
        import pandas as _pd
        df = _pd.DataFrame([[1, "a", 2, "b"]], columns=["id", "name", "id", "name"])
        columns, rows = df_to_columns_rows(df)
        assert len(columns) == len(set(columns)) == 4  # 唯一
        assert columns[0] == "id" and columns[2] == "id__1"  # 第二个重名列消歧
        assert list(rows[0].keys()) == columns
        assert rows[0]["id"] == 1 and rows[0]["id__1"] == 2  # 两列值都保留

    def test_df_to_columns_rows_unique_columns_untouched(self):
        """本就唯一的列名不应被改写。"""
        from services.shared.common.df_serialize import df_to_columns_rows
        import pandas as _pd
        columns, rows = df_to_columns_rows(_pd.DataFrame([[1, 2]], columns=["a", "b"]))
        assert columns == ["a", "b"]
        assert rows == [{"a": 1, "b": 2}]

    def test_inject_row_filter_preserves_alias(self):
        """带别名的 FROM 表被包裹后保留别名(不出现非法的 `AS 表名 别名`)。"""
        from services.datamind.permission.enforcer import permission_enforcer as enf
        sql = "select t1.* from t_user_customer t1 limit 100"
        out = enf._inject_row_filter(sql, "t_user_customer", "region = 'cn'")
        assert "AS t1" in out
        assert "AS t_user_customer t1" not in out  # 旧实现的非法双重别名
        assert "t1." in out  # 外部引用别名仍成立

    def test_inject_row_filter_covers_join_table(self):
        """JOIN 侧表也需被包裹, 否则该行级策略会被旁路。"""
        from services.datamind.permission.enforcer import permission_enforcer as enf
        sql = ("select t1.*, t2.* from t_user_customer t1 "
               "left join t_user_company_relation t2 on t1.account_code = t2.account_code limit 100")
        out = enf._inject_row_filter(sql, "t_user_company_relation", "dept = 'x'")
        assert "(SELECT * FROM t_user_company_relation WHERE dept = 'x') AS t2" in out
        assert "AS t2" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
