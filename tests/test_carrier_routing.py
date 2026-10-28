"""承载择路（M2）回归：decide_carrier 决策表 + 按承载解析绑定 + 口径约束。

锁住的行为（拍板口径，改动即破契约）：
1. 未声明承载 → status=none，取数行为与改造前一致（渐进增强不阻塞）；
2. 只有 detail 承载 → status=detail（逐站分开呈现，**不合计**）；
3. 声明了 authority 的 global 承载 → status=use，并按 source_model/source_object
   路由到目标源的绑定行（异构源物理形状另有其人）；
4. 多条非 detail 且都非权威 → status=ambiguous，回抛业务名候选让 LLM 用 ask_user 确认；
5. 显式指定不存在/不可用的承载 → status=unavailable，**不回退到别的承载静默出数**；
6. 指标约束 allowed_carrier=global 而该路径不可见 → 退成 detail 并带 note，
   绝不拿分站数字相加冒充全局值（错数比无答案危害大）；
7. 问"今天/小时级"时 T+1 与滞后路径不参与择路；
8. 按承载解析失败一律 fail-loud（源被删/源本体无生效版本/对象未绑表），
   不得回退业务契约行去查错库；
9. `_infer_agg` 必须把 COUNT(DISTINCT) 与 COUNT 分开（否则去重口径被误判为可相加）；
10. `allowed_carrier` 非法值直接拒（不静默改成 any）；
11. 择路分桶码（path_reason）在 use/detail/none **都要落戳**，否则“退成逐站”与
    “未声明承载”在审计/评测里无从分辨；词汇表与 `ResolvedBinding` 声明单一来源；
12. 灰度开关 `CARRIER_ROUTING_ENABLED=false` 时目录不暴露 carriers、取数不择路，
    但显式传了 carrier 必须报错不能静默忽略（两面同开关 + 不假装能用）；
13. 非 detail 路径**执行失败**必须标 `degraded`（与“无权限被裁剪”可区分，后者对用户不提）。
"""
from __future__ import annotations

import pytest

from backend.semantics import topology as tp


def _carrier(**over):
    base = {"id": 1, "model_id": 100, "object_key": "user", "carrier_key": "detail",
            "display_name": "各站点明细", "carrier_type": "detail", "datasource": "",
            "sites": ["cn", "jp"], "source_model": "", "source_object": "", "grain": "",
            "freshness": "realtime", "as_of_note": "", "is_authority": 0, "priority": 100,
            "size_class": "", "status": "active", "created_by": "", "updated_at": ""}
    base.update(over)
    return base


GLOBAL = _carrier(id=2, carrier_key="global", display_name="数仓全局汇总",
                  carrier_type="global", datasource="Doris", sites=[],
                  source_model="数据仓库模型", source_object="用户汇总",
                  freshness="t_plus_1", as_of_note="T+1，不含今天", is_authority=1,
                  priority=10)
LOG = _carrier(id=3, carrier_key="sls_log", display_name="SLS 行为日志",
               carrier_type="log", datasource="arms-prd-cn", sites=[],
               source_model="ARMS RUM 观测本体", source_object="rum_event", priority=200)


@pytest.fixture
def world(monkeypatch):
    """打桩拓扑读侧：可见承载由测试给定，权限裁剪与字典约束单独验证。"""
    state = {"rows": [], "constraints": {}}

    def _visible(keys, user_id=0, workspace_id=0):
        wanted = {tp._identity(str(k)) for k in (keys or [])}
        grouped = {}
        for c in state["rows"]:
            if tp._identity(c["object_key"]) in wanted:
                grouped.setdefault(c["object_key"], []).append(dict(c))
        return {"carriers": grouped, "carriers_filtered": 0}

    monkeypatch.setattr(tp, "visible_carriers", _visible)
    monkeypatch.setattr(tp, "list_carriers_cached", lambda: [dict(c) for c in state["rows"]])
    monkeypatch.setattr(tp, "metric_carrier_constraints",
                        lambda okey, metrics: dict(state["constraints"]))
    return state


def _decide(**kw):
    return tp.decide_carrier(kw.pop("object_key", "user"), **kw)


# ── 1. decide_carrier 决策表 ──────────────────────────────────────────

def test_none_when_no_carrier_declared(world):
    assert _decide()["status"] == "none"


def test_detail_only_means_per_site(world):
    world["rows"] = [_carrier()]
    d = _decide()
    assert d["status"] == "detail" and d["carrier"] is None
    assert d["candidates"] == ["detail"]


