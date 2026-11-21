"""`unresolved[]` 结构化缺口载荷（spec `Chat语义缺口驱动补全` P0）。

缺陷族：planner 早就算出了 `{term, type, reason, candidates}`（写入
`prov["unresolved_terms"]`），但工具层把它洗成自然语言 warning 才交给 LLM——
Chat 只能说"尚未建模"，说不出"哪个词 / 哪类缺口 / 有哪些相近候选"；
更严重的是「无权」这一支只回裸 `str(exc)`，与「未建模」在机器层面无法区分，
会让 Chat 既建议用户去建一个他本来就看不见的对象（信息泄露），又产生重复建模。

本组用例锁四件事：
1. `safe_unresolved` 只放业务名、白名单取值、越界即丢（护栏 §7）；
2. 三种失败**分开**：`unbound` / `forbidden` / `no_source` 不得互相冒用（D4）；
3. 部分成功也要带缺口（否则 Chat 把一份"看起来完整"的结果当全量交付）；
4. 失败分支同样带缺口（护栏拒绝 / 执行期拒绝），不是只在成功时给。
"""
import asyncio
from types import SimpleNamespace

from backend.modules.mind.execution.models import ExecutionContext
from backend.semantics.contract import safe_unresolved

ADMIN_DS = [1780478236183, 1792408797510]


def _ctx(site_code="", datasource_id=0, system=True, available=ADMIN_DS):
    policy = SimpleNamespace(selection={"system": ["system_usage"]} if system else {},
                             as_bot={})
    return ExecutionContext(
        user_id=1778154925, username="admin", user_role="admin", workspace_id=301,
        datasource_id=datasource_id,
        extra={"site_code": site_code, "as_bot_key": "admin",
               "available_datasource_ids": list(available or []),
               "secure_runtime": SimpleNamespace(policy=policy, tool_tasks=set())})


def _binding(object_key="case"):
    """最小 binding 替身：只提供 _execute_single 真正读到的属性。"""
    return SimpleNamespace(
        object_key=object_key, column_map={}, physical_table="t_case",
        dataset_ref="ds_ref", datasource_id=1,
        guardrail=SimpleNamespace(query_mode="local"))


def _plan(sql="SELECT 1", provenance=None, warnings=None):
    return SimpleNamespace(
        sql=sql, warnings=list(warnings or []), dialect="mysql",
        provenance=provenance if provenance is not None else {},
        guardrail=SimpleNamespace(query_mode="local", size_class="small",
                                  allow_full_scan=False))


def _forbid_evolution(monkeypatch):
    """把落候选口换成“一调就炸”：用来证明某些分支**结构上**不可能产生工单（D4）。"""
    from backend.modules.catalog.services import evolution_service

    def _boom(*a, **k):
        raise AssertionError("forbidden/no_source 不得产生候选工单（D4：不越权建模）")

    monkeypatch.setattr(evolution_service, "record_parse_fail_terms", _boom)


# ── 1. 纯函数：白名单 / 裁剪 / 去重 / 不漏物理名 ────────────────────


def test_safe_unresolved_whitelists_reason_and_type():
    """非白名单的 reason/type 不得原样透出（内部枚举不是契约）。"""
    out = safe_unresolved({"unresolved_terms": [
        {"term": "状态", "type": "dimension", "reason": "fuzzy_rejected",
         "candidates": ["订单状态"]},
        {"term": "设备", "type": "physical_column", "reason": "internal_only"},
    ]})
    assert out[0] == {"term": "状态", "type": "dimension",
                      "reason": "fuzzy_rejected", "candidates": ["订单状态"]}
    # 未知 type 置空、未知 reason 归一为 unresolved —— 不猜、不丢词
    assert out[1]["type"] == "" and out[1]["reason"] == "unresolved"


def test_safe_unresolved_drops_leak_keyword_candidates():
    """候选里含已知泄露关键字（catalog/datasource/physical_table）的条目要剔除。

    这里**不做形态判定**（如“带点号就是库.表”）——护栏禁止用内容形态规则判敏感：
    形态规则既拦不全（`adt_order.status` 不带任何关键字），又会误伤合法业务名
    （现网就有 `avg_cases_per_device` / `view_name` 这类下划线名称）。
    物理名的真正防线是上游：planner 的 `candidates` 只来自字典业务名（F19 口径），
    本函数只负责把越界的**已知**泄露形态丢掉。
    """
    out = safe_unresolved(reason="fuzzy_rejected", term="状态", type="dimension",
                          candidates=["订单状态", "physical_table=t_order",
                                      "catalog_ref=x", "datasource:prd-cn"])
    assert out[0]["candidates"] == ["订单状态"]


