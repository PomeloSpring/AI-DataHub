"""AS-BOT 系统能力 + 检索系统级硬限定回归（离线，无 DB/CLI/SDK）。"""
import asyncio
import json

import pytest

from backend.modules.mind.execution.models import ExecutionContext
from backend.modules.mind.execution.sdk_tools import system_tools as st
from backend.modules.mind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
from backend.modules.mind.rag import qmind_retriever as qr


def _payload(result):
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def ctx():
    token = set_execution_context(ExecutionContext(user_id=7, workspace_id=3, user_role="admin"))
    yield
    ExecutionContextVar.reset(token)


def _patch_role(monkeypatch, role):
    from backend.common import auth
    monkeypatch.setattr(auth, "resolve_execution_owner",
                        lambda uid, ws: {"user_id": uid, "workspace_id": ws, "role": role})


# ── 管理员门禁（fail-closed）──────────────────────────────────────

def test_admin_identity_fail_closed_without_user(monkeypatch):
    from backend.common import auth
    called = []
    monkeypatch.setattr(auth, "resolve_execution_owner", lambda uid, ws: called.append(uid))
    token = ExecutionContextVar.set(ExecutionContext(user_id=0))
    try:
        assert st._admin_identity() is None
    finally:
        ExecutionContextVar.reset(token)
    assert called == []  # 无身份不查库


@pytest.mark.parametrize("role", ["viewer", "analyst", ""])
def test_system_usage_denies_non_admin(ctx, monkeypatch, role):
    _patch_role(monkeypatch, role)
    out = asyncio.run(st.system_usage({"days": 3}))
    assert out.get("isError") and "管理员" in _payload(out)["error"]


def test_system_overview_denies_non_admin(ctx, monkeypatch):
    _patch_role(monkeypatch, "viewer")
    out = asyncio.run(st.system_overview({}))
    assert out.get("isError")


# ── system_usage 复用 observability_service ────────────────────────

def test_system_usage_answers_chat_activity(ctx, monkeypatch):
    _patch_role(monkeypatch, "admin")
    from backend.modules.auth.services import observability_service as obs
    captured = {}
    def fake_summary(params):
        captured.update(params)
        return {"summary": {"users": 4, "turns": 12, "sessions": 6, "total_tokens": 999,
                            "credits": 1.5, "errors": 0}, "daily": [{"dt": "2026-09-21", "turns": 12}]}
    monkeypatch.setattr(obs, "usage_summary", fake_summary)
    out = _payload(asyncio.run(st.system_usage({"days": 3, "entrypoint": "chat"})))
    assert out["has_usage"] is True and out["active_users"] == 4
    assert captured["entrypoint"] == "chat" and captured["start"] and captured["end"]


def test_system_usage_rejects_bad_entrypoint(ctx, monkeypatch):
    _patch_role(monkeypatch, "admin")
    from backend.modules.auth.services import observability_service as obs
    captured = {}
    monkeypatch.setattr(obs, "usage_summary", lambda p: captured.update(p) or {"summary": {}, "daily": []})
    asyncio.run(st.system_usage({"days": 3, "entrypoint": "'; DROP TABLE x;--"}))
    assert captured["entrypoint"] == ""  # 非白名单入口被清空，杜绝注入/串味


def test_system_usage_by_user_grouping(ctx, monkeypatch):
    _patch_role(monkeypatch, "admin")
    from backend.modules.auth.services import observability_service as obs
    monkeypatch.setattr(obs, "usage_by_user", lambda p: [{"user_id": 7, "turns": 5}])
    out = _payload(asyncio.run(st.system_usage({"days": 3, "group_by": "user"})))
    assert out["group_by"] == "user" and out["users"] == [{"user_id": 7, "turns": 5}]


# ── system_overview 只读快照，best-effort 降级 ─────────────────────

def test_system_overview_counts_and_degrades(ctx, monkeypatch):
    _patch_role(monkeypatch, "admin")
    from backend.common import db
    def fake_query(sql, params=None, fetchone=False):
        if "adh_table_info" in sql:
            raise RuntimeError("模拟单指标查询失败")  # 该项降级为 None，不阻断整体
        return {"c": 3}
    monkeypatch.setattr(db, "execute_query", fake_query)
    out = _payload(asyncio.run(st.system_overview({})))
    ov = out["overview"]
    # datasources 计数正常（adh_datasources 无 is_active 列，旧引用已移除）
    assert ov["datasources"] == 3 and "datasources_active" not in ov
    assert ov["tables"] is None  # 单指标失败降级可诊断，不阻断总览
    assert ov["pending_contract_changes"] == 3


