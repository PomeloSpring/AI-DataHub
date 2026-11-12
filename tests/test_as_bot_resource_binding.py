"""AS-BOT 唯一资源绑定入口：旧入口退役、权限投影与任务落库形态，全部隔离真实资源。"""
import asyncio
import copy
import importlib
import json
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.common import auth
from backend.modules.mind.execution import as_bots, tool_policy, scheduled_analysis as sa
from backend.modules.mind.execution.sdk_tools import external_tools
from tests.test_scheduled_execution import scheduled_as_bot


@pytest.fixture
def kb_api():
    # 导入时的兼容建表不访问真实元库。
    conn = MagicMock()
    from backend.common.db import get_metadata_conn
    with patch('backend.common.db.get_metadata_conn', return_value=conn):
        module = importlib.import_module('backend.modules.mind.api.knowledge_bases')
    module.get_metadata_conn = get_metadata_conn
    return module


@pytest.mark.parametrize('field', ['workspace_id', 'workspace_ids'])
def test_resource_payload_rejects_legacy_binding(kb_api, field):
    from backend.modules.platform.api.mcp_servers import MCPServerCreate, MCPServerUpdate
    for model, required in [(MCPServerCreate, {'name': 'm'}), (MCPServerUpdate, {}),
                             (kb_api.KnowledgeBaseCreate, {'name': 'k', 'kb_type': 'qmind'}),
                             (kb_api.KnowledgeBaseUpdate, {})]:
        with pytest.raises(ValidationError, match='AS-BOT'):
            model(**required, **{field: [] if field.endswith('ids') else 0})


@pytest.mark.parametrize('field', ['mcp_server_ids', 'mcp_server_id', 'knowledge_base_ids'])
def test_workspace_payload_cannot_bind_resources(field):
    from backend.modules.auth.api.workspaces import CreateWorkspaceRequest, UpdateWorkspaceRequest
    for model in (CreateWorkspaceRequest, UpdateWorkspaceRequest):
        with pytest.raises(ValidationError, match='AS-BOT'):
            model(name='空间', **{field: [1]})


def test_old_mcp_routes_return_410_after_authorization(monkeypatch):
    from backend.modules.auth.api import workspaces as api
    member = Mock()
    conn = MagicMock()
    monkeypatch.setattr(api, '_check_membership', member)
    monkeypatch.setattr(api, 'DBConnection', lambda: conn)
    app = FastAPI()
    app.include_router(api.router, prefix='/workspaces')
    app.dependency_overrides[auth.get_current_user] = lambda: {'user_id': 7, 'role': 'admin'}
    with TestClient(app) as client:
        assert client.post('/workspaces/3/mcp-servers?mcp_server_id=9').status_code == 410
        assert client.delete('/workspaces/3/mcp-servers/9').status_code == 410
        member.side_effect = HTTPException(403, '拒绝')
        assert client.post('/workspaces/3/mcp-servers?mcp_server_id=9').status_code == 403
    conn.__enter__.return_value.cursor.return_value.__enter__.return_value.execute.assert_not_called()


def test_as_bot_directory_projection_does_not_use_workspace_resource_table(monkeypatch, scheduled_as_bot):
    _user = {'user_id': 7, 'role': 'admin'}
    scheduled_as_bot['as_bot']['knowledge_base_ids'] = [4]
    scheduled_as_bot['as_bot']['mcp_server_ids'] = [9]
    scheduled_as_bot['as_bot']['tools']['external'] = {'9': []}
    assert as_bots.visible_resource_ids(_user, 3, 'knowledge_base_ids') == [4]
    assert as_bots.visible_resource_ids(_user, 3, 'mcp_server_ids') == []
    scheduled_as_bot['enabled'] = False
    assert as_bots.visible_resource_ids(_user, 3, 'knowledge_base_ids') == []
    assert all('adh_workspace_mcp_servers' not in sql for sql, _ in scheduled_as_bot['queries'])


def test_mcp_scoped_directory_redacts_secrets(monkeypatch):
    from backend.modules.platform.api import mcp_servers as api
    monkeypatch.setattr(as_bots, 'visible_resource_ids', lambda *a: [9])
    monkeypatch.setattr(api, 'execute_query', lambda *a: [{'id': 9, 'name': 'MCP', 'is_active': 1,
        'env': {'TOKEN': 'secret'}, 'url': 'private', 'command': 'private', 'workspace_id': 8}])
    data = api.list_mcp_servers(3, {'user_id': 7, 'role': 'viewer'})
    assert data == [{'id': 9, 'name': 'MCP', 'is_active': 1}]
    with pytest.raises(HTTPException) as exc:
        api.list_mcp_servers(None, {'user_id': 7, 'role': 'viewer'})
    assert exc.value.status_code == 403


