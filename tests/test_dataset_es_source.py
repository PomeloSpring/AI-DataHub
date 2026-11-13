"""ES 型数据集（挂在 ES 型数据源上）的契约回归测试。

口径（2026-11-13 阶段三第 2 步）：内置 Elasticsearch 通道（`source_type='es'` + `es_pattern`、
`config.ES_*`、`es_client.legacy_ctx`、`_query_es`）**已退役**，ES 数据集只剩一种形态 ——
`source_type='sql'` + 执行源 `db_type='es'`。本文件因此只覆盖两件事：

1. **执行形状与迁移前逐字一致**：正文自聚合、不套派生表、翻页走 `search_after` 游标、
   时间参数留空由服务端补滚动窗并回显、行数上限与正文 LIMIT 取小值合一；
2. **治理域内**：列级屏蔽/脱敏与行级策略经 `governed_execute` 编译进正文，成功/拒绝落审计，
   不支持项（运行时二次聚合、运行时筛选、行级 scope）fail-loud 而不是静默忽略。

内置通道的机件另有「退役护栏」一节（见 tests/test_es_builtin_channel_retired.py）：
写回 `es_pattern`、直调 `es_client.run_sql` 的第二条取数路都必须显式拒，不得静默复活。

全部不依赖真实 ES 集群：打治理口与 `es_pagination`，闸门判定用真实实现。
"""

import json

import pytest

from backend.core import es_client
from backend.core import es_pagination
from backend.core.governed_query import NoIdentityError
from backend.modules.viz.services import dataset_registry as reg
from backend.modules.viz.services import dataset_service as svc


class _Boom:
    """被调用即抛：用来锁定"这条路径不得经过某个实现"。"""

    def __init__(self, name):
        self.name = name

    def __call__(self, *a, **kw):
        raise AssertionError(f"{self.name} 不应被 ES 分支调用")


# ── 阶段二：ES 数据源型数据集（source_type='sql' + ES 源）───────────────────
# 迁移后的 ES 数据集不再靠 `source_type='es'`/`es_pattern` 自我介绍，而是“执行源 db_type=es”。
# 取数形状必须与迁移前逐字一致（正文自聚合、不套派生表、翻页走游标），但权限/RLS/
# 列级脱敏/审计一律进治理口 —— 这正是本次迁移的目的。

ES_SRC_ID = 1794535612029
SRC_ALIAS = "t_equipment_warning_log"
SRC_BODY = (f'SELECT "warningTime.keyword" AS wt, "level.keyword" AS lv FROM "{SRC_ALIAS}" '
            'WHERE isRelease = 1 ORDER BY wt DESC LIMIT 500')
SRC_TIME_BODY = (f'SELECT "warningTime.keyword" AS wt FROM "{SRC_ALIAS}" '
                 "WHERE (${start_time} = '' OR \"warningTime.keyword\" >= ${start_time}) "
                 "AND (${end_time} = '' OR \"warningTime.keyword\" <= ${end_time}) LIMIT 500")
TIME_VARS = json.dumps({"start_time": {"type": "string", "default": ""},
                        "end_time": {"type": "string", "default": ""},
                        "site": {"type": "string", "default": ""}})


def _src_row(**over) -> dict:
    row = {"id": 901, "name": "设备告警明细（ES 源）", "status": "active",
           "source_type": "sql", "es_pattern": "", "object_key": "",
           "datasource_id": ES_SRC_ID, "available_datasources": "[]",
           "sql_query": SRC_BODY, "variables": "{}", "sql_bodies": "{}",
           "field_config": "[]", "owner_id": 1, "paginable": 0}
    row.update(over)
    return row


def _stub_es_source(monkeypatch, *, rows: int = 1794535612029):
    """钉住“执行源是 ES 型”这件事（判据 = 数据源行的 db_type，不拿 es_pattern 当辅助信号）。"""
    from backend.common.db import datasource_db

    monkeypatch.setattr(svc, "_dataset_db_type", lambda _ds: "es")
    monkeypatch.setattr(datasource_db, "get_datasource_by_id",
                        lambda _id: {"id": rows, "name": "cloud-platform-es", "db_type": "es"})


