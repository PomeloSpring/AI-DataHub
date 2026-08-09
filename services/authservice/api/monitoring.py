"""Monitoring API routes — aggregated service health probing and node metrics.

Distributed-deployment aware:
- Each service can live on a different host: override via env
  MONITOR_HOST_<SERVICE_KEY> (e.g. MONITOR_HOST_DATAMIND=10.0.1.5),
  falling back to MONITOR_HOST (default 127.0.0.1).
- Node metrics are collected by calling each service's local
  /system-metrics endpoint and deduplicating by hostname, so every
  machine hosting services shows up exactly once.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends

from services.shared.common.auth import require_admin
from services.shared.common.system_metrics import collect_local_metrics

logger = logging.getLogger(__name__)

router = APIRouter()

# Default host where services are reachable (non-containerized dev: localhost)
_DEFAULT_HOST = os.getenv("MONITOR_HOST", "127.0.0.1")

# Architecture layers (order defines display order)
LAYERS = [
    {"key": "access", "name": "接入层", "desc": "用户界面与请求入口"},
    {"key": "app", "name": "业务服务层", "desc": "核心业务微服务"},
    {"key": "ai", "name": "AI 智能层", "desc": "模型管理与智能分析"},
    {"key": "infra", "name": "基础引擎层", "desc": "查询引擎与向量 / 图数据库服务"},
]

# Service registry (ordered by layer)
SERVICE_REGISTRY = [
    {"key": "frontend", "name": "前端应用", "desc": "React SPA (Vite)", "port": 3000, "path": "/", "layer": "access"},
    {"key": "authservice", "name": "AuthService", "desc": "认证 / RBAC / 审计", "port": 8006, "path": "/health", "layer": "app"},
    {"key": "datacatalog", "name": "DataCatalog", "desc": "数据目录 / 元数据", "port": 8005, "path": "/health", "layer": "app"},
    {"key": "dataviz", "name": "DataViz", "desc": "仪表盘 / 图表 / 报表", "port": 8004, "path": "/health", "layer": "app"},
    {"key": "dataflow", "name": "DataFlow", "desc": "数据同步 / 工作流调度", "port": 8003, "path": "/health", "layer": "app"},
    {"key": "datagov", "name": "DataGov", "desc": "数据治理 / 质量 / 血缘", "port": 8002, "path": "/api/health", "layer": "app"},
    {"key": "aiplatform", "name": "AI Platform", "desc": "MCP / Agent / 模型管理", "port": 8007, "path": "/health", "layer": "ai"},
    {"key": "datamind", "name": "DataMind", "desc": "NL2SQL / Agent / RAG", "port": 8001, "path": "/api/health", "layer": "ai"},
    {"key": "dataengine", "name": "DataEngine", "desc": "Rust 查询引擎网关", "port": 8082, "path": "/api/health", "layer": "infra"},
    {"key": "vectorservice", "name": "VectorService", "desc": "Doris 向量检索", "port": 8010, "path": "/health", "layer": "infra"},
    {"key": "graphservice", "name": "GraphService", "desc": "Neo4j 知识图谱", "port": 8011, "path": "/api/health", "layer": "infra"},
]

_PROBE_TIMEOUT = 3.0  # seconds


def _service_host(key: str) -> str:
    """Host for a service: MONITOR_HOST_<KEY> override, else MONITOR_HOST."""
    return os.getenv(f"MONITOR_HOST_{key.upper()}", _DEFAULT_HOST)


def _http_get_json(url: str, timeout: float = _PROBE_TIMEOUT) -> dict | None:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 — probe failures are reported as status, not raised
        return None


def _probe_service(svc: dict) -> dict:
    """Probe a single service health endpoint, returning status + latency."""
    host = _service_host(svc["key"])
    url = f"http://{host}:{svc['port']}{svc['path']}"
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            body = resp.read().decode("utf-8", errors="replace")
            detail = {}
            try:
                detail = json.loads(body)
                if not isinstance(detail, dict):
                    detail = {}
            except (ValueError, TypeError):
                pass
            return {
                **svc,
                "host": host,
                "status": "healthy",
                "latency_ms": latency_ms,
                "version": detail.get("version"),
                "message": detail.get("status") or "",
            }
    except Exception as exc:  # noqa: BLE001 — probe failures must not break the aggregate
        logger.debug("Health probe failed for %s: %s", svc["key"], exc)
        return {
            **svc,
            "host": host,
            "status": "down",
            "latency_ms": None,
            "version": None,
            "message": str(exc) or "unreachable",
        }


@router.get("/services")
def get_services_health(admin: dict = Depends(require_admin)):
    """Probe all registered services concurrently (admin only)."""
    with ThreadPoolExecutor(max_workers=len(SERVICE_REGISTRY)) as pool:
        services = list(pool.map(_probe_service, SERVICE_REGISTRY))

    healthy = sum(1 for s in services if s["status"] == "healthy")
    latencies = [s["latency_ms"] for s in services if s["latency_ms"] is not None]

    # Group by architecture layer, preserving registry order
    layers = []
    for layer in LAYERS:
        items = [s for s in services if s["layer"] == layer["key"]]
        layer_healthy = sum(1 for s in items if s["status"] == "healthy")
        layers.append({
            **layer,
            "total": len(items),
            "healthy": layer_healthy,
            "down": len(items) - layer_healthy,
            "services": items,
        })

    return {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total": len(services),
            "healthy": healthy,
            "down": len(services) - healthy,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        },
        "layers": layers,
    }


# ══════════════════════════════════════════════════════════════════════
# Distributed node metrics aggregation
# ══════════════════════════════════════════════════════════════════════

def _fetch_node_metrics(svc: dict) -> dict | None:
    """Fetch local metrics from a service's node; None if unavailable."""
    host = _service_host(svc["key"])
    metrics = _http_get_json(f"http://{host}:{svc['port']}/system-metrics")
    if not isinstance(metrics, dict) or "hostname" not in metrics:
        return None
    metrics["source_service"] = svc["key"]
    metrics["source_host"] = host
    return metrics


@router.get("/system")
def get_system_metrics(admin: dict = Depends(require_admin)):
    """Collect node metrics across all service hosts (admin only).

    Queries each service's local /system-metrics endpoint concurrently and
    deduplicates by hostname, so each physical/virtual node appears once.
    Non-Python services (frontend, dataengine) don't expose the endpoint
    and are skipped; the monitoring service's own node is always included.
    """
    # Always include this node even if its own probe is flaky
    local = collect_local_metrics()
    local["source_service"] = "authservice"
    local["source_host"] = _service_host("authservice")

    # Python services that mount the node-metrics router
    probes = [s for s in SERVICE_REGISTRY if s["key"] not in ("frontend", "dataengine")]
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        fetched = list(pool.map(_fetch_node_metrics, probes))

    # Deduplicate by hostname (same host -> one entry, keep first success)
    nodes: dict[str, dict] = {}
    for m in [local, *(f for f in fetched if f)]:
        nodes.setdefault(m["hostname"], m)

    return {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "node_count": len(nodes),
        "nodes": list(nodes.values()),
    }
