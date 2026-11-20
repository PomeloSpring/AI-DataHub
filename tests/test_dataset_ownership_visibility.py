"""数据集归属与可见性（owner / visibility）回归测试 — 2026-11-20 口径。

产品口径（用户拍板）：
- 普通用户通过 chat / Playground 建的产物**自动归属本人**，默认 `private`（仅创建者可见）；
- 管理员在数据集页建的是系统内置/治理资源，默认 `workspace`（按角色授权分发）；
- 可见性**唯一裁决** `dataset_service.dataset_visible_to`：admin / 属主 / workspace+角色授权，
  `public` 枚举保留但未启用；无身份 fail-closed；
- 清单、详情、取数口、看板挂图**同一口径**（列表隐藏 ≠ 取不到数，取数口才是真闸门）；
- 共享是**显式动作**（`set_dataset_sharing`），建数据集时不得顺手补授角色；
- 共享看板时级联把其引用的数据集**追加**发布给同一批角色（并集，不撤别人的共享）。

全部内存 monkeypatch，不依赖元库数据。
"""
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.core.role_service import role_service               # noqa: E402
from backend.modules.viz.services import dataset_service as svc  # noqa: E402
from backend.modules.viz.services.dashboard_service import (      # noqa: E402
    assert_chart_data_source,)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _ds(**over):
    row = {
        "id": 501, "name": "订单概览", "status": "active", "source_type": "sql",
        "object_key": "", "datasource_id": 11, "available_datasources": [],
        "sql_query": "SELECT region, COUNT(*) AS cnt FROM t_order GROUP BY region",
        "sql_bodies": None, "variables": "{}", "field_config": "[]",
        "chart_type": "", "chart_preset": "{}", "owner_id": 1,
        "visibility": "private",
    }
    row.update(over)
    return row


def _identity(role="analyst", user_id=1001):
    return {"user_id": user_id, "username": "u", "role": role, "workspace_id": 301}


@pytest.fixture
def grants(monkeypatch):
    """角色数据集授权集（第二裁决输入）；改写 holder 即可换授权。"""
    holder = {"ids": []}
    monkeypatch.setattr(role_service, "get_user_allowed_datasets",
                        lambda uid: list(holder["ids"]))
    return holder


# ── 1. 可见性唯一裁决 ────────────────────────────────────────────────────

class TestVisibilityDecision:
    def test_no_identity_fails_closed(self, grants):
        assert svc.dataset_visible_to(_ds(), {"user_id": 0, "role": ""}) is False

    def test_admin_sees_everything_incl_private(self, grants):
        """管理面可见：admin 能治理他人私有数据集（数据行仍由 enforcer 按查看者治理）。"""
        grants["ids"] = []
        assert svc.dataset_visible_to(_ds(owner_id=999), _identity(role="admin")) is True

    def test_owner_sees_own_private_without_role_grant(self, grants):
        grants["ids"] = []
        assert svc.dataset_visible_to(_ds(owner_id=1001), _identity()) is True

    def test_other_users_private_invisible(self, grants):
        grants["ids"] = [501]          # 即便角色表里有脏授权行，private 也不放行
        assert svc.dataset_visible_to(_ds(owner_id=999), _identity()) is False

    def test_workspace_requires_role_grant(self, grants):
        grants["ids"] = [501]
        assert svc.dataset_visible_to(
            _ds(owner_id=999, visibility="workspace"), _identity()) is True
        grants["ids"] = [777]
        assert svc.dataset_visible_to(
            _ds(owner_id=999, visibility="workspace"), _identity()) is False

    def test_public_is_not_enabled(self, grants):
        grants["ids"] = []
        assert svc.dataset_visible_to(
            _ds(owner_id=999, visibility="public"), _identity()) is False

    def test_default_visibility_by_creator(self, grants):
        assert svc.default_visibility(_identity(role="admin")) == "workspace"
        assert svc.default_visibility(_identity()) == "private"
        assert svc.default_visibility(_identity(role="viewer")) == "private"

    def test_assert_message_is_business_readable_and_redacted(self, grants):
        grants["ids"] = []
        with pytest.raises(PermissionError) as e:
            svc._assert_dataset_visible(_ds(owner_id=999), _identity(), action="取数")
        msg = str(e.value)
        assert "个人数据集" in msg and "订单概览" in msg
        assert "501" not in msg and "datasource" not in msg.lower()

        with pytest.raises(PermissionError) as e2:
            svc._assert_dataset_visible(
                _ds(owner_id=999, visibility="workspace"), _identity(), action="取数")
        assert "未被授权" in str(e2.value)


# ── 2. 清单口径（非 admin = 属主 ∪ workspace+授权） ──────────────────────

