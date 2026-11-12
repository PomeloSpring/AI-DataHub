"""看板图表「只能从数据集取数」的服务端闸门离线回归(不触真库)。

2026-11-06 拍板：看板编辑器里的图表不得再由用户直接编写 SQL，口径唯一归属是
「数据集」（BI 治理建模层：字段声明/认证/血缘/角色清单），可挂清单必须来自
**角色授权内的数据集**（与编辑器/组件库下拉同一个裁决点，不开双口径）。

锁定的契约：
  - 写入口 `assert_chart_data_source` 是唯一的把关点（create/update/batch 三处都经它），
    旧的第二把关函数 `assert_dataset_chart_usable` 已退役，不得再长出第二套判断。
  - 提交 SQL / 存量带 SQL / 旧直连快照通道：显式拒绝并给出出路（不静默受理、不清空冒充）。
  - 数据集必须在角色授权清单内（fail-closed：空授权 = 不可挂，admin 代码层 bypass）、
    必须存在且 active、且「可用数据源范围 ∩ 角色授权源 ≠ ∅」（不开"挂得上、刷新必报错"的空承诺）。
  - 豁免形态各有把关人：控件、模板示例图、AS-BOT 设计产物、声明式语义图。
  - 刷新只认三条通道；带旧 SQL 的行指名收编而不是继续跑或刷成空图。
  - API 契约层：ChartCreate/ChartUpdate 不再有 sql_query，ChartRefreshRequest 不再有
    count_sql（客户端可任意递交统计 SQL = 第二取数通道）。
"""

import importlib
import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

dash = importlib.import_module("backend.modules.viz.services.dashboard_service")
api = importlib.import_module("backend.modules.viz.api.dashboard")
chart_api = importlib.import_module("backend.modules.viz.api.chart")
ds_svc = importlib.import_module("backend.modules.viz.services.dataset_service")
role_module = importlib.import_module("backend.core.role_service")

IDENT = {"user_id": 7, "username": "analyst01", "role": "analyst", "workspace_id": 1}
ADMIN_IDENT = {**IDENT, "user_id": 1, "role": "admin", "username": "admin"}


# ── 测试替身：闸门依赖全部固定答案，不碰真库 ────────────────────────────────


class NoopCursor:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    rowcount = 0

    def execute(self, sql, params=None):
        raise AssertionError(f"闸门阶段不得发写语句: {sql}")

    def fetchone(self): return None
    def fetchall(self): return []


class FakeConn:
    def cursor(self): return NoopCursor()
    def commit(self): raise AssertionError("闸门未通过不得提交")
    def close(self): pass


def _gate_env(monkeypatch, *, stored=None, datasets=("销售趋势",), scope=None,
              active=True, exists=True):
    """装配闸门依赖：存量图表行 / 角色数据集清单 / 可执行源范围。"""
    monkeypatch.setattr(dash, "execute_query", lambda *a, **k: stored)
    monkeypatch.setattr(dash, "get_metadata_conn", lambda: FakeConn())
    monkeypatch.setattr(dash, "_invalidate_dashboard_payload", lambda *a, **k: None)
    ds = None if not exists else {
        "id": 7, "name": "销售趋势数据集", "status": "active" if active else "disabled",
    }
    monkeypatch.setattr(ds_svc, "get_dataset", lambda ds_id: ds)
    monkeypatch.setattr(ds_svc, "usable_scope",
                        lambda d, ident, **kw: (scope if scope is not None
                                                else ([{"id": 5, "name": "生产库"}], [])))
    monkeypatch.setattr(role_module.role_service, "get_user_allowed_datasets",
                        lambda uid: [7] if "销售趋势" in datasets else [])


def dataset_chart(**over):
    chart = {"id": 21, "dashboard_id": 10, "name": "销售趋势", "chart_type": "line",
             "source_type": "dataset", "source_id": 7, "sql_query": None,
             "query_source": "raw_sql", "semantic_query": None,
             "config": json.dumps({"xCol": "dt", "yCol": "sales"})}
    chart.update(over)
    return chart


# ── 写入口闸门：SQL / 旧通道 / 角色清单 ──────────────────────────────────────