# ── 检索能力叠加：系统形态同样回退业务元数据，但标注来源可区分 ──────

def test_system_scope_falls_back_with_source_marked(monkeypatch):
    monkeypatch.setattr(qr, "_bound_qmind_kbs", lambda kb_ids: [])
    monkeypatch.setattr(qr, "_fallback_graphrag",
                        lambda *a, **k: {"chunks": [{"content": "biz"}], "count": 1, "rag_source": "graphrag"})
    out = qr.qmind_retrieve("近3天有谁用chat", 0, [], system_scope=True)
    # 能力叠加：回退业务检索（不硬限定），但 system_scope 标注供分桶归因
    assert out["rag_source"] == "graphrag" and out["count"] == 1
    assert out.get("system_scope") is True


def test_non_system_scope_still_falls_back(monkeypatch):
    monkeypatch.setattr(qr, "_bound_qmind_kbs", lambda kb_ids: [])
    monkeypatch.setattr(qr, "_fallback_graphrag", lambda *a, **k: {"chunks": [{"content": "biz"}], "count": 1, "rag_source": "graphrag"})
    out = qr.qmind_retrieve("销售额", 5, [])
    assert out["rag_source"] == "graphrag" and out["count"] == 1


def test_system_scope_circuit_open_falls_back_marked(monkeypatch):
    monkeypatch.setattr(qr, "_bound_qmind_kbs", lambda kb_ids: [{"id": 1, "name": "sys", "notebook_id": "nb", "cfg": {}}])
    monkeypatch.setattr(qr, "_breaker_allowed", lambda now=None: False)
    called = []
    monkeypatch.setattr(qr, "_fallback_graphrag",
                        lambda *a, **k: called.append(1) or {"chunks": [], "count": 0, "rag_source": "graphrag"})
    out = qr.qmind_retrieve("用量", 0, [1], system_scope=True)
    # 熔断同样透明回退本地 hybrid（能力叠加），降级与来源均标注（可诊断可区分）
    assert called == [1] and out.get("degraded")
    assert "qmind_circuit_open" in out["rag_source"] and out.get("system_scope") is True


def _system_ctx(kb_ids=None):
    """系统域执行上下文（工具授权口径：policy 授权 system 工具组）。"""
    from types import SimpleNamespace
    policy = SimpleNamespace(selection={"system": ["system_usage", "system_overview"]}, as_bot={})
    runtime = SimpleNamespace(policy=policy)
    return ExecutionContext(user_id=7, workspace_id=3,
                            extra={"secure_runtime": runtime,
                                   "bound_knowledge_base_ids": list(kb_ids or [])})


def test_knowledge_search_system_scope_passes_through(monkeypatch):
    from backend.modules.mind.execution.sdk_tools import semantic_tools as stl
    token = set_execution_context(_system_ctx([11]))
    seen = {}
    def fake_retrieve(q, ds, kb_ids, system_scope=False):
        seen["system_scope"] = system_scope
        seen["kb_ids"] = kb_ids
        return {"chunks": [], "count": 0, "rag_source": "system_kb_only"}
    monkeypatch.setattr("backend.modules.mind.rag.qmind_retriever.qmind_retrieve", fake_retrieve)
    monkeypatch.setattr("backend.modules.mind.execution.resource_guard.execute_query", lambda *a: [{"id": 11}])
    try:
        asyncio.run(stl.knowledge_search({"question": "近3天有谁用chat"}))
    finally:
        ExecutionContextVar.reset(token)
    assert seen["system_scope"] is True and seen["kb_ids"] == [11]


