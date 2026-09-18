"""敏感字段"不能查询出来"(block)多级护城河 —— 离线单测。

全部使用假数据注入(monkeypatch), 不触碰真实数据库:
  - M3 enforcer._get_sensitive_policies: 全局 ∪ 指定数据源并集; block→blocks, 其余→masks
  - M3 enforcer.get_blocked_columns / _references_blocked(词边界, SELECT * 不误判)
  - M3 enforcer.check_access: admin 仍并入 block→hidden_columns
  - M6 gates._blocked_column_guard: intent 点名 block 列→拒绝; 未点名→放行
  - M4 execute_query_with_permission: 无 user_context 仍套治理基线并丢弃 block 列
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from services.datamind.permission.enforcer import (
    PermissionEnforcer, PermissionResult, permission_enforcer,
)
from services.shared.semantics import gates, planner
from services.shared.semantics.models import PlannedExecution, ResolvedBinding, SemanticQuery


# ── M3: _get_sensitive_policies 全局 ∪ 指定并集 ─────────────────

class TestSensitivePoliciesUnion:
    def test_block_and_mask_partition(self, monkeypatch):
        captured = {}

        def fake_exec(sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
            return [
                {"column_name": "phone", "mask_type": "block"},
                {"column_name": "EMAIL", "mask_type": "BLOCK"},   # 大小写不敏感
                {"column_name": "salary", "mask_type": "partial"},
                {"column_name": "id", "mask_type": "none"},
                {"column_name": None, "mask_type": "block"},       # 脏行忽略
            ]

        monkeypatch.setattr("services.shared.common.db.execute_query", fake_exec)
        masks, blocks = permission_enforcer._get_sensitive_policies(3, 7, "t_user")
        # block 归 blocks 并去重(大小写按原样保留), 其余归 masks
        assert sorted(blocks) == sorted(["phone", "EMAIL"])
        assert masks.get("salary")  # partial -> 有映射
        assert "phone" not in masks and "EMAIL" not in masks

    def test_query_uses_global_union(self, monkeypatch):
        captured = {}

        def fake_exec(sql, params=None):
            captured["sql"] = sql
            captured["params"] = list(params or [])
            return []

        monkeypatch.setattr("services.shared.common.db.execute_query", fake_exec)
        permission_enforcer._get_sensitive_policies(3, 7, "t_user")
        sql = captured["sql"]
        # 全局(datasource_id=0 / table_name='') ∪ 指定, workspace_id 也含 0
        assert "datasource_id IN (%s, 0)" in sql
        assert "table_name IN (%s, '')" in sql
        assert "workspace_id IN (%s, 0)" in sql
        assert captured["params"] == [7, "t_user", 3]


# ── M3: get_blocked_columns / _references_blocked ───────────────

class TestBlockHelpers:
    def test_get_blocked_columns_delegates(self, monkeypatch):
        monkeypatch.setattr(
            PermissionEnforcer, "_get_sensitive_policies",
            lambda self, ws, ds, table: ({"salary": "full"}, ["phone", "id_card"]),
        )
        assert permission_enforcer.get_blocked_columns(0, 0, "") == ["phone", "id_card"]

    def test_references_blocked_word_boundary(self):
        blocked = ["phone", "salary"]
        assert PermissionEnforcer._references_blocked("SELECT user_id, phone FROM t", blocked) == ["phone"]
        # SELECT * 不含列名 -> 不算点名(交由执行层结果剔除)
        assert PermissionEnforcer._references_blocked("SELECT * FROM t", blocked) == []
        # 词边界: salary_avg / salarys 不应误伤 salary
        assert PermissionEnforcer._references_blocked("SELECT salary_avg, salarys FROM t", blocked) == []
        # 但真实 salary 命中
        assert "salary" in PermissionEnforcer._references_blocked("SELECT AVG(salary) FROM t", blocked)
        # 大小写不敏感
        assert PermissionEnforcer._references_blocked("SELECT PHONE FROM t", blocked) == ["phone"]


# ── M3: check_access 对 admin 仍并入 block ──────────────────────

class TestCheckAccessAdmin:
    def test_admin_still_blocks_sensitive_columns(self, monkeypatch):
        from services.authservice.services.role_service import role_service

        monkeypatch.setattr(
            PermissionEnforcer, "_get_sensitive_policies",
            lambda self, ws, ds, table: ({"salary": "full"}, ["phone"]),
        )
        monkeypatch.setattr(role_service, "get_user_roles", lambda uid, ws: [{"name": "admin"}])

        res = permission_enforcer.check_access(
            user_id=99, workspace_id=1, datasource_id=7, table_name="t_user")
        assert res.allowed is True
        # admin 旁路只放宽 RBAC/RLS, 合规屏蔽列仍生效
        assert "phone" in res.hidden_columns
        assert res.masked_columns.get("salary") == "full"
        assert any(p.startswith("sensitive_block:") for p in res.policies_applied)

    def test_sensitive_only_skips_rbac(self, monkeypatch):
        # 无身份(user_id=0): 只套治理基线, 不查角色
        def _boom(*a, **k):
            raise AssertionError("sensitive_only 不应触发 RBAC/RLS 查询")

        monkeypatch.setattr(
            PermissionEnforcer, "_get_sensitive_policies",
            lambda self, ws, ds, table: ({}, ["phone"]),
        )
        from services.authservice.services.role_service import role_service
        monkeypatch.setattr(role_service, "get_user_roles", _boom)
        res = permission_enforcer.check_access(
            user_id=0, workspace_id=0, datasource_id=7, table_name="t_user",
            sensitive_only=True)
        assert res.allowed is True
        assert res.hidden_columns == ["phone"]


# ── M6: gates._blocked_column_guard 显式请求即拒绝 ───────────────

class _StubResolver:
    def __init__(self, binding, dialect="mysql"):
        pass

    def resolve_dimension(self, name):
        return ("", "dim")


def _binding():
    return ResolvedBinding(
        object_key="case", datasource_id=7, physical_table="t_case", bind_kind="primary")


def _planned(sql):
    return PlannedExecution(sql=sql, dialect="mysql", datasource_id=7)


class TestSemanticGuard:
    def _patch(self, monkeypatch, blocked):
        monkeypatch.setattr(
            PermissionEnforcer, "get_blocked_columns",
            lambda self, ws, ds, table="": list(blocked))
        monkeypatch.setattr(planner, "_ColumnResolver", _StubResolver)

    def test_named_blocked_column_rejected(self, monkeypatch):
        self._patch(monkeypatch, ["phone"])
        q = SemanticQuery(object="case", metrics=["case_count"], dimensions=["phone"])
        msg = gates._blocked_column_guard(q, _binding(), _planned("SELECT `phone`, COUNT(*) FROM t_case"), 0)
        assert msg and "phone" in msg and "不允许查询" in msg

    def test_sql_text_fallback_rejected(self, monkeypatch):
        # intent 未点名, 但编译 SQL 词边界命中 block 列(如间接聚合泄露)
        self._patch(monkeypatch, ["salary"])
        q = SemanticQuery(object="case", metrics=["avg_salary"], dimensions=["status"])
        msg = gates._blocked_column_guard(q, _binding(), _planned("SELECT `status`, AVG(salary) FROM t_case"), 0)
        assert msg and "salary" in msg

    def test_not_named_passes(self, monkeypatch):
        self._patch(monkeypatch, ["phone"])
        q = SemanticQuery(object="case", metrics=["case_count"], dimensions=["status"])
        msg = gates._blocked_column_guard(
            q, _binding(), _planned("SELECT `status`, COUNT(*) FROM t_case"), 0)
        assert msg is None

    def test_sql_template_binding_skipped(self, monkeypatch):
        # sql_template 绑定交由执行层结果剔除, 语义层不在此拒绝
        self._patch(monkeypatch, ["phone"])
        b = ResolvedBinding(
            object_key="case", datasource_id=7, physical_table="t_case", bind_kind="sql_template")
        q = SemanticQuery(object="case", dimensions=["phone"])
        assert gates._blocked_column_guard(q, b, _planned("SELECT `phone` FROM t_case"), 0) is None


# ── M4: execute_query_with_permission 无 user 仍丢 block 列 ──────

class TestExecutorMoat:
    def test_no_user_context_still_drops_blocked(self, monkeypatch):
        from services.datamind.nl2sql.sql import query_executor as qe

        seen = {}

        def fake_enforce_sql(sql, user_id, workspace_id, datasource_id):
            seen["user_id"] = user_id
            return sql, PermissionResult(hidden_columns=["phone"])

        def fake_exec(sql, datasource_id=None, query_type="sql"):
            df = pd.DataFrame([{"id": 1, "name": "a", "phone": "13800000000"}])
            return df, 5, 1

        audit_calls = {"n": 0}
        monkeypatch.setattr(permission_enforcer, "enforce_sql", fake_enforce_sql)
        monkeypatch.setattr(qe, "execute_query", fake_exec)
        monkeypatch.setattr(
            qe, "_log_permission_audit",
            lambda **k: audit_calls.__setitem__("n", audit_calls["n"] + 1))

        df, _ms, _rc = qe.execute_query_with_permission(
            "SELECT * FROM t_user", datasource_id=7, query_type="sql",
            user_context={}, workspace_id=0)

        # 无身份仍走治理基线: enforce_sql 以 user_id=0 调用, block 列被丢弃
        assert seen["user_id"] == 0
        assert "phone" not in df.columns
        assert "name" in df.columns
        # 无真实用户不落审计
        assert audit_calls["n"] == 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
