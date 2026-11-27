"""Role-Based Access Control Service.

Manages roles, role attributes (data scope), user-role assignments,
and workspace-role associations.

Permission model:
  Role → defines data scope (RLS attributes like region=cn)
  User → assigned to one or more roles
  Workspace → authorized to roles (not individual users)
  RLS Policy → uses role attributes for row filtering
"""

import json
import logging
import re
import time
from contextlib import contextmanager
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _invalidate_as_bot_config(reason: str) -> None:
    """角色/权限配置写入口同批调用：轮次起点的配置缓存必须随之失效。

    口径见 `backend/common/as_bot_config_cache.py`（只缓存配置事实；工具级裁决恒实时）。
    统一清整命名空间：配置写操作是低频管理动作，逐 key 失效漏挂风险大。
    """
    from backend.common.as_bot_config_cache import invalidate
    invalidate(reason)


def _gen_id():
    return int(time.time() * 1000000)


# 复制角色时逐表复制的角色级授权（表名 → 除 id/role_id 外的业务列）。
# **不含 adh_user_roles**：一键复制是“快速造角色”，绑定用户不是角色配置的一部分，
# 由管理员在「绑定用户」里另行指派（产品口径：复制不带人）。
_ROLE_COPY_TABLES = {
    "adh_role_perms": ("perm_code",),
    "adh_role_datasource_access": ("datasource_id",),
    "adh_role_dataset_access": ("dataset_id",),
    "adh_role_dashboard_access": ("dashboard_id",),
    "adh_role_rls_policies": ("policy_id",),
    "adh_role_attributes": ("workspace_id", "attr_key", "attr_value"),
    "adh_role_table_access": ("datasource_id", "table_name", "access_type"),
    "adh_role_column_access": ("datasource_id", "table_name", "column_name", "access_type", "mask_pattern"),
}


