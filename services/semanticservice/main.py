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

from .api.playground import router as playground_router
from .api.semantic import router as semantic_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    redirect_slashes=True,
    title="SemanticLayer Service",
    description="BI/ChatBI -> 语义层 -> 执行层 三层架构的中间层: 本体 SSoT, binding/MDL/选路/护栏",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 权限码驱动的 API 鉴权(由 adh_perm_registry 统一声明, 与 authservice/datamind 一致)
from services.shared.common.api_permission import add_api_permission_middleware
add_api_permission_middleware(app)

app.include_router(semantic_router)
app.include_router(playground_router)


@app.get("/")
async def root():
    return {
        "service": "semanticservice",
        "version": "0.1.0",
        "endpoints": [
            "POST /api/semantic/resolve",
            "POST /api/semantic/query",
            "GET  /api/semantic/health",
            "POST /api/semantic/playground/ast",
            "POST /api/semantic/playground/lineage",
            "POST /api/semantic/playground/rls-diff",
            "POST /api/semantic/playground/provenance",
        ],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8012)
