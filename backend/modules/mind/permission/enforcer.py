"""Permission Enforcement Layer — unified data access control.

Integrates role_service (RBAC) and rls_service (row/column security)
into the query execution pipeline WITHOUT external dependencies (no Ranger/LDAP/Kerberos).

Permission model:
  1. Datasource access — role_service.get_user_allowed_datasources()
  2. Table access — role_service.get_user_allowed_tables()
  3. Column restrictions — role_service.get_user_column_restrictions()
  4. Row-level security — rls_service.get_effective_policies()
  5. Sensitive field marks — datagov adh_sensitive_fields（对所有用户生效，含管理员）
"""

import re
import hashlib
import logging
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional, Callable

import pandas as pd

logger = logging.getLogger(__name__)

# ── check_access TTL 缓存 (30s) ─────────────────────────────────────
# 同一用户/数据源/表的权限在短时间内不会变化，缓存避免重复 DB 查询。
# 主要受益方: check_sql 预检、多表 SQL 的逐表 check_access 调用。
_ACCESS_CACHE_TTL = 30  # seconds
_access_cache: dict[str, tuple[float, "PermissionResult"]] = {}


def invalidate_access_cache():
    """清空权限缓存（权限策略变更后调用）。"""
    _access_cache.clear()

# datagov adh_sensitive_fields.mask_type → enforcer 脱敏方式
_SENSITIVE_MASK_MAP = {
    "full": "null",      # 完全遮蔽 → 置空
    "partial": "partial",
    "hash": "hash",
    # "none" → 不脱敏
}


@dataclass
class PermissionResult:
    """Result of permission check."""
    allowed: bool = True
    reason: str = ""
    row_filter: str = ""
    hidden_columns: list = field(default_factory=list)
    masked_columns: dict = field(default_factory=dict)  # {col: mask_type}
    policies_applied: list = field(default_factory=list)


def _ds_deny_reason(datasource_id) -> str:
    """数据源拒绝文案：显示 name 不裸显 id（ui-resource-display）。

    datasource_id 缺省/0 的真实原因是“未选择目标数据源”（而非无权），
    给可操作提示；查不到 name 时给可读标注 + 原 id 供排查。
    """
    if not datasource_id:
        return "未选择目标数据源，已拒绝执行（请指定查询数据源后重试）"
    name = ""
    try:
        from backend.common.db.datasource_db import get_datasource_by_id
        name = (get_datasource_by_id(datasource_id) or {}).get("name") or ""
    except Exception:
        pass  # 名称解析失败不影响拒绝决定本身，仅影响文案丰富度
    if name:
        return f"无权访问数据源「{name}」"
    return f"无权访问数据源（已删除或不可见，原 ID: {datasource_id}）"


