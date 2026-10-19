"""工作空间删除：影响清单 + 级联清理（空间内产物 + 持久卷目录）。

口径（需求确认 2026-10）：
- **默认工作站不可删除**（adh_workspaces.is_default=1），只能删用户自建的自定义空间；
- 空间内产物**彻底删除**：会话（含磁盘工作区）、看板与图表、定时任务与 DAG 工作流、
  保存查询、通知渠道、空间级绑定配置；
- **保留**跨工作站资产（adh_user_assets 资产清单、adh_user_memory 用户记忆）、
  审计与观测流水（adh_query_audit / adh_llm_traces / adh_audit_logs /
  adh_intent_intercepts / adh_rls_audit_logs）、数据中台治理资产
  （adh_datasets / adh_data_products / 本体 / RLS 策略 / 角色）。

删除顺序纪律：先清持久卷目录再删库行——目录清理失败时库行仍在，用户可重试删除
（重试幂等：目录已不存在即视为无残留）；反过来会留下无从定位的孤儿目录。
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from backend.common.db import DBConnection

logger = logging.getLogger(__name__)

# 空间级配置/绑定表：只按 workspace_id 归属，无属主维度
_BINDING_TABLES: tuple[str, ...] = (
    "adh_workspace_users",
    "adh_workspace_datasources",
    "adh_workspace_mcp_servers",
    "adh_workspace_execution_layers",
    "adh_workspace_assets",
    "adh_workspace_skills",
    "adh_workspace_knowledge_config",
)

# 影响清单对外键名（前端确认框按此顺序渲染中文标签）
_IMPACT_KEYS: tuple[str, ...] = (
    "conversations", "dashboards", "scheduled_tasks", "dag_workflows", "saved_queries",
)


def _in_clause(values) -> str:
    return "(" + ",".join(["%s"] * len(values)) + ")"


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s",
        (table,))
    return cur.fetchone() is not None


def _owned_ids(cur, table: str, workspace_id: int, owner_id: int) -> list[int]:
    """本空间内属主创建的父资源 id（子表按父 id 级联，避免误删他人在此空间的产物）。"""
    cur.execute(f"SELECT id FROM {table} WHERE workspace_id = %s AND owner_id = %s",
                (workspace_id, owner_id))
    return [int(r["id"]) for r in cur.fetchall()]


def collect_impact(cur, workspace_id: int, owner_id: int) -> dict:
    """统计将被删除的空间内产物数量（确认框展示 + 审计留痕）。"""
    cur.execute("SELECT COUNT(*) AS c FROM adh_conversations WHERE workspace_id = %s",
                (workspace_id,))
    impact = {"conversations": int(cur.fetchone()["c"])}
    for key, table in (("dashboards", "adh_dashboards"),
                       ("scheduled_tasks", "adh_scheduled_tasks"),
                       ("dag_workflows", "adh_dag_workflows"),
                       ("saved_queries", "adh_saved_queries")):
        cur.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE workspace_id = %s AND owner_id = %s",
                    (workspace_id, owner_id))
        impact[key] = int(cur.fetchone()["c"])
    return impact


def build_cascade_statements(workspace_id: int, owner_id: int,
                             parent_ids: dict[str, list[int]],
                             available_tables: set[str]) -> list[tuple[str, tuple]]:
    """生成级联 DELETE 语句清单（纯函数，便于回归断言级联范围）。

    parent_ids: {"dashboards": [...], "scheduled_tasks": [...], "dag_workflows": [...]}
    available_tables: 库中实际存在的表名（历史遗留表可能已下线，缺表即无数据可删）。
    """
    stmts: list[tuple[str, tuple]] = []

    # 会话域按 workspace_id 整片删除（会话列表本就按空间 + 属主隔离，不留孤儿）
    if "adh_message_feedback" in available_tables:
        stmts.append(("DELETE FROM adh_message_feedback WHERE workspace_id = %s", (workspace_id,)))
    stmts.append(("DELETE FROM adh_agent_sessions WHERE workspace_id = %s", (workspace_id,)))
    stmts.append(("DELETE FROM adh_conversations WHERE workspace_id = %s", (workspace_id,)))

    dash_ids = parent_ids.get("dashboards") or []
    if dash_ids:
        # 图表/可见性授权按看板 id 级联：他人在此空间建的看板（owner 不符）不被动
        stmts.append(("DELETE FROM adh_charts WHERE dashboard_id IN " + _in_clause(dash_ids),
                      tuple(dash_ids)))
        if "adh_role_dashboard_access" in available_tables:
            stmts.append(("DELETE FROM adh_role_dashboard_access WHERE dashboard_id IN "
                          + _in_clause(dash_ids), tuple(dash_ids)))
    if "adh_chart_snapshots" in available_tables:
        stmts.append(("DELETE FROM adh_chart_snapshots WHERE workspace_id = %s", (workspace_id,)))
    stmts.append(("DELETE FROM adh_dashboards WHERE workspace_id = %s AND owner_id = %s",
                  (workspace_id, owner_id)))

    task_ids = parent_ids.get("scheduled_tasks") or []
    if "adh_scheduled_logs" in available_tables:
        if task_ids:
            stmts.append(("DELETE FROM adh_scheduled_logs WHERE scheduled_task_id IN "
                          + _in_clause(task_ids), tuple(task_ids)))
        stmts.append(("DELETE FROM adh_scheduled_logs WHERE workspace_id = %s", (workspace_id,)))
    stmts.append(("DELETE FROM adh_scheduled_tasks WHERE workspace_id = %s AND owner_id = %s",
                  (workspace_id, owner_id)))

    wf_ids = parent_ids.get("dag_workflows") or []
    if "adh_dag_runs" in available_tables:
        if wf_ids:
            stmts.append(("DELETE FROM adh_dag_runs WHERE workflow_id IN " + _in_clause(wf_ids),
                          tuple(wf_ids)))
        stmts.append(("DELETE FROM adh_dag_runs WHERE workspace_id = %s", (workspace_id,)))
    stmts.append(("DELETE FROM adh_dag_workflows WHERE workspace_id = %s AND owner_id = %s",
                  (workspace_id, owner_id)))

    stmts.append(("DELETE FROM adh_saved_queries WHERE workspace_id = %s AND owner_id = %s",
                  (workspace_id, owner_id)))
    if "adh_notification_channels" in available_tables:
        stmts.append(("DELETE FROM adh_notification_channels WHERE workspace_id = %s",
                      (workspace_id,)))

    for table in _BINDING_TABLES:
        if table in available_tables:
            stmts.append((f"DELETE FROM {table} WHERE workspace_id = %s", (workspace_id,)))

    stmts.append(("DELETE FROM adh_workspaces WHERE id = %s AND owner_id = %s",
                  (workspace_id, owner_id)))
    return stmts


def load_workspace(cur, workspace_id: int) -> dict:
    cur.execute("SELECT id, name, owner_id, is_default FROM adh_workspaces WHERE id = %s",
                (workspace_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return row


def assert_deletable(cur, workspace: dict) -> None:
    """删除前置把关：默认工作站不可删、有活执行不可删（均 fail-loud 提醒用户）。"""
    if int(workspace.get("is_default") or 0) == 1:
        raise HTTPException(
            status_code=409,
            detail="默认工作站不支持删除，仅可删除自行创建的自定义工作空间")
    cur.execute("SELECT COUNT(*) AS c FROM adh_agent_sessions WHERE workspace_id = %s "
                "AND (status = 'running' OR status = 'deleting' OR execution_token IS NOT NULL)",
                (workspace["id"],))
    busy = int(cur.fetchone()["c"])
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"该工作空间仍有 {busy} 个会话在执行或正在停止，请先停止后再删除")


def get_delete_impact(workspace_id: int, user: dict) -> dict:
    """确认框数据源：影响清单 + 磁盘用量（不落任何写操作）。"""
    from backend.modules.mind.execution.session_workspace import workspace_disk_usage
    with DBConnection() as conn:
        with conn.cursor() as cur:
            workspace = load_workspace(cur, workspace_id)
            _assert_requester_owns(cur, workspace, user)
            impact = collect_impact(cur, workspace_id, int(workspace["owner_id"]))
    impact.update(
        workspace_id=int(workspace_id),
        workspace_name=workspace["name"],
        is_default=int(workspace.get("is_default") or 0),
        disk_usage_bytes=workspace_disk_usage(int(workspace_id), int(workspace["owner_id"])),
        deletable=0 == int(workspace.get("is_default") or 0),
    )
    return impact


def _assert_requester_owns(cur, workspace: dict, user: dict) -> None:
    """工作空间随用户走：仅属主或 admin 可删（与 _check_membership 同口径）。"""
    if user.get("role") == "admin":
        return
    if int(workspace.get("owner_id") or 0) != int(user.get("user_id") or 0):
        raise HTTPException(status_code=403, detail="只能删除自己创建的工作空间")


def delete_workspace(workspace_id: int, user: dict) -> dict:
    """删除工作空间：把关 → 清持久卷目录 → 级联删库行 → 审计。

    返回影响清单（前端提示"已删除 N 个会话…"）。
    """
    from backend.common.auth import log_audit
    from backend.modules.mind.execution.session_workspace import remove_workspace_directory

    with DBConnection() as conn:
        with conn.cursor() as cur:
            workspace = load_workspace(cur, workspace_id)
            _assert_requester_owns(cur, workspace, user)
            assert_deletable(cur, workspace)
            owner_id = int(workspace["owner_id"])
            impact = collect_impact(cur, workspace_id, owner_id)

    # 阶段一：持久卷目录（失败即中止，库行保持完整，用户可重试）
    removed_files = remove_workspace_directory(workspace_id, owner_id)

    # 阶段二：库内级联（单事务，宁阻断勿留半截）
    with DBConnection() as conn:
        conn.begin()
        try:
            with conn.cursor() as cur:
                available = {t for t in (
                    "adh_message_feedback", "adh_role_dashboard_access", "adh_chart_snapshots",
                    "adh_scheduled_logs", "adh_dag_runs", "adh_notification_channels",
                    *_BINDING_TABLES) if _table_exists(cur, t)}
                # 父资源 id：看板/任务/DAG 按属主收敛（他人在此空间的产物不被动）
                parent_ids = {
                    "dashboards": _owned_ids(cur, "adh_dashboards", workspace_id, owner_id),
                    "scheduled_tasks": _owned_ids(cur, "adh_scheduled_tasks", workspace_id, owner_id),
                    "dag_workflows": _owned_ids(cur, "adh_dag_workflows", workspace_id, owner_id),
                }
                for sql, params in build_cascade_statements(
                        workspace_id, owner_id, parent_ids, available):
                    cur.execute(sql, params)
                cur.execute("SELECT COUNT(*) AS c FROM adh_workspaces WHERE id = %s", (workspace_id,))
                if int(cur.fetchone()["c"]) != 0:
                    raise HTTPException(500, "工作空间删除未生效，请联系管理员核对后重试")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    logger.info("工作空间已删除: id=%s owner=%s impact=%s removed_files=%s by=%s",
                workspace_id, owner_id, impact, removed_files, user.get("user_id"))
    log_audit(int(user.get("user_id") or 0), user.get("username") or "", "delete_workspace",
              target_type="workspace", target_id=int(workspace_id),
              detail=f"name={workspace['name']} impact={impact} removed_files={removed_files}",
              module="auth")
    return {"success": True, "message": f"工作空间「{workspace['name']}」已删除", **impact}