def test_knowledge_search_business_scope_not_system(monkeypatch):
    from backend.modules.mind.execution.sdk_tools import semantic_tools as stl
    from types import SimpleNamespace
    policy = SimpleNamespace(selection={"semantic": ["knowledge_search"]}, as_bot={})
    token = set_execution_context(ExecutionContext(
        user_id=7, workspace_id=3, datasource_id=5,
        extra={"secure_runtime": SimpleNamespace(policy=policy),
               "bound_knowledge_base_ids": [22]}))
    seen = {}
    def fake_retrieve(q, ds, kb_ids, system_scope=False):
        seen["system_scope"] = system_scope
        return {"chunks": [], "count": 0, "rag_source": "none"}
    monkeypatch.setattr("backend.modules.mind.rag.qmind_retriever.qmind_retrieve", fake_retrieve)
    monkeypatch.setattr("backend.modules.mind.execution.resource_guard.execute_query", lambda *a: [{"id": 22}])
    try:
        asyncio.run(stl.knowledge_search({"question": "销售额"}))
    finally:
        ExecutionContextVar.reset(token)
    assert seen["system_scope"] is False


def test_knowledge_search_multi_questions_run_concurrently(monkeypatch):
    """多问法一次调用内**并发**检索，且结果顺序与入参一致。

    历史缺陷：for 循环串行，2 问 = 2 段检索时间直接叠加（实测一轮 11.0s 里真检索 4.2s）。
    """
    import threading
    import time as _t
    from types import SimpleNamespace

    from backend.modules.mind.execution.sdk_tools import semantic_tools as stl

    policy = SimpleNamespace(selection={"semantic": ["knowledge_search"]}, as_bot={})
    token = set_execution_context(ExecutionContext(
        user_id=7, workspace_id=3, datasource_id=5,
        extra={"secure_runtime": SimpleNamespace(policy=policy),
               "bound_knowledge_base_ids": [22]}))
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    def fake_retrieve(q, ds, kb_ids, system_scope=False):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        _t.sleep(0.05)                      # 模拟单次 qmind 子进程检索
        with lock:
            state["active"] -= 1
        return {"chunks": [{"content": q}], "count": 1, "rag_source": "qmind",
                "hit_object_keys": []}

    monkeypatch.setattr("backend.modules.mind.rag.qmind_retriever.qmind_retrieve", fake_retrieve)
    monkeypatch.setattr("backend.modules.mind.execution.resource_guard.execute_query",
                        lambda *a: [{"id": 22}])
    monkeypatch.setattr(stl, "_attach_routes", lambda r: r)
    monkeypatch.setattr(stl, "_attach_paths", lambda r, keywords=None: r)
    questions = ["官网域名", "中国站网址", "访问入口"]
    try:
        out = _payload(asyncio.run(stl.knowledge_search({"questions": questions})))
    finally:
        ExecutionContextVar.reset(token)

    assert state["max_active"] > 1, "多问法仍在串行检索，耗时会逐问叠加"
    assert [x["question"] for x in out["results"]] == questions   # 并发不得打乱结果顺序
    assert out["total"] == len(questions) and out["attribution"]["qmind_hit"] == len(questions)
    assert all(x["retrieval_source"] == "qmind_hit" for x in out["results"])


def test_get_glossary_queries_all_keywords_in_one_call(monkeypatch):
    """多关键词 = **一次**合并查询 + 未命中词显式可见。

    历史缺陷：逐词 list_terms（5 词 = 5 次建连 + 10 条 SQL），实测并发版也不占优（线程/
    连接开销盖过收益），因此收敛为单条 SQL；本用例锁定“只进服务一次”这个口径。
    """
    from backend.modules.catalog.services import term_service as ts
    from backend.modules.mind.execution.sdk_tools import semantic_tools as stl

    calls = []

    def fake_search_terms(keywords, size=60, datasource_id=None):
        calls.append(list(keywords))
        hit = {"华东": {"id": 1, "term_cn": "华东", "matched_keywords": ["华东"]},
               "活跃用户": {"id": 2, "term_cn": "活跃用户", "matched_keywords": ["活跃用户"]}}
        items = [hit[w] for w in keywords if w in hit]
        return {"total": len(items), "items": items, "queried_keywords": list(keywords)}

    monkeypatch.setattr(ts.TermService, "search_terms", staticmethod(fake_search_terms))
    out = _payload(asyncio.run(stl.get_glossary({"keywords": "华东, 活跃用户, 入口地址,华东"})))

    assert len(calls) == 1, "多关键词仍在逐词往返元库"
    assert calls[0] == ["华东", "活跃用户", "入口地址"]      # 重复词不重查
    assert [i["id"] for i in out["items"]] == [1, 2]        # 合并去重
    assert out["matched_keywords"] == ["华东", "活跃用户"]
    assert out["unmatched_keywords"] == ["入口地址"]        # 0 命中词显式可见，不假装有结果