class TestEsSourceDatasetQuery:
    IDENT = {"user_id": 7, "username": "u", "role": "analyst", "workspace_id": 1}

    def _route(self, monkeypatch, row, params, *, plain=None):
        import backend.core.governed_query as gq
        import backend.core.query_executor as qe

        _stub_es_source(monkeypatch)
        seen: dict = {}
        monkeypatch.setattr(svc, "get_dataset", lambda _id: row)
        monkeypatch.setattr(svc, "resolve_effective_source", lambda *a, **k: ES_SRC_ID)
        monkeypatch.setattr(svc, "effective_source_name", lambda _id: "cloud-platform-es")
        monkeypatch.setattr(svc, "_es_audit", lambda ident, ds, sql, **kw:
                            seen.setdefault("audit", []).append((sql, kw)))

        def fake_governed(sql, ds_id, uid, ws, username=""):
            seen["sql"] = sql
            seen["ds_id"] = ds_id
            return plain or {"columns": ["wt", "lv"],
                             "rows": [{"wt": "2026-11-01 10:00:00", "lv": "error"}],
                             "row_count": 1, "elapsed_ms": 8}

        def fake_page(sql, ds_id, user_context, ws, **kw):
            seen["sql"] = sql
            seen["page"] = kw
            seen["user_context"] = user_context
            return {"columns": ["wt", "lv"], "rows": [{"wt": "2026-11-01 10:00:00"}],
                    "row_count": 1, "total": 4468044,
                    "page_size": kw["page_size"], "page_num": kw["page_num"],
                    "paging_mode": "cursor", "elapsed_ms": 12}

        monkeypatch.setattr(gq, "governed_execute", fake_governed)
        monkeypatch.setattr(qe, "execute_es_page_with_permission", fake_page)
        return svc.query_dataset(row["id"], params, self.IDENT), seen

    def test_body_is_sent_flat_without_derived_table(self, monkeypatch):
        """ES 无派生表：编译器的 `SELECT * FROM (…) AS _ds_base` 包裹一层必须不出现。"""
        out, seen = self._route(monkeypatch, _src_row(), {"limit": 500})
        sql = seen["sql"]
        assert "_ds_base" not in sql and "FROM (" not in sql and "SELECT * FROM" not in sql
        assert sql.startswith('SELECT "warningTime.keyword" AS wt')
        assert sql.rstrip().endswith("LIMIT 500")
        assert seen["ds_id"] == ES_SRC_ID          # 进治理口，不再走内置通道
        assert out["execution_mode"] == "es" and out["datasource"] == "cloud-platform-es"
        assert out["unpaged"] is True and "paging" not in out
        # 治理标注必须随结果下发，且得是 **ES 数据源口径**（策略编译进 SQL）；
        # 内置通道那份“column_policy=not_applied”对 ES 数据源是反向陈述（管理员会按“这里权限没生效”做错判断）。
        assert out["governance"] == svc._ES_SOURCE_GOVERNANCE_NOTE
        assert out["governance"]["column_policy"] == "compiled_into_sql"
        assert out["governance"]["channel"] != "builtin_elasticsearch"

    def test_page_goes_through_cursor_entry_not_offset(self, monkeypatch):
        """分页数据集：走 `execute_es_page_with_permission`（search_after），不得拼 OFFSET/COUNT 包裹。"""
        row = _src_row(paginable=1)
        out, seen = self._route(monkeypatch, row, {"page_size": 20, "page_num": 2, "limit": 500})
        assert seen["page"]["page_size"] == 20 and seen["page"]["page_num"] == 2
        assert seen["page"]["context_key"]           # 翻页上下文签名非空（否则第 2 页找不到游标）
        assert "OFFSET" not in seen["sql"] and "COUNT(*)" not in seen["sql"]
        assert out["total"] == 4468044
        assert out["paging"] == {"mode": "cursor", "page_size": 20, "page_num": 2,
                                 "total": 4468044, "can_jump": False, "stable_order": True}
        assert seen["user_context"] == {"user_id": 7, "username": "u"}

    def test_page_size_caps_row_limit(self, monkeypatch):
        """按页取数时行限就是页大小：不得“图上只显 20 行、正文却拉 500 行”。"""
        _, seen = self._route(monkeypatch, _src_row(paginable=1),
                              {"page_size": 20, "page_num": 1, "limit": 500})
        assert seen["sql"].rstrip().endswith("LIMIT 20")

    def test_requested_limit_never_raises_body_limit(self, monkeypatch):
        """正文 LIMIT 比请求行数小时按正文（建模者控制扫描规模），不得静默拉高。"""
        row = _src_row(sql_query=SRC_BODY.replace("LIMIT 500", "LIMIT 30"))
        _, seen = self._route(monkeypatch, row, {"limit": 500})
        assert seen["sql"].rstrip().endswith("LIMIT 30")

    def test_empty_time_args_are_widened_and_echoed(self, monkeypatch):
        from datetime import datetime

        row = _src_row(sql_query=SRC_TIME_BODY, variables=TIME_VARS)
        out, seen = self._route(monkeypatch, row, {"limit": 500, "args": {}})
        win = out["default_window"]
        assert win["days"] == 30 and win["reason"]
        assert win["start_time"] in seen["sql"] and "'' = ''" not in seen["sql"]
        start = datetime.strptime(win["start_time"], "%Y-%m-%d %H:%M:%S")
        assert 29 <= (datetime.now() - start).days <= 30

    def test_runtime_filters_and_second_aggregation_are_refused(self, monkeypatch):
        """不能拿“静默忽略”冒充支持：图上绑了指标而实际按正文出数 = 两回事。"""
        _stub_es_source(monkeypatch)
        ident = self.IDENT
        for params, needle in (
                ({"filters": [{"field": "lv", "value": "error"}]}, "运行时筛选器"),
                ({"order": [{"by": "wt", "desc": True}]}, "排序"),
                ({"dimensions": ["lv"], "measures": ["c"]}, "二次聚合")):
            with pytest.raises(ValueError, match=needle):
                svc._query_sql(_src_row(), params, [], ident)

    def test_row_scopes_are_refused(self, monkeypatch):
        _stub_es_source(monkeypatch)
        with pytest.raises(ValueError, match="行级范围"):
            svc._query_sql(_src_row(), {"limit": 10}, [{"field": "lv", "op": "eq", "value": "e"}],
                           self.IDENT)

    def test_dataset_page_scope_save_is_refused(self, monkeypatch):
        """保存这一关同样拒：存下 scope 等于配一个永不生效的权限开关。"""
        _stub_es_source(monkeypatch)
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _src_row())
        monkeypatch.setattr(svc, "_require_editor", lambda *a, **k: None)
        with pytest.raises(ValueError, match="行级策略"):
            svc.set_scopes(901, [{"subject_type": "role", "subject_id": 2,
                                  "filters": [{"field": "lv", "op": "eq", "value": "e"}]}],
                           self.IDENT)

    def test_builtin_channel_datasets_are_no_longer_routable(self, monkeypatch):
        """退役护栏：数据集侧不得再有“不挂数据源、直打 es_client”的第二条取数路。

        内置通道行（`source_type='es'` / `es_pattern`）在库里已 0 行，分派入口也随之删除；
        拿旧行形状发问只能得到“不存在”，不能静默走 MCP 通道、也不能拿旧治理标注回前端。
        """
        assert not hasattr(svc, "_query_es"), "内置通道取数入口必须不存在"
        assert not hasattr(svc, "is_es_dataset")
        assert not hasattr(svc, "require_es_channel")
        assert svc.execution_is_es(_es_like_builtin_row()) is False


