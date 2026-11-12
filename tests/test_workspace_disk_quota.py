"""磁盘配额必须真的生效（2026-11 存储治理 P0-2）：无登记行 = 系统缺省，不是"不限制"。

锁定的口径：
1. **唯一裁决点** `role_service.get_user_workspace_quota` —— 建空间门、新建会话磁盘门、
   用量面板、管理员页四处必须拿到同一个数字。历史缺陷是拦截门自己 SELECT 把"无行"读成
   0=不限，而管理员页 COALESCE 显示 5GB：页面写着有配额、实际从不拦（配了没用）。
2. 显式登记行是权威，行值 0 = 管理员"该用户不设限"逃生口（与缺省靠 is_default 区分）。
3. 缺省数字只有一个来源（common/config.py），且与迁移 SQL 的列缺省同值，防两边分叉。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.common import config
from backend.core import role_service as role_module
from backend.core.role_service import role_service
from backend.modules.mind.execution import session_workspace

MB = 1024 ** 2


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):  # noqa: ARG002
        return None

    def fetchone(self):
        return self._rows

    def fetchall(self):
        return self._rows or []


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        return None


def _patch_quota_row(monkeypatch, row):
    """让 role_service 里"取配额行"的 SELECT 返回给定行（None = 无登记行）。"""
    monkeypatch.setattr(role_module, "get_metadata_conn", lambda: _FakeConn(row))


# ── ① 唯一裁决点：无行取缺省、有行为权威 ────────────────────────────

def test_no_registered_row_uses_system_default_not_unlimited(monkeypatch):
    _patch_quota_row(monkeypatch, None)
    quota = role_service.get_user_workspace_quota(12345)
    assert quota["disk_quota_bytes"] == config.WORKSPACE_DISK_QUOTA_DEFAULT_BYTES
    assert quota["disk_quota_bytes"] > 0, "无登记行不得被解释为不限制（fail-closed）"
    assert quota["max_workspaces"] == config.WORKSPACE_QUOTA_DEFAULT_MAX_WORKSPACES
    assert quota["is_default"] is True


def test_registered_row_overrides_default(monkeypatch):
    _patch_quota_row(monkeypatch, {"max_workspaces": 9, "disk_quota_bytes": 123 * MB})
    quota = role_service.get_user_workspace_quota(12345)
    assert quota["disk_quota_bytes"] == 123 * MB
    assert quota["max_workspaces"] == 9
    assert quota["is_default"] is False


def test_registered_zero_is_explicit_unlimited_escape_hatch(monkeypatch):
    """行里 0 是管理员显式配置（仅可经 SQL 写入），语义与"无行取缺省"必须可区分。"""
    _patch_quota_row(monkeypatch, {"max_workspaces": 5, "disk_quota_bytes": 0})
    quota = role_service.get_user_workspace_quota(12345)
    assert quota["disk_quota_bytes"] == 0 and quota["is_default"] is False


# ── ② 拦截门真的拦 ─────────────────────────────────────────────────

def _gate(monkeypatch, *, quota_bytes, usage_bytes):
    monkeypatch.setattr(role_service, "get_user_workspace_quota",
                        lambda uid: {"max_workspaces": 5, "disk_quota_bytes": quota_bytes,
                                     "is_default": True})
    monkeypatch.setattr(session_workspace, "workspace_disk_usage",
                        lambda ws, uid: usage_bytes)
    return session_workspace.assert_workspace_disk_quota(301, 12345)


def test_gate_blocks_when_usage_reaches_default_quota(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _gate(monkeypatch, quota_bytes=config.WORKSPACE_DISK_QUOTA_DEFAULT_BYTES,
              usage_bytes=config.WORKSPACE_DISK_QUOTA_DEFAULT_BYTES)
    assert exc.value.status_code == 409
    detail = exc.value.detail
    # 文案可诊断：带已用/配额的可读体积 + 清理与调整配额的出路（不得只说"已用尽"）
    assert "配额" in detail and "MB" in detail
    assert "归档" in detail and "管理员" in detail


def test_gate_allows_below_quota_and_honours_unlimited_row(monkeypatch):
    assert _gate(monkeypatch, quota_bytes=500 * MB, usage_bytes=499 * MB) is None
    assert _gate(monkeypatch, quota_bytes=0, usage_bytes=99 * 1024 ** 3) is None  # 显式不设限


# ── ③ 面板与管理员页与拦截门同源（双口径回归防线）──────────────────

def test_disk_status_reports_same_effective_quota_as_gate(monkeypatch):
    from backend.modules.mind.api import user_assets

    expected = {"max_workspaces": 5,
                "disk_quota_bytes": config.WORKSPACE_DISK_QUOTA_DEFAULT_BYTES,
                "is_default": True}
    monkeypatch.setattr(role_service, "get_user_workspace_quota", lambda uid: dict(expected))
    monkeypatch.setattr(user_assets, "get_metadata_conn", lambda: _FakeConn(None))
    monkeypatch.setattr(session_workspace, "workspace_disk_usage", lambda ws, uid: 7 * MB)

    status = user_assets.disk_status(301, 12345)
    assert status["quota_bytes"] == config.WORKSPACE_DISK_QUOTA_DEFAULT_BYTES
    assert status["quota_is_default"] is True
    assert status["usage_bytes"] == 7 * MB


def test_admin_quota_listing_marks_system_default(monkeypatch):
    """管理员页显示的就是实际生效值：无行 → 缺省 + quota_is_default=True。"""
    users = [{"user_id": 1, "username": "a", "registered_max_workspaces": None,
              "registered_disk_quota_bytes": None},
             {"user_id": 2, "username": "b", "registered_max_workspaces": 7,
              "registered_disk_quota_bytes": 88 * MB}]

    class _Cursor:
        """模拟 pymysql 游标语义：with 块退出即 close，之后再 execute 必须报错。

        （这条断言不是摆设：本批改动初版把循环写到了 with 块外面，
         真库会 ProgrammingError "Cursor is closed"，管理员页直接 500。）
        """
        def __init__(self):
            self._rows = None
            self._closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._closed = True
            return False

        def execute(self, sql, params=None):
            if self._closed:
                raise AssertionError("Cursor is closed —— 不得在 with 块外使用游标")
            if "LEFT JOIN adh_user_workspace_quota" in sql:
                self._rows = [dict(u) for u in users]
            else:
                self._rows = [{"id": 301, "name": "默认空间", "is_default": 1}]

        def fetchall(self):
            return self._rows

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            return None

    monkeypatch.setattr(role_module, "get_metadata_conn", lambda: _Conn())
    out = role_service.list_workspace_quotas()
    assert out[0]["disk_quota_bytes"] == config.WORKSPACE_DISK_QUOTA_DEFAULT_BYTES
    assert out[0]["quota_is_default"] is True
    assert out[1]["disk_quota_bytes"] == 88 * MB and out[1]["quota_is_default"] is False


# ── ④ 不得再长出第二个配额读法 / 第二套数字 ─────────────────────────

def test_execution_and_panel_paths_do_not_read_quota_table_directly():
    root = Path(__file__).resolve().parents[1]
    for rel in ("backend/modules/mind/execution/session_workspace.py",
                "backend/modules/mind/api/user_assets.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "adh_user_workspace_quota" not in text, \
            f"{rel} 又自己 SELECT 配额表了 —— 配额必须经 role_service.get_user_workspace_quota"


def test_default_number_has_single_source_and_matches_migration_ddl():
    """配置缺省与迁移里的列缺省必须同值，否则"将来插入的行"与代码裁决分叉。"""
    root = Path(__file__).resolve().parents[1]
    sql = (root / "docker" / "mysql" / "workspace_disk_quota_default_migration.sql").read_text(
        encoding="utf-8")
    ddl = re.search(r"ALTER COLUMN disk_quota_bytes SET DEFAULT (\d+)", sql)
    assert ddl, "迁移里找不到列缺省语句"
    assert int(ddl.group(1)) == config.WORKSPACE_DISK_QUOTA_DEFAULT_BYTES
