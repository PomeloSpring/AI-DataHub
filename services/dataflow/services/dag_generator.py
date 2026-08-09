"""Dynamic DAG Generator — generates Airflow DAG Python files from task configs.

Supports templates for common sync patterns:
- mysql_to_doris: MySQL source to Apache Doris target
- full_sync: Full table synchronization
- incremental_sync: Incremental sync based on a tracking column
"""

import logging
import os
import re
from datetime import datetime
from typing import Optional

from services.dataflow.services.airflow_client import airflow_client

logger = logging.getLogger(__name__)


class DAGGenerator:
    """Generate Airflow DAG files dynamically from sync task configurations."""

    def __init__(self, dags_folder: Optional[str] = None):
        self.dags_folder = dags_folder or os.getenv("AIRFLOW_DAGS_FOLDER", "/opt/airflow/dags")

    def generate_sync_dag(self, task_config: dict) -> str:
        """Generate a sync DAG and write it to the Airflow DAGs folder.

        Args:
            task_config: {
                "name": "task_name",
                "source_type": "mysql" | "postgres" | "api" | "file",
                "source_config": {...},
                "target_type": "doris" | "mysql" | "es",
                "target_config": {...},
                "sync_mode": "full" | "incremental",
                "schedule": "0 2 * * *",
                "task_config": {...}
            }

        Returns:
            dag_id: The generated DAG identifier.
        """
        dag_id = self._make_dag_id(task_config["name"])
        source_type = task_config["source_type"]
        target_type = task_config["target_type"]
        sync_mode = task_config.get("sync_mode", "full")

        # Select template
        template_key = f"{source_type}_to_{target_type}"
        if template_key not in self._templates():
            template_key = f"generic_{sync_mode}"

        dag_code = self._render_dag(dag_id, task_config, template_key)

        # Write DAG file
        airflow_client.create_dag(dag_id, dag_code, self.dags_folder)

        logger.info("Generated DAG: %s (template=%s)", dag_id, template_key)
        return dag_id

    def _make_dag_id(self, name: str) -> str:
        """Convert a task name to a valid Airflow DAG ID."""
        dag_id = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower().strip())
        dag_id = re.sub(r"_+", "_", dag_id).strip("_")
        return f"dataflow_sync_{dag_id}"

    def _render_dag(self, dag_id: str, config: dict, template_key: str) -> str:
        """Render a DAG Python file from template and config."""
        schedule = config.get("schedule") or "0 2 * * *"
        sync_mode = config.get("sync_mode", "full")
        source_type = config.get("source_type", "mysql")
        target_type = config.get("target_type", "doris")
        source_config = config.get("source_config", {})
        target_config = config.get("target_config", {})
        task_config = config.get("task_config", {})

        # Extract connection details for template
        source_host = source_config.get("host", "localhost")
        source_port = source_config.get("port", 3306)
        source_db = source_config.get("database", "default")
        source_user = source_config.get("username", source_config.get("user", "root"))
        source_table = source_config.get("table", task_config.get("source_table", "source_table"))

        target_host = target_config.get("host", "localhost")
        target_port = target_config.get("port", 9030)
        target_db = target_config.get("database", "default")
        target_user = target_config.get("username", target_config.get("user", "root"))
        target_table = target_config.get("table", task_config.get("target_table", "target_table"))

        incremental_column = task_config.get("incremental_column", "updated_at")
        batch_size = task_config.get("batch_size", 10000)

        template_func = self._templates().get(template_key, self._generic_template)
        return template_func(
            dag_id=dag_id,
            schedule=schedule,
            sync_mode=sync_mode,
            source_type=source_type,
            target_type=target_type,
            source_host=source_host,
            source_port=source_port,
            source_db=source_db,
            source_user=source_user,
            source_table=source_table,
            target_host=target_host,
            target_port=target_port,
            target_db=target_db,
            target_user=target_user,
            target_table=target_table,
            incremental_column=incremental_column,
            batch_size=batch_size,
            source_config=source_config,
            target_config=target_config,
            task_config=task_config,
        )

    def _templates(self) -> dict:
        """Map of template_key -> render function."""
        return {
            "mysql_to_doris": self._mysql_to_doris_template,
            "mysql_to_mysql": self._mysql_to_mysql_template,
            "generic_full": self._generic_template,
            "generic_incremental": self._generic_template,
        }

    def _mysql_to_doris_template(self, **kwargs) -> str:
        """Template for MySQL to Apache Doris sync."""
        return f'''"""Auto-generated DAG: {kwargs["dag_id"]}
Generated at: {datetime.now().isoformat()}
Sync mode: {kwargs["sync_mode"]}
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.mysql.hooks.mysql import MySqlHook

default_args = {{
    "owner": "dataflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}}


def extract_from_mysql(**context):
    """Extract data from MySQL source."""
    hook = MySqlHook(mysql_conn_id="mysql_source")
    sql = "SELECT * FROM {kwargs["source_db"]}.{kwargs["source_table"]}"
    if "{kwargs["sync_mode"]}" == "incremental":
        # Use execution date as watermark
        last_sync = context.get("prev_execution_date", datetime(2020, 1, 1))
        sql += f" WHERE {kwargs["incremental_column"]} > \'{{last_sync}}\'"
    sql += " LIMIT {kwargs["batch_size"]}"
    df = hook.get_pandas_df(sql)
    context["ti"].xcom_push(key="record_count", value=len(df))
    return df.to_json()


def load_to_doris(**context):
    """Load data into Apache Doris via Stream Load."""
    import httpx
    import json

    records = json.loads(context["ti"].xcom_pull(task_ids="extract"))
    if not records:
        print("No records to load")
        return

    # Doris Stream Load
    url = f"http://{kwargs["target_host"]}:{kwargs["target_port"]}/api/{kwargs["target_db"]}/{kwargs["target_table"]}/_stream_load"
    headers = {{
        "format": "json",
        "strip_outer_array": "true",
        "Expect": "100-continue",
    }}
    data = json.dumps(list(records.values()) if isinstance(records, dict) else records)
    resp = httpx.put(url, content=data, headers=headers, auth=("root", ""), timeout=300)
    resp.raise_for_status()
    result = resp.json()
    if result.get("Status") != "Success":
        raise RuntimeError(f"Doris Stream Load failed: {{result}}")
    print(f"Loaded {{len(records)}} records to Doris")


def notify_completion(**context):
    """Send completion notification."""
    record_count = context["ti"].xcom_pull(task_ids="extract", key="record_count") or 0
    print(f"Sync completed: {{record_count}} records synced")


with DAG(
    dag_id="{kwargs["dag_id"]}",
    default_args=default_args,
    description="Auto-generated sync DAG: {kwargs["source_type"]} -> {kwargs["target_type"]}",
    schedule_interval="{kwargs["schedule"]}",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["dataflow", "sync", "{kwargs["source_type"]}", "{kwargs["target_type"]}"],
) as dag:

    t1 = PythonOperator(task_id="extract", python_callable=extract_from_mysql)
    t2 = PythonOperator(task_id="load", python_callable=load_to_doris)
    t3 = PythonOperator(task_id="notify", python_callable=notify_completion)

    t1 >> t2 >> t3
'''

    def _mysql_to_mysql_template(self, **kwargs) -> str:
        """Template for MySQL to MySQL sync."""
        return f'''"""Auto-generated DAG: {kwargs["dag_id"]}
Generated at: {datetime.now().isoformat()}
Sync mode: {kwargs["sync_mode"]}
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.mysql.hooks.mysql import MySqlHook

default_args = {{
    "owner": "dataflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}}


def sync_tables(**context):
    """Sync data from source MySQL to target MySQL."""
    source_hook = MySqlHook(mysql_conn_id="mysql_source")
    target_hook = MySqlHook(mysql_conn_id="mysql_target")

    sql = "SELECT * FROM {kwargs["source_db"]}.{kwargs["source_table"]}"
    if "{kwargs["sync_mode"]}" == "incremental":
        last_sync = context.get("prev_execution_date", datetime(2020, 1, 1))
        sql += f" WHERE {kwargs["incremental_column"]} > \'{{last_sync}}\'"
    sql += " LIMIT {kwargs["batch_size"]}"

    df = source_hook.get_pandas_df(sql)
    if df.empty:
        print("No records to sync")
        return

    target_hook.insert_rows(
        table="{kwargs["target_db"]}.{kwargs["target_table"]}",
        rows=df.values.tolist(),
        target_fields=list(df.columns),
    )
    print(f"Synced {{len(df)}} records")
    context["ti"].xcom_push(key="record_count", value=len(df))


with DAG(
    dag_id="{kwargs["dag_id"]}",
    default_args=default_args,
    description="Auto-generated sync DAG: MySQL -> MySQL",
    schedule_interval="{kwargs["schedule"]}",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["dataflow", "sync", "mysql", "mysql"],
) as dag:

    t1 = PythonOperator(task_id="sync", python_callable=sync_tables)
'''

    def _generic_template(self, **kwargs) -> str:
        """Generic sync template for unsupported source/target combinations."""
        return f'''"""Auto-generated DAG: {kwargs["dag_id"]}
Generated at: {datetime.now().isoformat()}
Source: {kwargs["source_type"]} -> Target: {kwargs["target_type"]}
Sync mode: {kwargs["sync_mode"]}

NOTE: This is a generic template. Customize the extract/load functions
for your specific source/target combination.
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator

default_args = {{
    "owner": "dataflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}}


def extract(**context):
    """Extract data from source.

    TODO: Implement extraction logic for {kwargs["source_type"]}.
    Source config: host={kwargs["source_host"]}, db={kwargs["source_db"]}, table={kwargs["source_table"]}
    """
    print(f"Extracting from {kwargs["source_type"]}: {kwargs["source_db"]}.{kwargs["source_table"]}")
    # Placeholder - implement actual extraction
    records = []
    context["ti"].xcom_push(key="record_count", value=len(records))
    return records


def transform(**context):
    """Transform/validate extracted records."""
    records = context["ti"].xcom_pull(task_ids="extract")
    # Placeholder - implement transformation
    return records


def load(**context):
    """Load data into target.

    TODO: Implement loading logic for {kwargs["target_type"]}.
    Target config: host={kwargs["target_host"]}, db={kwargs["target_db"]}, table={kwargs["target_table"]}
    """
    records = context["ti"].xcom_pull(task_ids="transform")
    print(f"Loading to {kwargs["target_type"]}: {kwargs["target_db"]}.{kwargs["target_table"]}")
    # Placeholder - implement actual loading


def notify(**context):
    """Send completion notification."""
    count = context["ti"].xcom_pull(task_ids="extract", key="record_count") or 0
    print(f"Sync completed: {{count}} records")


with DAG(
    dag_id="{kwargs["dag_id"]}",
    default_args=default_args,
    description="Auto-generated sync DAG: {kwargs["source_type"]} -> {kwargs["target_type"]}",
    schedule_interval="{kwargs["schedule"]}",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["dataflow", "sync", "{kwargs["source_type"]}", "{kwargs["target_type"]}"],
) as dag:

    t1 = PythonOperator(task_id="extract", python_callable=extract)
    t2 = PythonOperator(task_id="transform", python_callable=transform)
    t3 = PythonOperator(task_id="load", python_callable=load)
    t4 = PythonOperator(task_id="notify", python_callable=notify)

    t1 >> t2 >> t3 >> t4
'''


# Singleton
dag_generator = DAGGenerator()
