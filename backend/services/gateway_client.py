"""DataFusion Gateway HTTP Client.

Communicates with the Rust DataFusion Gateway service for SQL execution
with transparent RLS (Row-Level Security) enforcement.

Usage:
    from backend.services.gateway_client import gateway_client

    result = gateway_client.execute(
        sql="SELECT * FROM orders WHERE amount > 100",
        datasource_config={"db_type": "mysql", "host": "...", ...},
        rls_policies=[{"tables": ["orders"], "row_filter": "region = 'cn'", ...}],
    )
"""

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Configuration from environment
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:50051")
GATEWAY_TIMEOUT = int(os.getenv("GATEWAY_TIMEOUT", "30"))
GATEWAY_ENABLED = os.getenv("GATEWAY_ENABLED", "true").lower() == "true"


class GatewayClient:
    """DataFusion Gateway HTTP client."""

    def __init__(self, base_url: str = None, timeout: int = None):
        self.base_url = (base_url or GATEWAY_URL).rstrip("/")
        self.timeout = timeout or GATEWAY_TIMEOUT
        self._session = requests.Session()
        self._healthy: Optional[bool] = None
        self._last_check: float = 0

    def execute(
        self,
        sql: str,
        datasource_config: dict,
        rls_policies: list[dict],
        request_id: str = None,
    ) -> dict:
        """Execute SQL via DataFusion Gateway.

        Args:
            sql: The SQL query to execute.
            datasource_config: Datasource connection config dict with keys:
                db_type, host, port, database, user, password, ssl
            rls_policies: List of RLS policy dicts, each with keys:
                tables, row_filter, hidden_columns, masked_columns
            request_id: Optional request ID for audit correlation.

        Returns:
            dict with keys: columns, rows, row_count, rls_applied,
                           execution_time_ms, error (optional)

        Raises:
            GatewayError: If the gateway request fails.
        """
        payload = {
            "sql": sql,
            "datasource": datasource_config,
            "rls_policies": rls_policies,
        }
        if request_id:
            payload["request_id"] = request_id

        try:
            start = time.time()
            resp = self._session.post(
                f"{self.base_url}/api/query",
                json=payload,
                timeout=self.timeout,
            )
            elapsed_ms = int((time.time() - start) * 1000)

            if resp.status_code != 200:
                error_msg = resp.text
                try:
                    error_msg = resp.json().get("error", error_msg)
                except Exception:
                    pass
                raise GatewayError(f"Gateway returned {resp.status_code}: {error_msg}")

            result = resp.json()
            result["gateway_latency_ms"] = elapsed_ms
            return result

        except requests.exceptions.ConnectionError as e:
            self._healthy = False
            raise GatewayError(f"Gateway connection failed: {e}") from e
        except requests.exceptions.Timeout as e:
            raise GatewayError(f"Gateway timeout ({self.timeout}s): {e}") from e
        except requests.exceptions.RequestException as e:
            raise GatewayError(f"Gateway request failed: {e}") from e

    def health(self, force_check: bool = False) -> bool:
        """Check if the Gateway is healthy.

        Results are cached for 30 seconds to avoid excessive health checks.
        """
        now = time.time()

        # Use cached result if recent
        if not force_check and self._healthy is not None and (now - self._last_check) < 30:
            return self._healthy

        try:
            resp = self._session.get(
                f"{self.base_url}/api/health",
                timeout=5,
            )
            self._healthy = resp.status_code == 200
        except Exception:
            self._healthy = False

        self._last_check = now
        return self._healthy

    def is_enabled(self) -> bool:
        """Check if gateway is enabled in configuration."""
        return GATEWAY_ENABLED


class GatewayError(Exception):
    """Gateway communication error."""
    pass


# Singleton instance
gateway_client = GatewayClient()
