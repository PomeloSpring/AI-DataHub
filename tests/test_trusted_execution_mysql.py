"""显式启用的隔离 MySQL 集成测试；默认跳过，不读取项目数据库配置。"""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pymysql
import pytest

from backend.common import db
from backend.modules.flow.services import scheduled_task_service as scheduled
from backend.modules.viz.services import report_service as reports
from backend.modules.mind.execution import as_bots

pytestmark = pytest.mark.skipif(os.getenv("ADH_TEST_MYSQL_ENABLE") != "1", reason="仅限显式隔离 MySQL")


def _script(cur, path):
    delimiter, buffer = ";", []
    for line in path.read_text().splitlines():
        if line.strip().startswith("--"):
            continue
        if line.startswith("DELIMITER "):
            delimiter = line.split()[1]
            continue
        buffer.append(line)
        if line.rstrip().endswith(delimiter):
            sql = "\n".join(buffer).strip()[:-len(delimiter)]
            if sql:
                cur.execute(sql)
            buffer = []
    assert not "".join(buffer).strip()


@pytest.fixture
def isolated(monkeypatch):
    port = int(os.environ["ADH_TEST_MYSQL_PORT"])
    assert port != 3306, "测试不能使用默认数据库端口"
    database = "adh_ta_test_" + uuid4().hex[:12]
    def connect(database_name=None):
        return pymysql.connect(host="127.0.0.1", port=port, user="root",
            password=os.environ["ADH_TEST_MYSQL_PASSWORD"], database=database_name,
            autocommit=True, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    admin = connect()
    with admin.cursor() as cur:
        cur.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
    def factory():
        return connect(database)
    root = Path(__file__).resolve().parents[1]
    conn = factory()
    try:
        with conn.cursor() as cur:
            _script(cur, root / "docker/mysql/scheduled_task_migration.sql")
            cur.execute("CREATE TABLE adh_as_bot_approvals (id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, action_key VARCHAR(64), payload JSON, conversation_id BIGINT, status ENUM('pending','approved','rejected','executed','failed') DEFAULT 'pending', decided_at DATETIME, decided_by INT, result JSON)")
            cur.execute("CREATE TABLE adh_saved_queries (id BIGINT PRIMARY KEY, owner_id BIGINT, sql_query TEXT)")
            _script(cur, root / "docker/mysql/trusted_execution_migration.sql")
            _script(cur, root / "docker/mysql/trusted_execution_migration.sql")
        monkeypatch.setattr(db, "get_metadata_conn", factory)
        monkeypatch.setattr(scheduled, "get_metadata_conn", factory)
        from backend.common import auth as _auth
        monkeypatch.setattr(_auth, "get_metadata_conn", factory)
        yield factory
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE `{database}`")
        admin.close()


def _task():
    return scheduled.scheduled_task_service.create_task({"name": "隔离测试", "task_type": "query",
        "task_config": {"datasource_id": 8, "questions": [{"sql": "SELECT 1"}]},
        "cron_expression": "* * * * *"}, owner_id=7, workspace_id=3)


def test_migration_reentrant_and_log_claim_atomic(isolated):
    service = scheduled.scheduled_task_service
    tid = _task()
    lid = service.create_log(tid, "manual", "queued", run_key="same-run", workspace_id=3)
    assert service.create_log(tid, "manual", "queued", run_key="same-run", workspace_id=3) == lid
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda _: service.claim_log(lid, "test", 300), range(8)))
    assert sum(claimed) == 1
    assert service.finish_log(lid, status="cancelled")
    assert not service.finish_log(lid, status="success")
    assert service.get_task(tid)["run_count"] == 1
    assert service.get_log(lid)["status"] == "cancelled"


