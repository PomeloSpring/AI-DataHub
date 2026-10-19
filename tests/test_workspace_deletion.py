"""工作空间删除回归：默认工作站不可删、属主可删、级联范围不得越界到跨工作站资产。

口径（需求确认）：只删用户自建的自定义空间；空间内产物（会话含磁盘工作区、看板与
图表、定时任务与 DAG、保存查询、通知渠道、空间绑定）彻底删除；审计/观测流水与
跨工作站资产（资产清单、用户记忆、数据集/数据产品）必须保留。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.common import api_permission
from backend.modules.auth.services import workspace_deletion as wd
from backend.modules.mind.execution import session_workspace


class _FakeCursor:
    def __init__(self, log, respond):
        self._log = log
        self._respond = respond
        self._rows: list = []

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self._log.append(("sql", normalized, tuple(params or ())))
        rows = self._respond(normalized, tuple(params or ()))
        if rows is None:
            self._rows = []
        else:
            self._rows = rows if isinstance(rows, list) else [rows]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, log, respond):
        self._log = log
        self._respond = respond

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._log, self._respond)

    def begin(self):
        self._log.append(("begin", "", ()))

    def commit(self):
        self._log.append(("commit", "", ()))

    def rollback(self):
        self._log.append(("rollback", "", ()))


def _make_responder(workspace, *, busy=0, impact=None, dashboards=(), tasks=(), workflows=()):
    """按 SQL 特征回放假数据；impact 提供各计数表的行数。"""
    counts = {"conversations": 0, "dashboards": 0, "scheduled_tasks": 0,
              "dag_workflows": 0, "saved_queries": 0}
    counts.update(impact or {})

    def respond(sql, params):
        if sql.startswith("SELECT id, name, owner_id, is_default FROM adh_workspaces"):
            return workspace
        if sql.startswith("SELECT COUNT(*) AS c FROM adh_agent_sessions"):
            return {"c": busy}
        if "information_schema.tables" in sql:
            return {"one": 1}
        if sql.startswith("SELECT COUNT(*) AS c FROM adh_workspaces"):
            return {"c": 0}          # 级联已删除空间行 → 核验通过
        for key, table in (("conversations", "adh_conversations"),
                           ("dashboards", "adh_dashboards"),
                           ("scheduled_tasks", "adh_scheduled_tasks"),
                           ("dag_workflows", "adh_dag_workflows"),
                           ("saved_queries", "adh_saved_queries")):
            if sql.startswith(f"SELECT COUNT(*) AS c FROM {table}"):
                return {"c": counts[key]}
        if sql.startswith("SELECT id FROM adh_dashboards"):
            return [{"id": i} for i in dashboards]
        if sql.startswith("SELECT id FROM adh_scheduled_tasks"):
            return [{"id": i} for i in tasks]
        if sql.startswith("SELECT id FROM adh_dag_workflows"):
            return [{"id": i} for i in workflows]
        return None

    return respond


@pytest.fixture
def harness(monkeypatch):
    """拦截 DB 连接、磁盘清理与审计，返回 (事件日志, 调用记录)。"""
    events: list = []

    def install(workspace, **kwargs):
        log: list = []
        respond = _make_responder(workspace, **kwargs)

        def fake_connection():
            return _FakeConn(log, respond)

        def fake_remove_directory(workspace_id, user_id):
            events.append(("rmtree", workspace_id, user_id))
            return 7

        def fake_audit(user_id, username, action, **kw):
            events.append(("audit", action, kw.get("target_id")))

        monkeypatch.setattr(wd, "DBConnection", fake_connection)
        monkeypatch.setattr(session_workspace, "remove_workspace_directory", fake_remove_directory)
        monkeypatch.setattr(session_workspace, "workspace_disk_usage",
                            lambda ws_id, uid: 4096, raising=False)
        from backend.common import auth as common_auth
        monkeypatch.setattr(common_auth, "log_audit", fake_audit)
        wd_events = events
        wd_events.append(log)
        return log

    install.events = events
    return install


OWNER = 1778154925
OWNER_USER = {"user_id": OWNER, "username": "admin", "role": "viewer"}
ADMIN_USER = {"user_id": 1, "username": "root", "role": "admin"}
CUSTOM = {"id": 302, "name": "test", "owner_id": OWNER, "is_default": 0}
DEFAULT_WS = {"id": 301, "name": "admin的工作站", "owner_id": OWNER, "is_default": 1}

# 必须保留的表：跨工作站资产 + 审计/观测 + 中台治理资产
MUST_KEEP = (
    "adh_user_assets", "adh_user_memory", "adh_datasets", "adh_data_products",
    "adh_query_audit", "adh_llm_traces", "adh_audit_logs", "adh_intent_intercepts",
    "adh_rls_audit_logs", "adh_ontology_models", "adh_rls_policies", "adh_roles",
)


def _deleted_tables(log):
    return {sql.split()[2] for kind, sql, _ in log if kind == "sql" and sql.startswith("DELETE FROM")}


def test_default_workspace_cannot_be_deleted(harness):
    """默认工作站拒删：409 可解释原因，且一条 DELETE 都不发。"""
    log = harness(DEFAULT_WS)
    with pytest.raises(HTTPException) as exc:
        wd.delete_workspace(301, OWNER_USER)
    assert exc.value.status_code == 409
    assert "默认" in exc.value.detail
    assert [entry for entry in log if entry[1].startswith("DELETE")] == []


def test_workspace_with_live_session_blocks_deletion(harness):
    """仍有执行中会话时不删（避免边跑边清目录）。"""
    log = harness(CUSTOM, busy=1)
    with pytest.raises(HTTPException) as exc:
        wd.delete_workspace(302, OWNER_USER)
    assert exc.value.status_code == 409
    assert "执行" in exc.value.detail
    assert [entry for entry in log if entry[1].startswith("DELETE")] == []


def test_non_owner_cannot_delete_workspace(harness):
    harness(CUSTOM)
    with pytest.raises(HTTPException) as exc:
        wd.delete_workspace(302, {"user_id": 999, "username": "other", "role": "viewer"})
    assert exc.value.status_code == 403


def test_admin_may_delete_others_workspace(harness):
    harness(CUSTOM)
    result = wd.delete_workspace(302, ADMIN_USER)
    assert result["success"] is True


def test_owner_delete_cascades_owned_products(harness):
    """级联范围：会话/看板/图表/任务/DAG/保存查询/通知渠道/绑定表 + 空间行本身。"""
    log = harness(CUSTOM, dashboards=(11,), tasks=(21,), workflows=(31,))
    result = wd.delete_workspace(302, OWNER_USER)
    assert result["conversations"] == 0
    deleted = _deleted_tables(log)
    expected = {
        "adh_agent_sessions", "adh_conversations", "adh_message_feedback",
        "adh_charts", "adh_chart_snapshots", "adh_role_dashboard_access", "adh_dashboards",
        "adh_scheduled_logs", "adh_scheduled_tasks", "adh_dag_runs", "adh_dag_workflows",
        "adh_saved_queries", "adh_notification_channels",
        "adh_workspace_users", "adh_workspace_datasources", "adh_workspace_mcp_servers",
        "adh_workspace_execution_layers", "adh_workspace_assets", "adh_workspaces",
    }
    assert expected <= deleted, f"缺少级联清理: {expected - deleted}"
    # 子表按父 id 收敛（他人在此空间的看板不被动），同时兜底清 workspace_id 归属行
    dashboard_scoped = [sql for _, sql, params in log
                        if sql.startswith("DELETE FROM adh_charts") and params == (11,)]
    assert dashboard_scoped, "adh_charts 应按本空间看板 id 级联删除"
    assert [sql for _, sql, params in log
            if sql.startswith("DELETE FROM adh_dashboards") and params == (302, OWNER)]


def test_deletion_never_touches_cross_workspace_assets_or_audit(harness):
    """护栏：资产清单/用户记忆/数据集/审计观测流水一律保留。"""
    log = harness(CUSTOM)
    wd.delete_workspace(302, OWNER_USER)
    deleted = _deleted_tables(log)
    assert not (deleted & set(MUST_KEEP)), f"越界删除: {deleted & set(MUST_KEEP)}"


def test_directory_cleanup_precedes_db_delete(harness):
    """先清持久卷再删库行：库行失败可重试，反过来会留孤儿目录。"""
    log = harness(CUSTOM)
    wd.delete_workspace(302, OWNER_USER)
    events = harness.events
    assert ("rmtree", 302, OWNER) in events
    assert events.index(("rmtree", 302, OWNER)) < events.index(("audit", "delete_workspace", 302))
    assert ("commit", "", ()) in log


def test_delete_impact_reports_counts_and_deletable(harness):
    harness(CUSTOM, impact={"conversations": 13, "dashboards": 2, "saved_queries": 2})
    impact = wd.get_delete_impact(302, OWNER_USER)
    assert impact["conversations"] == 13
    assert impact["dashboards"] == 2
    assert impact["disk_usage_bytes"] == 4096
    assert impact["deletable"] is True
    assert impact["workspace_name"] == "test"

    harness(DEFAULT_WS)
    default_impact = wd.get_delete_impact(301, OWNER_USER)
    assert default_impact["deletable"] is False


def test_delete_impact_rejects_other_users_workspace(harness):
    harness(CUSTOM)
    with pytest.raises(HTTPException) as exc:
        wd.get_delete_impact(302, {"user_id": 999, "username": "other", "role": "viewer"})
    assert exc.value.status_code == 403


def test_delete_impact_route_is_self_service_whitelisted():
    """删除影响预览属登录自举路由：漏配白名单会让普通角色 403（配了入口用不了）。"""
    for path in ("/api/workspaces", "/api/workspaces/302", "/api/workspaces/302/set-default",
                 "/api/workspaces/302/delete-impact"):
        assert api_permission._SELF_ROUTE_PATTERNS.fullmatch(path), path
    # 但不得顺手放行其他空间子资源
    assert not api_permission._SELF_ROUTE_PATTERNS.fullmatch("/api/workspaces/302/users")

