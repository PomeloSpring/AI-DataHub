"""Phase 4 web 入口契约 —— 单 app 装配 + 单一监听口护栏。

- 对外 REST 路径不变：代表性契约路由必须存在于web 入口路由表（逐模块取样）；
- 去重 fail-loud：跨模块真撞（精确重复/参数模式互匹配）启动即报；同模块内按注册序生效；
- health 为单进程合并口径（monitoring 探测读 status/version 字段）；
- **端口口径对账**：注册表 / vite 代理 / nginx 上游 / Dockerfile / config / monitoring
  必须同源单一 web 端口 8000。口径漂移会把前端直接打成 ECONNREFUSED（真实发生过），
  故逐处对账而不是只盯一处；旧 8 个契约端口不得再被任何出站地址引用；
- 单进程附带收益：跨模块内存缓存一致性（mind 触发的失效与 viz 读取面同一实例）。
"""

import re
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from backend.app.main import (
    _health_body,
    _iter_effective_routes,
    _patterns_conflict,
    app as web_app,
    assert_no_duplicate_routes,
)
from backend.app.serve import service_ports
from backend.common import config as _cfg

ROOT = Path(__file__).resolve().parents[1]

# 随进程壳退役的旧契约端口（8 微服务 + semhub）
RETIRED_PORTS = {8001, 8002, 8003, 8004, 8005, 8006, 8007, 8012}


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
    ("GET", "/api/semantic/schema"),                    # semhub（Phase 6 契约导出）
    ("GET", "/api/dashboard/groups"),                   # viz
}


def test_merged_app_route_table_covers_contract():
    table = _route_table(web_app)
    missing = sorted(f"{m} {p}" for m, p in CONTRACT_ROUTES - table)
    assert not missing, f"web 入口缺失契约路由: {missing}"


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


def test_health_body_is_merged_single_process_contract():
    """单进程合并口径：不再按端口还原旧服务名（monitoring 只读 status/version）。"""
    class _Req:
        def __init__(self, port):
            self.scope = {"server": ("127.0.0.1", port)}

    for port in (_cfg.WEB_PORT, 9999):
        body = _health_body(_Req(port))
        assert body["status"] == "ok" and body["service"] == "ai-datahub", f"port={port}"

    client = TestClient(web_app)
    for path in ("/health", "/api/health"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} 不可达"
        assert resp.json()["status"] in ("ok", "healthy")


# ═══════════════════════════════════════════════════════════════════
# 端口口径护栏：一个监听口、全部入口同源
# ═══════════════════════════════════════════════════════════════════
def test_registry_exposes_single_web_listen_port():
    """services.conf 是唯一权威登记：只能有一个监听口，且 = config.WEB_PORT = 8000。"""
    ports = service_ports()
    assert ports == [8000], f"注册表监听口应为单一 8000，实际 {ports}"
    assert _cfg.WEB_PORT == ports[0], "config.WEB_PORT 与注册表不一致"
    assert not set(ports) & RETIRED_PORTS, f"退役端口回潮: {set(ports) & RETIRED_PORTS}"


def test_frontend_proxies_point_at_the_listen_port():
    """前端代理目标端口必须等于后端监听口 —— 不一致就是 ECONNREFUSED。"""
    vite = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    m = re.search(r"const BACKEND = 'http://127\.0\.0\.1:(\d+)'", vite)
    assert m, "vite 代理缺单一 BACKEND 目标（不得再按微服务名分端口）"
    assert int(m.group(1)) == service_ports()[0]
    # 代理表内不得残留按服务名分流的旧目标（端口漂移的另一种形态）
    assert not re.search(r"proxyOptions\(\s*'http", vite), "vite 仍向多个后端目标代理"

    nginx = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    upstreams = {int(p) for p in re.findall(r"proxy_pass\s+http://[\w.-]+:(\d+)", nginx)}
    assert upstreams and upstreams <= {8000}, f"nginx 上游端口漂移: {upstreams}"


def test_deployment_files_do_not_rebind_retired_ports():
    """Dockerfile / compose 不得再抄一份端口清单（口径只属于 services.conf）。"""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^EXPOSE\s+8000-", dockerfile, re.M), "EXPOSE 仍是多端口区间"
    assert not re.search(r"--ports[^\]]*80(0[1-7]|12)", dockerfile), "CMD 仍在显式传旧多端口"
    for name in ("docker-compose.yml", "docker-compose.full.yml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for p in re.findall(r"[\"']?(\d{4,5}):(\d{4,5})[\"']?\n", text):
            assert p[1] not in {str(x) for x in RETIRED_PORTS}, f"{name} 映射了退役端口 {p}"


_URL_PORT_RE = re.compile(r"(?:localhost|127\.0\.0\.1|backend):(\d{4,5})")


def test_no_outbound_address_targets_retired_ports():
    """源码/配置里的出站地址不得指向退役端口（包括本进程自调的语义层 URL）。

    semhub router 已拼进 web 进程，SEMANTIC_SERVICE_URL 若还指 :8012，
    收敛后代理目标不存在，语义层 playground/大屏取数会静默不可用。
    """
    targets = [
        "backend/app/serve.py",
        "backend/common/config.py",
        "backend/common/semantic_client.py",
        "backend/modules/mind/api/playground.py",
        "backend/modules/auth/api/monitoring.py",
        "frontend/vite.config.ts",
        "frontend/nginx.conf",
        "Dockerfile",
    ]
    offenders = []
    for rel in targets:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for m in _URL_PORT_RE.finditer(text):
            if int(m.group(1)) in RETIRED_PORTS:
                offenders.append(f"{rel}: :{m.group(1)}")
    assert not offenders, f"仍引用退役端口: {offenders}"
    assert "localhost:8012" not in _cfg.SEMANTIC_SERVICE_URL
    assert _cfg.SEMANTIC_SERVICE_URL.endswith(str(service_ports()[0]))


def test_monitoring_registry_matches_real_processes():
    """监控登记表只列真实监听进程（单一 web 口），不得把“一个进程健康”渲染成“8 个服务健康”。"""
    from backend.modules.auth.api import monitoring as mon

    ports = {s["port"] for s in mon.SERVICE_REGISTRY}
    assert ports <= {3000, service_ports()[0]}, f"探测口漂移: {ports}"
    keys = {s["key"] for s in mon.SERVICE_REGISTRY}
    assert "web" in keys and not keys & {"datamind", "semhub", "authservice"}
    # 分层输出不留空层（前端会把空层渲成“0 全部正常”的误导信号）
    layers = {s["layer"] for s in mon.SERVICE_REGISTRY}
    assert [x["key"] for x in mon.LAYERS] == [x["key"] for x in mon.LAYERS if x["key"] in layers]


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