def test_kb_scoped_directory_redacts_configuration(monkeypatch, kb_api):
    from datetime import datetime
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [{'id': 4, 'name': '库', 'kb_type': 'qmind', 'status': 'active',
        'source_config': '{"token":"private"}', 'created_at': datetime(2026, 1, 1), 'updated_at': datetime(2026, 1, 1)}]
    monkeypatch.setattr(kb_api, 'get_metadata_conn', lambda: conn)
    monkeypatch.setattr(as_bots, 'visible_resource_ids', lambda *a: [4])
    data = asyncio.run(kb_api.list_knowledge_bases(None, None, 3, {'user_id': 7, 'role': 'viewer'}))
    assert data[0].source_config is None and not hasattr(data[0], 'workspace_ids')
    monkeypatch.setattr(as_bots, 'visible_resource_ids', lambda *a: [])
    cursor.reset_mock()
    assert asyncio.run(kb_api.list_knowledge_bases(None, None, 3, {'user_id': 7, 'role': 'viewer'})) == []
    cursor.execute.assert_not_called()


def test_external_service_requires_as_bot_and_live_enabled_resource(monkeypatch):
    lookup = Mock(return_value={'id': 9, 'workspace_id': 100, 'is_active': 1})
    monkeypatch.setattr(external_tools, 'execute_query', lookup)
    empty = tool_policy.compile_policy({'as_bot_key': 'x', 'tools': {}})
    with pytest.raises(PermissionError, match='AS-BOT'):
        external_tools.load_server(9, empty)
    lookup.assert_not_called()
    policy = tool_policy.compile_policy({'as_bot_key': 'x', 'mcp_server_ids': [9],
                                         'tools': {'external': {'9': ['read_report']}}})
    assert external_tools.load_server(9, policy)['id'] == 9
    assert 'workspace_id' not in lookup.call_args.args[0]
    lookup.return_value = None
    with pytest.raises(PermissionError, match='停用'):
        external_tools.load_server(9, policy)


def test_design_candidates_require_as_bot_kb_binding(monkeypatch):
    from backend.modules.viz.services import dashboard_design_service as ds
    state = {'knowledge_base_ids': []}
    monkeypatch.setattr(as_bots, 'resolve_as_bots', lambda *a, **k: [state])
    monkeypatch.setattr(as_bots, 'default_as_bot', lambda bots: bots[0] if bots else None)
    monkeypatch.setattr('backend.common.auth.authorize_workspace', lambda *a, **k: None)
    query = Mock(return_value=[{'id': 4, 'name': '库', 'workspace_ids': [99]}])
    monkeypatch.setattr(ds, 'execute_query', query)
    user = {'user_id': 7, 'role': 'admin'}
    assert ds._accessible_kbs(user, 3) == []
    query.assert_not_called()
    state['knowledge_base_ids'] = [4]
    assert ds._accessible_kbs(user, 3)[0]['id'] == 4
    assert query.call_args.args[1] == (4,)
    assert 'workspace_ids' not in query.call_args.args[0]
    query.return_value = []
    with pytest.raises(ds.DesignError, match='停用'):
        ds._accessible_kbs(user, 3)


