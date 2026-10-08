"""AuthService — FastAPI entry point (port 8006).

Handles user management, authentication, workspaces, RBAC, and audit logging.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.common.config import SERVICE_PORTS
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

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("AuthService starting on port %d ...", SERVICE_PORTS["authservice"])
    yield
    logger.info("AuthService shutting down.")


app = FastAPI(
    title="AuthService",
    description="User management, authentication, workspaces, RBAC, and audit",
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

# API 权限校验中间件
from backend.common.api_permission import add_api_permission_middleware
add_api_permission_middleware(app)

# Include routers
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(users_router, prefix="/api/users", tags=["users"])
app.include_router(workspaces_router, prefix="/api/workspaces", tags=["workspaces"])
app.include_router(roles_router, prefix="/api/roles", tags=["roles"])
app.include_router(audit_router, prefix="/api/audit", tags=["audit"])
app.include_router(rls_router, prefix="/api/admin", tags=["RLS Security"])
app.include_router(monitoring_router, prefix="/api/monitoring", tags=["monitoring"])
app.include_router(observability_router, prefix="/api/observability", tags=["observability"])
app.include_router(knowledge_router, prefix="/api/admin", tags=["knowledge"])
app.include_router(eval_router, prefix="/api/eval", tags=["eval"])

# Node metrics for distributed monitoring
from backend.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/health")
def health():
    return {"status": "ok", "service": "authservice"}


if __name__ == "__main__":
    import uvicorn

    port = SERVICE_PORTS["authservice"]
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