def test_safe_unresolved_dedupes_and_caps():
    """同词同因只留一条；总条数与候选数都有上限（防止一次回一屏）。"""
    terms = [{"term": "指标A", "type": "metric", "reason": "not_found"} for _ in range(12)]
    out = safe_unresolved({"unresolved_terms": terms})
    assert len(out) == 1, out
    many = safe_unresolved(reason="unresolved", term="词",
                           candidates=[f"候选{i}" for i in range(20)])
    assert len(many[0]["candidates"]) == 8


def test_safe_unresolved_ignores_blank_term():
    """空词不成缺口：不得产出 `{"term": ""}` 这种噪声条目。"""
    assert safe_unresolved({"unresolved_terms": [{"term": "   "}]}) == []
    assert safe_unresolved(reason="no_source") == []


# ── 2. 三种失败必须分开（D4） ────────────────────────────────────


def test_unbound_branch_marks_unbound(monkeypatch):
    """对象建模了但本数据源没绑定 → `unbound`（不是 no_source，更不是 forbidden）。"""
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver

    monkeypatch.setattr(binding_resolver, "resolve_binding",
                        lambda *a, **k: (None, []))
    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "case", "metrics": ["case_count"], "datasource_id": 1}, None))
    assert is_error is True
    assert payload["unresolved"] == [
        {"term": "case", "type": "object", "reason": "unbound", "candidates": []}]


def test_forbidden_branch_marks_forbidden(monkeypatch):
    """无权限 → `forbidden`：与 unbound 分开，绝不建议用户去建看不见的对象。"""
    from backend.modules.mind.execution import resource_guard
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver

    _forbid_evolution(monkeypatch)
    monkeypatch.setattr(binding_resolver, "resolve_binding",
                        lambda *a, **k: (_binding("case"), []))
    monkeypatch.setattr(resource_guard, "validate_binding",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("无权访问")))
    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "case", "metrics": ["case_count"], "datasource_id": 1}, None))
    assert is_error is True
    assert payload["unresolved"] == [
        {"term": "case", "type": "object", "reason": "forbidden", "candidates": []}]


def test_no_source_branch_marks_no_source(monkeypatch):
    """未定源 → `no_source`，且只给站点/源业务名，不给对象绑定细节。"""
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq

    _forbid_evolution(monkeypatch)
    monkeypatch.setattr(sq, "_object_source_candidates", lambda ctx, key: [])
    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "rum_event", "metrics": ["pv"]},
        _ctx(site_code="global", system=False, available=[])))
    assert is_error is True
    assert payload["unresolved"][0]["reason"] == "no_source"
    assert payload["unresolved"][0]["term"] == "rum_event"


def test_ambiguous_source_marks_no_source_with_source_names():
    """多源歧义也是 no_source（缺的是"选哪个源"），候选只放源业务名。"""
    from backend.modules.mind.execution.sdk_tools.semantic_query import (
        ambiguous_source_payload,
    )

    payload = ambiguous_source_payload("case", [
        {"site": "cn", "datasource": "prd-cn"},
        {"site": "jp", "datasource": "prd-jp"},
    ])
    gap = payload["unresolved"][0]
    assert gap["reason"] == "no_source" and gap["term"] == "case"
    assert set(gap["candidates"]) == {"cn（prd-cn）", "jp（prd-jp）"}


# ── 3. 失败与部分成功都要带缺口 ──────────────────────────────────


def test_guardrail_reject_carries_unresolved(monkeypatch):
    """planner 拒绝（空 SQL）时缺口仍要交出，否则 Chat 只能复述一段自然语言。"""
    from backend.modules.mind.execution import resource_guard
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver, planner

    monkeypatch.setattr(binding_resolver, "resolve_binding",
                        lambda *a, **k: (_binding("case"), []))
    monkeypatch.setattr(resource_guard, "validate_binding", lambda *a, **k: None)
    monkeypatch.setattr(planner, "plan", lambda q, b: _plan(
        sql="", warnings=["维度过大"],
        provenance={"unresolved_terms": [
            {"term": "渠道", "type": "dimension", "reason": "not_found",
             "candidates": ["获客渠道"]}]}))
    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "case", "metrics": ["case_count"], "datasource_id": 1}, None))
    assert is_error is True and payload["error"] == "语义层护栏拒绝了本次查询"
    assert payload["unresolved"][0]["term"] == "渠道"
    assert payload["unresolved"][0]["reason"] == "not_found"


