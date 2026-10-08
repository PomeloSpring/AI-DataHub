"""SemanticLayer Microservice — Palantir-style semantic layer for BI/ChatBI + dataviz.

Port: 8012

Role (per 语义层架构 定稿):
- 独占 binding 解析 + MDL 编译 + 选路 planner + 护栏/权限注入
- LLM(ChatBI) 与 dataviz 大屏共用 SemanticQuery/SemanticResult 契约
- Phase 3 迁入 rag/ontology_traversal / graphrag 检索
- Phase 6 迁入 SQL Playground (原 datamind/api/playground.py)

Phase 1: 只暴露只读契约端点 (resolve/query/health), 执行 stub 到 Phase 4。
"""

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.modules.semhub.api.graph import router as graph_router
from backend.modules.semhub.api.playground import router as playground_router
from backend.modules.semhub.api.semantic import router as semantic_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    redirect_slashes=True,
    title="SemanticLayer Service",
    description="BI/ChatBI -> 语义层 -> 执行层 三层架构的中间层: 本体 SSoT, binding/MDL/选路/护栏; 并含知识图谱(/api/graph, 原 graphservice 并入)",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.common.api_permission import add_api_permission_middleware
add_api_permission_middleware(app)
app.include_router(semantic_router)
app.include_router(playground_router)
# 知识图谱 (原 graphservice:8011 并入): /api/graph 契约保持不变, 现由本服务(8012)提供
app.include_router(graph_router, prefix="/api/graph", tags=["知识图谱"])

# Node metrics for distributed monitoring
from backend.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "semhub", "port": 8012}


@app.get("/")
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8012)
