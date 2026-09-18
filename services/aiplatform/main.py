"""AI Platform Microservice — MCP Servers, Agents, Embed, Model Config/Lab/Train, Brand, Cache.

Port: 8007
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.aiplatform.api.mcp_servers import router as mcp_servers_router
from services.aiplatform.api.agents import router as agents_router
from services.aiplatform.api.embed import router as embed_router
from services.aiplatform.api.model_train import router as model_train_router
from services.aiplatform.api.sync_metadata import router as sync_metadata_router
from services.aiplatform.api.mcp_market import router as mcp_market_router
from services.aiplatform.api.model_config import router as model_config_router
from services.aiplatform.api.skills import router as skills_router
from services.aiplatform.api.brand import router as brand_router
from services.aiplatform.api.cache import router as cache_router
from services.aiplatform.api.execution_layers import router as execution_layers_router
from services.aiplatform.api.prompts import router as prompts_router
from services.aiplatform.api.wakers import router as wakers_router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("aiplatform")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("AIPlatform service starting up")
    yield
    logger.info("AIPlatform service shutting down")


app = FastAPI(
    title="AI Platform Service",
    description="MCP Servers, Agents, Embed integration, Model Config/Lab/Train, Brand, Cache",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
# 权限码驱动的 API 鉴权(由 adh_perm_registry 统一声明, 与 authservice/datamind 一致)
from services.shared.common.api_permission import add_api_permission_middleware
add_api_permission_middleware(app)

app.include_router(mcp_servers_router, prefix="/api/admin/mcp-servers", tags=["MCP Servers"])
app.include_router(agents_router, prefix="/api/admin/agents", tags=["Agents"])
app.include_router(embed_router, prefix="/api/embed", tags=["Embed"])
app.include_router(model_train_router, prefix="/api/model-train", tags=["Model Train"])
app.include_router(sync_metadata_router, prefix="/api/admin/sync", tags=["Sync Metadata"])
app.include_router(mcp_market_router, prefix="/api/mcp-market", tags=["MCP Market"])
app.include_router(model_config_router, prefix="/api/admin/model-config", tags=["Model Config"])
app.include_router(skills_router, prefix="/api/admin", tags=["Skills"])
app.include_router(brand_router, prefix="/api/admin/brand", tags=["Brand"])
app.include_router(cache_router, prefix="/api/admin/cache", tags=["Cache"])
app.include_router(execution_layers_router, prefix="/api/admin/execution-layers", tags=["Execution Layers"])
app.include_router(prompts_router, prefix="/api/admin/prompts", tags=["Prompts"])
app.include_router(wakers_router, prefix="/api/admin/wakers", tags=["Wakers"])

# Node metrics for distributed monitoring
from services.shared.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "aiplatform", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
