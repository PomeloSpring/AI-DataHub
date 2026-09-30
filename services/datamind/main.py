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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from services.shared import observability

from services.datamind.api.chat import router as chat_router
from services.datamind.api.attachments import router as attachments_router
from services.datamind.api.knowledge_bases import router as knowledge_bases_router
from services.datamind.api.skills_admin import router as skills_admin_router
from services.datamind.api.pipeline import router as pipeline_router
from services.datamind.api.history import router as history_router
from services.datamind.api.playground import router as playground_router
from services.datamind.api.model_config import router as model_config_router
from services.datamind.api.execution import router as execution_router
from services.datamind.api.as_bot import router as as_bot_router

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

# API 权限校验中间件
from services.shared.common.api_permission import add_api_permission_middleware
add_api_permission_middleware(app)


@app.middleware("http")
async def _trace_middleware(request: Request, call_next):
    """为每个请求分配 trace_id 并回写 X-Trace-Id 响应头。

    在 call_next 前设置 contextvar,使其传播到端点与流式生成器(子 task 拷贝上下文);
    实际 recorder 由各入口(chat/playground/agent)按身份创建。
    """
    trace_id = observability.set_trace_id()
    response = await call_next(request)
    try:
        response.headers["X-Trace-Id"] = trace_id
    except Exception:  # noqa: BLE001
        pass
    return response

# Include routers
app.include_router(chat_router, prefix="/api/chat", tags=["Chat / NL2SQL"])
app.include_router(attachments_router, prefix="/api/chat/attachments", tags=["Chat Attachments"])
app.include_router(knowledge_bases_router, prefix="/api", tags=["Knowledge Bases Management"])
app.include_router(skills_admin_router, prefix="/api", tags=["Skills Management"])
app.include_router(pipeline_router, prefix="/api/pipeline", tags=["Pipeline Execution"])
app.include_router(history_router, prefix="/api/history", tags=["Query History"])
app.include_router(playground_router, prefix="/api/playground", tags=["SQL Playground"])
app.include_router(model_config_router, prefix="/api/model-config", tags=["Model Config"])
app.include_router(execution_router, prefix="/api/execution", tags=["Execution Layers"])
app.include_router(as_bot_router, prefix="/api/as-bot", tags=["AS-BOT System Assistant"])

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
    import asyncio
    from services.datamind.execution.session_workspace import reconcile_stale_sessions
    repaired = await asyncio.to_thread(reconcile_stale_sessions)
    logger.info("Waker 会话恢复检查完成，中断执行数=%s", repaired)


@app.on_event("shutdown")
def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("DataMind service shut down")
