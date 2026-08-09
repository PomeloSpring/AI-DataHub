"""DataMind Microservice — AI Engine for NL2SQL, Agent, RAG, and Knowledge.

Run: uvicorn services.datamind.main:app --host 0.0.0.0 --port 8001 --reload

This service is a thin wrapper around the existing backend modules.
It exposes the AI capabilities as a standalone service with its own port and MCP server.
"""

import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path so we can import from services.*
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Configure logging
_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("datamind")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.datamind.api.chat import router as chat_router
from services.datamind.api.attachments import router as attachments_router
from services.datamind.api.agent import router as agent_router
from services.datamind.api.knowledge import router as knowledge_router
from services.datamind.api.knowledge_bases import router as knowledge_bases_router
from services.datamind.api.pipeline import router as pipeline_router
from services.datamind.api.query import router as query_router
from services.datamind.api.history import router as history_router
from services.datamind.api.playground import router as playground_router
from services.datamind.api.model_config import router as model_config_router
from services.datamind.api.execution import router as execution_router

app = FastAPI(
    title="DataMind API",
    description="DataMind AI Engine — NL2SQL, Agent, RAG, Knowledge capabilities",
    version="1.0.0",
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
app.include_router(chat_router, prefix="/api/chat", tags=["Chat / NL2SQL"])
app.include_router(attachments_router, prefix="/api/chat/attachments", tags=["Chat Attachments"])
app.include_router(agent_router, prefix="/api/agent", tags=["Agent Dispatch"])
app.include_router(knowledge_router, prefix="/api/knowledge", tags=["Knowledge Base"])
app.include_router(knowledge_bases_router, prefix="/api", tags=["Knowledge Bases Management"])
app.include_router(pipeline_router, prefix="/api/pipeline", tags=["Pipeline Execution"])
app.include_router(query_router, prefix="/api/query", tags=["SQL Query"])
app.include_router(history_router, prefix="/api/history", tags=["Query History"])
app.include_router(playground_router, prefix="/api/playground", tags=["SQL Playground"])
app.include_router(model_config_router, prefix="/api/model-config", tags=["Model Config"])
app.include_router(execution_router, prefix="/api/execution", tags=["Execution Layers"])

# Node metrics for distributed monitoring
from services.shared.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "datamind",
        "version": "1.0.0",
    }


@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup."""
    logger.info("DataMind service starting up...")
    # Pre-warm agent registry for deep/agent mode
    try:
        from services.datamind.nl2sql.orchestrator.pipeline_orchestrator import _init_agents
        _init_agents()
        logger.info("Agent registry initialized")
    except Exception as e:
        logger.warning("Agent registry init deferred: %s", e)


@app.on_event("shutdown")
def shutdown_event():
    """Flush pending Langfuse events on shutdown."""
    try:
        from services.shared.common.llm.langfuse_client import flush
        flush()
    except Exception:
        pass
    logger.info("DataMind service shut down")
