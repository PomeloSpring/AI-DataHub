"""AS-BOT 权限、目录、调用守卫与真实 MySQL 并发的回归门禁。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import HTTPException

from services.datamind.execution import tool_policy as policies
from services.datamind.execution import session_workspace as sessions
from services.datamind.execution.manager import _merge_allowed_tools
from services.datamind.execution.models import ExecutionContext, ExecutionTask


@pytest.mark.parametrize("raw", [None, {}, [], "{}", "[]", "null", {"groups": ["semantic"], "mcp": {}},
                                 {"groups": [], "standard": [], "mcp": {}}])
def test_empty_as_bot_never_grants_tools(raw):
    assert not policies.compile_policy({"as_bot_key": "test", "tools": raw}).allowed


@pytest.mark.parametrize("raw", ["bad-json", True, {"standard": ["unknown"]}, {"standard": ["bash"]},
                                 {"mcp": {"semantic": ["nonexistent"]}}, {"groups": ["nonexistent"]},
                                 {"mcp": {"query": ["nonexistent_tool"]}}])
def test_invalid_tools_rejected(raw):
    with pytest.raises(ValueError):
        policies.compile_policy({"as_bot_key": "test", "tools": raw})


def test_query_group_is_as_bot_authorizable():
    """nl2sql 是独立路径：as_bot 显式勾选 query 组即授权受治理的 execute_sql（非裸连旁路）。"""
    p = policies.compile_policy({"as_bot_key": "test", "tools": {"groups": ["query"]}})
    assert p.allowed == {"mcp__datahub_query__check_sql", "mcp__datahub_query__execute_sql"}
    # 未勾选 query 组的 as_bot 不会拿到 execute_sql，二者由 as_bot 职责分离
    semantic_only = policies.compile_policy({"as_bot_key": "test", "tools": {"groups": ["semantic"]}})
    assert not any(n.endswith("execute_sql") for n in semantic_only.allowed)


@pytest.mark.parametrize("a,b,expected", [(None, None, None), (None, [], []), ([], ["read"], []),
                                         (["read"], ["write"], []), (["read", "write"], None, ["read", "write"])])
def test_ceiling_intersection_never_widens(a, b, expected):
    assert _merge_allowed_tools(a, b) == expected


def test_explicit_ceiling_cannot_be_overridden_by_as_bot():
    as_bot = {"as_bot_key": "test", "tools": {"standard": ["read"], "mcp": {"semantic": ["get_metrics"]}}}
    assert not policies.compile_policy(as_bot, []).allowed
    assert policies.compile_policy(as_bot, ["read"]).allowed == {"mcp__datahub_workspace__read"}


def test_external_reference_without_tool_grant_is_not_all():
    with pytest.raises(ValueError, match="逐工具"):
        policies.compile_policy({"as_bot_key": "test", "mcp_server_ids": [7], "tools": {}})
    p = policies.compile_policy({"as_bot_key": "test", "mcp_server_ids": [7],
                                 "tools": {"external": {"7": ["custom_report"]}}})
    assert p.allowed == {"mcp__external_7__custom_report"}


def test_same_session_paths_stable_and_other_sessions_distinct(tmp_path):
    a = sessions.session_paths(tmp_path, "a" * 32, 1, create=True)
    b = sessions.session_paths(tmp_path, "b" * 32, 1, create=True)
    (a / "workspace" / "sentinel").write_text("A")
    assert sessions.session_paths(tmp_path, "a" * 32, 1) == a
    assert a != b and not (b / "workspace" / "sentinel").exists()
    assert not (a / "workspace" / "runtime").exists()
    with pytest.raises(ValueError):
        sessions.session_paths(tmp_path, "../escape", 1)
    with pytest.raises(RuntimeError, match="禁止自动新建"):
        sessions.session_paths(tmp_path, "c" * 32, 1)


def test_parallel_storage_node_publication_is_atomic(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(lambda _: sessions.storage_node(tmp_path), range(30)))
    assert len(set(result)) == 1


@pytest.mark.parametrize("name,args", [("read", {"path": "link"}), ("glob", {"path": ".", "pattern": "*"}),
                                      ("grep", {"path": ".", "pattern": "SECRET"}),
                                      ("write", {"path": "../outside", "content": "bad"})])
def test_worker_rejects_escape_and_symlink(tmp_path, monkeypatch, name, args):
    from services.datamind.execution import sandbox_worker
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("SECRET")
    (root / "link").symlink_to(outside)
    monkeypatch.setattr(sandbox_worker, "ROOT", root)
    with pytest.raises(PermissionError):
        sandbox_worker.execute(name, args)
    assert outside.read_text() == "SECRET"


def test_sandbox_command_mounts_only_session_and_has_no_network(tmp_path):
    from services.aiplatform.services.sandbox_executor import SessionToolSandbox
    p = policies.compile_policy({"as_bot_key": "test", "tools": {"standard": ["read", "bash"]}})
    runtime = sessions.SessionWorkspace("a" * 32, "b" * 32, tmp_path, p, None)
    _, command = SessionToolSandbox.command(runtime, "bash")
    assert "--network=none" in command and "--cap-drop=ALL" in command
    assert "--read-only" in command and "--security-opt=no-new-privileges" in command
    assert command[command.index("--mount") + 1] == f"type=bind,src={tmp_path}/workspace,dst=/workspace,readonly"
    assert not any("docker.sock" in x or ".env" in x for x in command)
    assert "--user" in command


def test_permission_revocation_blocks_old_runtime(monkeypatch):
    first = policies.compile_policy({"as_bot_key": "test", "tools": {"standard": ["read"]}})
    revoked = policies.compile_policy({"as_bot_key": "test", "tools": {}})
    runtime = SimpleNamespace(policy=first, ceiling=None, layer_id=0, verify=Mock())
    ctx = ExecutionContext(extra={"secure_runtime": runtime})
    monkeypatch.setattr(policies, "resolve_policy", lambda *a: revoked)
    with pytest.raises(PermissionError, match="权限已变化"):
        policies.check_tool(ctx, "mcp__datahub_workspace__read")
    runtime.verify.assert_called_once()


def test_missing_runtime_cannot_execute():
    with pytest.raises(PermissionError):
        policies.check_tool(ExecutionContext(), "Read")


def test_tool_handler_rechecks_permissions_before_side_effect(monkeypatch):
    from services.datamind.execution.sdk_tools import compat
    from services.datamind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
    sdk = SimpleNamespace(tool=lambda *a, **k: lambda handler: handler)
    monkeypatch.setattr(compat, "load_sdk", lambda *a: sdk)
    monkeypatch.setattr(policies, "check_tool", Mock(side_effect=PermissionError("未授权")))
    calls = []
    async def operation(args):
        calls.append(args)
        return {"content": []}
    handler = compat.make_tool("qoder", "read", "读取", {"path": str}, operation,
                               qualified_name="mcp__datahub_workspace__read")
    token = set_execution_context(ExecutionContext(user_id=1))
    try:
        result = asyncio.run(handler({"path": "secret"}))
        assert result["isError"] and not calls
    finally:
        ExecutionContextVar.reset(token)


def test_external_tools_are_discovered_and_registered_exactly(monkeypatch):
    from services.datamind.execution.sdk_tools import external_tools
    from contextlib import asynccontextmanager
    p = policies.compile_policy({"as_bot_key": "test", "mcp_server_ids": [9],
                                 "tools": {"external": {"9": ["report"]}}})
    runtime = SimpleNamespace(policy=p)
    ctx = ExecutionContext(user_id=1, workspace_id=2)
    catalog = {n: SimpleNamespace(name=n, description="自定义报表" if n == "report" else "未授权",
                                  inputSchema={"type": "object", "properties": {}}) for n in ("report", "secret")}
    @asynccontextmanager
    async def connection(*args):
        yield SimpleNamespace()
    async def discover(*args):
        return catalog
    monkeypatch.setattr(external_tools, "load_server", lambda *a: {"id": 9, "transport": "http", "url": "https://test.invalid"})
    monkeypatch.setattr(external_tools, "discover", discover)
    monkeypatch.setattr(external_tools, "make_tool", lambda backend, name, *a, **k: name)
    monkeypatch.setattr(external_tools, "make_server", lambda backend, name, tools: {"tools": tools})
    result = asyncio.run(external_tools.build_external_servers("qoder", ctx, runtime))
    assert result == {"external_9": {"tools": ["report"]}}
    assert p.manifest()["tools"][0]["description"] == "自定义报表"
    catalog.pop("report")
    with pytest.raises(ValueError, match="不在 MCP 实际目录"):
        asyncio.run(external_tools.build_external_servers("qoder", ctx, runtime))


def test_failed_cleanup_cannot_release_claim(tmp_path, monkeypatch):
    policy = policies.compile_policy({"as_bot_key": "test"})
    runtime = sessions.SessionWorkspace("a" * 32, "b" * 32, tmp_path, policy, None, unsafe=True)
    write = Mock()
    monkeypatch.setattr(sessions, "execute_write", write)
    with pytest.raises(RuntimeError):
        runtime.finish("", True)
    write.assert_not_called()


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/", "file:///etc/passwd"])
def test_webfetch_denies_private_and_file_urls(url):
    from services.datamind.execution.sdk_tools.workspace_tools import fetch_public_url
    with pytest.raises(PermissionError):
        asyncio.run(fetch_public_url(url))


@pytest.mark.skipif(os.getenv("ADH_TEST_AGENT_MYSQL") != "1", reason="需显式启用隔离测试记录的真实 MySQL 回归")
def test_mysql_claim_concurrency_ownership_and_resume(tmp_path, monkeypatch):
    from services.shared.common.db import execute_insert, execute_write, execute_query
    from services.shared.common import auth
    uid = 2**60 + uuid.uuid4().int % 10**10
    cid = execute_insert("INSERT INTO adh_conversations (user_id,title,workspace_id,as_bot_key,messages) "
                         "VALUES (%s,%s,0,%s,'[]')", (uid, "__agent_security_test__", "security-test"))
    p = policies.compile_policy({"as_bot_key": "security-test", "tools": {}})
    monkeypatch.setattr(sessions, "workspace_base", lambda: tmp_path)
    monkeypatch.setattr(policies, "resolve_policy", lambda *a: p)
    monkeypatch.setattr(auth, "authorize_workspace", lambda *a: 0)

    def task(user=uid, supplied=""):
        return ExecutionTask(task_id=uuid.uuid4().hex, question="test", context=ExecutionContext(
            user_id=user, user_role="admin", workspace_id=0,
            extra={"conversation_id": cid, "as_bot_key": "security-test", "session_id": supplied}))

    def claim():
        try:
            return sessions.claim_session(task(), "qoder", {})
        except HTTPException as exc:
            return exc.status_code
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        successful = [r for r in results if isinstance(r, sessions.SessionWorkspace)]
        assert len(successful) == 1 and 409 in results
        first = successful[0]
        first.finish("trusted-sdk", True)
        resumed = sessions.claim_session(task(), "qoder", {})
        assert resumed.key == first.key and resumed.root == first.root
        assert resumed.sdk_session_id == "trusted-sdk"
        resumed.finish("trusted-sdk", True)
        with pytest.raises(HTTPException) as denied:
            sessions.claim_session(task(user=uid + 1), "qoder", {})
        assert denied.value.status_code == 404
        with pytest.raises(HTTPException) as forged:
            sessions.claim_session(task(supplied="forged-sdk"), "qoder", {})
        assert forged.value.status_code == 403
        assert execute_query("SELECT status FROM adh_agent_sessions WHERE conversation_id=%s", (cid,), fetchone=True)["status"] == "idle"
    finally:
        execute_write("DELETE FROM adh_agent_sessions WHERE conversation_id=%s AND user_id=%s", (cid, uid))
        execute_write("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (cid, uid))


@pytest.mark.skipif(os.getenv("ADH_TEST_AGENT_DOCKER") != "1", reason="需显式启用真实 Docker 沙箱实测")
def test_real_docker_session_isolation_and_readonly(tmp_path):
    from services.aiplatform.services.sandbox_executor import SessionToolSandbox
    writable = policies.compile_policy({"as_bot_key": "test", "tools": {"standard": ["read", "write", "bash"]}})
    readonly = policies.compile_policy({"as_bot_key": "test", "tools": {"standard": ["read", "bash"]}})
    a = sessions.SessionWorkspace("a" * 32, uuid.uuid4().hex,
                                  sessions.session_paths(tmp_path, "a" * 32, 1, create=True), writable, None)
    b = sessions.SessionWorkspace("b" * 32, uuid.uuid4().hex,
                                  sessions.session_paths(tmp_path, "b" * 32, 1, create=True), readonly, None)
    a.verify = lambda: None
    b.verify = lambda: None
    host_secret = tmp_path / "host-secret"
    host_secret.write_text("DO_NOT_LEAK_HOST")

    async def run():
        result = await SessionToolSandbox.run(a, "write", {"path": "note.txt", "content": "SESSION_A"})
        assert result["written"]
        result = await SessionToolSandbox.run(a, "read", {"path": "note.txt"})
        assert result["text"] == "SESSION_A"
        with pytest.raises(ValueError):
            await SessionToolSandbox.run(b, "read", {"path": str(a.workspace / "note.txt")})
        forbidden = await SessionToolSandbox.run(a, "bash", {"command": f"cat {host_secret}"})
        assert forbidden["exit_code"] != 0 and "DO_NOT_LEAK_HOST" not in forbidden["stdout"]
        result = await SessionToolSandbox.run(b, "bash", {"command": "echo forbidden > note.txt"})
        assert result["exit_code"] != 0 and not (b.workspace / "note.txt").exists()
        result = await SessionToolSandbox.run(a, "bash", {"command": "id -u"})
        assert result["stdout"].strip() != "0"
        result = await SessionToolSandbox.run(a, "bash", {"command": "python -c \"import socket; socket.create_connection(('127.0.0.1', 8001), timeout=1)\""})
        assert result["exit_code"] != 0
    asyncio.run(run())


def test_unimplemented_tools_are_reported_but_never_registered():
    # query_by_tags 已落地本体资源域隔离(可注册执行); 仅 task(子代理)隔离尚未验证 → 不注册且显示不可用原因。
    policy = policies.compile_policy({"as_bot_key": "test", "tools": {
        "standard": ["task"], "mcp": {"semantic": ["query_by_tags"]}}})
    assert "mcp__datahub_semantic__query_by_tags" in policy.allowed
    assert "mcp__datahub_workspace__task" not in policy.allowed
    assert not policy.standard  # task 被摘除, 不注册
    manifest = policy.manifest()
    assert len(manifest["unavailable_tools"]) == 1
    assert manifest["unavailable_tools"][0]["name"].endswith("__task")
    assert all(item["reason"] for item in manifest["unavailable_tools"])


def test_tag_datasource_filter_failclosed_branches():
    from services.datacatalog.services import tags_service as ts
    rows = [{"entity_type": "table", "entity_id": "t_a"}, {"entity_type": "table", "entity_id": "t_b"}]
    assert ts._filter_by_datasource(rows, None) is rows   # 不传=旧 REST 行为, 不过滤
    assert ts._filter_by_datasource(rows, []) == []        # fail-closed: 空授权不返回
    assert ts._filter_by_datasource([], [1]) == []         # 无命中


def test_tag_datasource_filter_scopes_by_datasource(monkeypatch):
    # A 源 AS-BOT 查不到 B 源 tagged 实体: 仅 t_a 属于授权数据源。
    from services.datacatalog.services import tags_service as ts

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            self._res = [{"e": "t_a"}] if "adh_table_info" in sql else []
        def fetchall(self): return self._res

    class _Conn:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()

    monkeypatch.setattr(ts, "DBConnection", _Conn)
    rows = [{"entity_type": "table", "entity_id": "t_a"}, {"entity_type": "table", "entity_id": "t_b"}]
    out = ts._filter_by_datasource(rows, [1780478236183])
    assert [r["entity_id"] for r in out] == ["t_a"]


def test_scoped_get_metrics_business_includes_global_dicts_gated_by_model(monkeypatch):
    # 回归: 字典存为 datasource_id=0 时, 业务 get_metrics 必须能看到(与 run_semantic_query 同作用域),
    # 同时模型查询仍严格限定本数据源(不纳入系统模型)。
    import json
    from types import SimpleNamespace
    from services.datamind.execution.sdk_tools import scoped_metadata as sm

    sqls = []
    def fake_exec(sql, params=None, fetchone=False):
        sqls.append(sql)
        if "adh_ontology_models" in sql:
            return [{"id": 1, "name": "m", "status": "active",
                     "json_content": json.dumps({"objects": [{"key": "case"}]})}]
        if "adh_metrics" in sql:
            return [{"bound_object_key": "case", "name": "案例数量", "name_en": "", "aliases": [], "description": "", "unit": ""}]
        if "adh_dimensions" in sql:
            return [{"bound_object_key": "case", "name": "创建日期", "name_en": "", "aliases": [], "description": "", "value_labels": "{}", "category": "时间"}]
        return []

    monkeypatch.setattr(sm, "execute_query", fake_exec)
    ctx = SimpleNamespace(datasource_id=5, extra={})  # 业务 AS-BOT, 业务域
    res = sm.execute("get_metrics", {"keyword": ""}, ctx)
    assert res["total"] == 1 and res["objects"][0]["object"] == "case"
    dict_sqls = [s for s in sqls if ("adh_metrics" in s or "adh_dimensions" in s)]
    assert dict_sqls and all("datasource_id = 0" in s for s in dict_sqls)   # 含全局字典
    model_sql = next(s for s in sqls if "adh_ontology_models" in s)
    # 口径（x3）：模型可见域 = 跨源业务本体 + 本源源本体（源本体仍限本源，不泄其他源）
    assert "kind = 'business'" in model_sql
    assert "kind = 'source' AND datasource_id = %s" in model_sql   # 源本体限本源
    assert "kind = 'system'" not in model_sql                      # 业务 AS-BOT 不见系统本体


def test_broken_as_bot_is_visible_without_hiding_valid_candidates(monkeypatch):
    from services.datamind.execution import as_bots
    rows = [{"id": 1, "as_bot_key": "bad", "tools": "broken"},
            {"id": 2, "as_bot_key": "good", "tools": {}}]
    monkeypatch.setattr(as_bots, "_query", lambda *a: rows)
    candidates = as_bots.resolve_as_bots(0, "admin", include_unavailable=True)
    assert candidates[0]["available"] is False and candidates[0]["unavailable_reason"]
    assert candidates[1]["as_bot_key"] == "good"
    assert as_bots.resolve_as_bots(0, "admin", as_bot_key="good")[0]["as_bot_key"] == "good"
    with pytest.raises(ValueError):
        as_bots.resolve_as_bots(0, "admin", as_bot_key="bad")


def test_screen_guard_rejects_other_workspace_and_nonowner_write(monkeypatch):
    from services.datamind.execution import resource_guard
    policy = policies.compile_policy({"as_bot_key": "test"})
    ctx = ExecutionContext(user_id=1, user_role="admin", workspace_id=3, datasource_id=7,
                           extra={"secure_runtime": SimpleNamespace(policy=policy)})
    monkeypatch.setattr(resource_guard, "bind_resources", lambda *a: None)
    dashboard = {"owner_id": 2, "workspace_id": 4, "is_public": True}
    monkeypatch.setattr(resource_guard, "execute_query", lambda *a, **kw: dashboard)
    with pytest.raises(PermissionError, match="工作空间"):
        resource_guard.validate_tool_resources(ctx, "mcp__datahub_screen__get_data_screen", {"dashboard_id": 8})
    dashboard["workspace_id"] = 3
    with pytest.raises(PermissionError, match="修改"):
        resource_guard.validate_tool_resources(ctx, "mcp__datahub_screen__update_data_screen_chart", {"dashboard_id": 8})


def test_live_upper_limits_and_binding_revocation(monkeypatch):
    """执行层全局生效(不按工作空间绑定): 工具上限取执行层配置, 与绑定无关; 停用仍立即拒绝。"""
    from services.datamind.execution import service
    row = {"id": 4, "status": "active", "config": {"mode": "sdk", "cli_name": "qoder", "allowed_tools": ["read"]}}
    monkeypatch.setattr(service, "get_layer", lambda _: row)
    monkeypatch.setattr(service, "get_workspace_layers", lambda _: [])
    runtime = SimpleNamespace(layer_id=4, layer_bound=True, backend="qoder")
    ctx = ExecutionContext(workspace_id=1)
    assert policies.live_ceiling(ctx, runtime) == ["read"]
    # 执行层停用 → 立即拒绝(有效性校验保留)
    row["status"] = "disabled"
    with pytest.raises(PermissionError, match="停用"):
        policies.live_ceiling(ctx, runtime)


def test_live_ceiling_allows_inherit_when_workspace_has_no_external_bindings(monkeypatch):
    """默认工作空间未绑定任何外部层时, Agent 模式应允许继承系统默认外部层, 不误报撤回。"""
    from services.datamind.execution import service
    row = {"id": 6, "status": "active", "config": {"mode": "sdk", "cli_name": "qoder", "allowed_tools": ["read"]}}
    monkeypatch.setattr(service, "get_layer", lambda _: row)
    # 工作空间未绑定任何层
    monkeypatch.setattr(service, "get_workspace_layers",
                        lambda _: [])
    runtime = SimpleNamespace(layer_id=6, layer_bound=False, backend="qoder")
    # 不抛异常; bound=None → 仅用层自身 ceiling
    assert policies.live_ceiling(ExecutionContext(workspace_id=1), runtime) == ["read"]


def test_live_ceiling_still_revokes_when_other_external_layer_bound(monkeypatch):
    """执行层不按工作空间绑定: 无关绑定不影响本层生效(空间绑定概念已退役)。"""
    from services.datamind.execution import service
    row = {"id": 6, "status": "active", "config": {"mode": "sdk", "cli_name": "qoder", "allowed_tools": ["read"]}}
    monkeypatch.setattr(service, "get_layer", lambda _: row)
    monkeypatch.setattr(service, "get_workspace_layers",
                        lambda _: [{"id": 9, "layer_type": "cli", "allowed_tools": []}])
    runtime = SimpleNamespace(layer_id=6, layer_bound=False, backend="qoder")
    assert policies.live_ceiling(ExecutionContext(workspace_id=1), runtime) == ["read"]


@pytest.mark.parametrize("transport", ["http", "sse", "streamable_http"])
def test_remote_mcp_without_trust_contract_cannot_connect(transport):
    from services.datamind.execution.sdk_tools.external_tools import connection
    async def run():
        with pytest.raises(PermissionError, match="可信身份"):
            async with connection({"transport": transport, "url": "http://127.0.0.1"}, None, None):
                pytest.fail("禁止向未经治理的远端发起请求")
    asyncio.run(run())


def test_webfetch_reads_all_chunks_and_reports_truncation(monkeypatch):
    import aiohttp
    from services.datamind.execution.sdk_tools.workspace_tools import fetch_public_url
    chunks = [b"first", b"second", b""]
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        @property
        def content(self): return self
        async def read(self, size): return chunks.pop(0)[:size]
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(aiohttp, "TCPConnector", lambda **kwargs: None)
    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    assert asyncio.run(fetch_public_url("https://example.com")) == {"text": "firstsecond", "truncated": False}
    chunks[:] = [b"x" * 80_000, b"y" * 30_000]
    result = asyncio.run(fetch_public_url("https://example.com"))
    assert len(result["text"]) == 100_000 and result["truncated"]


def test_bash_nonzero_is_mcp_error(monkeypatch):
    from services.datamind.execution.sdk_tools import workspace_tools
    from services.aiplatform.services.sandbox_executor import SessionToolSandbox
    monkeypatch.setattr(workspace_tools, "make_tool", lambda b, n, d, s, h, **kw: h)
    monkeypatch.setattr(workspace_tools, "make_server", lambda b, n, tools: tools)
    async def run(*args):
        return {"exit_code": 7, "stderr": "failed", "stdout": ""}
    monkeypatch.setattr(SessionToolSandbox, "run", run)
    runtime = SimpleNamespace(policy=SimpleNamespace(standard=["bash"]))
    handler = workspace_tools.build_workspace_server("qoder", runtime)[0]
    assert asyncio.run(handler({"command": "exit 7"}))["isError"]


def test_live_sdk_process_cannot_be_released(tmp_path):
    runtime = sessions.SessionWorkspace("a" * 32, "b" * 32, tmp_path, None, None,
                                        sdk_pid=os.getpid(), sdk_start=sessions.process_identity(os.getpid()))
    with pytest.raises(RuntimeError, match="仍然存活"):
        runtime.confirm_stopped()
    assert runtime.unsafe


@pytest.mark.parametrize("system,binding_source", [(False, 2), (False, 0), (True, 2)])
def test_binding_cannot_escape_domain(monkeypatch, system, binding_source):
    from services.datamind.execution import resource_guard
    # 域判定口径 = 工具授权（system 组），不再看 as_bot_key 哨兵
    tools = {"mcp": {"system": ["system_usage"]}} if system else {}
    policy = policies.compile_policy({"as_bot_key": "test", "tools": tools})
    ctx = ExecutionContext(datasource_id=0 if system else 1,
                           extra={"secure_runtime": SimpleNamespace(policy=policy)})
    monkeypatch.setattr(resource_guard, "execute_query", lambda *a, **kw: {"datasource_id": binding_source})
    with pytest.raises(PermissionError):
        resource_guard.validate_binding(ctx, SimpleNamespace(model_id=3, datasource_id=binding_source))


@pytest.fixture
def datasource_scope(monkeypatch):
    from services.datamind.execution import resource_guard as guard
    from services.authservice.services.role_service import role_service
    # 纯角色裁决: 数据源可用集 = 用户角色授权(state["user"]); 工作空间不再参与裁决。
    state = {"user": [7, 8], "queries": [], "kbs": []}

    def query(sql, params=(), **kwargs):
        state["queries"].append((sql, params))
        if "adh_knowledge_bases" in sql:
            assert "workspace_ids" not in sql and "workspace_id IN" not in sql
            assert params == (4,) and "status='active'" in sql
            return state["kbs"]
        if "FROM adh_datasources" in sql:
            # 供 _resolve_authorized_source / list_datasources 投影：按传入 id 返回业务名+方言。
            return [{"id": i, "name": f"ds-{i}", "db_type": "mysql"} for i in params]
        raise AssertionError(f"意外的查询: {sql}")

    monkeypatch.setattr(guard, "execute_query", query)
    monkeypatch.setattr(role_service, "get_user_allowed_datasources", lambda uid, ws: state["user"])

    def context(selected=7, role="admin", key="test", system=False):
        tools = {"mcp": {"system": ["system_usage"]}} if system else {}
        policy = policies.compile_policy({"as_bot_key": key,
                                          "knowledge_base_ids": [4], "tools": tools})
        ctx = ExecutionContext(user_id=1, user_role=role, workspace_id=3, datasource_id=selected,
                               extra={"as_bot_key": key, "secure_runtime": SimpleNamespace(policy=policy, tool_tasks=set())})
        return ctx, policy

    return guard, state, context


@pytest.mark.parametrize("selected", [7, 8])
def test_as_bot_source_within_role_grant_preserved(datasource_scope, selected):
    """AS-BOT 不持有数据源边界：会话源只要在用户角色授权集内即保留。"""
    guard, state, context = datasource_scope
    ctx, policy = context(selected)
    guard.bind_resources(ctx, policy)
    assert ctx.datasource_id == selected
    assert ctx.extra["bound_knowledge_base_ids"] == [4]


def test_source_outside_role_grant_rejected_without_switching(datasource_scope):
    """会话已选源不在用户角色授权集内 → 拒绝且不静默切换到其他源。"""
    guard, state, context = datasource_scope
    ctx, policy = context(9)
    with pytest.raises(PermissionError, match="当前用户"):
        guard.bind_resources(ctx, policy)
    assert ctx.datasource_id == 9


@pytest.mark.parametrize("user,role,expected", [
    ([7], "admin", 7),        # 角色授权唯一源 → 自动选定
    ([8], "analyst", 8),
    ([7, 8], "admin", 0),     # 多源不猜，等待会话选择
    ([], "admin", 0),         # admin 空授权 fail-closed（不 bypass）
    ([], "analyst", 0),
])
def test_auto_select_only_unique_authorized_source(datasource_scope, user, role, expected):
    guard, state, context = datasource_scope
    state.update(user=user)
    ctx, policy = context(0, role)
    guard.bind_resources(ctx, policy)
    assert ctx.datasource_id == expected


def test_user_grants_still_required_for_inherited_source(datasource_scope):
    guard, state, context = datasource_scope
    state["user"] = []
    ctx, policy = context(7, "analyst")
    with pytest.raises(PermissionError, match="当前用户"):
        guard.bind_resources(ctx, policy)


def test_admin_empty_grants_fail_closed(datasource_scope):
    """admin 不 bypass 纯角色裁决：角色授权为空时，已选源同样被拒（fail-closed）。"""
    guard, state, context = datasource_scope
    state["user"] = []
    ctx, policy = context(7, "admin")
    with pytest.raises(PermissionError, match="当前用户"):
        guard.bind_resources(ctx, policy)
    assert ctx.datasource_id == 7


def test_system_scope_still_inherits_business_sources(datasource_scope):
    """能力叠加（域规则更新）：system 能力不屏蔽业务数据源继承，只附加系统资源面。"""
    guard, state, context = datasource_scope
    ctx, policy = context(7, system=True)
    guard.bind_resources(ctx, policy)
    assert ctx.datasource_id == 7  # 角色授权内保留，不再切系统域独立


def test_business_scope_inherits_role_granted_sources(datasource_scope):
    """未授权 system 组即纯业务域：会话源在角色授权集内正常继承（域边界由工具授权承担）。"""
    guard, state, context = datasource_scope
    ctx, policy = context(8)
    guard.bind_resources(ctx, policy)
    assert ctx.datasource_id == 8


@pytest.mark.parametrize("tool", ["get_metrics", "run_semantic_query", "knowledge_search", "execute_sql", "check_sql"])
def test_ambiguous_scope_requires_session_selection(datasource_scope, tool):
    guard, state, context = datasource_scope
    ctx, _ = context(0)
    with pytest.raises(PermissionError, match="选择"):
        guard.validate_tool_resources(ctx, f"mcp__datahub_semantic__{tool}", {})


def test_role_grant_revocation_applies_to_existing_and_new_instances(datasource_scope):
    """角色授权撤回后：已存在与新建立的会话实例都重新裁决并拒绝（不缓存旧授权）。"""
    guard, state, context = datasource_scope
    first, p1 = context(7)
    second, p2 = context(7)
    guard.bind_resources(first, p1)
    guard.bind_resources(second, p2)
    state["user"] = [8]
    for ctx, policy in ((first, p1), (second, p2), context(7)):
        with pytest.raises(PermissionError, match="当前用户"):
            guard.bind_resources(ctx, policy)


def test_binding_resolution_rechecks_inherited_authorization(datasource_scope):
    guard, state, context = datasource_scope
    ctx, _ = context(7)
    guard.validate_binding(ctx, SimpleNamespace(model_id=3, datasource_id=7))
    state["user"] = []
    with pytest.raises(PermissionError, match="当前用户"):
        guard.validate_binding(ctx, SimpleNamespace(model_id=3, datasource_id=7))


@pytest.mark.parametrize("args", [{}, {"datasource_id": None}, {"datasource_id": 7}])
def test_tool_source_optional_null_uses_confirmed_scope(datasource_scope, args):
    guard, state, context = datasource_scope
    state["user"] = [7]     # 角色授权唯一源 → 自动确认作用域
    ctx, _ = context(0)
    result = guard.validate_tool_resources(ctx, "mcp__datahub_query__execute_sql", args)
    assert ctx.datasource_id == 7 and result["datasource_id"] == 7
    assert args == {} or args["datasource_id"] in (None, 7)


def test_tool_selects_source_by_authorized_name(datasource_scope):
    # 多源场景：LLM 用业务名在角色授权集(=[7,8])内选源，服务端解析 name→id 作生效源
    guard, state, context = datasource_scope
    ctx, _ = context(7)
    result = guard.validate_tool_resources(ctx, "mcp__datahub_query__execute_sql", {"datasource": "ds-8"})
    assert ctx.datasource_id == 8 and result["datasource_id"] == 8
    assert "datasource" not in result  # 名字解析后不残留于参数


def test_tool_rejects_unknown_datasource_name(datasource_scope):
    # 未授权/不存在的源名 → 可操作错误含候选名，供 LLM 问用户；不改写会话源
    guard, state, context = datasource_scope
    ctx, _ = context(7)
    with pytest.raises(PermissionError, match="可用数据源"):
        guard.validate_tool_resources(ctx, "mcp__datahub_query__execute_sql", {"datasource": "nope"})
    assert ctx.datasource_id == 7


def test_tool_ignores_numeric_datasource_id_uses_session(datasource_scope):
    # LLM 不再持有 id：数字 datasource_id（含越权 9）被忽略，以会话源为准
    guard, state, context = datasource_scope
    ctx, _ = context(7)
    result = guard.validate_tool_resources(ctx, "mcp__datahub_query__execute_sql", {"datasource_id": 9})
    assert ctx.datasource_id == 7 and result["datasource_id"] == 7


@pytest.mark.parametrize("workspaces", [[], [3], [0], "[]", "[3]", [9], "bad-json", None])
def test_bound_knowledge_base_ignores_legacy_workspace_binding(datasource_scope, workspaces):
    guard, state, context = datasource_scope
    state["kbs"] = [{"id": 4, "workspace_ids": workspaces}]
    ctx, _ = context(7)
    guard.validate_tool_resources(ctx, "mcp__datahub_semantic__knowledge_search", {"datasource_id": None})


@pytest.mark.parametrize("kbs", [[], [{"id": 9}], [{"id": 4}, {"id": 9}]])
def test_knowledge_scope_still_fail_closed(datasource_scope, kbs):
    guard, state, context = datasource_scope
    state["kbs"] = kbs
    ctx, _ = context(7)
    with pytest.raises(PermissionError):
        guard.validate_tool_resources(ctx, "mcp__datahub_semantic__knowledge_search", {})


def test_empty_knowledge_binding_never_inherits_all_kbs(datasource_scope):
    guard, state, context = datasource_scope
    ctx, policy = context(7)
    policy.as_bot["knowledge_base_ids"] = []
    with pytest.raises(PermissionError, match="未绑定知识库"):
        guard.validate_tool_resources(ctx, "mcp__datahub_semantic__knowledge_search", {})
    assert all("adh_knowledge_bases" not in sql for sql, _ in state["queries"])


def test_resource_scope_failure_keeps_actionable_safe_message(datasource_scope, monkeypatch):
    from services.datamind.execution.sdk_tools import compat
    from services.datamind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
    guard, state, context = datasource_scope
    ctx, _ = context(0)
    monkeypatch.setattr(compat, "load_sdk", lambda *a: SimpleNamespace(tool=lambda *a, **k: lambda h: h))
    monkeypatch.setattr(policies, "check_tool", lambda *a: ctx.extra["secure_runtime"])
    handler_called = []
    async def handler(args):
        handler_called.append(args)
    tool = compat.make_tool("qoder", "get_metrics", "指标", {}, handler)
    token = set_execution_context(ctx)
    try:
        result = asyncio.run(tool({}))
    finally:
        ExecutionContextVar.reset(token)
    assert result["isError"] and not handler_called
    assert "选择" in result["content"][0]["text"]
    assert "datasource_id" not in result["content"][0]["text"]


@pytest.fixture
def mysql_cleanup_session(tmp_path, monkeypatch):
    if os.getenv("ADH_TEST_AGENT_MYSQL") != "1":
        pytest.skip("需显式启用隔离测试记录的真实 MySQL 回归")
    from services.shared.common.db import execute_insert, execute_write, execute_query
    from services.shared.common import auth
    uid = 2**60 + uuid.uuid4().int % 10**10
    key = uuid.uuid4().hex
    monkeypatch.setattr("services.shared.common.config.ADH_WORKSPACES_DIR", str(tmp_path))
    node = sessions.storage_node(tmp_path)
    root = sessions.session_paths(tmp_path, key, 0, create=True)
    (root / "workspace" / "example.txt").write_text("仅清理本测试目录")
    policy = policies.compile_policy({"as_bot_key": "cleanup-test", "tools": {}})
    monkeypatch.setattr(policies, "resolve_policy", lambda *a: policy)
    monkeypatch.setattr(auth, "authorize_workspace", lambda *a: 0)
    cid = execute_insert("INSERT INTO adh_conversations (user_id,title,workspace_id,as_bot_key,messages) "
                         "VALUES (%s,'__cleanup_test__',0,'cleanup-test','[]')", (uid,))
    try:
        execute_write("INSERT INTO adh_agent_sessions (session_key,conversation_id,user_id,workspace_id,as_bot_key,backend,"
                      "storage_node,relative_dir,policy_hash,status) VALUES (%s,%s,%s,0,'cleanup-test','qoder',%s,%s,%s,'idle')",
                      (key, cid, uid, node, str(root.relative_to(tmp_path)), policy.digest))
        yield {"user_id": uid, "id": cid, "key": key, "root": root, "query": execute_query}
    finally:
        execute_write("DELETE FROM adh_agent_sessions WHERE session_key=%s AND user_id=%s", (key, uid))
        execute_write("DELETE FROM adh_conversations WHERE id=%s AND user_id=%s", (cid, uid))


def test_mysql_parallel_delete_cleans_once(mysql_cleanup_session, monkeypatch):
    from services.datamind.api.chat import delete_conversation
    f = mysql_cleanup_session
    removed = []
    remove = sessions._remove_session_directory

    def tracked(path):
        removed.append(path)
        remove(path)

    monkeypatch.setattr(sessions, "_remove_session_directory", tracked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: delete_conversation(f['id'], {'user_id': f['user_id']}), range(2)))
    assert all(result['success'] for result in results)
    assert len(removed) == 1 and not f['root'].exists()
    assert not f['query']("SELECT session_key FROM adh_agent_sessions WHERE conversation_id=%s", (f['id'],))
    assert not f['query']("SELECT id FROM adh_conversations WHERE id=%s", (f['id'],))


def test_mysql_failed_cleanup_blocks_resume_but_allows_delete_retry(mysql_cleanup_session, monkeypatch):
    from services.datamind.api.chat import delete_conversation
    f = mysql_cleanup_session
    remove = sessions._remove_session_directory
    monkeypatch.setattr(sessions, "_remove_session_directory", Mock(side_effect=OSError("清理故障")))
    with pytest.raises(HTTPException) as error:
        delete_conversation(f['id'], {'user_id': f['user_id']})
    assert error.value.status_code == 503
    row = f['query']("SELECT status FROM adh_agent_sessions WHERE conversation_id=%s", (f['id'],), fetchone=True)
    assert row['status'] == 'deleting' and f['root'].exists()
    task = ExecutionTask(task_id=uuid.uuid4().hex, question="不允许恢复待删除会话", context=ExecutionContext(
        user_id=f['user_id'], user_role="admin", workspace_id=0,
        extra={"conversation_id": f['id'], "as_bot_key": "cleanup-test"}))
    with pytest.raises(HTTPException) as blocked:
        sessions.claim_session(task, "qoder", {})
    assert blocked.value.status_code == 409 and '删除' in blocked.value.detail
    monkeypatch.setattr(sessions, "_remove_session_directory", remove)
    assert delete_conversation(f['id'], {'user_id': f['user_id']})['success']
    assert not f['root'].exists()


def test_mysql_delete_and_claim_cannot_race(mysql_cleanup_session, monkeypatch):
    from threading import Event
    from services.datamind.api.chat import delete_conversation
    f = mysql_cleanup_session
    entered, release = Event(), Event()
    remove = sessions._remove_session_directory

    def paused(path):
        entered.set()
        assert release.wait(15)
        remove(path)

    monkeypatch.setattr(sessions, "_remove_session_directory", paused)
    task = ExecutionTask(task_id=uuid.uuid4().hex, question="并发续聊", context=ExecutionContext(
        user_id=f['user_id'], user_role="admin", workspace_id=0,
        extra={"conversation_id": f['id'], "as_bot_key": "cleanup-test"}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(delete_conversation, f['id'], {'user_id': f['user_id']})
        try:
            assert entered.wait(15)
            claiming = pool.submit(sessions.claim_session, task, "qoder", {})
        finally:
            release.set()
        assert deleting.result(timeout=15)['success']
        with pytest.raises(HTTPException) as denied:
            claiming.result(timeout=15)
    assert denied.value.status_code in (404, 409) and not f['root'].exists()


def test_mysql_clear_messages_resets_context_and_keeps_conversation_usable(mysql_cleanup_session):
    """清空 = 清理历史并重置模型上下文；会话保持 idle 可继续聊，不再被 closed 409 阻断。"""
    from services.shared.common.db import execute_write
    from services.datamind.api.chat import update_conversation, UpdateConversationRequest
    f = mysql_cleanup_session
    # 存量旧行处于被误判的 closed，且残留可 resume 的 sdk_session_id
    execute_write("UPDATE adh_agent_sessions SET status='closed', sdk_session_id='old-sdk' WHERE session_key=%s", (f['key'],))
    update_conversation(f['id'], UpdateConversationRequest(messages=[]), {"user_id": f["user_id"]})
    row = f['query']("SELECT status, sdk_session_id, execution_token FROM adh_agent_sessions WHERE conversation_id=%s",
                     (f['id'],), fetchone=True)
    assert row['status'] == 'idle' and not row['sdk_session_id'] and not row['execution_token']
    conv = f['query']("SELECT messages FROM adh_conversations WHERE id=%s", (f['id'],), fetchone=True)
    assert conv['messages'] in ('[]', [])


def test_mysql_clear_messages_blocked_while_running(mysql_cleanup_session):
    """执行中不得清空：拒绝且不把 running 会话重置成 idle。"""
    from services.shared.common.db import execute_write
    from services.datamind.api.chat import update_conversation, UpdateConversationRequest
    f = mysql_cleanup_session
    execute_write("UPDATE adh_agent_sessions SET status='running', execution_token=%s WHERE session_key=%s",
                  (uuid.uuid4().hex, f['key']))
    with pytest.raises(HTTPException) as error:
        update_conversation(f['id'], UpdateConversationRequest(messages=[]), {"user_id": f["user_id"]})
    assert error.value.status_code == 409 and '尚未停止' in error.value.detail
    row = f['query']("SELECT status FROM adh_agent_sessions WHERE conversation_id=%s", (f['id'],), fetchone=True)
    assert row['status'] == 'running'


def test_metadata_projection_excludes_physical_structure(monkeypatch):
    import json
    from services.datamind.execution.sdk_tools import scoped_metadata
    calls = []
    def query(sql, params):
        calls.append((sql, params))
        return [{"id": 3, "name": "订单模型", "status": "active", "json_content": {"objects": [
            {"key": "订单", "display_name": "订单", "primary_table": "secret_table", "properties": [
                {"display_name": "金额", "column": "secret_col"}, {"column": "hidden_col"}]}]}}]
    monkeypatch.setattr(scoped_metadata, "execute_query", query)
    result = scoped_metadata.execute("search_metadata", {}, ExecutionContext(datasource_id=7))
    # 口径（x3）：业务 AS-BOT 见跨源业务本体 + 自己绑定源的源本体；params 仍按源收口
    assert calls[0][1] == (7,)
    assert "kind = 'business'" in calls[0][0] and "kind = 'source' AND datasource_id = %s" in calls[0][0]
    assert "金额" in json.dumps(result, ensure_ascii=False)
    assert "secret" not in json.dumps(result) and "hidden_col" not in json.dumps(result)


def test_list_datasources_projection_returns_authorized_names_no_id(monkeypatch):
    from services.datamind.execution.sdk_tools import scoped_metadata
    seen = {}
    def query(sql, params=(), **kw):
        seen["sql"], seen["params"] = sql, params
        return [{"name": "ds-7", "db_type": "mysql"}, {"name": "ds-8", "db_type": "doris"}]
    monkeypatch.setattr(scoped_metadata, "execute_query", query)
    ctx = ExecutionContext(datasource_id=7, extra={"as_bot_key": "test", "available_datasource_ids": [7, 8]})
    result = scoped_metadata.execute("list_datasources", {}, ctx)
    assert seen["params"] == (7, 8) and "FROM adh_datasources" in seen["sql"]
    assert result["datasources"] == [{"name": "ds-7", "db_type": "mysql"}, {"name": "ds-8", "db_type": "doris"}]
    assert all("id" not in d for d in result["datasources"])  # 守 §7：不回传 id


def test_catalog_strip_physical_ids_keeps_modeling_columns():
    from services.datamind.execution.sdk_tools.catalog_tools import _strip_physical_ids
    data = {"tables": [{"id": 1, "datasource_id": 7, "table_name": "t_case", "table_comment": "案件"}],
            "columns": [{"id": 2, "datasource_id": 7, "column_name": "amount", "data_type": "decimal"}]}
    out = _strip_physical_ids(data)
    assert "datasource_id" not in out["tables"][0] and "id" not in out["tables"][0]
    assert out["tables"][0]["table_name"] == "t_case"
    assert out["columns"][0]["column_name"] == "amount" and out["columns"][0]["data_type"] == "decimal"


def test_as_bot_can_write_sql_detection():
    from services.datamind.execution.sdk_tools.compat import _as_bot_can_write_sql
    sql = SimpleNamespace(policy=SimpleNamespace(allowed=frozenset({"mcp__datahub_query__execute_sql"})))
    sem = SimpleNamespace(policy=SimpleNamespace(allowed=frozenset({"mcp__datahub_semantic__get_metrics"})))
    assert _as_bot_can_write_sql(sql) is True
    assert _as_bot_can_write_sql(sem) is False
    assert _as_bot_can_write_sql(None) is False


def test_sql_capable_as_bot_metadata_tools_bypass_projection(monkeypatch):
    import asyncio
    import json
    from services.datamind.execution.sdk_tools import compat, scoped_metadata
    from services.datamind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar, ExecutionContext
    called = {"handler": 0, "projection": 0}

    async def physical_handler(args):
        called["handler"] += 1
        return {"content": [{"type": "text", "text": json.dumps({"columns": [{"column_name": "amount"}]})}]}

    def fake_execute(name, args, ctx):
        called["projection"] += 1
        return {"objects": []}

    monkeypatch.setattr(scoped_metadata, "execute", fake_execute)
    monkeypatch.setattr(compat, "load_sdk", lambda *a: SimpleNamespace(tool=lambda *x, **k: (lambda h: h)))
    monkeypatch.setattr("services.datamind.execution.tool_policy.check_tool", lambda *a: None)
    monkeypatch.setattr("services.datamind.execution.resource_guard.validate_tool_resources", lambda *a: a[2])

    def run(allowed):
        runtime = SimpleNamespace(policy=SimpleNamespace(allowed=allowed), tool_tasks=set())
        ctx = ExecutionContext(user_id=1, user_role="admin", workspace_id=3, datasource_id=7,
                               extra={"as_bot_key": "test", "secure_runtime": runtime})
        tool = compat.make_tool("qoder", "get_table_schema", "desc", {}, physical_handler,
                                qualified_name="mcp__datahub_catalog__get_table_schema")
        tok = set_execution_context(ctx)
        try:
            asyncio.run(tool({"table_name": "t"}))
        finally:
            ExecutionContextVar.reset(tok)

    run(frozenset({"mcp__datahub_query__execute_sql"}))          # 可写 SQL → 物理 handler
    assert called["handler"] == 1 and called["projection"] == 0
    run(frozenset({"mcp__datahub_semantic__get_metrics"}))       # 语义层 → 本体投影
    assert called["projection"] == 1 and called["handler"] == 1


@pytest.mark.skipif(os.getenv("ADH_TEST_AGENT_DOCKER") != "1", reason="需显式启用真实 Docker 沙箱实测")
def test_docker_cancellation_waits_for_container_removal(tmp_path):
    import subprocess
    from services.aiplatform.services.sandbox_executor import SessionToolSandbox
    policy = policies.compile_policy({"as_bot_key": "test", "tools": {"standard": ["read", "bash"]}})
    runtime = sessions.SessionWorkspace("c" * 32, uuid.uuid4().hex,
        sessions.session_paths(tmp_path, "c" * 32, 1, create=True), policy, None)
    runtime.verify = lambda: None
    async def run():
        operation = asyncio.create_task(SessionToolSandbox.run(runtime, "bash", {"command": "sleep 60"}))
        for _ in range(100):
            running = await asyncio.to_thread(subprocess.run,
                ["docker", "ps", "-q", "--filter", f"label=adh.agent.execution={runtime.token}"],
                capture_output=True, text=True, check=True)
            if running.stdout.strip():
                break
            await asyncio.sleep(0.05)
        else:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            pytest.fail("沙箱未启动")
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        runtime.confirm_stopped()
        assert not runtime.unsafe
    asyncio.run(run())
