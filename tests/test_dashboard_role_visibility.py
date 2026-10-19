"""看板可见性按角色授权 —— 离线单测(不触碰真实数据库)。

对应看板可见性计划的裁决口径(唯一裁决 = adh_user_roles ⋈ adh_role_dashboard_access):
  V1  fail-closed: 空授权 = 一律不可见, 不解释为全量; owner/is_public 不再授予"可看"。
  V2  admin 全可见(user_role 短路 + 授权表回查)。
  V3  撤权即时生效: 裁决与列表不进任何 TTL 缓存(不留可见窗口)。
  V4  ws=0 镜像角色同样生效(workspace 分桶: 目标 ws OR ws=0)。
  V5  授权写路径全量替换幂等(角色→看板、看板→角色双向同一份授权)。
  V6  取数链路(refresh_chart/refresh_all_charts)同受裁决门控。
  V7  enforce_visibility=False 仅供 AS-BOT 大屏设计通道豁免。

全部用假 DB/假缓存注入, 不落真库。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib

import pytest

# backend.modules.viz.services 包把同名子模块属性重绑为实例, 故显式取真模块对象
dash_svc = importlib.import_module("backend.modules.viz.services.dashboard_service")
role_svc_mod = importlib.import_module("backend.core.role_service")
role_service = role_svc_mod.role_service


# ── 假 DB: 按 SQL 关键字路由, 全量记录 (sql, params) 供断言 ──────────────


class _FakeDB:
    def __init__(self):
        self.admin_cnt = 0       # adh_user_roles ⋈ adh_roles 的 admin 计数
        self.allowed = []        # get_user_allowed_dashboards 的 dashboard_id 集
        self.dashboards = []     # adh_dashboards 行
        self.charts = []         # adh_charts 行
        self.dashboard_ws = {}   # dashboard_id -> workspace_id
        self.log = []            # [(sql, params)]


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._sql = ""
        self._params: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sql = " ".join(sql.split())
        self._params = list(params or [])
        self.db.log.append((self._sql, self._params))

    def fetchone(self):
        s = self._sql
        if "COUNT(*) AS cnt" in s:
            return {"cnt": self.db.admin_cnt}
        if "SELECT workspace_id FROM adh_dashboards" in s:
            ws = self.db.dashboard_ws.get(self._params[0])
            if ws is None:
                row = next((d for d in self.db.dashboards if d["id"] == self._params[0]), None)
                ws = row.get("workspace_id") if row else None
            return None if ws is None else {"workspace_id": ws}
        if "FROM adh_dashboards WHERE id = %s" in s:
            return next((dict(d) for d in self.db.dashboards if d["id"] == self._params[0]), None)
        return None

    def fetchall(self):
        s = self._sql
        if "SELECT DISTINCT rda.dashboard_id" in s:
            return [{"dashboard_id": i} for i in self.db.allowed]
        if "FROM adh_charts" in s:
            return [dict(c) for c in self.db.charts]
        if "FROM adh_dashboards" in s:
            rows = [dict(d) for d in self.db.dashboards]
            if "id IN" in s:
                ids = set(self._params[1:] if "workspace_id = %s" in s else self._params)
                rows = [r for r in rows if r["id"] in ids]
            return rows
        return []


class _FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _FakeCursor(self.db)

    def commit(self):
        pass

    def close(self):
        pass


class _StaleCache:
    """命中即返回过期可见集的假 TTL 缓存: 用于证明可见性链路根本不读缓存。"""

    def __init__(self, stale):
        self.stale = stale

    def get(self, key):
        return self.stale

    def get_or_set(self, key, factory):
        return self.stale

    def set(self, *args, **kwargs):
        pass


class _MemCache:
    """进程内假缓存(测试隔离用): 同接口 get/set/invalidate/invalidate_prefix/get_or_set。"""

    def __init__(self):
        self.store = {}
        self.hits = 0
        self.misses = 0

    def get(self, key):
        if key in self.store:
            self.hits += 1
            return self.store[key]
        self.misses += 1
        return None

    def set(self, key, value, ttl=None):
        self.store[key] = value

    def invalidate(self, key=None):
        self.store.clear() if key is None else self.store.pop(key, None)

    def invalidate_prefix(self, prefix):
        for k in [k for k in self.store if k.startswith(prefix)]:
            self.store.pop(k, None)

    def get_or_set(self, key, factory, ttl=None):
        value = self.get(key)
        if value is not None:
            return value
        value = factory()
        if value is not None:
            self.set(key, value, ttl=ttl)
        return value


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB()
    factory = lambda: _FakeConn(fake)  # noqa: E731
    monkeypatch.setattr(dash_svc, "get_metadata_conn", factory)
    monkeypatch.setattr(role_svc_mod, "get_metadata_conn", factory)
    # 目录/详情载荷构建走 common_db.execute_query + dashboard_cache: 统一接入假 DB/假缓存,
    # 测试之间不共用真 Redis(避免键污染), 也方便断言缓存命中与写失效
    import backend.common.db as common_db

    def _exec(sql, params=None, fetchone=False):
        cur = factory().cursor()
        cur.execute(sql, params)
        return cur.fetchone() if fetchone else cur.fetchall()

    monkeypatch.setattr(common_db, "execute_query", _exec)
    monkeypatch.setattr(dash_svc, "dashboard_cache", _MemCache())
    return fake


def _dash(did, ws=3, status="enabled", **kw):
    row = {"id": did, "name": f"看板 {did}", "workspace_id": ws, "status": status,
           "owner_id": 1, "is_public": 0, "layout": "[]", "filters": "{}", "params": "[]"}
    row.update(kw)
    return row


# ── V1 fail-closed: 空授权 ─────────────────────────────────────────────


def test_empty_grants_fail_closed(db):
    db.allowed = []
    assert dash_svc.visible_dashboard_ids(9) == set()
    assert dash_svc.check_dashboard_visible(101, 9, workspace_id=3) is False
    # 列表侧同样裁决: 空可见集直接短路, 连看板表都不查
    assert dash_svc.DashboardService().list_dashboards(9, 3) == []
    assert not any("FROM adh_dashboards" in s for s, _ in db.log)


def test_owner_and_is_public_grant_nothing(db):
    """owner/is_public 不再授予"可看": 只有角色授权算数。"""
    db.allowed = []
    db.dashboards = [_dash(101, owner_id=9, is_public=1)]
    assert dash_svc.check_dashboard_visible(101, 9, workspace_id=3) is False
    assert dash_svc.DashboardService().get_dashboard(101, 9) is None


def test_anonymous_denied_without_query(db):
    assert dash_svc.check_dashboard_visible(101, 0) is False
    assert db.log == []  # 无身份直接拒, 不查授权表


# ── 命中可见 + 列表 id IN 过滤 ─────────────────────────────────────────


def test_granted_dashboards_visible_and_list_filtered(db):
    db.allowed = [101, 102]
    db.dashboards = [_dash(101), _dash(102), _dash(203)]
    svc = dash_svc.DashboardService()
    assert [r["id"] for r in svc.list_dashboards(9, 3)] == [101, 102]
    assert dash_svc.check_dashboard_visible(101, 9, workspace_id=3) is True
    assert dash_svc.check_dashboard_visible(203, 9, workspace_id=3) is False
    select = next(s for s, _ in db.log if "FROM adh_dashboards" in s and "id IN" in s)
    assert "id IN (%s,%s)" in select
    assert "workspace_id = %s" in select


def test_check_visible_resolves_workspace_from_row(db):
    """workspace_id 缺省时按看板归属解析后再裁决; 看板不存在 = 不可见。"""
    db.allowed = [101]
    db.dashboard_ws = {101: 3, 203: 5}
    assert dash_svc.check_dashboard_visible(101, 9) is True
    assert dash_svc.check_dashboard_visible(203, 9) is False
    assert dash_svc.check_dashboard_visible(999, 9) is False


# ── V2 admin 全可见 ────────────────────────────────────────────────────


def test_admin_short_circuit_sees_all(db):
    assert dash_svc.visible_dashboard_ids(1, "admin") is None
    assert dash_svc.check_dashboard_visible(999, 1, "admin") is True
    assert db.log == []  # 短路不查库


def test_admin_resolved_from_grant_table(db):
    db.admin_cnt = 1
    db.dashboards = [_dash(101), _dash(203)]
    assert dash_svc.visible_dashboard_ids(1) is None
    assert dash_svc.check_dashboard_visible(999, 1, workspace_id=3) is True
    # admin 全可见: 列表不做 id 过滤
    assert [r["id"] for r in dash_svc.DashboardService().list_dashboards(1, 3)] == [101, 203]
    assert not any("id IN" in s and "FROM adh_dashboards" in s for s, _ in db.log)


# ── V3 撤权即时生效(绕开 TTL 缓存) ─────────────────────────────────────


def test_revocation_takes_effect_immediately(db, monkeypatch):
    db.allowed = [101]
    db.dashboards = [_dash(101), _dash(102)]
    db.dashboard_ws = {101: 3, 102: 3}
    svc = dash_svc.DashboardService()
    assert [r["id"] for r in svc.list_dashboards(9, 3)] == [101]
    # 预置"命中即返回旧可见集"的 TTL 缓存: 若链路读缓存, 撤权后仍会看到 101
    monkeypatch.setattr(dash_svc, "dashboard_cache", _StaleCache([_dash(101)]))
    db.allowed = []
    assert svc.list_dashboards(9, 3) == []
    assert dash_svc.check_dashboard_visible(101, 9, workspace_id=3) is False


# ── V4 ws=0 镜像分桶 ──────────────────────────────────────────────────


def test_ws_mirror_bucket_in_sql(db):
    """工作空间角色已下线：可见性按真值源全量角色裁决（SQL 无 workspace 维度）。"""
    db.allowed = [101]
    assert role_service.get_user_allowed_dashboards(9, 3) == [101]
    sql, params = db.log[-1]
    assert "workspace_id" not in sql
    assert params == [9]


# ── V5 授权写路径全量替换幂等 ─────────────────────────────────────────


def test_set_role_dashboards_full_replace_idempotent(db):
    writes = lambda: [(s, p) for s, p in db.log if "adh_role_dashboard_access" in s]  # noqa: E731
    role_service.set_role_dashboards(5, [1, 2, 2, 3])
    first = writes()
    assert first[0] == ("DELETE FROM adh_role_dashboard_access WHERE role_id = %s", [5])
    inserts = [p for s, p in first if "INSERT IGNORE" in s]
    assert sorted(p[1] for p in inserts) == [1, 2, 3]  # 去重
    assert all(p[0] == 5 for p in inserts)
    # 同参数重放写序列完全一致(幂等)
    db.log.clear()
    role_service.set_role_dashboards(5, [1, 2, 2, 3])
    assert writes() == first
    # 收缩授权同样是全量替换
    db.log.clear()
    role_service.set_role_dashboards(5, [3])
    assert [p[1] for s, p in writes() if "INSERT IGNORE" in s] == [3]


def test_set_dashboard_roles_full_replace(db):
    role_service.set_dashboard_roles(101, [5, 5, 6])
    writes = [(s, p) for s, p in db.log if "adh_role_dashboard_access" in s]
    assert writes[0] == ("DELETE FROM adh_role_dashboard_access WHERE dashboard_id = %s", [101])
    assert sorted(p[0] for s, p in writes if "INSERT IGNORE" in s) == [5, 6]


# ── V6 取数链路同受裁决门控 ───────────────────────────────────────────


def test_refresh_denied_for_revoked_user(db):
    db.allowed = []
    db.dashboard_ws = {101: 3}
    svc = dash_svc.ChartService()
    with pytest.raises(PermissionError):
        svc.refresh_chart(101, 7, user_id=9)
    with pytest.raises(PermissionError):
        svc.refresh_all_charts(101, user_id=9)


def test_refresh_all_denied_without_identity(db):
    with pytest.raises(dash_svc.NoIdentityError):
        dash_svc.ChartService().refresh_all_charts(101, user_id=0)


# ── V7 AS-BOT 大屏设计通道豁免 ────────────────────────────────────────


def test_enforce_visibility_false_exempts_design_channel(db):
    db.allowed = []
    db.dashboards = [_dash(101)]
    svc = dash_svc.DashboardService()
    assert svc.get_dashboard(101, 9) is None  # 默认强制裁决
    got = svc.get_dashboard(101, 9, enforce_visibility=False)
    assert got and got["id"] == 101           # 仅供 AS-BOT 大屏设计通道


# ── V8 角色授予只授「可看」, 不授「可编辑」 ─────────────────────────────


def test_grant_gives_view_not_edit(db):
    """系统分配给角色的看板: 能看, 不能编辑(仅属主/admin 可写)。"""
    db.allowed = [101]
    db.dashboards = [_dash(101, owner_id=1), _dash(203, owner_id=9)]
    assert dash_svc.check_dashboard_visible(101, 9, workspace_id=3) is True
    assert dash_svc.check_dashboard_editable(101, 9) is False   # 属主是别人 → 只读
    assert dash_svc.check_dashboard_editable(203, 9) is True    # 自己的看板可写


def test_edit_scope_fail_closed(db):
    db.admin_cnt = 1
    assert dash_svc.check_dashboard_editable(101, 1) is True     # admin 短路
    assert dash_svc.check_dashboard_editable(999, 1, "admin") is True
    assert dash_svc.check_dashboard_editable(101, 0) is False    # 无身份一律拒
    db.admin_cnt = 0
    db.dashboards = []
    assert dash_svc.check_dashboard_editable(999, 9) is False    # 看板不存在一律拒


def test_detail_and_list_carry_editable_flag(db):
    """清单/详情携带 editable 供前端隐藏写入口; 浏览域清单不外泄 owner_id。"""
    db.allowed = [101, 203]
    db.dashboards = [_dash(101, owner_id=1), _dash(203, owner_id=9)]
    rows = dash_svc.DashboardService().list_dashboards(9, 3)
    assert {r["id"]: r["editable"] for r in rows} == {101: False, 203: True}
    assert all("owner_id" in r for r in rows)  # 详情/管理列表保留属主供属主自检
    catalog = dash_svc.catalog_cards([101, 203], 9, "")
    assert {r["id"]: r["editable"] for r in catalog} == {101: False, 203: True}
    assert all("owner_id" not in r for r in catalog)  # 清单不外泄属主 id


def test_api_write_gate_rejects_role_grantee(db):
    """写接口层 fail-closed: 非属主/admin 改看板 → 403 且原因可解释。"""
    from fastapi import HTTPException
    from backend.modules.viz.api import dashboard as dash_api
    db.dashboards = [_dash(101, owner_id=1)]
    with pytest.raises(HTTPException) as e:
        dash_api._require_editable(101, {"user_id": 9, "role": "analyst"})
    assert e.value.status_code == 403
    assert "属主" in e.value.detail  # 可解释拒绝原因, 不是笼统失败
    dash_api._require_editable(101, {"user_id": 1, "role": "analyst"})   # 属主放行
    dash_api._require_editable(101, {"user_id": 9, "role": "admin"})     # admin 放行


def test_api_chart_write_endpoint_wired_to_gate(db):
    """图表写端点必须真的接了写闸(不是只有 helper 在库里)。"""
    from fastapi import HTTPException
    from backend.modules.viz.api.dashboard import ChartUpdate, update_chart_endpoint
    db.dashboards = [_dash(101, owner_id=1)]
    with pytest.raises(HTTPException) as e:
        update_chart_endpoint(101, 7, ChartUpdate(name="改名"),
                              user={"user_id": 9, "role": "analyst"})
    assert e.value.status_code == 403


# ── V9 共享载荷缓存: 只加速「长什么样」, 不放权 ──────────────────────


def test_card_payload_cached_until_write(db):
    """卡片几何载荷缓存命中不回源; 写路径失效后重建。"""
    db.dashboards = [_dash(101, owner_id=1)]
    cache = dash_svc.dashboard_cache
    dash_svc._card_rows()
    misses = cache.misses
    dash_svc._card_rows()
    assert cache.misses == misses           # 命中缓存, 不再回源
    dash_svc._invalidate_dashboard_payload(101)
    dash_svc._card_rows()
    assert cache.misses > misses            # 失效后重建


def test_detail_payload_cached_and_copy_isolated(db):
    """详情载荷缓存命中且返回副本: 调用方改写不得污染缓存。"""
    db.allowed = [101]
    db.dashboards = [_dash(101, owner_id=1)]
    svc = dash_svc.DashboardService()
    first = svc.get_dashboard(101, 9)
    assert first and first["id"] == 101
    first["name"] = "被调用方改写"
    hits = dash_svc.dashboard_cache.hits
    again = svc.get_dashboard(101, 9)
    assert dash_svc.dashboard_cache.hits > hits   # 第二次走缓存
    assert again["name"] == "看板 101"            # 缓存对象未被污染


def test_cache_never_grants_extra_visibility(db):
    """毒化缓存(卡片里藏了无授权看板)也不得多看: 可见集每次实时裁决。"""
    db.dashboards = [_dash(101, owner_id=1), _dash(203, owner_id=1)]
    db.allowed = [101]
    # 直接把两块看板都塞进共享卡片缓存, 模拟缓存被污染/过期
    dash_svc.dashboard_cache.set(
        dash_svc.CARDS_KEY, dash_svc.card_catalog_fields([dict(d) for d in db.dashboards]))
    groups = dash_svc.build_visible_groups(9, "viewer")
    assert [b["id"] for g in groups for b in g["dashboards"]] == [101]


def test_detail_cache_not_served_without_visibility(db):
    """详情缓存不放权: 无授权时即使缓存里有载荷也返回 None。"""
    db.dashboards = [_dash(101, owner_id=1)]
    db.allowed = []
    payload = dash_svc.DashboardService()._fetch_dashboard_payload(101)
    dash_svc.dashboard_cache.set(dash_svc.DETAIL_KEY.format(101), payload)
    assert dash_svc.DashboardService().get_dashboard(101, 9) is None