def test_report_persistence_and_claim_idempotent(isolated):
    kwargs = dict(title="隔离报告", content="", workspace_id=3, owner_id=7,
                  run_key="report-run", generation_status="queued", analysis_source={"intent": {"object": "订单"}})
    report = reports.create_report(**kwargs)
    assert reports.create_report(**kwargs)["id"] == report["id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: reports.claim_report(report["id"]), range(8)))
    assert sum(claims) == 1
    assert reports.finish_report(report["id"], generation_status="failed", stage_error_code="QUERY_FAILED")
    assert not reports.finish_report(report["id"], generation_status="ready")
    assert reports.load_report(report["id"])["generation_status"] == "failed"


def test_approval_channel_fully_removed():
    """审批通道已退役：分布式幂等仲裁改由发布状态条件更新承担
    （并发双发布只落一次图，见 test_dashboard_design 的隔离 MySQL 段）。"""
    for name in ("create_approval", "get_approval", "claim_approval",
                 "update_approval_status", "load_action_registry", "validate_action_payload"):
        assert not hasattr(as_bots, name), f"审批通道残留: {name}"
    assert not hasattr(as_bots, "check_as_bot_permission")


def test_expired_run_reconciles_once(isolated):
    service = scheduled.scheduled_task_service
    tid = _task()
    lid = service.create_log(tid, "manual", "queued", run_key="expired-run", workspace_id=3)
    db.execute_write("UPDATE adh_scheduled_logs SET lease_expires_at=DATE_SUB(NOW(), INTERVAL 1 SECOND) WHERE id=%s", (lid,))
    assert service.cleanup_stale_running_logs() == 1
    assert service.cleanup_stale_running_logs() == 0
    assert service.get_task(tid)["last_status"] == "timeout"
    assert not service.claim_log(lid, "late-worker", 300)


def test_expired_lease_forces_timeout_on_late_finish(isolated):
    kwargs = dict(title="迟到报告", content="", workspace_id=3, owner_id=7,
                  run_key="late-run", generation_status="queued", analysis_source={"intent": {"object": "订单"}})
    rid = reports.create_report(**kwargs)["id"]
    assert reports.claim_report(rid)
    db.execute_write("UPDATE adh_reports SET lease_expires_at=DATE_SUB(NOW(), INTERVAL 1 SECOND) WHERE id=%s", (rid,))
    # 迟到 worker 尝试写 degraded，但租约已过期 → 强制 timeout，不发布伪成功正文
    assert reports.finish_report(rid, generation_status="degraded", content="事实表", stage_error_code="FACT_ONLY")
    row = reports.load_report(rid)
    assert row["generation_status"] == "timeout" and row["stage_error_code"] == "TIMEOUT"
    assert not row.get("content")


def test_report_finish_rejects_invalid_status(isolated):
    rid = reports.create_report(title="x", content="", workspace_id=3, owner_id=7,
                                run_key="bad-status", generation_status="queued")["id"]
    reports.claim_report(rid)
    with pytest.raises(ValueError):
        reports.finish_report(rid, generation_status="ready-ish")
    with pytest.raises(ValueError):
        reports.finish_report(rid, generation_status="degraded", owner_id=999)


def test_owner_claim_is_conditional_and_keeps_history(isolated, monkeypatch):
    from backend.common import auth
    service = scheduled.scheduled_task_service
    tid = _task()
    db.execute_write("UPDATE adh_scheduled_tasks SET owner_id=0 WHERE id=%s", (tid,))
    checks = []
    monkeypatch.setattr(auth, "resolve_execution_owner", lambda uid, ws: checks.append((uid, ws)))
    assert service.get_task(tid)["ownership_status"] == "unclaimed"
    assert service.claim_owner(tid, 7)["success"]
    assert not service.claim_owner(tid, 8)["success"]
    assert service.get_task(tid)["owner_id"] == 7
    assert checks == [(7, 3), (8, 3)]


def test_notification_claim_once(isolated):
    service = scheduled.scheduled_task_service
    lid = service.create_log(_task(), "manual", "queued", run_key="notification-run", workspace_id=3)
    assert not service.claim_notification(lid)
    assert service.claim_log(lid, "test", 300)
    assert service.finish_log(lid, status="success")
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: service.claim_notification(lid), range(8)))
    assert sum(claims) == 1


