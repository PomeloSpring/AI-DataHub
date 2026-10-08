"""Phase 4 合并 web 入口契约 —— 单 app 装配护栏。

- 对外 REST 路径不变：代表性契约路由必须存在于合并入口路由表（逐模块取样）；
- 去重 fail-loud：跨模块真撞（精确重复/参数模式互匹配）启动即报；同模块内按注册序生效；
- health 按端口还原旧服务口径（monitoring 探测读 status/version 字段）；
- 单进程附带收益：跨模块内存缓存一致性（mind 触发的失效与 viz 读取面同一实例）。
"""

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from backend.processes.main import (
    _health_body,
    _iter_effective_routes,
    _patterns_conflict,
    app as web_app,
    assert_no_duplicate_routes,
)


def _route_table(application):
    out = set()
    for r in _iter_effective_routes(application.router.routes):
        for m in (getattr(r, "methods", None) or []):
            if m not in ("HEAD", "OPTIONS"):
                out.add((m, getattr(r, "path", "")))
    return out


# 代表性契约路由（每模块取样；对外 REST 路径不变的最小冻结面）
CONTRACT_ROUTES = {
    ("GET", "/health"), ("GET", "/api/health"), ("GET", "/system-metrics"),
    ("GET", "/api/admin/knowledge"),                    # auth
    ("GET", "/api/monitoring/services"),                # auth（服务健康聚合探测）
    ("GET", "/api/admin/menu-tree"),                    # catalog
    ("GET", "/api/admin/metadata"),                     # catalog
    ("POST", "/api/admin/sync/metadata"),               # catalog（真撞裁决①：真实现胜出）
    ("GET", "/api/dag/workflows"),                      # flow
    ("GET", "/api/quality/dashboard"),                  # gov
    ("GET", "/api/lineage/graph"),                      # gov
    ("GET", "/api/admin/skills"),                       # mind（真撞裁决②：前端契约胜出）
    ("GET", "/api/as-bot/alias-suggestions"),           # mind
    ("GET", "/api/admin/as-bots/roles"),                # platform
    ("GET", "/"),                                       # semhub
    ("GET", "/api/graph/query"),                        # semhub
    ("GET", "/api/dashboard/groups"),                   # viz
}


def test_merged_app_route_table_covers_contract():
    table = _route_table(web_app)
    missing = sorted(f"{m} {p}" for m, p in CONTRACT_ROUTES - table)
    assert not missing, f"合并入口缺失契约路由: {missing}"


def test_adjudicated_routes_have_single_owner():
    """真撞裁决落定后，裁决面必须只剩一个注册者（去重不回潮）。"""
    counts = {}
    for r in _iter_effective_routes(web_app.router.routes):
        path = getattr(r, "path", "")
        for m in (getattr(r, "methods", None) or []):
            if m in ("HEAD", "OPTIONS"):
                continue
            counts[(m, path)] = counts.get((m, path), 0) + 1
    for key in [("POST", "/api/admin/sync/metadata"),
                ("POST", "/api/admin/sync/metadata/columns"),
                ("GET", "/api/admin/skills"),
                ("POST", "/api/admin/skills"),
                ("GET", "/health"),
                ("GET", "/api/health"),
                ("GET", "/system-metrics")]:
        assert counts.get(key) == 1, f"{key} 注册数应为 1，实际 {counts.get(key, 0)}"


def test_duplicate_routes_fail_loud_across_modules():
    """跨模块真撞（同模式互匹配）→ RuntimeError；同模块内按注册序生效（与原壳一致）。"""
    def build():
        a = FastAPI()
        r1, r2 = APIRouter(), APIRouter()

        @r1.get("/x/{name}")
        def _a(name: str):
            return {"who": "a"}

        @r2.get("/x/{skill_key}")
        def _b(skill_key: str):
            return {"who": "b"}

        a.include_router(r1)
        a.include_router(r2)
        return a

    a = build()
    cross = [(f"m{i}", e) for i, e in enumerate(a.router.routes)]
    try:
        assert_no_duplicate_routes(cross)
    except RuntimeError as e:
        assert "互匹配" in str(e) or "重复注册" in str(e)
    else:
        raise AssertionError("跨模块同模式真撞未被 fail-loud 拦截")

    same = [("m", e) for e in a.router.routes]
    assert_no_duplicate_routes(same)  # 同模块路由序自理，不应报


