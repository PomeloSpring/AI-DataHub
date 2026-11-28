"""看板整板刷新：一看板一份配置、异步执行实例、缓存 TTL 与任务监控投影。

浏览器只负责展示缓存与发起管理动作；真正的整板取数由 Celery 专用队列执行。
图表内交互查询仍走 ChartService，但不得覆盖本模块管理的共享缓存。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import pymysql

from backend.common.auth import resolve_execution_owner
from backend.common.db import execute_query, execute_write
from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)

ALLOWED_INTERVALS = (30, 60, 300, 600, 1800, 3600, 86400, 604800, 2592000)
MIN_CACHE_TTL = 30
MAX_CACHE_TTL = 2592000
DEFAULT_CACHE_TTL = 60
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("success", "partial", "failed", "timeout", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


_LEAK_PATTERN = re.compile(
    r"(password|passwd|pwd|host|hostname|port|username|user=|账号|密码|连接串|jdbc|"
    r"mysql://|postgres(ql)?://|oracle://|\bTraceback\b|File \")", re.IGNORECASE)
_ID_PATTERN = re.compile(r"\b(kb|id|model|notebook|datasource)=\d+", re.IGNORECASE)


def _public_error(exc: BaseException | str, limit: int = 500) -> str:
    lines = [line.strip() for line in str(exc or "").splitlines()
             if line.strip() and not _LEAK_PATTERN.search(line)]
    text = _ID_PATTERN.sub(lambda match: f"{match.group(1).lower()}=?", "；".join(lines))
    return text[:limit] if text else "执行未完成，请在服务端日志排查"


def _default_config(dashboard_id: int, user_id: int) -> dict:
    return {
        "dashboard_id": int(dashboard_id),
        "schedule_type": "disabled",
        "interval_seconds": None,
        "cron_expression": None,
        "timezone": "Asia/Shanghai",
        "cache_ttl_seconds": DEFAULT_CACHE_TTL,
        "is_active": 0,
        "created_by": int(user_id),
        "updated_by": int(user_id),
        "version": 0,
        "last_run_at": None,
        "last_status": None,
        "last_error_code": None,
        "last_error_message": None,
        "run_count": 0,
    }


def _normalize_config(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    out = dict(row)
    for field in ("created_at", "updated_at", "last_run_at"):
        out[field] = _iso(out.get(field))
    out["is_active"] = bool(int(out.get("is_active") or 0))
    return out


def _normalize_run(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    out = dict(row)
    out["run_id"] = out.get("id")
    for field in ("queued_at", "started_at", "finished_at", "lease_expires_at", "created_at", "updated_at"):
        out[field] = _iso(out.get(field))
    return out


def _dashboard(dashboard_id: int) -> Optional[dict]:
    rows = execute_query(
        "SELECT id, name, workspace_id, params, owner_id FROM adh_dashboards WHERE id=%s",
        (int(dashboard_id),), fetchone=True)
    return rows


def assert_refresh_manageable(dashboard_id: int, user: dict) -> None:
    """整板刷新/配置唯一写权限：admin 或 dashboard:manage。"""
    from backend.common.api_permission import check_api_permission

    uid = int((user or {}).get("user_id") or 0)
    role = str((user or {}).get("role") or "")
    if uid <= 0:
        raise PermissionError("缺少可信用户身份")
    if role != "admin" and not check_api_permission(
            role, "POST", f"/api/dashboard/{int(dashboard_id)}/refresh-config"):
        raise PermissionError("仅管理员或具备看板管理权限的角色可控制整板刷新")
    if not _dashboard(dashboard_id):
        raise ValueError("看板不存在")


def get_config(dashboard_id: int) -> dict:
    row = execute_query(
        "SELECT * FROM adh_dashboard_refresh_configs WHERE dashboard_id=%s",
        (int(dashboard_id),), fetchone=True)
    return _normalize_config(row) or _default_config(dashboard_id, 0)


def _validate_config(data: dict, *, current: Optional[dict] = None) -> dict:
    schedule_type = str(data.get("schedule_type") or "disabled").strip().lower()
    if schedule_type not in ("disabled", "interval", "cron"):
        raise ValueError("刷新周期类型只能是 disabled、interval 或 cron")
    interval_seconds = data.get("interval_seconds")
    cron_expression = data.get("cron_expression")
    timezone_name = str(data.get("timezone") or "Asia/Shanghai").strip()
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError("刷新时区无效") from exc
    if schedule_type == "interval":
        try:
            interval_seconds = int(interval_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("固定刷新周期无效") from exc
        if interval_seconds not in ALLOWED_INTERVALS:
            raise ValueError("固定刷新周期不在支持档位内")
        cron_expression = None
    elif schedule_type == "cron":
        cron_expression = str(cron_expression or "").strip()
        if not cron_expression:
            raise ValueError("请输入 Cron 表达式")
        from backend.core.task_runtime import call
        try:
            call("task.parse_cron", cron_expression)
        except ValueError as exc:
            raise ValueError(f"无效的 Cron 表达式: {exc}") from exc
        interval_seconds = None
    else:
        interval_seconds = None
        cron_expression = None

    raw_ttl = data.get("cache_ttl_seconds")
    if raw_ttl in (None, ""):
        if schedule_type == "interval":
            raw_ttl = interval_seconds
        elif current and current.get("cache_ttl_seconds"):
            raw_ttl = current["cache_ttl_seconds"]
        else:
            raw_ttl = DEFAULT_CACHE_TTL
    try:
        cache_ttl = int(raw_ttl)
    except (TypeError, ValueError) as exc:
        raise ValueError("缓存有效期必须是整数秒") from exc
    if not MIN_CACHE_TTL <= cache_ttl <= MAX_CACHE_TTL:
        raise ValueError(f"缓存有效期需在 {MIN_CACHE_TTL}~{MAX_CACHE_TTL} 秒之间")
    return {
        "schedule_type": schedule_type,
        "interval_seconds": interval_seconds,
        "cron_expression": cron_expression,
        "timezone": timezone_name,
        "cache_ttl_seconds": cache_ttl,
        "is_active": 1 if schedule_type != "disabled" else 0,
    }


def save_config(dashboard_id: int, data: dict, user: dict) -> dict:
    assert_refresh_manageable(dashboard_id, user)
    uid = int(user["user_id"])
    current = get_config(dashboard_id)
    clean = _validate_config(data, current=current)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_dashboard_refresh_configs "
                "(dashboard_id,schedule_type,interval_seconds,cron_expression,timezone,"
                " cache_ttl_seconds,is_active,created_by,updated_by,version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1) "
                "ON DUPLICATE KEY UPDATE schedule_type=VALUES(schedule_type),"
                "interval_seconds=VALUES(interval_seconds),cron_expression=VALUES(cron_expression),"
                "timezone=VALUES(timezone),cache_ttl_seconds=VALUES(cache_ttl_seconds),"
                "is_active=VALUES(is_active),updated_by=VALUES(updated_by),version=version+1",
                (int(dashboard_id), clean["schedule_type"], clean["interval_seconds"],
                 clean["cron_expression"], clean["timezone"], clean["cache_ttl_seconds"],
                 clean["is_active"], uid, uid))
        conn.commit()
    finally:
        conn.close()
    return get_config(dashboard_id)


def toggle_config(dashboard_id: int, is_active: bool, user: dict) -> dict:
    assert_refresh_manageable(dashboard_id, user)
    config = get_config(dashboard_id)
    if config["schedule_type"] == "disabled":
        raise ValueError("请先选择刷新周期再启用")
    execute_write(
        "UPDATE adh_dashboard_refresh_configs SET is_active=%s, updated_by=%s, version=version+1 "
        "WHERE dashboard_id=%s",
        (1 if is_active else 0, int(user["user_id"]), int(dashboard_id)))
    return get_config(dashboard_id)


def _active_run(dashboard_id: int) -> Optional[dict]:
    return execute_query(
        "SELECT * FROM adh_dashboard_refresh_runs WHERE dashboard_id=%s "
        "AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
        (int(dashboard_id),), fetchone=True)


def _default_params(dashboard: dict) -> dict:
    params = dashboard.get("params") or []
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (TypeError, ValueError):
            params = []
    out = {}
    if isinstance(params, list):
        for item in params:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = item.get("default", "")
    return out


def _cache_key(dashboard_id: int, params: dict) -> str:
    raw = json.dumps({"dashboard_id": int(dashboard_id), "params": params or {}},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _security_scope_base(user_id: int, user_role: str, workspace_id: int) -> str:
    """角色/RLS/列策略/数据集 scope 的稳定指纹；同作用域才允许复用缓存。"""
    from backend.core.role_service import role_service

    role_ids: list[int] = []
    if user_role != "admin":
        role_ids = sorted(int(row.get("id") or 0) for row in role_service.get_user_roles(user_id) or [])
        if not role_ids:
            return hashlib.sha256(f"no-role:{int(user_id)}".encode()).hexdigest()
    placeholders = ",".join(["%s"] * len(role_ids)) if role_ids else "NULL"
    params = tuple(role_ids)
    policy_rows = execute_query(
        "SELECT 'perm' AS kind, perm_code AS a, '' AS b, '' AS c FROM adh_role_perms "
        f"WHERE role_id IN ({placeholders}) UNION ALL "
        "SELECT 'attr', attr_key, attr_value, CAST(workspace_id AS CHAR) FROM adh_role_attributes "
        f"WHERE role_id IN ({placeholders}) UNION ALL "
        "SELECT 'rls_bind', CAST(policy_id AS CHAR), '', '' FROM adh_role_rls_policies "
        f"WHERE role_id IN ({placeholders}) UNION ALL "
        "SELECT 'rls', CAST(p.id AS CHAR), JSON_OBJECT('datasource_id',p.datasource_id,"
        "'table_name',p.table_name,'policy_type',p.policy_type,'filter_type',p.filter_type,"
        "'filter_expr',p.filter_expr,'user_attribute',p.user_attribute,'is_active',p.is_active), '' "
        "FROM adh_rls_policies p "
        f"JOIN adh_role_rls_policies rp ON rp.policy_id=p.id WHERE rp.role_id IN ({placeholders}) UNION ALL "
        "SELECT 'rls_col', CAST(c.id AS CHAR), c.column_name, CONCAT(c.access_type, ':', c.mask_pattern) "
        "FROM adh_rls_column_policies c JOIN adh_role_rls_policies rp ON rp.policy_id=c.policy_id "
        f"WHERE rp.role_id IN ({placeholders}) UNION ALL "
        "SELECT 'table', CAST(datasource_id AS CHAR), table_name, access_type FROM adh_role_table_access "
        f"WHERE role_id IN ({placeholders}) UNION ALL "
        "SELECT 'datasource', CAST(datasource_id AS CHAR), '', '' FROM adh_role_datasource_access "
        f"WHERE role_id IN ({placeholders}) UNION ALL "
        "SELECT 'dataset', CAST(dataset_id AS CHAR), '', '' FROM adh_role_dataset_access "
        f"WHERE role_id IN ({placeholders})",
        params + params + params + params + params + params + params + params) or []
    policy_rows = sorted(policy_rows, key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
    global_rows = execute_query(
        "SELECT 'sensitive' AS kind, CONCAT(CAST(workspace_id AS CHAR), ':', CAST(datasource_id AS CHAR)), "
        "table_name, column_name, "
        "sensitivity_level, mask_type, mask_config FROM adh_sensitive_fields UNION ALL "
        "SELECT 'dataset_scope', CAST(dataset_id AS CHAR), subject_type, CAST(subject_id AS CHAR), "
        "filters, '', '' FROM adh_dataset_scopes") or []
    global_rows = sorted(global_rows, key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
    raw = json.dumps({"role": user_role, "role_ids": role_ids, "workspace": int(workspace_id or 0),
                      "policies": policy_rows, "global": global_rows},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _chart_scope(base_scope: str, chart: dict, dataset: dict | None = None) -> str:
    """private 数据集只有属主可复用；shared 数据集按角色策略作用域共享。"""
    if dataset and str(dataset.get("visibility") or "") == "private":
        return hashlib.sha256(f"{base_scope}:private:{int(dataset.get('owner_id') or 0)}".encode()).hexdigest()
    return base_scope


def _cache_is_fresh(dashboard_id: int, config: dict, dashboard: dict, scope: str) -> bool:
    import importlib
    dash = importlib.import_module("backend.modules.viz.services.dashboard_service")

    rows = execute_query(
        "SELECT c.id,c.chart_type,c.config,c.source_type,c.source_id,c.semantic_query,c.data_cache,"
        "c.data_cache_at,c.data_cache_key,c.data_cache_scope,ds.visibility,ds.owner_id "
        "FROM adh_charts c LEFT JOIN adh_datasets ds ON c.source_type='dataset' AND c.source_id=ds.id "
        "WHERE c.dashboard_id=%s",
        (int(dashboard_id),)) or []
    targets = []
    for row in rows:
        cfg = row.get("config") or {}
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except (TypeError, ValueError):
                cfg = {}
        if str(row.get("chart_type") or "").startswith("widget_") or (isinstance(cfg, dict) and cfg.get("as_bot_design")):
            continue
        targets.append(row)
    if not targets:
        return False
    ttl = int(config.get("cache_ttl_seconds") or DEFAULT_CACHE_TTL)
    boundary = _now() - timedelta(seconds=ttl)
    board_key = _cache_key(dashboard_id, _default_params(dashboard))
    for row in targets:
        chart_scope = _chart_scope(scope, row, row)
        expected = dash._expected_cache_key(row, board_key, chart_scope)
        if not (row.get("data_cache") and row.get("data_cache_at") and row["data_cache_at"] >= boundary
                and row.get("data_cache_key") == expected and row.get("data_cache_scope") == chart_scope):
            return False
    return True


def _ensure_config(dashboard_id: int, user_id: int) -> dict:
    row = execute_query(
        "SELECT * FROM adh_dashboard_refresh_configs WHERE dashboard_id=%s",
        (int(dashboard_id),), fetchone=True)
    if row:
        return _normalize_config(row)
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO adh_dashboard_refresh_configs "
                "(dashboard_id,schedule_type,cache_ttl_seconds,is_active,created_by,updated_by,version) "
                "VALUES (%s,'disabled',%s,0,%s,%s,1)",
                (int(dashboard_id), DEFAULT_CACHE_TTL, int(user_id), int(user_id)))
        conn.commit()
    finally:
        conn.close()
    return get_config(dashboard_id)


def _create_run(dashboard_id: int, config: dict, trigger_type: str,
                run_key: str, requested_by: Optional[int]) -> Optional[dict]:
    """在配置行锁内创建活跃实例；同看板已有 queued/running 时返回它。"""
    conn = get_metadata_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dashboard_id FROM adh_dashboard_refresh_configs WHERE dashboard_id=%s FOR UPDATE",
                (int(dashboard_id),))
            if not cur.fetchone():
                conn.rollback()
                return None
            cur.execute(
                "SELECT * FROM adh_dashboard_refresh_runs WHERE dashboard_id=%s "
                "AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
                (int(dashboard_id),))
            active = cur.fetchone()
            if active:
                conn.commit()
                return active
            cur.execute(
                "SELECT * FROM adh_dashboard_refresh_runs WHERE run_key=%s",
                (run_key,))
            existing = cur.fetchone()
            if existing:
                conn.commit()
                return existing
            try:
                cur.execute(
                    "INSERT INTO adh_dashboard_refresh_runs "
                    "(dashboard_id,config_version,trigger_type,run_key,status,active_key,requested_by,lease_expires_at) "
                    "VALUES (%s,%s,%s,%s,'queued',%s,%s,DATE_ADD(UTC_TIMESTAMP(), INTERVAL 10 MINUTE))",
                    (int(dashboard_id), int(config.get("version") or 0), trigger_type,
                     run_key, f"dashboard:{int(dashboard_id)}", requested_by))
            except pymysql.IntegrityError:
                conn.rollback()
                return _active_run(dashboard_id)
            run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return execute_query("SELECT * FROM adh_dashboard_refresh_runs WHERE id=%s", (run_id,), fetchone=True)


def _dispatch(run: dict) -> None:
    from backend.core.task_runtime import send_task
    try:
        send_task("backend.modules.flow.tasks.dashboard_tasks.refresh_dashboard",
                  args=(int(run["id"]),), task_id=str(run["run_key"]),
                  queue="dashboard_refresh")
    except Exception as exc:
        _finish_run(int(run["id"]), "failed", error_code="DISPATCH_FAILED",
                    error_message=_public_error(exc))
        raise RuntimeError("看板刷新任务派发失败，请检查任务队列") from exc


def request_refresh(dashboard_id: int, user: dict) -> dict:
    assert_refresh_manageable(dashboard_id, user)
    dashboard = _dashboard(dashboard_id)
    config = _ensure_config(dashboard_id, int(user["user_id"]))
    active = _active_run(dashboard_id)
    if active:
        return {**_normalize_run(active), "cache_hit": False, "deduplicated": True}
    owner = resolve_execution_owner(int(config.get("created_by") or 0),
                                    int(dashboard.get("workspace_id") or 0))
    scope = _security_scope_base(int(owner["user_id"]), str(owner.get("role") or ""),
                                 int(owner.get("workspace_id") or 0))
    if _cache_is_fresh(dashboard_id, config, dashboard, scope):
        return {"run_id": None, "status": "cache_hit", "cache_hit": True,
                "deduplicated": False, "cache_expires_in_seconds": config["cache_ttl_seconds"]}
    run_key = f"dashboard:{dashboard_id}:manual:{_now():%Y%m%d%H%M%S%f}"
    run = _create_run(dashboard_id, config, "manual", run_key, int(user["user_id"]))
    if not run:
        raise RuntimeError("看板刷新配置不存在")
    if run.get("status") in ACTIVE_STATUSES and int(run.get("requested_by") or 0) != int(user["user_id"]):
        return {**_normalize_run(run), "cache_hit": False, "deduplicated": True}
    _dispatch(run)
    return {**_normalize_run(run), "cache_hit": False, "deduplicated": False}


def claim_scheduled_run(dashboard_id: int, run_key: str, trigger_type: str) -> Optional[dict]:
    """Beat 派发前调用；返回 None 表示配置停用或已有活跃实例。"""
    config_row = execute_query(
        "SELECT * FROM adh_dashboard_refresh_configs WHERE dashboard_id=%s AND is_active=1",
        (int(dashboard_id),), fetchone=True)
    if not config_row:
        return None
    if _active_run(dashboard_id):
        return None
    config = _normalize_config(config_row)
    dashboard = _dashboard(dashboard_id)
    if not dashboard:
        return None
    owner = resolve_execution_owner(int(config.get("created_by") or 0),
                                    int(dashboard.get("workspace_id") or 0))
    scope = _security_scope_base(int(owner["user_id"]), str(owner.get("role") or ""),
                                 int(owner.get("workspace_id") or 0))
    if _cache_is_fresh(dashboard_id, config, dashboard, scope):
        return None
    return _create_run(dashboard_id, config, trigger_type, run_key, None)


def get_run(run_id: int, dashboard_id: Optional[int] = None,
            user: Optional[dict] = None) -> Optional[dict]:
    if dashboard_id is not None and user is not None:
        assert_refresh_manageable(int(dashboard_id), user)
    sql = "SELECT * FROM adh_dashboard_refresh_runs WHERE id=%s"
    params = [int(run_id)]
    if dashboard_id is not None:
        sql += " AND dashboard_id=%s"
        params.append(int(dashboard_id))
    return _normalize_run(execute_query(sql, tuple(params), fetchone=True))


def list_runs(dashboard_id: Optional[int] = None, page: int = 1, size: int = 20) -> dict:
    page = max(1, int(page or 1))
    size = max(1, min(100, int(size or 20)))
    where = "WHERE dashboard_id=%s" if dashboard_id is not None else ""
    params = (int(dashboard_id),) if dashboard_id is not None else ()
    total = int((execute_query(f"SELECT COUNT(*) AS total FROM adh_dashboard_refresh_runs {where}",
                               params, fetchone=True) or {}).get("total") or 0)
    rows = execute_query(
        f"SELECT * FROM adh_dashboard_refresh_runs {where} ORDER BY id DESC LIMIT %s OFFSET %s",
        params + (size, (page - 1) * size)) or []
    return {"items": [_normalize_run(row) for row in rows], "total": total,
            "page": page, "page_size": size}


def _finish_run(run_id: int, status: str, *, error_code: str = "",
                error_message: str = "", total: int = 0, succeeded: int = 0,
                failed: int = 0, summary: str = "") -> bool:
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_dashboard_refresh_runs SET status=%s, finished_at=UTC_TIMESTAMP(),"
                "active_key=NULL,lease_expires_at=NULL,total_charts=%s,succeeded_charts=%s,"
                "failed_charts=%s,result_summary=%s,error_code=%s,error_message=%s "
                "WHERE id=%s AND status IN ('queued','running')",
                (status, total, succeeded, failed, summary, error_code,
                 error_message, int(run_id)))
            changed = cur.rowcount
            if changed:
                cur.execute(
                    "UPDATE adh_dashboard_refresh_configs c JOIN adh_dashboard_refresh_runs r "
                    "ON r.dashboard_id=c.dashboard_id SET c.last_run_at=UTC_TIMESTAMP(),"
                    "c.last_status=%s,c.last_error_code=%s,c.last_error_message=%s,c.run_count=c.run_count+1 "
                    "WHERE r.id=%s",
                    (status, error_code, error_message, int(run_id)))
        conn.commit()
        return bool(changed)
    finally:
        conn.close()


def execute_run(run_id: int, worker_id: str = "") -> dict:
    """Celery 入口：默认参数整板重查，逐图结果落共享缓存。"""
    from backend.modules.viz.services.dashboard_service import chart_service

    run = get_run(int(run_id))
    if not run:
        raise ValueError("看板刷新实例不存在")
    if run["status"] not in ACTIVE_STATUSES:
        return run
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_dashboard_refresh_runs SET status='running',started_at=UTC_TIMESTAMP(),"
                "worker_id=%s,lease_expires_at=DATE_ADD(UTC_TIMESTAMP(), INTERVAL 10 MINUTE) "
                "WHERE id=%s AND status='queued'",
                (worker_id, int(run_id)))
            claimed = cur.rowcount == 1
        conn.commit()
    finally:
        conn.close()
    if not claimed:
        return get_run(int(run_id))

    try:
        dashboard = _dashboard(int(run["dashboard_id"]))
        config = get_config(int(run["dashboard_id"]))
        if not dashboard:
            raise ValueError("看板不存在或已删除")
        owner = resolve_execution_owner(int(config.get("created_by") or 0),
                                        int(dashboard.get("workspace_id") or 0))
        assert_refresh_manageable(int(run["dashboard_id"]), owner)
        params = _default_params(dashboard)
        scope = _security_scope_base(int(owner["user_id"]), str(owner.get("role") or ""),
                                     int(owner.get("workspace_id") or 0))
        result = chart_service.refresh_all_charts(
            int(run["dashboard_id"]), params=params,
            user_id=int(owner["user_id"]), workspace_id=int(owner.get("workspace_id") or 0),
            username=owner.get("username") or "", persist_cache=True,
            cache_key=_cache_key(int(run["dashboard_id"]), params),
            cache_scope=scope, refresh_run_id=int(run_id))
        payloads = result.get("charts") or {}
        total = len(payloads)
        failed = sum(1 for item in payloads.values() if not isinstance(item, dict) or item.get("error"))
        succeeded = total - failed
        status = "success" if failed == 0 else ("partial" if succeeded else "failed")
        summary = f"图表刷新 {succeeded}/{total} 成功"
        if failed:
            summary += f"，{failed} 张失败"
        _finish_run(int(run_id), status, total=total, succeeded=succeeded, failed=failed,
                    summary=summary,
                    error_code="CHART_FAILED" if failed else "",
                    error_message="部分图表刷新失败，详见看板卡片提示" if failed else "")
        return get_run(int(run_id))
    except Exception as exc:  # noqa: BLE001 - 失败必须落实例，不能静默消失
        logger.exception("看板刷新执行失败 run=%s", run_id)
        _finish_run(int(run_id), "failed", error_code="EXECUTION_FAILED",
                    error_message=_public_error(exc))
        return get_run(int(run_id))


def fail_dispatch(run_id: int, error: BaseException | str) -> bool:
    return _finish_run(int(run_id), "failed", error_code="DISPATCH_FAILED",
                       error_message=_public_error(error))


def cancel_run(run_id: int) -> bool:
    return _finish_run(int(run_id), "cancelled", error_code="CANCELLED",
                       error_message="任务已被停止")


def stop_config(dashboard_id: int) -> int:
    execute_write("UPDATE adh_dashboard_refresh_configs SET is_active=0,version=version+1 WHERE dashboard_id=%s",
                  (int(dashboard_id),))
    rows = execute_query(
        "SELECT id FROM adh_dashboard_refresh_runs WHERE dashboard_id=%s AND status IN ('queued','running')",
        (int(dashboard_id),)) or []
    count = 0
    for row in rows:
        count += int(cancel_run(int(row["id"])))
    return count


def cleanup_stale(timeout_minutes: int = 10) -> int:
    rows = execute_query(
        "SELECT id FROM adh_dashboard_refresh_runs WHERE status IN ('queued','running') "
        "AND lease_expires_at IS NOT NULL AND lease_expires_at<=UTC_TIMESTAMP()") or []
    count = 0
    for row in rows:
        count += int(_finish_run(int(row["id"]), "timeout", error_code="TIMEOUT",
                                 error_message="执行超过租约未完成，已按超时清理"))
    return count


def list_configs_for_monitor() -> list:
    rows = execute_query(
        "SELECT c.*, d.name AS dashboard_name, d.workspace_id, u.username AS owner_name,"
        "(SELECT COUNT(*) FROM adh_dashboard_refresh_runs r WHERE r.dashboard_id=c.dashboard_id "
        " AND r.status IN ('queued','running')) AS active_runs,"
        "(SELECT COUNT(*) FROM adh_dashboard_refresh_runs r WHERE r.dashboard_id=c.dashboard_id "
        " AND r.status IN ('queued','running') AND r.lease_expires_at IS NOT NULL "
        " AND r.lease_expires_at<=UTC_TIMESTAMP()) AS stuck_runs "
        "FROM adh_dashboard_refresh_configs c JOIN adh_dashboards d ON d.id=c.dashboard_id "
        "LEFT JOIN adh_users u ON u.id=c.created_by ORDER BY c.updated_at DESC") or []
    out = []
    for row in rows:
        cfg = _normalize_config(row)
        cfg["name"] = f"看板刷新：{row.get('dashboard_name') or '看板已删除'}"
        cfg["schedule"] = cfg.get("cron_expression") if cfg["schedule_type"] == "cron" else (
            f"每 {cfg.get('interval_seconds')} 秒" if cfg["schedule_type"] == "interval" else "关闭")
        cfg["owner_name"] = row.get("owner_name") or "未知"
        cfg["active_runs"] = int(row.get("active_runs") or 0)
        cfg["stuck_runs"] = int(row.get("stuck_runs") or 0)
        out.append(cfg)
    return out


def decorate_dashboard_payload(payload: dict, user_id: int, user_role: str = "",
                               workspace_id: Optional[int] = None) -> None:
    """给详情载荷附加管理能力、刷新配置与缓存 TTL；按查看者剥离越权缓存。"""
    import importlib
    dash = importlib.import_module("backend.modules.viz.services.dashboard_service")
    from backend.modules.viz.services import dataset_service
    from backend.core.role_service import role_service

    dashboard_id = int(payload.get("id") or 0)
    config = get_config(dashboard_id)
    manageable = False
    try:
        assert_refresh_manageable(dashboard_id, {"user_id": user_id, "role": user_role})
        manageable = True
    except (PermissionError, ValueError):
        manageable = False
    dashboard = _dashboard(dashboard_id) or {}
    default_params = _default_params(dashboard)
    board_key = _cache_key(dashboard_id, default_params)
    ttl = int(config.get("cache_ttl_seconds") or DEFAULT_CACHE_TTL)
    boundary = _now() - timedelta(seconds=ttl)
    base_scope = _security_scope_base(int(user_id), str(user_role or ""),
                                      int(workspace_id or dashboard.get("workspace_id") or 0))
    allowed_sources = None
    if user_role != "admin":
        allowed_sources = {int(v) for v in role_service.get_user_allowed_datasources(
            int(user_id), int(workspace_id or dashboard.get("workspace_id") or 0)) or []}
    stale = False
    has_data_chart = False
    for chart in payload.get("charts") or []:
        chart_type = str(chart.get("chart_type") or "")
        if chart_type.startswith("widget_") or (chart.get("config") or {}).get("as_bot_design"):
            continue
        has_data_chart = True
        cached = chart.get("data_cache")
        if not cached:
            chart["cache_stale"] = True
            stale = True
            continue
        accessible = True
        dataset = None
        if user_role != "admin":
            if chart.get("source_type") == "dataset" and chart.get("source_id"):
                dataset = dataset_service.get_dataset(int(chart["source_id"])) or {}
                accessible = dataset_service.dataset_visible_to(
                    dataset, {"user_id": int(user_id), "role": user_role})
            else:
                source_id = int(dash._get_chart_datasource_id(chart) or 0)
                if source_id > 0:
                    accessible = source_id in (allowed_sources or set())
        if not accessible:
            chart["data_cache"] = None
            chart["cache_access_denied"] = True
            chart["cache_stale"] = True
            stale = True
            continue
        cache_at = chart.get("data_cache_at")
        if hasattr(cache_at, "isoformat"):
            cache_at = cache_at.isoformat()
        chart["data_cache_at"] = cache_at
        chart_scope = _chart_scope(base_scope, chart, dataset)
        expected_key = dash._expected_cache_key(chart, board_key, chart_scope)
        valid = bool(cache_at and chart.get("data_cache_key") == expected_key
                     and chart.get("data_cache_scope") == chart_scope)
        try:
            valid = valid and datetime.fromisoformat(str(cache_at).replace("Z", "+00:00")).replace(tzinfo=None) >= boundary
        except (TypeError, ValueError):
            valid = False
        chart["cache_stale"] = not valid
        chart["cache_expires_at"] = (
            (datetime.fromisoformat(str(cache_at).replace("Z", "+00:00")).replace(tzinfo=None)
             + timedelta(seconds=ttl)).isoformat() if cache_at and valid else None)
        stale = stale or not valid
    payload["refresh_manageable"] = manageable
    payload["refresh_config"] = config if manageable else {
        "schedule_type": config.get("schedule_type"),
        "is_active": bool(config.get("is_active")),
        "cache_ttl_seconds": ttl,
    }
    payload["cache_stale"] = bool(stale or (has_data_chart and not any(
        c.get("data_cache") for c in payload.get("charts") or [])))
    payload["cache_ttl_seconds"] = ttl


class DashboardRefreshService:
    get_config = staticmethod(get_config)
    save_config = staticmethod(save_config)
    toggle_config = staticmethod(toggle_config)
    request_refresh = staticmethod(request_refresh)
    claim_scheduled_run = staticmethod(claim_scheduled_run)
    get_run = staticmethod(get_run)
    list_runs = staticmethod(list_runs)
    execute_run = staticmethod(execute_run)
    cancel_run = staticmethod(cancel_run)
    fail_dispatch = staticmethod(fail_dispatch)
    stop_config = staticmethod(stop_config)
    cleanup_stale = staticmethod(cleanup_stale)
    list_configs_for_monitor = staticmethod(list_configs_for_monitor)
    decorate_dashboard_payload = staticmethod(decorate_dashboard_payload)


dashboard_refresh_service = DashboardRefreshService()
