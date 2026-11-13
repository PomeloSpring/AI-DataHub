"""数据护城河统一强制 —— 离线单测(不触碰真实数据库)。

对应 data-moat 计划的封堵点:
  S1  Playground 不再充当 SQL 客户端: 语义层/datamind 路由无返回数据行的 /execute;
      datamind 裸执行降级路径 _execute_local 已删除。
  S2  datamind 死端点 /api/query(execute/export) 已删除, 主服务不再挂载。
  S3  dataviz 取数强制过护城河: 无可信身份 -> fail-closed; 有身份 -> 经统一治理
      执行器(execute_query_with_permission); 身份取自服务端而非请求体。
  S4  签名内部身份头透传: 语义层只信可信头, 忽略 body.user_id; 签名可校验防篡改。
  S5  审计行主键不得同毫秒碰撞: 毫秒时间戳强占主键会让第二条写 duplicate-key 并被
      `except` 吞成 warning（= 静默丢审计），auto_increment 的表不自己写 id。

全部用 monkeypatch 注入假数据/假执行器, 不落真库、不真连数据源。
"""

import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

import backend.common.auth as auth_mod
from backend.modules.viz.api import chart as chart_api
from backend.modules.viz.services import report_service as report_svc
from backend.core.governed_query import governed_execute, NoIdentityError

# backend.modules.viz.services 包把同名子模块属性重绑为实例, 故显式取真模块对象
import importlib as _importlib
dash_svc = _importlib.import_module("backend.modules.viz.services.dashboard_service")


def _route_paths(router):
    return [getattr(r, "path", "") for r in router.routes]


def _chart_row():
    """`/api/charts/{id}/refresh` 预查行：只回图所属看板（不回数据行）。"""
    return {"dashboard_id": 1}


# ── S1: Playground 取数受治理(继承用户角色权限, 非删除/非裸执行) ──────

class TestPlaygroundGovernedExecution:
    def test_datamind_no_bare_executor(self):
        # 裸连数据源降级路径(DB 工具后门)必须不存在; 取数只能走护城河
        from backend.modules.mind.api import playground as dm_pg

        assert not hasattr(dm_pg, "_execute_local")
        assert not hasattr(dm_pg, "_get_datasource_conn")

    def test_datamind_execute_is_governed_and_trusts_jwt_identity(self, monkeypatch):
        import pandas as pd
        from backend.modules.mind.api import playground as dm_pg
        import backend.core.query_executor as qe

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
        from backend.modules.mind.api import playground as dm_pg

        # 无可信身份(JWT 解析不到 user) -> 4xx, 绝不降级为不过滤执行(I5)
        with pytest.raises(HTTPException) as ei:
            dm_pg.execute_via_playground(
                {"sql": "SELECT * FROM t_user", "datasource_id": 7, "user_id": 999},
                user={"user_id": 0}, workspace_id=0)
        assert ei.value.status_code == 403

    def test_dataset_placeholders_are_named_not_masked_as_syntax_error(self):
        """数据集正文粘进 Playground：`${var}` 不会被渲染，必须报出真因与下一步。

        旧行为是把未替换的占位符直接下发给引擎（ES 回 `mismatched input '$'`），文案只剩
        “请检查查询是否受支持”——用户看不出是入参没替换，会以为平台不支持这条查询。
        报错要带真因与可执行下一步（no-silent-degradation）。
        """
        from fastapi import HTTPException
        from backend.modules.mind.api import playground as dm_pg

        sql = ('SELECT COUNT(*) AS c FROM "t_software_app_operation_log" '
               "WHERE ${start_time} = '' OR \"evenTime.keyword\" >= ${start_time} "
               'AND "appCode.keyword" = ${app_code} LIMIT 10')
        with pytest.raises(HTTPException) as ei:
            dm_pg.execute_via_playground({"sql": sql, "datasource_id": 900},
                                         user={"user_id": 42, "username": "admin"},
                                         workspace_id=1)
        detail = str(ei.value.detail)
        assert ei.value.status_code == 400
        assert "${start_time}" in detail and "${app_code}" in detail
        assert "预览取数" in detail          # 给出下一步，而不是“检查是否受支持”
        # 占位符未渲染时不得把 SQL 下发引擎（不白跑一次、不落一条误导向的审计）

    def test_semantic_playground_has_no_data_execute_route(self):
        # 语义层只留静态/预览(不返回数据行); 取数在 datamind 侧经护城河执行
        from backend.modules.semhub.api import playground as sem_pg

        paths = _route_paths(sem_pg.router)
        assert not any(p.rstrip("/").endswith("/execute") for p in paths), paths
        assert not any(p.rstrip("/").endswith("/explain") for p in paths), paths
        assert any(p.endswith("/rls-diff") for p in paths)


