"""Role-Based Access Control Service.

Manages roles, role attributes (data scope), user-role assignments,
and workspace-role associations.

Permission model:
  Role → defines data scope (RLS attributes like region=cn)
  User → assigned to one or more roles
  Workspace → authorized to roles (not individual users)
  RLS Policy → uses role attributes for row filtering
"""

import logging
import time
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _gen_id():
    return int(time.time() * 1000000)


class RoleService:
    """Role and permission management service."""

    # ── Role CRUD ──────────────────────────────────────────────────

    def list_roles(self, workspace_id: int = None) -> list:
        """List all roles（workspace_id 参数已废弃：工作空间角色下线，保留签名兼容）。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_roles WHERE is_active = 1 ORDER BY is_system DESC, name")
                return cur.fetchall()
        finally:
            conn.close()

    def get_role(self, role_id: int) -> Optional[dict]:
        """Get a single role with its attributes."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_roles WHERE id = %s", (role_id,))
                role = cur.fetchone()
                if role:
                    cur.execute(
                        "SELECT * FROM adh_role_attributes WHERE role_id = %s ORDER BY workspace_id, attr_key",
                        (role_id,)
                    )
                    role["attributes"] = cur.fetchall()
                    cur.execute(
                        "SELECT u.id as user_id, u.username FROM adh_user_roles ur JOIN adh_users u ON u.id = ur.user_id WHERE ur.role_id = %s",
                        (role_id,)
                    )
                    role["users"] = cur.fetchall()
                return role
        finally:
            conn.close()

    def create_role(self, name: str, display_name: str, description: str = "") -> int:
        """Create a new role and its associated AS-BOT (one-to-one binding)."""
        role_id = _gen_id()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_roles (id, name, display_name, description) VALUES (%s, %s, %s, %s)",
                    (role_id, name, display_name, description)
                )
                # 联动创建同名 AS-BOT（角色-AS-BOT 一对一绑定）
                import json
                default_persona = {
                    "responsibility": f"协助 {display_name or name} 角色完成数据分析与相关任务",
                    "style": "专业、简洁、数据驱动，中文回答",
                    "boundary": "只回答数据相关问题，不臆造数据，涉及权限外数据应说明并拒绝"
                }
                default_tools = {
                    "mcp": {
                        "semantic": ["get_metrics", "get_glossary", "knowledge_search", "run_semantic_query"],
                        "catalog": ["search_metadata", "get_table_schema", "list_datasources"]
                    },
                    "groups": ["semantic", "catalog"],
                    "external": {},
                    "standard": ["read", "grep", "glob"]
                }
                cur.execute(
                    """INSERT INTO adh_as_bots 
                       (as_bot_key, name, display_name, description, category, system_prompt, persona, tools, 
                        chart_enabled, is_active, is_builtin, role_id, workspace_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (name, name, display_name or name, description or f"{display_name or name}角色的AI助手",
                     "custom",
                     f"你是 {display_name or name} 的AI助手，负责协助完成数据分析与相关任务。",
                     json.dumps(default_persona, ensure_ascii=False),
                     json.dumps(default_tools, ensure_ascii=False),
                     1, 1, 0, role_id, 0)
                )
                conn.commit()
                logger.info(f"Created role '{name}' (id={role_id}) with associated AS-BOT")
                return role_id
        finally:
            conn.close()

    def update_role(self, role_id: int, data: dict) -> bool:
        """Update role metadata."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fields = []
                params = []
                for key in ["display_name", "description", "is_active"]:
                    if key in data:
                        fields.append(f"{key} = %s")
                        params.append(data[key])
                if not fields:
                    return False
                params.append(role_id)
                cur.execute(f"UPDATE adh_roles SET {', '.join(fields)} WHERE id = %s", params)
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_role(self, role_id: int) -> bool:
        """Delete a role (only if not system role) and its associated AS-BOT."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT is_system, name FROM adh_roles WHERE id = %s", (role_id,))
                role = cur.fetchone()
                if not role or role.get("is_system"):
                    return False
                # 联动删除关联 AS-BOT（角色-AS-BOT 一对一绑定）
                cur.execute("DELETE FROM adh_as_bots WHERE role_id = %s", (role_id,))
                logger.info(f"Deleted AS-BOT for role '{role.get('name')}' (role_id={role_id})")
                cur.execute("DELETE FROM adh_role_attributes WHERE role_id = %s", (role_id,))
                cur.execute("DELETE FROM adh_user_roles WHERE role_id = %s", (role_id,))
                cur.execute("DELETE FROM adh_role_dashboard_access WHERE role_id = %s", (role_id,))
                cur.execute("DELETE FROM adh_roles WHERE id = %s", (role_id,))
                conn.commit()
                return True
        finally:
            conn.close()

    # ── Role Attributes (Data Scope) ───────────────────────────────

    def get_role_attributes(self, role_id: int, workspace_id: int = None) -> dict:
        """Get role attributes as a dict {attr_key: attr_value}."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                if workspace_id is not None:
                    cur.execute(
                        "SELECT attr_key, attr_value FROM adh_role_attributes WHERE role_id = %s AND workspace_id = %s",
                        (role_id, workspace_id)
                    )
                else:
                    cur.execute(
                        "SELECT attr_key, attr_value FROM adh_role_attributes WHERE role_id = %s",
                        (role_id,)
                    )
                return {r["attr_key"]: r["attr_value"] for r in cur.fetchall()}
        finally:
            conn.close()

    def set_role_attributes(self, role_id: int, workspace_id: int, attrs: dict) -> bool:
        """Set role attributes for a workspace (replace all)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_role_attributes WHERE role_id = %s AND workspace_id = %s",
                    (role_id, workspace_id)
                )
                for key, value in attrs.items():
                    cur.execute(
                        "INSERT INTO adh_role_attributes (id, role_id, workspace_id, attr_key, attr_value) VALUES (%s, %s, %s, %s, %s)",
                        (_gen_id(), role_id, workspace_id, key, str(value))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    # ── User-Role Assignment ───────────────────────────────────────

    def write_user_role_binding(self, cur, user_id: int, role_name: str) -> int:
        """用户-角色绑定唯一写入口：adh_user_roles（真值源）同事务写入。

        设计口径（2026-10 收口）：adh_user_roles 是用户→角色的**唯一真值源**
        （数据权限/API 权限码门控均经它消费）；adh_users.role_id 绑定角色 id、
        user_role 名列仅是登录/JWT 的显示缓存，随真值源同事务同步。
        **工作空间角色已废除**（无 workspace 维度）。调用方持有事务游标
        （失败一起回滚）。

        role_name→role_id 按 adh_roles.name 解析；未知名 fail-loud 抛 ValueError，
        不静默写 0/跳过。返回 role_id。
        """
        cur.execute("SELECT id FROM adh_roles WHERE name = %s", (str(role_name or ""),))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"角色不存在: {role_name!r}（请先在角色管理中创建）")
        role_id = int(row["id"])
        cur.execute("DELETE FROM adh_user_roles WHERE user_id = %s", (user_id,))
        cur.execute(
            "INSERT INTO adh_user_roles (id, user_id, role_id) VALUES (%s, %s, %s)",
            (_gen_id(), user_id, role_id))
        return role_id

    def _apply_user_role(self, cur, user_id: int, role_name: str) -> int:
        """游标版绑定写入（真值源 + adh_users.role_id 绑定 + 名缓存同事务）。"""
        role_id = self.write_user_role_binding(cur, user_id, role_name)
        cur.execute(
            "UPDATE adh_users SET role_id = %s, user_role = %s, updated_at = %s WHERE id = %s",
            (role_id, str(role_name), time.strftime("%Y-%m-%d %H:%M:%S"), user_id))
        return role_id

    def set_global_role(self, user_id: int, role_name: str) -> bool:
        """独立事务版全局角色分配（管理接口/数据修复用）。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                self._apply_user_role(cur, user_id, role_name)
            conn.commit()
            return True
        finally:
            conn.close()

    def assign_user_role(self, user_id: int, role_id: int, workspace_id: int = 0) -> bool:
        """Assign a role to a user（全局绑定）。

        必须走绑定唯一写入口（真值源 + role_id 绑定 + 名缓存同事务），不得只写
        一处（历史缺陷：列有值真值源无行 → 零权限码被 API 门控 403）。
        workspace_id 参数已废弃（工作空间角色下线），保留签名兼容调用方。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM adh_roles WHERE id = %s", (role_id,))
                row = cur.fetchone()
                if not row:
                    logger.warning("[role_service] assign_user_role 角色不存在: role_id=%s", role_id)
                    return False
                self._apply_user_role(cur, user_id, str(row["name"]))
                conn.commit()
                return True
        finally:
            conn.close()

    def remove_user_role(self, user_id: int, role_id: int, workspace_id: int = 0) -> bool:
        """Remove a role from a user（workspace_id 参数已废弃，保留签名兼容）。

        移除后**回落 viewer**（口径：全局角色不可为空——登录/JWT 与 API 门控
        都消费绑定，空角色=零权限码死号）。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM adh_user_roles WHERE user_id = %s AND role_id = %s",
                    (user_id, role_id)
                )
                removed = cur.rowcount > 0
                if removed:
                    self._apply_user_role(cur, user_id, "viewer")
                conn.commit()
                return removed
        finally:
            conn.close()

    def get_user_roles(self, user_id: int, workspace_id: int = None) -> list:
        """Get roles assigned to a user（真值源 adh_user_roles；workspace_id 参数已废弃）。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT r.* FROM adh_roles r
                       JOIN adh_user_roles ur ON ur.role_id = r.id
                       WHERE ur.user_id = %s AND r.is_active = 1""",
                    (user_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    # ── 工作空间私有化(个人工作站): 属主校验与双配额 ─────────────

    def check_workspace_owner(self, user_id: int, workspace_id: int) -> bool:
        """工作空间随用户走: 仅属主可访问(成员体系已退役)。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM adh_workspaces WHERE id = %s AND owner_id = %s",
                    (workspace_id, user_id))
                return cur.fetchone() is not None
        finally:
            conn.close()

    def get_user_workspace_quota(self, user_id: int) -> dict:
        """每用户工作空间配额; 无配置行 = 缺省 5 个空间 / 每空间 5GB。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT max_workspaces, disk_quota_bytes FROM adh_user_workspace_quota WHERE user_id = %s",
                    (user_id,))
                row = cur.fetchone()
                return dict(row) if row else {"max_workspaces": 5, "disk_quota_bytes": 5 * 1024 ** 3}
        finally:
            conn.close()

    def count_user_workspaces(self, user_id: int) -> int:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM adh_workspaces WHERE owner_id = %s", (user_id,))
                return int(cur.fetchone()["c"])
        finally:
            conn.close()

    def set_user_workspace_quota(self, user_id: int, max_workspaces: int, disk_quota_bytes: int) -> bool:
        """设置/更新用户工作空间配额(管理员)。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adh_user_workspace_quota (user_id, max_workspaces, disk_quota_bytes) "
                    "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "max_workspaces=VALUES(max_workspaces), disk_quota_bytes=VALUES(disk_quota_bytes)",
                    (user_id, max_workspaces, disk_quota_bytes))
                conn.commit()
                return True
        finally:
            conn.close()

    def list_workspace_quotas(self) -> list:
        """管理员统管: 全用户 + 各自空间清单(磁盘用量由 API 层补充)。"""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT u.id AS user_id, u.username,
                              COALESCE(q.max_workspaces, 5) AS max_workspaces,
                              COALESCE(q.disk_quota_bytes, %s) AS disk_quota_bytes
                       FROM adh_users u
                       LEFT JOIN adh_user_workspace_quota q ON q.user_id = u.id
                       ORDER BY u.id""", (5 * 1024 ** 3,))
                users = cur.fetchall()
                for item in users:
                    cur.execute(
                        "SELECT id, name, is_default FROM adh_workspaces "
                        "WHERE owner_id = %s ORDER BY is_default DESC, id",
                        (item["user_id"],))
                    item["workspaces"] = cur.fetchall()
                return users
        finally:
            conn.close()

    def get_user_effective_attributes(self, user_id: int, workspace_id: int) -> dict:
        """Get the merged attributes for a user from all their roles.

        Later roles override earlier ones if keys conflict.
        """
        roles = self.get_user_roles(user_id, workspace_id)
        merged = {}
        for role in roles:
            attrs = self.get_role_attributes(role["id"], workspace_id)
            merged.update(attrs)
        return merged

    def check_user_workspace_access(self, user_id: int, workspace_id: int) -> bool:
        """已退役：工作空间随用户走（仅属主，见 check_workspace_owner），
        工作空间角色体系已下线；保留方法体仅为兼容旧调用点（均无）。"""
        return self.check_workspace_owner(user_id, workspace_id)

    # ── Datasource Access ──────────────────────────────────────────

    def get_role_datasources(self, role_id: int) -> list:
        """Get datasources a role can access."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT rda.*, ds.name as datasource_name, ds.db_type
                       FROM adh_role_datasource_access rda
                       LEFT JOIN adh_datasources ds ON ds.id = rda.datasource_id
                       WHERE rda.role_id = %s ORDER BY ds.name""",
                    (role_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_role_datasources(self, role_id: int, datasource_ids: list) -> bool:
        """Replace all datasource access for a role."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_role_datasource_access WHERE role_id = %s", (role_id,))
                for ds_id in datasource_ids:
                    cur.execute(
                        "INSERT INTO adh_role_datasource_access (id, role_id, datasource_id) VALUES (%s, %s, %s)",
                        (_gen_id(), role_id, ds_id)
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    def get_user_allowed_datasources(self, user_id: int, workspace_id: int = 0) -> list:
        """Get all datasource IDs a user can access via their roles.

        Returns list of datasource_ids。**空列表 = 无任何授权（fail-closed）**，
        不是“不限制”——waker-datasource-domain §1 明确禁止把空授权解释为全量，
        消费方一律按 `datasource_id not in allowed` 拒绝，不得写 `if allowed and ...`。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT rda.datasource_id
                       FROM adh_user_roles ur
                       JOIN adh_role_datasource_access rda ON rda.role_id = ur.role_id
                       WHERE ur.user_id = %s""",
                    (user_id,)
                )
                return [r["datasource_id"] for r in cur.fetchall()]
        finally:
            conn.close()

    # ── 功能 AI 能力（权限码 → AS-BOT 功能能力继承）────────────────────────────

    def get_user_role_ai_perms(self, user_id: int, workspace_id: int = 0) -> dict:
        """用户经角色持有的权限码及其 AI 可调用级别。

        Returns ``{perm_code: {"ai_access", "label", "ai_note"}}`` —— 供
        ``tool_policy.compile_policy`` 做功能能力继承判定（AS-BOT 自动继承，
        AS-BOT 仅做减法）。

        口径与其它数据权限路径一致：走 ``adh_user_roles``（唯一真值源，无 workspace
        维度——工作空间角色已下线），不走 JWT 里的 ``user_role`` 列缓存。

        ``ai_access`` 取 ``adh_perm_registry`` 的配置值（管理员可调），
        但它不是最终裁决：涉密硬上界由 ``perm_link`` 再收窄一次，
        配置写成 write 也抬不上去。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT p.perm_code, p.ai_access, p.label, p.ai_note
                       FROM adh_user_roles ur
                       JOIN adh_role_perms rp ON rp.role_id = ur.role_id
                       JOIN adh_perm_registry p ON p.perm_code = rp.perm_code
                       WHERE ur.user_id = %s
                         AND p.is_active = 1""",
                    (user_id,)
                )
                return {
                    r["perm_code"]: {
                        "ai_access": r.get("ai_access") or "none",
                        "label": r.get("label") or r["perm_code"],
                        "ai_note": r.get("ai_note") or "",
                    }
                    for r in cur.fetchall()
                }
        finally:
            conn.close()

    def get_user_role_perm_codes(self, user_id: int, workspace_id: int = 0) -> list:
        """用户经角色持有的权限码清单（与 get_user_role_ai_perms 同口径）。"""
        return sorted(self.get_user_role_ai_perms(user_id, workspace_id))

    # ── Dashboard Visibility (看板可见性按角色授权) ───────────────────

    def get_role_dashboards(self, role_id: int) -> list:
        """Get dashboard IDs visible to a role, with display info."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT rda.dashboard_id, d.name, d.status
                       FROM adh_role_dashboard_access rda
                       LEFT JOIN adh_dashboards d ON d.id = rda.dashboard_id
                       WHERE rda.role_id = %s ORDER BY d.name""",
                    (role_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_role_dashboards(self, role_id: int, dashboard_ids: list) -> bool:
        """Replace all dashboard visibility grants for a role (full replace, idempotent)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_role_dashboard_access WHERE role_id = %s", (role_id,))
                for dash_id in set(dashboard_ids or []):
                    cur.execute(
                        "INSERT IGNORE INTO adh_role_dashboard_access (role_id, dashboard_id) VALUES (%s, %s)",
                        (role_id, int(dash_id))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    def get_dashboard_roles(self, dashboard_id: int) -> list:
        """Get role IDs that can see a dashboard (看板→角色方向的同一份授权)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT rda.role_id, r.name, r.display_name
                       FROM adh_role_dashboard_access rda
                       LEFT JOIN adh_roles r ON r.id = rda.role_id
                       WHERE rda.dashboard_id = %s ORDER BY r.name""",
                    (dashboard_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_dashboard_roles(self, dashboard_id: int, role_ids: list) -> bool:
        """Replace all role grants for a dashboard (full replace, idempotent)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_role_dashboard_access WHERE dashboard_id = %s", (dashboard_id,))
                for role_id in set(role_ids or []):
                    cur.execute(
                        "INSERT IGNORE INTO adh_role_dashboard_access (role_id, dashboard_id) VALUES (%s, %s)",
                        (int(role_id), dashboard_id)
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    def get_user_allowed_dashboards(self, user_id: int, workspace_id: int = 0) -> list:
        """Get dashboard IDs a user can see via their roles (可见性唯一裁决).

        adh_user_roles ⋈ adh_role_dashboard_access（真值源无 workspace 维度——
        工作空间角色已下线）。**fail-closed**: 空授权返回空列表 = 一律不可见,
        不解释为全量。workspace_id 参数已废弃，保留签名兼容。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT rda.dashboard_id
                       FROM adh_user_roles ur
                       JOIN adh_role_dashboard_access rda ON rda.role_id = ur.role_id
                       WHERE ur.user_id = %s""",
                    (user_id,)
                )
                return [r["dashboard_id"] for r in cur.fetchall()]
        finally:
            conn.close()

    def list_dashboard_catalog(self) -> list:
        """All dashboards (admin 配置选择器用). 看板不按工作空间归属, 平铺返回."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT d.id, d.name, d.status
                       FROM adh_dashboards d
                       ORDER BY d.name"""
                )
                return cur.fetchall()
        finally:
            conn.close()

    # ── Table Access ───────────────────────────────────────────────

    def get_role_tables(self, role_id: int) -> list:
        """Get tables a role can access."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_role_table_access WHERE role_id = %s ORDER BY datasource_id, table_name",
                    (role_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_role_tables(self, role_id: int, tables: list) -> bool:
        """Replace all table access for a role.

        Each table dict: {datasource_id, table_name, access_type}
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_role_table_access WHERE role_id = %s", (role_id,))
                for t in tables:
                    cur.execute(
                        """INSERT INTO adh_role_table_access
                           (id, role_id, datasource_id, table_name, access_type)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (_gen_id(), role_id, t.get("datasource_id", 0),
                         t["table_name"], t.get("access_type", "read"))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    def get_user_allowed_tables(self, user_id: int, datasource_id: int, workspace_id: int = 0) -> list:
        """Get table names a user can access for a specific datasource.

        Returns list of table_names. Empty list means no restriction (all allowed).
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT rta.table_name
                       FROM adh_user_roles ur
                       JOIN adh_role_table_access rta ON rta.role_id = ur.role_id
                       WHERE ur.user_id = %s
                         AND (rta.datasource_id = %s OR rta.datasource_id = 0)
                        """,
                    (user_id, datasource_id)
                )
                return [r["table_name"] for r in cur.fetchall()]
        finally:
            conn.close()

    # ── Column Access ──────────────────────────────────────────────

    def get_role_columns(self, role_id: int) -> list:
        """Get column permissions for a role."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_role_column_access WHERE role_id = %s ORDER BY datasource_id, table_name, column_name",
                    (role_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_role_columns(self, role_id: int, columns: list) -> bool:
        """Replace all column access for a role.

        Each column dict: {datasource_id, table_name, column_name, access_type, mask_pattern}
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_role_column_access WHERE role_id = %s", (role_id,))
                for c in columns:
                    cur.execute(
                        """INSERT INTO adh_role_column_access
                           (id, role_id, datasource_id, table_name, column_name, access_type, mask_pattern)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (_gen_id(), role_id, c.get("datasource_id", 0),
                         c["table_name"], c["column_name"],
                         c.get("access_type", "visible"), c.get("mask_pattern", ""))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    def get_user_column_restrictions(self, user_id: int, datasource_id: int,
                                      table_name: str, workspace_id: int = 0) -> dict:
        """Get column restrictions for a user on a specific table.

        Returns:
            {
                "hidden_columns": ["salary", "ssn"],
                "masked_columns": {"phone": "partial"},
            }
            Empty lists = no restrictions (all columns visible).
        """
        hidden = []
        masked = {}
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT rca.column_name, rca.access_type, rca.mask_pattern
                       FROM adh_user_roles ur
                       JOIN adh_role_column_access rca ON rca.role_id = ur.role_id
                       WHERE ur.user_id = %s
                         AND (rca.datasource_id = %s OR rca.datasource_id = 0)
                         AND rca.table_name = %s
                        """,
                    (user_id, datasource_id, table_name)
                )
                for r in cur.fetchall():
                    if r["access_type"] == "hidden":
                        if r["column_name"] not in hidden:
                            hidden.append(r["column_name"])
                    elif r["access_type"] == "masked":
                        masked[r["column_name"]] = r.get("mask_pattern") or "partial"
        finally:
            conn.close()
        return {"hidden_columns": hidden, "masked_columns": masked}


# Singleton instance
role_service = RoleService()