class TestListOwnership:
    def _stub(self, monkeypatch, rows):
        captured = {}

        def fake(sql, params=None, fetchone=False, **kw):
            if "adh_datasets" in sql:
                captured["sql"] = sql
                captured["params"] = tuple(params or ())
                hit = [dict(r, references=0) for r in rows]
                return (hit[0] if hit else None) if fetchone else hit
            if "adh_charts" in sql:
                return {"n": 0} if fetchone else []
            return None if fetchone else []

        monkeypatch.setattr(svc, "execute_query", fake)
        monkeypatch.setattr(svc, "_load_datasource_catalog",
                            lambda: {"prd-cn": {"id": 11, "name": "prd-cn",
                                                "db_type": "mysql", "site_code": ""}})
        monkeypatch.setattr(svc, "_allowed_source_ids", lambda ident: {11})
        monkeypatch.setattr(svc, "usable_scope",
                            lambda ds, ident, **kw: ([{"id": 11, "name": "prd-cn"}], []))
        return captured

    def test_non_admin_sql_is_owner_or_role_granted(self, monkeypatch, grants):
        grants["ids"] = [777]
        cap = self._stub(monkeypatch, [_ds(owner_id=1001, visibility="private")])
        items = svc.list_datasets(_identity())
        assert "d.owner_id = %s" in cap["sql"]
        assert "d.visibility = 'workspace'" in cap["sql"]
        assert "d.id IN (%s)" in cap["sql"]
        assert cap["params"][:2] == (1001, 777)
        assert items[0]["owner_name"] == "未知创建者"     # 未 JOIN 到时给可读占位，不回 id
        assert items[0]["visibility_label"] == "仅创建者"
        assert items[0]["is_owner"] is True and items[0]["editable"] is True

    def test_empty_grant_still_lists_owner_rows(self, monkeypatch, grants):
        grants["ids"] = []
        cap = self._stub(monkeypatch, [_ds(owner_id=1001)])
        assert svc.list_datasets(_identity())      # 空授权不得把属主短路掉
        assert "AND 1=0" in cap["sql"]

    def test_other_users_private_row_is_dropped_even_if_sql_returns_it(
            self, monkeypatch, grants):
        """兜底裁决：SQL 口径异常/旧库行漏进结果时，不得展示他人私有数据集。"""
        grants["ids"] = []
        self._stub(monkeypatch, [_ds(owner_id=999, visibility="private")])
        assert svc.list_datasets(_identity()) == []


# ── 3. 取数口同口径（列表隐藏 ≠ 取不到数） ──────────────────────────────

class TestQueryGate:
    def test_other_private_dataset_rejected_before_execution(self, monkeypatch, grants):
        grants["ids"] = []
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _ds(owner_id=999))
        monkeypatch.setattr(svc, "_query_sql", lambda *a: pytest.fail("不得下发执行"))
        with pytest.raises(PermissionError) as e:
            svc.query_dataset(501, {}, _identity())
        assert "个人数据集" in str(e.value)

    def test_owner_can_run_own_private_dataset(self, monkeypatch, grants):
        grants["ids"] = []
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _ds(owner_id=1001))
        monkeypatch.setattr(svc, "_scope_filters", lambda *a: [])
        monkeypatch.setattr(svc, "resolve_effective_source", lambda ds, ident, **kw: 11)
        sunk = []
        monkeypatch.setattr(svc, "_query_sql", lambda ds, params, scopes, ident: (
            sunk.append(ds) or {"columns": ["region"], "rows": [{"region": "cn"}]}))
        out = svc.query_dataset(501, {}, _identity())
        assert sunk and out["rows"] == [{"region": "cn"}]


# ── 4. 显式共享 / 收回 ──────────────────────────────────────────────────