class TestChartSqlRejected:
    def test_submitted_sql_is_rejected_with_way_out(self, monkeypatch):
        _gate_env(monkeypatch)
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source({**dataset_chart(), "sql_query": "SELECT 1"}, IDENT)
        assert "不再接受直接编写 SQL" in str(exc.value) and "数据集" in str(exc.value)

    def test_stored_sql_blocks_even_a_rename(self, monkeypatch):
        """patch 只改名称也要拦：存量 SQL 不得经"这次没动 SQL"继续挂在图上。"""
        _gate_env(monkeypatch, stored=dataset_chart(sql_query="SELECT 1 FROM t"))
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source({"name": "新标题"}, IDENT, chart_id=21)
        assert "不再接受直接编写 SQL" in str(exc.value)

    def test_legacy_query_channel_demands_migration(self, monkeypatch):
        _gate_env(monkeypatch)
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source(dataset_chart(source_type="query", source_id=None), IDENT)
        assert "已下线" in str(exc.value) and "数据集" in str(exc.value)
        assert "21" not in str(exc.value)          # 文案指名图表名，不裸显内部 id

    def test_unknown_channel_is_rejected(self, monkeypatch):
        _gate_env(monkeypatch)
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source(dataset_chart(source_type="playground"), IDENT)
        assert "只能从数据集取数" in str(exc.value)

    def test_snapshot_channel_demands_migration(self, monkeypatch):
        _gate_env(monkeypatch)
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source(dataset_chart(source_type="snapshot", source_id=3), IDENT)
        assert "已下线" in str(exc.value) and "改挂" in str(exc.value)

    def test_empty_payload_source_type_is_allowed_for_placeholder(self, monkeypatch):
        """占位图（拖入字模/图表卡尚未配数）不报错，也不给"挂得上"的空承诺。"""
        _gate_env(monkeypatch)
        dash.assert_chart_data_source(
            {"name": "新图", "chart_type": "bar", "source_type": "empty"}, IDENT)
        dash.assert_chart_data_source(
            {"name": "模板图", "chart_type": "line", "source_type": "template"}, IDENT)


class TestRoleDatasetGrant:
    def test_dataset_outside_role_grant_is_rejected(self, monkeypatch):
        _gate_env(monkeypatch, datasets=())          # 角色未授权任何数据集
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source(dataset_chart(), IDENT)
        assert "未被授权" in str(exc.value)
        assert "角色权限" in str(exc.value)           # 给出出处，不让用户猜

    def test_empty_grant_is_fail_closed_not_all(self, monkeypatch):
        _gate_env(monkeypatch, datasets=())
        with pytest.raises(ValueError):
            dash.assert_chart_data_source(dataset_chart(), {**IDENT, "role": "viewer"})

    def test_admin_bypasses_grant_list(self, monkeypatch):
        _gate_env(monkeypatch, datasets=())
        dash.assert_chart_data_source(dataset_chart(), ADMIN_IDENT)

    def test_missing_identity_rejected(self, monkeypatch):
        _gate_env(monkeypatch)
        with pytest.raises(dash.NoIdentityError):
            dash.assert_chart_data_source(dataset_chart(), {"user_id": 0, "role": "analyst"})

    def test_missing_or_inactive_dataset_rejected(self, monkeypatch):
        _gate_env(monkeypatch, exists=False)
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source(dataset_chart(), IDENT)
        assert "不存在" in str(exc.value)

        _gate_env(monkeypatch, active=False)
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source(dataset_chart(), IDENT)
        assert "已停用" in str(exc.value)

    def test_no_executable_source_rejected(self, monkeypatch):
        """可用范围 ∩ 角色授权源 = 空 → 不开"挂得上、刷新必报错"的空承诺。"""
        _gate_env(monkeypatch, scope=([], ["已删除的库"]))
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source(dataset_chart(), IDENT)
        assert "任何可用数据源" in str(exc.value)
        assert "已删除的库" in str(exc.value)         # 缺失源用业务名回显

    def test_dataset_chart_without_source_id_rejected(self, monkeypatch):
        _gate_env(monkeypatch)
        with pytest.raises(ValueError) as exc:
            dash.assert_chart_data_source(dataset_chart(source_id=None), IDENT)
        assert "缺少数据集标识" in str(exc.value)


