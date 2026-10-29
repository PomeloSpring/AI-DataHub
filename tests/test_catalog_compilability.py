"""目录层与编译层口径一致性（"挂着名字却答不出数"必须显式暴露）。

背景（会话 1783069459140 的根因之一）：目录按 `bound_object_key` 广告指标/维度，
planner 按 `target_table = 对象绑定物理表` 加载字典。两套口径不一致时（ARMS RUM
19 指标 + 28 维度 `target_table` 全空），`get_metrics` 把名字列得齐全，planner 却
一个也加载不到 → 会话表现为「无法解析指标「页面浏览量」…可用指标: []」。
那是**假可用**：对用户承诺了查不出的口径（no-silent-degradation）。

本文件锁定：
1. `dict_unavailable_reason` 的裁决口径（与 planner 的加载条件一一对应，不误伤可编译行）；
2. `binding_scope_for` 的保守回退（查不到绑定 ≠ 不可用）；
3. `get_metrics` 把不可编译项放进 `unavailable`（带原因、带使用约束），不进可用清单；
4. 对外文案不含物理表名（护栏 §7）。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.mind.execution.models import ExecutionContext  # noqa: E402
from backend.modules.mind.execution.sdk_tools.context import (  # noqa: E402
    ExecutionContextVar, set_execution_context,
)
from backend.semantics import mdl_compiler as mc  # noqa: E402

ALL_TABLES = {"logstore-rum", "t_case_records"}
BY_OBJECT = {"rum_event": {"logstore-rum"}, "case": {"t_case_records"}}

METRIC_OK = {"id": 1, "name": "页面浏览量", "bound_object_key": "rum_event",
             "target_table": "logstore-rum", "target_column": "",
             "formula": "COUNT(view.id)", "formula_dsl": ""}
DIM_OK = {"id": 2, "name": "页面URL", "bound_object_key": "rum_event",
          "target_table": "logstore-rum", "target_column": "view.url",
          "category": "文本", "formula": "", "formula_dsl": ""}


class TestDictUnavailableReason:
    def test_compilable_metric_and_dim_pass(self):
        assert mc.dict_unavailable_reason(METRIC_OK, ALL_TABLES, kind="metric") == ""
        assert mc.dict_unavailable_reason(DIM_OK, ALL_TABLES, kind="dimension") == ""

    def test_metric_without_target_table_is_blocked(self):
        row = {**METRIC_OK, "target_table": ""}
        reason = mc.dict_unavailable_reason(row, ALL_TABLES, kind="metric")
        assert "物理表" in reason and "不可取数" in reason

    def test_dim_needs_table_and_column(self):
        assert "物理列" in mc.dict_unavailable_reason(
            {**DIM_OK, "target_column": ""}, ALL_TABLES, kind="dimension")
        assert mc.dict_unavailable_reason(
            {**DIM_OK, "target_table": ""}, ALL_TABLES, kind="dimension")

    def test_metric_without_formula_is_blocked(self):
        row = {**METRIC_OK, "formula": "", "formula_dsl": ""}
        assert "公式" in mc.dict_unavailable_reason(row, ALL_TABLES, kind="metric")

    def test_table_without_active_binding_is_blocked(self):
        row = {**METRIC_OK, "target_table": "orders"}
        reason = mc.dict_unavailable_reason(row, ALL_TABLES, kind="metric")
        assert "部署绑定" in reason
        # §7：原因文案不回显物理表名
        assert "orders" not in reason

    def test_empty_binding_scope_only_does_null_checks(self):
        """绑定层查不到（空集合）时不得把正常行判成不可用 —— 那是新的假告警。"""
        assert mc.dict_unavailable_reason(METRIC_OK, set(), kind="metric") == ""
        assert mc.dict_unavailable_reason(DIM_OK, set(), kind="dimension") == ""


class TestBindingScope:
    def test_object_scope_preferred(self):
        assert mc.binding_scope_for(METRIC_OK, ALL_TABLES, BY_OBJECT) == {"logstore-rum"}

    def test_falls_back_to_all_tables_when_object_unknown(self):
        row = {**METRIC_OK, "bound_object_key": "no_such_object"}
        assert mc.binding_scope_for(row, ALL_TABLES, BY_OBJECT) == ALL_TABLES

    def test_object_key_case_variant_still_resolves(self):
        assert mc.binding_scope_for({**METRIC_OK, "bound_object_key": "RUM_Event"},
                                    ALL_TABLES, BY_OBJECT) == {"logstore-rum"}

    def test_wrong_table_for_own_object_is_blocked(self):
        """归属对象的表集合内没有 target_table = planner 必然加载不到（比全局集合更严）。"""
        row = {**METRIC_OK, "target_table": "t_case_records"}
        scope = mc.binding_scope_for(row, ALL_TABLES, BY_OBJECT)
        assert "部署绑定" in mc.dict_unavailable_reason(row, scope, kind="metric")


def _run_get_metrics(monkeypatch, metrics, dims, tables=(ALL_TABLES, BY_OBJECT), binding=None):
    from backend.modules.mind.execution.sdk_tools import semantic_tools as stl

    monkeypatch.setattr(mc, "_get_metrics", lambda ds: metrics)
    monkeypatch.setattr(mc, "_get_dimensions", lambda ds: dims)
    monkeypatch.setattr(mc, "active_binding_tables", binding or (lambda: tables))
    monkeypatch.setattr("backend.semantics.graph_rag.link_graph.object_links", lambda keys: [])
    monkeypatch.setattr("backend.semantics.cross_site.list_deployments", lambda: [])
    token = set_execution_context(ExecutionContext(user_id=7, workspace_id=3, datasource_id=5))
    try:
        out = asyncio.run(stl.get_metrics({}))
    finally:
        ExecutionContextVar.reset(token)
    return json.loads(out["content"][0]["text"])


def test_get_metrics_moves_uncompilable_rows_into_unavailable(monkeypatch):
    bad_metric = {**METRIC_OK, "id": 9, "name": "会话平均时长(ms)", "target_table": ""}
    bad_dim = {**DIM_OK, "id": 8, "name": "统计日期", "target_column": ""}
    payload = _run_get_metrics(monkeypatch, [METRIC_OK, bad_metric], [DIM_OK, bad_dim])

    names = [m["name"] for o in payload["objects"] for m in o["metrics"]]
    dims = [d["name"] for o in payload["objects"] for d in o["dimensions"]]
    assert names == ["页面浏览量"] and dims == ["页面URL"]
    blocked = {(b["name"], b["kind"]) for b in payload["unavailable"]}
    assert blocked == {("会话平均时长(ms)", "metric"), ("统计日期", "dimension")}
    assert all(b["reason"] for b in payload["unavailable"])
    # 使用约束必须写进返回体：不得拿 unavailable 里的口径向用户承诺，也不得自行换指标
    assert "不得当作可查口径向用户承诺" in payload["unavailable_note"]


def test_get_metrics_no_unavailable_key_when_all_compilable(monkeypatch):
    payload = _run_get_metrics(monkeypatch, [METRIC_OK], [DIM_OK])
    assert "unavailable" not in payload
    assert payload["objects"][0]["object"] == "rum_event"


def test_get_metrics_binding_load_failure_degrades_to_null_checks_only(monkeypatch):
    """绑定层读取失败时只做空值裁决：正常行不得被误判不可用（假告警也是缺陷）。"""
    def _boom():
        raise RuntimeError("bindings unavailable")
    payload = _run_get_metrics(
        monkeypatch, [{**METRIC_OK, "target_table": "orders"}], [DIM_OK], binding=_boom)
    assert "unavailable" not in payload
    assert [m["name"] for o in payload["objects"] for m in o["metrics"]] == ["页面浏览量"]


def test_get_metrics_output_never_leaks_physical_names(monkeypatch):
    bad = {**METRIC_OK, "id": 9, "name": "异常数", "target_table": "secret_table"}
    payload = _run_get_metrics(monkeypatch, [METRIC_OK, bad], [DIM_OK], tables=({"other_table"}, {}))
    text = json.dumps(payload, ensure_ascii=False)
    for leak in ("secret_table", "other_table", "logstore-rum", "view.url", "target_table"):
        assert leak not in text
