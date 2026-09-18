"""Semantic Layer Client — Python client for semanticservice (:8012).

统一 ChatBI 与大屏(dataviz)对语义层的访问:发送声明式 SemanticQuery,
拿回 SemanticResult(columns/rows/provenance),绝不下发裸 SQL。

Usage:
    from services.shared.common.semantic_client import SemanticClient

    res = SemanticClient().query({
        "object": "adh:obj:Order",
        "metrics": ["order_count"],
        "dimensions": ["region"],
        "filters": [{"dim": "region", "op": "eq", "value": "EMEA"}],
        "datasource_id": 1780478236183,
    })
    columns, rows = res["columns"], res["rows"]
"""

import logging
import os
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

SEMANTIC_SERVICE_URL = os.getenv("SEMANTIC_SERVICE_URL", "http://localhost:8012")
SEMANTIC_TIMEOUT = int(os.getenv("SEMANTIC_TIMEOUT", "60"))


class SemanticError(Exception):
    """语义层调用错误(携带 HTTP 状态码与后端 detail)。"""

    def __init__(self, message: str, status_code: int = 0, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class SemanticClient:
    """Thin HTTP client over the semantic layer contract."""

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None):
        self.base_url = (base_url or SEMANTIC_SERVICE_URL).rstrip("/")
        self.timeout = timeout or SEMANTIC_TIMEOUT

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/semantic/health", timeout=5)
            return r.status_code == 200
        except Exception as e:  # noqa: BLE001
            logger.warning("[semantic] health check failed: %s", e)
            return False

    def resolve(self, intent: dict[str, Any]) -> dict[str, Any]:
        """intent -> {binding, plan} 预览(无执行)。用于对齐校验/高风险提案。"""
        return self._post("/api/semantic/resolve", intent)

    def query(self, intent: dict[str, Any]) -> dict[str, Any]:
        """SemanticQuery -> SemanticResult(dict)。失败抛 SemanticError。"""
        return self._post("/api/semantic/query", intent)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            r = requests.post(url, json=body, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise SemanticError(f"语义层不可达: {e}", status_code=0) from e
        if r.status_code >= 400:
            detail: Any
            try:
                detail = r.json()
            except Exception:  # noqa: BLE001
                detail = r.text
            msg = detail.get("detail", detail) if isinstance(detail, dict) else detail
            raise SemanticError(f"语义层返回 {r.status_code}: {msg}",
                                status_code=r.status_code, detail=detail)
        return r.json()


_default_client: Optional[SemanticClient] = None


def get_semantic_client() -> SemanticClient:
    global _default_client
    if _default_client is None:
        _default_client = SemanticClient()
    return _default_client