class TestExemptChannels:
    def test_widget_skips_gate(self, monkeypatch):
        _gate_env(monkeypatch, datasets=())
        dash.assert_chart_data_source(
            {"chart_type": "widget_daterange", "source_type": "query", "sql_query": "SELECT 1"}, IDENT)

    def test_as_bot_design_skips_gate(self, monkeypatch):
        """设计面板是护栏 §7 的显式例外通道，SQL 由服务端生成，自有关门。"""
        _gate_env(monkeypatch, datasets=())
        dash.assert_chart_data_source(
            dataset_chart(config=json.dumps({"as_bot_design": True}),
                          sql_query="SELECT server_generated", source_type="query"), IDENT)

    def test_semantic_chart_skips_sql_rejection(self, monkeypatch):
        """AS-BOT 大屏工具存的行带编译产物 SQL：不能被人写 SQL 判定误伤。"""
        _gate_env(monkeypatch, datasets=())
        dash.assert_chart_data_source(
            dataset_chart(query_source="semantic", semantic_query={"object": "order"},
                          sql_query="SELECT compiled FROM t", source_type="query"), IDENT)


# ── 写入口接线：三处都过闸门，不过则一笔不写 ─────────────────────────────────


class TestWritePathsWired:
    def test_create_chart_rejects_sql_before_any_write(self, monkeypatch):
        _gate_env(monkeypatch)
        with pytest.raises(ValueError):
            dash.chart_service.create_chart(10, {"name": "x", "chart_type": "bar",
                                                 "sql_query": "SELECT 1"}, identity=IDENT)

    def test_update_chart_rejects_legacy_channel(self, monkeypatch):
        _gate_env(monkeypatch, stored=dataset_chart())
        with pytest.raises(ValueError):
            dash.chart_service.update_chart(10, 21, {"source_type": "query"}, identity=IDENT)

    def test_batch_save_aborts_whole_draft_on_bad_item(self, monkeypatch):
        log = []

        class LogCursor(NoopCursor):
            def execute(self, sql, params=None):
                log.append(" ".join(sql.split()))
            def fetchall(self):
                return [{"id": 21, "name": "销售趋势"}]

        class LogConn(FakeConn):
            def cursor(self): return LogCursor()

        _gate_env(monkeypatch, datasets=())
        monkeypatch.setattr(dash, "get_metadata_conn", lambda: LogConn())
        outcome = dash.chart_service.batch_save(
            10, [], {"21": {"source_type": "query"}}, [], identity=IDENT)
        assert outcome["ok"] is False
        assert any("销售趋势" in e for e in outcome["errors"])   # 指名图表（不裸显 id）
        assert not [s for s in log if s.startswith(("INSERT", "UPDATE", "DELETE"))]


# ── 刷新分派：三条通道 + 存量 SQL 指名收编 ──────────────────────────────────


def _refresh_env(monkeypatch, chart, *, page_size=None, page_num=None):
    """按单图刷新路径装配（可见性 + 图表行 + 数据集入口）。"""
    log = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def execute(self, sql, params=None):
            log.append(" ".join(sql.split()))

        def fetchone(self): return chart

    class Conn:
        def cursor(self): return Cur()
        def commit(self): log.append("COMMIT")
        def close(self): pass

    monkeypatch.setattr(dash, "check_dashboard_visible", lambda *a, **k: True)
    monkeypatch.setattr(dash, "get_metadata_conn", lambda: Conn())
    monkeypatch.setattr(dash, "_invalidate_dashboard_payload", lambda *a, **k: None)
    from backend.common import auth as _auth
    monkeypatch.setattr(_auth, "get_user_by_id", lambda uid: {"user_role": "analyst"})
    calls = {}

    def fake_query_dataset(dataset_id, params, identity):
        calls["dataset_id"] = dataset_id
        calls["params"] = params
        return {"columns": ["dt"], "rows": [{"dt": "10-19"}], "row_count": 1, "total": 137}

    monkeypatch.setattr(ds_svc, "query_dataset", fake_query_dataset)
    monkeypatch.setattr(ds_svc, "get_dataset", lambda ds_id: {"id": ds_id, "name": "ds", "variables": None})
    out = dash.chart_service.refresh_chart(10, 21, page_size=page_size, page_num=page_num,
                                          user_id=7, workspace_id=1, username="analyst01")
    return out, calls, log


