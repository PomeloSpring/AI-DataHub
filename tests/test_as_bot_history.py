"""AS-BOT 会话历史回归：会话合并共用、无 __system_bot__ 哨兵（离线，假 DB 连接）。"""
from types import SimpleNamespace

import pytest

from services.datamind.api import chat
from services.shared.common.db import metadata_db


class _FakeCursor:
    def __init__(self, log):
        self.log = log
        self.lastrowid = 999

    def execute(self, sql, params=None):
        self.log.append((sql, tuple(params) if params else ()))

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return _FakeCursor(self.log)

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def capture(monkeypatch):
    log = []
    monkeypatch.setattr(metadata_db, "get_metadata_conn", lambda: _FakeConn(log))
    monkeypatch.setattr(chat, "authorize_workspace", lambda *a: 0)
    return log


def _stub_policy(monkeypatch, as_bot_key, seen=None):
    """桩掉 resolve_policy：返回带 as_bot 的策略对象（可记录 ctx）。"""
    from services.datamind.execution import tool_policy

    def fake_resolve(ctx, *a, **k):
        if seen is not None:
            seen["ctx_as_bot_key"] = ctx.extra.get("as_bot_key")
        return SimpleNamespace(as_bot={"as_bot_key": as_bot_key})

    monkeypatch.setattr(tool_policy, "resolve_policy", fake_resolve)


def test_list_scope_as_bot_filter(capture):
    chat.list_conversations(workspace_id=0, as_bot_key="ops-bot", user={"user_id": 7})
    sql, params = capture[-1]
    assert "as_bot_key = %s" in sql and "ops-bot" in params


def test_workspace_list_includes_global_conversations(capture):
    """合并共用：工作空间列表含全局(ws=0)会话（面板所建），双向互见不按入口区分。"""
    chat.list_conversations(workspace_id=3, as_bot_key="", user={"user_id": 7})
    sql, params = capture[-1]
    assert "workspace_id IN (%s, 0)" in sql
    assert params == (7, 3)  # 所有权仍按用户


def test_list_merged_no_sentinel_exclusion(capture):
    """会话历史合并共用：两入口会话互见，无 __system_bot__ 哨兵/入口区分。"""
    chat.list_conversations(workspace_id=0, as_bot_key="", user={"user_id": 7})
    sql, params = capture[-1]
    assert "__system_bot__" not in sql
    assert "__system_bot__" not in params
    assert "as_bot_key <>" not in sql
    assert "as_bot_key =" not in sql.split("WHERE", 1)[1]  # 不按入口/as_bot 过滤 → 互见
    assert "user_id = %s" in sql and 7 in params  # 所有权仍按用户


def test_create_tags_resolved_as_bot_key(capture, monkeypatch):
    """会话归属取策略解析结果（服务端注入），客户端传值只进解析上下文。"""
    seen = {}
    _stub_policy(monkeypatch, "resolved-bot", seen)
    chat.create_conversation(
        chat.CreateConversationRequest(workspace_id=5, datasource_id=0, as_bot_key="ops-bot"),
        user={"user_id": 7})
    sql, params = capture[0]
    assert seen["ctx_as_bot_key"] == "ops-bot"  # 请求标识进入策略解析
    assert "as_bot_key" in sql and "resolved-bot" in params and 7 in params


def test_create_default_as_bot_empty_for_business(capture, monkeypatch):
    _stub_policy(monkeypatch, "")
    chat.create_conversation(chat.CreateConversationRequest(workspace_id=5), user={"user_id": 7})
    sql, params = capture[0]
    assert "INSERT INTO adh_conversations" in sql
    assert "" in params  # 未绑定 AS-BOT → 空串


def test_create_permission_denied_before_insert(capture, monkeypatch):
    from fastapi import HTTPException
    def deny(*args):
        raise HTTPException(403, "未授权")
    monkeypatch.setattr(chat, "authorize_workspace", deny)
    with pytest.raises(HTTPException):
        chat.create_conversation(chat.CreateConversationRequest(workspace_id=5), user={"user_id": 7})
    assert not capture


