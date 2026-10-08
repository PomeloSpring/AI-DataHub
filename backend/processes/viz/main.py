"""DataViz Microservice — Dashboards, Charts, and Reports.

Runs on port 8004. Provides CRUD for dashboards, chart data refresh,
and LLM-powered report generation.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.modules.viz.api.dashboard import router as dashboard_router
from backend.modules.viz.api.chart import router as chart_router
from backend.modules.viz.api.report import router as report_router
from backend.modules.viz.api.component_data import router as component_data_router
from backend.modules.viz.api.vis_library import router as vis_library_router
from backend.modules.viz.api.datasets import router as datasets_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("dataviz")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    logger.info("DataViz service starting on port 8004")
    yield
    logger.info("DataViz service shutting down")


app = FastAPI(redirect_slashes=True,
    title="DataViz Service",
    description="Dashboards, charts, and report generation",
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

from backend.common.api_permission import add_api_permission_middleware
add_api_permission_middleware(app)

# Mount routers
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["dashboards"])
app.include_router(chart_router, prefix="/api/charts", tags=["charts"])
app.include_router(report_router, prefix="/api/reports", tags=["reports"])
app.include_router(component_data_router, prefix="/api", tags=["component-data"])
app.include_router(vis_library_router, prefix="/api/vis-library", tags=["vis-library"])
app.include_router(datasets_router, prefix="/api/datasets", tags=["datasets"])

# Node metrics for distributed monitoring
from backend.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/health")
async def health_check():
    """Service health check."""
    return {"status": "ok", "service": "dataviz", "port": 8004}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8004, reload=True)
