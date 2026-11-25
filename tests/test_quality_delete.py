"""质量报告 / 问题记录删除回归。

口径（.qoder/rules/no-silent-degradation.md、product-capability-first.md）：
* 删除前按行读取 workspace 并走 authorize_resource_scope（不能凭路径 id 越权）；
* 不存在显式 404，且不发出 DELETE；
* 批量删除逐条复用单条实现，逐条回报失败项（部分成功不得当成全部成功）；
* 「只拉失败记录」的 passed 过滤必须下推 SQL（不是前端再筛）。
"""
import importlib

import pytest
from fastapi import HTTPException


class _Cursor:
    def __init__(self, rows, log):
        self.rows = rows
        self.log = log
        self._sql = ""
        self._params = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sql = sql
        self._params = list(params or [])
        self.log.append((sql, self._params))

    def fetchone(self):
        if self._sql.lstrip().upper().startswith("SELECT COUNT(*)"):
            return {"total": 0}
        if self._sql.lstrip().upper().startswith("SELECT"):
            return self.rows.get(self._params[0])
        return None

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, rows, log):
        self.rows = rows
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor(self.rows, self.log)


def _patch(monkeypatch, rows):
    api = importlib.import_module("backend.modules.gov.api.quality")
    log: list = []
    scoped: list = []
    monkeypatch.setattr(api, "DBConnection", lambda: _Conn(rows, log))
    monkeypatch.setattr(
        api, "authorize_resource_scope",
        lambda user, ws: scoped.append(ws) or ws,
    )
    return api, log, scoped


def _deletes(log):
    return [sql for sql, _ in log if sql.lstrip().upper().startswith("DELETE")]


def test_delete_report_checks_row_scope_then_deletes(monkeypatch):
    api, log, scoped = _patch(monkeypatch, {5: {"id": 5, "workspace_id": 0}})

    out = api.delete_report(5, user={"user_id": 42})

    assert out == {"message": "Report deleted", "id": 5}
    assert scoped == [0]
    assert _deletes(log) == ["DELETE FROM adh_quality_reports WHERE id = %s"]


def test_delete_report_missing_is_404_and_deletes_nothing(monkeypatch):
    api, log, scoped = _patch(monkeypatch, {})

    with pytest.raises(HTTPException) as err:
        api.delete_report(9, user={"user_id": 42})

    assert err.value.status_code == 404
    assert scoped == []
    assert _deletes(log) == []


def test_delete_result_checks_row_scope_and_returns_rule(monkeypatch):
    api, log, scoped = _patch(
        monkeypatch, {1: {"id": 1, "rule_id": 7, "workspace_id": 0}},
    )

    out = api.delete_result(1, user={"user_id": 42})

    assert out["rule_id"] == 7 and out["id"] == 1
    assert scoped == [0]
    assert _deletes(log) == ["DELETE FROM adh_quality_results WHERE id = %s"]


def test_batch_delete_reports_partial_failure_per_item(monkeypatch):
    api, log, scoped = _patch(
        monkeypatch, {1: {"id": 1, "rule_id": 7, "workspace_id": 0}},
    )

    out = api.batch_delete_results(
        api.ResultBatchDelete(ids=[1, 1, 2]), user={"user_id": 42},
    )

    assert out["deleted"] == [1]
    assert out["deleted_count"] == 1
    assert out["failed"] == [{"id": 2, "reason": "问题记录不存在"}]
    # 重复 id 去重后只删一次；缺失的不发 DELETE
    assert _deletes(log) == ["DELETE FROM adh_quality_results WHERE id = %s"]


def test_results_passed_filter_is_pushed_into_sql(monkeypatch):
    api, log, _ = _patch(monkeypatch, {})

    out = api.list_results(rule_id=7, passed=0, workspace_id=None,
                           start_date=None, end_date=None, page=1, page_size=100,
                           user={"user_id": 42})

    assert out["items"] == []
    select_sql, params = [x for x in log if "FROM adh_quality_results" in x[0] and "COUNT" not in x[0]][0]
    assert "passed = %s" in select_sql
    assert 0 in params