# ── S2: datamind /api/query 死端点已删 ─────────────────────────────────

class TestSingleSqlExit:
    """取数口唯一（护栏 §1）：Playground / MCP / 数据集 / 看板 / Chat 必须同一壳。

    用户口径：“不能数据集一套查询代码、语义查数一套、看板一套、Playground 又一套；
    Playground 可以多功能，但最后取数应当是同一个。”本类钉两件事：
    1) 行为：两个入口都真的经 `semantics.execute.execute_sql`（SQL 形状唯一出口）；
    2) 源码：不得再直调 `execute_query_with_permission` 或自己拼序列化/清洗（第三条壳的成因）。
    """

    @staticmethod
    def _fake_result():
        from types import SimpleNamespace

        return SimpleNamespace(
            columns=[SimpleNamespace(name="id"), SimpleNamespace(name="name")],
            rows=[[1, "a"], [2, "b"]], row_count=2, elapsed_ms=7)

    def test_playground_经唯一出口(self, monkeypatch):
        from backend.modules.mind.api import playground as dm_pg
        from backend.semantics import execute as sem_execute

        seen = {}

        def spy(sql, ctx=None):
            seen["sql"], seen["ctx"] = sql, ctx
            return self._fake_result()

        monkeypatch.setattr(sem_execute, "execute_sql", spy)
        out = dm_pg.execute_via_playground(
            {"sql": "SELECT id, name FROM t_user LIMIT 10", "datasource_id": 7},
            user={"user_id": 42, "username": "bob"}, workspace_id=5)
        assert seen["ctx"].user_id == 42 and seen["ctx"].workspace_id == 5
        assert seen["ctx"].datasource_id == 7      # 身份与源都取自服务端，不从请求体信任
        assert out["columns"] == ["id", "name"]
        assert out["rows"] == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        assert out["row_count"] == 2 and out["elapsed_ms"] == 7

    def test_playground_不再自带第二套壳(self):
        """源码级：不得直调 `execute_query_with_permission`，也不得自己 `df_to_columns_rows`。"""
        import inspect

        from backend.modules.mind.api import playground as dm_pg

        src = inspect.getsource(dm_pg.execute_via_playground)
        assert "governed_execute(" in src
        assert "execute_query_with_permission(" not in src
        assert "df_to_columns_rows" not in src

    def test_mcp_execute_sql_经唯一出口并显式标截断(self, monkeypatch):
        import asyncio
        import json as _json

        from backend.processes.mind import mcp_server
        from backend.semantics import execute as sem_execute

        seen = {}

        def spy(sql, ctx=None):
            seen["ctx"] = ctx
            return self._fake_result()

        monkeypatch.setattr(sem_execute, "execute_sql", spy)
        payload = _json.loads(asyncio.run(
            mcp_server.handle_execute_sql({"sql": "SELECT id, name FROM t LIMIT 10",
                                            "datasource_id": 7})))
        assert seen["ctx"].user_id == 0                 # 系统身份：只套敏感基线（护栏 §2）
        assert seen["ctx"].datasource_id == 7
        assert payload["columns"] == ["id", "name"]
        assert payload["row_count"] == 2 and payload["truncated"] is False

    def test_mcp_不再手写序列化与清洗(self):
        import inspect

        from backend.processes.mind import mcp_server

        src = inspect.getsource(mcp_server.handle_execute_sql)
        assert "execute_sql(" in src
        assert "execute_query_with_permission(" not in src
        # 重名列必须走出口里的无损消歧，不得拿 pandas 默认行为充数（护栏 §8）
        assert "to_dict(orient=" not in src


