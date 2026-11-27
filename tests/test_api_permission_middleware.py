"""api_permission 中间件判定面 —— 豁免边界与开放读清单（覆盖缺口补齐）。

背景（2026-10 缺陷）：应用壳豁免正则 `^/api/admin/brand$` 被**尾斜杠**击穿
（`/api/admin/brand/` 轮询 403），字模库等渲染样式资产被当普通读门控
（看板能看但样式加载 403）。判定链本身此前无专项用例——本文件补齐：
- 豁免/开放读匹配对尾斜杠归一；
- 字模库读开放（渲染必需）、写仍按码门控；
- check_api_permission 的放行/拒绝/管理短路。
"""

from backend.common import api_permission as ap


def test_open_read_brand_allows_trailing_slash():
    for p in ("/api/admin/brand", "/api/admin/brand/"):
        assert ap._OPEN_READ_ROUTES.fullmatch(p.rstrip("/")), p


def test_open_read_vis_library_render_assets():
    """字模库读 = 看板/大屏渲染必需样式资产（与品牌同类），登录即可。"""
    for p in ("/api/vis-library/categories", "/api/vis-library/components",
              "/api/vis-library/components/", "/api/vis-library/components/123"):
        assert ap._OPEN_READ_ROUTES.fullmatch(p.rstrip("/")), p
    # 写门控在调用方 method 判定（仅 GET 豁免），不在正则层


def test_open_read_menu_tree_and_udfs_kept():
    for p in ("/api/menu-tree", "/api/admin/menu-tree", "/api/udfs", "/api/udfs/categories"):
        assert ap._OPEN_READ_ROUTES.fullmatch(p.rstrip("/")), p


def test_check_permission_allows_granted_code(monkeypatch):
    monkeypatch.setattr(ap, "_load_role_apis",
                        lambda role: [{"pattern": "/api/quality*", "method": "GET", "allowed": True}])
    assert ap.check_api_permission("analyst", "GET", "/api/quality/dashboard")
    assert ap.check_api_permission("analyst", "GET", "/api/quality/dashboard/")  # 尾斜杠同判
    assert not ap.check_api_permission("analyst", "POST", "/api/quality/rules")  # 方法不匹配
    assert not ap.check_api_permission("analyst", "GET", "/api/vis-library/components")  # 未授权码


def test_check_permission_admin_and_empty_role(monkeypatch):
    assert ap.check_api_permission("admin", "DELETE", "/api/anything")
    assert not ap.check_api_permission("", "GET", "/api/anything")
    monkeypatch.setattr(ap, "_load_role_apis", lambda role: [])
    assert not ap.check_api_permission("viewer", "GET", "/api/anything")  # 空规则 fail-closed