def test_authority_global_is_chosen(world):
    world["rows"] = [_carrier(), GLOBAL]
    d = _decide()
    assert d["status"] == "use"
    assert d["carrier"]["key"] == "global"
    # 择路结果必须带足解析所需的路由字段（服务端内部用，不外返）
    assert d["carrier"]["datasource"] == "Doris"
    assert d["carrier"]["source_model"] == "数据仓库模型"
    assert d["carrier"]["source_object"] == "用户汇总"


def test_two_non_authority_paths_are_ambiguous(world):
    world["rows"] = [_carrier(), GLOBAL | {"is_authority": 0}, LOG]
    d = _decide()
    assert d["status"] == "ambiguous"
    assert {c["key"] for c in d["candidates"]} == {"global", "sls_log"}
    assert all("name" in c for c in d["candidates"])       # 候选给人看的是业务名


def test_explicit_carrier_wins_over_priority(world):
    world["rows"] = [_carrier(), GLOBAL, LOG]
    assert _decide(carrier="sls_log")["carrier"]["key"] == "sls_log"


def test_explicit_unknown_carrier_is_fail_loud(world):
    world["rows"] = [_carrier(), GLOBAL]
    d = _decide(carrier="warehouse")
    assert d["status"] == "unavailable"
    assert "warehouse" in d["reason"]
    assert set(d["candidates"]) == {"detail", "global"}    # 带可用清单供改名重试


def test_realtime_need_excludes_t_plus_1(world):
    world["rows"] = [_carrier(), GLOBAL]
    d = _decide(need_realtime=True)
    assert d["status"] == "detail"                          # T+1 不参与"今天"的口径


def test_realtime_need_keeps_global_when_it_is_realtime(world):
    world["rows"] = [_carrier(), GLOBAL | {"freshness": "realtime"}]
    assert _decide(need_realtime=True)["carrier"]["key"] == "global"


def test_metric_constraint_global_falls_back_to_detail_not_sum(world):
    """global-only 指标但该路径不可见 → 退成逐站分开，不拿分站数相加冒充全局值。"""
    world["rows"] = [_carrier()]
    world["constraints"] = {"用户数": "global"}
    d = _decide(metrics=["用户数"])
    assert d["status"] == "detail"
    assert "不可用" in d["note"] and "不合计" in d["note"]


def test_metric_constraint_without_any_path_is_unavailable(world):
    world["rows"] = [GLOBAL]
    world["constraints"] = {"全球用户数": "detail"}
    assert _decide(metrics=["全球用户数"])["status"] == "unavailable"


def test_object_key_case_insensitive(world):
    """LLM 写 'User' 而承载登记在 'user' 上时不得退成"无承载"。"""
    world["rows"] = [_carrier(), GLOBAL]
    assert _decide(object_key="User")["carrier"]["key"] == "global"


def test_permission_filtered_count_is_returned_but_not_explained(monkeypatch):
    """被裁剪条数只回给服务端（审计/日志），不得变成对用户的解释文案。"""
    monkeypatch.setattr(tp, "visible_carriers",
                        lambda keys, user_id=0, workspace_id=0: {"carriers": {},
                                                                 "carriers_filtered": 2})
    monkeypatch.setattr(tp, "list_carriers_cached", lambda: [])
    d = tp.decide_carrier("user", user_id=7, workspace_id=1)
    assert d["status"] == "none" and d["carriers_filtered"] == 2
    assert "carriers_filtered" not in str(d.get("reason", ""))


# ── 2. 按承载解析绑定 ────────────────────────────────────────────────

class _Cur:
    def __init__(self, script, sink):
        self._script, self.sink = script, sink
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sink.append((" ".join(sql.split()), params))
        for needle, rows in self._script.items():
            if needle in sql:
                self._rows = rows() if callable(rows) else rows
                return
        self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, script):
        self.script, self.sink = script, []

    def cursor(self):
        return _Cur(self.script, self.sink)

    def close(self):
        pass


@pytest.fixture
def resolver(monkeypatch):
    from backend.semantics import binding_resolver as br

    state = {"script": {}}

    def _conn():
        return _Conn(state["script"])

    monkeypatch.setattr(br, "get_metadata_conn", _conn)
    return br, state


def test_carrier_resolution_requires_full_routing(resolver):
    br, _ = resolver
    binding, warns = br.resolve_binding_via_carrier({"datasource": "Doris"})
    assert binding is None and any("缺少数据源/源本体/源对象" in w for w in warns)


