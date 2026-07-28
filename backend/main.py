"""ChatBI FastAPI backend entry point.
Run: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

Environment variables:
    LOG_LEVEL — logging level (DEBUG/INFO/WARNING/ERROR), default INFO
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Colored Log Formatter ───────────────────────────────────────────
class ColoredFormatter(logging.Formatter):
    """Formatter that adds ANSI colors to log levels."""

    COLORS = {
        logging.DEBUG:    "\033[36m",    # Cyan
        logging.INFO:     "\033[32m",    # Green
        logging.WARNING:  "\033[33m",    # Yellow
        logging.ERROR:    "\033[1;31m",  # Bold Red
        logging.CRITICAL: "\033[1;35m",  # Bold Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        # Save original levelname
        orig_levelname = record.levelname
        color = self.COLORS.get(record.levelno, "")
        if color:
            record.levelname = f"{color}{record.levelname}{self.RESET}"
            record.msg = f"{color}{record.msg}{self.RESET}"
        result = super().format(record)
        # Restore original
        record.levelname = orig_levelname
        return result

# Configure logging level from environment
_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
_handler = logging.StreamHandler()
_handler.setFormatter(ColoredFormatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    handlers=[_handler],
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.api import auth, chat, query, dashboard, history, admin, playground, datasource, embed
from backend.api.mcp_market import router as mcp_market_router
from backend.api.menu import router as menu_router
from backend.api.component_data import router as component_data_router
from backend.api.model_lab import router as model_lab_router
from backend.api.model_train import router as model_train_router
from backend.api.model_config import router as model_config_router
from backend.api.admin_workflow import router as admin_workflow_router
from backend.api import pipeline
from backend.api.workspace_v2 import router as workspace_router
from backend.api.scheduled_task import router as scheduled_task_router
from backend.api.sandbox import router as sandbox_router

app = FastAPI(title="ChatBI API", description="ChatBI 数据分析助手 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://172.23.27.104:3000",
        # Allow any origin for development
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(chat.router, prefix="/api/chat", tags=["对话"])
app.include_router(query.router, prefix="/api/query", tags=["查询"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["仪表盘"])
app.include_router(history.router, prefix="/api/history", tags=["历史"])
app.include_router(admin.router, prefix="/api/admin", tags=["管理"])
app.include_router(playground.router, prefix="/api/playground", tags=["Playground"])
app.include_router(datasource.router, prefix="/api/datasources", tags=["Datasources"])
app.include_router(menu_router, prefix="/api/admin", tags=["menu"])
app.include_router(component_data_router, prefix="/api", tags=["component-data"])
app.include_router(model_lab_router, prefix="/api/model-lab", tags=["ModelLab"])
app.include_router(model_train_router, prefix="/api/model-train", tags=["ModelTrain"])
app.include_router(model_config_router, prefix="/api/model-config", tags=["ModelConfig"])
app.include_router(embed.router, prefix="/api/embed", tags=["嵌入集成"])
app.include_router(admin_workflow_router, prefix="/api/admin", tags=["工作流管理"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["Pipeline"])
app.include_router(mcp_market_router, prefix="/api/mcp-market", tags=["MCP市场"])
app.include_router(workspace_router, prefix="/api/workspaces", tags=["工作空间"])
app.include_router(scheduled_task_router, prefix="/api", tags=["定时任务"])
app.include_router(sandbox_router, prefix="/api/sandbox", tags=["沙箱管理"])

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.on_event("shutdown")
def shutdown_event():
    """Flush pending Langfuse events on app shutdown."""
    try:
        from backend.common.llm.langfuse_client import flush
        flush()
    except Exception:
        pass
