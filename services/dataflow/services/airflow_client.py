"""Airflow REST API Client — communicates with Apache Airflow via its REST API.

Uses httpx for HTTP calls. Airflow API base URL is configurable via AIRFLOW_API_URL.
Default credentials via AIRFLOW_USERNAME / AIRFLOW_PASSWORD env vars.

Airflow REST API reference:
- https://airflow.apache.org/docs/apache-airflow/stable/stable-rest-api-ref.html
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class AirflowClient:
    """Client for Apache Airflow REST API (v2)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 60.0,
    ):
        self.base_url = (base_url or os.getenv("AIRFLOW_API_URL", "http://localhost:8080")).rstrip("/")
        self.username = username or os.getenv("AIRFLOW_USERNAME", "airflow")
        self.password = password or os.getenv("AIRFLOW_PASSWORD", "airflow")
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=f"{self.base_url}/api/v1",
                auth=(self.username, self.password),
                timeout=self.timeout,
            )
        return self._client

    def close(self):
        if self._client and not self._client.is_closed:
            self._client.close()

    # ── DAG Operations ────────────────────────────────────────────

    def list_dags(self, limit: int = 100, offset: int = 0) -> dict:
        """List all DAGs."""
        resp = self.client.get("/dags", params={"limit": limit, "offset": offset})
        resp.raise_for_status()
        return resp.json()

    def get_dag_info(self, dag_id: str) -> dict:
        """Get metadata for a specific DAG."""
        resp = self.client.get(f"/dags/{dag_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def trigger_dag(self, dag_id: str, conf: dict = None, run_id: str = None) -> dict:
        """Trigger a DAG run.

        Args:
            dag_id: The DAG identifier.
            conf: Configuration dict passed to the DAG run.
            run_id: Optional custom run ID (dag_run_id).

        Returns:
            DAG run info including execution_date and state.
        """
        payload = {"conf": conf or {}}
        if run_id:
            payload["dag_run_id"] = run_id

        resp = self.client.post(f"/dags/{dag_id}/dagRuns", json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_dag_run_status(self, dag_id: str, run_id: str) -> dict:
        """Get the status of a specific DAG run.

        Returns dict with state, execution_date, start_date, end_date.
        """
        resp = self.client.get(f"/dags/{dag_id}/dagRuns/{run_id}")
        if resp.status_code == 404:
            return {"dag_id": dag_id, "run_id": run_id, "state": "not_found"}
        resp.raise_for_status()
        data = resp.json()
        return {
            "dag_id": dag_id,
            "run_id": data.get("dag_run_id", run_id),
            "state": data.get("state", "unknown"),
            "execution_date": data.get("execution_date"),
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
        }

    def list_dag_runs(self, dag_id: str, limit: int = 10) -> list:
        """List recent runs for a DAG."""
        resp = self.client.get(
            f"/dags/{dag_id}/dagRuns",
            params={"limit": limit, "order_by": "-execution_date"},
        )
        resp.raise_for_status()
        return resp.json().get("dag_runs", [])

    def get_dag_run_logs(self, dag_id: str, run_id: str, task_id: str = None, try_number: int = 1) -> str:
        """Get task instance logs for a DAG run.

        If task_id is not provided, returns logs for the first task.
        """
        if not task_id:
            # Get task instances for this run
            ti_resp = self.client.get(
                f"/dags/{dag_id}/dagRuns/{run_id}/taskInstances",
            )
            ti_resp.raise_for_status()
            tasks = ti_resp.json().get("task_instances", [])
            if not tasks:
                return "No task instances found"
            task_id = tasks[0]["task_id"]

        resp = self.client.get(
            f"/dags/{dag_id}/dagRuns/{run_id}/taskInstances/{task_id}/logs/{try_number}",
        )
        if resp.status_code == 404:
            return f"Logs not found for task {task_id}"
        resp.raise_for_status()
        return resp.text

    def get_dag_run_task_instances(self, dag_id: str, run_id: str) -> list:
        """Get all task instances for a DAG run."""
        resp = self.client.get(f"/dags/{dag_id}/dagRuns/{run_id}/taskInstances")
        resp.raise_for_status()
        return resp.json().get("task_instances", [])

    # ── DAG File Management ───────────────────────────────────────

    def create_dag(self, dag_id: str, dag_code: str, dags_folder: str = None) -> dict:
        """Create or update a DAG by writing its Python file.

        This writes the DAG file to the Airflow DAGs folder.
        Requires the DAGs folder to be accessible from this service.

        Args:
            dag_id: The DAG identifier.
            dag_code: Python source code for the DAG.
            dags_folder: Override for the DAGs folder path.

        Returns:
            Status dict with dag_id and file_path.
        """
        folder = dags_folder or os.getenv("AIRFLOW_DAGS_FOLDER", "/opt/airflow/dags")
        file_path = f"{folder}/{dag_id}.py"

        try:
            os.makedirs(folder, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(dag_code)
            logger.info("DAG file written: %s", file_path)
            return {"dag_id": dag_id, "file_path": file_path, "status": "created"}
        except OSError as e:
            logger.error("Failed to write DAG file %s: %s", file_path, e)
            raise RuntimeError(f"Failed to write DAG file: {e}")

    # ── Connection / Health ───────────────────────────────────────

    def health_check(self) -> dict:
        """Check if Airflow API is reachable."""
        try:
            resp = self.client.get("/health")
            resp.raise_for_status()
            return {"status": "healthy", "airflow": resp.json()}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


# Singleton
airflow_client = AirflowClient()