def test_middleware_trailing_slash_exempt(monkeypatch):
    """中间件集成：带尾斜杠的应用壳读对无码角色放行（尾斜杠曾击穿豁免）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/api/admin/brand/")
    def brand():
        return {"ok": True}

    @app.get("/api/vis-library/components")
    def comps():
        return {"ok": True}

    ap.add_api_permission_middleware(app)
    client = TestClient(app)

    class _U:
        token = "t"

    monkeypatch.setattr("backend.common.auth.resolve_current_user", lambda t: {"user_id": 9, "role": "nocodes", "username": "u"})
    monkeypatch.setattr(ap, "_load_role_apis", lambda role: [])
    h = {"Authorization": "Bearer t"}
    assert client.get("/api/admin/brand/", headers=h).status_code == 200
    assert client.get("/api/vis-library/components", headers=h).status_code == 200


# ── 资源归属校验（会话语义 vs 资源语义拆分） ───────────────────


def test_resource_scope_global_resource_passes():
    """全局资源（ws=0）不被会话语义误拦（勾了权限码即可见）；会话语义 ws=0 仍拒。"""
    import pytest as _pytest
    from fastapi import HTTPException

    from backend.common.auth import authorize_resource_scope, authorize_workspace

    u = {"user_id": 9, "role": "analyst"}
    assert authorize_resource_scope(u, 0) == 0
    assert authorize_resource_scope(u, None) == 0
    with _pytest.raises(HTTPException) as ei:
        authorize_workspace(u, 0)  # 会话未选空间仍拒（语义保留）
    assert ei.value.status_code == 403


def test_resource_scope_owned_workspace_and_foreign_denied(monkeypatch):
    import pytest as _pytest
    from fastapi import HTTPException

    from backend.common.auth import authorize_resource_scope
    from backend.core.role_service import role_service

    monkeypatch.setattr(role_service, "check_workspace_owner", lambda uid, ws: uid == 9 and ws == 305)
    u = {"user_id": 9, "role": "analyst"}
    assert authorize_resource_scope(u, 305) == 305
    with _pytest.raises(HTTPException):
        authorize_resource_scope(u, 999)  # 他人空间拒绝


# ── 图谱生成入口与授权登记一致（「关系图谱」页的「图谱生成」按钮）───────

def test_graph_sync_route_exists():
    """前端调的是 POST /api/graph/sync；端点改名必须同步注册表与用例。"""
    from backend.modules.semhub.api.graph import router as graph_router

    assert any(getattr(r, "path", "") == "/sync" and "POST" in (getattr(r, "methods", None) or set())
               for r in graph_router.routes), "图谱生成端点已变动，请同步 graph:manage 的 api_pattern"


def test_graph_manage_pattern_covers_sync_and_stays_narrow():
    """只写 /api/graph/rebuild* 会漏掉真实端点 → 非 admin 必 403（admin 旁路掩盖缺口）。"""
    pattern = "/api/graph/rebuild*,/api/graph/sync*"
    covered = [one.strip() for one in pattern.split(",") if one.strip()]
    assert any(ap._match_path(one, "/api/graph/sync") for one in covered)
    assert any(ap._match_path(one, "/api/graph/rebuild") for one in covered)
    # 不越界：/api/graph 下的其他写口（如 query）不得被该模式顺手放行
    assert not any(ap._match_path(one, "/api/graph/query") for one in covered)


def test_registry_grants_graph_sync_to_modeling_roles():
    """真实注册表必须覆盖 /api/graph/sync，且建模角色（能 activate）同样能手动重建。"""
    import pytest as _pytest
    from backend.common.db import execute_query

    try:
        row = execute_query("SELECT api_pattern, api_method FROM adh_perm_registry "
                            "WHERE perm_code='graph:manage'", fetchone=True)
    except Exception:  # noqa: BLE001  元库不可用时不假绿也不假红
        _pytest.skip("元数据库不可用，跳过登记一致性断言")
    if not row:
        _pytest.skip("元数据库无 graph:manage 登记")
    patterns = [p.strip() for p in (row["api_pattern"] or "").split(",") if p.strip()]
    assert any(ap._match_path(p, "/api/graph/sync") for p in patterns), \
        f"graph:manage 未覆盖图谱生成端点: {patterns}（非 admin 点「图谱生成」必 403）"
    assert "POST" in (row["api_method"] or "")
    granted = execute_query("SELECT 1 FROM adh_role_perms p JOIN adh_roles r ON r.id=p.role_id "
                            "WHERE p.perm_code='graph:manage' AND r.name='analyst'", fetchone=True)
    assert granted, "analyst 持有 ontology:activate（激活即重建图），应同时被授予手动图谱生成"


# ── 方法限定 api_pattern（`METHOD path` 条目）───────────────────────
#
# 背景：一行内 api_pattern × api_method 是笛卡尔积，无法表达「允许 POST 新建、
# 不允许 POST 批量删除」（同为 POST、同在 /articles* 前缀下）。解法是 api_pattern
# 里形如 `METHOD path` 的条目（含空格）按该 method 精确登记、不参与求积；
# 纯路径条目（不含空格）行为必须与旧版完全一致（向后兼容）。

def _stub_role_db(monkeypatch, *, codes, registry_rows):
    """把 _load_role_apis 的 DB 读换成假数据，走真实解析分支（不连库）。"""
    from backend.common import db

    def fake(sql, params=None, **kw):
        if "FROM adh_roles" in sql:
            return [{"id": 1}]
        if "FROM adh_role_perms" in sql:
            return [{"perm_code": c} for c in codes]
        if "FROM adh_perm_registry" in sql:
            return registry_rows
        return []

    monkeypatch.setattr(db, "execute_query", fake)
    monkeypatch.setattr(ap, "_cache", {})


def test_method_scoped_entry_hit_and_miss(monkeypatch):
    """限定条目只放行它声明的方法×路径，不与本行 api_method 求积。"""
    _stub_role_db(
        monkeypatch,
        codes=["article:create"],
        registry_rows=[{
            "perm_code": "article:create",
            # 与真实 article:create 行同构：全限定条目（含空格），
            # 纯路径零回归由下面两个用例锁（混排 / 纯路径行）
            "api_pattern": ("POST /api/content/articles,"
                            "GET /api/content/articles*,PUT /api/content/articles*"),
            "api_method": "GET,POST,PUT",
        }])
    # 限定命中：精确新建
    assert ap.check_api_permission("author", "POST", "/api/content/articles")
    # 限定未命中：同为 POST 但路径不同 —— batch-delete 不在创建档里（越权拦截）
    assert not ap.check_api_permission("author", "POST", "/api/content/articles/batch-delete")
    # 纯路径条目行为不变：GET/PUT 靠各自限定条目放行（方法均在 api_method 里）
    assert ap.check_api_permission("author", "GET", "/api/content/articles/a1")
    assert ap.check_api_permission("author", "PUT", "/api/content/articles/a1")
    # DELETE 不在 api_method 里 → 拒（单条删除不属创作档）
    assert not ap.check_api_permission("author", "DELETE", "/api/content/articles/a1")
    # 限定条目不得被同前缀“借方法”：POST 打到 GET 限定的路径仍拒
    assert not ap.check_api_permission("author", "POST", "/api/content/articles/a1/files")


def test_method_scoped_and_cartesian_rows_mixed(monkeypatch):
    """限定条目与笛卡尔积行混排：两种解析互不串味（各自按声明生效）。"""
    _stub_role_db(
        monkeypatch,
        codes=["mix:code"],
        registry_rows=[{
            "perm_code": "mix:code",
            "api_pattern": "GET /api/only-get,/api/both*",
            "api_method": "GET,POST",
        }])
    # 限定条目：只有 GET 能过，即使本行 api_method 含 POST
    assert ap.check_api_permission("mix", "GET", "/api/only-get")
    assert not ap.check_api_permission("mix", "POST", "/api/only-get")
    assert not ap.check_api_permission("mix", "POST", "/api/only-get/child")
    # 纯路径条目：笛卡尔积照旧（GET、POST 都过）
    assert ap.check_api_permission("mix", "GET", "/api/both/x")
    assert ap.check_api_permission("mix", "POST", "/api/both/x")
    assert not ap.check_api_permission("mix", "DELETE", "/api/both/x")


def test_plain_path_rows_zero_regression(monkeypatch):
    """纯路径行（无空格）行为与旧版完全一致：尾斜杠、通配符、方法集合都不变。"""
    _stub_role_db(
        monkeypatch,
        codes=["legacy:code"],
        registry_rows=[{
            "perm_code": "legacy:code",
            "api_pattern": "/api/quality*,/api/exact",
            "api_method": "*",
        }])
    assert ap.check_api_permission("legacy", "GET", "/api/quality/dashboard")
    assert ap.check_api_permission("legacy", "GET", "/api/quality/dashboard/")  # 尾斜杠同判
    assert ap.check_api_permission("legacy", "DELETE", "/api/exact")            # method=* 全方法
    assert not ap.check_api_permission("legacy", "GET", "/api/other")
