"""仪表盘设计：离线安全契约与显式启用的真实 MySQL 多连接回归。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
import uuid
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from services.dataviz.services import dashboard_design_service as ds

USER = {"user_id": 7, "role": "admin", "username": "designer", "workspace_id": 0}
WIDGET = {"title": "案例数量", "chart_type": "bar", "query": {"object": "case", "metrics": ["案例数量"],
          "time_window": "7d", "time_column": "创建日期"}, "config": {}, "position": {"x": 0, "y": 0, "w": 640, "h": 360}}


@pytest.mark.parametrize("field", ["sql", "raw_sql", "statement", "datasource_id", "user_id", "workspace_id", "countSql", "sqlQuery"])
def test_agent_rejects_sql_and_identity_at_any_depth(field):
    with pytest.raises(ds.DesignError):
        ds.validate_widgets([{**WIDGET, "config": {"nested": [{field: "secret"}]}}])


def test_projection_never_contains_manual_sql_or_preview_rows():
    widget = {**WIDGET, "manual_sql": "SELECT private FROM secret", "query_source": "raw_sql"}
    row = {"id": "a" * 32, "version": 1, "status": "designing", "content": {"widgets": [widget]},
           "preview": {"rows": [{"secret": 9}], "sql": "private"}}
    text = json.dumps(ds.public_design(row))
    assert "SELECT" not in text and "private" not in text and '"rows"' not in text


def test_sql_requires_extra_permission(monkeypatch):
    monkeypatch.setattr('services.shared.common.api_permission.check_api_permission', lambda *a: False)
    with pytest.raises(ds.DesignError, match="SQL"):
        ds.require_sql_permission(USER)


def test_no_selection_is_not_global_scope():
    with pytest.raises(ds.DesignError, match="确认目标"):
        ds.resolve_scope({}, USER)


def test_domains_discovered_without_waker_datasource_bindings(monkeypatch):
    """业务域不依赖 Waker 数据源，但知识库必须显式绑定。"""
    monkeypatch.setattr('services.datamind.execution.wakers.resolve_system_bot_waker',
                        lambda: {"knowledge_base_ids": [4]})
    def fake(sql, params=None, fetchone=False):
        if 'adh_ontology_models' in sql:
            return [{'id': 9, 'name': 'biz-db', 'db_type': 'mysql', 'model_name': '业务本体'}]
        return [{'id': 4, 'name': 'kb', 'kb_type': 'qmind', 'workspace_ids': '[]', 'source_config': '{}'}]
    monkeypatch.setattr(ds, 'execute_query', fake)
    domains = ds._discover_domains(USER, 0)
    assert domains == [{'datasource_name': 'biz-db', 'model_name': '业务本体',
                        'knowledge_bases': [{'id': 4, 'name': 'kb', 'kb_type': 'qmind'}]}]


def _scope_fake(rows_map):
    def fake(sql, params=None, fetchone=False):
        for key, value in rows_map.items():
            if key in sql:
                return value
        return {'id': 9, 'name': 'biz-db', 'db_type': 'mysql'}
    return fake


def test_workspace_derived_from_dashboard_not_user_choice(monkeypatch):
    monkeypatch.setattr('services.datamind.execution.wakers.resolve_system_bot_waker',
                        lambda: {"knowledge_base_ids": []})
    monkeypatch.setattr(ds, 'execute_query', _scope_fake({
        'adh_ontology_models': {'id': 7}, 'adh_dashboards': {'id': 5, 'workspace_id': 1, 'owner_id': USER['user_id']},
        'adh_charts': [{'id': 1, 'name': 'c', 'chart_type': 'bar'}], 'adh_knowledge_bases': []}))
    doc = {'operation': 'append', 'name': 'x', 'selection': {'datasource_name': 'biz-db', 'dashboard_id': 5}}
    scope = ds.resolve_scope(doc, USER)
    assert scope['workspace'] == 1 and scope['datasource_id'] == 9


def test_domain_without_active_ontology_fails_loud(monkeypatch):
    monkeypatch.setattr(ds, 'execute_query', _scope_fake({'adh_ontology_models': None}))
    with pytest.raises(ds.DesignError, match="生效的业务本体模型"):
        ds.resolve_scope({'selection': {'datasource_name': 'biz-db'}}, USER)


def test_empty_kb_scope_does_not_query_all(monkeypatch):
    from services.datamind.rag import qmind_retriever as qr
    q = Mock(side_effect=AssertionError("不应查询全库"))
    monkeypatch.setattr('services.shared.common.db.execute_query', q)
    assert qr._bound_qmind_kbs([]) == []
    q.assert_not_called()


def test_unknown_source_chunk_never_reaches_agent(monkeypatch):
    from services.datamind.rag import qmind_retriever as qr
    row = {"content": {}, "status": "designing"}
    scope = {"datasource_id": 5, "knowledge_bases": [{"id": 1, "name": "kb", "kb_type": "qmind",
              "source_config": {"notebook_id": "test"}}]}
    monkeypatch.setattr(ds, 'load', lambda *a: (row, USER))
    monkeypatch.setattr(ds, 'resolve_scope', lambda *a: scope)
    monkeypatch.setattr(qr, 'retrieve_notebook_strict', lambda *a: [{"content": "其他业务域秘密", "title": "unknown"}])
    with pytest.raises(ds.DesignError, match="可核验") as error:
        ds.business_knowledge('a', USER, ['案例'])
    assert "秘密" not in str(error.value)


def test_same_object_key_does_not_authorize_other_domain(monkeypatch):
    from services.datamind.rag import qmind_retriever as qr
    monkeypatch.setattr(ds, 'load', lambda *a: ({"content": {}, "status": "designing"}, USER))
    monkeypatch.setattr(ds, 'resolve_scope', lambda *a: {"datasource_id": 5, "knowledge_bases": [
        {"id": 1, "name": "kb", "kb_type": "qmind", "source_config": {"notebook_id": "test"}}]})
    monkeypatch.setattr(qr, 'retrieve_notebook_strict', lambda *a: [{"content": "## 业务对象: 案例 (case)", "title": "本体模型-另一个.md"}])
    monkeypatch.setattr(ds, 'execute_query', lambda *a, **k: [{"id": 9, "datasource_id": 6}])
    with pytest.raises(ds.DesignError, match="其他业务域"):
        ds.business_knowledge('a', USER, ['案例'])


def test_sql_source_scope_cannot_be_changed_by_qualified_name(monkeypatch):
    monkeypatch.setattr(ds, 'execute_query', lambda *a, **k: [{"table_name": "cases", "database_name": "biz", "catalog_name": "adh"}])
    scope = {"datasource_id": 5, "dialect": "mysql"}
    assert ds.validate_query_sources('SELECT * FROM biz.cases LIMIT 5', scope) == ['biz.cases']
    with pytest.raises(ds.DesignError):
        ds.validate_query_sources('SELECT * FROM secrets.cases LIMIT 5', scope)


@pytest.mark.parametrize('sql', ['DELETE FROM cases', 'SELECT 1; SELECT 2', 'SELECT * INTO OUTFILE "/tmp/x" FROM cases'])
def test_manual_sql_must_remain_single_readonly_query(sql):
    with pytest.raises(PermissionError):
        ds.compile_widget({"query_source": "raw_sql", "manual_sql": sql}, {"dialect": "mysql"})


def test_direct_approval_creation_cannot_skip_preview():
    from services.datamind.execution.wakers import create_approval
    with pytest.raises(ValueError, match="成功预览"):
        create_approval(7, 'dashboard.publish', {"design_id": 'a' * 32, "version": 1, "digest": 'b' * 64})


def test_rest_refuses_forged_publish_payload():
    from services.datamind.api.as_bot import CreateApprovalRequest, create_pending_approval
    with pytest.raises(HTTPException) as error:
        asyncio.run(create_pending_approval(CreateApprovalRequest(action_key='dashboard.publish', payload={}), USER))
    assert error.value.status_code == 422


def test_system_routes_stay_system_and_tools_are_explicit(monkeypatch):
    from services.datamind.execution.prompt_composer import AS_BOT_DESIGN_RULES
    from services.datamind.execution import wakers
    monkeypatch.setattr(wakers, '_query', lambda *a: [{"waker_key": "__system_bot__", "id": 1, "tools": {}}])
    bot = wakers.resolve_system_bot_waker()
    assert 'query' not in bot['tools']['mcp']
    assert 'get_business_semantics' in bot['tools']['mcp']['semantic']
    assert '系统问题' in AS_BOT_DESIGN_RULES and '标题、配色' in AS_BOT_DESIGN_RULES
    from services.datamind.execution.sdk_tools import scoped_metadata
    calls = []
    monkeypatch.setattr(scoped_metadata, 'execute_query', lambda sql, *a, **k: calls.append(sql) or [])
    scoped_metadata.execute('get_metrics', {}, ds.SimpleNamespace(datasource_id=5, extra={'waker_key': '__system_bot__'}))
    assert all('datasource_id=0' in sql for sql in calls)


@pytest.fixture
def mysql_design(monkeypatch):
    if os.getenv('ADH_TEST_DESIGN_MYSQL') != '1':
        pytest.skip('需显式启用隔离 MySQL 设计回归')
    from services.shared.common.db import execute_query, execute_insert, execute_write
    from services.shared.common.db.metadata_db import get_metadata_conn
    from services.dataviz.services import governed_query
    uid = 2**52 + uuid.uuid4().int % 100000000
    user = {**USER, 'user_id': uid}
    monkeypatch.setattr(ds, 'identity', lambda u: user if u.get('user_id') == uid else (_ for _ in ()).throw(ds.DesignError('无权访问')))
    cid = execute_insert("INSERT INTO adh_conversations (user_id,title,workspace_id,waker_key,messages) VALUES (%s,'__design_test__',0,'__system_bot__','[]')", (uid,))
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO adh_dashboards (name,owner_id,workspace_id,created_at,updated_at) VALUES ('__design_test__',%s,0,UTC_TIMESTAMP(),UTC_TIMESTAMP())", (uid,))
            dashboard_id = cur.lastrowid
    finally:
        conn.close()
    def scope(doc, live, cur=None):
        if not doc.get('selection'):
            raise ds.DesignError('请确认范围')
        return {'workspace': 0, 'datasource_id': 1, 'datasource_name': 'fixture', 'dialect': 'mysql',
                'target': ds.target_snapshot(doc, live, 0, cur=cur), 'knowledge_bases': []}
    monkeypatch.setattr(ds, 'resolve_scope', scope)
    def compile_fixture(row, live):
        s = scope(row['content'], live)
        if not row['content'].get('widgets'):
            raise ds.DesignError('尚无图表')
        return s, ['SELECT 1 AS n LIMIT 500'] * len(row['content']['widgets']), ds.digest({'content': row['content'], 'target': s['target']})
    monkeypatch.setattr(ds, 'compile_design', compile_fixture)
    monkeypatch.setattr(governed_query, 'governed_execute', lambda *a: {'columns': ['n'], 'rows': [{'n': 1}], 'row_count': 1})
    monkeypatch.setattr(ds, 'validate_query_sources', lambda *a: [])
    d = ds.request_design(user, cid, '追加案例数量', operation='append')
    selection = {'dashboard_id': dashboard_id, 'datasource_name': 'fixture', 'knowledge_base_ids': []}
    d = ds.select_scope(d['design_id'], user, d['version'], selection)
    d = ds.prepare(d['design_id'], user, d['version'], [WIDGET], ['统计案例、近7天、柱状图'])
    yield {'design': d, 'user': user, 'dashboard_id': dashboard_id, 'q': execute_query, 'write': execute_write, 'cid': cid}
    execute_write('DELETE FROM adh_charts WHERE dashboard_id=%s', (dashboard_id,))
    execute_write('DELETE FROM adh_as_bot_approvals WHERE user_id=%s', (uid,))
    execute_write('DELETE FROM adh_as_bot_dashboard_designs WHERE user_id=%s', (uid,))
    execute_write('DELETE FROM adh_dashboards WHERE owner_id=%s', (uid,))
    execute_write('DELETE FROM adh_conversations WHERE user_id=%s', (uid,))


def test_mysql_preview_isolated_publish_idempotent(mysql_design):
    f = mysql_design
    d = f['design']
    p = ds.preview(d['design_id'], f['user'], d['version'])
    assert not f['q']('SELECT id FROM adh_charts WHERE dashboard_id=%s', (f['dashboard_id'],))
    aid = p['design']['approval_id']
    first = ds.publish(aid, f['user'])
    second = ds.publish(aid, f['user'])
    assert first == second and first['status'] == 'executed'
    rows = f['q']('SELECT data_cache,semantic_query,query_source FROM adh_charts WHERE dashboard_id=%s', (f['dashboard_id'],))
    assert len(rows) == 1 and rows[0]['data_cache'] is None and rows[0]['query_source'] == 'semantic'


def test_mysql_edit_expires_old_approval(mysql_design):
    f = mysql_design
    p = ds.preview(f['design']['design_id'], f['user'], f['design']['version'])
    d = p['design']
    changed = ds.edit_sql(d['design_id'], f['user'], d['version'], '0', 'SELECT 2 AS n LIMIT 2')
    assert changed['version'] > d['version'] and not changed['preview_valid']
    with pytest.raises(ds.DesignError):
        ds.publish(d['approval_id'], f['user'])
    row = f['q']('SELECT status FROM adh_as_bot_approvals WHERE id=%s', (d['approval_id'],), fetchone=True)
    assert row['status'] == 'superseded'


def test_mysql_expired_preview_cannot_publish(mysql_design):
    f = mysql_design
    p = ds.preview(f['design']['design_id'], f['user'], f['design']['version'])
    f['write']('UPDATE adh_as_bot_dashboard_designs SET preview_expires_at=UTC_TIMESTAMP()-INTERVAL 1 SECOND WHERE id=%s', (f['design']['design_id'],))
    with pytest.raises(ds.DesignError, match='过期'):
        ds.publish(p['design']['approval_id'], f['user'])


def test_mysql_two_connections_publish_only_once(mysql_design):
    f = mysql_design
    p = ds.preview(f['design']['design_id'], f['user'], f['design']['version'])
    def publish():
        try:
            return ds.publish(p['design']['approval_id'], f['user'])
        except ds.DesignError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert any(isinstance(r, dict) and r['status'] == 'executed' for r in results)
    assert len(f['q']('SELECT id FROM adh_charts WHERE dashboard_id=%s', (f['dashboard_id'],))) == 1


def test_mysql_fault_rolls_back_chart_and_approval(mysql_design, monkeypatch):
    import importlib
    dashboard_service = importlib.import_module('services.dataviz.services.dashboard_service')
    f = mysql_design
    p = ds.preview(f['design']['design_id'], f['user'], f['design']['version'])
    real = dashboard_service.publish_design_in_transaction
    def fail(*args):
        real(*args)
        raise RuntimeError('模拟进程提交前故障')
    monkeypatch.setattr(dashboard_service, 'publish_design_in_transaction', fail)
    with pytest.raises(RuntimeError):
        ds.publish(p['design']['approval_id'], f['user'])
    assert not f['q']('SELECT id FROM adh_charts WHERE dashboard_id=%s', (f['dashboard_id'],))
    assert f['q']('SELECT status FROM adh_as_bot_approvals WHERE id=%s', (p['design']['approval_id'],), fetchone=True)['status'] == 'pending'
    monkeypatch.setattr(dashboard_service, 'publish_design_in_transaction', real)
    assert ds.publish(p['design']['approval_id'], f['user'])['status'] == 'executed'
