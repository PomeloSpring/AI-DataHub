""""没定出生效源" 必须说成"没定源"，不得被报成"对象未绑定/未建模/该站无数据"。

缺陷族（实测会话 1783069459277，一次问数把三个入口都踩了一遍）：
1. `run_semantic_query` 的守卫写成 `not system_scope(ctx)`——那是**能力**判据，而能力
   叠加后 admin 类 AS-BOT 都有 system 工具组 ⇒ 聚合态会话直接跳过守卫，落到
   `resolve_binding(datasource_id=0)` 报出 "object 'rum_event' 未绑定到任何物理表"。
   绑定其实好好地在 arms-prd-cn 上（physical_table=logstore-rum），真因是本轮没带 site。
   模型据此放弃语义层去手写 SQL，最终三重失败（选错源 → 1064 → 被说成"连接抖动"）。
2. 大屏/图表生成入口（screen_tools）是同一条链路，同样把"没定源"报成"未绑定"。
3. 元数据目录（scoped_metadata）的分支顺序把聚合态会话刷上"仅系统本体可见，
   业务本体需…切聚合态才纳入"——同一回合目录里已经返回了业务对象，模型据此
   判定业务本体不可用。
4. 数据集执行失败一律说"连接/凭据不可用，稍后重试"，方言不符/缺表这类确定性
   缺陷被掩成抖动（分类口径唯一真源 = `semantics.contract.classify_exec_failure`）。

判据必须是**事实**而非能力：先问"该对象在 ds=0 上到底有没有绑定"。
"""
import asyncio
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from backend.modules.mind.execution.models import ExecutionContext
from backend.semantics.contract import classify_exec_failure

ADMIN_DS = [1780478236183, 1792408797510]   # 中国站的业务库 + 观测源


def _ctx(site_code="", datasource_id=0, system=True, available=ADMIN_DS):
    policy = SimpleNamespace(selection={"system": ["system_usage"]} if system else {},
                             as_bot={})
    return ExecutionContext(
        user_id=1778154925, username="admin", user_role="admin", workspace_id=301,
        datasource_id=datasource_id,
        extra={"site_code": site_code, "as_bot_key": "admin",
               "available_datasource_ids": list(available or []),
               "secure_runtime": SimpleNamespace(policy=policy, tool_tasks=set())})


def _binding_stub(rows_by_ds):
    """按 datasource_id 返回预置绑定的 resolve_binding 替身（记录调用参数）。"""
    calls = []

    def fake(object_ref, datasource_id=0, model_id=None, datasource_name=""):
        calls.append((object_ref, int(datasource_id or 0)))
        row = rows_by_ds.get(int(datasource_id or 0))
        return row, []

    return fake, calls


# ── 1. run_semantic_query：未定源的可操作提示 ──────────────────────


def test_unresolved_source_payload_aggregate_site_teaches_per_site_orchestration():
    from backend.modules.mind.execution.sdk_tools.semantic_query import (
        unresolved_source_payload,
    )

    out = unresolved_source_payload(_ctx(site_code="global"), "rum_event")
    assert "rum_event" in out["error"]
    assert "site" in out["error"] and "分站" in out["error"]
    # 关键：不得把"没定源"报成"对象未绑定"（模型会照这句话向用户收口成"该站没数据"）；
    # 同时必须显式反驳这个误读（no-silent-degradation：错误原因要可诊断）
    for wrong in ("未绑定到任何物理表", "暂无数据"):
        assert wrong not in out["error"], out["error"]
    assert "这不代表数据未建模" in out["error"], out["error"]
    assert "不得" in out["hint"] and "暂无数据" in out["hint"]   # 禁语作为口径明写
    assert out["retry_with"] == "site"


