"""DataGov Microservice — Data Quality, Lineage, Standards, and Security.

Run: uvicorn services.datagov.main:app --host 0.0.0.0 --port 8002 --reload
"""

import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path for shared imports
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Also add the services directory so `shared` can be imported directly
_services_root = Path(__file__).resolve().parent.parent
if str(_services_root) not in sys.path:
    sys.path.insert(0, str(_services_root))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.datagov.api.quality import router as quality_router
from services.datagov.api.lineage import router as lineage_router
from services.datagov.api.standards import router as standards_router
from services.datagov.api.security import router as security_router

# ── Logging ──────────────────────────────────────────────────────────
_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("datagov")

# ── App ──────────────────────────────────────────────────────────────
app = FastAPI(redirect_slashes=True,
    title="DataGov API",
    description="数据治理服务 — 数据质量、数据血缘、数据标准、敏感数据管理",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────────
app.include_router(quality_router, prefix="/api/quality", tags=["数据质量"])
app.include_router(lineage_router, prefix="/api/lineage", tags=["数据血缘"])
app.include_router(standards_router, prefix="/api/standards", tags=["数据标准"])
app.include_router(security_router, prefix="/api/security", tags=["敏感数据"])

# Node metrics for distributed monitoring
from services.shared.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/api/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "datagov", "port": 8002}
