"""看板组合排序（组合之间 + 组合之内）离线回归。

锁定的契约：
  - **组内顺序 = 编排顺序**：`build_visible_groups` 必须按 `adh_dashboard_group_items.sort`
    输出组内看板，不得回退成目录全局序（is_default → sort_order）—— 否则管理员在组合里
    排出来的顺序对「点组合进哪块板 / 组内切换条左右 / 大屏轮播次序」完全不算数（历史缺陷）；
  - 可见集只做交集过滤（fail-closed），不参与也不改变相对次序；
  - `reorder_groups` 只写 sort 一列（整行 UPDATE 会把 name/description 抹空，与既有
    「幂等 upsert 覆盖列」同一类缺陷）；组合已被删除时整批不写并显式报错；
  - 编辑组合只改名称/说明 **不得**把 sort 抬回 0（= 首位）；新建组合默认落末尾（不插队）。
"""
import os
import sys
import importlib
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backend.common.db as db_pkg                                            # noqa: E402
from backend.modules.viz.api import dashboard as api                          # noqa: E402

# `services/__init__` 把同名单例重导出为实例, `from ... import dashboard_service` 拿到的是
# DashboardService 对象而不是模块, monkeypatch 会直接 AttributeError —— 显式取真模块对象
svc = importlib.import_module("backend.modules.viz.services.dashboard_service")

ADMIN = {"user_id": 1, "username": "admin01", "role": "admin"}


def _card(did: int, *, sort_order: int, is_default: bool = False) -> dict:
    """目录行：刻意让全局序(is_default → sort_order)与编排顺序不一致，用来暴露排序被抹平。"""
    return {"id": did, "name": f"看板 {did}", "status": "enabled", "is_default": is_default,
            "sort_order": sort_order, "description": "", "chart_count": 1, "tags": []}


# ── 假 DB: 记录写语句 + 按 SQL 关键字回放读结果 ─────────────────────────────

@pytest.fixture
def fake_db(monkeypatch):
    """把 backend.common.db 的读写口换成记录器（api/service 里的函数内 import 调用时取补丁）。"""
    log = []
    box = {"rows": lambda sql: []}

    def _query(sql, params=None, fetchone=False):
        log.append((sql, tuple(params or ())))
        rows = box["rows"](sql)
        return (rows[0] if rows else None) if fetchone else rows

    def _write(sql, params=None):
        log.append((sql, tuple(params or ())))
        return 1

    monkeypatch.setattr(db_pkg, "execute_query", _query)
    monkeypatch.setattr(db_pkg, "execute_write", _write)
    return SimpleNamespace(log=log, box=box)


class _Cur:
    def __init__(self, conn):
        self._conn, self._sql, self.rowcount = conn, "", 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sql = sql
        self._conn.log.append((sql, tuple(params or ())))

    def fetchone(self):
        return {"max_sort": self._conn.max_sort} if "MAX(sort)" in self._sql else None

    @property
    def lastrowid(self):
        return self._conn.new_id


