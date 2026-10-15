"""RLS (Row-Level Security) Service — policy management and enforcement.

Provides CRUD for RLS policies, column policies, user attributes,
and SQL filtering logic for row-level and column-level security.
"""

import logging
import time
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _gen_id():
    return int(time.time() * 1000000)


# 列策略是纯黑名单: 每一行必须声明要剔除/脱敏的列, 合法 access_type 仅此两种。
# 'visible'(白名单语义)会被执行层 get_effective_policies 静默忽略——保存它等于把
# "配了不生效"的缺陷落库, 必须在写入口 fail-loud 拒绝(白名单能力由表策略
# policy_type='table' 承担, 列的可见性白名单可通过删除列策略或建表策略实现)。
_VALID_COLUMN_ACCESS = frozenset({"hidden", "masked"})


class RLSService:
    """RLS policy management service."""

    # ── Policy CRUD ────────────────────────────────────────────────

    def list_policies(self, workspace_id: int, datasource_id: int = None,
                      table_name: str = None, page: int = 1, size: int = 20) -> dict:
        """List RLS policies with optional filters."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where = []
                params = []
                # workspace_id=0 = 不限工作空间(全局清单, 供角色绑定选择器)
                if workspace_id:
                    where.append("workspace_id = %s")
                    params.append(workspace_id)
                if datasource_id:
                    where.append("datasource_id = %s")
                    params.append(datasource_id)
                if table_name:
                    where.append("table_name = %s")
                    params.append(table_name)

                where_clause = ("WHERE " + " AND ".join(where)) if where else ""

                cur.execute(f"SELECT COUNT(*) as total FROM adh_rls_policies {where_clause}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_rls_policies {where_clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset]
                )
                items = cur.fetchall()
                return {"total": total, "items": items}
        finally:
            conn.close()

    def get_policy(self, policy_id: int) -> Optional[dict]:
        """Get a single policy by ID."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_rls_policies WHERE id = %s", (policy_id,))
                return cur.fetchone()
        finally:
            conn.close()

    def create_policy(self, data: dict) -> int:
        """Create a new RLS policy."""
        policy_id = _gen_id()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_rls_policies
                       (id, name, description, workspace_id, datasource_id, table_name,
                        policy_type, filter_type, filter_expr, user_attribute, is_active, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (policy_id, data["name"], data.get("description", ""),
                     data["workspace_id"], data["datasource_id"], data["table_name"],
                     data.get("policy_type", "both"),
                     data.get("filter_type", "condition"),
                     data.get("filter_expr", ""),
                     data.get("user_attribute", ""),
                     data.get("is_active", 1),
                     data.get("created_by"))
                )
                conn.commit()
                return policy_id
        finally:
            conn.close()

    def update_policy(self, policy_id: int, data: dict) -> bool:
        """Update an existing RLS policy.

        返回 True=更新落库(含值未变化的幂等保存); False=没有可更新字段或策略不存在
        (调用方必须据此显式报错, 不得假成功)。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fields = []
                params = []
                for key in ["name", "description", "datasource_id", "table_name",
                            "policy_type", "filter_type",
                            "filter_expr", "user_attribute", "is_active"]:
                    if key in data:
                        fields.append(f"{key} = %s")
                        params.append(data[key])
                if not fields:
                    return False
                params.append(policy_id)
                cur.execute(
                    f"UPDATE adh_rls_policies SET {', '.join(fields)} WHERE id = %s",
                    params
                )
                if cur.rowcount == 0:
                    # MySQL 对「值未变化」的 UPDATE 也报 affected=0:
                    # 记录仍在→幂等保存成功; 记录已不在→更新未生效
                    cur.execute("SELECT id FROM adh_rls_policies WHERE id = %s", (policy_id,))
                    return cur.fetchone() is not None
                conn.commit()
                return True
        finally:
            conn.close()

    def delete_policy(self, policy_id: int) -> bool:
        """Delete a RLS policy and its column policies."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_rls_column_policies WHERE policy_id = %s", (policy_id,))
                cur.execute("DELETE FROM adh_rls_policies WHERE id = %s", (policy_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Column Policy CRUD ─────────────────────────────────────────

    def get_column_policies(self, policy_id: int) -> list:
        """Get all column policies for a given RLS policy."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adh_rls_column_policies WHERE policy_id = %s ORDER BY column_name",
                    (policy_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_column_policies(self, policy_id: int, columns: list) -> bool:
        """Replace all column policies for a policy (纯黑名单: 列出的列被屏蔽/脱敏).

        Each column dict should have: column_name, access_type('hidden'|'masked'),
        mask_pattern (optional), description (optional).

        非空列策略必须命中已有列策略本体(调用方应编辑列策略本身, 不新建空壳);
        黑名单行 access_type 非法(visible 等)时整批拒绝, 不落半成品。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT policy_id FROM adh_rls_column_policies WHERE policy_id = %s", (policy_id,))
                has_rows = cur.fetchone() is not None
                if columns and not has_rows:
                    # 双保险: roles.py 业务路由同样拒绝; 基线表列策略本体不存在时
                    # (adh_rls_column_policies 为空) 允许首次建立, 不阻断初始化
                    cur.execute("SELECT id FROM adh_rls_policies WHERE id = %s", (policy_id,))
                    if cur.fetchone():
                        raise ValueError("列策略未建基线，请编辑列策略本体，勿新建空壳策略")
                for col in columns:
                    access_type = col.get("access_type", "hidden")
                    if access_type not in _VALID_COLUMN_ACCESS:
                        raise ValueError(
                            f"列 '{col.get('column_name', '')}' 的 access_type={access_type!r} 非法，"
                            f"列黑名单仅支持 {'/'.join(sorted(_VALID_COLUMN_ACCESS))}")
                # Delete existing
                cur.execute("DELETE FROM adh_rls_column_policies WHERE policy_id = %s", (policy_id,))
                # Insert new
                for col in columns:
                    cur.execute(
                        """INSERT INTO adh_rls_column_policies
                           (id, policy_id, column_name, access_type, mask_pattern, description)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (_gen_id(), policy_id, col["column_name"],
                         col.get("access_type", "hidden"),
                         col.get("mask_pattern", ""),
                         col.get("description", ""))
                    )
                conn.commit()
                return True
        finally:
            conn.close()

    # ── Effective Policies ─────────────────────────────────────────

    def get_effective_policies(self, user_id: int, workspace_id: int,
                               datasource_id: int, table_name: str) -> dict:
        """Get the effective RLS policies for a user/table combination.

        勾选才生效: 仅应用「用户任一角色绑定的策略」(adh_role_rls_policies);
        角色未绑定任何策略 = 不施加任何 RLS 行/列限制。

        Returns:
            {
                "row_filter": "region = 'cn'",           # SQL WHERE fragment or ""
                "hidden_columns": ["salary", "ssn"],     # columns to remove from SELECT
                "masked_columns": {"phone": "partial"},   # columns to mask
                "policies_applied": [policy_id, ...]      # which policies were applied
            }

        表策略(policy_type='table')不在此消费: 它是表级可见性白名单,
        由 enforcer 在表访问裁决处应用 get_role_table_whitelist()。
        """
        result = {
            "row_filter": "",
            "hidden_columns": [],
            "masked_columns": {},
            "policies_applied": [],
        }

        from backend.core.role_service import role_service

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # 勾选才生效: 仅应用「用户任一角色绑定的策略」; 无角色/无绑定 = 无限制
                roles = role_service.get_user_roles(user_id, workspace_id)
                role_ids = [r["id"] for r in roles]
                if not role_ids:
                    return result
                ph = ",".join(["%s"] * len(role_ids))
                cur.execute(
                    f"SELECT DISTINCT policy_id FROM adh_role_rls_policies WHERE role_id IN ({ph})",
                    role_ids)
                bound_ids = [r["policy_id"] for r in cur.fetchall()]
                if not bound_ids:
                    return result

                # Find matching policies (绑定集 ∩ workspace/datasource/table/is_active 匹配)
                ph2 = ",".join(["%s"] * len(bound_ids))
                cur.execute(
                    f"""SELECT * FROM adh_rls_policies
                        WHERE id IN ({ph2})
                          AND (workspace_id = %s OR workspace_id = 0)
                          AND datasource_id = %s AND table_name = %s
                          AND is_active = 1""",
                    (*bound_ids, workspace_id, datasource_id, table_name)
                )
                policies = cur.fetchall()

                if not policies:
                    return result

                # :user_xxx 属性取值: 统一由用户角色权限管理(角色属性, adh_role_attributes);
                # 旧版按用户单独配置的 adh_rls_user_attributes 已退役(不再读取)
                user_attrs = role_service.get_user_effective_attributes(user_id, workspace_id)

                for policy in policies:
                    pid = policy["id"]
                    result["policies_applied"].append(pid)

                    # Row-level filter
                    if policy["policy_type"] in ("row", "both") and policy.get("filter_expr"):
                        filter_expr = policy["filter_expr"]
                        if policy["filter_type"] == "user_attribute" and policy.get("user_attribute"):
                            attr_key = policy["user_attribute"]
                            attr_val = user_attrs.get(attr_key, "")
                            # Replace :user_xxx with actual value
                            filter_expr = filter_expr.replace(f":user_{attr_key}", f"'{attr_val}'")
                        if result["row_filter"]:
                            result["row_filter"] += " AND " + filter_expr
                        else:
                            result["row_filter"] = filter_expr

                    # Column-level policies
                    if policy["policy_type"] in ("column", "both"):
                        cur.execute(
                            "SELECT column_name, access_type, mask_pattern FROM adh_rls_column_policies WHERE policy_id = %s",
                            (pid,)
                        )
                        col_policies = cur.fetchall()
                        for cp in col_policies:
                            col = cp["column_name"]
                            if cp["access_type"] == "hidden":
                                if col not in result["hidden_columns"]:
                                    result["hidden_columns"].append(col)
                            elif cp["access_type"] == "masked":
                                result["masked_columns"][col] = cp.get("mask_pattern") or "partial"
                            else:
                                # 历史脏数据可能残留非法 access_type(如 visible): 忽略其效果
                                # 但必须显式告警提醒治理, 不得静默当不存在
                                logger.warning(
                                    "列策略 %s: 列 %r 的 access_type=%r 未知，已忽略（请修复该列策略）",
                                    pid, col, cp["access_type"])

                return result
        finally:
            conn.close()

    # ── Table Whitelist (policy_type='table') ──────────────────────

    def get_role_table_whitelist(self, user_id: int, workspace_id: int,
                                 datasource_id: int) -> Optional[set]:
        """角色绑定的表级白名单(表策略 policy_type='table')。

        语义: 未绑定任何表策略的角色 = 不限制(兼容现状, 表可见性仍由角色数据源
        授权表决定); 一旦绑定了任何表策略, 该角色对本源的可见表**收窄**为白名单
        交集(只增严不减, 与"角色策略不得弱化敏感基线"同哲学)。

        Returns:
            None  — 该用户(经其所有角色)未绑定任何**表策略**(不限制);
            set() — 绑定了表策略但该源无任何允许表(全部拒绝);
            set(表名) — 允许的表名集合(小写比较在调用方处理, 与 RLS 行策略同源口径)。
        """
        from backend.core.role_service import role_service
        roles = role_service.get_user_roles(user_id, workspace_id)
        role_ids = [r["id"] for r in roles]
        if not role_ids:
            return None
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                ph = ",".join(["%s"] * len(role_ids))
                cur.execute(
                    f"SELECT DISTINCT policy_id FROM adh_role_rls_policies WHERE role_id IN ({ph})",
                    role_ids)
                bound_ids = [r["policy_id"] for r in cur.fetchall()]
                if not bound_ids:
                    return None
                ph2 = ",".join(["%s"] * len(bound_ids))
                cur.execute(
                    f"""SELECT table_name FROM adh_rls_policies
                        WHERE id IN ({ph2}) AND policy_type = 'table' AND is_active = 1
                          AND datasource_id = %s
                          AND (workspace_id = %s OR workspace_id = 0)""",
                    (*bound_ids, datasource_id, workspace_id)
                )
                rows = cur.fetchall()
                if not rows:
                    # 角色只绑了行/列策略未绑表策略 → 表级不限制
                    return None
                return {r["table_name"] for r in rows if r.get("table_name")}
        finally:
            conn.close()

    # ── Role Attribute Keys (行策略"属性名"选择框清单) ────────────

    def list_role_attr_keys(self) -> list:
        """全部角色属性 key(去重排序): 行策略表单的属性名候选清单。

        与 rls_loader/mdl_compiler 的 :user_{attr} 替换口径一致——可选属性
        统一由角色属性(adh_role_attributes)维护, 用户级清单已退役。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT attr_key FROM adh_role_attributes ORDER BY attr_key")
                return [r["attr_key"] for r in cur.fetchall() if r.get("attr_key")]
        finally:
            conn.close()

    # ── Audit Logging ──────────────────────────────────────────────

    def log_audit(self, user_id: int, workspace_id: int, policy_id: int = None,
                  policy_name: str = "", table_name: str = "", action: str = "",
                  original_sql: str = "", filtered_sql: str = "") -> None:
        """Log an RLS enforcement event."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_rls_audit_logs
                       (id, user_id, workspace_id, policy_id, policy_name, table_name,
                        action, original_sql, filtered_sql)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (_gen_id(), user_id, workspace_id, policy_id, policy_name,
                     table_name, action, original_sql, filtered_sql)
                )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to log RLS audit: %s", e)
        finally:
            conn.close()

    def list_audit_logs(self, workspace_id: int, user_id: int = None,
                        page: int = 1, size: int = 20) -> dict:
        """List RLS audit logs.
        
        workspace_id=0 表示查询所有工作空间的日志（管理员视角）。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where = []
                params = []
                # workspace_id=0 表示查所有工作空间
                if workspace_id:
                    where.append("workspace_id = %s")
                    params.append(workspace_id)
                if user_id:
                    where.append("user_id = %s")
                    params.append(user_id)

                where_clause = " AND ".join(where) if where else "1=1"
                cur.execute(f"SELECT COUNT(*) as total FROM adh_rls_audit_logs WHERE {where_clause}", params)
                total = cur.fetchone()["total"]

                offset = (page - 1) * size
                cur.execute(
                    f"SELECT * FROM adh_rls_audit_logs WHERE {where_clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [size, offset]
                )
                return {"total": total, "items": cur.fetchall()}
        finally:
            conn.close()


# Singleton instance
rls_service = RLSService()
