"""DataViz Microservice — Dashboards, Charts, and Reports.

Runs on port 8004. Provides CRUD for dashboards, chart data refresh,
and LLM-powered report generation.
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Add parent to path so shared imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.dataviz.api.dashboard import router as dashboard_router
from services.dataviz.api.chart import router as chart_router
from services.dataviz.api.report import router as report_router
from services.dataviz.api.component_data import router as component_data_router

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

# Mount routers
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["dashboards"])
app.include_router(chart_router, prefix="/api/charts", tags=["charts"])
app.include_router(report_router, prefix="/api/reports", tags=["reports"])
app.include_router(component_data_router, prefix="/api", tags=["component-data"])

# Node metrics for distributed monitoring
from services.shared.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/health")
async def health_check():
    """Service health check."""
    return {"status": "ok", "service": "dataviz", "port": 8004}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8004, reload=True)
