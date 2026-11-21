"""P2 处置建议与队列分档（spec `Chat语义缺口驱动补全` §P2 / D7）。

锁四件事：
1. `suggest_action` 的**唯一判据**是 `classify_term`（R7：审核面不得长出第二套分类器，
   否则同一候选在两个页面得到两个结论，人只会信自己顺手看到的那个）；
2. D7 门槛：alias/term 命中 1 次即可处置；new_* 命中 1 次只标「待观察」；
   binding_gap 进「核绑定」队列而不是「待建」；
3. 建议只预填"不需要判断"的字段（收编目标/显示名），对象与属性列一律留空——
   预填等于替人把口径定了，比不给建议更危险；
4. 排序 `kind 权重 → hit_count DESC → created_at ASC → id`：旧口径 `updated_at DESC`
   会被探针回写刷动，且把可一键的 alias 压到需排查的 binding_gap 之后。
"""
from backend.modules.catalog.services import evolution_service as evo


def _install(monkeypatch, hits_by_marker: dict | None = None) -> list[tuple[str, tuple]]:
    """把库访问换成可断言的假实现；返回 (sql, params) 调用记录。"""
    calls: list[tuple[str, tuple]] = []
    hits = hits_by_marker or {}

    def fake_q(sql, params=None):
        s = " ".join(str(sql).split())
        calls.append((s, params))
        for marker, rows in hits.items():
            if marker in s:
                # rows 可为 callable(params)：同一个表会被不同词反复查，
                # 无参数判断的桩会把 A 词的命中错安给 B 词（实测就踩过）。
                return rows(params) if callable(rows) else rows
        return []

    monkeypatch.setattr(evo, "_q", fake_q)
    monkeypatch.setattr(evo, "_w", lambda *a, **k: None)
    return calls


def _cand(**over) -> dict:
    base = {"id": 1, "term": "x", "kind": "term", "source": "parse_fail",
            "datasource_name": "", "object_hint": "", "target_ref": "",
            "target_type": "", "hit_count": 1, "evidence": {}}
    base.update(over)
    return base


# ── R7：建议与 classify_term 同源 ──────────────────────────────────


def test_suggest_action_delegates_to_classify_term(monkeypatch):
    """建议必须走 classify_term（同输入同输出），不得在审核面另写一套分类。"""
    seen: list[dict] = []

    def fake_classify(term, **kwargs):
        seen.append({"term": term, **kwargs})
        return {"kind": "alias", "target_ref": "首次响应时长", "target_type": "metric",
                "ambiguous": False, "suggested_refs": [], "classify_reason": "stub"}

    monkeypatch.setattr(evo, "classify_term", fake_classify)
    row = _cand(term="首响时长", kind="alias", target_type="metric", hit_count=4,
                evidence={"candidates": ["首次响应时长"], "reason": "fuzzy_rejected"})

    out = evo.suggest_action(row)

    assert seen and seen[0]["term"] == "首响时长"
    # 候选上能还原的上下文必须原样传下去，否则重算口径与入库时不一致
    assert seen[0]["hint_type"] == "metric"
    assert seen[0]["candidates"] == ["首次响应时长"]
    assert seen[0]["feedback_reason"] == "fuzzy_rejected"
    assert out["suggested_kind"] == "alias"
    assert out["prefilled"]["target_ref"] == "首次响应时长"


def test_alias_with_unique_hit_prefills_target(monkeypatch):
    """命中唯一归属（别名档）时，收编目标直接预填——这是唯一被用通过的那条出口。"""
    _install(monkeypatch, {
        "FROM adh_metrics WHERE is_active = 1 AND aliases LIKE":
            [{"id": 5, "name": "首次响应时长", "aliases": "[\"首响时长\"]",
              "bound_object_key": "case", "target_table": "t_case"}],
    })
    out = evo.suggest_action(_cand(term="首响时长", kind="alias", target_type="metric"))

    assert out["queue"] == "build"
    assert out["tier"] == "act"
    assert out["confidence"] == "high"
    assert out["prefilled"] == {"target_type": "metric", "target_ref": "首次响应时长"}


