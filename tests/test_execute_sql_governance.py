"""execute_sql 治理硬门禁: 生成的 SQL 必须先过权限审核, 严禁直连数据源执行。

锁定不变量(护栏 §1/§6/§9):
1. execute_sql 一律经 execute_query_with_permission(带身份)执行, 不裸连;
2. 权限审核拒绝(PermissionError)时在到达数据源前即中止, 返回错误不外泄;
3. 源码级守卫: query_tools 的 execute_sql 不得直接引用 execute_query/get_connection/cur.execute,
   防止后续被改成绕过治理的裸执行通道;
4. 成功/拒绝/失败均落 adh_query_audit —— 历史缺陷: 只有语义路经 gates._audit 落审计,
   nl2sql 的 execute_sql 一条不落, 管理员在「查询历史」页无法核对实际执行的是哪张表
   (会话 1783069459151 里“AI 说查 observability_rum、审计只见 logstore-rum”的争议根源)。
"""
import ast
import json
import re
from pathlib import Path

import pandas as pd
import pytest

import backend.core.query_executor as qexec
import backend.modules.mind.execution.sdk_tools.query_tools as qt
from backend.modules.mind.execution.sdk_tools import context as ctxmod
from backend.modules.mind.execution.sdk_tools.query_tools import execute_sql

QUERY_TOOLS = Path(__file__).resolve().parents[1] / "backend/modules/mind/execution/sdk_tools/query_tools.py"


def _set_ctx(extra=None):
    # 用真实 ExecutionContext（含 extra）：查询工具的站点/授权集解析读 ctx.extra，
    # 用 SimpleNamespace 拼假上下文会把它打成 AttributeError（历史回归源）。
    from backend.modules.mind.execution.models import ExecutionContext
    return ctxmod.set_execution_context(
        ExecutionContext(user_id=42, username="alice", workspace_id=7, datasource_id=8,
                         user_role="admin", extra=dict(extra or {})))


def _payload(result):
    return json.loads(result["content"][0]["text"])


@pytest.fixture(autouse=True)
def _hermetic_db_type_lookup(monkeypatch):
    """本文件不验 SLS 扫描窗提醒：把 db_type 判定钉成非 SLS，不让用例为了判定去连元库。

    提醒口径由 tests/test_sls_time_window_notice.py 单独锁定（含 db_type 回源与缓存）。"""
    monkeypatch.setattr(qt, "_datasource_db_type", lambda ds_id: "mysql")


