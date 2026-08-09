"""VectorService — FastAPI microservice for Doris HNSW vector search.

Provides REST API + MCP Server for vector similarity search,
upsert, and management operations on Doris vector tables.

REST API: port 8010
MCP SSE Server: port 31010
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add shared modules to path
_shared_dir = Path(__file__).resolve().parent.parent
if str(_shared_dir.parent) not in sys.path:
    sys.path.insert(0, str(_shared_dir.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.shared.vectorservice.api.vector import router as vector_router
from services.shared.vectorservice.vector_db import close_vector_pool, get_vector_pool_stats

logger = logging.getLogger(__name__)

# ── Logging Setup ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ── Lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("VectorService starting up...")
    logger.info("Vector pool stats: %s", get_vector_pool_stats())
    yield
    logger.info("VectorService shutting down...")
    close_vector_pool()


# ── FastAPI App ───────────────────────────────────────────────────────

app = FastAPI(
    title="VectorService",
    description="Doris HNSW vector search microservice — REST API + MCP Server",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(vector_router, prefix="/api/vector", tags=["vector"])


# ── Node Metrics (distributed monitoring) ────────────────────────────
from services.shared.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])

# ── Health Check ──────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    pool_stats = get_vector_pool_stats()
    return {
        "status": "healthy",
        "service": "vectorservice",
        "version": "1.0.0",
        "vector_pool": pool_stats,
    }


# ── Entry Point ───────────────────────────────────────────────────────

def _run_mcp_server():
    """Run MCP SSE server on port 31010 in a subprocess."""
    import subprocess
    mcp_script = Path(__file__).resolve().parent / "mcp_server.py"
    proc = subprocess.Popen(
        [sys.executable, str(mcp_script)],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return proc


if __name__ == "__main__":
    import uvicorn
    import os

    # Start MCP server subprocess
    mcp_proc = _run_mcp_server()
    logger.info("MCP SSE server started on port 31010 (pid=%d)", mcp_proc.pid)

    try:
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=int(os.getenv("VECTOR_SERVICE_PORT", "8010")),
            reload=False,
            log_level="info",
        )
    finally:
        mcp_proc.terminate()
        mcp_proc.wait(timeout=5)
        logger.info("MCP SSE server stopped.")