def test_unresolved_source_payload_concrete_site_offers_both_knobs():
    from backend.modules.mind.execution.sdk_tools.semantic_query import (
        unresolved_source_payload,
    )

    out = unresolved_source_payload(_ctx(site_code="cn"), "rum_event")
    assert "site" in out["retry_with"] and "datasource" in out["retry_with"]
    assert "未绑定" not in out["error"]


def test_unresolved_source_payload_without_site_falls_back_to_session_source():
    from backend.modules.mind.execution.sdk_tools.semantic_query import (
        unresolved_source_payload,
    )

    out = unresolved_source_payload(_ctx(), "")
    assert "会话" in out["error"] and out["retry_with"] == "datasource"


def test_semantic_query_guard_uses_fact_not_capability(monkeypatch):
    """system 能力（admin 恒有）不再让守卫失效；判据改为"该对象在 ds=0 有没有绑定"。

    旧写法：`not system_scope(ctx)` ⇒ 聚合态会话跳过守卫 → 报"未绑定到任何物理表"。
    """
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver

    fake, calls = _binding_stub({0: None})        # 业务对象在 ds=0 上没有绑定
    monkeypatch.setattr(binding_resolver, "resolve_binding", fake)
    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "rum_event", "metrics": ["pv"]}, _ctx(site_code="global")))
    assert is_error is True
    assert "未绑定" not in payload["error"], payload
    assert "site" in payload["error"], payload
    assert calls == [("rum_event", 0)], calls      # 守卫确实做了那次事实探测


def test_semantic_query_system_object_on_ds_zero_not_blocked(monkeypatch):
    """系统域对象绑定就在 ds=0：有绑定就照常放行，不得被新守卫拦（行为零变化）。"""
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver, planner

    fake, _calls = _binding_stub({0: SimpleNamespace(object_key="sys_usage", model_id=7001)})
    monkeypatch.setattr(binding_resolver, "resolve_binding", fake)
    from backend.modules.mind.execution import resource_guard
    monkeypatch.setattr(resource_guard, "validate_binding", lambda *a, **k: None)
    # 放行后走到 plan：用空 SQL 的护栏返回证明"已过守卫"（不是未定源文案）
    monkeypatch.setattr(planner, "plan", lambda q, b: SimpleNamespace(
        sql="", warnings=["stub"], provenance={}, guardrail=SimpleNamespace(
            query_mode="", size_class="", allow_full_scan=False)))
    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "sys_usage", "metrics": ["pv"]}, _ctx(site_code="global")))
    assert is_error is True
    assert payload["error"] == "语义层护栏拒绝了本次查询", payload


def test_semantic_query_business_guard_without_system_capability(monkeypatch):
    """无 system 能力（analyst 形态）：未定源仍直接给可操作提示，不做 ds=0 探测。"""
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver

    fake, calls = _binding_stub({0: None})
    monkeypatch.setattr(binding_resolver, "resolve_binding", fake)
    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "rum_event", "metrics": ["pv"]},
        _ctx(site_code="cn", system=False, available=[])))
    assert is_error is True and "site" in payload["error"]
    assert calls == []          # 没有系统能力就不必先探 ds=0（少一趟无谓查询）


# ── 2. 大屏/图表生成入口同一条链路 ────────────────────────────────


def test_screen_intent_unresolved_source_not_reported_as_unbound(monkeypatch):
    from backend.modules.mind.execution.sdk_tools import screen_tools as st
    from backend.semantics import binding_resolver

    fake, _calls = _binding_stub({0: None})
    monkeypatch.setattr(binding_resolver, "resolve_binding", fake)
    with pytest.raises(st.ScreenIntentError) as ei:
        asyncio.run(st._resolve_intent({"object": "rum_event", "metrics": ["pv"]},
                                      _ctx(site_code="global")))
    payload = json.loads(ei.value.response["content"][0]["text"])
    assert "site" in payload["error"]
    assert "未绑定" not in payload["error"]