def _es_like_builtin_row(**over) -> dict:
    """旧内置通道行形状（库里已零行）：只用于证明它不再被当成 ES 执行通道。"""
    row = {"id": 900, "name": "设备告警按天", "status": "active",
           "source_type": "es", "es_pattern": "t-t_equipment_warning_log*",
           "object_key": "", "datasource_id": 0, "available_datasources": "[]",
           "sql_query": SRC_BODY, "variables": "{}", "sql_bodies": "{}",
           "field_config": "[]", "owner_id": 1}
    row.update(over)
    return row


class TestEsBusinessErrorPassthrough:
    """ES 侧的可操作文案不得被通用错因分类洗成“稍后重试”。"""

    def test_exec_fail_text_keeps_actionable_es_message(self):
        msg = "Elasticsearch 查询超时（超过 20s）：该查询扫描量/分组基数过大。"
        assert svc._exec_fail_text(msg, "es", "es_business") == msg

    def test_other_categories_still_get_canned_hint(self):
        assert svc._exec_fail_text("some raw driver error", "es", "connectivity") != \
            "some raw driver error"

    def test_execute_sql_marks_safe_business_errors(self, monkeypatch):
        from backend.semantics import execute as sem_exec
        from backend.semantics.contract import ErrorCode, SemanticError

        monkeypatch.setattr(sem_exec, "_sql_audit", lambda *a, **k: None)

        def boom(*a, **k):
            raise es_client.EsError("索引「x-*」未在数据源「cloud-platform-es」的开放范围内")

        import backend.core.query_executor as qe

        monkeypatch.setattr(qe, "execute_query_with_permission", boom)
        with pytest.raises(SemanticError) as exc:
            sem_exec.execute_sql('SELECT 1 FROM "x-*" LIMIT 1',
                                 sem_exec.PolicyContext(user_id=7, datasource_id=ES_SRC_ID))
        assert exc.value.code is ErrorCode.EXEC_FAILED
        assert "开放范围" in exc.value.message          # 可操作提示原样外抛
        assert exc.value.detail["failure_category"] == "es_business"