def test_execute_sql_routes_through_governed_executor(monkeypatch):
    """正常查询也必须走 execute_query_with_permission, 且透传身份/工作空间。

    fake 签名与真实契约一致: (sql, datasource_id, user_context, workspace_id,
    database, federated_names) — 旧用例曾按错误签名(query_type 参数)锁定,
    导致调用错位(\"sql\"落 user_context)未被发现。"""
    captured = {}

    def fake_exec(sql, datasource_id, user_context, workspace_id,
                  database="", federated_names=None):
        captured.update(sql=sql, ds=datasource_id,
                        user=user_context, ws=workspace_id)
        return pd.DataFrame([{"a": 1}]), 5, 1

    monkeypatch.setattr(qexec, "execute_query_with_permission", fake_exec)
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    token = _set_ctx()
    try:
        result = __import__("asyncio").run(execute_sql({"sql": "SELECT a FROM t LIMIT 10"}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert "isError" not in result
    # 关键: 走的是受治理入口, 且携带服务端身份(user_id/workspace), 非裸连
    assert captured["user"] == {"user_id": 42, "username": "alice"}
    assert captured["ws"] == 7
    assert _payload(result)["row_count"] == 1


def test_permission_denial_blocks_before_datasource(monkeypatch):
    """权限审核拒绝时, 不得把 SQL 下发数据源; 返回错误且不落数据行。"""
    calls = {"exec": 0}

    def deny(sql, *a, **k):
        calls["exec"] += 1
        raise PermissionError("无权访问表 orders")

    # execute_query_with_permission 内部若真去连库会调用 execute_query; 这里两者都监控
    monkeypatch.setattr(qexec, "execute_query_with_permission", deny)
    monkeypatch.setattr(qexec, "execute_query", lambda *a, **k: calls.__setitem__("raw", calls.get("raw", 0) + 1))
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    token = _set_ctx()
    try:
        result = __import__("asyncio").run(execute_sql({"sql": "SELECT * FROM orders LIMIT 10"}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert result.get("isError") is True
    assert "无权访问" in _payload(result)["error"]
    assert "raw" not in calls  # 从未走裸执行


@pytest.mark.parametrize("sql", [
    "DROP TABLE users",
    "DELETE FROM orders",
    "UPDATE t SET a=1",
    "SELECT 1; DROP TABLE x",
])
def test_non_readonly_rejected_before_execution(monkeypatch, sql):
    """非只读/多语句在安全校验阶段即拒, 不进 execute_query_with_permission。"""
    called = {"n": 0}
    monkeypatch.setattr(qexec, "execute_query_with_permission",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    token = _set_ctx()
    try:
        result = __import__("asyncio").run(execute_sql({"sql": sql}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)
    assert result.get("isError") is True
    assert called["n"] == 0


def test_handler_has_no_bypass_reference():
    """源码守卫: execute_sql 只能引用受治理入口, 不得出现直连数据源的符号。"""
    src = QUERY_TOOLS.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "execute_sql")
    body = ast.unparse(fn)
    # Phase 7.2：执行口统一为 semantics.execute.execute_sql（内部经 execute_query_with_permission）
    assert "semantics.execute" in body and "execute_sql" in body, \
        "execute_sql 必须经 semantics.execute 统一取数执行口"
    # 禁止绕过治理的直连符号
    assert "get_connection" not in body, "execute_sql 不得直连数据源"
    assert not re.search(r"\bexecute_query\b(?!_with_permission)", body), "execute_sql 不得调用未治理的 execute_query"


def test_check_sql_governance_preview_does_not_dump_registry(monkeypatch):
    """check_sql 权限预览不得把全局敏感基线/脱敏注册表整包外发给 LLM。

    历史缺陷：基线里的 activator_ip（业务库的列）被模型当成“RUM 的 IP 被合规屏蔽”。
    口径：只列与本次查询表/列相关的受管控列，其余只报数量并禁止跨字段推断；
    SQL 显式点名的被屏蔽列仍列名（那是调用方自己的输入，保留可操作性）。"""
    from backend.core.enforcer import PermissionResult, permission_enforcer

    def fake_check_access(_uid, _ws, _ds, _table, sensitive_only=False):
        r = PermissionResult()
        r.hidden_columns = ["activator_ip", "mobile", "id_card"]
        r.masked_columns = {"phone": "partial"}
        r.policies_applied = ["sensitive_block:activator_ip,id_card,mobile", "sensitive_fields:phone"]
        return r

    monkeypatch.setattr(permission_enforcer, "_extract_tables", lambda sql: ["t_rum"])
    monkeypatch.setattr(permission_enforcer, "_policy_table", lambda t, ds: t)
    monkeypatch.setattr(permission_enforcer, "check_access", fake_check_access)
    # 本次表真实列：只有 phone/mobile 属于 t_rum；activator_ip/id_card 是别的字段
    monkeypatch.setattr(qt, "_governed_columns_in_scope", lambda sql, ds, tables: {"phone", "mobile"})
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    monkeypatch.setattr(qt, "_resolve_datasource_arg", lambda args, ctx, table_hint="": 8)

    token = _set_ctx()
    try:
        result = __import__("asyncio").run(
            qt.check_sql({"sql": "SELECT mobile, phone FROM t_rum LIMIT 10"}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    perm = _payload(result)["permission"]
    dumped = json.dumps(perm, ensure_ascii=False)
    assert "activator_ip" not in dumped, "全局基线列名泄露给 LLM"
    assert "id_card" not in dumped, "全局基线列名泄露给 LLM"
    assert perm["hidden_columns"] == ["mobile"]          # 在表内且被 SQL 点名
    assert perm["masked_columns"] == {"phone": "partial"}
    assert "其他字段" in perm["governance_note"] and "不得据此推断" in perm["governance_note"]
    assert all("activator_ip" not in str(p) for p in perm["policies_applied"]), perm["policies_applied"]


# ── 护栏 §9：SQL 路的查询审计 ────────────────────────────────────

def _audit_spy(monkeypatch):
    rows = []
    monkeypatch.setattr(qexec, "log_audit",
                        lambda **kw: rows.append(kw))
    return rows


def test_execute_sql_success_lands_query_audit_with_question(monkeypatch):
    """成功执行必须落一条 query_type='sql' 的审计，且 question 来自服务端注入。"""
    audits = _audit_spy(monkeypatch)
    monkeypatch.setattr(qexec, "execute_query_with_permission",
                        lambda sql, datasource_id, user_context, workspace_id, **k: (
                            pd.DataFrame([{"a": 1}, {"a": 2}]), 12, 2))
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    token = _set_ctx({"question": "按用户维度统计 6 款 app 使用率"})
    try:
        result = __import__("asyncio").run(
            execute_sql({"sql": "SELECT a FROM t LIMIT 10"}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert "isError" not in result
    assert len(audits) == 1, "execute_sql 成功必须落查询审计"
    row = audits[0]
    assert row["query_type"] == "sql" and row["status"] == "success"
    assert row["row_count"] == 2 and row["time_ms"] == 12
    assert row["user_id"] == 42 and row["role"] == "admin"
    assert row["datasource_id"] == 8
    assert row["question"] == "按用户维度统计 6 款 app 使用率"
    assert "SELECT a FROM t" in row["sql"], "审计必须留实际执行的语句供核对"


def test_execute_sql_denial_and_error_land_audit(monkeypatch):
    """拒绝与执行失败同样留痕（拒绝原因可查），且不影响对外脱敏文案。"""
    from backend.semantics.contract import EXEC_FAIL_HINT

    audits = _audit_spy(monkeypatch)
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    token = _set_ctx({"question": "查手机号"})
    try:
        # 1) 权限拒绝
        monkeypatch.setattr(qexec, "execute_query_with_permission",
                            lambda *a, **k: (_ for _ in ()).throw(PermissionError("无权访问表 orders")))
        denied = __import__("asyncio").run(execute_sql({"sql": "SELECT * FROM orders LIMIT 10"}))
        # 2) 引擎执行失败
        monkeypatch.setattr(qexec, "execute_query_with_permission",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("connection reset")))
        failed = __import__("asyncio").run(execute_sql({"sql": "SELECT * FROM orders LIMIT 10"}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert denied.get("isError") is True and failed.get("isError") is True
    assert len(audits) == 2, audits
    by_status = {r["status"]: r for r in audits}
    assert by_status["denied"]["error"] == "无权访问表 orders"
    assert by_status["denied"]["query_type"] == "sql"
    assert "connection reset" in by_status["error"]["error"], "对内审计留真实错因"
    # 对外仍然脱敏（护栏 §7）：不给 LLM 回原始引擎报错
    assert _payload(failed)["error"] == EXEC_FAIL_HINT


def test_internal_call_without_identity_does_not_write_query_audit(monkeypatch):
    """无真实身份（user_id=0）的内部调用不落审计行——与 _log_permission_audit 同口径。"""
    from backend.modules.mind.execution.models import ExecutionContext

    audits = _audit_spy(monkeypatch)
    monkeypatch.setattr(qexec, "execute_query_with_permission",
                        lambda sql, datasource_id, user_context, workspace_id, **k: (
                            pd.DataFrame([{"a": 1}]), 3, 1))
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    token = ctxmod.set_execution_context(
        ExecutionContext(user_id=0, username="", workspace_id=0, datasource_id=8))
    try:
        result = __import__("asyncio").run(
            execute_sql({"sql": "SELECT a FROM t LIMIT 10"}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert "isError" not in result
    assert audits == [], "仅真实用户调用落审计行"


def test_check_sql_precheck_does_not_write_query_audit(monkeypatch):
    """check_sql 不执行、不返数据行，不应往查询审计里制造噪音行。"""
    from backend.core.enforcer import PermissionResult, permission_enforcer

    audits = _audit_spy(monkeypatch)
    monkeypatch.setattr(permission_enforcer, "_extract_tables", lambda sql: ["t_a"])
    monkeypatch.setattr(permission_enforcer, "_policy_table", lambda t, ds: t)
    monkeypatch.setattr(permission_enforcer, "check_access",
                        lambda *a, **k: PermissionResult())
    monkeypatch.setattr(qt, "_governed_columns_in_scope", lambda sql, ds, tables: set())
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    token = _set_ctx({"question": "x"})
    try:
        result = __import__("asyncio").run(qt.check_sql({"sql": "SELECT a FROM t_a LIMIT 10"}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    assert _payload(result)["verdict"] == "ok"
    assert audits == []


def test_secure_sdk_injects_question_for_audit():
    """源码守卫：本轮问题必须在 set_execution_context 前注入 ctx.extra，
    否则 execute_sql 审计行永远拿不到 question（服务端注入不能依赖 LLM 传参）。"""
    src = (Path(__file__).resolve().parents[1]
           / "backend/modules/mind/execution/secure_sdk.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_execute_stream")
    body = ast.unparse(fn)
    # ast.unparse 会统一引号风格，得两种都认
    injected = re.search(r"task\.context\.extra\[(['\"])question\1\]", body)
    assert injected, "必须服务端注入本轮问题作为审计 question"
    assert body.index(injected.group(0)) < body.index("set_execution_context("), \
        "注入必须早于上下文发布，工具 handler 才读得到"


def test_execute_sql_dialect_failure_is_not_masked_as_retry(monkeypatch):
    """确定性方言缺陷不得被说成“系统侧连接失败，稍后重试”（会话 1783069459277）。

    锁三件事：① 分类在执行口做完并随结果下发（failure_category）；
    ② 生效源回显（业务名+方言）必须跟着错误一起给，否则模型无从发现
       “写到 MySQL 上的其实是 SLS 方言”；③ 原始报错仍只在服务端日志/审计（护栏 §7）。
    """
    audits = _audit_spy(monkeypatch)
    monkeypatch.setattr(qt, "_huge_scan_guard", lambda sql, ds: None)
    monkeypatch.setattr(qt, "_datasource_display_name", lambda ds_id: "prd-cn")

    def fail(sql, datasource_id=None, user_context=None, workspace_id=0):
        raise RuntimeError('(1064, You have an error in your SQL syntax near \'""rum_event""\')')

    monkeypatch.setattr(qexec, "execute_query_with_permission", fail)
    token = _set_ctx({"question": "近3月按 IP 关联用户操作路径"})
    try:
        result = __import__("asyncio").run(
            execute_sql({"sql": 'SELECT COUNT(*) FROM "rum_event" LIMIT 10'}))
    finally:
        ctxmod.ExecutionContextVar.reset(token)

    payload = _payload(result)
    assert result["isError"] is True
    assert payload["failure_category"] == "sql_dialect"
    assert "方言" in payload["error"] and "重试不会好" in payload["error"]
    assert payload["target"] == {"source_name": "prd-cn", "dialect": "mysql"}
    assert "1064" not in payload["error"] and "rum_event" not in payload["error"]
    by_status = {r["status"]: r for r in audits}
    assert "1064" in by_status["error"]["error"], "对内审计必须留真实错因"
