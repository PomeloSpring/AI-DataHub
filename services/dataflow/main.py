"""DataFlow Microservice — FastAPI entry point.

Handles data sync, workflow orchestration (via Airflow integration),
scheduled tasks, and notifications.

Runs on port 8003.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.dataflow.api.sync import router as sync_router
from services.dataflow.api.workflow import router as workflow_router
from services.dataflow.api.scheduled import router as scheduled_router
from services.dataflow.api.scheduled import templates_router as report_templates_router
from services.dataflow.api.notification import router as notification_router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("DataFlow service starting up")
    yield
    logger.info("DataFlow service shutting down")


app = FastAPI(redirect_slashes=True,
    title="AI-DataHub DataFlow Service",
    description="Data sync, workflow orchestration, scheduled tasks, and notifications",
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
app.include_router(sync_router, prefix="/api/sync", tags=["Sync"])
app.include_router(workflow_router, prefix="/api/workflow", tags=["Workflow"])
app.include_router(scheduled_router, prefix="/api/scheduled-tasks", tags=["Scheduled Tasks"])
app.include_router(report_templates_router, prefix="/api/report-templates", tags=["Report Templates"])
app.include_router(notification_router, prefix="/api/notification", tags=["Notifications"])

# Node metrics for distributed monitoring
from services.shared.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "dataflow", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("DATAFLOW_PORT", "8003"))
    uvicorn.run(
        "services.dataflow.main:app",
        host="0.0.0.0",
        port=port,
        reload=os.getenv("ENV", "development") == "development",
    )