def test_screen_intent_unbound_message_says_unbound_is_not_no_data(monkeypatch):
    """真·未绑定（已定源）也必须带"未绑定≠无数据"的口径，不得诱导模型下"该站无数据"。"""
    from backend.modules.mind.execution.sdk_tools import screen_tools as st
    from backend.semantics import binding_resolver

    def fake(object_ref, datasource_id=0, model_id=None, datasource_name=""):
        if int(datasource_id or 0) == 0:
            return SimpleNamespace(object_key=object_ref), []   # ds=0 探测放行
        return None, ["no binding found"]

    monkeypatch.setattr(binding_resolver, "resolve_binding", fake)
    with pytest.raises(st.ScreenIntentError) as ei:
        asyncio.run(st._resolve_intent(
            {"object": "rum_event", "metrics": ["pv"], "datasource_id": ADMIN_DS[0]},
            _ctx(site_code="cn", datasource_id=ADMIN_DS[0])))
    payload = json.loads(ei.value.response["content"][0]["text"])
    assert "未绑定" in payload["error"] and "未绑定 ≠ 无数据" in payload["hint"]


# ── 3. 元数据目录：聚合态优先于"未选源"标注 ──────────────────────


def test_aggregate_scope_note_precedes_unselected_source_note(monkeypatch):
    """聚合态会话不得被刷"仅系统本体可见，业务本体需…切聚合态才纳入"。

    历史缺陷：同一回合 keyword=IP 已返回业务对象，文案却叫模型去"切聚合态"，
    模型据此判定业务本体不可用、跳过语义层去手写 SQL。
    """
    from backend.modules.mind.execution.sdk_tools import scoped_metadata as sm

    monkeypatch.setattr(sm, "execute_query",
                        lambda sql, params=None, fetchone=False: [])
    ctx = _ctx(site_code="global", datasource_id=0)
    ctx.extra["site_code"] = "global"
    out = sm.execute("get_metrics", {}, ctx)
    note = out.get("note", "")
    assert "聚合态" in note and "已按授权集内各具体站点纳入业务本体" in note
    assert "仅系统本体可见" not in note, note
    assert not out.get("incomplete_scope"), "聚合态不是'上下文不完整'"


def test_unselected_business_source_still_marks_incomplete_scope(monkeypatch):
    """具体站点未选源且无系统能力：仍按旧口径显式标注（行为零变化）。"""
    from backend.modules.mind.execution.sdk_tools import scoped_metadata as sm

    monkeypatch.setattr(sm, "execute_query",
                        lambda sql, params=None, fetchone=False: [])
    with pytest.raises(PermissionError):
        sm.execute("get_metrics", {}, _ctx(site_code="cn", datasource_id=0, system=False))


# ── 4. 数据集：确定性失败不得说成"稍后重试" ──────────────────────


def test_dataset_exec_fail_text_splits_deterministic_from_transient():
    from backend.modules.viz.services.dataset_service import (
        _EXEC_FAIL_HINT, _exec_fail_text,
    )

    assert _exec_fail_text("(1064, You have an error in your SQL syntax)", "mysql") \
        != _EXEC_FAIL_HINT
    assert "方言" in _exec_fail_text("(1064, syntax error)", "mysql")
    assert "重试不会好" in _exec_fail_text("(1146, Table 'x' doesn't exist)", "mysql")
    # 抖动/未知仍回原来的数据集文案（含"选择可用的执行数据源"这一 UI 动作）
    assert _exec_fail_text("(2002, Can't connect to MySQL server)", "mysql") == _EXEC_FAIL_HINT
    assert _exec_fail_text("boom", "") == _EXEC_FAIL_HINT


def test_dataset_exec_fail_text_never_leaks_engine_detail():
    from backend.modules.viz.services.dataset_service import _exec_fail_text

    raw = ('(1064, syntax error near "SELECT `pwd` FROM users" '
           'at 10.0.0.9 as root/secret')
    out = _exec_fail_text(raw, "mysql")
    for secret in ("10.0.0.9", "root", "pwd", "1064", "users"):
        assert secret not in out, out
    assert classify_exec_failure(raw, "mysql")[0] == "sql_dialect"


