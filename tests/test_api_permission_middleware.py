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