def test_ambiguous_alias_leaves_target_blank(monkeypatch):
    """多个归属方时不预填目标：替人挑一个 = 把歧义悄悄定了。"""
    _install(monkeypatch)
    out = evo.suggest_action(_cand(
        term="渠道", kind="alias", target_type="dimension",
        evidence={"candidates": ["应用渠道", "获客渠道"]}))

    assert out["suggested_kind"] == "alias"
    assert out["ambiguous"] is True
    assert out["confidence"] == "medium"
    assert out["prefilled"]["target_ref"] == ""
    assert out["suggested_refs"][:2] == ["应用渠道", "获客渠道"]


# ── D7 门槛 ──────────────────────────────────────────────────────


def test_new_metric_at_one_hit_is_observe_not_recommend(monkeypatch):
    """命中 1 次的新指标只进「待观察」，不占「推荐建」的位置（D7）。"""
    _install(monkeypatch)
    out = evo.suggest_action(_cand(term="avg_cases_per_device", kind="new_metric",
                                   target_type="metric", hit_count=1))

    assert out["queue"] == "build"
    assert out["tier"] == "observe"
    assert "待观察" in out["basis"]
    # 指标必须人手补口径：公式不许预填
    assert "formula" not in out["prefilled"]
    assert out["prefilled"]["display_name"] == "avg_cases_per_device"


def test_new_dimension_at_two_hits_is_recommend(monkeypatch):
    """命中 ≥2 才升为「推荐建」；门槛常量只有一处（RECOMMEND_HITS）。"""
    _install(monkeypatch)
    out = evo.suggest_action(_cand(term="客户等级", kind="new_dimension",
                                   target_type="dimension",
                                   hit_count=evo.RECOMMEND_HITS))
    assert out["tier"] == "recommend"
    assert "推荐建" in out["basis"]


def test_new_dimension_does_not_prefill_column_or_object(monkeypatch):
    """维度建议不预填对象/属性列：那是新建口径的核心判断（不预填 = 不替人做主）。"""
    _install(monkeypatch)
    out = evo.suggest_action(_cand(
        term="事件时间", kind="new_dimension", target_type="dimension", hit_count=1,
        object_hint="rum_event"))
    assert set(out["prefilled"]) == {"display_name"}


def test_binding_gap_goes_to_binding_queue(monkeypatch):
    """已建档但取数不可达 → 核绑定队列，不混在「待建」里（D7）。"""
    _install(monkeypatch, {
        "FROM adh_metrics WHERE is_active = 1 AND (name":
            lambda p: ([{"id": 9, "name": "DAU", "name_en": "", "aliases": "[]",
                        "bound_object_key": "case", "target_table": "t_case"}]
                       if p and p[0] == "DAU" else []),
    })
    out = evo.suggest_action(_cand(term="DAU", kind="binding_gap", target_type="metric"))

    assert out["suggested_kind"] == "binding_gap"
    assert out["queue"] == "binding"
    assert out["tier"] == "verify"
    assert out["prefilled"] == {}
    assert "核绑定" in out["basis"]


def test_term_stays_in_build_queue_with_act_tier(monkeypatch):
    """纯黑话进术语缓冲区：仍在「待建」队列，但档位是可一键（命中 1 次即可）。"""
    _install(monkeypatch)
    out = evo.suggest_action(_cand(term="返点", kind="term", hit_count=1))
    assert out["queue"] == "build" and out["tier"] == "act"
    assert out["prefilled"]["description"]


# ── 分类漂移必须显式 ──────────────────────────────────────────────


def test_drift_between_stored_and_recomputed_kind_is_flagged(monkeypatch):
    """落库 kind 与按当前证据重算不一致：建议按重算给，但必须标 drift + low。"""
    _install(monkeypatch)
    out = evo.suggest_action(_cand(
        term="渠道", kind="new_metric", target_type="metric",
        evidence={"candidates": ["应用渠道"]}))

    assert out["drift"] is True
    assert out["confidence"] == "low"
    assert out["suggested_kind"] == "alias"
    assert "重算" in out["basis"]


# ── 队列排序口径 ──────────────────────────────────────────────────


