"""DataEngine Client — Python client for DataEngine (Rust DataFusion Gateway).

Communicates with the Rust DataFusion Gateway for:
- SQL execution against MySQL/Doris/PostgreSQL
- Datasource management
- Query result caching

Usage:
    from services.shared.common.engine_client import engine_client

    result = engine_client.query(
        sql="SELECT * FROM orders WHERE amount > 100",
        datasource_id="datasource-uuid",
    )
"""

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Configuration from environment
ENGINE_SERVER_URL = os.getenv("ENGINE_SERVER_URL", "http://localhost:8082")
ENGINE_TIMEOUT = int(os.getenv("ENGINE_TIMEOUT", "60"))
ENGINE_ENABLED = os.getenv("ENGINE_ENABLED", "true").lower() == "true"


class EngineError(Exception):
    """Engine server error."""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class QueryResult:
    """Query result from engine server."""

    def __init__(self, columns: list[str], data: list[list], dtypes: dict[str, str],
                 row_count: int = 0, execution_time_ms: int = 0, rls_applied: list = None):
        self.columns = columns
        self.data = data
        self.dtypes = dtypes
        self.row_count = row_count
        self.execution_time_ms = execution_time_ms
        self.rls_applied = rls_applied or []

    def to_dict(self) -> dict:
        return {
            "columns": self.columns,
            "data": self.data,
            "dtypes": self.dtypes,
            "row_count": self.row_count,
            "execution_time_ms": self.execution_time_ms,
        }

    def to_dataframe(self):
        """Convert to pandas DataFrame."""
        import pandas as pd
        if not self.data:
            return pd.DataFrame(columns=self.columns)
        return pd.DataFrame(self.data, columns=self.columns)


class EngineClient:
    """Client for DataEngine (Rust DataFusion Gateway)."""

    def __init__(self, base_url: str = None, timeout: int = None):
        self.base_url = (base_url or ENGINE_SERVER_URL).rstrip("/")
        self.timeout = timeout or ENGINE_TIMEOUT
        self._session = requests.Session()
        self._healthy: Optional[bool] = None
        self._last_check: float = 0
        self._datasource_cache: dict[str, str] = {}  # name -> id mapping

    def health(self) -> bool:
        """Check if engine server is healthy."""
        now = time.time()
        if self._healthy is not None and now - self._last_check < 30:
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

    def query(
        self,
        sql: str,
        datasource_id: str,
        limit: int = None,
        rls_policies: list[dict] = None,
    ) -> QueryResult:
        """Execute SQL through DataEngine.

        Args:
            sql: The SQL query to execute.
            datasource_id: Datasource UUID (from create_datasource).
            limit: Optional row limit.
            rls_policies: Optional list of RLS policies to apply.
                Each policy: {"tables": [...], "row_filter": "...",
                              "hidden_columns": [...], "masked_columns": {...}}

        Returns:
            QueryResult with columns, data, dtypes.

        Raises:
            EngineError on failure.
        """
        body = {
            "sql": sql,
            "datasource_id": datasource_id,
        }
        if rls_policies:
            body["rls_policies"] = rls_policies

        try:
            resp = self._session.post(
                f"{self.base_url}/api/query",
                json=body,
                timeout=self.timeout,
            )

            if resp.status_code != 200:
                raise EngineError(
                    f"Engine query failed: {resp.text}",
                    status_code=resp.status_code,
                )

            result = resp.json()

            if result.get("error"):
                raise EngineError(f"Engine query error: {result['error']}")

            # Parse columns from response format
            columns_raw = result.get("columns", [])
            columns = [col.get("name", "") if isinstance(col, dict) else str(col) for col in columns_raw]
            dtypes = {col.get("name", ""): col.get("data_type", "") for col in columns_raw if isinstance(col, dict)}

            return QueryResult(
                columns=columns,
                data=result.get("rows", []),
                dtypes=dtypes,
                row_count=result.get("row_count", 0),
                execution_time_ms=result.get("execution_time_ms", 0),
                rls_applied=result.get("rls_applied", []),
            )

        except requests.exceptions.Timeout:
            raise EngineError(f"Engine query timed out after {self.timeout}s")
        except requests.exceptions.ConnectionError:
            raise EngineError(f"Cannot connect to engine server at {self.base_url}")
        except EngineError:
            raise
        except Exception as e:
            raise EngineError(f"Engine query error: {e}")

    def create_datasource(
        self,
        name: str,
        db_type: str,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str,
    ) -> str:
        """Create a datasource in DataEngine.

        Returns:
            Datasource UUID.
        """
        body = {
            "name": name,
            "db_type": db_type,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "database": database,
        }

        try:
            resp = self._session.post(
                f"{self.base_url}/api/datasources",
                json=body,
                timeout=self.timeout,
            )

            if resp.status_code != 200:
                raise EngineError(
                    f"Create datasource failed: {resp.text}",
                    status_code=resp.status_code,
                )

            result = resp.json()
            ds_id = result.get("id", "")
            self._datasource_cache[name] = ds_id
            return ds_id

        except EngineError:
            raise
        except Exception as e:
            raise EngineError(f"Create datasource error: {e}")

    def list_datasources(self) -> list[dict]:
        """List all datasources."""
        try:
            resp = self._session.get(
                f"{self.base_url}/api/datasources",
                timeout=self.timeout,
            )

            if resp.status_code != 200:
                raise EngineError(
                    f"List datasources failed: {resp.text}",
                    status_code=resp.status_code,
                )

            result = resp.json()
            return result.get("datasources", [])

        except EngineError:
            raise
        except Exception as e:
            raise EngineError(f"List datasources error: {e}")

    def test_datasource(self, datasource_id: str) -> bool:
        """Test datasource connection."""
        try:
            resp = self._session.post(
                f"{self.base_url}/api/datasources/{datasource_id}/test",
                timeout=self.timeout,
            )

            if resp.status_code == 200:
                result = resp.json()
                return result.get("success", False)

            return False

        except Exception:
            return False

    def get_or_create_datasource(
        self,
        name: str,
        db_type: str,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str,
    ) -> str:
        """Get existing datasource by name or create new one.

        Returns:
            Datasource UUID.
        """
        # Check cache first
        if name in self._datasource_cache:
            return self._datasource_cache[name]

        # List existing datasources
        try:
            datasources = self.list_datasources()
            for ds in datasources:
                if ds.get("name") == name:
                    ds_id = ds.get("id", "")
                    self._datasource_cache[name] = ds_id
                    return ds_id
        except Exception:
            pass

        # Create new datasource
        return self.create_datasource(
            name=name,
            db_type=db_type,
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
        )


# Singleton instance
engine_client = EngineClient()
