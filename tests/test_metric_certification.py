"""口径认证（P4）回归：区分「可信口径」与「草稿口径」。

背景：`adh_metrics`/`adh_dimensions` 的 certified/owner 列一直存在，但 55 个口径
**全部 certified=0、无责任人**——认证机制在，从没用起来，而且未认证口径照样被
当成权威答案。

锁住的行为：
1. **认证必须有 owner**：没有责任人的口径不叫认证，只是把 0 改成 1；
2. **责任人列两表不同**：`adh_metrics.owner` / `adh_dimensions.owner_role`，
   混用会 1054（实测踩过）；
3. **解除认证不需要 owner**（退回草稿不应强制填责任人）；
4. **覆盖率必须可见**：认证率低是待办清单，不是缺陷，但不能假装口径都权威；
5. 认证**不改变**解析/查询行为（否则 0 认证时知识库会空，反破坏现有功能）。

纯函数 + fake DB，不触碰数据库。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datacatalog.services import metrics_service as ms


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sqls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sqls.append(" ".join(str(sql).split()))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.sqls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        cur = _FakeCursor(self.rows)
        cur.sqls = self.sqls
        return cur


def _patch(monkeypatch, rows):
    fake = _FakeDB(rows)
    monkeypatch.setattr(ms, "DBConnection", lambda: fake)
    return fake


class TestCertifyRequiresOwner:
    def test_certify_without_owner_rejected(self, monkeypatch):
        _patch(monkeypatch, [{"id": 1, "name": "GMV", "certified": 0, "owner": ""}])
        with pytest.raises(ValueError, match="owner"):
            ms.certify_metric(1, True, owner="")

    def test_uncertify_without_owner_allowed(self, monkeypatch):
        """退回草稿不该强制填责任人。"""
        _patch(monkeypatch, [{"id": 1, "name": "GMV", "certified": 1, "owner": "张三"}])
        r = ms.certify_metric(1, False, owner="")
        assert r["certified"] is False
        assert r["owner"] == "张三"          # 保留原责任人

    def test_certify_with_owner(self, monkeypatch):
        _patch(monkeypatch, [{"id": 1, "name": "GMV", "certified": 0, "owner": ""}])
        r = ms.certify_metric(1, True, owner="张三", certified_by="user:1")
        assert r == {"id": 1, "name": "GMV", "certified": True, "owner": "张三"}

    def test_missing_row_raises(self, monkeypatch):
        _patch(monkeypatch, [])
        with pytest.raises(ValueError, match="不存在"):
            ms.certify_metric(999, True, owner="张三")


class TestOwnerColumnDiffersPerTable:
    """两表责任人列不同：adh_metrics.owner / adh_dimensions.owner_role。"""

    def test_metric_uses_owner_column(self, monkeypatch):
        fake = _patch(monkeypatch, [{"id": 1, "name": "GMV", "certified": 0, "owner": ""}])
        ms.certify_metric(1, True, owner="张三")
        writes = [s for s in fake.sqls if s.upper().startswith("UPDATE")]
        assert any("adh_metrics SET certified = %s, owner = %s" in s for s in writes)

    def test_dimension_uses_owner_role_column(self, monkeypatch):
        fake = _patch(monkeypatch, [{"id": 2, "name": "案例状态", "certified": 0, "owner": ""}])
        ms.certify_dimension(2, True, owner="李四")
        writes = [s for s in fake.sqls if s.upper().startswith("UPDATE")]
        assert any("adh_dimensions SET certified = %s, owner_role = %s" in s for s in writes)

    def test_select_aliases_owner_for_dimension(self, monkeypatch):
        """查询维度时也要把 owner_role 别名成 owner，否则取不到。"""
        fake = _patch(monkeypatch, [{"id": 2, "name": "x", "certified": 0, "owner": ""}])
        ms.certify_dimension(2, True, owner="李四")
        selects = [s for s in fake.sqls if s.upper().startswith("SELECT")]
        assert any("owner_role AS owner" in s for s in selects)


class TestCertificationSummary:
    def _summary(self, monkeypatch, m_row, d_row):
        rows = [m_row, d_row]
        state = {"i": 0}

        class _C:
            def __init__(self, outer):
                self.outer = outer

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                return None

            def fetchone(self):
                r = rows[state["i"]]
                state["i"] += 1
                return r
        monkeypatch.setattr(ms, "DBConnection", lambda: type(
            "F", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: False,
                      "cursor": lambda s: _C(s)})())
        return ms.certification_summary()

    def test_zero_certification_is_exposed(self, monkeypatch):
        s = self._summary(monkeypatch,
                          {"total": 16, "cert": 0, "no_owner": 16},
                          {"total": 39, "cert": 0, "no_owner": 39})
        assert s["overall"]["total"] == 55
        assert s["overall"]["certified"] == 0
        assert s["overall"]["draft"] == 55
        assert "未认证" in s["note"]

    def test_partial_certification(self, monkeypatch):
        s = self._summary(monkeypatch,
                          {"total": 16, "cert": 10, "no_owner": 2},
                          {"total": 39, "cert": 5, "no_owner": 30})
        assert s["overall"]["certified"] == 15
        assert s["overall"]["draft"] == 40
        assert s["metrics"]["without_owner"] == 2
        assert s["dimensions"]["without_owner"] == 30

    def test_all_certified_says_ok(self, monkeypatch):
        s = self._summary(monkeypatch,
                          {"total": 3, "cert": 3, "no_owner": 0},
                          {"total": 2, "cert": 2, "no_owner": 0})
        assert s["overall"]["certified_ratio"] == 1.0
        assert "全部已认证" in s["note"]


class TestApiValidation:
    def test_invalid_scope_rejected(self):
        from fastapi import HTTPException
        from services.datacatalog.api import metrics as api
        with pytest.raises(HTTPException) as e:
            api.certify_scope({"scope": "hack", "id": 1, "certified": True, "owner": "x"})
        assert e.value.status_code == 400

    def test_invalid_id_rejected(self):
        from fastapi import HTTPException
        from services.datacatalog.api import metrics as api
        for bad in ({"scope": "metric", "id": "abc", "certified": True, "owner": "x"},
                    {"scope": "metric", "id": 0, "certified": True, "owner": "x"}):
            with pytest.raises(HTTPException) as e:
                api.certify_scope(bad)
            assert e.value.status_code == 400