class TestQueryEndpointRemoved:
    def test_query_module_gone(self):
        assert importlib.util.find_spec("backend.modules.mind.api.query") is None

    def test_main_does_not_mount_query(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        # 唯一 web 入口为 backend/app/main.py（旧 8 壳已删）
        with open(os.path.join(root, "backend", "app", "main.py"), encoding="utf-8") as f:
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

        import backend.core.query_executor as qe

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
    """`/api/charts/{id}/refresh` 是服务层薄委托：身份取自 JWT、无身份/越权 -> 403。

    2026-11-06 取数通道收口后本模块不再自己执行 SQL（旧实现是第二口：直接跑
    `adh_charts.sql_query`、接受前端递交 `count_sql`、且不校验看板可见性），
    因此断言收三件事：**无第二执行口**（源码级）+ **身份只从服务端注入** + **先鉴权后取数**。
    """

    def test_no_bare_executor_left(self):
        import inspect

        src = inspect.getsource(chart_api).replace(chart_api.__doc__ or "", "")
        assert not hasattr(chart_api, "_execute_on_datasource")
        # 不自己开连接、不自己治理执行（取数一律下沉服务层）
        for banned in ("DBConnection", "governed_execute", "execute_query_with_permission"):
            assert banned not in src, f"chart.py 不得再现 {banned}（第二取数通道）"
        # 请求体不接受任何口径/统计语句字段（旧 count_sql = 前端递交裸 SQL 的第二口）；
        # 分页只认 page_size/page_num 两个业务参数，offset 不得成为对外契约（由服务端算）
        assert set(chart_api.ChartRefreshRequest.model_fields) == {"params", "page_size", "page_num"}

    def _stub(self, monkeypatch, *, visible=True, captured=None, boom=None):
        monkeypatch.setattr(chart_api, "execute_query",
                            lambda sql, params=None, **kw: dict(_chart_row()))
        monkeypatch.setattr(chart_api, "check_dashboard_visible", lambda dash_id, uid: visible)

        def fake_refresh(dashboard_id, chart_id, **kw):
            if captured is not None:
                captured.update(dashboard_id=dashboard_id, chart_id=chart_id, **kw)
            if boom is not None:
                raise boom
            return {"columns": ["id"], "rows": [{"id": 1}]}

        monkeypatch.setattr(chart_api.chart_service, "refresh_chart", fake_refresh)

    def test_identity_from_server_not_body(self, monkeypatch):
        captured = {}
        self._stub(monkeypatch, captured=captured)
        req = chart_api.ChartRefreshRequest()
        # 身份只来自注入的 user（服务端 JWT）；伪造 body 无 user_id 字段可用
        result = chart_api.refresh_chart(
            chart_id=1, req=req, user={"user_id": 42, "username": "bob"}, workspace_id=9)
        assert captured["user_id"] == 42 and captured["username"] == "bob"
        assert captured["workspace_id"] == 9 and captured["dashboard_id"] == 1
        assert result["rows"] == [{"id": 1}]

    def test_invisible_dashboard_rejected_before_query(self, monkeypatch):
        from fastapi import HTTPException

        called = {}
        self._stub(monkeypatch, visible=False, captured=called)
        with pytest.raises(HTTPException) as ei:
            chart_api.refresh_chart(chart_id=1, req=chart_api.ChartRefreshRequest(),
                                    user={"user_id": 42, "username": "bob"}, workspace_id=9)
        assert ei.value.status_code == 403
        assert called == {}                        # 不可见 = 一行业务数据都不取

    def test_no_identity_maps_to_403(self, monkeypatch):
        from fastapi import HTTPException

        self._stub(monkeypatch, boom=NoIdentityError("no identity"))
        with pytest.raises(HTTPException) as ei:
            chart_api.refresh_chart(chart_id=1, req=chart_api.ChartRefreshRequest(),
                                    user={"user_id": 0, "username": ""}, workspace_id=9)
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
        from backend.modules.semhub.api import playground as sem_pg

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
        from backend.modules.mind.api import playground as dm_pg

        monkeypatch.setattr(auth_mod, "ADH_SECRET_KEY", "unit-test-secret")
        captured = {}

        def fake_proxy(path, body, headers=None):
            captured["path"] = path
            captured["body"] = body
            captured["headers"] = headers or {}
            return {"ok": True}

        monkeypatch.setattr(dm_pg, "_proxy_or_502", fake_proxy)
        dm_pg.rls_diff_via_playground(
            {"sql": "SELECT id FROM t_user", "user_id": 999, "workspace_id": 888},
            user={"user_id": 42}, workspace_id=5,
        )
        # 转发的是 body 剥离 user_id/workspace_id 后的版本(身份不从 body 信任)
        assert "user_id" not in captured["body"]
        assert "workspace_id" not in captured["body"]
        ident = auth_mod.verify_internal_identity(captured["headers"]["X-Internal-Identity"])
        assert ident and ident["user_id"] == 42 and ident["workspace_id"] == 5

    def test_all_analysis_proxies_carry_signed_identity(self, monkeypatch):
        """AST/血缘/溯源 与 rls-diff 同口径: 代理必须签发可信内部头并剥身份字段,
        否则语义层 _INTERNAL_ROUTES 直接 401"缺少可信身份"。"""
        from backend.modules.mind.api import playground as dm_pg

        monkeypatch.setattr(auth_mod, "ADH_SECRET_KEY", "unit-test-secret")
        captured = {}

        def fake_proxy(path, body, headers=None):
            captured["path"] = path
            captured["body"] = body
            captured["headers"] = headers or {}
            return {"ok": True}

        monkeypatch.setattr(dm_pg, "_proxy_or_502", fake_proxy)
        for fn, path in (
            (dm_pg.ast_via_playground, "/ast"),
            (dm_pg.lineage_via_playground, "/lineage"),
            (dm_pg.provenance_via_playground, "/provenance"),
            (dm_pg.rls_diff_via_playground, "/rls-diff"),
        ):
            captured.clear()
            fn({"sql": "SELECT id FROM t_user", "user_id": 999, "workspace_id": 888},
               user={"user_id": 42}, workspace_id=5)
            assert captured["path"] == path
            assert "user_id" not in captured["body"] and "workspace_id" not in captured["body"]
            ident = auth_mod.verify_internal_identity(captured["headers"].get("X-Internal-Identity", ""))
            assert ident and ident["user_id"] == 42 and ident["workspace_id"] == 5

    def test_analysis_identity_falls_back_to_default_workspace(self, monkeypatch):
        """未显式指定 workspace 时解析用户默认工作空间, 严禁签 0
        (0 会被语义层中间件 authorize_workspace 显式拒绝)。"""
        from backend.modules.mind.api import playground as dm_pg

        monkeypatch.setattr(auth_mod, "ADH_SECRET_KEY", "unit-test-secret")
        monkeypatch.setattr(dm_pg, "resolve_user_default_workspace_id", lambda uid: 12)
        captured = {}

        def fake_proxy(path, body, headers=None):
            captured["headers"] = headers or {}
            return {"ok": True}

        monkeypatch.setattr(dm_pg, "_proxy_or_502", fake_proxy)
        dm_pg.ast_via_playground({"sql": "SELECT 1"}, user={"user_id": 42}, workspace_id=0)
        ident = auth_mod.verify_internal_identity(captured["headers"].get("X-Internal-Identity", ""))
        assert ident and ident["workspace_id"] == 12


class TestJoinGovernedExecution:
    """关联查询(t1.*, t2.*)回归: 重复列名不得崩溃, RLS 注入不得破坏别名/旁路 JOIN 表。"""

    def test_df_to_columns_rows_disambiguates_duplicate_columns(self):
        """JOIN 产生的重名列应被无损消歧(全部列保留且可按列名寻址)。"""
        from backend.common.df_serialize import df_to_columns_rows
        import pandas as _pd
        df = _pd.DataFrame([[1, "a", 2, "b"]], columns=["id", "name", "id", "name"])
        columns, rows = df_to_columns_rows(df)
        assert len(columns) == len(set(columns)) == 4  # 唯一
        assert columns[0] == "id" and columns[2] == "id__1"  # 第二个重名列消歧
        assert list(rows[0].keys()) == columns
        assert rows[0]["id"] == 1 and rows[0]["id__1"] == 2  # 两列值都保留

    def test_df_to_columns_rows_unique_columns_untouched(self):
        """本就唯一的列名不应被改写。"""
        from backend.common.df_serialize import df_to_columns_rows
        import pandas as _pd
        columns, rows = df_to_columns_rows(_pd.DataFrame([[1, 2]], columns=["a", "b"]))
        assert columns == ["a", "b"]
        assert rows == [{"a": 1, "b": 2}]

    def test_inject_row_filter_preserves_alias(self):
        """带别名的 FROM 表被包裹后保留别名(不出现非法的 `AS 表名 别名`)。"""
        from backend.core.enforcer import permission_enforcer as enf
        sql = "select t1.* from t_user_customer t1 limit 100"
        out = enf._inject_row_filter(sql, "t_user_customer", "region = 'cn'")
        assert "AS t1" in out
        assert "AS t_user_customer t1" not in out  # 旧实现的非法双重别名
        assert "t1." in out  # 外部引用别名仍成立

    def test_inject_row_filter_covers_join_table(self):
        """JOIN 侧表也需被包裹, 否则该行级策略会被旁路。"""
        from backend.core.enforcer import permission_enforcer as enf
        sql = ("select t1.*, t2.* from t_user_customer t1 "
               "left join t_user_company_relation t2 on t1.account_code = t2.account_code limit 100")
        out = enf._inject_row_filter(sql, "t_user_company_relation", "dept = 'x'")
        assert "(SELECT * FROM t_user_company_relation WHERE dept = 'x') AS t2" in out
        assert "AS t2" in out


class TestAuditRowIdentity:
    """护栏 §9：成功与拒绝均落审计 —— 前提是审计行真能写进去。

    历史缺陷（2026-11-13 实测）：三处审计写入用 `int(time.time() * 1000)` 当主键，
    同毫秒的两次写（整板逐图刷新就是典型）必碰 duplicate-key，而写入均 `except`
    成 warning —— 审计行静默丢，比没落账更难查（看上去一一切正常）。
    """

    @staticmethod
    def _fake_conn(sink: list):
        """假连接：把 (sql, params) 收到 sink 里，不碰真库。"""

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                sink.append((" ".join(str(sql).split()), tuple(params or ())))

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return _Cur()

            def commit(self):
                sink.append(("COMMIT", ()))

            def close(self):
                pass

        return _Conn()

    def test_next_row_id_unique_and_monotonic(self):
        from backend.common.utils import next_row_id

        ids = [next_row_id() for _ in range(5000)]
        assert len(set(ids)) == len(ids), "同微秒不得重号（碰撞 = 审计丢行）"
        assert ids == sorted(ids), "主键必须单调递增（不单调 = 同毫秒回头碰上）"

    def test_next_row_id_unique_across_threads(self):
        import threading

        from backend.common.utils import next_row_id

        got: list = []
        lock = threading.Lock()

        def worker():
            batch = [next_row_id() for _ in range(500)]
            with lock:
                got.extend(batch)

        ts = [threading.Thread(target=worker) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert len(got) == 4000
        assert len(set(got)) == len(got), "多线程并发取号也不得重号"

    def test_operational_audit_takes_no_explicit_id(self, monkeypatch):
        """`adh_audit_logs` / `adh_query_audit` 本来就是 auto_increment：不自己占主键。"""
        import backend.common.auth as auth_mod
        import backend.core.query_executor as qe
        from backend.common.db import metadata_db

        sink: list = []
        fake = self._fake_conn(sink)
        monkeypatch.setattr(auth_mod, "_get_connection", lambda: fake)
        monkeypatch.setattr(metadata_db, "get_metadata_conn", lambda: fake)

        auth_mod.log_audit(1778154925, "admin", "dataset.query", target_type="dataset", target_id=33)
        qe.log_audit(1778154925, "admin", "admin", "问题", "SELECT 1 LIMIT 10", "success", 1)

        writes = [(s, p) for s, p in sink if s.startswith("INSERT")]
        assert len(writes) == 2
        for sql, params in writes:
            assert "(id," not in sql and "(id " not in sql, f"auto_increment 表不应自己写 id: {sql[:120]}"
            assert sql.count("%s") == len(params), "占位符与参数个数必须对齐"

    def test_rls_audit_id_is_microsecond_and_not_repeated(self, monkeypatch):
        """`adh_rls_audit_logs.id` 无 auto_increment，必须给值 —— 但给的是单调微秒。"""
        import backend.core.query_executor as qe

        sink: list = []
        fake = self._fake_conn(sink)
        # `_log_permission_audit` 用的是模块顶部已绑定的名字，得直接换它属性
        monkeypatch.setattr(qe, "get_metadata_conn", lambda: fake)

        for _ in range(3):
            qe._log_permission_audit({"user_id": 7}, 5, ["t_x"], "SELECT 1", "SELECT 1", True)

        writes = [(s, p) for s, p in sink if s.startswith("INSERT")]
        assert len(writes) == 3, "三次裁决就要三行审计（写不进去等于丢）"
        ids = [p[0] for _, p in writes]
        assert len(set(ids)) == 3
        assert all(i > 10 ** 15 for i in ids), f"主键应是微秒量级，不再是毫秒: {ids}"

    def test_rls_service_audit_shares_the_generator(self, monkeypatch):
        import backend.core.rls_service as rls

        sink: list = []
        fake = self._fake_conn(sink)
        monkeypatch.setattr(rls, "get_metadata_conn", lambda: fake)
        rls.rls_service.log_audit(7, 0, policy_id=1, policy_name="p", table_name="t_x", action="allow")

        writes = [(s, p) for s, p in sink if s.startswith("INSERT")]
        assert len(writes) == 1
        assert writes[0][1][0] > 10 ** 15

    def test_no_audit_writer_mints_millisecond_ids(self):
        """函数级护栏：五个审计写入函数里不得再出现“毫秒时间戳当主键”。

        按函数体扫而不是按文件扫：`_ts_id()` 还给用户/角色建号用（那里碰撞会直接报错、
        不是静默丢行），一刀切禁会把无关改动拖进来。
        """
        import inspect
        import re

        import backend.common.auth as auth_mod
        import backend.core.query_executor as qe
        import backend.core.rls_service as rls
        from backend.modules.auth.services import auth_service

        writers = [auth_mod.log_audit, auth_service.log_audit, qe.log_audit,
                   qe._log_permission_audit, rls.rls_service.log_audit]
        ms = re.compile(r"time\.time\(\)\s*\*\s*1000\)|timestamp\(\)\s*\*\s*1_?000\)")
        bad = [f"{fn.__module__}.{fn.__qualname__}" for fn in writers if ms.search(inspect.getsource(fn))]
        assert not bad, f"审计主键回到毫秒口径（同毫秒必碰→ duplicate-key 被吞 → 丢行）: {bad}"


class TestDatasourceRawExecuteSurface:
    """S6 数据源面的裸 SQL 试跑 `/api/datasources/{ds_id}/execute`：不得是第二条取数路。

    旧实现自己 `pymysql` 直连（SQL 源）+ 直调 `es_client.run_sql`（ES 源），于是同一句 SQL
    在数据源页拿到未脱敏全量、在看板/预览/语义查数/Playground 只拿到授权后的行 ——
    列级屏蔽与脱敏、行级策略、源级 RBAC、查询审计四项全部缺席，且任何登录用户
    可对任意 ds_id 跑任意 SELECT（端点只有 JWT 门）。现在只允许经 `semantics.execute.execute_sql`。
    """

    @staticmethod
    def _fake_result():
        import types

        return types.SimpleNamespace(
            columns=[types.SimpleNamespace(name="id")],
            rows=[(1,)], row_count=1, elapsed_ms=5)

    def _stub_ds_exists(self, monkeypatch, present=True):
        import backend.modules.catalog.services.datasource_service as ds_svc

        async def fake_raw(ds_id):
            return {"id": int(ds_id), "db_type": "mysql", "name": "prd-cn"} if present else None

        monkeypatch.setattr(ds_svc.datasource_service, "get_datasource_raw", fake_raw)
        return ds_svc

    def _spy_sem(self, monkeypatch):
        import backend.semantics.execute as sem_execute

        seen: list = []

        def spy(sql, policy_ctx=None, **kw):
            seen.append((sql, policy_ctx))
            return self._fake_result()

        monkeypatch.setattr(sem_execute, "execute_sql", spy)
        return seen

    def test_试跑经唯一出口且身份由服务端传入(self, monkeypatch):
        import asyncio

        ds_svc = self._stub_ds_exists(monkeypatch)
        seen = self._spy_sem(monkeypatch)

        out = asyncio.run(ds_svc.datasource_service.execute_sql(
            7, "SELECT id FROM t_x", user_id=42, workspace_id=9, username="bob"))

        assert len(seen) == 1, "取数必须落在 semantics.execute.execute_sql 一个口上"
        sql, ctx = seen[0]
        assert sql == "SELECT id FROM t_x"
        assert (ctx.user_id, ctx.workspace_id, ctx.username, ctx.datasource_id) == (42, 9, "bob", 7)
        assert out == {"columns": ["id"], "rows": [{"id": 1}], "row_count": 1, "elapsed_ms": 5}

    def test_ES源不在本面分派(self, monkeypatch):
        """ES 型源同样只交一个出口：分派在 `query_executor.execute_query` 一处完成。"""
        import asyncio

        ds_svc = self._stub_ds_exists(monkeypatch)
        seen = self._spy_sem(monkeypatch)

        asyncio.run(ds_svc.datasource_service.execute_sql(
            8, 'SELECT "a.keyword" FROM logs LIMIT 10', user_id=42, workspace_id=9))

        assert len(seen) == 1 and seen[0][1].datasource_id == 8

    def test_无身份_fail_closed_不碰执行口(self, monkeypatch):
        import asyncio

        ds_svc = self._stub_ds_exists(monkeypatch)
        seen = self._spy_sem(monkeypatch)

        with pytest.raises(NoIdentityError):
            asyncio.run(ds_svc.datasource_service.execute_sql(7, "SELECT 1", user_id=0))
        assert seen == [], "无身份不得降级成裸连/无过滤执行"

    def test_数据源面不得再长出第二执行口(self):
        """源码级护栏（行为用例证明不了“以后没人改回去”）。

        只扫执行面：`get_datasource_conn*` 给表/列元数据浏览用（information_schema），
        那不是返回业务数据行的取数，不在一刀切禁之列。
        """
        import inspect

        import backend.modules.catalog.services.datasource_service as ds_svc

        svc = ds_svc.DatasourceService
        assert not hasattr(svc, "_execute_db_sql"), "MySQL 侧直连执行支路必须不存在"
        assert not hasattr(ds_svc, "_es_execute"), "ES 侧直调 es_client 的执行支路必须不存在"
        body = inspect.getsource(svc.execute_sql)
        for banned in ("get_datasource_conn", "run_sql(", "cursor()"):
            assert banned not in body, f"execute_sql 不得再出现 {banned}（第二取数通道）"
        # 整个模块不再直接打 ES 取数接口（元数据探测另有专函数，不走 /_sql）
        module_src = inspect.getsource(ds_svc)
        assert "run_sql(" not in module_src, "ES 取数只在 core.query_executor 一处"

    def test_端点身份只取JWT_拒绝映射403(self, monkeypatch):
        import asyncio

        from fastapi import HTTPException

        from backend.modules.catalog.api import datasources as ds_api
        import backend.semantics.execute as sem_execute

        ds_svc = self._stub_ds_exists(monkeypatch)
        seen = self._spy_sem(monkeypatch)

        # body 递交 user_id 不生效（伪造无效果），身份只来自注入的 user（服务端 JWT）
        asyncio.run(ds_api.execute_sql(
            ds_id=7, req={"sql": "SELECT id FROM t_x", "user_id": 999, "workspace_id": 777},
            user={"user_id": 42, "username": "bob"}, workspace_id=9))
        assert seen and seen[0][1].user_id == 42 and seen[0][1].workspace_id == 9

        def deny(sql, policy_ctx=None, **kw):
            raise sem_execute.SemanticError(
                sem_execute.ErrorCode.FORBIDDEN, "行级策略拦截", blocked_at="permission")

        monkeypatch.setattr(sem_execute, "execute_sql", deny)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ds_api.execute_sql(
                ds_id=7, req={"sql": "SELECT 1"}, user={"user_id": 42, "username": "bob"},
                workspace_id=9))
        assert exc.value.status_code == 403

        with pytest.raises(HTTPException) as exc:
            asyncio.run(ds_api.execute_sql(
                ds_id=7, req={"sql": "SELECT 1"}, user={"user_id": 0, "username": ""},
                workspace_id=9))
        assert exc.value.status_code == 403, "无可信身份 = 403，不是 400/500"

    def test_端点不存在的源回404(self, monkeypatch):
        import asyncio

        from fastapi import HTTPException

        from backend.modules.catalog.api import datasources as ds_api

        self._stub_ds_exists(monkeypatch, present=False)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ds_api.execute_sql(
                ds_id=999, req={"sql": "SELECT 1"}, user={"user_id": 42, "username": "bob"},
                workspace_id=9))
        assert exc.value.status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
