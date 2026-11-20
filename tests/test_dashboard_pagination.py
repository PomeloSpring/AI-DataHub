"""系统配置-看板管理页服务端**真分页**回归（禁止"拉全量再前端切片"的假分页）。

口径（.qoder/rules/list-pagination-server-side.md）：
* 传了 page_size 才返回 {items,total,page,page_size}；不传仍回全量数组（编辑器切板/下钻选择器要全量在手）；
* 行总数由服务端过滤后 COUNT 得到（不是当前页长度），搜索/状态/标签/收藏全部下推 SQL；
* 偏移量由页号在服务端算出（调用方不递 offset），每页行数夹到上限，排序含唯一 id 兜底（不重行/漏行）；
* 分页清单只为**当前页**看板取图表，且只取渲染所需瘦列（sql_query/data_cache 属单看板详情载荷）。
"""
import importlib
import json

import pytest


def _dash(**over):
    row = {"id": 1, "name": "订单看板", "description": "订单概览", "layout": None,
           "filters": {}, "params": [], "tags": '["订单"]', "status": "enabled",
           "owner_id": 7, "is_public": 0, "is_default": 0, "carousel_interval": 0,
           "sort_order": 0, "created_at": "2026-01-01 00:00:00",
           "updated_at": "2026-01-02 00:00:00", "workspace_id": 3, "i18n": None}
    row.update(over)
    return row


def _chart(**over):
    row = {"id": 11, "dashboard_id": 1, "name": "趋势", "chart_type": "line",
           "config": "{}", "position": '{"x":0,"y":0,"w":4,"h":3}', "i18n": None}
    row.update(over)
    return row


class _FakeCursor:
    """按 SQL 关键字路由的假游标：全量记录 (sql, params) 供断言。"""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.db.log.append((" ".join(sql.split()), list(params or [])))
        self.db.current = " ".join(sql.split())

    def fetchone(self):
        s = self.db.current
        if "COUNT(*) AS total" in s:
            return {"total": self.db.total}
        return None

    def fetchall(self):
        s = self.db.current
        if "FROM adh_charts" in s:
            ids = set(self.db.log[-1][1])
            return [dict(c) for c in self.db.charts if c["dashboard_id"] in ids]
        if "SELECT id, is_default, sort_order FROM adh_dashboards" in s:
            return [dict(r) for r in self.db.order_rows]
        if "SELECT tags FROM adh_dashboards" in s:
            return [{"tags": r.get("tags")} for r in self._dash_rows()]
        if "FROM adh_dashboards" in s:
            return self._dash_rows()
        return []

    def _dash_rows(self):
        """模拟 WHERE 里的 `id IN (可见集)` 下推：假库不能对越权行照单返回。"""
        vis = self.db.visible
        return [dict(r) for r in self.db.rows if vis is None or r["id"] in vis]