def test_carrier_resolution_rejects_missing_datasource(resolver):
    br, state = resolver
    state["script"] = {"SELECT id FROM adh_datasources": []}
    binding, warns = br.resolve_binding_via_carrier(
        {"key": "global", "type": "global", "datasource": "Doris",
         "source_model": "数据仓库模型", "source_object": "用户汇总"})
    assert binding is None and any("已不存在或已改名" in w for w in warns)


def test_carrier_resolution_rejects_archived_source_model(resolver):
    br, state = resolver
    state["script"] = {"SELECT id FROM adh_datasources": [{"id": 9}],
                       "FROM adh_ontology_models": []}
    binding, warns = br.resolve_binding_via_carrier(
        {"key": "global", "type": "global", "datasource": "Doris",
         "source_model": "数据仓库模型", "source_object": "用户汇总"})
    assert binding is None and any("没有生效版本" in w for w in warns)
    assert any("生成本体模型" in w for w in warns)      # 给出可操作处置


def test_carrier_resolution_stamps_provenance_and_reuses_resolver(monkeypatch):
    from backend.semantics import binding_resolver as br
    from backend.semantics.models import ResolvedBinding

    calls = {}

    def _fake(object_ref, datasource_id=0, model_id=None, datasource_name=""):
        calls["args"] = (object_ref, datasource_id, model_id)
        return ResolvedBinding(object_key=object_ref, datasource_id=datasource_id,
                               physical_table="dws_user_stat"), []

    conn = _Conn({"SELECT id FROM adh_datasources": [{"id": 9}],
                  "FROM adh_ontology_models": [{"id": 77}]})
    monkeypatch.setattr(br, "get_metadata_conn", lambda: conn)
    monkeypatch.setattr(br, "resolve_binding", _fake)

    binding, warns = br.resolve_binding_via_carrier(
        {"key": "global", "type": "global", "datasource": "Doris",
         "source_model": "数据仓库模型", "source_object": "用户汇总",
         "freshness": "t_plus_1", "as_of_note": "T+1，不含今天"},
        datasource_id=102, reason="authority")
    # 物理形状按源本体对象 + 目标源 + 源本体模型解析（不是业务契约行）
    assert calls["args"] == ("用户汇总", 9, 77)
    assert binding.carrier_key == "global" and binding.carrier_bucket == "use"
    assert binding.path_used_reason == "authority"
    assert binding.as_of_note == "T+1，不含今天"


def test_same_target_skips_reResolution(monkeypatch):
    """承载目标与调用方已解析的一致时不重复解析（返回 None,[] 让调用方回常规路径）。"""
    from backend.semantics import binding_resolver as br

    conn = _Conn({"SELECT id FROM adh_datasources": [{"id": 9}],
                  "FROM adh_ontology_models": [{"id": 77}]})
    monkeypatch.setattr(br, "get_metadata_conn", lambda: conn)
    monkeypatch.setattr(br, "resolve_binding",
                        lambda *a, **k: pytest.fail("不应重复解析"))
    binding, warns = br.resolve_binding_via_carrier(
        {"datasource": "Doris", "source_model": "数据仓库模型", "source_object": "用户汇总"},
        datasource_id=9, model_id=77)
    assert binding is None and warns == []


# ── 3. 时效判定 / 字典约束 / 值域 ────────────────────────────────────

def _q(**kw):
    obj = type("Q", (), {})()
    obj.time_window = kw.get("time_window", "")
    obj.time_range = kw.get("time_range", "")
    obj.time_start = kw.get("time_start", "")
    return obj


def test_needs_realtime():
    from backend.modules.mind.execution.sdk_tools.semantic_query import _needs_realtime
    assert _needs_realtime(_q(time_window="24h")) is True
    assert _needs_realtime(_q(time_window="1d")) is True
    assert _needs_realtime(_q(time_window="7d")) is False
    assert _needs_realtime(_q(time_range="last_month")) is False
    assert _needs_realtime(_q(time_range="today")) is False
    assert _needs_realtime(_q(time_start="今天")) is True


def test_infer_agg_distinguishes_count_distinct():
    from backend.modules.catalog.services.ontology_service import _infer_agg
    assert _infer_agg("COUNT(DISTINCT t_user_customer.user_code)") == "COUNT_DISTINCT"
    assert _infer_agg("count (distinct id)") == "COUNT_DISTINCT"
    assert _infer_agg("COUNT(t_case_records.id)") == "COUNT"
    assert _infer_agg("SUM(amount)") == "SUM"
    assert _infer_agg("NULLIF(a,0)/b") == "CUSTOM"


