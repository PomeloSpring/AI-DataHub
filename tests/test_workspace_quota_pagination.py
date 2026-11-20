"""用户工作空间统管清单的服务端**真分页**回归（禁止"拉全量再前端切片"的假分页）。

口径（.qoder/rules/list-pagination-server-side.md）：
* 返回契约 {items,total,page,page_size}，total 由服务端 COUNT 得到（不是当前页长度）；
* 搜索下推 SQL 且 COUNT 与当前页共用同一份谓词，偏移量由页号算出（调用方不递 offset）；
* 每页行数服务端夹到 [1, _QUOTA_PAGE_SIZE_MAX]，排序含唯一主键 tiebreaker（u.id）；
* 清单只回配额，不回空间数（用户侧自建入口已关）也不回逐空间用量；
  磁盘用量由 API 层按**页内用户**的空间算（`list_user_workspace_ids` 一次 IN 查询，不 N+1），
  逐空间明细在详情里按所选用户单独取。
"""
from __future__ import annotations

import importlib

from backend.common import config

MB = 1024 ** 2


class _RecordingCursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.calls.append((" ".join(sql.split()), list(params or [])))
        self._rows = self._conn.route(" ".join(sql.split()))
        return None

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    """按 SQL 片段路由返回行；calls 记录全部执行（含参数），供断言下推与偏移量。"""

    def __init__(self, routes, calls):
        self._routes = routes
        self.calls = calls

    def route(self, sql):
        for pattern, rows in self._routes:
            if pattern in sql:
                return rows(sql) if callable(rows) else rows
        return []

    def cursor(self):
        return _RecordingCursor(self)

    def close(self):
        return None


def _patch_conn(monkeypatch, routes):
    rs = importlib.import_module("backend.core.role_service")
    calls = []

    def factory():
        return _FakeConn(routes, calls)

    monkeypatch.setattr(rs, "get_metadata_conn", factory)
    return calls


def _user_row(**over):
    row = {"user_id": 1, "username": "zhangsan", "registered_max_workspaces": None,
           "registered_disk_quota_bytes": None}
    row.update(over)
    return row


# ── ① 契约与偏移量：total 来自 COUNT，LIMIT/OFFSET 由页号算 ──────────

def test_quota_page_total_from_count_not_page_length(monkeypatch):
    routes = [
        ("COUNT(*) AS cnt", [{"cnt": 7}]),
        ("FROM adh_users u", [_user_row(user_id=i) for i in (11, 12)]),
    ]
    calls = _patch_conn(monkeypatch, routes)

    from backend.core.role_service import role_service
    out = role_service.list_workspace_quotas_page(page=3, page_size=2, q="li")

    assert set(out) == {"items", "total", "page", "page_size"}
    assert out["total"] == 7, "total 必须是过滤后的全量行数，不是当前页长度"
    assert out["page"] == 3 and out["page_size"] == 2
    assert len(out["items"]) == 2

    count_sql, count_params = calls[0]
    assert "COUNT(*)" in count_sql and "LIKE" in count_sql, "搜索必须下推 COUNT"
    assert count_params == ["%li%"]

    page_sql, page_params = calls[1]
    assert "LIMIT %s OFFSET %s" in page_sql
    assert page_params == ["%li%", 2, 4], "偏移量由服务端按 (page-1)*page_size 算，不接受调用方递 offset"
    assert "workspace_count" not in page_sql, "清单不再数空间（列已移除，不留无用子查询）"


def test_quota_page_orders_by_unique_id_and_shares_filters():
    """排序含唯一主键兜底 + COUNT 与页查询同源（防止相邻页重行/漏行、两套谓词漂移）。"""
    from backend.core.role_service import role_service
    where, params = role_service._quota_filters("  王  ")
    assert where == " WHERE u.username LIKE %s" and params == ["%王%"]
    assert role_service._quota_filters("") == ("", [])
    assert role_service._QUOTA_ORDER_BY.endswith("u.id")


# ── ② 服务端夹死每页行数与页号 ──────────────────────────────────────

def test_quota_page_clamps_page_and_size(monkeypatch):
    routes = [("COUNT(*) AS cnt", [{"cnt": 0}]), ("FROM adh_users u", [])]
    calls = _patch_conn(monkeypatch, routes)

    from backend.core.role_service import role_service
    out = role_service.list_workspace_quotas_page(page=0, page_size=10_000)
    assert out["page"] == 1 and out["page_size"] == role_service._QUOTA_PAGE_SIZE_MAX
    assert calls[1][1] == [role_service._QUOTA_PAGE_SIZE_MAX, 0]

    out = role_service.list_workspace_quotas_page(page_size=None)
    assert out["page_size"] == 20, "缺省每页 20（与前端档位候选同源）"