class TestSharing:
    def _stub(self, monkeypatch, ds, *, role_rows=None):
        monkeypatch.setattr(svc, "get_dataset", lambda _id: dict(ds))
        monkeypatch.setattr(svc, "_require_manage", lambda ident, path: None)
        monkeypatch.setattr(svc, "_references", lambda _id: [])
        monkeypatch.setattr(svc, "execute_query",
                            lambda sql, params=None, fetchone=False, **kw: (
                                role_rows if "adh_roles" in sql else []))
        writes = []
        monkeypatch.setattr(svc, "execute_write",
                            lambda sql, params=None: writes.append((sql, params)))
        return writes

    def test_revoke_to_private_clears_role_grants(self, monkeypatch):
        writes = self._stub(monkeypatch, _ds(owner_id=1001, visibility="workspace"))
        svc.set_dataset_sharing(501, {"visibility": "private"}, _identity())
        assert any("SET visibility='private'" in s for s, _ in writes)
        assert any("DELETE FROM adh_role_dataset_access" in s for s, _ in writes)

    def test_workspace_requires_roles_and_writes_grants(self, monkeypatch):
        writes = self._stub(monkeypatch, _ds(owner_id=1001), role_rows=[{"id": 2}, {"id": 3}])
        with pytest.raises(ValueError):
            svc.set_dataset_sharing(501, {"visibility": "workspace"}, _identity())
        svc.set_dataset_sharing(
            501, {"visibility": "workspace", "role_ids": [2, 3]}, _identity())
        assert any("SET visibility='workspace'" in s for s, _ in writes)
        assert sum("INSERT IGNORE INTO adh_role_dataset_access" in s for s, _ in writes) == 2

    def test_unknown_role_is_rejected(self, monkeypatch):
        self._stub(monkeypatch, _ds(owner_id=1001), role_rows=[{"id": 2}])
        with pytest.raises(ValueError) as e:
            svc.set_dataset_sharing(
                501, {"visibility": "workspace", "role_ids": [2, 999]}, _identity())
        assert "角色不存在" in str(e.value)

    def test_public_is_rejected_explicitly(self, monkeypatch):
        self._stub(monkeypatch, _ds(owner_id=1001))
        with pytest.raises(ValueError) as e:
            svc.set_dataset_sharing(501, {"visibility": "public"}, _identity())
        assert "尚未开放" in str(e.value)

    def test_non_owner_non_admin_cannot_share(self, monkeypatch):
        self._stub(monkeypatch, _ds(owner_id=999, visibility="workspace"))
        with pytest.raises(ValueError) as e:
            svc.set_dataset_sharing(501, {"visibility": "private"}, _identity())
        assert "仅创建者或管理员" in str(e.value)

    def test_grant_is_union_not_replace(self, monkeypatch):
        """看板级联必须是并集：不得撤掉别的看板共享出去的访问。"""
        writes = []
        monkeypatch.setattr(svc, "execute_query",
                            lambda sql, params=None, fetchone=False, **kw: [{"id": 2}])
        monkeypatch.setattr(svc, "execute_write",
                            lambda sql, params=None: writes.append((sql, params)))
        live = svc.grant_dataset_to_roles(501, [2, 999])
        assert live == [2]
        assert not any("DELETE FROM adh_role_dataset_access" in s for s, _ in writes)
        assert any("visibility='workspace'" in s for s, _ in writes)


# ── 5. 看板挂图闸门（同口径） ────────────────────────────────────────────

class TestDashboardGate:
    def test_other_private_dataset_cannot_be_charted(self, monkeypatch, grants):
        grants["ids"] = []
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _ds(owner_id=999))
        with pytest.raises(ValueError) as e:
            assert_chart_data_source(
                {"chart_type": "bar", "source_type": "dataset", "source_id": 501,
                 "config": json.dumps({})}, _identity())
        assert "个人数据集" in str(e.value)

    def test_owner_can_chart_own_private_dataset(self, monkeypatch, grants):
        grants["ids"] = []
        monkeypatch.setattr(svc, "get_dataset", lambda _id: _ds(owner_id=1001))
        monkeypatch.setattr(svc, "usable_scope", lambda ds, ident, **kw: (
            [{"id": 11, "name": "prd-cn"}], []))
        assert_chart_data_source(
            {"chart_type": "bar", "source_type": "dataset", "source_id": 501,
             "config": json.dumps({})}, _identity())


# ── 6. 共享看板时级联发布数据集 ──────────────────────────────────────────

class TestScreenCascade:
    def test_dashboard_publish_grants_referenced_datasets(self, monkeypatch):
        from backend.modules.mind.execution.sdk_tools import screen_tools as sc
        import backend.common.db as db

        monkeypatch.setattr(db, "execute_query",
                            lambda sql, params=None, **kw: [{"source_id": 501}, {"source_id": 502}])
        called = []
        monkeypatch.setattr(svc, "grant_dataset_to_roles",
                            lambda did, roles: called.append((did, tuple(roles))) or list(roles))
        ids = sc._publish_datasets_of_dashboard(88, [2, 3])
        assert ids == [501, 502]
        assert called == [(501, (2, 3)), (502, (2, 3))]


# ── 7. 迁移文件静态约束 ──────────────────────────────────────────────────

class TestMigrationFile:
    def test_migration_is_idempotent_and_has_no_db_name(self):
        sql = (ROOT / "docker/mysql/dataset_ownership_visibility_migration.sql").read_text(
            encoding="utf-8")
        assert not any(line.strip().upper().startswith("USE ")
                       for line in sql.splitlines())       # 环境库可能是 adh2，库名不写死
        assert "information_schema" in sql     # 列存在性幂等判定
        assert "adh_role_dataset_access" in sql and "'workspace'" in sql
        assert "idx_ds_visibility" in sql