class TestEsSourceDatasetValidation:
    def _ctx(self, monkeypatch, allowed: tuple):
        ctx = es_client.EsCtx(datasource_id=ES_SRC_ID, label="cloud-platform-es",
                              base_url="https://es.invalid:9200", username="u", password="p",
                              api_key="", verify_certs=True, timeout_sec=20, max_rows=1000,
                              allowed=allowed)
        monkeypatch.setattr(es_client, "ctx_for", lambda _ds_id: ctx)
        return ctx

    def test_es_source_definition_is_detected(self, monkeypatch):
        _stub_es_source(monkeypatch)
        assert reg.is_es_source_definition({"datasource_id": ES_SRC_ID})
        assert not reg.is_es_source_definition({"datasource_id": 0})

    def test_validate_definition_routes_es_source_body_to_es_probe(self, monkeypatch):
        """ES 正文不得过 sqlglot（无 ES 方言 → 必误判），改走 `es_client.parse_select`。"""
        _stub_es_source(monkeypatch)
        self._ctx(monkeypatch, (SRC_ALIAS,))
        errors, stored = reg.validate_definition(_src_row())
        assert errors == [], errors
        assert json.loads(stored["touched_tables"]) == [SRC_ALIAS]
        assert stored["sql_bodies"] == "{}"

    def test_rejects_body_outside_datasource_scope(self, monkeypatch):
        _stub_es_source(monkeypatch)
        self._ctx(monkeypatch, ("other_alias",))
        errors, _ = reg.validate_definition(_src_row())
        assert any("未在数据源" in e and "开放范围" in e for e in errors), errors
        assert any("other_alias" in e for e in errors)   # 必须给出可查清单

    def test_rejects_leftover_es_pattern_and_extra_definitions(self, monkeypatch):
        """双口径共存直接拒：es_pattern（通道收窄）/object_key（阶段四才开放）/sql_bodies（死键）。"""
        _stub_es_source(monkeypatch)
        self._ctx(monkeypatch, (SRC_ALIAS,))
        for over in ({"es_pattern": "logs-*"}, {"object_key": "alert"},
                     {"sql_bodies": json.dumps({"mysql": "SELECT 1"})}):
            errors, _ = reg.validate_definition(_src_row(**over))
            assert errors, over

    def test_rejects_measure_expr_on_es_source(self, monkeypatch):
        _stub_es_source(monkeypatch)
        self._ctx(monkeypatch, (SRC_ALIAS,))
        fc = json.dumps([{"field": "lv", "role": "measure", "agg": "NONE",
                          "expr": "SUM(c) / SUM(d)"}])
        errors, _ = reg.validate_definition(_src_row(field_config=fc))
        assert any("expr 在 ES 源数据集不可用" in e for e in errors), errors

    def test_available_datasources_must_be_same_engine(self):
        """混进非 ES 源就能被定源选中，结果是拿 mysql 语法跑 ES 正文 → 保存即拒。"""
        catalog = {"cloud-platform-es": {"id": ES_SRC_ID, "name": "cloud-platform-es",
                                        "db_type": "es", "site_code": ""},
                   "prd-cn": {"id": 5, "name": "prd-cn", "db_type": "mysql", "site_code": ""}}
        svc._validate_es_source_scope(ES_SRC_ID, ["cloud-platform-es"], catalog)
        with pytest.raises(ValueError, match="prd-cn"):
            svc._validate_es_source_scope(ES_SRC_ID, ["cloud-platform-es", "prd-cn"], catalog)
