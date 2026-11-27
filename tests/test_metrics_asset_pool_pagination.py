"""字典待办（/api/metrics/asset-pool）真分页契约（P1-2）。

锁定四件事：

1. 传 ``page_size`` = 服务端真分页信封：``total`` 来自 COUNT（≠ 页长）、
   LIMIT/OFFSET 由服务端按 (page-1)*page_size 算、page_size 越界被夹；
2. 不传 ``page_size`` = 兼容选择面全量聚合（不挂分页条的显式例外）；
3. ``suggestions`` 候选 payload 不再混进字典资产池（候选真源是进化中心）；
4. queue/q 筛选下推 SQL（COUNT 与当前页共用同一谓词），非法入参显式拒。
"""

from __future__ import annotations

import pytest

from backend.modules.catalog.services import metrics_service


class _FakeCursor:
    def __init__(self, calls, count_rows, item_rows):
        self.calls = calls
        self.count_rows = count_rows
        self.item_rows = item_rows
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        self.calls.append((s, tuple(params or ())))
        if "COUNT(*)" in s and "GROUP BY queue" in s:
            self._rows = list(self.count_rows)
        else:
            self._rows = list(self.item_rows)

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, calls, count_rows, item_rows):
        self._cur = _FakeCursor(calls, count_rows, item_rows)

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install(monkeypatch, count_rows=None, item_rows=None):
    calls: list = []
    counts = count_rows if count_rows is not None else [
        {"queue": "metric", "n": 5}, {"queue": "dimension", "n": 3},
        {"queue": "term", "n": 2}]
    items = item_rows if item_rows is not None else [
        {"queue": "metric", "id": 1, "title": "案例数", "name_en": "case_cnt",
         "aliases": "[\"件数\"]", "bound_object_key": "", "pool_reason": "unbound",
         "unit": "", "agg_type": "COUNT", "category": "", "description": "",
         "datasource_id": 0, "term_type": ""},
        {"queue": "metric", "id": 2, "title": "订单量", "name_en": "",
         "aliases": "[]", "bound_object_key": "", "pool_reason": "unbound",
         "unit": "", "agg_type": "SUM", "category": "", "description": "",
         "datasource_id": 0, "term_type": ""},
    ]

    def _factory():
        return _FakeConn(calls, counts, items)

    monkeypatch.setattr(metrics_service, "DBConnection", _factory)
    return calls


def test_paged_mode_returns_server_envelope_with_true_total(monkeypatch):
    calls = _install(monkeypatch)
    out = metrics_service.asset_pool(page=2, page_size=2)

    assert out["total"] == 10, "total 必须来自服务端 COUNT，不是当前页长度"
    assert out["page"] == 2 and out["page_size"] == 2
    assert [r["title"] for r in out["items"]] == ["案例数", "订单量"]
    assert out["counts"] == {"metrics": 5, "dimensions": 3, "terms": 2, "total": 10}
    item_sql, item_params = calls[1]
    assert "LIMIT %s OFFSET %s" in item_sql
    assert item_params[-2:] == (2, 2), "偏移量必须由服务端按 (page-1)*page_size 算"
    count_sql, count_params = calls[0]
    assert "COUNT(*)" in count_sql
    assert count_params == item_params[:-2], "COUNT 与当前页共用同一筛选谓词"


def test_page_size_is_clamped_to_server_cap(monkeypatch):
    _install(monkeypatch)
    out = metrics_service.asset_pool(page=1, page_size=999)
    assert out["page_size"] == metrics_service._ASSET_POOL_MAX_PAGE_SIZE
    assert out["page_size"] <= 50, "档位/入参都不得超过服务端上限"


def test_legacy_mode_returns_groups_and_never_suggestions(monkeypatch):
    _install(monkeypatch)
    out = metrics_service.asset_pool()
    assert "metrics" in out and "dimensions" in out and "terms" in out
    assert out["metrics"][0]["name"] == "案例数"
    assert "suggestions" not in out, "候选真源是进化中心，不得混进字典资产池"
    assert "items" not in out and "total" not in out, "不挂分页条的选择面不发分页信封"


def test_queue_and_keyword_filters_push_down_to_sql(monkeypatch):
    calls = _install(monkeypatch)
    metrics_service.asset_pool(queue="metric", q="订单", page=1, page_size=20)

    count_sql, count_params = calls[0]
    item_sql, item_params = calls[1]
    # 当前页只查 metric 队列，但计数仍覆盖全部队列（徽章不随当前队列抖动）
    assert "FROM adh_metrics" in item_sql and "FROM adh_dimensions" not in item_sql
    assert "FROM adh_metrics" in count_sql and "FROM adh_dimensions" in count_sql
    assert "%订单%" in item_params and "%订单%" in count_params
    assert item_sql.count("LIKE %s") == count_sql.count("LIKE %s")


def test_invalid_queue_and_pagination_values_are_explicit(monkeypatch):
    calls = _install(monkeypatch)
    with pytest.raises(ValueError):
        metrics_service.asset_pool(queue="bogus", page_size=10)
    with pytest.raises(ValueError):
        metrics_service.asset_pool(page=0, page_size=10)
    with pytest.raises(ValueError):
        metrics_service.asset_pool(page="x", page_size=10)
    assert not calls, "非法入参不得触达数据库"