class _FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _FakeCursor(self.db)

    def commit(self):
        self.db.commits += 1

    def close(self):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    ds = importlib.import_module("backend.modules.viz.services.dashboard_service")
    db = type("DB", (), {})()
    db.rows, db.charts, db.log, db.total = [], [], [], 0
    db.order_rows, db.commits = [], 0
    db.visible = None
    monkeypatch.setattr(ds, "get_metadata_conn", lambda: _FakeConn(db))
    monkeypatch.setattr(ds, "visible_dashboard_ids", lambda *a, **k: db.visible)
    monkeypatch.setattr(ds, "_invalidate_dashboard_cache", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_invalidate_dashboard_payload", lambda *a, **k: None)
    return ds, db


def test_page_envelope_total_from_count_and_filters_pushed_down(fake_db):
    ds, db = fake_db
    db.rows = [_dash(id=101), _dash(id=102)]
    db.total = 46
    db.charts = [_chart(id=11, dashboard_id=101), _chart(id=12, dashboard_id=102)]

    out = ds.dashboard_service.list_dashboards_page(
        7, 3, "admin", search="订单%_x", status="enabled", tags=["订单", "  "],
        favorite_ids=[102, 999], page=2, page_size=20)

    assert set(out) == {"items", "total", "page", "page_size"}
    assert out["total"] == 46 and out["page"] == 2 and out["page_size"] == 20
    # total 来自 COUNT，不是当前页长度
    assert out["total"] != len(out["items"])

    count_sql, count_params = db.log[0]
    page_sql, page_params = db.log[1]
    # 筛选全部下推 SQL，且 COUNT 与页 SQL 共用同一份 WHERE（两份谓词必然漂移）
    assert "COUNT(*) AS total FROM adh_dashboards WHERE" in count_sql
    assert count_sql.split(" WHERE ")[1] == page_sql.split(" WHERE ")[1].split(" ORDER BY ")[0]
    for frag in ("workspace_id = %s", "(name LIKE %s OR description LIKE %s)", "status = %s",
                 "JSON_CONTAINS(COALESCE(tags, '[]'), %s)"):
        assert frag in count_sql and frag in page_sql
    assert page_params[:len(count_params)] == count_params
    # 搜索词里的 LIKE 元字符必须转义，否则 % _ 变成通配符（误命中且无从排查）
    assert "%订单\\%\\_x%" in count_params
    # 标签多选 = OR；空标签被丢掉，不产生空条件
    assert count_sql.count("JSON_CONTAINS") == 1 and json.dumps("订单", ensure_ascii=False) in count_params
    # 收藏只当过滤条件，且必须落在可见集内（这里 admin 可见集=None → 原样，但 999 不在清单也不会报错）
    assert "id IN (%s,%s)" in count_sql

    # 真分页：LIMIT/OFFSET 由服务端按页号算，排序含唯一 id 兜底
    assert "LIMIT %s OFFSET %s" in page_sql
    assert page_params[-2:] == [20, 20]
    assert page_sql.endswith("ORDER BY is_default DESC, sort_order ASC, updated_at DESC, id DESC"
                             " LIMIT %s OFFSET %s")


def test_page_fetches_charts_only_for_current_page_slim_columns(fake_db):
    ds, db = fake_db
    db.rows = [_dash(id=101)]
    db.total = 1
    db.charts = [_chart(id=11, dashboard_id=101), _chart(id=12, dashboard_id=999)]

    out = ds.dashboard_service.list_dashboards_page(7, 3, "admin", page=1, page_size=5)

    charts_sql, charts_params = db.log[-1]
    assert charts_params == [101], "只为本页看板取图表"
    # 瘦列：口径正文（sql_query/data_cache/semantic_query）属单看板详情载荷，不得进清单
    for col in ("sql_query", "data_cache", "semantic_query"):
        assert col not in charts_sql
    for col in ("chart_type", "config", "position", "i18n"):
        assert col in charts_sql
    assert out["items"][0]["charts"][0]["id"] == 11
    assert len(out["items"][0]["charts"]) == 1
    # 清单仍要带前端判定字段（可写性由服务端裁决）
    assert out["items"][0]["editable"] is True


def test_page_clamps_size_and_page_number(fake_db):
    ds, db = fake_db
    db.rows, db.total = [], 0
    assert ds.dashboard_service.list_dashboards_page(7, 3, "admin", page_size=5000)["page_size"] \
        == ds._DASHBOARD_PAGE_SIZE_MAX
    assert ds.dashboard_service.list_dashboards_page(7, 3, "admin", page_size=0)["page_size"] == 20
    out = ds.dashboard_service.list_dashboards_page(7, 3, "admin", page=0, page_size=10)
    assert out["page"] == 1


def test_empty_total_short_circuits_without_chart_query(fake_db):
    ds, db = fake_db
    db.rows, db.total = [], 0
    out = ds.dashboard_service.list_dashboards_page(7, 3, "admin", search="没有这块板")
    assert out == {"items": [], "total": 0, "page": 1, "page_size": 20}
    assert len(db.log) == 1, "COUNT=0 即返回，不再发清单/图表查询"


def test_invisible_and_empty_favorite_intersection_are_fail_closed(fake_db):
    ds, db = fake_db
    db.rows, db.total = [_dash()], 1
    # 无可见看板（非 admin 且角色未授权）→ 空清单，不发任何查询
    db.visible = set()
    assert ds.dashboard_service.list_dashboards_page(7, 3, "analyst")["total"] == 0
    assert db.log == []
    # 收藏 ids 与可见集无交集 → 同样空清单（服务端不信任前端递来的 id）
    db.visible = {1}
    out = ds.dashboard_service.list_dashboards_page(7, 3, "analyst", favorite_ids=[2, 3])
    assert out["items"] == [] and out["total"] == 0 and db.log == []


def test_unknown_status_rejected(fake_db):
    ds, db = fake_db
    with pytest.raises(ValueError):
        ds.dashboard_service.list_dashboards_page(7, 3, "admin", status="随便写")


def test_full_list_keeps_bare_array_shape(fake_db):
    """全量档（编辑器切板/下钻选择器）返回形态不变，只是排序补了唯一 tiebreaker。"""
    ds, db = fake_db
    db.rows, db.visible = [_dash()], None
    out = ds.dashboard_service.list_dashboards(7, 3, "admin")
    assert isinstance(out, list) and out[0]["id"] == 1
    list_sql, _ = db.log[0]
    assert "LIMIT" not in list_sql and list_sql.endswith(
        "ORDER BY is_default DESC, sort_order ASC, updated_at DESC, id DESC")


def test_tags_facet_is_union_over_visible_scope(fake_db):
    ds, db = fake_db
    db.rows = [_dash(tags='["订单", "销售"]'), _dash(id=2, tags='["库存"]'), _dash(id=3, tags=None)]
    # 并集去重 + 稳定排序（码点序），不随 SQL 返回顺序抖动
    assert ds.dashboard_service.list_dashboard_tags(7, 3, "admin") == ["库存", "订单", "销售"]
    # 候选同样受可见域约束（不得把越权看板的标签外泄成选项）
    db.visible = {2}
    assert ds.dashboard_service.list_dashboard_tags(7, 3, "analyst") == ["库存"]


def test_move_swaps_neighbors_without_touching_updated_at(fake_db):
    ds, db = fake_db
    db.order_rows = [{"id": 1, "is_default": 0, "sort_order": 10},
                     {"id": 2, "is_default": 0, "sort_order": 20},
                     {"id": 3, "is_default": 0, "sort_order": 30}]
    out = ds.dashboard_service.move_dashboard(7, 2, -1, 3, "admin")
    assert out["moved"] is True
    updates = [c for c in db.log if c[0].startswith("UPDATE adh_dashboards SET sort_order")]
    assert [u[1] for u in updates] == [[10, 2], [20, 1]]
    # updated_at 显式钉住：否则移动会把看板甩到「更新时间」序的另一页
    assert all("updated_at=updated_at" in u[0] for u in updates)
    assert db.commits == 1


def test_move_renumbers_when_sort_order_is_tied(fake_db):
    """存量并列（老数据 sort_order 全 0）时交换两值等于没换 → 按全序重编一次消解并列。"""
    ds, db = fake_db
    db.order_rows = [{"id": i, "is_default": 0, "sort_order": 0} for i in (1, 2, 3)]
    assert ds.dashboard_service.move_dashboard(7, 3, -1, 3, "admin")["moved"] is True
    updates = [c[1] for c in db.log if c[0].startswith("UPDATE adh_dashboards SET sort_order")]
    assert updates == [[1000, 1], [1001, 3], [1002, 2]]


def test_move_refuses_boundary_and_pinned_zone(fake_db):
    ds, db = fake_db
    db.order_rows = [{"id": 1, "is_default": 1, "sort_order": 10},
                     {"id": 2, "is_default": 0, "sort_order": 20}]
    # 置顶（默认看板）与非置顶之间不互换，越界与越区都要显式回原因（不是静默成功）
    assert ds.dashboard_service.move_dashboard(7, 2, -1, 3, "admin") == {"moved": False, "reason": "pinned_zone"}
    assert ds.dashboard_service.move_dashboard(7, 2, 1, 3, "admin") == {"moved": False, "reason": "boundary"}
    assert ds.dashboard_service.move_dashboard(7, 404, -1, 3, "admin") == {"moved": False, "reason": "not_found"}
    assert not [c for c in db.log if c[0].startswith("UPDATE")], "被拒时不得落库"
    with pytest.raises(ValueError):
        ds.dashboard_service.move_dashboard(7, 1, 0, 3, "admin")


def test_move_locates_neighbor_within_callers_visible_list(fake_db):
    """邻居按「这个用户看到的同一份清单」定位，否则可能与一块它看不见的板交换（移动后纹丝不动）。"""
    ds, db = fake_db
    db.order_rows = [{"id": 1, "is_default": 0, "sort_order": 10}]
    db.visible = {1}
    assert ds.dashboard_service.move_dashboard(7, 1, 1, 3, "analyst")["reason"] == "boundary"
    scope_sql, scope_params = db.log[0]
    assert "id IN (%s)" in scope_sql and scope_params == [3, 1]


def test_move_with_empty_visible_set_fails_loud_not_bad_sql(fake_db):
    """空可见集不得拼出 `id IN ()` 畸形 SQL：显式回 not_found，且一条查询都不发。"""
    ds, db = fake_db
    db.visible = set()
    assert ds.dashboard_service.move_dashboard(7, 1, 1, 3, "analyst") == {"moved": False, "reason": "not_found"}
    assert db.log == [] and db.commits == 0


def test_endpoint_branches_on_page_size(monkeypatch):
    """不传 page_size → 裸数组；传 page_size → 分页信封（两档显式，非静默）。"""
    from backend.modules.viz.api import dashboard as api
    svc = importlib.import_module("backend.modules.viz.services.dashboard_service").dashboard_service
    monkeypatch.setattr(svc, "list_dashboards", lambda *a, **k: [_dash()])
    monkeypatch.setattr(svc, "list_dashboards_page",
                        lambda *a, **k: {"items": [_dash()], "total": 1, "page": 1, "page_size": 20})
    full = api.list_dashboards_endpoint(user={"user_id": 7, "role": "admin"}, workspace_id=3,
                                        search="", status=None, tags=None, favorite_ids=None,
                                        page=1, page_size=None)
    assert isinstance(full, list)
    paged = api.list_dashboards_endpoint(user={"user_id": 7, "role": "admin"}, workspace_id=3,
                                         search="订单", status="enabled", tags="订单,销售",
                                         favorite_ids="1,2", page=2, page_size=20)
    assert isinstance(paged, dict) and set(paged) == {"items", "total", "page", "page_size"}


def test_move_endpoint_requires_editability(monkeypatch):
    """写动作唯一裁决点：非属主/非 admin 一律 403，且不调用服务层。"""
    from fastapi import HTTPException
    from backend.modules.viz.api import dashboard as api
    svc = importlib.import_module("backend.modules.viz.services.dashboard_service").dashboard_service
    called = []
    monkeypatch.setattr(api, "check_dashboard_editable", lambda *a, **k: False)
    monkeypatch.setattr(svc, "move_dashboard", lambda *a, **k: called.append(a) or {"moved": True})
    with pytest.raises(HTTPException) as err:
        api.move_dashboard_endpoint(req=api.MoveRequest(dashboard_id=5, direction=-1),
                                    user={"user_id": 7, "role": "analyst"}, workspace_id=3)
    assert err.value.status_code == 403 and called == []


def test_move_endpoint_surfaces_reason_as_conflict(monkeypatch):
    """边界/越区被拒 → 409 + 可读原因（前端原样透出，不退化成“移动失败”）。"""
    from fastapi import HTTPException
    from backend.modules.viz.api import dashboard as api
    svc = importlib.import_module("backend.modules.viz.services.dashboard_service").dashboard_service
    monkeypatch.setattr(api, "check_dashboard_editable", lambda *a, **k: True)
    monkeypatch.setattr(svc, "move_dashboard",
                        lambda *a, **k: {"moved": False, "reason": "pinned_zone"})
    with pytest.raises(HTTPException) as err:
        api.move_dashboard_endpoint(req=api.MoveRequest(dashboard_id=5, direction=-1),
                                    user={"user_id": 7, "role": "admin"}, workspace_id=3)
    assert err.value.status_code == 409 and "置顶" in err.value.detail