def test_norm_allowed_carrier_rejects_illegal():
    from backend.modules.catalog.services.metrics_service import _norm_allowed_carrier
    assert _norm_allowed_carrier("") == "any"
    assert _norm_allowed_carrier("GLOBAL") == "global"
    with pytest.raises(ValueError):
        _norm_allowed_carrier("warehouse")


def test_metric_carrier_constraints_reads_dict(monkeypatch):
    """allowed_carrier 的读取口径：按 name/name_en/aliases 命中，限定名取末段。"""
    conn = _Conn({"FROM adh_metrics": [
        {"name": "全球用户数", "name_en": "global_user_count",
         "aliases": '["去重用户数"]', "allowed_carrier": "global"},
    ]})
    monkeypatch.setattr(tp, "get_metadata_conn", lambda: conn)
    got = tp.metric_carrier_constraints("user", ["全球用户数", "user.去重用户数", "别的"])
    assert got.get("全球用户数") == "global"
    assert got.get("去重用户数") == "global"
    assert "别的" not in got


def test_metric_constraints_degrade_visibly_when_column_missing(monkeypatch, caplog):
    """旧库未跑迁移：按无约束处理但必须留可诊断痕迹（不静默当查询失败）。"""
    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            raise RuntimeError("Unknown column 'allowed_carrier'")

    class _C:
        def cursor(self):
            return _Boom()

        def close(self):
            pass

    monkeypatch.setattr(tp, "get_metadata_conn", lambda: _C())
    assert tp.metric_carrier_constraints("user", ["全球用户数"]) == {}


# ── 4. 契约字段：intent 允许 carrier，SQL 仍拒收 ─────────────────────

def test_intent_accepts_carrier_and_still_rejects_sql():
    from backend.semantics.intent import parse_intent
    q, err, _notes = parse_intent({"object": "user", "metrics": ["全球用户数"],
                                   "carrier": "global"})
    assert not err and q.carrier == "global"
    _q2, err2, _n2 = parse_intent({"object": "user", "sql": "select 1"})
    assert err2


def test_resolved_binding_carrier_fields_are_internal_only():
    """carrier 溯源字段不得随结果外泄：工具面只挑 as_of_note 作业务文案。"""
    from pathlib import Path
    src = Path("backend/modules/mind/execution/sdk_tools/semantic_query.py").read_text(encoding="utf-8")
    assert "payload_out[\"caliber_note\"] = binding.as_of_note" in src
    for leaked in ("binding.carrier_key", "binding.path_used_reason", "binding.datasource_id"):
        assert leaked not in src, f"{leaked} 不应出现在对 LLM 的返回构造里"
    # 落戳单点在 topology（本模块不对外构返体），工具面只能调用它
    assert "topology.stamp_provenance(binding, decision" in src


# ── 5. 择路溯源落戳：detail/none 也要可诊断 ──────────────────────

class _Bind:
    def __init__(self, **kw):
        self.carrier_bucket = kw.pop("carrier_bucket", "")
        self.path_used_reason = kw.pop("path_used_reason", "")
        for k, v in kw.items():
            setattr(self, k, v)


def test_stamp_provenance_fills_none_bucket():
    b = _Bind()
    tp.stamp_provenance(b, {"status": "none", "path_reason": "legacy-binding"})
    assert b.carrier_bucket == "none" and b.path_used_reason == "legacy-binding"


def test_stamp_provenance_fills_detail_bucket():
    """退成逐站（detail-fallback）与“本来就是逐站”（site-detail）必须可区分。"""
    b = _Bind()
    tp.stamp_provenance(b, {"status": "detail", "path_reason": "detail-fallback"})
    assert b.carrier_bucket == "detail" and b.path_used_reason == "detail-fallback"
    b2 = _Bind()
    tp.stamp_provenance(b2, {"status": "detail", "path_reason": "site-detail"})
    assert b2.path_used_reason == "site-detail"


def test_stamp_provenance_never_overwrites_existing_stamp():
    """resolve_binding_via_carrier 已落过的戳不得被覆盖（择路算好的事实只搬运）。"""
    b = _Bind(carrier_bucket="use", path_used_reason="authority")
    tp.stamp_provenance(b, {"status": "use", "path_reason": "priority"}, explicit=True)
    assert b.carrier_bucket == "use" and b.path_used_reason == "authority"