class _Conn:
    """假连接：service 的 get_metadata_conn 与 api 的 DBConnection 共用同一个记录口。"""

    def __init__(self, *, max_sort=0, new_id=77):
        self.log, self.committed, self.max_sort, self.new_id = [], False, max_sort, new_id

    def cursor(self):
        return _Cur(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        self.committed = True

    def close(self):
        pass


@pytest.fixture
def fake_conn(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(svc, "get_metadata_conn", lambda: conn)
    monkeypatch.setattr(api, "DBConnection", lambda: conn)
    return conn


@pytest.fixture
def payload_invalidations(monkeypatch):
    """写路径的载荷缓存失效在单测里打桩并记账（顺序变了必须让浏览端回源，不能等 TTL）。"""
    calls = []
    noop = lambda *a, **k: calls.append(True)  # noqa: E731
    monkeypatch.setattr(svc, "_invalidate_dashboard_payload", noop)
    monkeypatch.setattr(api, "_invalidate_dashboard_payload", noop)
    return calls


def _patch_catalog(monkeypatch, *, cards, struct):
    """目录 = 共享卡片载荷(按全局序) ∩ 实时可见集; 组结构 = 编排事实。"""
    visible = {c["id"] for c in cards}

    def _visible(user_id, user_role=""):
        return visible

    def _catalog_cards(vis, user_id, user_role="", is_admin=None):
        return [dict(c) for c in cards if int(c["id"]) in (vis or set())]

    monkeypatch.setattr(svc, "visible_dashboard_ids", _visible)
    monkeypatch.setattr(svc, "catalog_cards", _catalog_cards)
    monkeypatch.setattr(svc, "_groups_structure", lambda: struct)
    monkeypatch.setattr(svc, "_user_role_ids", lambda user_id: [9])


# ── 读路径: 顺序即编排事实 ──────────────────────────────────────────────────

def test_group_boards_follow_arrangement_not_global_catalog_order(monkeypatch):
    """编排顺序 (3 → 1 → 2) 必须原样下发。

    目录全局序是 2(默认) → 1 → 3：若读路径按它过滤，用户点组合先进「看板 2」、轮播从它起，
    管理员在组合里排的顺序等于白排（这正是修复前的行为）。
    """
    cards = [_card(1, sort_order=2), _card(2, sort_order=1, is_default=True), _card(3, sort_order=3)]
    _patch_catalog(monkeypatch, cards=cards, struct={
        "groups": [{"id": 5, "name": "经营组", "description": "", "sort": 10}],
        "items": {"5": [3, 1, 2]},
        "role_groups": {"9": [5]},
    })

    out = svc.build_visible_groups(7, "analyst")

    assert [g["id"] for g in out] == [5, 0]
    assert [d["id"] for d in out[0]["dashboards"]] == [3, 1, 2]
    assert out[1]["dashboards"] == []          # 全部已入组 → 未分组为空


def test_group_member_order_survives_visibility_intersection(monkeypatch):
    """可见集只负责剔除：撤掉中间那块板，剩下的相对次序不得变、被撤的不得出现在任何清单。"""
    cards = [_card(1, sort_order=2), _card(2, sort_order=1), _card(3, sort_order=3)]
    _patch_catalog(monkeypatch, cards=cards, struct={
        "groups": [{"id": 5, "name": "经营组", "description": "", "sort": 10}],
        "items": {"5": [3, 2, 1]},
        "role_groups": {"9": [5]},
    })
    monkeypatch.setattr(svc, "visible_dashboard_ids", lambda user_id, user_role="": {1, 3})

    out = svc.build_visible_groups(7, "analyst")

    assert [d["id"] for d in out[0]["dashboards"]] == [3, 1]
    assert out[1]["dashboards"] == []          # 不可见的看板不是「未分组」，是压根不可见


def test_group_card_order_follows_group_sort(monkeypatch):
    """组合之间的顺序取组表 sort 序（管理端组合卡上移/下移写入），前端不再二次排序。"""
    cards = [_card(1, sort_order=1)]
    _patch_catalog(monkeypatch, cards=cards, struct={
        # _groups_structure 的 groups 已按 ORDER BY sort,id 读出: 后建的组被排到了前面
        "groups": [{"id": 6, "name": "后建的组", "description": "", "sort": 10},
                   {"id": 5, "name": "先建的组", "description": "", "sort": 20}],
        "items": {"5": [1]},
        "role_groups": {"9": [5, 6]},
    })

    out = svc.build_visible_groups(7, "analyst")

    assert [g["id"] for g in out[:2]] == [6, 5]
    assert [g["sort"] for g in out[:2]] == [10, 20]


# ── 写路径: 组合顺序入库 ────────────────────────────────────────────────────

def test_reorder_groups_writes_only_sort(monkeypatch, fake_conn, payload_invalidations, fake_db):
    fake_db.box["rows"] = lambda sql: [{"id": 5}, {"id": 6}]

    assert svc.reorder_groups([{"id": 5, "sort": 20}, {"id": 6, "sort": 10}]) is True

    writes = [w for w in fake_conn.log if w[0].startswith("UPDATE")]
    assert [w[1] for w in writes] == [(20, 5), (10, 6)]
    assert all("SET sort=%s WHERE id=%s" in sql for sql, _ in writes)   # 不得顺带写 name/description
    assert fake_conn.committed and payload_invalidations                 # 顺序变了要让浏览端回源


def test_reorder_groups_rejects_missing_group_without_half_saving(monkeypatch, fake_conn, fake_db):
    """有组合被删掉时整批不写并报错：只保存一半顺序 = 用户看到的次序与管理员排的无法对账。"""
    fake_db.box["rows"] = lambda sql: [{"id": 5}]

    with pytest.raises(ValueError) as e:
        svc.reorder_groups([{"id": 5, "sort": 10}, {"id": 6, "sort": 20}])

    assert "不存在" in str(e.value)
    assert not [w for w in fake_conn.log if w[0].startswith("UPDATE")]
    assert not fake_conn.committed


def test_reorder_groups_empty_orders_is_noop(monkeypatch, fake_conn, fake_db):
    """空清单不发无谓的 SELECT/UPDATE（前端首屏没有可排内容时的正常路径）。"""
    assert svc.reorder_groups([]) is True
    assert fake_conn.log == []


def test_reorder_endpoint_passes_orders(monkeypatch, payload_invalidations):
    seen = {}

    def _fake(orders):
        seen["orders"] = orders
        return True

    # api 里是 `from ... import reorder_groups`，补丁必须打在 api 模块自身，否则改了个没人调的对象
    monkeypatch.setattr(api, "reorder_groups", _fake)

    out = api.reorder_groups_endpoint(api.GroupReorderRequest(orders=[{"id": 5, "sort": 10}]), admin=ADMIN)

    assert out == {"success": True, "count": 1}
    assert seen["orders"] == [{"id": 5, "sort": 10}]


def test_reorder_endpoint_surfaces_reason_as_400(monkeypatch):
    def _boom(orders):
        raise ValueError("1 个仪表盘组已不存在，排序未保存，请刷新后重试")

    monkeypatch.setattr(api, "reorder_groups", _boom)
    with pytest.raises(api.HTTPException) as e:
        api.reorder_groups_endpoint(api.GroupReorderRequest(orders=[{"id": 5, "sort": 10}]), admin=ADMIN)
    assert e.value.status_code == 400 and "不存在" in e.value.detail


# ── 既有写入口不得把顺序抹平 ────────────────────────────────────────────────

def test_update_group_without_sort_keeps_existing_order(fake_db, payload_invalidations):
    """编辑名称/说明时前端不回传 sort：服务端回读原值，不得拿缺省 0 把组合抬到首位。"""
    def rows(sql):
        if "SELECT id FROM adh_dashboard_groups" in sql:
            return [{"id": 5}]
        if "SELECT sort FROM adh_dashboard_groups" in sql:
            return [{"sort": 30}]
        return []

    fake_db.box["rows"] = rows
    api.update_group_endpoint(5, api.GroupCreate(name="经营日报", description="口径说明"), admin=ADMIN)

    sql, params = next(w for w in fake_db.log if w[0].startswith("UPDATE adh_dashboard_groups SET name"))
    assert params == ("经营日报", "口径说明", 30, 5)


def test_update_group_with_explicit_sort_writes_it(fake_db, payload_invalidations):
    def rows(sql):
        return [{"id": 5}] if "SELECT id FROM" in sql else [{"sort": 30}]

    fake_db.box["rows"] = rows
    api.update_group_endpoint(5, api.GroupCreate(name="经营日报", sort=7), admin=ADMIN)

    _, params = next(w for w in fake_db.log if w[0].startswith("UPDATE adh_dashboard_groups SET name"))
    assert params[2] == 7


def test_create_group_appends_to_tail(fake_conn, payload_invalidations):
    """新建组合默认落末尾（MAX(sort)+10）：固定写 0 会把已排好的清单顶乱。"""
    fake_conn.max_sort = 40

    out = api.create_group_endpoint(api.GroupCreate(name="新组合"), admin=ADMIN)

    assert out == {"success": True, "id": 77}
    sql, params = next(w for w in fake_conn.log if w[0].startswith("INSERT INTO adh_dashboard_groups"))
    assert params == ("新组合", "", 50)


def test_create_group_respects_explicit_sort(fake_conn, payload_invalidations):
    api.create_group_endpoint(api.GroupCreate(name="插队组", sort=5), admin=ADMIN)

    _, params = next(w for w in fake_conn.log if w[0].startswith("INSERT INTO adh_dashboard_groups"))
    assert params == ("插队组", "", 5)


def test_group_items_endpoint_writes_arrangement_index(fake_db, payload_invalidations):
    """组内看板全量替换时按数组下标写 sort —— 前端「已选看板」的上移/下移靠它落库。"""
    fake_db.box["rows"] = lambda sql: [{"id": 5}] if "SELECT id FROM adh_dashboard_groups" in sql else []

    out = api.set_group_items_endpoint(5, api.GroupItemsRequest(dashboard_ids=[33, 11, 22]), admin=ADMIN)

    inserts = [w for w in fake_db.log if w[0].startswith("INSERT IGNORE INTO adh_dashboard_group_items")]
    assert [(p[1], p[2]) for _, p in inserts] == [(33, 0), (11, 1), (22, 2)]
    assert out["count"] == 3


def test_groups_endpoint_returns_arrangement_order(fake_db):
    """管理端组合清单按 sort 出（与浏览端同一口径），前端只做上移/下移不再自行排序。"""
    fake_db.box["rows"] = lambda sql: (
        [{"id": 6, "name": "后建的组", "description": "", "sort": 10},
         {"id": 5, "name": "先建的组", "description": "", "sort": 20}]
        if "FROM adh_dashboard_groups" in sql else
        [{"dashboard_id": 22}, {"dashboard_id": 11}] if "group_items" in sql else [])

    out = api.list_groups_endpoint(user=ADMIN)

    assert [g["id"] for g in out] == [6, 5]
    assert out[0]["dashboard_ids"] == [22, 11]