def _as_json(value, default):
    """JSON 列取值归一：驱动对 JSON 列可能回 str 也可能回已解码对象，两种都要能吃。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return default if value is None else value


@contextmanager
def _txn(cur):
    """多语句写的显式事务：失败整体回滚，不留半态。

    为什么不能靠 conn.commit()/conn.rollback()：连接池建连带 autocommit=True
    （`backend/common/db/metadata_db.py` 的 PooledDB），每条语句各自成事务即时落库，
    失败后再发 ROLLBACK 面对的是空事务，早前语句已经各自提交——历史缺陷就是
    复制角色报 1264 后，角色行/已复制的授权行照样存下来（“报错但数据存下来了”）。
    START TRANSACTION 显式把这批语句收进同一事务，COMMIT/ROLLBACK 才真实生效。
    """
    cur.execute("START TRANSACTION")
    try:
        yield
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:  # noqa: BLE001 — 回滚失败不得掩盖原始错误
            pass
        raise
    cur.execute("COMMIT")


def default_workspace_quota() -> dict:
    """系统缺省配额（**数字只在这里从配置读**，调用点不得写死 5GB/5 个）。

    来源 `backend/common/config.py` 的 WORKSPACE_QUOTA_DEFAULT_MAX_WORKSPACES /
    WORKSPACE_DISK_QUOTA_DEFAULT_BYTES（可环境变量覆盖，换环境不改代码）。
    """
    from backend.common import config
    return {"max_workspaces": int(config.WORKSPACE_QUOTA_DEFAULT_MAX_WORKSPACES or 0),
            "disk_quota_bytes": int(config.WORKSPACE_DISK_QUOTA_DEFAULT_BYTES or 0)}


def _invalidate_datasource_derived_caches() -> None:
    """失效“我的可用数据源/可查站点”派生缓存（角色授权变更时）。

    缓存层不可用不影响权限裁决正确性（只多一次重算），故不抛出。
    """
    try:
        from backend.common.ttl_cache import datasource_cache
        datasource_cache.invalidate_prefix("auth_ds:")
        datasource_cache.invalidate_prefix("sites:")
    except Exception as e:  # noqa: BLE001
        logger.debug("[role] datasource-derived cache invalidate skipped: %s", e)


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
            with conn.cursor() as cur, _txn(cur):
                cur.execute(
                    "INSERT INTO adh_roles (id, name, display_name, description) VALUES (%s, %s, %s, %s)",
                    (role_id, name, display_name, description)
                )
                # 联动创建同名 AS-BOT（角色-AS-BOT 一对一绑定）
                default_persona = {
                    "responsibility": f"协助 {display_name or name} 角色完成数据分析与相关任务",
                    # style 不写回复语言（语言口径唯一在提示词「回复语言」段，见 backend/common/i18n）：
                    # 写进 persona 后界面切英文仍会被拉回中文，且新角色与迁移种子会双口径漂移。
                    "style": "专业、简洁、数据驱动",
                    "boundary": "只回答数据相关问题，不臆造数据，涉及权限外数据应说明并拒绝"
                }
                default_tools = {
                    "mcp": {
                        "semantic": ["get_metrics", "get_glossary", "knowledge_search", "run_semantic_query"],
                        "catalog": ["search_metadata", "get_table_schema", "list_datasources"],
                        # 血缘读与 catalog 同级：表节点按角色数据源授权 fail-closed 过滤，
                        # 业务资产节点是治理资产。下钻分析离不开「这个数从哪来」，
                        # 不给新角色默认开启就等于每个新角色都要人工补一次（口径漂移源）。
                        "lineage": ["get_lineage"],
                        # 记忆只读写本人行、不返回业务数据行，属个人工作区能力 → 新建角色默认开启
                        "memory": ["save_memory", "list_memory", "delete_memory"],
                    },
                    "groups": ["semantic", "catalog", "lineage", "memory"],
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
                logger.info(f"Created role '{name}' (id={role_id}) with associated AS-BOT")
                _invalidate_as_bot_config(f"create_role id={role_id}")
                return role_id
        finally:
            conn.close()

    def duplicate_role(self, source_role_id: int, name: str,
                       display_name: str = "", description: str = "") -> dict:
        """一键复制角色（连同 AS-BOT 配置）：快速创建同配置的新角色。

        复制范围 = 角色本体 + 全部角色级授权（权限码/数据源/数据集/看板/数据安全策略/
        数据范围属性/表级/列级）+ 关联 AS-BOT 配置（提示词/persona/工具/知识库/模型/执行层）。
        **不复制绑定用户**（adh_user_roles），新角色需重命名（name 全局唯一，同为新 AS-BOT 的
        as_bot_key）。AS-BOT 的提示词/简介/persona/i18n 里提及原角色名处同步替换为新名称，
        替换次数随返回值显式回报（prompt_renamed），不静默改写（no-silent-degradation）。

        单事务：任一步失败整体回滚（显式 _txn，见 _txn 注释），不留半截角色；
        来源角色不存在/重名均 fail-loud 抛 ValueError。
        """
        name = (name or "").strip()
        display_name = (display_name or "").strip() or name
        if not name:
            raise ValueError("角色标识不能为空")
        if len(name) > 64:
            raise ValueError("角色标识过长（最多 64 个字符）")
        if len(display_name) > 128:
            raise ValueError("显示名称过长（最多 128 个字符）")
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur, _txn(cur):
                cur.execute("SELECT * FROM adh_roles WHERE id = %s", (source_role_id,))
                source = cur.fetchone()
                if not source:
                    raise ValueError("来源角色不存在")
                cur.execute("SELECT id FROM adh_roles WHERE name = %s", (name,))
                if cur.fetchone():
                    raise ValueError(f"角色标识「{name}」已存在，请换一个")
                new_role_id = _gen_id()
                cur.execute(
                    "INSERT INTO adh_roles (id, name, display_name, description, is_system, is_active) "
                    "VALUES (%s, %s, %s, %s, 0, 1)",
                    (new_role_id, name, display_name, description or ""))
                copied = {table: self._copy_role_rows(cur, table, cols, source_role_id, new_role_id)
                          for table, cols in _ROLE_COPY_TABLES.items()}
                as_bot = self._duplicate_as_bot(cur, source, source_role_id, new_role_id, name, display_name)
            logger.info("Duplicated role %s -> '%s' (id=%s, perms=%s)",
                        source_role_id, name, new_role_id, copied.get("adh_role_perms"))
            _invalidate_as_bot_config(f"duplicate_role src={source_role_id} new={new_role_id}")
            return {
                "id": new_role_id, "name": name, "display_name": display_name,
                "copied": {
                    "permissions": copied["adh_role_perms"],
                    "datasources": copied["adh_role_datasource_access"],
                    "datasets": copied["adh_role_dataset_access"],
                    "dashboards": copied["adh_role_dashboard_access"],
                    "rls_policies": copied["adh_role_rls_policies"],
                    "attributes": copied["adh_role_attributes"],
                    "tables": copied["adh_role_table_access"],
                    "columns": copied["adh_role_column_access"],
                },
                "as_bot_copied": as_bot["copied"],
                "prompt_renamed": as_bot["renamed"],
            }
        finally:
            conn.close()

    @staticmethod
    def _copy_role_rows(cur, table: str, cols: tuple, source_role_id: int, new_role_id: int) -> int:
        """把来源角色在一张授权表里的行整份复制给新角色。

        id 是否显式生成看列定义：**自增列不传 id**（库生成）——各表 id 列类型不一
        （如 adh_role_dataset_access.id 是 INT），强塞 _gen_id() 雪花值必报 1264
        Out of range（历史缺陷：“报错但数据存下来了”的报错源）；非自增列才用
        _gen_id() 显式生成新 id，不沿用旧 id。
        """
        cur.execute(f"SHOW COLUMNS FROM {table} WHERE Field = 'id'")
        id_row = cur.fetchone()
        id_auto = bool(id_row and "auto_increment" in str(id_row.get("Extra") or ""))
        cur.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE role_id = %s", (source_role_id,))
        rows = cur.fetchall() or []
        base = _gen_id()
        for i, row in enumerate(rows):
            vals = [row[c] for c in cols]
            if id_auto:
                cur.execute(f"INSERT INTO {table} (role_id, {', '.join(cols)}) "
                            f"VALUES (%s, {', '.join(['%s'] * len(cols))})",
                            [new_role_id] + vals)
            else:
                cur.execute(f"INSERT INTO {table} (id, role_id, {', '.join(cols)}) "
                            f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))})",
                            [base + i, new_role_id] + vals)
        return len(rows)

    @staticmethod
    def _duplicate_as_bot(cur, source: dict, source_role_id: int, new_role_id: int,
                          name: str, display_name: str) -> dict:
        """复制角色的一对一 AS-BOT：身份（as_bot_key/name/display_name）随新角色名，配置原样搬。

        - 提示词/简介/persona/i18n 中提及原角色名处替换为新名称（长 token 先换，避免短名把
          长名截断），替换次数显式回报给调用方，不静默改写；工具/skills/知识库/模型等纯配置不动。
        - 新 AS-BOT 的 is_builtin 恒为 0（内置标记只属于系统角色的内置 bot）。
        - 来源角色没有 AS-BOT 时回 copied=False，不静默补默认 AS-BOT（那会让“复制”变“新建”，
          配置悄悄丢失——no-silent-degradation）。
        """
        cur.execute("SELECT * FROM adh_as_bots WHERE role_id = %s", (source_role_id,))
        src_bot = cur.fetchone()
        if not src_bot:
            return {"copied": False, "renamed": 0}
        # 旧名 → 新名 替换对（同旧名只取首个映射；短于 2 字符的不换，避免单字乱替换）
        pairs: list = []
        for old, new in (
            (source.get("display_name") or "", display_name),
            (source.get("name") or "", name),
            (src_bot.get("display_name") or "", display_name),
            (src_bot.get("name") or "", name),
        ):
            old = str(old or "").strip()
            new = str(new or "").strip()
            if len(old) >= 2 and old != new and all(old != o for o, _ in pairs):
                pairs.append((old, new))
        pairs.sort(key=lambda p: -len(p[0]))
        # 单趟正则替换（长 token 先于短 token 匹配）：逐趟 str.replace 会把上一趟的
        # 新文本再当旧名换掉（如 sales→sales2 后，sales_cn→sales_cn2 里的 "sales"
        # 被二次命中变成 sales2_cn2），一次扫描只换一次才稳。
        rename_map = dict(pairs)
        pattern = re.compile("|".join(re.escape(old) for old, _ in pairs)) if pairs else None

        renamed = 0

        def _rename(value):
            if pattern is None:
                return value
            if isinstance(value, str):
                def _sub(m):
                    nonlocal renamed
                    renamed += 1
                    return rename_map[m.group(0)]
                return pattern.sub(_sub, value)
            if isinstance(value, dict):
                return {k: _rename(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_rename(v) for v in value]
            return value

        cur.execute(
            """INSERT INTO adh_as_bots
               (as_bot_key, name, display_name, description, category, system_prompt,
                persona, tools, mcp_server_ids, knowledge_base_ids, skills, models,
                execution_layer_id, chart_enabled, is_active, is_builtin,
                role_id, workspace_id, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (name, name, display_name,
             _rename(str(src_bot.get("description") or "")),
             src_bot.get("category") or "custom",
             _rename(str(src_bot.get("system_prompt") or "")),
             json.dumps(_rename(_as_json(src_bot.get("persona"), {})), ensure_ascii=False),
             json.dumps(_as_json(src_bot.get("tools"), {}), ensure_ascii=False),
             json.dumps(_as_json(src_bot.get("mcp_server_ids"), []), ensure_ascii=False),
             json.dumps(_as_json(src_bot.get("knowledge_base_ids"), []), ensure_ascii=False),
             json.dumps(_as_json(src_bot.get("skills"), []), ensure_ascii=False),
             json.dumps(_as_json(src_bot.get("models"), []), ensure_ascii=False),
             src_bot.get("execution_layer_id"),
             1 if src_bot.get("chart_enabled") else 0,
             int(src_bot.get("is_active") or 0),
             0,  # 新角色的 AS-BOT 恒为自定义（is_builtin 只属于系统角色的内置 bot）
             new_role_id,
             int(src_bot.get("workspace_id") or 0),
             src_bot.get("created_by") or "admin")
        )
        return {"copied": True, "renamed": renamed}

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
                changed = cur.rowcount > 0
                if changed:
                    # is_active/display_name 变更会改变角色有效性与 AS-BOT 候选集
                    _invalidate_as_bot_config(f"update_role id={role_id}")
                return changed
        finally:
            conn.close()

    def delete_role(self, role_id: int) -> bool:
        """Delete a role (only if not system role) and its associated AS-BOT."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur, _txn(cur):
                cur.execute("SELECT is_system, name FROM adh_roles WHERE id = %s", (role_id,))
                role = cur.fetchone()
                if not role or role.get("is_system"):
                    return False
                # 联动删除关联 AS-BOT（角色-AS-BOT 一对一绑定）
                cur.execute("DELETE FROM adh_as_bots WHERE role_id = %s", (role_id,))
                logger.info(f"Deleted AS-BOT for role '{role.get('name')}' (role_id={role_id})")
                # 全部角色级授权一并清（与 _ROLE_COPY_TABLES 同一份清单）：历史缺陷是
                # 只删 6 张，adh_role_perms/rls_policies/table/column 会留孤儿行。
                for table in _ROLE_COPY_TABLES:
                    cur.execute(f"DELETE FROM {table} WHERE role_id = %s", (role_id,))
                cur.execute("DELETE FROM adh_user_roles WHERE role_id = %s", (role_id,))
                cur.execute("DELETE FROM adh_roles WHERE id = %s", (role_id,))
            _invalidate_as_bot_config(f"delete_role id={role_id}")
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
            with conn.cursor() as cur, _txn(cur):
                cur.execute(
                    "DELETE FROM adh_role_attributes WHERE role_id = %s AND workspace_id = %s",
                    (role_id, workspace_id)
                )
                for key, value in attrs.items():
                    cur.execute(
                        "INSERT INTO adh_role_attributes (id, role_id, workspace_id, attr_key, attr_value) VALUES (%s, %s, %s, %s, %s)",
                        (_gen_id(), role_id, workspace_id, key, str(value))
                    )
                _invalidate_as_bot_config(f"set_role_attributes role={role_id} ws={workspace_id}")
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
        # 角色绑定变更 → 角色 id/权限码缓存（轮次档 + live 档）必须失效。本函数是唯一写入
        # 口并在调用方事务内，此处只能先于 commit 失效：若并发回源先读到旧值，
        # 由 live 短 TTL 与工具级裁决的 digest 比对兜底，不致越权。
        _invalidate_as_bot_config(f"user_role_binding user={user_id} role={role_id}")
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
            with conn.cursor() as cur, _txn(cur):
                self._apply_user_role(cur, user_id, role_name)
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
            with conn.cursor() as cur, _txn(cur):
                cur.execute("SELECT name FROM adh_roles WHERE id = %s", (role_id,))
                row = cur.fetchone()
                if not row:
                    logger.warning("[role_service] assign_user_role 角色不存在: role_id=%s", role_id)
                    return False
                self._apply_user_role(cur, user_id, str(row["name"]))
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
            with conn.cursor() as cur, _txn(cur):
                cur.execute(
                    "DELETE FROM adh_user_roles WHERE user_id = %s AND role_id = %s",
                    (user_id, role_id)
                )
                removed = cur.rowcount > 0
                if removed:
                    self._apply_user_role(cur, user_id, "viewer")
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

    # ── 工作空间私有化(个人空间): 属主校验与双配额 ─────────────

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

    @staticmethod
    def _effective_quota(registered_max, registered_disk) -> dict:
        """生效配额的唯一解析式：登记行是权威，无行取系统缺省（不是“不限制”）。

        `disk_quota_bytes=0` 只可能来自**显式登记行**（管理员给该用户不设限的逃生口）；
        缺省值不会算出 0，二者靠 `is_default` 区分。数字来源仍是 `default_workspace_quota()`。
        """
        if registered_max is not None or registered_disk is not None:
            return {"max_workspaces": int(registered_max or 0),
                    "disk_quota_bytes": int(registered_disk or 0),
                    "is_default": False}
        out = default_workspace_quota()
        out["is_default"] = True
        return out

    def get_user_workspace_quota(self, user_id: int) -> dict:
        """用户生效配额（**唯一裁决点**）：登记行为权威，无行时取系统缺省（不是“不限制”）。

        为什么只能有这一个口径：磁盘配额有两个门（建空间数、新建会话磁盘用量），历史上
        它们各读各的 —— 无行时一边按缺省拦、一边把空读成 0=不限，结果是管理员页显示
        “每空间 5GB”而实际从不拦（配了没用）。管理员页、用量面板与拦截门均走本函数，
        解析式统一在 `_effective_quota`。

        返回 `{max_workspaces, disk_quota_bytes, is_default}`。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT max_workspaces, disk_quota_bytes FROM adh_user_workspace_quota WHERE user_id = %s",
                    (user_id,))
                row = cur.fetchone() or {}
        finally:
            conn.close()
        return self._effective_quota(row.get("max_workspaces"), row.get("disk_quota_bytes"))

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

    # 统管清单的服务端每页硬上限（前端档位候选 20/50/100 不得越过这里）
    _QUOTA_PAGE_SIZE_MAX = 100
    # 排序唯一口径：u.id 是唯一主键，天然 tiebreaker，相邻页不重行/漏行
    _QUOTA_ORDER_BY = " ORDER BY u.id"

    @staticmethod
    def _quota_filters(q: str) -> tuple:
        """统管清单的搜索谓词（COUNT 与当前页共用一份，不得各写一套导致口径漂移）。"""
        keyword = (q or "").strip()
        if not keyword:
            return "", []
        return " WHERE u.username LIKE %s", [f"%{keyword}%"]

    def list_workspace_quotas_page(self, page: int = 1, page_size: int = 20,
                                   q: str = "") -> dict:
        """管理员统管: 按用户的**服务端真分页**清单。

        行总数由 COUNT 得到、偏移量由页号算出（调用方不递 offset）、搜索下推 SQL。
        每页只给配额与「该用户各空间的 id」，磁盘用量由 API 层按页内用户算
        （递归扫盘不落在 SQL 服务层，也不得回到"全库每个空间都扫一遍"）。
        返回契约 `{items,total,page,page_size}` 与字模库/内容清单同源。
        """
        page = max(1, int(page or 1))
        page_size = min(self._QUOTA_PAGE_SIZE_MAX, max(1, int(page_size or 20)))
        where, params = self._quota_filters(q)
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM adh_users u" + where, params)
                total = int((cur.fetchone() or {}).get("cnt", 0))
                cur.execute(
                    """SELECT u.id AS user_id, u.username,
                              q.max_workspaces AS registered_max_workspaces,
                              q.disk_quota_bytes AS registered_disk_quota_bytes
                       FROM adh_users u
                       LEFT JOIN adh_user_workspace_quota q ON q.user_id = u.id"""
                    + where + self._QUOTA_ORDER_BY + " LIMIT %s OFFSET %s",
                    list(params) + [page_size, (page - 1) * page_size])
                rows = cur.fetchall() or []
        finally:
            conn.close()
        items = []
        for row in rows:
            effective = self._effective_quota(row.get("registered_max_workspaces"),
                                              row.get("registered_disk_quota_bytes"))
            items.append({"user_id": row["user_id"], "username": row["username"],
                          "max_workspaces": effective["max_workspaces"],
                          "disk_quota_bytes": effective["disk_quota_bytes"],
                          # 前端字段名与 /disk-status 同口径（管理员页要能分辨"系统缺省"与"有人显式配过"）
                          "quota_is_default": effective["is_default"]})
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def list_user_workspace_ids(self, user_ids: list) -> dict:
        """按页内用户一次取回 `{user_id: [workspace_id, ...]}`（清单磁盘用量的输入）。

        一次 IN 查询，不得逐用户各查一遍（N+1）。空入参不发 SQL。
        """
        ids = [int(u) for u in (user_ids or []) if u]
        if not ids:
            return {}
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT owner_id, id FROM adh_workspaces WHERE owner_id IN ("
                    + ", ".join(["%s"] * len(ids)) + ")", ids)
                rows = cur.fetchall() or []
        finally:
            conn.close()
        grouped: dict = {}
        for row in rows:
            grouped.setdefault(int(row["owner_id"]), []).append(int(row["id"]))
        return grouped

    def get_user_workspace_quota_detail(self, user_id: int):
        """统管详情: 单个用户的生效配额 + 空间清单（磁盘用量由 API 层补充）。

        用户不存在返回 None —— 调用方给 404，不得回空清单冒充"该用户没有空间"。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT username FROM adh_users WHERE id = %s", (user_id,))
                user = cur.fetchone()
                if not user:
                    return None
                cur.execute(
                    "SELECT id, name, is_default FROM adh_workspaces "
                    "WHERE owner_id = %s ORDER BY is_default DESC, id",
                    (user_id,))
                workspaces = cur.fetchall() or []
        finally:
            conn.close()
        effective = self.get_user_workspace_quota(user_id)
        return {"user_id": int(user_id), "username": user["username"],
                "max_workspaces": effective["max_workspaces"],
                "disk_quota_bytes": effective["disk_quota_bytes"],
                "quota_is_default": effective["is_default"],
                "workspaces": workspaces}

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
            with conn.cursor() as cur, _txn(cur):
                cur.execute("DELETE FROM adh_role_datasource_access WHERE role_id = %s", (role_id,))
                for ds_id in datasource_ids:
                    cur.execute(
                        "INSERT INTO adh_role_datasource_access (id, role_id, datasource_id) VALUES (%s, %s, %s)",
                        (_gen_id(), role_id, ds_id)
                    )
                # 数据源授权是「可用数据源 / 可查站点」的唯一裁决源，变更后必须立刻
                # 失效两个派生缓存，否则聊天端会在 TTL 窗口内看到旧范围。
                _invalidate_datasource_derived_caches()
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

    def get_user_role_ai_perms(self, user_id: int, workspace_id: int = 0,
                               use_cache: bool = False, live: bool = False) -> dict:
        """用户经角色持有的权限码及其 AI 可调用级别。

        Returns ``{perm_code: {"ai_access", "label", "ai_note"}}`` —— 供
        ``tool_policy.compile_policy`` 做功能能力继承判定（AS-BOT 自动继承，
        AS-BOT 仅做减法）。

        口径与其它数据权限路径一致：走 ``adh_user_roles``（唯一真值源，无 workspace
        维度——工作空间角色已下线），不走 JWT 里的 ``user_role`` 列缓存。

        ``ai_access`` 取 ``adh_perm_registry`` 的配置值（管理员可调），
        但它不是最终裁决：涉密硬上界由 ``perm_link`` 再收窄一次，
        配置写成 write 也抬不上去。

        use_cache: **仅缓存读路径**（``tool_policy.resolve_policy`` 预检/领取/工具裁决一路）可传，
        同一角色多用户逐轮重复读同一行配置属纯浪费。缓存写入口必须同批失效：
        用户角色绑定、角色权限码、权限点 ai_access、角色本身（改名/删除，见 rbac_service），
        均走 `_invalidate_as_bot_config`。

        live: 与 use_cache=True 同时生效——走**工具级裁决档**（短 TTL + `live:` key 前缀），
        撤销授权的检测窗口 = 该档 TTL（而不是轮次档 60s）。不传则为轮次起点档。
        两档语义与不变量见 `backend/common/as_bot_config_cache.py` 模块注释。
        """
        from backend.common import as_bot_config_cache
        if not use_cache:
            return self._query_user_role_ai_perms(user_id)
        # workspace_id 不属查询条件（无工作空间维度），不进 key，避免同一事实多份缓存
        return as_bot_config_cache.get_or_set(
            f"{as_bot_config_cache.KEY_ROLE_PERMS}{user_id}",
            lambda: self._query_user_role_ai_perms(user_id), live=live)

    @staticmethod
    def _query_user_role_ai_perms(user_id: int) -> dict:
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
        """用户经角色持有的权限码清单（与 get_user_role_ai_perms 同口径，恒实时）。"""
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
            with conn.cursor() as cur, _txn(cur):
                cur.execute("DELETE FROM adh_role_dashboard_access WHERE role_id = %s", (role_id,))
                for dash_id in set(dashboard_ids or []):
                    cur.execute(
                        "INSERT IGNORE INTO adh_role_dashboard_access (role_id, dashboard_id) VALUES (%s, %s)",
                        (role_id, int(dash_id))
                    )
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

    # ── Dataset Access (数据集可见性按角色授权) ────────────────────────

    def get_role_datasets(self, role_id: int) -> list:
        """Get dataset IDs visible to a role, with display info."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT rda.dataset_id, d.name
                       FROM adh_role_dataset_access rda
                       LEFT JOIN adh_datasets d ON d.id = rda.dataset_id
                       WHERE rda.role_id = %s ORDER BY d.name""",
                    (role_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    def set_role_datasets(self, role_id: int, dataset_ids: list) -> bool:
        """Replace all dataset visibility grants for a role (full replace, idempotent)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur, _txn(cur):
                cur.execute("DELETE FROM adh_role_dataset_access WHERE role_id = %s", (role_id,))
                for ds_id in set(dataset_ids or []):
                    cur.execute(
                        "INSERT IGNORE INTO adh_role_dataset_access (role_id, dataset_id) VALUES (%s, %s)",
                        (role_id, int(ds_id))
                    )
                return True
        finally:
            conn.close()

    def get_user_allowed_datasets(self, user_id: int) -> list:
        """Get dataset IDs a user can see via their roles (可见性唯一裁决).

        **fail-closed**: 空授权返回空列表 = 一律不可见, 不解释为全量。
        admin 在调用方 bypass。
        """
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT rda.dataset_id
                       FROM adh_user_roles ur
                       JOIN adh_role_dataset_access rda ON rda.role_id = ur.role_id
                       WHERE ur.user_id = %s""",
                    (user_id,)
                )
                return [r["dataset_id"] for r in cur.fetchall()]
        finally:
            conn.close()

    def list_dataset_catalog(self) -> list:
        """All active datasets (admin 配置选择器用)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT d.id, d.name FROM adh_datasets d
                       WHERE d.status = 'active' ORDER BY d.name"""
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
            with conn.cursor() as cur, _txn(cur):
                cur.execute("DELETE FROM adh_role_table_access WHERE role_id = %s", (role_id,))
                for t in tables:
                    cur.execute(
                        """INSERT INTO adh_role_table_access
                           (id, role_id, datasource_id, table_name, access_type)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (_gen_id(), role_id, t.get("datasource_id", 0),
                         t["table_name"], t.get("access_type", "read"))
                    )
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
            with conn.cursor() as cur, _txn(cur):
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