@pytest.fixture
def deletion_store(tmp_path, monkeypatch):
    """事务替身保留已提交状态；磁盘使用真实临时目录。"""
    import copy
    from services.datamind.execution import session_workspace as sessions
    key = 'a' * 32
    monkeypatch.setattr('services.shared.common.config.ADH_WORKSPACES_DIR', str(tmp_path))
    node = sessions.storage_node(tmp_path)
    root = sessions.session_paths(tmp_path, key, 3, create=True)
    (root / 'workspace' / 'result.txt').write_text('本会话文件')
    (root / 'runtime' / 'settings.json').write_text('{}')
    neighbor = sessions.session_paths(tmp_path, 'b' * 32, 3, create=True)
    (neighbor / 'workspace' / 'keep.txt').write_text('其他会话文件')
    state = {'conversation': {'id': 10, 'user_id': 7, 'workspace_id': 3},
             'session': {'session_key': key, 'conversation_id': 10, 'user_id': 7, 'workspace_id': 3,
                         'status': 'idle', 'execution_token': None, 'storage_node': node,
                         'relative_dir': str(root.relative_to(tmp_path))}, 'log': [], 'commits': 0}

    class Connection:
        def __enter__(self):
            self.tx = copy.deepcopy({k: state[k] for k in ('conversation', 'session')})
            return self

        def __exit__(self, exc_type, *_):
            if exc_type is None:
                state.update(self.tx)
                state['commits'] += 1
            return False

        def begin(self):
            pass

        def cursor(self):
            return Cursor(self)

    class Cursor:
        def __init__(self, connection):
            self.conn = connection
            self.rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            state['log'].append((sql, params))
            tx = self.conn.tx
            if sql.startswith('SELECT'):
                assert 'FOR UPDATE' in sql
                self.row = tx['conversation' if 'adh_conversations' in sql else 'session']
            elif sql.startswith('UPDATE adh_agent_sessions'):
                assert "status='deleting'" in sql
                tx['session']['status'] = 'deleting'
            elif sql.startswith('DELETE FROM adh_agent_sessions'):
                tx['session'] = None
            elif sql.startswith('DELETE FROM adh_conversations'):
                tx['conversation'] = None
            else:
                raise AssertionError(sql)

        def fetchone(self):
            return self.row

    monkeypatch.setattr(sessions, 'DBConnection', Connection)
    return sessions, state, root, neighbor


@pytest.mark.parametrize('status', ['idle', 'interrupted', 'closed', 'deleting'])
def test_delete_conversation_cleans_only_its_session(deletion_store, status):
    sessions, state, root, neighbor = deletion_store
    state['session']['status'] = status
    assert chat.delete_conversation(10, {'user_id': 7}) == {'success': True}
    assert not root.exists()
    assert (neighbor / 'workspace' / 'keep.txt').read_text() == '其他会话文件'
    assert root.parent.is_dir()
    assert (root.parents[2] / '.storage-node').exists()
    assert state['session'] is None and state['conversation'] is None
    assert chat.delete_conversation(10, {'user_id': 7}) == {'success': True}


@pytest.mark.parametrize('field,value', [('status', 'running'), ('status', 'unknown'), ('execution_token', 'active')])
def test_running_or_unconfirmed_session_cannot_be_deleted(deletion_store, field, value):
    from fastapi import HTTPException
    _, state, root, _ = deletion_store
    state['session'][field] = value
    with pytest.raises(HTTPException) as error:
        chat.delete_conversation(10, {'user_id': 7})
    assert error.value.status_code == 409
    assert root.exists() and state['conversation'] and state['session']
    assert state['commits'] == 0


def test_delete_conversation_checks_owner_before_filesystem(deletion_store):
    from fastapi import HTTPException
    _, state, root, _ = deletion_store
    with pytest.raises(HTTPException) as error:
        chat.delete_conversation(10, {'user_id': 8})
    assert error.value.status_code == 404
    assert root.exists() and state['session']['status'] == 'idle'


def test_delete_without_agent_session_does_not_guess_paths(deletion_store):
    _, state, root, _ = deletion_store
    state['session'] = None
    assert chat.delete_conversation(10, {'user_id': 7})['success']
    assert root.exists() and state['conversation'] is None


def test_delete_owned_orphan_session_can_be_retried(deletion_store):
    _, state, root, _ = deletion_store
    state['conversation'] = None
    assert chat.delete_conversation(10, {'user_id': 7})['success']
    assert not root.exists() and state['session'] is None


def test_failed_cleanup_retains_deleting_state_for_retry(deletion_store, monkeypatch):
    from fastapi import HTTPException
    sessions, state, root, _ = deletion_store
    remove = sessions._remove_session_directory

    def fail(path):
        assert state['session']['status'] == 'deleting'
        raise OSError('private path and filesystem failure')

    monkeypatch.setattr(sessions, '_remove_session_directory', fail)
    with pytest.raises(HTTPException) as error:
        chat.delete_conversation(10, {'user_id': 7})
    assert error.value.status_code == 503 and '重试' in error.value.detail
    assert 'private' not in error.value.detail
    assert state['conversation'] and state['session']['status'] == 'deleting' and root.exists()
    monkeypatch.setattr(sessions, '_remove_session_directory', remove)
    assert chat.delete_conversation(10, {'user_id': 7})['success']
    assert not root.exists() and state['session'] is None