class TestRefreshDispatch:
    def test_dataset_chart_pages_via_dataset_entry(self, monkeypatch):
        """表值图分页只递 page_size/page_num；偏移与行总数均由服务端算。"""
        chart = dataset_chart(chart_type="table_value",
                              config=json.dumps({"pageSize": 20}))
        out, calls, _ = _refresh_env(monkeypatch, chart, page_size=20, page_num=3)
        assert calls["params"]["page_size"] == 20 and calls["params"]["page_num"] == 3
        # 旧口径全部退出：前端不再递交 offset，也不递交“要不要统计”的开关
        for retired in ("page_offset", "page_limit", "page_count_sql", "count_sql"):
            assert retired not in calls["params"], retired
        assert out["total"] == 137

    def test_table_value_defaults_to_first_page_with_configured_size(self, monkeypatch):
        """不显式给页（整板重查/首屏）也得按页取：否则“不传就拉全量”就是伪分页源头。"""
        chart = dataset_chart(chart_type="table_value",
                              config=json.dumps({"pageSize": 50}))
        _out, calls, _ = _refresh_env(monkeypatch, chart)
        assert calls["params"]["page_size"] == 50 and calls["params"]["page_num"] == 1

    def test_oversized_pageSize_is_clamped_to_100(self, monkeypatch):
        """旧配置残留的 500 行大页不能把取数规模放开。"""
        chart = dataset_chart(chart_type="table_value",
                              config=json.dumps({"pageSize": 500}))
        _out, calls, _ = _refresh_env(monkeypatch, chart)
        assert calls["params"]["page_size"] == 100

    def test_non_table_value_charts_never_get_page_params(self, monkeypatch):
        """给曲线图套 20 行页限会把趋势砍没：分页只属于表值图。"""
        _out, calls, _ = _refresh_env(monkeypatch, dataset_chart(), page_size=20, page_num=2)
        assert "page_size" not in calls["params"] and "page_num" not in calls["params"]

    def test_pinned_datasource_is_passed_down_to_query(self, monkeypatch):
        """看板级选源：图表 config.datasource（业务名）随刷新下递到 query_dataset 的 datasource 参数。"""
        chart = dataset_chart(config=json.dumps({"xCol": "dt", "datasource": "prd-jp"}))
        _out, calls, _ = _refresh_env(monkeypatch, chart)
        assert calls["params"]["datasource"] == "prd-jp"

    def test_unpinned_chart_does_not_inject_datasource(self, monkeypatch):
        """未钉选时不注入 datasource（存量行为零变化，交服务端在交集内自动定源）。"""
        _out, calls, _ = _refresh_env(monkeypatch, dataset_chart())
        assert "datasource" not in calls["params"]

    def test_legacy_sql_chart_is_not_executed(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("存量手写法 SQL 不得继续下发")
        monkeypatch.setattr(dash, "governed_execute", boom)
        with pytest.raises(ValueError) as exc:
            _refresh_env(monkeypatch, dataset_chart(source_type="query", sql_query="SELECT 1"))
        assert "旧版直写 SQL" in str(exc.value)

    def test_semantic_chart_refuses_pagination(self, monkeypatch):
        """声明式语义图没有 OFFSET 形状：不开“看着能翻页、其实拿的是同一页”的空承诺。"""
        chart = dataset_chart(chart_type="table_value", source_type="query", sql_query=None,
                              query_source="semantic",
                              semantic_query=json.dumps({"object": "order"}))
        with pytest.raises(ValueError) as exc:
            _refresh_env(monkeypatch, chart, page_size=20)
        assert "按页取数" in str(exc.value)

    def test_refresh_all_charts_shares_dispatch(self, monkeypatch):
        """整板重查与单图同一实现：不得再保留第二份「遍历执行图表 SQL」的老代码。"""
        src = inspect.getsource(dash.ChartService.refresh_all_charts)
        assert "_refresh_by_channel" in src
        assert "governed_execute(" not in src
        assert "validate_sql" not in src


# ── API 契约：不再有可递交 SQL 的字段 ────────────────────────────────────────


class TestApiContractHasNoSqlChannel:
    def test_chart_models_have_no_default_sql_channel(self):
        """新建图表不得默认长进已下线的 'query' 通道（旧字段仅留给显式拒收）。"""
        assert api.ChartCreate(name="x", chart_type="bar").source_type is None
        assert api.ChartCreate.model_fields["sql_query"].deprecated is True

    def test_submitting_sql_through_the_endpoint_is_rejected(self, monkeypatch):
        """旧客户端递 SQL → 400 + 出路（不静默丢弃后当保存成功）。"""
        from fastapi import HTTPException

        _gate_env(monkeypatch)
        monkeypatch.setattr(api, "check_dashboard_editable", lambda *_: True)
        body = api.ChartCreate(name="x", chart_type="bar", sql_query="SELECT secret FROM t")
        with pytest.raises(HTTPException) as exc:
            api.add_chart_endpoint(10, body, user={"user_id": 7, "role": "analyst",
                                                   "username": "analyst01"})
        assert exc.value.status_code == 400
        assert "不再接受直接编写 SQL" in exc.value.detail

    def test_refresh_request_has_no_count_sql(self):
        assert "count_sql" not in api.ChartRefreshRequest.model_fields
        assert "count_sql" not in chart_api.ChartRefreshRequest.model_fields

    def test_dataset_only_gate_is_the_single_choke_point(self):
        """旧的第二把关函数已退役（不得留下"看着能用其实没接线"的入口）。"""
        assert not hasattr(dash, "assert_dataset_chart_usable")
        assert hasattr(dash, "assert_chart_data_source")

    def test_chart_api_does_not_execute_sql_itself(self):
        src = inspect.getsource(chart_api)
        assert "governed_execute(" not in src
        assert "_substitute_params" not in src          # 参数拼接的裸 SQL 通道随旧实现一并退出
        assert "chart_service.refresh_chart" in src     # 一律委托同一分派


# ── 存量角色默认授权迁移（收口不凭白让存量看板编辑者入口消失）─────────────

class TestDefaultGrantMigration:
    """`docker/mysql/chart_dataset_default_grant_migration.sql` 的契约（幂等 + 不扩权）。"""

    PATH = os.path.join(os.path.dirname(__file__), "..",
                        "docker", "mysql", "chart_dataset_default_grant_migration.sql")

    def test_file_exists_and_is_idempotent(self):
        with open(self.PATH, encoding="utf-8") as fh:
            sql = fh.read()
        assert "INSERT IGNORE INTO adh_role_dataset_access" in sql
        assert "uk_role_dataset" in sql                      # 唯一键 = 可重跑的事实基础

    def test_only_grants_chart_referenced_active_datasets(self):
        """只补“看板上真有引用”的数据集：不借迁移把全量数据集放给普通角色。"""
        with open(self.PATH, encoding="utf-8") as fh:
            sql = fh.read()
        assert "c.source_type = 'dataset'" in sql and "EXISTS" in sql
        assert "d.status = 'active'" in sql

    def test_does_not_touch_admin_or_custom_roles(self):
        """只针对内置 analyst/viewer，其余角色（含 admin / 自建角色）的授权不得被迁移改到。"""
        with open(self.PATH, encoding="utf-8") as fh:
            sql = fh.read()
        assert "'analyst', 'viewer'" in sql
        assert "adh_role_datasource_access" not in sql        # 数据源执行权是另一层，不由本迁移扩

    def test_schema_agnostic(self):
        """不写 USE：库名跟连接配置（本仓迁移新口径），否则在联调库上会建/写到错库。"""
        with open(self.PATH, encoding="utf-8") as fh:
            assert "\nUSE " not in fh.read()

