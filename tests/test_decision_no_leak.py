"""决策建议的泄露回归：依据、对外投影、AI 工具返回里不得出现物理层与内部标识。

这是 security-guardrails §7（黑盒）+ no-silent-degradation §1（fail-loud）在本模块的
落地把门。要防的缺陷形态很具体：

* 引擎把语义层 `provenance`（含物理表名）顺手塞进 evidence → 决策中心/Chat 卡片
  直接渲染出 `equipment_metrics` 这类列名；
* 出站投影"清洗"掉敏感字段后照常返回 → 缺陷被掩盖，直到给出错答案才暴露；
* AI 工具返回把 `datasource_id` / SQL 片段带回模型上下文 → 一次 prompt injection
  就能把内部结构复述给用户。

判定标准不自己另写一套关键词表：复用 ``outbound_guard`` 的既有清单（护栏 §7 分工）。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from backend.modules.decision import engine, service
from backend.modules.decision import dictionary as dic
from backend.modules.decision.engine import EvalContext
from backend.modules.mind.execution.outbound_guard import find_sensitive_hits

# 物理层/内部标识的直观特征（outbound_guard 是键名与形态判定，这里再钉住本模块
# 实际会用到的那几个物理名，防止"清单没覆盖到就等于安全"的错觉）。
_PHYSICAL_MARKERS = (
    "equipment_metrics", "adh_metrics", "adh_decision_trees", "datasource_id",
    "physical_table", "catalog_ref", "provenance", "base_sql", "secured_sql",
    "SELECT ", "select ", " GROUP BY ", "count(1)",
)


def _hits(payload) -> list[str]:
    """把任意结构过一遍既有敏感清单。"""
    return find_sensitive_hits(json.dumps(payload, ensure_ascii=False, default=str))


def _assert_no_physical(payload) -> None:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    for marker in _PHYSICAL_MARKERS:
        assert marker not in text, f"对外载荷里出现了内部标识/物理名「{marker}」"
    assert not _hits(payload), f"对外载荷命中 outbound_guard 敏感类别：{_hits(payload)}"


# ── 离线夹具：一棵带"脏"取数结果的树（provenance/SQL 都塞在语义层返回里）──

_INDEX = {
    "metric": [dic.DictHit("metric", "activation_rate", "equipment", "dict_exact",
                           aliases=["激活率"]),
               dic.DictHit("metric", "online_rate", "equipment", "dict_exact")],
    "dimension": [dic.DictHit("dimension", "site", "equipment", "dict_exact")],
}


def _doc():
    return {"root": {"type": "branch", "label": "激活风险判定", "op": "all", "children": [
        {"type": "leaf", "label": "激活率过低", "advice_key": "adv_low_activation",
         "when": {"metric": "activation_rate", "comparator": "<", "value": 0.6,
                  "dimension": "site", "time_window": "7d"},
         "evidence_note": "近 7 天激活率低于 60%"},
    ]}}


def _tree_row():
    return {"id": 1, "tree_key": "activation-risk", "name": "设备激活风险",
            "status": "active", "version": 3, "is_active": 1, "tree_doc": _doc(),
            "required_metrics": ["activation_rate"], "apply_sites": []}


def _dirty_result():
    """语义层正常返回：带 provenance（含物理表）/resolved_tables/执行 SQL
    —— 引擎必须一律不带出去。"""
    from backend.semantics.models import ResultColumn, SemanticResult
    return SemanticResult(
        columns=[ResultColumn(name="site", role="dimension"),
                 ResultColumn(name="activation_rate", role="measure")],
        rows=[["上海仓", 0.41]],
        resolved_tables=["equipment_metrics"],
        provenance={"physical_table": "equipment_metrics",
                    "base_sql": "SELECT site, activation_rate FROM equipment_metrics",
                    "datasource_id": 12},
    )


def _ctx(**kw):
    base = dict(mode="full", user_id=7, username="alice", role="analyst",
                workspace_id=301, datasource_id=12, site_code="cn", dictionary=_INDEX)
    base.update(kw)
    return EvalContext(**base)


@pytest.fixture
def dirty_engine(monkeypatch):
    """取数一律回带脏 provenance 的结果；顺带记录引擎到底读没读这些脏字段。"""
    def fake_execute(query, policy):
        return _dirty_result()

    monkeypatch.setattr("backend.semantics.execute.execute", fake_execute)
    return fake_execute


@pytest.fixture
def fake_store(monkeypatch):
    """把 service 的 DB 触点换成内存表，让投影/落实例这段可离线断言。"""
    advice = [{"advice_key": "adv_low_activation", "action": "复核校准记录",
               "rationale": "连续 7 日激活率低于标称", "severity": "high",
               "kb_refs": ["calibration-sop"]}]
    monkeypatch.setattr(service, "execute_query",
                        lambda sql, params=None, fetchone=False: advice
                        if "adh_decision_advice" in sql else [])
    written = []
    monkeypatch.setattr(service, "execute_write",
                        lambda sql, params=None: written.append((sql, params)) or 1)
    written_rows = written
    return written_rows


# ── 1. 引擎产物 ──────────────────────────────────────────────────

class TestEngineOutcomeIsBusinessLevel:
    def test_outcome_carries_no_physical_reference(self, dirty_engine):
        outcome = engine.evaluate_tree(_tree_row(), _ctx())
        assert outcome.verdict == "hit"
        _assert_no_physical(outcome.model_dump())

    def test_evidence_keys_are_whitelisted(self, dirty_engine):
        """evidence 只允许业务口径字段；语义层溯源结构不得被顺手复制进来。"""
        outcome = engine.evaluate_tree(_tree_row(), _ctx())
        allowed = {"metric", "comparator", "threshold", "measured", "unit", "dimension",
                   "time_window", "label", "path", "note", "judged"}
        for row in outcome.evidence:
            assert set(row) <= allowed, f"evidence 多出未审字段：{set(row) - allowed}"

    def test_unresolved_candidates_are_business_names(self, monkeypatch):
        """解析失败只回抛业务名候选，不用物理名当"提示"。"""
        def fake_execute(query, policy):  # pragma: no cover - 不该被调用
            raise AssertionError("解析失败不该打库")

        monkeypatch.setattr("backend.semantics.execute.execute", fake_execute)
        doc = {"root": {"type": "branch", "label": "r", "op": "all", "children": [
            {"type": "leaf", "label": "未知指标", "advice_key": "adv_x",
             "when": {"metric": "activation_rat", "comparator": ">", "value": 1}}]}}
        row = _tree_row()
        row["tree_doc"] = doc
        outcome = engine.evaluate_tree(row, _ctx())
        _assert_no_physical(outcome.model_dump())
        assert outcome.unresolved, "解析失败必须留原因，不能折叠成 no_hit"
        assert all("datasource" not in str(u) for u in outcome.unresolved)


# ── 2. 对外投影 ─────────────────────────────────────────────────

class TestProjectionFailLoud:
    def test_project_run_is_clean(self, dirty_engine, fake_store):
        outcome = engine.evaluate_tree(_tree_row(), _ctx())
        assert outcome.verdict == "hit", outcome.model_dump()
        run = service.record_run(_tree_row(), outcome, _ctx())
        _assert_no_physical(service.project_run(run, outcome))

    def test_leaked_evidence_blocks_instead_of_silently_stripping(self, fake_store):
        """命中敏感就把整次返回拒掉（500 + 明确文案），不许"删掉字段继续发"。

        静默清洗会让缺陷长期隐身：前端少一列依据没人察觉，直到有人问
        「为什么这条建议没有数值」才暴露（no-silent-degradation §1）。
        """
        outcome = engine.DecisionOutcome(
            tree_key="activation-risk", tree_name="设备激活风险", verdict="hit",
            evidence=[{"metric": "activation_rate", "comparator": "<", "threshold": 0.6,
                       "measured": 0.41,
                       "base_sql": "SELECT site, activation_rate FROM equipment_metrics"}])
        run = service.record_run(_tree_row(), outcome, _ctx())
        with pytest.raises(service.DecisionError) as exc:
            service.project_run(run, outcome)
        assert exc.value.code == "DECISION_OUTBOUND_BLOCKED"
        assert exc.value.status == 500

    def test_recorded_row_itself_is_clean(self, dirty_engine, fake_store):
        """实例落库的 evidence/hit_path/advice_snapshot 也会被通知/回放读到，同样收口。"""
        outcome = engine.evaluate_tree(_tree_row(), _ctx())
        run = service.record_run(_tree_row(), outcome, _ctx())
        _assert_no_physical({"evidence": run["evidence"], "hit_path": run["hit_path"],
                             "advice": run["advice_snapshot"],
                             "reason": run["verdict_reason"]})


# ── 3. AI 工具返回 ──────────────────────────────────────────────

class TestAiChannelReturns:
    def test_decision_brief_is_clean(self, dirty_engine, fake_store):
        from backend.modules.mind.execution import function_tools as ft
        outcome = engine.evaluate_tree(_tree_row(), _ctx())
        run = service.record_run(_tree_row(), outcome, _ctx())
        brief = ft._decision_brief([service.project_run(run, outcome)])
        _assert_no_physical(brief)
        # outbound_guard 在 compat 收口处还会拦一道：这里提前自检，别等运行时拒发
        assert not _hits([json.dumps(brief, ensure_ascii=False, default=str)])

    def test_brief_omits_datasource_and_internal_ids(self, dirty_engine, fake_store):
        from backend.modules.mind.execution import function_tools as ft
        outcome = engine.evaluate_tree(_tree_row(), _ctx())
        run = service.record_run(_tree_row(), outcome, _ctx())
        text = json.dumps(ft._decision_brief([service.project_run(run, outcome)]),
                          ensure_ascii=False, default=str)
        for key in ("datasource_id", "tree_id", "site_code", "user_id"):
            assert f'"{key}"' not in text, f"AI 可见载荷里带了内部键 {key}"

    def test_brief_keeps_skip_reason_verbatim(self, fake_store):
        """skipped/partial 的原因必须在载荷里，否则模型会把"没评估"说成"数据正常"。"""
        from backend.modules.mind.execution import function_tools as ft
        out = ft._decision_brief([{"tree_name": "设备激活风险", "verdict": "skipped",
                                   "reason": "当前角色未授权决策建议", "advice": []}])
        assert out["evaluations"][0]["reason"] == "当前角色未授权决策建议"
        assert out.get("attention")


# ── 4. 契约入口（写面根本进不了物理名）──────────────────────────

class TestContractRejectsPhysical:
    @pytest.mark.parametrize("bad", [
        "SELECT site FROM equipment_metrics",           # SQL 片段
        "equipment_metrics.activation_rate",            # 表.列 不是字典里存在的业务名时
        "datasource_id=12",                             # 内部标识
        'activation_rate"',                             # 引号
    ])
    def test_bad_metric_rejected_by_model(self, bad):
        from pydantic import ValidationError
        from backend.modules.decision.models import DecisionCondition
        with pytest.raises(ValidationError):
            DecisionCondition(metric=bad, comparator=">", value=1)

    def test_plain_business_name_accepted(self):
        from backend.modules.decision.models import DecisionCondition
        assert DecisionCondition(metric="激活率", comparator=">", value=1).metric == "激活率"

    def test_validate_doc_rejects_unresolvable_names(self, monkeypatch):
        """保存门禁：字典确认不了的业务名一律回抛候选，不静默绑定近名。"""
        from backend.modules.decision.models import DecisionTreeDoc
        monkeypatch.setattr(service, "advice_keys_index",
                            lambda: {"adv_low_activation": {}})
        monkeypatch.setattr(dic, "load_dictionary", lambda datasource_id=0: _INDEX)

        errors, warnings = service.validate_doc(DecisionTreeDoc.model_validate(_doc()))
        assert errors == [], f"名字、维度、建议文案都存对 → 好树不该报错：{errors}"
        assert warnings == []

        bad = DecisionTreeDoc.model_validate({"root": {"type": "branch", "label": "r",
                                                       "op": "all", "children": [
            {"type": "leaf", "label": "近名", "advice_key": "adv_low_activation",
             "when": {"metric": "activation_ra", "comparator": ">", "value": 1}}]}})
        errors, _ = service.validate_doc(bad)
        assert errors and errors[0]["type"] == "metric" and errors[0]["candidates"]
        assert "activation_rate" in errors[0]["candidates"]

    def test_publish_blocks_dangling_object_keys(self, monkeypatch):
        """某个指标挂在一个已被删掉的本体对象上 → 硬拒发布（不能上线一个注定取不到数的树）。"""
        tree = _tree_row()
        monkeypatch.setattr(service, "require_tree", lambda key: tree)
        monkeypatch.setattr(service, "advice_keys_index", lambda: {"adv_low_activation": {}})
        monkeypatch.setattr(dic, "load_dictionary", lambda datasource_id=0: _INDEX)
        monkeypatch.setattr(service, "_object_keys_for", lambda names: ["equipment", "ghost_obj"])
        monkeypatch.setattr(service, "_existing_object_keys", lambda keys: {"equipment"})
        with pytest.raises(service.DecisionError) as exc:
            service.publish("activation-risk", {"user_id": 1})
        assert exc.value.code == "DECISION_DANGLING_OBJECT"
        assert "ghost_obj" in str(exc.value)


# ── 5. 通知面（P3 落地后继续往这里加）───────────────────────────

def test_notification_variables_stay_metadata_only():
    """通知变量面只有执行元信息：决策建议正文/阈值/实测值一律不得进通知。

    口径来自 message_template_service 的既有把门（test_message_template_notification
    锁"业务字段不在变量面里"）。本用例把它延伸到决策模块：任何新增变量名如果
    暗示业务内容（advice/metric/threshold/value/detail），就是扩了泄露面。
    """
    from backend.modules.flow.services import message_template_service as mts

    suspicious = re.compile(r"(?i)advice|metric|threshold|detail|evidence|risk_level")
    for var in mts.MESSAGE_TEMPLATE_VARIABLES:
        name = var.get("key") if isinstance(var, dict) else str(var)
        assert not suspicious.search(str(name)), f"通知变量「{name}」携带业务内容，违反护栏"