def test_cleanup_retry_after_files_removed_before_db_commit(deletion_store, monkeypatch):
    from fastapi import HTTPException
    sessions, state, root, _ = deletion_store
    remove = sessions._remove_session_directory

    def fail_after_files(path):
        remove(path)
        raise OSError('模拟文件清理后事务中断')

    monkeypatch.setattr(sessions, '_remove_session_directory', fail_after_files)
    with pytest.raises(HTTPException):
        chat.delete_conversation(10, {'user_id': 7})
    assert state['conversation'] and state['session']['status'] == 'deleting' and not root.exists()
    monkeypatch.setattr(sessions, '_remove_session_directory', remove)
    assert chat.delete_conversation(10, {'user_id': 7})['success']
    assert state['session'] is None


@pytest.mark.parametrize('field,value', [('relative_dir', '../../other'), ('session_key', '../escape'),
                                       ('workspace_id', 4), ('user_id', 8), ('storage_node', 'f' * 32)])
def test_cleanup_rejects_untrusted_mapping(deletion_store, field, value):
    from fastapi import HTTPException
    _, state, root, neighbor = deletion_store
    state['session'][field] = value
    with pytest.raises(HTTPException):
        chat.delete_conversation(10, {'user_id': 7})
    assert root.exists() and neighbor.exists() and state['conversation']


def test_cleanup_does_not_follow_internal_symlink(deletion_store, tmp_path):
    _, state, root, _ = deletion_store
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep.txt').write_text('保留')
    (root / 'workspace' / 'link').symlink_to(outside, target_is_directory=True)
    assert chat.delete_conversation(10, {'user_id': 7})['success']
    assert (outside / 'keep.txt').read_text() == '保留'


def test_cleanup_rejects_replaced_session_root(deletion_store):
    from fastapi import HTTPException
    _, state, root, neighbor = deletion_store
    original = root.with_name('saved-original')
    root.rename(original)
    root.symlink_to(neighbor, target_is_directory=True)
    with pytest.raises(HTTPException):
        chat.delete_conversation(10, {'user_id': 7})
    assert neighbor.exists() and original.exists() and state['conversation']


def test_cleanup_does_not_create_missing_mount(deletion_store, tmp_path, monkeypatch):
    # 挂载目录不存在 = 无残留可清理 → 放行删除(需求确认语义)；仍不创建挂载目录/标记。
    _, state, root, _ = deletion_store
    missing = tmp_path / 'unmounted'
    monkeypatch.setattr('services.shared.common.config.ADH_WORKSPACES_DIR', str(missing))
    assert chat.delete_conversation(10, {'user_id': 7}) == {'success': True}
    assert not missing.exists() and root.exists()  # 不重建挂载；原卷内文件不动
    assert state['session'] is None and state['conversation'] is None


def test_cleanup_does_not_create_missing_storage_marker(deletion_store):
    from fastapi import HTTPException
    _, state, root, _ = deletion_store
    marker = root.parents[2] / '.storage-node'
    marker.rename(marker.with_name('saved-marker'))
    with pytest.raises(HTTPException):
        chat.delete_conversation(10, {'user_id': 7})
    assert not marker.exists() and root.exists() and state['conversation']


def test_empty_workspace_does_not_block_delete(deletion_store):
    import shutil
    _, state, root, _ = deletion_store
    shutil.rmtree(root)  # 工作区已被清空，仅剩数据库映射
    assert chat.delete_conversation(10, {'user_id': 7}) == {'success': True}
    assert not root.exists()
    assert state['session'] is None and state['conversation'] is None
    assert (root.parents[2] / '.storage-node').exists()  # 不因清理而重建


def test_partial_layout_can_be_cleaned_on_retry(deletion_store):
    _, state, root, _ = deletion_store
    (root / 'runtime').rename(root / 'partial-runtime')
    state['session']['status'] = 'deleting'
    assert chat.delete_conversation(10, {'user_id': 7})['success']
    assert not root.exists()


def test_deleting_session_is_blocked_by_preflight(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from services.datamind.execution import session_workspace as sessions
    monkeypatch.setattr('services.shared.common.auth.authorize_workspace', lambda *a: 3)
    monkeypatch.setattr(sessions, 'validate_conversation', lambda *a: None)
    monkeypatch.setattr(sessions, 'execute_query', lambda *a, **k: {'status': 'deleting'})
    req = SimpleNamespace(workspace_id=3, pipeline_mode='agent', attachments=[], conversation_id=10, as_bot_key='test')
    with pytest.raises(HTTPException) as error:
        sessions.preflight_request(req, {'user_id': 7, 'role': 'admin'})
    assert error.value.status_code == 409 and '删除' in error.value.detail