def test_dataset_exec_fail_text_trusts_the_carried_category():
    """错因类别随异常带走时，不得拿已翻译的中文文案再猜一遍（现网误判的根因）。

    2026-11-10 实测：两个遗留看板 9 张图的正文引用了 Doris 里已不存在的表（确定性
    缺表），对外却一律说“通常是数据源连接/凭据不可用…稍后重试”——因为 raw SQL 路的
    `str(e)` 已是 `governed_query` 翻译好的中文，而 `classify_exec_failure` 的判据是英文标记。
    """
    from backend.modules.viz.services import dataset_service as ds

    humanized = ("目标数据源里没有这条 SQL 引用的表或列（目标源类型：doris）。这是确定性失败，"
                 "重试不会好：表名/列名请先用 get_table_schema 确认。")
    # 没类别时只能按原文猜：中文必失配 → 退成抖动文案（这就是旧行为）
    assert ds._exec_fail_text(humanized, "doris") == ds._EXEC_FAIL_HINT
    # 类别在场即直接采信：说回“表/列不存在、重试不会好”
    fixed = ds._exec_fail_text(humanized, "doris", "missing_object")
    assert "重试不会好" in fixed and "稍后重试" not in fixed
    assert fixed == ds._EXEC_FAIL_HINT_BY_CATEGORY["missing_object"]


def test_exec_category_of_reads_both_carriers():
    from backend.modules.viz.services.dataset_service import _exec_category_of
    from backend.semantics.contract import ErrorCode, SemanticError

    raw = RuntimeError("取数在执行阶段失败")
    raw.exec_category = "sql_dialect"          # raw SQL 路：governed_query 带的属性
    assert _exec_category_of(raw) == "sql_dialect"
    sem = SemanticError(ErrorCode.EXEC_FAILED, "缺表", blocked_at="execute",
                        detail={"failure_category": "missing_object"})   # 语义路：detail
    assert _exec_category_of(sem) == "missing_object"
    assert _exec_category_of(ValueError("原始引擎报错")) == ""   # 拿不到就回空，由调用方按原文判


def test_governed_query_keeps_the_category_on_the_way_out():
    """governed_query 把 SemanticError 压扁成 RuntimeError 时，类别必须跟着走。"""
    from backend.core import governed_query as gq
    from backend.modules.viz.services.dataset_service import _exec_category_of
    from backend.semantics.contract import ErrorCode, SemanticError

    def boom(sql, ctx):
        raise SemanticError(ErrorCode.EXEC_FAILED, "目标数据源里没有这条 SQL 引用的表或列",
                            blocked_at="execute", detail={"failure_category": "missing_object"})

    with mock.patch("backend.semantics.execute.execute_sql", boom):
        with pytest.raises(RuntimeError) as got:
            gq.governed_execute("SELECT 1", 7, 1)
    assert _exec_category_of(got.value) == "missing_object"


def test_dataset_exec_fail_paths_all_pass_the_category():
    """两条取数通道都不得回到“只拿中文文案猜”的写法（单一口径护栏）。"""
    import inspect

    from backend.modules.viz.services import dataset_service as ds

    for fn in (ds._query_sql, ds._query_semantic):
        src = inspect.getsource(fn)
        assert "_exec_category_of(e)" in src, f"{fn.__name__} 丢掉了随异常带过的错因类别"


# ── 5. 提示词侧：堵住"语义层报未定源 → 弃语义层去写 SQL"的逃生门 ──────


def _caps(*names):
    return {"tools": [{"name": n, "description": ""} for n in names], "unavailable_tools": []}


