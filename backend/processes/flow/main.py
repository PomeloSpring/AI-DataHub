"""DataFlow Microservice — FastAPI entry point.

Handles data sync, scheduled tasks, and notifications.

Runs on port 8003.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.modules.flow.api.sync import router as sync_router
from backend.modules.flow.api.scheduled import router as scheduled_router
from backend.modules.flow.api.scheduled import templates_router as report_templates_router
from backend.modules.flow.api.notification import router as notification_router
from backend.modules.flow.api.task_monitor import router as task_monitor_router
from backend.modules.flow.api.dag import router as dag_router
from backend.modules.flow.api.udf import router as udf_router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("DataFlow service starting up")
    # 内置任务注册表落库(名称/职责/周期以代码为准，不覆盖暂停开关与运行态)
    from backend.common.system_jobs import ensure_seeded
    ensure_seeded()
    yield
    logger.info("DataFlow service shutting down")


app = FastAPI(redirect_slashes=True,
    title="AI-DataHub DataFlow Service",
    description="Data sync, scheduled tasks, and notifications",
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
from backend.common.api_permission import add_api_permission_middleware
add_api_permission_middleware(app)

app.include_router(sync_router, prefix="/api/sync", tags=["Sync"])
app.include_router(dag_router, prefix="/api/dag", tags=["DAG Workflows"])
app.include_router(udf_router, prefix="/api", tags=["UDF"])
app.include_router(scheduled_router, prefix="/api/scheduled-tasks", tags=["Scheduled Tasks"])
app.include_router(report_templates_router, prefix="/api/report-templates", tags=["Report Templates"])
app.include_router(notification_router, prefix="/api/notification", tags=["Notifications"])
app.include_router(task_monitor_router, prefix="/api/task-monitor", tags=["Task Monitor"])

# Node metrics for distributed monitoring
from backend.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "dataflow", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("DATAFLOW_PORT", "8003"))
    uvicorn.run(
        "backend.processes.flow.main:app",
        host="0.0.0.0",
        port=port,
        reload=os.getenv("ENV", "development") == "development",
    )