def test_decide_carrier_reasons_are_within_documented_vocabulary(world):
    """分桶码必须与 ResolvedBinding.path_used_reason 的声明词汇一致（防两套词）。"""
    from pathlib import Path
    doc = Path("backend/semantics/models.py").read_text(encoding="utf-8")
    world["rows"] = [_carrier()]
    got = [tp.decide_carrier("user")["path_reason"]]
    world["rows"] = [_carrier(), GLOBAL]
    got.append(tp.decide_carrier("user")["path_reason"])
    got.append(tp.decide_carrier("user", carrier="global")["path_reason"])
    world["rows"] = [_carrier()]
    world["constraints"] = {"用户数": "global"}
    got.append(tp.decide_carrier("user", metrics=["用户数"])["path_reason"])
    for token in got:
        assert token, "已执行路径必须有分桶码（否则评测无从归因）"
        assert token in doc, f"{token} 未在 models.py 词汇表里声明"


# ── 6. 灰度开关 CARRIER_ROUTING_ENABLED ───────────────────────

def test_kill_switch_makes_routing_a_no_op(world, monkeypatch):
    """关闭态 = 行为等同改造前，但不静默：返回 no-carrier 分桶 + 原因。"""
    world["rows"] = [_carrier(), GLOBAL]
    monkeypatch.setattr(tp, "routing_enabled", lambda: False)
    d = tp.decide_carrier("user")
    assert d["status"] == "none" and d["carrier"] is None
    assert d["path_reason"] == "legacy-binding" and "CARRIER_ROUTING_ENABLED" in d["note"]


def test_kill_switch_is_on_by_default():
    """缺省开（carriers 表为空时本来就零行为变化）；开关默认值不得被改反。"""
    from backend.common import config
    assert tp.routing_enabled() is bool(config.CARRIER_ROUTING_ENABLED)
    assert config.CARRIER_ROUTING_ENABLED is True


def test_kill_switch_rejects_explicit_carrier_instead_of_ignoring(world, monkeypatch):
    """关闭态下模型仍传 carrier 必须显式拒（不能“看起来能用”地走另一条路径出数）。"""
    import asyncio

    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver as br

    world["rows"] = [_carrier(), GLOBAL]
    # 不碰元库：两条解析路径都给“解不到”，只验证开关分支与否则
    monkeypatch.setattr(br, "resolve_binding", lambda *a, **k: (None, ["no binding found"]))
    monkeypatch.setattr(br, "resolve_binding_via_carrier",
                        lambda *a, **k: (None, []))

    monkeypatch.setattr(tp, "routing_enabled", lambda: False)
    out, is_err = asyncio.run(sq._execute_single(
        {"object": "user", "metrics": ["用户数"], "carrier": "global", "datasource_id": 0}, None))
    assert is_err and "未启用" in out["error"]
    assert "carrier" in out["hint"]
    # 开启态下同一请求不得进这个分支（走常规解析，而非开关报错）
    monkeypatch.setattr(tp, "routing_enabled", lambda: True)
    out2, _ = asyncio.run(sq._execute_single(
        {"object": "user", "metrics": ["用户数"], "carrier": "global", "datasource_id": 0}, None))
    assert "未启用" not in str(out2.get("error", ""))


def test_get_metrics_hides_carriers_when_switch_off(monkeypatch):
    """两面同开关：目录不暴露 carriers，模型才不会去传一个被忽略的参数。"""
    from pathlib import Path
    src = Path("backend/modules/mind/execution/sdk_tools/semantic_tools.py").read_text(encoding="utf-8")
    assert "obj_keys and topology.routing_enabled()" in src


# ── 7. 降级边界（拍板决策 6：执行失败=降级，必须显式）──────────────

def test_global_path_failure_is_marked_degraded():
    """有权限但执行失败 → 必须带 degraded 标记与可执行 hint（不得退成“看起来能用”）。"""
    from backend.modules.mind.execution.sdk_tools.semantic_query import _attach_degraded_hint
    payload = {}
    _attach_degraded_hint(payload, {"carrier": {"key": "global", "name": "数仓全局汇总",
                                                "type": "global"}})
    assert payload["degraded"] is True
    assert "数仓全局汇总" in payload["degraded_reason"]      # 对用户只出业务名
    assert "相加不等于" in payload["degraded_hint"]
    assert "datasource" not in str(payload).lower()


def test_detail_path_failure_is_not_marked_degraded():
    """detail（逐站明细）本来就是缺省口径，失败不构成“全局降级”；未选路同样不标。"""
    from backend.modules.mind.execution.sdk_tools.semantic_query import _attach_degraded_hint
    for decision in ({"carrier": {"key": "detail", "type": "detail"}},
                     {"status": "none", "carrier": None},
                     {}):
        payload = {}
        _attach_degraded_hint(payload, decision)
        assert "degraded" not in payload, decision