def test_partial_success_carries_unresolved(monkeypatch):
    """取数跑通了但有词没认出来：结果照常回，缺口一并回（不得当全量交付）。"""
    from backend.modules.mind.execution import resource_guard
    from backend.modules.mind.execution.sdk_tools import context as sdk_context
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver, execute, planner

    monkeypatch.setattr(binding_resolver, "resolve_binding",
                        lambda *a, **k: (_binding("case"), []))
    monkeypatch.setattr(resource_guard, "validate_binding", lambda *a, **k: None)
    monkeypatch.setattr(planner, "plan", lambda q, b: _plan(
        provenance={"unresolved_terms": [
            {"term": "首响时长", "type": "metric", "reason": "fuzzy_rejected",
             "candidates": ["首次响应时长"]}]}))
    # 首次 import 时会解析 CASES；用 §1 数值字面量构造 result 之外的字段均可
    monkeypatch.setattr(execute, "execute_plan", lambda *a, **k: SimpleNamespace(
        columns=[SimpleNamespace(name="case_count")], rows=[(3,)],
        row_count=1, elapsed_ms=5, applied_rls=[], masked_columns=[]))
    monkeypatch.setattr(sdk_context, "record_unattended_query",
                        lambda *a, **k: None)
    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "case", "metrics": ["case_count"], "datasource_id": 1}, None))
    assert is_error is False, payload
    assert payload["rows"] == [{"case_count": 3}]
    assert payload["unresolved"][0]["term"] == "首响时长"
    assert payload["unresolved"][0]["candidates"] == ["首次响应时长"]


def test_exec_error_carries_unresolved(monkeypatch):
    """执行期被拒（闸门/RLS）也带缺口：一维未解析常常正是 SQL 被拒的因。"""
    from backend.modules.mind.execution import resource_guard
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver, execute, planner
    from backend.semantics.contract import ErrorCode, SemanticError

    monkeypatch.setattr(binding_resolver, "resolve_binding",
                        lambda *a, **k: (_binding("case"), []))
    monkeypatch.setattr(resource_guard, "validate_binding", lambda *a, **k: None)
    monkeypatch.setattr(planner, "plan", lambda q, b: _plan(
        provenance={"unresolved_terms": [
            {"term": "客户等级", "type": "dimension", "reason": "unresolved"}]}))

    def _boom(*a, **k):
        raise SemanticError(ErrorCode.RLS_BLOCKED, "行级策略拒绝", blocked_at="rls")

    monkeypatch.setattr(execute, "execute_plan", _boom)
    monkeypatch.setattr(sq, "_effective_db_type", lambda ds: "")
    monkeypatch.setattr(sq, "record_execute_error", lambda *a, **k: None)
    payload, is_error = asyncio.run(sq._execute_single(
        {"object": "case", "metrics": ["case_count"], "datasource_id": 1}, None))
    assert is_error is True
    assert payload["unresolved"][0]["term"] == "客户等级"
    assert payload["unresolved"][0]["reason"] == "unresolved"


# ── 4. 无缺口时不得凭空造条目 ───────────────────────────────────


def test_no_gap_no_field(monkeypatch):
    """解析干净时不得出现 `unresolved: []` 空数组（省 token，也避免诱导模型追问）。"""
    from backend.modules.mind.execution import resource_guard
    from backend.modules.mind.execution.sdk_tools import semantic_query as sq
    from backend.semantics import binding_resolver, planner

    monkeypatch.setattr(binding_resolver, "resolve_binding",
                        lambda *a, **k: (_binding("case"), []))
    monkeypatch.setattr(resource_guard, "validate_binding", lambda *a, **k: None)
    monkeypatch.setattr(planner, "plan", lambda q, b: _plan(sql="", provenance={}))
    payload, _is_error = asyncio.run(sq._execute_single(
        {"object": "case", "metrics": ["case_count"], "datasource_id": 1}, None))
    assert "unresolved" not in payload