def test_get_glossary_single_keyword_still_supported(monkeypatch):
    """旧入参 keyword（单数）照常可用，输出结构统一为 items/total。"""
    from backend.modules.catalog.services import term_service as ts
    from backend.modules.mind.execution.sdk_tools import semantic_tools as stl

    monkeypatch.setattr(ts.TermService, "search_terms",
                        staticmethod(lambda keywords, size=60, datasource_id=None: {
                            "total": len(keywords),
                            "items": [{"id": 9, "term_cn": keywords[0],
                                       "matched_keywords": list(keywords)}],
                            "queried_keywords": list(keywords)}))
    out = _payload(asyncio.run(stl.get_glossary({"keyword": "渠道"})))
    assert out["items"][0]["term_cn"] == "渠道" and out["total"] == 1


def test_search_terms_builds_one_or_query(monkeypatch):
    """TermService.search_terms：一词一组 LIKE、词间 OR、与 datasource AND、全参数化。"""
    from backend.modules.catalog.services import term_service as ts

    captured = {}

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = list(params or [])

        def fetchall(self):
            return [{"id": 1, "term_cn": "华东", "term_en": "East", "term_aliases": "华东区",
                     "created_at": None, "updated_at": None}]

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(ts, "get_metadata_conn", lambda: _Conn())
    out = ts.TermService.search_terms(["华东", "活跃用户", "华东", ""])

    assert out["queried_keywords"] == ["华东", "活跃用户"]      # 去重 + 剔空
    sql = captured["sql"]
    assert sql.count("term_cn LIKE") == 2 and " OR " in sql       # 一次查询覆盖全部词
    assert "LIMIT" in sql
    # 字面量不得拼进 SQL（参数化=防注入），% 通配只出现在 params 里
    assert "华东" not in sql and "%华东%" in captured["params"]
    assert out["items"][0]["matched_keywords"] == ["华东"]


# ── 工具组注册 ────────────────────────────────────────────────────

def test_system_group_registered():
    from backend.modules.mind.execution.sdk_tools import TOOL_SERVER_BUILDERS, TOOL_SERVER_TOOLS
    assert "system" in TOOL_SERVER_BUILDERS
    srv, tools = TOOL_SERVER_TOOLS["system"]
    assert srv == "datahub_system" and set(tools) == {"system_usage", "system_overview"}


def test_system_scope_requires_system_tool_group():
    """系统/业务域边界由工具授权承担（取代旧 __system_bot__ 哨兵）。"""
    from types import SimpleNamespace
    from backend.modules.mind.execution.tool_policy import is_system_scope
    sys_policy = SimpleNamespace(selection={"system": ["system_usage"]}, as_bot={})
    biz_policy = SimpleNamespace(selection={"semantic": ["get_metrics"]}, as_bot={})
    assert is_system_scope(sys_policy) is True
    assert is_system_scope(biz_policy) is False


# ── scoped_metadata 两视图按工具授权分域断言 ────────────────────

