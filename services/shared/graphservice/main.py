"""GraphService Microservice — Neo4j Knowledge Graph API.

Run: uvicorn services.shared.graphservice.main:app --host 0.0.0.0 --port 8011
"""

import logging
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.shared.graphservice.api.graph import router as graph_router

_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _log_level, logging.INFO), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(title="GraphService API", description="知识图谱服务 — Neo4j 图查询", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(graph_router, prefix="/api/graph", tags=["知识图谱"])

# Node metrics for distributed monitoring
from services.shared.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "graphservice", "port": 8011}