def test_list_candidates_orders_by_kind_weight_then_hits(monkeypatch):
    """排序：kind 权重优先（处置成本不随热度变），末尾 id 兜底保证分页稳定。"""
    calls = _install(monkeypatch)
    evo.list_candidates(status="pending", limit=10)

    sql = calls[-1][0]
    assert "ORDER BY CASE kind" in sql, sql
    # 权重表与常量同源：alias 最前、binding_gap 最后
    assert f"WHEN 'alias' THEN {evo.KIND_WEIGHT['alias']}" in sql, sql
    assert f"WHEN 'binding_gap' THEN {evo.KIND_WEIGHT['binding_gap']}" in sql, sql
    assert "hit_count DESC, created_at ASC, id ASC" in sql, sql
    # 旧口径会让探针回写刷动排序（F24 同类根因），不得回归
    assert "updated_at" not in sql, sql


# ── 收编目标清单：只列在用的字典行，且不得假设 display_name 列存在 ──


def test_target_options_only_queries_columns_that_exist(monkeypatch):
    """`adh_metrics`/`adh_dimensions` **没有 display_name 列**（只有 name/name_en）。

    实测踩过：照抄对象清单的字段假设写了 `COALESCE(NULLIF(display_name,''),name)`，
    接口直接 1054 Unknown column → 别名出口必 500，而单测用桩库全绿。这里锁 SQL 形态。
    """
    calls = _install(monkeypatch, {
        "FROM adh_metrics WHERE is_active = 1":
            [{"name": "首次响应时长"}, {"name": "订单金额"}],
    })
    out = evo.target_options(target_type="metric", datasource_name="prd-cn")

    assert "display_name" not in calls[-1][0], calls[-1][0]
    assert "SELECT name FROM adh_metrics WHERE is_active = 1" in calls[-1][0]
    # 退役行不能被新别名挂上（别名指向不可用口径 = 假动作）
    assert "is_active = 1" in calls[-1][0]
    assert out == [{"key": "首次响应时长", "display_name": "首次响应时长"},
                   {"key": "订单金额", "display_name": "订单金额"}]


def test_target_options_scopes_by_datasource(monkeypatch):
    """指定数据源时只取本源 + 全局(ds=0)：跨源字典行不能被收编到本站别名上。"""
    calls = _install(monkeypatch, {
        "FROM adh_datasources WHERE name": [{"id": 7}],
        "FROM adh_dimensions WHERE is_active = 1": [{"name": "渠道"}],
    })
    evo.target_options(target_type="dimension", datasource_name="prd-cn")

    sql, params = next((s, p) for s, p in calls if "FROM adh_dimensions" in s)
    assert "(datasource_id = %s OR datasource_id = 0)" in sql, sql
    assert params == (7,), params


def test_target_options_rejects_unknown_type(monkeypatch):
    """非法类型必须报错（不能静默回空清单让界面看起来“没有可选目标”）。"""
    _install(monkeypatch)
    try:
        evo.target_options(target_type="widget")
    except ValueError as exc:
        assert "metric|dimension|object" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("非法 target_type 应抛 ValueError")


# ── 验收样本：现网 3 条 pending 分属不同处置面 ────────────────────


def test_live_samples_land_in_three_distinct_queues(monkeypatch):
    """`avg_cases_per_device`→待观察、`事件时间`→待观察(待建页签)、`DAU`→核绑定。"""
    _install(monkeypatch, {
        "FROM adh_metrics WHERE is_active = 1 AND (name":
            lambda p: ([{"id": 9, "name": "DAU", "name_en": "", "aliases": "[]",
                        "bound_object_key": "case", "target_table": "t_case"}]
                       if p and p[0] == "DAU" else []),
    })
    avg = evo.suggest_action(_cand(term="avg_cases_per_device", kind="new_metric",
                                   target_type="metric"))
    evt = evo.suggest_action(_cand(term="事件时间", kind="new_dimension",
                                   target_type="dimension", object_hint="rum_event"))
    dau = evo.suggest_action(_cand(term="DAU", kind="binding_gap", target_type="metric"))

    assert (avg["queue"], avg["tier"]) == ("build", "observe")
    assert (evt["queue"], evt["tier"]) == ("build", "observe")
    assert (dau["queue"], dau["tier"]) == ("binding", "verify")
    # 三类不混屏：核绑定与待建不同队列；待观察不置顶（tier 与 recommend 区分）
    assert dau["queue"] != avg["queue"]
    assert avg["tier"] != "recommend"