def _seed_owner(factory):
    c = factory()
    try:
        with c.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS adh_users (id BIGINT PRIMARY KEY, username VARCHAR(64), email VARCHAR(255), phone VARCHAR(255), avatar VARCHAR(255), user_role VARCHAR(32), status VARCHAR(16), last_login DATETIME, login_attempts INT DEFAULT 0, locked_until DATETIME, created_at DATETIME, updated_at DATETIME)")
            # 纯角色裁决：数据源授权来自 adh_user_roles ⋈ adh_role_datasource_access（adh_workspace_datasources 已退役）。
            cur.execute("CREATE TABLE IF NOT EXISTS adh_user_roles (id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, role_id BIGINT NOT NULL, workspace_id BIGINT NOT NULL DEFAULT 0, created_at DATETIME, UNIQUE KEY uk_user_role_ws (user_id, role_id, workspace_id))")
            cur.execute("CREATE TABLE IF NOT EXISTS adh_role_datasource_access (id BIGINT PRIMARY KEY, role_id BIGINT NOT NULL, datasource_id BIGINT NOT NULL, access_type VARCHAR(32) NOT NULL DEFAULT 'read', created_at DATETIME, UNIQUE KEY uk_role_ds (role_id, datasource_id))")
            cur.execute("INSERT INTO adh_users (id,username,user_role,status,created_at,updated_at) VALUES (7,'owner','admin','active',NOW(),NOW())")
            # 用户 7 在工作空间 3 持有角色 1，角色 1 授权数据源 8。
            cur.execute("INSERT INTO adh_user_roles (id,user_id,role_id,workspace_id) VALUES (1,7,1,3)")
            cur.execute("INSERT INTO adh_role_datasource_access (id,role_id,datasource_id,access_type,created_at) VALUES (8,1,8,'read',NOW())")
    finally:
        c.close()


def test_execute_core_full_path_and_idempotent(isolated, monkeypatch):
    """真实 MySQL 上跑完整 _execute_core（仅 mock 叶子取数）：终态、run_count、幂等。"""
    from backend.modules.flow.tasks import executor
    _seed_owner(isolated)
    tid = _task()
    # 叶子取数 mock：不连真实数据源，返回已治理结果形状
    monkeypatch.setattr(executor, "_execute_sql_on_datasource",
                        lambda sql, ds, ident: {"columns": ["n"], "rows": [{"n": 1}], "row_count": 1})
    monkeypatch.setattr(executor, "_collect_lineage", lambda *a: None)

    out = executor._execute_core(tid, "manual", "core-run-1", "test-worker")
    assert out["status"] == "success" and out["succeeded"] == 1
    service = scheduled.scheduled_task_service
    log = service.get_log(out["log_id"])
    assert log["status"] == "success" and log["questions_succeeded"] == 1
    assert service.get_task(tid)["run_count"] == 1

    # 相同 run_key 重跑：不新建日志、不重复计数
    out2 = executor._execute_core(tid, "manual", "core-run-1", "test-worker")
    assert out2["duplicate"] is True and out2["log_id"] == out["log_id"]
    assert service.get_task(tid)["run_count"] == 1


def test_execute_core_rejects_unowned_task(isolated, monkeypatch):
    from backend.modules.flow.tasks import executor
    _seed_owner(isolated)
    tid = _task()
    db.execute_write("UPDATE adh_scheduled_tasks SET owner_id=0 WHERE id=%s", (tid,))
    monkeypatch.setattr(executor, "_execute_sql_on_datasource", lambda *a: {"rows": [], "columns": []})
    out = executor._execute_core(tid, "manual", "unowned-run", "test-worker")
    assert out["status"] == "failed"
    log = scheduled.scheduled_task_service.get_log(out["log_id"])
    assert log["status"] == "failed" and log["stage_error_code"] == "OWNER_REQUIRED"
    # 无创建者 → 从未取数
    assert log["questions_succeeded"] == 0