def test_patterns_conflict_semantics():
    assert _patterns_conflict("/a/{x}", "/a/{y}")
    assert _patterns_conflict("/a/b", "/a/b")
    assert not _patterns_conflict("/a/{x}/versions", "/a/{y}/scripts")  # 静态段不同无遮蔽
    assert not _patterns_conflict("/a/{x}", "/a/{x}/b")


def test_health_body_per_port_preserves_legacy_contract():
    """monitoring 探测按端口读 status/version，旧服务口径逐字保留。"""
    class _Req:
        def __init__(self, port):
            self.scope = {"server": ("127.0.0.1", port)}

    assert _health_body(_Req(8001)) == {"status": "ok", "service": "datamind", "version": "1.0.0"}
    assert _health_body(_Req(8003)) == {"status": "healthy", "service": "dataflow", "version": "1.0.0"}
    assert _health_body(_Req(8006)) == {"status": "ok", "service": "authservice"}
    body = _health_body(_Req(9999))  # 非契约端口（容器汇聚口）→ 合并口径
    assert body["status"] == "ok" and body["service"] == "ai-datahub"

    client = TestClient(web_app)
    for path in ("/health", "/api/health"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} 不可达"
        assert resp.json()["status"] in ("ok", "healthy")


def test_cross_module_cache_invalidation_single_instance(monkeypatch):
    """单进程附带收益：datamind(mind) 触发的看板缓存失效与 dataviz(viz) 读取面同一实例。

    缓存实例真源在 backend.common.ttl_cache.dashboard_cache；mind 侧 screen_tools
    经 dashboard_service._invalidate_dashboard_cache（函数体内惰性 import 同一函数）失效，
    viz 侧读取面持同一实例 —— 跨模块失效与读取天然一致。
    行为面用内存假缓存验证（缓存是性能旁路，用例不依赖外部 Redis）。
    """
    import inspect
    from importlib import import_module

    from backend.common import ttl_cache
    from backend.modules.mind.execution.sdk_tools import screen_tools

    # viz/services/__init__ 重导出单例 dashboard_service 遮蔽同名子模块，须取真模块
    dash = import_module("backend.modules.viz.services.dashboard_service")

    # ① 实例唯一：viz 读取面与 common 真源同对象
    assert dash.dashboard_cache is ttl_cache.dashboard_cache

    # ② mind 侧失效路径源码锁定（惰性 import，模块命名空间取不到函数属性）
    src = inspect.getsource(screen_tools)
    assert ("from backend.modules.viz.services.dashboard_service import "
            "ChartService, DashboardService, _invalidate_dashboard_cache") in src

    # ③ 行为：mind 触发的失效按用户前缀清除共享实例条目，viz 侧观察到已清
    class _MemCache:
        def __init__(self):
            self.data = {}

        def set(self, key, value, ttl=None):
            self.data[key] = value

        def get(self, key):
            return self.data.get(key)

        def invalidate(self, key=None):
            self.data.clear() if key is None else self.data.pop(key, None)

        def invalidate_prefix(self, prefix):
            for k in [k for k in self.data if k.startswith(prefix)]:
                del self.data[k]

    mem = _MemCache()
    monkeypatch.setattr(dash, "dashboard_cache", mem)
    mem.set("dash:424242:t", {"v": 1})
    mem.set("dash:999999:t", {"v": 2})
    assert mem.get("dash:424242:t") is not None
    dash._invalidate_dashboard_cache(424242)  # mind 侧触发的同一函数
    assert mem.get("dash:424242:t") is None      # viz 侧观察到已清
    assert mem.get("dash:999999:t") is not None  # 其他用户条目不受影响
