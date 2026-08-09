"""Example Airflow DAG Template — MySQL to Doris Sync.

This is a reference DAG template showing the structure of auto-generated
sync DAGs. The DAG generator in dag_generator.py produces similar files
dynamically based on task configuration.

To use this template directly:
1. Copy this file to your Airflow DAGs folder
2. Update connection IDs and table names
3. Airflow will auto-detect and schedule the DAG
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

default_args = {
    "owner": "dataflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def extract_from_source(**context):
    """Extract data from MySQL source.

    Uses Airflow connection 'mysql_source'.
    """
    from airflow.providers.mysql.hooks.mysql import MySqlHook

    hook = MySqlHook(mysql_conn_id="mysql_source")

    # Incremental: use execution_date as watermark
    sync_mode = context["params"].get("sync_mode", "full")
    sql = "SELECT * FROM mydb.source_table"

    if sync_mode == "incremental":
        last_sync = context.get("prev_execution_date", datetime(2020, 1, 1))
        sql += f" WHERE updated_at > '{last_sync}'"

    sql += " LIMIT 10000"

    df = hook.get_pandas_df(sql)
    record_count = len(df)
    context["ti"].xcom_push(key="record_count", value=record_count)
    print(f"Extracted {record_count} records from source")
    return df.to_json()


def transform_data(**context):
    """Validate and transform extracted records.

    Customize this for your data quality rules.
    """
    import json

    records_json = context["ti"].xcom_pull(task_ids="extract")
    if not records_json:
        print("No records to transform")
        return "[]"

    records = json.loads(records_json)
    # Add your transformation logic here
    # - Type casting
    # - Null handling
    # - Data validation
    print(f"Transformed {len(records)} records")
    return records_json


def load_to_doris(**context):
    """Load data into Apache Doris via Stream Load API.

    Uses httpx for HTTP requests to Doris FE.
    """
    import json

    import httpx

    records_json = context["ti"].xcom_pull(task_ids="transform")
    if not records_json:
        print("No records to load")
        return

    records = json.loads(records_json)
    if not records:
        print("Empty dataset, skipping load")
        return

    # Doris Stream Load configuration
    doris_host = "localhost"
    doris_port = 8030
    doris_db = "target_db"
    doris_table = "target_table"

    url = f"http://{doris_host}:{doris_port}/api/{doris_db}/{doris_table}/_stream_load"
    headers = {
        "format": "json",
        "strip_outer_array": "true",
        "Expect": "100-continue",
    }

    # Convert records to JSONL for Stream Load
    if isinstance(records, dict):
        data = json.dumps(list(records.values()))
    elif isinstance(records, list):
        data = json.dumps(records)
    else:
        data = str(records)

    resp = httpx.put(
        url,
        content=data,
        headers=headers,
        auth=("root", ""),
        timeout=300,
    )
    resp.raise_for_status()
    result = resp.json()

    if result.get("Status") != "Success":
        raise RuntimeError(f"Doris Stream Load failed: {result}")

    loaded = result.get("NumberLoadedRows", 0)
    print(f"Loaded {loaded} rows to Doris")


def send_notification(**context):
    """Send completion notification via configured channels.

    Customize notification content and channels as needed.
    """
    record_count = context["ti"].xcom_pull(task_ids="extract", key="record_count") or 0
    execution_date = context["execution_date"]

    message = (
        f"## Sync Task Completed\n\n"
        f"- **Date**: {execution_date}\n"
        f"- **Records synced**: {record_count}\n"
        f"- **Status**: Success\n"
    )

    print(f"Notification: {message}")
    # Integrate with notification_service if needed:
    # from services.dataflow.services.notification_service import notification_service
    # notification_service.send(channel_id=1, content=message)


# ════════════════════════════════════════════════════════════════════
# DAG Definition
# ════════════════════════════════════════════════════════════════════

with DAG(
    dag_id="dataflow_sync_example",
    default_args=default_args,
    description="Example: MySQL to Doris sync with transformation",
    schedule_interval="0 2 * * *",  # Daily at 2:00 AM
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["dataflow", "example", "sync", "mysql", "doris"],
    params={
        "sync_mode": "full",  # Change to "incremental" for incremental sync
    },
) as dag:

    t1 = PythonOperator(
        task_id="extract",
        python_callable=extract_from_source,
        doc="Extract data from MySQL source",
    )

    t2 = PythonOperator(
        task_id="transform",
        python_callable=transform_data,
        doc="Validate and transform records",
    )

    t3 = PythonOperator(
        task_id="load",
        python_callable=load_to_doris,
        doc="Load data into Doris via Stream Load",
    )

    t4 = PythonOperator(
        task_id="notify",
        python_callable=send_notification,
        doc="Send completion notification",
    )

    # Task dependencies: extract -> transform -> load -> notify
    t1 >> t2 >> t3 >> t4
