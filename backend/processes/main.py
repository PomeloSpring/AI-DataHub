"""AI-DataHub 合并 web 入口 —— 全部模块 router 拼装进一个 FastAPI app（Phase 4 单进程终态）。

Run: python -m backend.processes.serve        # 单进程绑定全部契约端口（生产/开发）
     python -m backend.processes.serve --reload   # 开发热重载（同为多端口）
     uvicorn backend.processes.main:app --port 8001   # 单端口调试

装配口径（以原 8 个进程壳 processes/<名>/main.py 为源逐模块迁移，壳已删除）：

- 对外 REST 路径/端口不变：router prefix 保持现状（/api/catalog、/api/quality、/api/chat…），
  vite/nginx 代理与 monitoring 探测无需改动；
- 去重：node-metrics 只挂一次；/health 与 /api/health 各留一个 handler，
  响应 body 按请求端口还原旧服务口径（monitoring 探测读 status/version 字段，逐字保留）；
- CORS 取并集（各壳同为 allow_origins=["*"]）、API 权限中间件只注册一次、
  trace 中间件（原 datamind 独有，X-Trace-Id）合并为全局、logging.basicConfig 收敛一份；
- startup 合并：flow 内置任务注册表落库 ensure_seeded + catalog KB 对账循环 +
  mind waker 会话恢复 reconcile_stale_sessions（各自失败语义与原壳一致）。

真撞裁决（启动即报重复路由 fail-loud，见 assert_no_duplicate_routes；FastAPI 对重复
路由静默首匹配，必须自建检查）：

1. POST /api/admin/sync/metadata[/columns]：catalog MetadataService 真实现胜出——
   platform 侧为 TODO 桩（假成功），响应同形 {success,message}，前端契约兼容，桩已删；
2. /api/admin/skills 命名空间：mind 技能管理胜出（前端 SkillsManager/AsBotManager 契约，
   vite /api/admin/skills → 8001）；platform 技能模板 API（adh_skill_templates，
   前端/测试/内部调用方为 0）与之真撞，不予挂载。
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend import observability

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── 旧服务 health 口径（按端口还原）────────────────────────────────────
# auth/api/monitoring.py 的探测读 body 的 status/version 字段，逐字保留不变。
_HEALTH_BODIES = {
    8001: {"status": "ok", "service": "datamind", "version": "1.0.0"},
    8002: {"status": "ok", "service": "datagov", "port": 8002},
    8003: {"status": "healthy", "service": "dataflow", "version": "1.0.0"},
    8004: {"status": "ok", "service": "dataviz", "port": 8004},
    8005: {"status": "ok", "service": "datacatalog"},
    8006: {"status": "ok", "service": "authservice"},
    8007: {"status": "ok", "service": "aiplatform", "version": "1.0.0"},
    8012: {"status": "ok", "service": "semhub", "port": 8012},
}


# ── health 口径 / 重复路由 fail-loud 检查 ────────────────────────────────────────────
def _health_body(request: Request) -> dict:
    """按请求端口还原旧服务 health 口径；非契约端口（容器汇聚口）返回合并口径。"""
    port = (request.scope.get("server") or (None, None))[1]
    return _HEALTH_BODIES.get(port) or {
        "status": "ok", "service": "ai-datahub", "version": "1.0.0",
    }


def _iter_effective_routes(routes):
    """展开 FastAPI(>=0.141) 的惰性 _IncludedRouter，产出带最终 path 的路由。"""
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            yield from _iter_effective_routes(r.effective_candidates())
        else:
            yield r


def _patterns_conflict(p1: str, p2: str) -> bool:
    """两个路径模板是否互匹配（同方法下会互相遮蔽）。"""
    s1, s2 = p1.strip("/").split("/"), p2.strip("/").split("/")
    if len(s1) != len(s2):
        return False
    for a, b in zip(s1, s2):
        if a == b:
            continue
        if not (a.startswith("{") or b.startswith("{")):
            return False  # 静态段不同 → 不可能同匹配
    return True


def assert_no_duplicate_routes(mounts):
    """启动即报重复路由（fail-loud）。

    mounts: [(tag, route_entry)]，tag 为来源模块名或 "shared"。
    跨 tag 真撞（精确重复或参数模式互匹配）→ RuntimeError；同 tag 内按注册序生效，
    与原进程壳行为一致（如 catalog menu/admin_compat 的先后次序），不在此裁决。
    """
    seen = {}          # (method, path) -> tag
    collected = []     # (tag, method, path)
    problems = []
    for tag, entry in mounts:
        for r in _iter_effective_routes([entry]):
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", None)
            if not path or not methods:
                continue
            for m in sorted(methods):
                if m in ("HEAD", "OPTIONS"):
                    continue
                key = (m, path)
                if key in seen and seen[key] != tag:
                    problems.append(f"{m} {path} 重复注册: {seen[key]} vs {tag}")
                seen.setdefault(key, tag)
                collected.append((tag, m, path))

    n = len(collected)
    for i in range(n):
        tag1, m1, p1 = collected[i]
        for j in range(i + 1, n):
            tag2, m2, p2 = collected[j]
            if tag1 == tag2 or m1 != m2 or p1 == p2:
                continue
            if _patterns_conflict(p1, p2):
                problems.append(f"{m1} 路径模式互匹配遮蔽: {p1} ({tag1}) vs {p2} ({tag2})")

    if problems:
        raise RuntimeError(
            "合并入口路由真撞（fail-loud）——请按文件头「真撞裁决」消解或登记:\n"
            + "\n".join(sorted(set(problems)))
        )


# ── App 装配 ───────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    mounts = []  # (tag, route_entry)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """合并启动流程：任务注册表落库 + KB 对账循环 + waker 会话恢复。"""
        logger.info("AI-DataHub web starting up...")
        # flow: 内置任务注册表落库(名称/职责/周期以代码为准，不覆盖暂停开关与运行态)
        from backend.common.system_jobs import ensure_seeded
        ensure_seeded()
        # catalog: 本体→知识库同步对账循环(T9)；KB_SYNC_RECONCILE=0 可关闭
        if os.getenv("KB_SYNC_RECONCILE", "1") != "0":
            try:
                from backend.modules.catalog.services.ontology_kb_sync import start_reconciler
                start_reconciler(int(os.getenv("KB_SYNC_RECONCILE_INTERVAL", "600")))
                logger.info("[datacatalog] kb-sync reconciler started")
            except Exception as e:  # noqa: BLE001 与原壳一致：对账失败只记日志不影响服务
                logger.warning("[datacatalog] kb-sync reconciler not started: %s", e)
        # mind: waker 会话恢复检查
        import asyncio
        from backend.modules.mind.execution.session_workspace import reconcile_stale_sessions
        repaired = await asyncio.to_thread(reconcile_stale_sessions)
        logger.info("Waker 会话恢复检查完成，中断执行数=%s", repaired)
        yield
        logger.info("AI-DataHub web shut down")

    app = FastAPI(
        title="AI-DataHub API",
        description="AI-DataHub 数据中台合并入口 —— 目录/治理/可视化/编排/流程/认证/平台/语义层",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS（原 7 份配置同为全放开，取并集即此一份）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API 权限校验中间件（权限码驱动，由 adh_perm_registry 统一声明）——只注册一次
    from backend.common.api_permission import add_api_permission_middleware
    add_api_permission_middleware(app)

    @app.middleware("http")
    async def _trace_middleware(request: Request, call_next):
        """为每个请求分配 trace_id 并回写 X-Trace-Id 响应头（原 datamind 独有，合并为全局）。

        在 call_next 前设置 contextvar,使其传播到端点与流式生成器(子 task 拷贝上下文);
        实际 recorder 由各入口(chat/playground/agent)按身份创建。
        """
        trace_id = observability.set_trace_id()
        response = await call_next(request)
        try:
            response.headers["X-Trace-Id"] = trace_id
        except Exception:  # noqa: BLE001
            pass
        return response

    def mount(tag: str, router, prefix: str = "", tags=None):
        before = len(app.router.routes)
        app.include_router(router, prefix=prefix, tags=tags or [])
        for entry in app.router.routes[before:]:
            mounts.append((tag, entry))

    # ── auth（原 processes/auth/main.py，8006）─────────────────────────
    from backend.modules.auth.api.auth import router as auth_router
    from backend.modules.auth.api.users import router as users_router
    from backend.modules.auth.api.workspaces import router as workspaces_router
    from backend.modules.auth.api.roles import router as roles_router
    from backend.modules.auth.api.audit import router as audit_router
    from backend.modules.auth.api.rls import router as rls_router
    from backend.modules.auth.api.monitoring import router as monitoring_router
    from backend.modules.auth.api.observability import router as observability_router
    from backend.modules.auth.api.knowledge import router as knowledge_router
    from backend.modules.auth.api.eval import router as eval_router

    mount("auth", auth_router, "/api/auth", ["auth"])
    mount("auth", users_router, "/api/users", ["users"])
    mount("auth", workspaces_router, "/api/workspaces", ["workspaces"])
    mount("auth", roles_router, "/api/roles", ["roles"])
    mount("auth", audit_router, "/api/audit", ["audit"])
    mount("auth", rls_router, "/api/admin", ["RLS Security"])
    mount("auth", monitoring_router, "/api/monitoring", ["monitoring"])
    mount("auth", observability_router, "/api/observability", ["observability"])
    mount("auth", knowledge_router, "/api/admin", ["knowledge"])
    mount("auth", eval_router, "/api/eval", ["eval"])

    # ── catalog（原 processes/catalog/main.py，8005）──────────────────
    from backend.modules.catalog.api.catalog import router as catalog_router
    from backend.modules.catalog.api.metadata import router as metadata_router
    from backend.modules.catalog.api.templates import router as templates_router
    from backend.modules.catalog.api.glossary import router as glossary_router
    from backend.modules.catalog.api.lineage import router as lineage_router
    from backend.modules.catalog.api.metrics import router as metrics_router
    from backend.modules.catalog.api.tags import router as tags_router
    from backend.modules.catalog.api.datasources import router as datasources_router
    from backend.modules.catalog.api.menu import router as menu_router
    from backend.modules.catalog.api.admin_compat import router as admin_compat_router
    from backend.modules.catalog.api.ontology import router as ontology_router
    from backend.modules.catalog.api.data_products import router as data_products_router

    mount("catalog", catalog_router, "/api/catalog", ["Catalog"])
    mount("catalog", metadata_router, "/api/admin", ["Metadata Admin"])
    mount("catalog", templates_router, "/api/admin/templates", ["Templates Admin"])
    mount("catalog", glossary_router, "/api/admin/terms", ["Glossary Admin"])
    mount("catalog", lineage_router, "/api/admin/relations", ["Lineage Admin"])
    mount("catalog", metrics_router, "/api/metrics", ["Metrics"])
    mount("catalog", tags_router, "/api/tags", ["Tags"])
    mount("catalog", datasources_router, "/api/datasources", ["Datasources"])
    # 数据产品(L1 治理身份)：清单/详情/手工认领；挂 /api/catalog 下便于前端按目录分组
    mount("catalog", data_products_router, "/api/catalog/data-products", ["DataProducts"])
    mount("catalog", menu_router, "/api/menu", ["Menu"])
    # 同一套菜单 CRUD 也挂到 /api/admin/menu-tree(前端 MenuEditorTab 调用的路径),
    # 必须先于 admin_compat(其 GET 只有裸查询、无写操作端点)
    mount("catalog", menu_router, "/api/admin", ["Menu Admin Compat"])
    mount("catalog", admin_compat_router, "/api/admin", ["Admin Compat"])
    mount("catalog", ontology_router, "/api/catalog/ontology", ["Ontology Modeling"])

    # ── flow（原 processes/flow/main.py，8003）────────────────────────
    from backend.modules.flow.api.sync import router as sync_router
    from backend.modules.flow.api.scheduled import router as scheduled_router
    from backend.modules.flow.api.scheduled import templates_router as report_templates_router
    from backend.modules.flow.api.notification import router as notification_router
    from backend.modules.flow.api.task_monitor import router as task_monitor_router
    from backend.modules.flow.api.dag import router as dag_router
    from backend.modules.flow.api.udf import router as udf_router

    mount("flow", sync_router, "/api/sync", ["Sync"])
    mount("flow", dag_router, "/api/dag", ["DAG Workflows"])
    mount("flow", udf_router, "/api", ["UDF"])
    mount("flow", scheduled_router, "/api/scheduled-tasks", ["Scheduled Tasks"])
    mount("flow", report_templates_router, "/api/report-templates", ["Report Templates"])
    mount("flow", notification_router, "/api/notification", ["Notifications"])
    mount("flow", task_monitor_router, "/api/task-monitor", ["Task Monitor"])

    # ── gov（原 processes/gov/main.py，8002）──────────────────────────
    from backend.modules.gov.api.quality import router as quality_router
    from backend.modules.gov.api.lineage import router as gov_lineage_router
    from backend.modules.gov.api.standards import router as standards_router
    from backend.modules.gov.api.security import router as security_router

    mount("gov", quality_router, "/api/quality", ["数据质量"])
    mount("gov", gov_lineage_router, "/api/lineage", ["数据血缘"])
    mount("gov", standards_router, "/api/standards", ["数据标准"])
    mount("gov", security_router, "/api/security", ["敏感数据"])

    # ── mind（原 processes/mind/main.py，8001）────────────────────────
    from backend.modules.mind.api.chat import router as chat_router
    from backend.modules.mind.api.session_files import router as session_files_router
    from backend.modules.mind.api.knowledge_bases import router as knowledge_bases_router
    from backend.modules.mind.api.skills_admin import router as skills_admin_router
    from backend.modules.mind.api.pipeline import router as pipeline_router
    from backend.modules.mind.api.history import router as history_router
    from backend.modules.mind.api.playground import router as playground_router
    from backend.modules.mind.api.model_config import router as model_config_router
    from backend.modules.mind.api.execution import router as execution_router
    from backend.modules.mind.api.as_bot import router as as_bot_router
    from backend.modules.mind.api.user_assets import router as user_assets_router
    from backend.modules.mind.api.user_assets import workspace_router as workspace_artifacts_router

    mount("mind", chat_router, "/api/chat", ["Chat / NL2SQL"])
    mount("mind", session_files_router, "/api/chat", ["Session Files Preview"])
    mount("mind", knowledge_bases_router, "/api", ["Knowledge Bases Management"])
    mount("mind", skills_admin_router, "/api", ["Skills Management"])
    mount("mind", pipeline_router, "/api/pipeline", ["Pipeline Execution"])
    mount("mind", history_router, "/api/history", ["Query History"])
    mount("mind", playground_router, "/api/playground", ["SQL Playground"])
    mount("mind", model_config_router, "/api/model-config", ["Model Config"])
    mount("mind", execution_router, "/api/execution", ["Execution Layers"])
    mount("mind", as_bot_router, "/api/as-bot", ["AS-BOT System Assistant"])
    mount("mind", user_assets_router, "/api/assets", ["User Assets"])
    mount("mind", workspace_artifacts_router, "/api/workspace-assets", ["Workspace Session Artifacts"])

    # ── platform（原 processes/platform/main.py，8007）────────────────
    # skills_router（技能模板）与 mind 技能管理真撞且消费方为 0，不予挂载（见文件头裁决②）；
    # sync_metadata（TODO 桩）已删，/api/admin/sync/metadata 由 catalog 真实现承载（裁决①）。
    from backend.modules.platform.api.mcp_servers import router as mcp_servers_router
    from backend.modules.platform.api.agents import router as agents_router
    from backend.modules.platform.api.embed import router as embed_router
    from backend.modules.platform.api.model_train import router as model_train_router
    from backend.modules.platform.api.mcp_market import router as mcp_market_router
    from backend.modules.platform.api.model_config import router as platform_model_config_router
    from backend.modules.platform.api.brand import router as brand_router
    from backend.modules.platform.api.cache import router as cache_router
    from backend.modules.platform.api.execution_layers import router as execution_layers_router
    from backend.modules.platform.api.prompts import router as prompts_router
    from backend.modules.platform.api.as_bots import router as as_bots_router
    from backend.modules.platform.api.sandbox import router as sandbox_router

    mount("platform", mcp_servers_router, "/api/admin/mcp-servers", ["MCP Servers"])
    mount("platform", agents_router, "/api/admin/agents", ["Agents"])
    mount("platform", embed_router, "/api/embed", ["Embed"])
    mount("platform", model_train_router, "/api/model-train", ["Model Train"])
    mount("platform", mcp_market_router, "/api/mcp-market", ["MCP Market"])
    mount("platform", platform_model_config_router, "/api/admin/model-config", ["Model Config"])
    mount("platform", brand_router, "/api/admin/brand", ["Brand"])
    mount("platform", cache_router, "/api/admin/cache", ["Cache"])
    mount("platform", execution_layers_router, "/api/admin/execution-layers", ["Execution Layers"])
    mount("platform", prompts_router, "/api/admin/prompts", ["Prompts"])
    mount("platform", as_bots_router, "/api/admin/as-bots", ["AS-BOTs"])
    mount("platform", sandbox_router, "/api/sandbox", ["Sandbox"])

    # ── semhub（原 processes/semhub/main.py，8012）────────────────────
    from backend.modules.semhub.api.graph import router as graph_router
    from backend.modules.semhub.api.playground import router as semhub_playground_router
    from backend.modules.semhub.api.semantic import router as semantic_router

    mount("semhub", semantic_router)
    mount("semhub", semhub_playground_router)
    # 知识图谱 (原 graphservice:8011 并入): /api/graph 契约保持不变
    mount("semhub", graph_router, "/api/graph", ["知识图谱"])

    # ── viz（原 processes/viz/main.py，8004）──────────────────────────
    from backend.modules.viz.api.dashboard import router as dashboard_router
    from backend.modules.viz.api.chart import router as chart_router
    from backend.modules.viz.api.report import router as report_router
    from backend.modules.viz.api.component_data import router as component_data_router
    from backend.modules.viz.api.vis_library import router as vis_library_router
    from backend.modules.viz.api.datasets import router as datasets_router

    mount("viz", dashboard_router, "/api/dashboard", ["dashboards"])
    mount("viz", chart_router, "/api/charts", ["charts"])
    mount("viz", report_router, "/api/reports", ["reports"])
    mount("viz", component_data_router, "/api", ["component-data"])
    mount("viz", vis_library_router, "/api/vis-library", ["vis-library"])
    mount("viz", datasets_router, "/api/datasets", ["datasets"])

    # ── 共享根路由 ────────────────────────────────────────────────────
    # node-metrics（原 9 个进程壳各挂一份，合并后只挂一次）
    from backend.common.system_metrics import router as node_metrics_router
    mount("shared", node_metrics_router, "", ["node-metrics"])

    def _health_response(request: Request) -> dict:
        return _health_body(request)

    @app.get("/health", tags=["health"])
    def health(request: Request):
        """Health check endpoint（body 按端口还原旧服务口径）。"""
        return _health_response(request)

    @app.get("/api/health", tags=["health"])
    def api_health(request: Request):
        """Health check endpoint（body 按端口还原旧服务口径）。"""
        return _health_response(request)

    @app.get("/", tags=["semhub"])
    async def root():
        return {
            "service": "semhub",
            "version": "0.2.0",
            "endpoints": [
                "GET  /api/health",
                "POST /api/semantic/resolve",
                "POST /api/semantic/query",
                "GET  /api/semantic/health",
                "POST /api/semantic/playground/ast",
                "POST /api/semantic/playground/lineage",
                "POST /api/semantic/playground/rls-diff",
                "POST /api/semantic/playground/provenance",
                "*    /api/graph/* (知识图谱: query/search/nodes/sync/... 原 graphservice)",
            ],
        }

    # 直接挂在 app 上的路由归入 shared
    mounted_ids = {id(e) for _, e in mounts}
    for entry in app.router.routes:
        if id(entry) not in mounted_ids:
            mounts.append(("shared", entry))

    assert_no_duplicate_routes(mounts)
    return app


app = create_app()
