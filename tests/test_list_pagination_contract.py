"""列表分页契约 —— 敏感字段清单与数据安全策略清单必须服务端分页.

回归背景（用户反馈「敏感数据管理、数据安全策略没有做分页功能」）:
  1. GET /security/sensitive-fields 只回裸数组、无 total → 前端分页条算不出页数，
     total=0 直接 hideWhenSingle 隐藏，看着就是「没做分页」；
  2. 数据安全策略行/列/表三个页签共用一份「整张清单的服务端分页」，再在前端按
     policy_type 筛 → 每页 20 条被摊到三个页签（每页签只有几条），且「共 N 条」
     是全类型总数，与页签清单永远对不上，翻页还丢数据。
     收敛：类型筛选下推 SQL（policy_types 参数），每个页签一份独立分页清单。
"""
import pytest

from backend.core import rls_service as rls_svc_mod
from backend.modules.auth.api import rls as rls_api
from backend.modules.gov.api import security as security_api


class _FakeCursor:
    """按 execute 顺序回放结果集（COUNT 先于 SELECT）。"""

    def __init__(self, results):
        self._results = list(results)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, list(params or [])))

    def fetchone(self):
        return self._results[0]

    def fetchall(self):
        return self._results[-1]


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def close(self):
        pass


# ── RLS 策略清单：policy_types 下推 + 分页 ──────────────────────────

def test_list_policies_pushes_policy_types_into_sql(monkeypatch):
    cur = _FakeCursor([{"total": 1}, [{"id": 1, "policy_type": "column"}]])
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))

    out = rls_svc_mod.rls_service.list_policies(
        0, None, None, page=2, size=20, policy_types=["column"])

    count_sql, count_params = cur.executed[0]
    page_sql, page_params = cur.executed[1]
    assert "policy_type IN (%s)" in count_sql, count_sql
    assert "policy_type IN (%s)" in page_sql, page_sql
    assert count_params == ["column"] and count_params == ["column"]
    # 分页必须落到 LIMIT/OFFSET，且偏移按页码算
    assert "LIMIT %s OFFSET %s" in page_sql, page_sql
    assert page_params[-2:] == [20, 20], page_params
    assert out == {"total": 1, "items": [{"id": 1, "policy_type": "column"}]}


def test_list_policies_without_types_keeps_legacy_sql(monkeypatch):
    """未传 policy_types（角色绑定选择器等调用方）行为零变化。"""
    cur = _FakeCursor([{"total": 0}, []])
    monkeypatch.setattr(rls_svc_mod, "get_metadata_conn", lambda: _FakeConn(cur))
    rls_svc_mod.rls_service.list_policies(0, None, None, page=1, size=20)
    assert "policy_type IN" not in cur.executed[0][0]
    assert cur.executed[1][1][-2:] == [20, 0]


def test_api_parses_policy_types_query(monkeypatch):
    """路由层把逗号串拆成列表下推 SQL（行页签传 row,both 含遗留混合类型）。"""
    seen = {}

    class _Svc:
        def list_policies(self, workspace_id, datasource_id=None, table_name=None,
                          page=1, size=20, policy_types=None):
            seen.update(workspace_id=workspace_id, types=policy_types, page=page, size=size)
            return {"total": 0, "items": []}

    monkeypatch.setattr(rls_svc_mod, "rls_service", _Svc())
    out = _client().get("/rls-policies", params={"policy_types": "row, both", "page": 3, "size": 50})
    assert out.status_code == 200, out.text
    assert out.json()["total"] == 0
    assert seen["types"] == ["row", "both"], seen
    assert (seen["page"], seen["size"]) == (3, 50)


def test_api_rejects_oversize_page_size(monkeypatch):
    """每页上限 100（与全站 20/50/100 档位一致），不得被前端放大成全量拉取。"""
    class _Svc:
        def list_policies(self, *a, **kw):
            raise AssertionError("越界 size 不应走到服务层")

    monkeypatch.setattr(rls_svc_mod, "rls_service", _Svc())
    assert _client().get("/rls-policies", params={"size": 5000}).status_code == 422


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.common.auth import get_current_user

    app = FastAPI()
    app.include_router(rls_api.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": 1, "role": "admin"}
    return TestClient(app)


# ── 敏感字段清单：{items, total} + LIMIT/OFFSET ─────────────────────

def test_sensitive_fields_returns_items_and_total(monkeypatch):
    captured = []

    def _fake_execute_query(sql, params=None, fetchone: bool = False):
        captured.append((sql, list(params or [])))
        if sql.lstrip().upper().startswith("SELECT COUNT(*)"):
            return [{"total": 37}]
        return [{"id": i} for i in range(5)]

    monkeypatch.setattr(security_api, "execute_query", _fake_execute_query)

    out = security_api.list_sensitive_fields(
        workspace_id=0, datasource_id=None, sensitivity_level=None,
        page=2, size=20)

    assert set(out) >= {"items", "total"}, out
    assert out["total"] == 37, out
    assert len(out["items"]) == 5
    page_sql, page_params = captured[1]
    assert "LIMIT %s OFFSET %s" in page_sql, page_sql
    assert page_params[-2:] == [20, 20], page_params


def test_sensitive_fields_size_query_bounds(monkeypatch):
    """size 默认 20 / 限定 1..100 —— 与全站统一分页档位同源（走 HTTP 校验）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # 合法区间会进 handler， mock 掉 DB；越界（422）在校验层就拦下
    monkeypatch.setattr(
        security_api, "execute_query",
        lambda sql, params=None, fetchone=False: [{"total": 0}] if "COUNT" in sql.upper() else [])

    app = FastAPI()
    app.include_router(security_api.router, prefix="/api/security")
    client = TestClient(app)

    import inspect

    sig = inspect.signature(security_api.list_sensitive_fields)
    assert sig.parameters["size"].default.default == 20, "默认每页必须与全站统一档位 20 一致"

    assert client.get("/api/security/sensitive-fields", params={"size": 0}).status_code == 422
    assert client.get("/api/security/sensitive-fields", params={"size": 5000}).status_code == 422
    assert client.get("/api/security/sensitive-fields", params={"size": 100}).status_code == 200
