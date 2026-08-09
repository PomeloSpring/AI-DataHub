"""OpenMetadata REST 客户端 — datacatalog 适配层。

服务端持有 OM_AUTH_TOKEN（services/.env），前端不暴露密钥。
OM_ENABLED=false 或服务不可达时抛 OMClientError，由 API 层优雅降级。
"""

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class OMClientError(Exception):
    """OM 未启用 / 不可达 / 请求失败。status_code 供 API 层映射 HTTP 状态。"""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def om_enabled() -> bool:
    return os.getenv("OM_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _base_url() -> str:
    return os.getenv("OM_SERVER_URL", "http://localhost:8585").rstrip("/")


def _token() -> str:
    token = os.getenv("OM_AUTH_TOKEN", "").strip()
    if not token:
        raise OMClientError("OM_AUTH_TOKEN 未配置，请先执行 docker/om/init_om.py", 503)
    return token


def _request(method: str, path: str, body: dict = None, timeout: int = 20):
    if not om_enabled():
        raise OMClientError("OpenMetadata 集成未启用 (OM_ENABLED=false)", 503)
    url = f"{_base_url()}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {_token()}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        status = 404 if e.code == 404 else 502
        raise OMClientError(f"OM API {path} 返回 HTTP {e.code}: {detail}", status)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise OMClientError(f"无法连接 OpenMetadata ({_base_url()}): {e}", 502)


# ── 对外能力 ────────────────────────────────────────────────────────

def get_status() -> dict:
    """OM 可达性与版本。"""
    if not om_enabled():
        return {"enabled": False, "reachable": False}
    try:
        version = _request("GET", "/api/v1/system/version")
        return {"enabled": True, "reachable": True, "version": version.get("version")}
    except OMClientError:
        return {"enabled": True, "reachable": False}


def search(query: str, index: str = None, size: int = 10) -> dict:
    """全文搜索（表/主题/指标等）。"""
    path = f"/api/v1/search/query?q={urllib.request.quote(query)}&size={size}"
    if index:
        path += f"&index={urllib.request.quote(index)}"
    result = _request("GET", path)
    hits = result.get("hits", {})
    return {
        "total": hits.get("total", {}).get("value", 0) if isinstance(hits.get("total"), dict) else hits.get("total", 0),
        "items": hits.get("hits", {}).get("hits", []) if isinstance(hits.get("hits"), dict) else [],
    }


def get_table(fqn: str) -> dict:
    """按 FQN 获取表详情（含列、描述、标签、Owner）。"""
    return _request(
        "GET",
        f"/api/v1/tables/name/{urllib.request.quote(fqn, safe='')}?fields=description,columns,tags,owner",
    )


def get_lineage(fqn: str, upstream_depth: int = 3, downstream_depth: int = 3) -> dict:
    """表级血缘图：先按 FQN 查实体 id，再查 lineage API。"""
    table = get_table(fqn)
    return _request(
        "GET",
        f"/api/v1/lineage?type=table&id={table['id']}"
        f"&upstreamDepth={upstream_depth}&downstreamDepth={downstream_depth}",
    )


def list_services() -> dict:
    """列出已注册的数据库服务。"""
    return _request("GET", "/api/v1/services/databaseServices?limit=50")