def test_task_partial_update_validates_persisted_owner_and_strips_internals(monkeypatch,
                                                                          scheduled_as_bot):
    """部分更新以**库里的属主**重解角色；落库配置不持有 AS-BOT/数据源等内部键。"""
    from backend.modules.flow.services import scheduled_task_service as module
    task = {'id': 12, 'owner_id': 7, 'workspace_id': 3, 'task_type': 'agent', 'is_active': False,
            'task_config': {'questions': [{'title': '统计', 'question': '统计订单'}]}}
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = lambda: copy.deepcopy(task)
    cursor.rowcount = 1
    monkeypatch.setattr(module, 'get_metadata_conn', lambda: conn)
    service = module.ScheduledTaskService()

    service.update_task(12, {'name': '仅改名'})            # 属主角色可用 → 放行
    assert conn.commit.called
    conn.commit.reset_mock()
    conn.begin.reset_mock()

    scheduled_as_bot['enabled'] = False                     # 角色被解绑 AS-BOT
    with pytest.raises(sa.ScheduledScopeError, match='AS-BOT'):
        service.update_task(12, {'name': '不该保存'})
    conn.commit.assert_not_called()
    service.toggle_task(12, 0)                              # 停用始终允许，不拦旧任务下线
    assert conn.begin.called and conn.commit.called

    scheduled_as_bot['enabled'] = True
    calls = []
    monkeypatch.setattr(auth, 'resolve_execution_owner', lambda uid, ws: calls.append((uid, ws)) or {
        'user_id': uid, 'workspace_id': ws, 'role': 'admin', 'username': '创建者'})
    service.update_task(12, {'owner_id': 999, 'workspace_id': 99,
                             'task_config': {'datasource_id': 8, 'as_bot_key': 'sales',
                                             'mcp_server_ids': [9],
                                             'questions': [{'title': 't', 'question': '统计'}]}})
    assert calls == [(7, 3)]                                # 请求体改属主不接受：永远用库里那个人
    assert 'FOR UPDATE' in cursor.execute.call_args_list[-2].args[0]
    persisted = json.loads([p for p in cursor.execute.call_args_list[-1].args[1]
                            if isinstance(p, str) and p.startswith('{')][0])
    assert persisted == {'questions': [{'title': 't', 'question': '统计'}]}


def test_task_config_without_question_is_refused():
    """洗内部键不是橡皮章：剥完后没有分析问题就拒落库（不长每次执行都失败的任务）。"""
    from backend.modules.flow.services.scheduled_task_service import sanitize_task_config
    with pytest.raises(ValueError, match='分析问题'):
        sanitize_task_config({'datasource_id': 8, 'as_bot_key': 'sales'})
    assert sanitize_task_config({'site_code': 'cn', 'as_bot_key': 'x',
                                 'questions': [{'question': '统计'}]}) == {
        'site_code': 'cn', 'questions': [{'question': '统计'}]}


def test_as_bot_candidate_endpoints_are_retired():
    """不选 AS-BOT 就没有“给前端列候选”的接口：两个读端点与回调登记同步退役。"""
    from backend.modules.flow.api import scheduled as api
    from backend.core import task_runtime
    assert not hasattr(api, 'existing_task_as_bot_options')
    assert not any(str(getattr(r, 'path', '')).endswith('as-bot-options') for r in api.router.routes)
    assert not hasattr(sa, 'as_bot_options')
    assert 'mind.as_bot_options' not in task_runtime._CALLBACKS
    assert 'mind.needs_as_bot_migration' not in task_runtime._CALLBACKS


def test_explicit_unknown_as_bot_key_fails_closed(scheduled_as_bot):
    """仍允许显式指定 AS-BOT 的入口（Chat 卡片）：key 与角色绑定不符即拒，不回退默认。"""
    with pytest.raises(PermissionError, match='未授权使用所选 AS-BOT'):
        as_bots.resolve_as_bots(3, 'admin', 'unknown', 7)


def test_knowledge_binding_revocation_between_workers(scheduled_as_bot):
    from backend.modules.mind.execution.resource_guard import validate_knowledge_binding
    validate_knowledge_binding([4])
    scheduled_as_bot['kbs'] = []
    for _ in range(2):
        with pytest.raises(PermissionError):
            validate_knowledge_binding([4])


def test_dangling_kb_binding_error_names_missing_ids(monkeypatch):
    """悬空绑定必须 fail-loud 且可诊断：报错带缺失 id，不静默缩小知识边界。"""
    from backend.modules.mind.execution import resource_guard
    monkeypatch.setattr(resource_guard, 'execute_query',
                        lambda *a, **k: [{'id': 4}])
    with pytest.raises(PermissionError) as exc:
        resource_guard.validate_knowledge_binding([3, 4])
    assert '[3]' in str(exc.value)  # 悬空 id 可见，便于清理绑定


def test_load_knowledge_bases_error_names_missing_ids(monkeypatch):
    from backend.modules.mind.execution import as_bots
    monkeypatch.setattr(as_bots, '_query',
                        lambda *a, **k: [{'id': 4, 'name': 'kb4', 'kb_type': 'local', 'status': 'active'}])
    with pytest.raises(ValueError) as exc:
        as_bots.load_knowledge_bases([3, 4])
    assert '[3]' in str(exc.value)  # 悬空 id 可见，便于清理绑定
    assert as_bots.load_knowledge_bases([4])[0]['id'] == 4  # 无悬空时正常返回