# ── ③ 生效配额：登记行是权威、无行取系统缺省（与拦截门同源） ─────────

def test_quota_page_effective_value_and_default_flag(monkeypatch):
    default_disk = config.WORKSPACE_DISK_QUOTA_DEFAULT_BYTES
    routes = [
        ("COUNT(*) AS cnt", [{"cnt": 3}]),
        ("FROM adh_users u", [
            _user_row(user_id=1),  # 无登记行 → 系统缺省
            _user_row(user_id=2, registered_max_workspaces=9,
                      registered_disk_quota_bytes=88 * MB),
            _user_row(user_id=3, registered_max_workspaces=5, registered_disk_quota_bytes=0),
        ]),
    ]
    _patch_conn(monkeypatch, routes)

    from backend.core.role_service import role_service
    items = role_service.list_workspace_quotas_page()["items"]

    assert items[0]["disk_quota_bytes"] == default_disk and items[0]["quota_is_default"] is True
    assert items[1]["disk_quota_bytes"] == 88 * MB and items[1]["max_workspaces"] == 9
    assert items[1]["quota_is_default"] is False
    # 显式登记 0 = 管理员"不设限"逃生口，不得被当成缺省，也不得被换成缺省值
    assert items[2]["disk_quota_bytes"] == 0 and items[2]["quota_is_default"] is False
    assert "workspace_count" not in items[0], "空间数已不再下发（用户侧自建入口关闭，展示它属空承诺）"
    assert "disk_usage_bytes" not in items[0], "清单不做递归扫盘，用量由 API 层按页内用户补"
    assert "workspaces" not in items[0]


# ── ④ 详情: 只取所选用户的空间清单（磁盘用量成本收敛到这里） ────────

def test_quota_detail_scopes_workspaces_to_selected_user(monkeypatch):
    routes = [
        ("SELECT username FROM adh_users WHERE id", [{"username": "lisi"}]),
        ("FROM adh_workspaces WHERE owner_id", [
            {"id": 301, "name": "默认空间", "is_default": 1},
            {"id": 302, "name": "临时空间", "is_default": 0},
        ]),
        ("FROM adh_user_workspace_quota WHERE user_id", [{"max_workspaces": 7, "disk_quota_bytes": 64 * MB}]),
    ]
    calls = _patch_conn(monkeypatch, routes)

    from backend.core.role_service import role_service
    detail = role_service.get_user_workspace_quota_detail(42)

    assert detail["username"] == "lisi"
    assert detail["max_workspaces"] == 7 and detail["disk_quota_bytes"] == 64 * MB
    assert detail["quota_is_default"] is False
    assert [w["id"] for w in detail["workspaces"]] == [301, 302]
    assert all("FROM adh_users u" not in c[0] for c in calls), "详情不得走全库清单查询"
    assert [42] in [c[1] for c in calls], "空间清单按所选用户过滤"


# ── ⑤ 页内用户的空间 id: 一次 IN 查询（磁盘用量的输入，不得 N+1） ────

def test_quota_page_workspace_ids_single_in_query(monkeypatch):
    routes = [("SELECT owner_id, id FROM adh_workspaces WHERE owner_id IN", [
        {"owner_id": 11, "id": 301}, {"owner_id": 11, "id": 302}, {"owner_id": 12, "id": 303},
    ])]
    calls = _patch_conn(monkeypatch, routes)

    from backend.core.role_service import role_service
    grouped = role_service.list_user_workspace_ids([11, 12])

    assert grouped == {11: [301, 302], 12: [303]}
    assert len(calls) == 1, "页内用户一次取回，不得逐用户各查一遍"
    assert calls[0][1] == [11, 12]

    calls.clear()
    assert role_service.list_user_workspace_ids([]) == {}
    assert calls == [], "空入参不发 SQL"


def test_quota_detail_missing_user_returns_none(monkeypatch):
    _patch_conn(monkeypatch, [("SELECT username FROM adh_users WHERE id", [])])
    from backend.core.role_service import role_service
    assert role_service.get_user_workspace_quota_detail(999) is None, \
        "用户不存在必须可区分（调用方给 404），不得回空清单冒充『该用户没有空间』"