class PermissionEnforcer:
    """Unified permission enforcement for query execution.

    Uses role_service and rls_service for policy evaluation.
    No external dependencies — all policies stored in MySQL.
    """

    def check_access(
        self,
        user_id: int,
        workspace_id: int,
        datasource_id: int,
        table_name: str = "",
        columns: list = None,
        sensitive_only: bool = False,
    ) -> PermissionResult:
        """Check user access to a datasource/table/columns.

        Args:
            user_id: User ID from JWT token.
            workspace_id: Workspace context.
            datasource_id: Target datasource.
            table_name: Target table (empty = datasource-level check only).
            columns: Columns being accessed (empty = all).
            sensitive_only: 仅应用治理敏感基线(block/mask), 跳过 RBAC/RLS。
                用于无身份(user_id=0)的系统/内部调用: 行级/越权控制不适用,
                但敏感列屏蔽基线必须生效(护城河不可绕过)。

        Returns:
            PermissionResult with allowed, row_filter, hidden/masked columns.
        """
        # TTL 缓存: 同一 (user, ws, ds, table, sensitive_only) 30s 内不重复查 DB
        cache_key = f"{user_id}:{workspace_id}:{datasource_id}:{table_name}:{sensitive_only}"
        now = time.time()
        if cache_key in _access_cache:
            cached_at, cached_result = _access_cache[cache_key]
            if now - cached_at < _ACCESS_CACHE_TTL:
                return deepcopy(cached_result)

        result = self._check_access_impl(
            user_id, workspace_id, datasource_id, table_name, columns, sensitive_only
        )
        _access_cache[cache_key] = (now, result)
        return deepcopy(result)

    def _check_access_impl(
        self,
        user_id: int,
        workspace_id: int,
        datasource_id: int,
        table_name: str = "",
        columns: list = None,
        sensitive_only: bool = False,
    ) -> PermissionResult:
        """check_access 实际实现（不含缓存逻辑）。"""
        from backend.modules.auth.services.role_service import role_service
        from backend.modules.auth.services.rls_service import rls_service

        result = PermissionResult()

        # 敏感字段标记（datagov）— 治理基线，对所有用户生效（含管理员）
        #   mask(full/partial/hash) -> 脱敏返回; block -> 不能查询出来(并入 hidden_columns)
        sensitive_masked = {}
        sensitive_blocked: list = []
        if table_name:
            sensitive_masked, sensitive_blocked = self._get_sensitive_policies(
                workspace_id, datasource_id, table_name
            )
            if sensitive_masked:
                result.masked_columns.update(sensitive_masked)
                result.policies_applied.append(
                    f"sensitive_fields:{','.join(sorted(sensitive_masked))}"
                )
            for col in sensitive_blocked:
                if col not in result.hidden_columns:
                    result.hidden_columns.append(col)
            if sensitive_blocked:
                result.policies_applied.append(
                    f"sensitive_block:{','.join(sorted(sensitive_blocked))}"
                )

        # 无身份调用: 只套治理敏感基线(已并入), 不做 RBAC/RLS 行级过滤
        if sensitive_only:
            return result

        # RLS/RBAC 完全按用户角色权限配置生效，不做 admin 旁路
        # 敏感字段脱敏已在上方并入，对所有角色（含 admin）强制生效

        # Step 1: Check datasource access（fail-closed：空授权=无权，不得解释为全量）
        # waker-datasource-domain §1：admin 同样纯角色裁决不 bypass；与 resource_guard 同口径。
        # 历史缺陷：曾写 `if allowed_ds and datasource_id not in allowed_ds`——空授权时
        # 跳过检查（fail-open），无任何角色授权的用户数据源步直接放行。
        allowed_ds = role_service.get_user_allowed_datasources(user_id, workspace_id)
        if datasource_id and datasource_id not in allowed_ds:
            result.allowed = False
            result.reason = _ds_deny_reason(datasource_id)
            return result

        # Step 2: Check table access (if table specified)
        if table_name:
            allowed_tables = role_service.get_user_allowed_tables(
                user_id, datasource_id, workspace_id
            )
            if allowed_tables and table_name not in allowed_tables:
                result.allowed = False
                result.reason = f"无权访问表 {table_name}"
                return result

            # Step 3: Get column restrictions (合并, 不得覆盖敏感基线已并入的 hidden/masked)
            col_restrictions = role_service.get_user_column_restrictions(
                user_id, datasource_id, table_name, workspace_id
            )
            for col in col_restrictions.get("hidden_columns", []):
                if col not in result.hidden_columns:
                    result.hidden_columns.append(col)
            # 角色策略不得弱化敏感基线（不覆盖敏感字段的脱敏方式）
            for col, mask in col_restrictions.get("masked_columns", {}).items():
                if col not in sensitive_masked:
                    result.masked_columns[col] = mask

            # Step 4: Get RLS row filter
            rls_policies = rls_service.get_effective_policies(
                user_id, workspace_id, datasource_id, table_name
            )
            result.row_filter = rls_policies.get("row_filter", "")
            for pid in rls_policies.get("policies_applied", []):
                if pid not in result.policies_applied:
                    result.policies_applied.append(pid)

            # Merge RLS column policies (RLS overrides role-level, but never weakens sensitive baseline)
            rls_hidden = rls_policies.get("hidden_columns", [])
            rls_masked = rls_policies.get("masked_columns", {})
            for col in rls_hidden:
                if col not in result.hidden_columns:
                    result.hidden_columns.append(col)
            for col, mask in rls_masked.items():
                if col not in sensitive_masked:
                    result.masked_columns[col] = mask

        return result

    def enforce_sql(
        self,
        sql: str,
        user_id: int,
        workspace_id: int,
        datasource_id: int,
        tables: list = None,
    ) -> tuple[str, PermissionResult]:
        """Enforce permissions on a SQL query.

        Modifies SQL to inject row-level filters and returns post-execution
        column restrictions.

        Args:
            sql: Original SQL query.
            user_id: User ID.
            workspace_id: Workspace context.
            datasource_id: Target datasource.
            tables: Table names extracted from SQL (auto-detected if None).

        Returns:
            (modified_sql, PermissionResult)
        """
        from backend.semantics.sql_guard import extract_tables, inject_filters
        # 总是解析真实查询，调用方传入的表清单不能省略 JOIN 或 CTE 内的表。
        tables = extract_tables(sql)

        # 无身份(user_id 缺省/0): 仅套治理敏感基线, 不做 RBAC/RLS
        only_sensitive = not user_id

        if not tables:
            # No tables found, check datasource-level only
            result = self.check_access(
                user_id, workspace_id, datasource_id, sensitive_only=only_sensitive)
            if not result.allowed:
                raise PermissionError(result.reason)
            return sql, result

        # 批量校验: #2(datasource access) 和 #3(table access) 提到循环外只查一次
        # 避免 N 张表重复查 2 次 DB（原来 3 表 = 15~21 次查询 → 现在 8~11 次）
        combined_result = PermissionResult()
        modified_sql = sql
        table_filters = {}

        if not only_sensitive:
            from backend.modules.auth.services.role_service import role_service
            # 数据源级访问 — 只查一次（fail-closed：空授权=无权，与 check_access 同口径）
            allowed_ds = role_service.get_user_allowed_datasources(user_id, workspace_id)
            if datasource_id and datasource_id not in allowed_ds:
                raise PermissionError(_ds_deny_reason(datasource_id))
            # 表级访问 — 按表所属源缓存（跨源 SQL 每个源只查一次）
            allowed_tables_cache: dict = {}

        for table in tables:
            policy_table, table_ds_id = self._resolve_table_ref(table, datasource_id)

            if not only_sensitive:
                # 跨源表所属源同样必须逐个在授权集内（限定名不得绕过数据源授权）。
                # fail-closed 同口径：空授权=无权；历史缺陷 `if allowed_ds and` 会在
                # 空授权时跳过本步（跨源 SQL 的旁路）。无源可解析的裸表跟随主源（已校验）。
                if table_ds_id and table_ds_id not in allowed_ds:
                    raise PermissionError(_ds_deny_reason(table_ds_id))
                if table_ds_id not in allowed_tables_cache:
                    allowed_tables_cache[table_ds_id] = role_service.get_user_allowed_tables(
                        user_id, table_ds_id, workspace_id)
                allowed_tables = allowed_tables_cache[table_ds_id]
                if allowed_tables and policy_table not in allowed_tables:
                    raise PermissionError(f"无权访问表 {table}")

            # 仅做表级专属查询: #1 敏感字段 + #4 列限制 + #5 RLS
            # 按表所属源加载策略（跨源不串味）
            result = self._check_table_permissions(
                user_id, workspace_id, table_ds_id, policy_table,
                sensitive_only=only_sensitive,
                skip_datasource_check=True,
                skip_table_access_check=True,
            )

            # Inject row filter
            if result.row_filter:
                table_filters[table] = result.row_filter

            # Merge column restrictions
            for col in result.hidden_columns:
                if col not in combined_result.hidden_columns:
                    combined_result.hidden_columns.append(col)
            for col, mask in result.masked_columns.items():
                previous = combined_result.masked_columns.get(col)
                combined_result.masked_columns[col] = mask if previous in (None, mask) else "null"
            combined_result.policies_applied.extend(result.policies_applied)

        self._protect_projection(sql, combined_result)
        modified_sql, _ = inject_filters(sql, table_filters)
        combined_result.row_filter = modified_sql != sql
        return modified_sql, combined_result

    def _check_table_permissions(
        self,
        user_id: int,
        workspace_id: int,
        datasource_id: int,
        table_name: str,
        sensitive_only: bool = False,
        skip_datasource_check: bool = False,
        skip_table_access_check: bool = False,
    ) -> PermissionResult:
        """单表权限校验（表级专属查询: 敏感字段 + 列限制 + RLS）。

        与 check_access 的区别: 调用方已提前校验过数据源/表级访问,
        这里跳过重复查询, 只做表级专属的 3 项检查。
        """
        from backend.modules.auth.services.role_service import role_service
        from backend.modules.auth.services.rls_service import rls_service

        result = PermissionResult()

        # #1 敏感字段标记
        sensitive_masked, sensitive_blocked = self._get_sensitive_policies(
            workspace_id, datasource_id, table_name
        )
        if sensitive_masked:
            result.masked_columns.update(sensitive_masked)
            result.policies_applied.append(
                f"sensitive_fields:{','.join(sorted(sensitive_masked))}"
            )
        for col in sensitive_blocked:
            if col not in result.hidden_columns:
                result.hidden_columns.append(col)
        if sensitive_blocked:
            result.policies_applied.append(
                f"sensitive_block:{','.join(sorted(sensitive_blocked))}"
            )

        if sensitive_only:
            return result

        # #4 列限制
        col_restrictions = role_service.get_user_column_restrictions(
            user_id, datasource_id, table_name, workspace_id
        )
        for col in col_restrictions.get("hidden_columns", []):
            if col not in result.hidden_columns:
                result.hidden_columns.append(col)
        for col, mask in col_restrictions.get("masked_columns", {}).items():
            if col not in sensitive_masked:
                result.masked_columns[col] = mask

        # #5 RLS 行级 + 列级
        rls_policies = rls_service.get_effective_policies(
            user_id, workspace_id, datasource_id, table_name
        )
        result.row_filter = rls_policies.get("row_filter", "")
        for pid in rls_policies.get("policies_applied", []):
            if pid not in result.policies_applied:
                result.policies_applied.append(pid)
        for col in rls_policies.get("hidden_columns", []):
            if col not in result.hidden_columns:
                result.hidden_columns.append(col)
        for col, mask in rls_policies.get("masked_columns", {}).items():
            if col not in sensitive_masked:
                result.masked_columns[col] = mask

        return result

    def apply_post_processing(
        self,
        df: pd.DataFrame,
        result: PermissionResult,
    ) -> pd.DataFrame:
        """Apply column hiding and masking after query execution.

        Args:
            df: Query result DataFrame.
            result: PermissionResult from enforce_sql().

        Returns:
            Processed DataFrame with hidden columns removed and masked columns obfuscated.
        """
        if df is None or df.empty:
            return df

        hidden = {str(c).lower() for c in result.hidden_columns}
        masks = {str(c).lower(): m for c, m in result.masked_columns.items()}
        df = df.iloc[:, [i for i, c in enumerate(df.columns) if str(c).lower() not in hidden]].copy()
        for index, column in enumerate(df.columns):
            mask_type = masks.get(str(column).lower())
            if mask_type:
                values = df.iloc[:, index].map(lambda value: self._mask_value(value, mask_type))
                df.isetitem(index, values)
        return df

    # ── Internal helpers ───────────────────────────────────────────

    def _get_sensitive_policies(
        self, workspace_id: int, datasource_id: int, table_name: str
    ) -> tuple[dict, list]:
        """从 datagov 敏感字段标记加载 (masks, blocks)。

        取"全局(datasource_id=0 / table_name='') ∪ 指定数据源/表"并集:
        table_name='' 的规则视为跨所有表按列名生效。mask_type='block' 归入
        blocks(不能查询出来), 其余按 _SENSITIVE_MASK_MAP 归入 masks。失败不阻断。
        """
        try:
            from backend.common.db import execute_query
            rows = execute_query(
                """SELECT column_name, mask_type FROM adh_sensitive_fields
                   WHERE datasource_id IN (%s, 0) AND table_name IN (%s, '')
                     AND workspace_id IN (%s, 0)""",
                (datasource_id, table_name, workspace_id),
            )
            masks: dict = {}
            blocks: list = []
            for row in rows:
                col = row.get("column_name")
                if not col:
                    continue
                mt = (row.get("mask_type") or "").lower()
                if mt == "block":
                    if col not in blocks:
                        blocks.append(col)
                else:
                    mask = _SENSITIVE_MASK_MAP.get(mt)
                    if mask:
                        masks[col] = mask
            return masks, blocks
        except Exception as e:
            logger.warning("敏感策略加载失败，拒绝执行: %s", e)
            raise PermissionError("敏感数据策略暂不可用") from e

    def _get_sensitive_masks(
        self, workspace_id: int, datasource_id: int, table_name: str
    ) -> dict:
        """兼容旧签名: 仅返回脱敏映射(不含 block)。"""
        masks, _ = self._get_sensitive_policies(workspace_id, datasource_id, table_name)
        return masks

    def get_blocked_columns(
        self, workspace_id: int, datasource_id: int, table_name: str = ""
    ) -> list:
        """返回该(数据源/表)生效的合规屏蔽列名(全局 ∪ 指定), 供语义层精确拒绝。"""
        _, blocks = self._get_sensitive_policies(workspace_id, datasource_id, table_name)
        return blocks

    @staticmethod
    def _references_blocked(sql: str, blocked: list) -> list:
        """保守检测 SQL 是否显式点名了被屏蔽列(词边界, 大小写不敏感)。

        SELECT * 不含具体列名 -> 不算点名(由执行层后置丢弃兜底); 命中的列名返回。
        """
        hits: list = []
        for col in blocked or []:
            if col and re.search(rf"\b{re.escape(str(col))}\b", sql or "", re.IGNORECASE):
                hits.append(col)
        return hits

    def _extract_tables(self, sql: str) -> list:
        from backend.semantics.sql_guard import extract_tables
        return extract_tables(sql)

    def _resolve_table_ref(self, table: str, datasource_id: int) -> tuple[str, int]:
        """限定表引用归一 → (策略匹配用裸表名, 表所属数据源 id)。

        - 裸表 `t`：属于当前会话源；
        - 双段 `db.t`：db 必须等于当前源 namespace，否则拒绝（不得跨库套错策略）；
        - 三段 `ds.db.t`（跨源联邦）：catalog 段按 adh_datasources.name（全局唯一）
          解析所属源，db 段校验为该源 namespace。
        数据源名无法解析或 namespace 不匹配一律拒绝（fail-closed，不猜源）。
        跨源 SQL 中每张表按所属源加载敏感/RLS 策略，杜绝跨库同名串味（护栏 §10）。
        """
        parts = table.split(".")
        if len(parts) == 1:
            return table, datasource_id
        if len(parts) not in (2, 3):
            raise PermissionError("限定表引用尚未建立可信数据源绑定")
        from backend.common.db import get_datasource_by_id, get_datasource_by_name
        if len(parts) == 3:
            source = get_datasource_by_name(parts[0])
            if not source:
                raise PermissionError("限定表引用的数据源无法解析，已拒绝执行")
        else:
            source = get_datasource_by_id(datasource_id) if datasource_id else None
            if not source:
                raise PermissionError("限定表引用尚未建立可信数据源绑定")
        namespace = source.get("database_name") or ""
        if source.get("db_type") in ("postgres", "postgresql", "pg", "sls"):
            namespace = "public"
        if parts[-2].lower() != namespace.lower():
            raise PermissionError("跨库表引用尚未建立独立治理绑定")
        return parts[-1], int(source.get("id") or datasource_id or 0)

    def _policy_table(self, table: str, datasource_id: int) -> str:
        """兼容旧签名: 仅返回策略匹配用裸表名（限定名解析见 _resolve_table_ref）。"""
        return self._resolve_table_ref(table, datasource_id)[0]

    @staticmethod
    def _protect_projection(sql: str, result: PermissionResult):
        """避免通过列别名或计算表达式绕过结果侧隐藏和脱敏。"""
        from sqlglot import exp
        from backend.semantics.sql_guard import parse_query
        if not result.hidden_columns and not result.masked_columns:
            return
        tree = parse_query(sql)
        hidden = {str(c).lower() for c in result.hidden_columns}
        masks = {str(c).lower(): m for c, m in result.masked_columns.items()}
        if any(c.name.lower() in hidden for c in tree.find_all(exp.Column)):
            raise PermissionError("查询引用了禁止访问的字段")
        # 显式位置列重命名无法用名称可靠传播，保守拒绝。
        if any(a.args.get("columns") for a in tree.find_all(exp.TableAlias)):
            raise PermissionError("受保护字段不支持位置重命名")
        for _ in range(len(list(tree.find_all(exp.Select))) + 1):
            for select in tree.find_all(exp.Select):
                for item in select.expressions:
                    hits = [masks[c.name.lower()] for c in item.find_all(exp.Column) if c.name.lower() in masks]
                    if not hits:
                        continue
                    value = item.this if isinstance(item, exp.Alias) else item
                    if not isinstance(value, exp.Column):
                        raise PermissionError("受保护字段不支持派生计算")
                    alias = item.alias_or_name
                    mask = hits[0] if len(set(hits)) == 1 else "null"
                    masks[alias.lower()] = mask
                    result.masked_columns[alias] = mask

    # FROM/JOIN 后的下一词若是这些关键字, 说明表没有别名(而是子句边界), 不得误当作别名
    _SQL_RESERVED_AFTER_TABLE = {
        "where", "group", "by", "order", "limit", "having", "join", "inner", "left",
        "right", "full", "cross", "outer", "natural", "on", "using", "union", "minus",
        "intersect", "except", "set", "and", "or", "not", "as", "when", "then", "else",
        "end", "into", "for", "window", "semi", "anti", "straight_join", "asc", "desc", "with",
    }

    def _inject_row_filter(self, sql: str, table: str, row_filter: str) -> str:
        """注入行级过滤: 把 FROM/JOIN 引用的表包成带过滤的子查询。

        - 保留原表别名: `FROM t t1` -> `FROM (SELECT * FROM t WHERE f) AS t1` (不会退成非法的 `AS t t1`)
        - 同时覆盖 JOIN 表(而非只 FROM 首表), 避免关联查询对 JOIN 侧表的 RLS 旁路
        - 无别名时回退用表名作别名: `FROM t` -> `FROM (SELECT * FROM t WHERE f) AS t`
        """
        if not row_filter:
            return sql

        from backend.semantics.sql_guard import inject_filters
        return inject_filters(sql, {table: row_filter})[0]

    def _mask_value(self, value, mask_type: str):
        """Apply masking to a single value."""
        if value is None:
            return None

        value_str = str(value)

        if mask_type == "null":
            return None
        elif mask_type == "hash":
            return hashlib.sha256(value_str.encode()).hexdigest()[:16]
        elif mask_type == "partial":
            return self._partial_mask(value_str)
        else:
            # Default: partial masking
            return self._partial_mask(value_str)

    def _partial_mask(self, value: str) -> str:
        """Partial mask: keep first 2 and last 2 chars, mask middle."""
        if len(value) <= 4:
            return "*" * len(value)
        return value[:2] + "*" * (len(value) - 4) + value[-2:]


# Singleton instance
permission_enforcer = PermissionEnforcer()