def test_nl2sql_rules_close_the_unresolved_source_escape_hatch():
    """模型把「未绑定」当成弃语义层的许可证（历史缺陷的第一步），规则必须显式收回它。"""
    from backend.modules.mind.execution.prompt_composer import (
        NL2SQL_QUERY_RULES, compose_system_prompt,
    )

    assert "不包含本轮没定出站点/数据源" in NL2SQL_QUERY_RULES
    assert "failure_category" in NL2SQL_QUERY_RULES, "确定性缺陷的判据要落到规则里"

    prompt = compose_system_prompt(None, [], username="u", user_role="operator",
                                   capabilities=_caps("mcp__datahub_query__execute_sql",
                                                      "mcp__datahub_semantic__run_semantic_query"))
    assert "不包含本轮没定出站点/数据源" in prompt

    # 未授权 execute_sql 的会话不得为这段字数买单（口径与 has_execute_sql 同路）
    prompt2 = compose_system_prompt(None, [], username="u", user_role="viewer",
                                    capabilities=_caps("mcp__datahub_semantic__run_semantic_query"))
    assert "不包含本轮没定出站点/数据源" not in prompt2


# ── 6. 报告生成的 intent 来源（同一缺陷族：按站点绑定的业务对象在 ds=0 解不出来）──


def test_report_intent_source_resolves_datasource_from_site():
    """来源带站点时在授权集内定源（对象感知消歧：观测对象不被业务库抢走）。"""
    from backend.modules.viz.services import report_service

    ident = {"user_id": 1778154925, "username": "admin", "role": "admin", "workspace_id": 301}
    ds_id = report_service._report_source_datasource("rum_event", "中国", ident)
    assert int(ds_id) > 0
    # 聚合态不是"随便挑一个站"：必须回可操作错因（不静默选源充数）
    with pytest.raises(ValueError, match="聚合态|站点"):
        report_service._report_source_datasource("rum_event", "全部站点", ident)


def test_report_source_passes_site_code_through(monkeypatch):
    """执行入口必须把来源站点传到语义解析段（旧写法直接 execute_semantic_source(intent, identity)）。"""
    from backend.modules.viz.services import report_service

    seen = {}

    def fake(intent, identity, datasource_id=0, site_code=""):
        seen.update(datasource_id=datasource_id, site_code=site_code)
        return {"status": "success"}

    monkeypatch.setattr(report_service, "execute_semantic_source", fake)
    report_service.execute_report_source(
        {"intent": {"object": "rum_event", "metrics": ["事件总数"]}, "site_code": "cn"},
        {"user_id": 1, "workspace_id": 301})
    assert seen["site_code"] == "cn", seen


def test_report_bind_reason_distinguishes_no_site_from_unbound():
    from backend.modules.viz.services.report_service import _bind_failure_reason

    multi = _bind_failure_reason("rum_event", ["对象 'rum_event' 在多个站点有绑定 ['a','b']，未指定站点无法确定取数范围"], 0)
    assert "多个站点" in multi and "save_report" in multi
    # 源名不得被回显进对外文案（避免报告正文插值基础设施命名）
    assert "'a'" not in multi and "'b'" not in multi
    nosite = _bind_failure_reason("rum_event", ["no binding found"], 0)
    assert "site_code" in nosite and "未绑定" not in nosite
    bound = _bind_failure_reason("rum_event", [], 123)
    assert "生效数据源" in bound and "未绑定 ≠ 无数据" in bound


def test_apply_output_header_maps_slots_and_noops():
    from backend.semantics.execute import apply_output_header

    res = {"columns": ["o0", "o1"], "rows": [{"o0": "中国", "o1": 5}], "row_count": 1}
    out = apply_output_header(res, {"o0": "国家", "o1": "事件数"})
    assert out["columns"] == ["国家", "事件数"]
    assert out["rows"] == [{"国家": "中国", "事件数": 5}]
    assert res["columns"] == ["o0", "o1"], "不得就地改原结果"
    # 非 SLS（无 header_map）行为零变化
    assert apply_output_header(res, {}) is res
