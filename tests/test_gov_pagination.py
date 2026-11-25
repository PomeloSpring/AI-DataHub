"""质量规则 / 数据标准清单服务端**真分页**回归（禁止"拉全量再前端切片"的假分页）。

口径（.qoder/rules/list-pagination-server-side.md）：
* 传了 page_size 才走分页并返回 {items,total,page,page_size}；不传仍回全量数组（选用面需要全量在手）；
* 行总数由服务端 COUNT 得到（不是当前页长度），筛选/搜索全部下推 SQL；
* 偏移量由页号算出（调用方不递 offset），排序含唯一 id 兜底（不重行/漏行）。
"""

import importlib


class _FakeCursor:
    def __init__(self, log):
        self.log = log
        self._sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, list(params or [])))
        self._sql = sql

    def fetchone(self):
        if "COUNT(*)" in self._sql:
            return {"total": 3}
        return None

    def fetchall(self):
        return [{
            "id": 2, "rule_name": "空值检查", "description": "订单号非空",
            "rule_type": "not_null", "target_table": "orders", "target_column": "id",
            "severity": "high", "is_active": 1, "rule_config": "{}",
            "workspace_id": 0, "last_check_time": None,
        }]


class _FakeConn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self.log)


def test_list_rules_paginated_pushes_filters_and_uses_limit_offset(monkeypatch):
    quality_api = importlib.import_module("backend.modules.gov.api.quality")
    log: list = []
    monkeypatch.setattr(quality_api, "DBConnection", lambda: _FakeConn(log))

    out = quality_api.list_rules(
        workspace_id=0, target_table="ord", rule_type="not_null", severity="high",
        is_active=1, q=" 订单 ", page=2, page_size=2, user={"user_id": 1},
    )

    # 契约：{items,total,page,page_size}；total 来自 COUNT，不是当前页长度
    assert set(out) == {"items", "total", "page", "page_size"}
    assert out["total"] == 3 and out["page"] == 2 and out["page_size"] == 2
    assert len(out["items"]) == 1
    # 旧 DB 列 rule_name 归一为前端契约 name
    assert out["items"][0]["name"] == "空值检查"

    count_sql, count_params = log[0]
    assert "COUNT(*)" in count_sql
    # 筛选/搜索全部下推 SQL（不在前端过滤）
    assert "r.target_table LIKE %s" in count_sql
    assert "r.rule_type = %s" in count_sql
    assert "r.severity = %s" in count_sql
    assert "r.is_active = %s" in count_sql
    assert "(r.rule_name LIKE %s OR r.description LIKE %s)" in count_sql
    assert count_params == [0, "%ord%", "not_null", "high", 1, "%订单%", "%订单%"]

    page_sql, page_params = log[1]
    # 真分页：LIMIT/OFFSET 由服务端按页号算，偏移 = (page-1)*size
    assert "ORDER BY r.id DESC LIMIT %s OFFSET %s" in page_sql
    assert page_params[-2:] == [2, 2]


def test_list_rules_without_page_size_returns_full_array(monkeypatch):
    quality_api = importlib.import_module("backend.modules.gov.api.quality")
    log: list = []
    monkeypatch.setattr(quality_api, "DBConnection", lambda: _FakeConn(log))

    out = quality_api.list_rules(
        workspace_id=0, target_table=None, rule_type=None, severity=None,
        is_active=None, q=None, page=1, page_size=None, user={"user_id": 1},
    )

    assert isinstance(out, list) and len(out) == 1
    assert len(log) == 1 and "LIMIT" not in log[0][0]


def test_list_standards_paginated_pushes_filters(monkeypatch):
    standards_api = importlib.import_module("backend.modules.gov.api.standards")
    log: list = []

    def fake_execute_query(sql, params=None, fetchone=False):
        log.append((sql, list(params or []), fetchone))
        if "COUNT(*)" in sql:
            return {"total": 5}
        return [{
            "id": 1, "name": "字段命名规范", "standard_type": "naming",
            "description": "小写下划线", "rule_config": "{}", "is_active": 1,
            "workspace_id": 0, "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }]

    monkeypatch.setattr(standards_api, "execute_query", fake_execute_query)

    out = standards_api.list_standards(
        workspace_id=0, standard_type="naming", q="命名", page=3, page_size=20,
    )

    assert set(out) == {"items", "total", "page", "page_size"}
    assert out["total"] == 5 and out["page"] == 3 and out["page_size"] == 20
    assert len(out["items"]) == 1

    count_sql, count_params, count_fetchone = log[0]
    assert "COUNT(*)" in count_sql and count_fetchone is True
    assert "standard_type = %s" in count_sql
    assert "(name LIKE %s OR description LIKE %s)" in count_sql
    assert count_params == [0, "naming", "%命名%", "%命名%"]

    page_sql, page_params, _ = log[1]
    # 排序含唯一 id 兜底；偏移由服务端算出：(3-1)*20 = 40
    assert "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s" in page_sql
    assert page_params[-2:] == [20, 40]


def test_list_standards_without_page_size_returns_full_array(monkeypatch):
    standards_api = importlib.import_module("backend.modules.gov.api.standards")
    log: list = []

    def fake_execute_query(sql, params=None, fetchone=False):
        log.append((sql, list(params or []), fetchone))
        return [{"id": 1, "name": "n"}]

    monkeypatch.setattr(standards_api, "execute_query", fake_execute_query)

    out = standards_api.list_standards(
        workspace_id=0, standard_type=None, q=None, page=1, page_size=None,
    )

    assert isinstance(out, list) and len(out) == 1
    assert len(log) == 1 and "LIMIT" not in log[0][0]