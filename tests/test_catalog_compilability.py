"""目录层与编译层口径一致性（"挂着名字却答不出数"必须显式暴露）。

背景（会话 1783069459140 的根因之一）：目录按 `bound_object_key` 广告指标/维度，
planner 按 `target_table = 对象绑定物理表` 加载字典。两套口径不一致时（ARMS RUM
19 指标 + 28 维度 `target_table` 全空），`get_metrics` 把名字列得齐全，planner 却
一个也加载不到 → 会话表现为「无法解析指标「页面浏览量」…可用指标: []」。
那是**假可用**：对用户承诺了查不出的口径（no-silent-degradation）。

本文件锁定：
1. `dict_unavailable_reason` 的裁决口径（与 planner 的加载条件一一对应，不误伤可编译行）；
2. `binding_scope_for` 的保守回退（查不到绑定 ≠ 不可用）；
3. `get_metrics` 把不可编译项放进 `unavailable`（带原因、带使用约束），不进可用 清单；
4. 对外文案不含物理表名（护栏 §7）；
5. `get_metrics` 单次下发体积受预算约束，超预算显式降级但**不得删对象**（会话 1783069459352：八个泛词
   命中 15 个对象 = 70,598 字符，被运行时静默换成读不回来的文件指针，模型据此断言"平台没有线索对象"）。
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


def _run_get_metrics(monkeypatch, metrics, dims, tables=(ALL_TABLES, BY_OBJECT), binding=None,
                     args=None, return_envelope=False):
    from backend.modules.mind.execution.sdk_tools import semantic_tools as stl

    monkeypatch.setattr(mc, "_get_metrics", lambda ds: metrics)
    monkeypatch.setattr(mc, "_get_dimensions", lambda ds: dims)
    monkeypatch.setattr(mc, "active_binding_tables", binding or (lambda: tables))
    monkeypatch.setattr("backend.semantics.graph_rag.link_graph.object_links", lambda keys: [])
    monkeypatch.setattr("backend.semantics.cross_site.list_deployments", lambda: [])
    token = set_execution_context(ExecutionContext(user_id=7, workspace_id=3, datasource_id=5))
    try:
        out = asyncio.run(stl.get_metrics(args if args is not None else {"keyword": "页面,会话,统计,异常"}))
    finally:
        ExecutionContextVar.reset(token)
    return out if return_envelope else json.loads(out["content"][0]["text"])


def test_get_metrics_rejects_empty_keyword(monkeypatch):
    """空关键词=把整层字典一次下发（会话 1783069459335 实测 63KB 被运行时截断成半本目录）：必显式拒。

    拒绝文案必须给出路（该带什么词、多词一次传、未命中不代表平台没有），
    否则模型只会拿一个"参数错误"去改词瞎试（no-silent-degradation §1）。
    """
    for args in ({}, {"keyword": ""}, {"keyword": "   "}, {"keyword": "[]"}):
        payload = _run_get_metrics(monkeypatch, [METRIC_OK], [DIM_OK], args=args)
        assert "error" in payload, args
        text = payload["error"]
        assert "必须带 keyword" in text and "knowledge_search" in text, text
        assert "一次带齐" in text, text


def test_metric_keywords_accepts_multi_words_and_dedupes():
    """多概念一次传齐（逗号/换行/JSON 数组）：拆成多轮 = 多烧模型往返，且会被预算判超支。"""
    from backend.modules.mind.execution.sdk_tools.semantic_tools import metric_keywords

    assert metric_keywords({"keyword": "页面浏览量, 渠道"}) == ["页面浏览量", "渠道"]
    assert metric_keywords({"keyword": '["页面浏览量","渠道","页面浏览量"]'}) == ["页面浏览量", "渠道"]
    assert metric_keywords({"keyword": "页面\n渠道"}) == ["页面", "渠道"]
    assert metric_keywords({"keyword": "PageViews"}) == ["pageviews"]   # 匹配面小写，入参不必小写


def test_get_metrics_multi_keyword_single_call_unions(monkeypatch):
    """两个概念一次查全：命中集是并集，不必为第二个概念再调一次。"""
    second = {**METRIC_OK, "id": 9, "name": "异常数", "formula": "COUNT(err.id)"}
    payload = _run_get_metrics(monkeypatch, [METRIC_OK, second], [DIM_OK],
                               args={"keyword": "页面浏览量,异常数"})
    names = {m["name"] for o in payload["objects"] for m in o["metrics"]}
    assert names == {"页面浏览量", "异常数"}


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


class TestGetMetricsOutputBudget:
    """单次下发预算：泛词命中十几个对象的全量口径会被运行时静默截断成半本目录。

    会话 1783069459352：keyword 八个泛词 → 15 个对象 = 70,598 字符 → agent 运行时
    整份落盘换成文件指针（且在会话沙箱外读不回）→ 模型手里零目录 → 回答"平台没建
    线索对象"。所以体积必须由工具自己兜，且降级不得删对象（删对象 = 半本目录）。
    """

    @staticmethod
    def _wide_catalog(objects=12, per_object=20):
        metrics, dims = [], []
        i = 0
        for n in range(objects):
            for j in range(per_object):
                i += 1
                metrics.append({
                    "id": i, "name": f"对象{n}指标{j}", "name_en": f"o{n}_metric_{j}",
                    "bound_object_key": f"obj{n}", "target_table": "logstore-rum",
                    "target_column": "", "formula": f"COUNT(x{i}.id)", "formula_dsl": "",
                    "aliases": [f"m{n}_{j}", f"别名{n}{j}"],
                    "description": "口径说明：排除已删除记录，按会话去重后统计", "unit": "个",
                })
                dims.append({
                    "id": i, "name": f"对象{n}维度{j}", "name_en": f"o{n}_dim_{j}",
                    "bound_object_key": f"obj{n}", "target_table": "logstore-rum",
                    "target_column": f"view.col{i}", "category": "文本",
                    "formula": "", "formula_dsl": "",
                    "aliases": [f"d{n}_{j}"],
                    "value_labels": {"0": "甲类", "1": "乙类", "2": "丙类"},
                    "description": "枚举业务含义说明，供业务方直接解读",
                })
        return metrics, dims

    def test_keyword_mode_stays_within_budget_by_topk(self, monkeypatch):
        """条目级 topK 取代整对象补全：泛词最坏情况也不再触发运行时截断。

        历史事故（1783069459352）：命中 15 对象 → 整对象补全 70,598 字符 → 运行时
        整份落盘成读不回来的文件指针。现在发现层按条目下发，体积上限由 top_k 决定。
        """
        from backend.modules.mind.execution.sdk_tools.semantic_tools import (
            _compact_chars, _MAX_METRICS_PAYLOAD_CHARS,
        )

        metrics, dims = self._wide_catalog()
        payload = _run_get_metrics(monkeypatch, metrics, dims,
                                   args={"keyword": "对象", "top_k": 30})
        assert _compact_chars(payload) <= _MAX_METRICS_PAYLOAD_CHARS
        assert payload["search"]["truncated"] is True
        assert payload["search"]["matched_total"] == len(metrics) + len(dims)
        # 只回命中条目，但每对象必须带全量计数（子集不冒充全量）
        assert all(o["metric_total"] == 20 for o in payload["objects"])
        assert sum(len(o["metrics"]) + len(o["dimensions"]) for o in payload["objects"]) == 30

        envelope = _run_get_metrics(monkeypatch, metrics, dims,
                                    args={"keyword": "对象", "top_k": 30}, return_envelope=True)
        assert len(envelope["content"][0]["text"]) <= _MAX_METRICS_PAYLOAD_CHARS

    def test_budget_guard_drops_oversized_side_fields_not_objects(self):
        """relations/deployments 等旁路也能单独撑爆；最小档必须保全部对象 key。"""
        from backend.modules.mind.execution.sdk_tools.semantic_tools import (
            _compact_chars, fit_metrics_payload, _MAX_METRICS_PAYLOAD_CHARS,
        )

        out = {
            "objects": [{"object": f"obj{i}", "metric_total": 2, "dimension_total": 1,
                         "metrics": [{"name": f"指标{i}", "description": "x" * 1000}],
                         "dimensions": []} for i in range(30)],
            "relations": [{"source": "x" * 1000, "target": "y" * 1000} for _ in range(100)],
            "deployments": [{"site": "z" * 1000} for _ in range(100)],
        }
        fitted = fit_metrics_payload(out)
        assert _compact_chars(fitted) <= _MAX_METRICS_PAYLOAD_CHARS
        assert fitted["budget_level"] == "minimal"
        assert len(fitted["objects"]) == 30
        assert set(fitted["omitted_fields"]) == {"relations", "deployments"}

    def test_budget_guard_errors_instead_of_returning_over_limit_index(self):
        """对象多到连最小索引都超限时，显式报错，不返回半本目录。"""
        from backend.modules.mind.execution.sdk_tools.semantic_tools import (
            _compact_chars, fit_metrics_payload,
        )

        out = {"objects": [{"object": f"object_{i}", "metrics": [], "dimensions": []}
                            for i in range(5000)]}
        fitted = fit_metrics_payload(out, budget=500)
        assert fitted["code"] == "TOOL_OUTPUT_BUDGET_EXCEEDED"
        assert fitted["truncated"] is True
        assert _compact_chars(fitted) <= 500

    def test_budget_guard_is_second_line_and_keeps_every_object(self):
        """object 下钻（全量）仍可能超预算：fit_metrics_payload 是二道防线，且不删对象。"""
        from backend.modules.mind.execution.sdk_tools.semantic_tools import (
            _compact_chars, fit_metrics_payload, _MAX_METRICS_PAYLOAD_CHARS,
        )

        out = {"objects": [
            {"object": f"obj{i}", "metric_total": 50, "dimension_total": 0,
             "metrics": [{"name": f"指标{i}_{j}", "aliases": [f"别名{i}_{j}"],
                          "description": "口径说明" * 20} for j in range(50)],
             "dimensions": []} for i in range(30)], "total": 30}
        fitted = fit_metrics_payload(out)
        assert _compact_chars(fitted) <= _MAX_METRICS_PAYLOAD_CHARS
        assert len(fitted["objects"]) == 30          # 不得删对象
        assert fitted["budget_level"] in ("names", "index")
        assert "get_metrics" in (fitted.get("_budget_note") or "")

    def test_within_budget_payload_is_untouched(self, monkeypatch):
        """预算内零变化：既有口径（含 aliases/description/enum）不得被预热性降级。"""
        payload = _run_get_metrics(monkeypatch, [METRIC_OK], [DIM_OK])
        assert "budget_level" not in payload and "_budget_note" not in payload
        assert payload["objects"][0]["metrics"][0]["name"] == "页面浏览量"

    def test_index_level_keeps_counts_and_marks_truncation(self):
        """预算再小也只删明细、不删对象；每个对象标出总数量与已列名称数。"""
        from backend.modules.mind.execution.sdk_tools.semantic_tools import (
            _compact_chars, fit_metrics_payload,
        )

        metrics, dims = self._wide_catalog(objects=6, per_object=10)
        objects: list = []
        for row in metrics:
            objects.append({"object": row["bound_object_key"], "metrics": [], "dimensions": []})
        for row in dims:
            objects.append({"object": row["bound_object_key"], "metrics": [], "dimensions": []})
        buckets = {o["object"]: o for o in objects}
        for row in metrics:
            buckets[row["bound_object_key"]]["metrics"].append(
                {"name": row["name"], "aliases": row["aliases"],
                 "description": row["description"], "unit": row["unit"]})
        for row in dims:
            buckets[row["bound_object_key"]]["dimensions"].append(
                {"name": row["name"], "aliases": row["aliases"],
                 "enum": row["value_labels"], "description": row["description"]})
        out = {"objects": list(buckets.values()), "total": len(buckets), "keywords": ["对象"]}

        fitted = fit_metrics_payload(out, 2500)
        assert _compact_chars(fitted) <= 2500
        assert fitted["budget_level"] == "index"
        assert [o["object"] for o in fitted["objects"]] == [o["object"] for o in out["objects"]]
        for o in fitted["objects"]:
            assert o["metrics"] == 10 and o["dimensions"] == 10
            if len(o["metric_names"]) < o["metrics"]:
                assert "只列前" in o["metric_names_truncated"]