class TestScopedMetadataViewsByToolAuth:
    """元数据/本体可见域随工具授权切两视图（源本体已退役合并为 business）：
    system 能力 → 系统本体 ∪ 触达本源的 business；业务域 → 仅触达本源的 business。
    合并后 SQL 不再按 kind 硬过滤（退役 kind='source' 分支），可见域收口移到 Python
    `_model_visible`（按模型触达源 主源∩deploy_sites 与授权源集判定），故断言喂入行→投影结果。"""

    def _ctx(self, system: bool):
        from types import SimpleNamespace
        policy = SimpleNamespace(selection={"system": ["system_usage"]} if system
                                 else {"semantic": ["get_metrics"]}, as_bot={})
        return ExecutionContext(user_id=7, workspace_id=3, datasource_id=1,
                                extra={"as_bot_key": "t",
                                       "secure_runtime": SimpleNamespace(policy=policy, tool_tasks=set())})

    def _fake_query(self, sink):
        """只对本体模型查询喂三行：系统 / 触达本源(ds=1) / 触达他源(ds=2)；其余查询返回空。"""
        rows = [
            {"id": 100, "name": "系统本体", "status": "active", "kind": "system",
             "datasource_id": 0, "deploy_sites": None, "json_content": '{"objects":[]}'},
            {"id": 200, "name": "本源业务体", "status": "active", "kind": "business",
             "datasource_id": 1, "deploy_sites": None, "json_content": '{"objects":[]}'},
            {"id": 300, "name": "他源业务体", "status": "active", "kind": "business",
             "datasource_id": 2, "deploy_sites": None, "json_content": '{"objects":[]}'},
        ]

        def fake(sql, params=None, fetchone=False):
            sink.append((sql, params))
            return rows if "adh_ontology_models" in sql else []
        return fake

    def test_system_scope_sees_system_plus_business(self, monkeypatch):
        """能力叠加：system 能力可见系统本体 ∪ 触达本源的 business（不互斥）。"""
        from backend.modules.mind.execution.sdk_tools import scoped_metadata as sm
        calls = []
        monkeypatch.setattr(sm, "execute_query", self._fake_query(calls))
        out = sm.execute("list_ontology_models", {}, self._ctx(system=True))
        sql = calls[0][0]
        # 合并后源本体退役：SQL 不再出现 kind='source'，可见性由 Python 侧收口
        assert "kind = 'source'" not in sql
        ids = {m["id"] for m in out["models"]}
        assert ids == {100, 200}, ids   # 系统本体 + 本源业务；他源(300) 不泄漏

    def test_business_scope_sees_business_and_own_source(self, monkeypatch):
        """业务域无系统能力：仅触达本源(ds=1)的 business 可见，系统/他源均排除。"""
        from backend.modules.mind.execution.sdk_tools import scoped_metadata as sm
        calls = []
        monkeypatch.setattr(sm, "execute_query", self._fake_query(calls))
        out = sm.execute("list_ontology_models", {}, self._ctx(system=False))
        sql = calls[0][0]
        assert "kind = 'source'" not in sql and "kind = 'system'" not in sql
        ids = {m["id"] for m in out["models"]}
        assert ids == {200}, ids   # 系统本体(100) 无能力不可见；他源(300) 不泄漏

    def test_business_scope_requires_datasource_fail_closed(self):
        from backend.modules.mind.execution.sdk_tools import scoped_metadata as sm
        ctx = self._ctx(system=False)
        ctx.datasource_id = 0
        with pytest.raises(PermissionError, match="数据源"):
            sm.execute("search_ontology", {}, ctx)

    def test_unselected_source_marks_incomplete_scope(self, monkeypatch):
        """未选源时目录不完整必须显式标注（防'上下文不完整'被误报成'对象不存在'）。"""
        from backend.modules.mind.execution.sdk_tools import scoped_metadata as sm
        monkeypatch.setattr(sm, "execute_query",
                            lambda sql, params=None, fetchone=False: [])
        ctx = self._ctx(system=True)
        ctx.datasource_id = 0
        out = sm.execute("get_metrics", {}, ctx)
        assert out["total"] == 0
        assert out.get("incomplete_scope") is True
        assert "目录为空≠对象不存在" in out.get("note", "")
        out2 = sm.execute("get_metrics", {}, self._ctx(system=True))  # 已选源则完整、不标注
        assert "incomplete_scope" not in out2

    def test_system_scope_dict_condition_unified(self, monkeypatch):
        """get_metrics 字典作用域统一 `(本源 OR 全局)`（系统对象字典行在 ds=0 内）。"""
        from backend.modules.mind.execution.sdk_tools import scoped_metadata as sm
        for system in (True, False):
            calls = []
            monkeypatch.setattr(sm, "execute_query",
                                lambda sql, params=None, fetchone=False: calls.append((sql, params)) or [])
            sm.execute("get_metrics", {}, self._ctx(system=system))
            dict_calls = [c for c in calls if "adh_metrics" in c[0] or "adh_dimensions" in c[0]]
            assert dict_calls and all("datasource_id = %s OR datasource_id = 0" in c[0]
                                      for c in dict_calls)
