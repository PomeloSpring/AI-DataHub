"""看板出口（计划 §四出口 4）：看板筛选 → 语义 intent 的回归。

锁四件事：

1. **翻译只有一处**：看板参数值的形状解析复用 `semantics.param_plan`，决策模块
   不自拼 filters、不自己解析日期（那是第二套参数通道，历史上"看着正常其实没筛"
   就出在这种地方）；
2. **筛选真的落到取数上**：intent 里必须带上 filters 与起止区间（与看板图表同一口径）；
3. **不静默换口径**：参数没给值、只给一端、槽位不认识 → 都必须回一条可读说明，
   而不是悄悄按默认口径出数（no-silent-degradation §1）；
4. **缓存不串味**：带筛选与不带筛选的求值不能命中同一条缓存条目。

站点值不进 filters（语义层契约明写"站点不是 SQL 维度"），只回填请求 site，
仍由 site_service 在角色授权集内解析。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.modules.decision import engine, params as dparams, service


# ── 1. 翻译：只搬运 param_plan 的口径 ────────────────────────────

class TestTranslate:
    def test_dimension_value_becomes_semantic_filter(self):
        out = dparams.translate({"region": "华东"}, [
            {"key": "region", "slot": "dimension", "dimension": "区域"}])
        assert out["filters"] == [{"dim": "区域", "op": "eq", "value": "华东"}]
        assert out["notes"] == []

    def test_multi_select_uses_in_operator(self):
        out = dparams.translate({"region": ["华东", "华南"]}, [
            {"key": "region", "slot": "dimension", "dimension": "区域"}])
        assert out["filters"][0]["op"] == "in"
        assert out["filters"][0]["values"] == ["华东", "华南"]

    def test_date_range_becomes_absolute_interval(self):
        out = dparams.translate({"d": {"start": "2026-11-01", "end": "2026-11-06"}}, [
            {"key": "d", "slot": "time_window", "label": "统计区间"}])
        assert out["time_start"] == "2026-11-01" and out["time_end"] == "2026-11-06"
        assert "time_window" not in out, "翻译结果只有一个形状（起止日期），不双轨"

    def test_relative_days_expand_like_the_dashboard_chart(self):
        """“近 30 天”由 param_plan 展开成起止日期，与看板图表拿到的区间逐字一致。

        这里故意不换成 `30d` 走另一套相对窗口：两套口径隔一天就会错一天。
        """
        from datetime import date, timedelta

        out = dparams.translate({"d": "30d"}, [{"key": "d", "slot": "time_window"}])
        today = date.today()
        assert out["time_start"] == (today - timedelta(days=29)).isoformat()
        assert out["time_end"] == today.isoformat()
        assert out["notes"] == []

    def test_site_goes_to_site_not_filters(self):
        out = dparams.translate({"s": "cn"}, [{"key": "s", "slot": "site"}])
        assert out["site"] == "cn" and out["filters"] == []

    def test_missing_value_is_explained_not_silently_ignored(self):
        out = dparams.translate({"region": ""}, [
            {"key": "region", "slot": "dimension", "dimension": "区域", "label": "大区"}])
        assert out["filters"] == []
        assert out["notes"] and "大区" in out["notes"][0] and "未套用" in out["notes"][0]

    def test_half_open_range_is_refused_with_reason(self):
        """只给一端：套上去等于换个范围，必须说不套用（param_plan 同一立场）。"""
        out = dparams.translate({"d": {"start": "2026-11-01"}}, [
            {"key": "d", "slot": "time_window", "label": "统计区间"}])
        assert out["time_start"] == "" and out["time_end"] == ""
        assert any("只给到一端" in n for n in out["notes"])

    def test_unknown_slot_and_missing_dimension_are_named(self):
        out = dparams.translate({"x": "1", "y": "2"}, [
            {"key": "x", "slot": "expression"},
            {"key": "y", "slot": "dimension", "label": "品类"}])
        assert out["filters"] == []
        assert len(out["notes"]) == 2
        assert any("槽位" in n for n in out["notes"])
        assert any("维度业务名" in n for n in out["notes"])

    def test_bad_date_shape_reports_instead_of_guessing(self):
        out = dparams.translate({"d": "去年那阵子"}, [
            {"key": "d", "slot": "time_window", "label": "统计区间"}])
        assert out["time_start"] == "" and out["notes"]

    def test_no_bindings_means_untouched(self):
        out = dparams.translate({"d": "2026-11-01,2026-11-02"}, [])
        assert out == {"filters": [], "time_start": "",
                       "time_end": "", "site": "", "notes": []}


# ── 2. 引擎：翻译结果真的进了唯一执行口的 intent ─────────────────

def _tree(with_window=False):
    leaf = {"type": "leaf", "label": "校准超差", "advice_key": "adv_recalibrate",
            "when": {"metric": "扫描精度偏差均值", "comparator": ">=", "value": 0.05}}
    if with_window:
        leaf["when"]["time_window"] = "7d"
        leaf["when"]["dimension"] = "区域"
    return {"id": 1, "tree_key": "accuracy-risk", "name": "精度风险判定", "version": 2,
            "status": "active", "is_active": 1, "workspace_id": 7, "apply_sites": [],
            "required_metrics": ["扫描精度偏差均值"],
            "tree_doc": {"root": {"type": "branch", "label": "精度判定", "op": "all",
                                  "children": [leaf]}}}


@pytest.fixture
def captured(monkeypatch):
    """拦住语义层唯一执行口，把递出去的 intent 原样留下。"""
    box = {}

    def fake_execute(query, policy):
        box["query"] = query
        from backend.semantics.models import ResultColumn, SemanticResult
        dims = list(query.dimensions or [])
        cols = [*dims, "扫描精度偏差均值"]
        rows = [[("华东" if "区域" in d else 0.08) for d in cols] for _ in range(1)]
        return SemanticResult(columns=[ResultColumn(name=c) for c in cols], rows=rows)

    from backend.semantics import execute as sem_execute
    monkeypatch.setattr(sem_execute, "execute", fake_execute)
    from backend.modules.decision import dictionary as dic
    monkeypatch.setattr(dic, "load_dictionary", lambda datasource_id=0: {
        "metric": [dic.DictHit("metric", "扫描精度偏差均值", "equipment", "dict_exact")],
        "dimension": [dic.DictHit("dimension", "区域", "site", "dict_exact")]})
    return box


class TestEngineConsumesFilters:
    def test_dimension_filter_reaches_intent(self, captured):
        ctx = engine.EvalContext(mode="full", user_id=3, datasource_id=12,
                                 intent_filters=[{"dim": "区域", "op": "eq", "value": "华东"}])
        outcome = engine.evaluate_tree(_tree(), ctx)
        q = captured["query"]
        assert len(q.filters) == 1 and q.filters[0].dim == "区域" and q.filters[0].value == "华东"
        assert outcome.verdict == "hit"

    def test_absolute_interval_overrides_and_is_disclosed(self, captured):
        ctx = engine.EvalContext(mode="full", user_id=3, datasource_id=12,
                                 filter_time_start="2026-11-01", filter_time_end="2026-11-06",
                                 param_notes=["参数「统计区间」没有取值，本次按决策树自身口径评估（未套用该筛选）"])
        outcome = engine.evaluate_tree(_tree(with_window=True), ctx)
        q = captured["query"]
        assert q.time_window is None, "看板给了绝对区间却仍按 7d = 控件拖了没生效"
        assert q.time_start == "2026-11-01" and q.time_end == "2026-11-06"
        assert "看板筛选的日期区间" in outcome.reason, "换了口径必须说出口"
        assert "统计区间" in outcome.reason, "带进来的说明不能被丢掉"
        # 结构化一份：hit 时前端也得看到口径（只看 reason 会把已改口径的建议当全量）
        assert outcome.scope_notes and any("日期区间" in n for n in outcome.scope_notes)
        assert any("统计区间" in n for n in outcome.scope_notes)

    def test_absolute_interval_applies_without_note_when_tree_has_no_window(self, captured):
        ctx = engine.EvalContext(mode="full", user_id=3, datasource_id=12,
                                 filter_time_start="2026-11-01", filter_time_end="2026-11-06")
        outcome = engine.evaluate_tree(_tree(), ctx)
        q = captured["query"]
        assert (q.time_start, q.time_end) == ("2026-11-01", "2026-11-06")
        assert outcome.scope_notes == [], "没换口径也不该虚构一句说明"
        assert "筛选参数" not in (outcome.reason or "")

    def test_turn_mode_does_not_fetch_so_filters_are_irrelevant(self, captured):
        """看板筛选只在 full 下取数；turn 出口不补取数（首答零额外取数的口径不动）。"""
        ctx = engine.EvalContext(mode="turn", user_id=3, turn_columns=["扫描精度偏差均值"],
                                 turn_rows=[[0.09]], intent_filters=[{"dim": "区域", "op": "eq", "value": "华东"}])
        outcome = engine.evaluate_tree(_tree(), ctx)
        assert outcome.verdict == "hit"
        assert "query" not in captured, "turn 模式不该打语义层"


# ── 3. 缓存键随筛选变化（不带筛选的结果不能冒充筛选后的）────────

class TestCacheKeyFollowsFilters:
    def _ctx(self, **kw):
        return engine.EvalContext(mode="full", user_id=3, datasource_id=12, **kw)

    def test_key_differs_when_filtered(self):
        row = _tree()
        plain = service._eval_cache_key(row, self._ctx())
        dim = service._eval_cache_key(row, self._ctx(intent_filters=[{"dim": "区域", "op": "eq", "value": "华东"}]))
        rng = service._eval_cache_key(row, self._ctx(filter_time_start="2026-11-01", filter_time_end="2026-11-06"))
        assert plain != dim != rng and plain != rng

    def test_same_filters_same_key_and_site_still_separated(self):
        a = service._eval_cache_key(_tree(), self._ctx(filter_time_start="2026-11-01",
                                                       filter_time_end="2026-11-30"))
        b = service._eval_cache_key(_tree(), self._ctx(filter_time_start="2026-11-01",
                                                       filter_time_end="2026-11-30"))
        c = service._eval_cache_key(_tree(), self._ctx(filter_time_start="2026-11-01",
                                                       filter_time_end="2026-11-30", site_code="cn"))
        assert a == b and a != c

    def test_filter_signature_is_stable_string(self):
        ctx = self._ctx(intent_filters=[{"dim": "区域", "op": "in", "values": ["华东", "华南"]}])
        assert "区域:in" in ctx.filter_signature() and "华东" in ctx.filter_signature()


# ── 4. API 侧：绑定进上下文，且实例归因落 dashboard ──────────────

class TestApiWiring:
    def test_ctx_from_applies_bindings_and_marks_dashboard(self, monkeypatch):
        from backend.modules.decision.api import decisions as api

        monkeypatch.setattr(api, "_resolve_identity", lambda user, ws, site="": (12, "cn"))
        ctx = api._ctx_from({"user_id": 3, "username": "u", "role": "ops"}, 7,
                            params={"region": "华东", "s": "cn"},
                            param_bindings=[{"key": "region", "slot": "dimension", "dimension": "区域"},
                                            {"key": "s", "slot": "site"}],
                            trigger_type="dashboard")
        assert ctx.intent_filters == [{"dim": "区域", "op": "eq", "value": "华东"}]
        assert ctx.resolved_trigger() == "dashboard", "看板求值在命中历史里得是自己的出口"

    def test_turn_mode_ignores_bindings(self, monkeypatch):
        from backend.modules.decision.api import decisions as api

        monkeypatch.setattr(api, "_resolve_identity", lambda user, ws, site="": (12, ""))
        ctx = api._ctx_from({"user_id": 3}, 7, mode="turn", params={"region": "华东"},
                            param_bindings=[{"key": "region", "slot": "dimension", "dimension": "区域"}])
        assert ctx.intent_filters == [] and ctx.param_notes == []

    def test_evaluate_request_accepts_params_without_extra_fields(self):
        """extra=forbid 之下，看板只会发这两个新键；老调用方不传也照常工作。"""
        from backend.modules.decision.api import decisions as api

        old = api.EvaluateRequest(workspace_id=7)
        assert old.params == {} and old.param_bindings == []
        new = api.EvaluateRequest(workspace_id=7, params={"d": "30d"},
                                  param_bindings=[{"key": "d", "slot": "time_window"}])
        assert new.param_bindings[0]["slot"] == "time_window"


# ── 5. “没填”与“递错了形状”分开说，且不共用一条缓存 ──────────────
# 2026-11-12 端到端核验发现：参数值递成数组时 param_plan 回三个 None，旧文案
# 一律说成“被清空”（会让人以为是自己日期没选），而 notes 不进缓存键时
# 这句说明还会被上一次全量求值的命中顶掉（带筛选问、拿回全量数且一声不吐）。

class TestUnrecognizedParamShape:
    def test_list_shaped_range_says_shape_is_unrecognized(self):
        out = dparams.translate({"range": ["2026-11-01", "2026-11-08"]},
                                [{"key": "range", "slot": "time_window", "label": "统计区间"}])
        assert out["time_start"] == "" and out["time_end"] == ""
        assert len(out["notes"]) == 1
        assert "认不出来" in out["notes"][0] and "被清空" not in out["notes"][0]

    def test_empty_value_still_says_no_value(self):
        out = dparams.translate({"range": "   "},
                                [{"key": "range", "slot": "time_window", "label": "统计区间"}])
        assert out["notes"] == ["参数「统计区间」没有取值，本次按决策树自身口径评估（未套用该筛选）"]

    def test_object_shaped_range_is_applied(self):
        out = dparams.translate({"range": {"start": "2026-11-01", "end": "2026-11-08"}},
                                [{"key": "range", "slot": "time_window", "label": "统计区间"}])
        assert (out["time_start"], out["time_end"]) == ("2026-11-01", "2026-11-08")
        assert out["notes"] == []

    def test_unapplied_param_note_also_changes_the_cache_key(self):
        row = _tree()
        base = dict(mode="full", user_id=3, datasource_id=12)
        silent = service._eval_cache_key(row, engine.EvalContext(**base))
        noisy = service._eval_cache_key(row, engine.EvalContext(
            **base, param_notes=["参数「统计区间」的取值形态认不出来"]))
        assert silent != noisy, "没套用的说明也被缓存吃掉 = 带筛选问拿回全量数还一声不吐"
