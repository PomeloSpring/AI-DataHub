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
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends

from backend.common.auth import require_admin
from backend.common import config as _cfg
from backend.common.system_metrics import collect_local_metrics

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
    {"key": "semhub", "name": "SemanticLayer", "desc": "语义层 + Oxigraph 知识图谱", "port": 8012, "path": "/api/health", "layer": "infra"},
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


# ══════════════════════════════════════════════════════════════════════
# Middleware / infrastructure dependency health
# ══════════════════════════════════════════════════════════════════════
# 直接探测平台依赖的中间件(不经其 HTTP 服务封装)。每个探测 fail-safe：
# 异常 → status=down + 原因(显式暴露，不静默); 未配置 → status=skipped(不误报为故障)。

def _mk(key, name, desc, category, required):
    return {"key": key, "name": name, "desc": desc, "category": category,
            "required": required, "configured": True, "status": "down",
            "latency_ms": None, "detail": "", "message": ""}


def _check_mysql():
    r = _mk("mysql", "元数据库 (MySQL)", "对话/权限/本体/审批等元数据", "存储", True)
    r["detail"] = f"{_cfg.METADATA_DB_HOST}:{_cfg.METADATA_DB_PORT}/{_cfg.METADATA_DB_DATABASE}"
    started = time.monotonic()
    try:
        from backend.common.db.metadata_db import get_metadata_conn
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        r["status"] = "healthy"
        r["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    except Exception as e:  # noqa: BLE001 — 探测失败作为状态上报
        r["message"] = str(e) or "unreachable"
    return r


def _check_redis():
    r = _mk("redis", "Redis", "分布式缓存 + Celery 队列/租约", "缓存与队列", True)
    url = _cfg.REDIS_URL
    r["detail"] = url.split("://", 1)[-1]
    started = time.monotonic()
    try:
        import redis
        cli = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        r["status"] = "healthy" if cli.ping() else "down"
        r["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        try:
            r["detail"] = "v" + str(cli.info("server").get("redis_version", ""))
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        r["message"] = str(e) or "unreachable"
    return r


def _check_oxigraph():
    url = getattr(_cfg, "OXIGRAPH_URL", "") or ""
    r = _mk("oxigraph", "图存储 (Oxigraph)", "RDF 三元组 / GraphRAG", "图数据", False)
    if not url:
        r["configured"] = False; r["status"] = "skipped"; r["message"] = "未配置"; return r
    r["detail"] = url
    started = time.monotonic()
    try:
        # 任何 HTTP 响应(含 4xx)=服务可达; 仅连接错误/超时视为 down。
        req = urllib.request.Request(url.rstrip("/") + "/version", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                body = resp.read().decode("utf-8", errors="replace")[:120]
        except HTTPError as e:
            body = ""
            _ = e.code
        r["status"] = "healthy"
        r["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        try:
            r["detail"] = "v" + str(json.loads(body).get("version", "")) if body else url
        except Exception:  # noqa: BLE001
            r["detail"] = url
    except Exception as e:  # noqa: BLE001
        r["message"] = str(e) or "unreachable"
    return r


def _check_object_storage():
    ep = getattr(_cfg, "OBJECT_STORAGE_ENDPOINT", "") or ""
    r = _mk("object_storage", "对象存储 (S3/MinIO)", "附件/报告存储", "存储", False)
    if not ep:
        r["configured"] = False; r["status"] = "skipped"; r["message"] = "未配置"; return r
    bucket = getattr(_cfg, "OBJECT_STORAGE_BUCKET", "") or "adh-attachments"
    r["detail"] = f"{ep}/{bucket}"
    started = time.monotonic()
    try:
        import boto3
        from botocore.config import Config as _BotoConfig
        # 与生产客户端 object_storage.py 保持一致(避免只修生产、监控探针仍误报不可用):
        #   1) boto3 必须带 scheme, 缺省补 https://; 2) 阿里云 OSS 拒二级域名(path)风格, 强制 virtual;
        #   3) 关默认 flexible checksum(trailer OSS 不支持)。本探针只读 head_bucket, 复用 ObjectStorage 会触发建桶故单独建。
        if ep and not ep.startswith(("http://", "https://")):
            ep = "https://" + ep
        s3 = boto3.client(
            "s3", endpoint_url=ep,
            aws_access_key_id=getattr(_cfg, "OBJECT_STORAGE_ACCESS_KEY", "") or "",
            aws_secret_access_key=getattr(_cfg, "OBJECT_STORAGE_SECRET_KEY", "") or "",
            region_name=getattr(_cfg, "OBJECT_STORAGE_REGION", "") or "us-east-1",
            config=_BotoConfig(signature_version="s3v4", connect_timeout=3, read_timeout=3,
                               retries={"max_attempts": 1},
                               s3={"addressing_style": "virtual"},
                               request_checksum_calculation="when_required"),
        )
        s3.head_bucket(Bucket=bucket)
        r["status"] = "healthy"
        r["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    except Exception as e:  # noqa: BLE001 — 403/404/网络均视为不可用, 附原因
        r["message"] = (str(e) or "unreachable")[:200]
    return r


def _check_celery_workers():
    r = _mk("celery", "任务队列 (Celery)", "定时/报告 Worker 存活", "缓存与队列", False)
    try:
        from celery import Celery
        # 轻量只读探测：向 broker 广播 inspect().ping()，统计在线 Worker（不改任务状态、不消费）。
        app = Celery("adh_monitor", broker=_cfg.REDIS_URL, backend=_cfg.REDIS_URL)
        pong = app.control.inspect(timeout=1.0).ping() or {}
        n = len(pong)
        r["status"] = "healthy" if n else "down"
        r["detail"] = f"{n} 个 worker 在线"
        r["message"] = "" if n else "broker 可达但无运行中的 Worker"
    except Exception as e:  # noqa: BLE001 — 探测失败作为状态上报，不抛
        r["message"] = str(e) or "unreachable"
    return r


_MIDDLEWARE_CHECKS = [_check_mysql, _check_redis, _check_oxigraph,
                      _check_object_storage, _check_celery_workers]
# 说明：Doris 不是平台必需中间件（元库为 MySQL；Doris 属按数据源接入的业务/向量源，
# 其连通性在“数据源配置/查询引擎”处校验），因此不纳入全局中间件健康监控。


@router.get("/middleware")
def get_middleware_health(admin: dict = Depends(require_admin)):
    """并发探测平台依赖的中间件健康(仅管理员)。

    status: healthy(可达) / down(已配置但不可达) / skipped(未配置)。
    required 项(元库 MySQL / Redis)不可达会拉低整体健康; 可选项单独展示。
    """
    with ThreadPoolExecutor(max_workers=len(_MIDDLEWARE_CHECKS)) as pool:
        items = list(pool.map(lambda fn: fn(), _MIDDLEWARE_CHECKS))
    probed = [i for i in items if i["status"] != "skipped"]
    healthy = sum(1 for i in probed if i["status"] == "healthy")
    latencies = [i["latency_ms"] for i in probed if i["latency_ms"] is not None]
    return {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total": len(items),
            "healthy": healthy,
            "down": sum(1 for i in probed if i["status"] == "down"),
            "skipped": len(items) - len(probed),
            "required_down": sum(1 for i in probed if i["required"] and i["status"] != "healthy"),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        },
        "items": items,
    }
