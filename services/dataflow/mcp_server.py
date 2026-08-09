"""MCP Server Tools for DataFlow Service.

Exposes DataFlow functionality as MCP-compatible tools:
- create_sync_task: Create a data sync task
- run_task: Trigger task execution
- get_task_status: Query task status and logs
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# MCP tool definitions (JSON Schema format)
MCP_TOOLS = [
    {
        "name": "create_sync_task",
        "description": "Create a data sync task between source and target databases. Generates an Airflow DAG automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name for the sync task",
                },
                "source": {
                    "type": "object",
                    "description": "Source database configuration",
                    "properties": {
                        "type": {"type": "string", "enum": ["mysql", "postgres", "api", "file"]},
                        "host": {"type": "string"},
                        "port": {"type": "integer"},
                        "database": {"type": "string"},
                        "table": {"type": "string"},
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                    },
                    "required": ["type", "host", "database", "table"],
                },
                "target": {
                    "type": "object",
                    "description": "Target database configuration",
                    "properties": {
                        "type": {"type": "string", "enum": ["doris", "mysql", "es"]},
                        "host": {"type": "string"},
                        "port": {"type": "integer"},
                        "database": {"type": "string"},
                        "table": {"type": "string"},
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                    },
                    "required": ["type", "host", "database", "table"],
                },
                "sync_mode": {
                    "type": "string",
                    "enum": ["full", "incremental"],
                    "description": "Sync mode: full or incremental",
                    "default": "full",
                },
                "schedule": {
                    "type": "string",
                    "description": "Cron expression for scheduling (e.g., '0 2 * * *')",
                },
            },
            "required": ["name", "source", "target"],
        },
    },
    {
        "name": "run_task",
        "description": "Trigger execution of a sync or scheduled task by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "The task ID to execute",
                },
                "task_type": {
                    "type": "string",
                    "enum": ["sync", "scheduled"],
                    "description": "Type of task",
                    "default": "sync",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "get_task_status",
        "description": "Query the execution status and recent logs of a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "The task ID to query",
                },
                "task_type": {
                    "type": "string",
                    "enum": ["sync", "scheduled"],
                    "description": "Type of task",
                    "default": "sync",
                },
                "include_logs": {
                    "type": "boolean",
                    "description": "Whether to include recent execution logs",
                    "default": True,
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List all sync tasks or scheduled tasks with optional filtering.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": ["sync", "scheduled"],
                    "description": "Type of tasks to list",
                    "default": "sync",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status (e.g., idle, running, success, failed)",
                },
                "page": {
                    "type": "integer",
                    "default": 1,
                },
                "size": {
                    "type": "integer",
                    "default": 20,
                },
            },
        },
    },
]


def handle_tool_call(tool_name: str, arguments: dict) -> dict:
    """Handle an MCP tool call.

    Args:
        tool_name: Name of the tool to invoke.
        arguments: Tool arguments dict.

    Returns:
        Tool result dict with 'content' key.
    """
    from services.dataflow.services.sync_service import sync_service
    from services.dataflow.services.dag_generator import dag_generator
    from services.dataflow.services.airflow_client import airflow_client

    try:
        if tool_name == "create_sync_task":
            source = arguments["source"]
            target = arguments["target"]
            name = arguments["name"]

            dag_id = dag_generator.generate_sync_dag({
                "name": name,
                "source_type": source["type"],
                "source_config": source,
                "target_type": target["type"],
                "target_config": target,
                "sync_mode": arguments.get("sync_mode", "full"),
                "schedule": arguments.get("schedule"),
                "task_config": {},
            })

            task_id = sync_service.create_task(
                data={
                    "name": name,
                    "description": f"Sync {source['type']}.{source.get('table', '?')} -> {target['type']}.{target.get('table', '?')}",
                    "source_type": source["type"],
                    "source_config": source,
                    "target_type": target["type"],
                    "target_config": target,
                    "sync_mode": arguments.get("sync_mode", "full"),
                    "schedule": arguments.get("schedule"),
                    "task_config": {},
                },
                dag_id=dag_id,
            )

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "task_id": task_id,
                        "dag_id": dag_id,
                        "message": f"Sync task '{name}' created successfully",
                    }, ensure_ascii=False),
                }],
            }

        elif tool_name == "run_task":
            task_id = arguments["task_id"]
            task_type = arguments.get("task_type", "sync")

            if task_type == "sync":
                task = sync_service.get_task(task_id)
                if not task:
                    return {"content": [{"type": "text", "text": f"Sync task {task_id} not found"}]}
                dag_id = task.get("dag_id")
                if not dag_id:
                    return {"content": [{"type": "text", "text": f"No DAG configured for task {task_id}"}]}
                result = airflow_client.trigger_dag(dag_id, conf={"task_id": task_id, "triggered_by": "mcp"})
                log_id = sync_service.create_log(task_id, dag_run_id=result.get("dag_run_id", ""), status="running")
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": True,
                            "task_id": task_id,
                            "dag_run_id": result.get("dag_run_id"),
                            "log_id": log_id,
                        }, ensure_ascii=False),
                    }],
                }
            else:
                # Scheduled task
                task = sync_service.get_scheduled_task(task_id)
                if not task:
                    return {"content": [{"type": "text", "text": f"Scheduled task {task_id} not found"}]}
                log_id = sync_service.create_scheduled_log(task_id, "mcp")
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": True,
                            "task_id": task_id,
                            "log_id": log_id,
                            "message": "Scheduled task triggered",
                        }, ensure_ascii=False),
                    }],
                }

        elif tool_name == "get_task_status":
            task_id = arguments["task_id"]
            task_type = arguments.get("task_type", "sync")
            include_logs = arguments.get("include_logs", True)

            if task_type == "sync":
                task = sync_service.get_task(task_id)
                if not task:
                    return {"content": [{"type": "text", "text": f"Sync task {task_id} not found"}]}

                result = {"task": task}
                if include_logs:
                    logs = sync_service.list_logs(task_id, page=1, size=5)
                    result["recent_logs"] = logs.get("items", [])

                return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}
            else:
                task = sync_service.get_scheduled_task(task_id)
                if not task:
                    return {"content": [{"type": "text", "text": f"Scheduled task {task_id} not found"}]}

                result = {"task": task}
                if include_logs:
                    logs = sync_service.list_scheduled_logs(task_id, page=1, size=5)
                    result["recent_logs"] = logs.get("items", [])

                return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

        elif tool_name == "list_tasks":
            task_type = arguments.get("task_type", "sync")
            page = arguments.get("page", 1)
            size = arguments.get("size", 20)

            if task_type == "sync":
                result = sync_service.list_tasks(page=page, size=size, status=arguments.get("status"))
            else:
                result = sync_service.list_scheduled_tasks(page=page, size=size)

            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

        else:
            return {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}]}

    except Exception as e:
        logger.error("MCP tool call failed: %s - %s", tool_name, e)
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


def get_mcp_server_info() -> dict:
    """Return MCP server metadata for registration."""
    return {
        "name": "dataflow",
        "description": "Data sync, workflow orchestration, and scheduled task management",
        "version": "1.0.0",
        "tools": MCP_TOOLS,
    }
