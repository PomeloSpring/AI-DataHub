"""质量检查必须与业务取数共享身份、RLS 和敏感基线。"""
from unittest.mock import Mock

import pytest
from backend.modules.gov.services import quality_engine as quality


def test_quality_requires_identity_before_any_database_access(monkeypatch):
    db = Mock(side_effect=AssertionError("无身份不能访问数据库"))
    monkeypatch.setattr(quality, "DBConnection", db)
    with pytest.raises((PermissionError, Exception)) as error:
        quality.execute_single_rule({"id": 1, "rule_type": "row_count", "workspace_id": 3})
    assert "数据库" not in str(error.value)
    db.assert_not_called()


def test_check_query_uses_governance_and_binds_values(monkeypatch):
    seen = {}
    monkeypatch.setattr(quality, "authorize_workspace", lambda user, ws: ws, raising=False)
    def governed(sql, datasource_id, user_id, workspace_id, username=""):
        seen.update(sql=sql, user_id=user_id, workspace_id=workspace_id)
        return {"rows": [{"c": 4}], "columns": ["c"], "row_count": 1}
    monkeypatch.setattr(quality, "governed_execute", governed, raising=False)
    context = quality._open_target_conn(
        {"target_datasource_id": 7, "workspace_id": 3}, {"user_id": 42}, False)
    assert quality._fetch_one(context, "SELECT COUNT(*) c FROM t WHERE name REGEXP %s", ("x' OR 1=1 --",)) == {"c": 4}
    assert seen["user_id"] == 42 and seen["workspace_id"] == 3
    from backend.semantics.sql_guard import parse_query
    assert parse_query(seen["sql"]) is not None


def test_samples_are_not_fetched_for_persisted_runs(monkeypatch):
    monkeypatch.setattr(quality, "authorize_workspace", lambda user, ws: ws, raising=False)
    executor = Mock(side_effect=AssertionError("普通检查不取样本"))
    monkeypatch.setattr(quality, "governed_execute", executor, raising=False)
    context = quality._open_target_conn({"target_datasource_id": 7, "workspace_id": 3}, {"user_id": 42}, False)
    assert quality._fetch_all(context, "SELECT * FROM t LIMIT 5") == []
    executor.assert_not_called()
