"""DataMind MCP Server — Exposes AI capabilities as MCP tools.

Run: python services.datamind.mcp_server

This MCP server provides three tools:
- query_data: Natural language data query (NL2SQL)
- execute_sql: Direct SQL execution against a datasource
- analyze_data: Multi-dimensional data analysis

It wraps the existing backend modules and exposes them via the MCP protocol.
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Add project root to sys.path
_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("datamind-mcp")

# ── MCP Server Implementation ────────────────────────────────────────
# Uses a lightweight JSON-RPC over stdio approach compatible with MCP protocol.

TOOLS = [
    {
        "name": "query_data",
        "description": (
            "Query data using natural language. Converts the question to SQL "
            "via NL2SQL pipeline, executes it, and returns results with analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural language question about the data",
                },
                "datasource_id": {
                    "type": "integer",
                    "description": "Datasource ID (0 = default)",
                    "default": 0,
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "execute_sql",
        "description": (
            "Execute a SQL query directly against the specified datasource. "
            "Returns columns, rows, row count, and elapsed time."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL query to execute",
                },
                "datasource_id": {
                    "type": "integer",
                    "description": "Datasource ID (0 = default)",
                    "default": 0,
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "analyze_data",
        "description": (
            "Analyze data using multi-dimensional analysis. "
            "Supports trend, distribution, anomaly detection, and general analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Analysis question or data description",
                },
                "analysis_type": {
                    "type": "string",
                    "description": "Type of analysis: trend, distribution, anomaly, general",
                    "enum": ["trend", "distribution", "anomaly", "general"],
                    "default": "general",
                },
                "datasource_id": {
                    "type": "integer",
                    "description": "Datasource ID (0 = default)",
                    "default": 0,
                },
            },
            "required": ["question"],
        },
    },
]


async def handle_query_data(arguments: dict) -> str:
    """Execute NL2SQL pipeline for a natural language question."""
    from services.datamind.nl2sql.orchestrator.pipeline_orchestrator import execute_pipeline

    question = arguments.get("question", "")
    datasource_id = arguments.get("datasource_id", 0)

    if not question:
        return json.dumps({"error": "question is required"})

    result = {}
    try:
        async for event_type, data in execute_pipeline(
            question=question,
            history=[],
            datasource_id=datasource_id,
            pipeline_mode="quick",
            user_id=0,
            username="mcp",
        ):
            if event_type == "done":
                result = data
            elif event_type == "error":
                result["error"] = data.get("message", str(data))
    except Exception as e:
        logger.error("query_data failed: %s", e)
        result = {"error": str(e)}

    # Format for MCP response
    response = {
        "sql": result.get("sql"),
        "reply": result.get("reply", ""),
        "row_count": 0,
        "columns": [],
        "rows_preview": [],
    }
    query_result = result.get("result", {})
    if query_result:
        response["row_count"] = query_result.get("row_count", 0)
        response["columns"] = query_result.get("columns", [])
        response["rows_preview"] = query_result.get("rows", [])[:20]

    return json.dumps(response, ensure_ascii=False, default=str)


async def handle_execute_sql(arguments: dict) -> str:
    """Execute SQL directly against the datasource."""
    from services.datamind.nl2sql.sql.query_executor import execute_query

    sql = arguments.get("sql", "")
    datasource_id = arguments.get("datasource_id", 0)

    if not sql:
        return json.dumps({"error": "sql is required"})

    try:
        df, elapsed_ms, row_count = execute_query(sql, datasource_id)
        columns = list(df.columns) if not df.empty else []
        rows = df.to_dict(orient="records") if not df.empty else []

        # Sanitize for JSON
        import math
        from decimal import Decimal
        for row in rows:
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif isinstance(v, bytes):
                    row[k] = v.decode("utf-8", errors="replace")
                elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    row[k] = None
                elif isinstance(v, Decimal):
                    row[k] = float(v)

        return json.dumps({
            "columns": columns,
            "rows": rows[:100],  # Limit for MCP response
            "row_count": row_count,
            "elapsed_ms": elapsed_ms,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        logger.error("execute_sql failed: %s", e)
        return json.dumps({"error": str(e)})


async def handle_analyze_data(arguments: dict) -> str:
    """Analyze data using the analysis pipeline."""
    from services.shared.common.llm.llm_client import generate_sql as call_llm
    from services.datamind.nl2sql.sql.template_loader import get_analysis_prompt

    question = arguments.get("question", "")
    analysis_type = arguments.get("analysis_type", "general")
    datasource_id = arguments.get("datasource_id", 0)

    if not question:
        return json.dumps({"error": "question is required"})

    # First, get relevant data via NL2SQL
    from services.datamind.nl2sql.orchestrator.pipeline_orchestrator import execute_pipeline

    query_result = None
    try:
        async for event_type, data in execute_pipeline(
            question=question,
            history=[],
            datasource_id=datasource_id,
            pipeline_mode="quick",
            user_id=0,
            username="mcp",
        ):
            if event_type == "done":
                query_result = data
    except Exception as e:
        logger.error("analyze_data query failed: %s", e)
        return json.dumps({"error": f"Query failed: {str(e)}"})

    if not query_result or not query_result.get("result"):
        return json.dumps({
            "error": "No data retrieved for analysis",
            "reply": query_result.get("reply", "") if query_result else "",
        })

    result_data = query_result["result"]
    columns = result_data.get("columns", [])
    rows = result_data.get("rows", [])

    if not columns or not rows:
        return json.dumps({"reply": "No data to analyze"})

    # Run LLM analysis
    try:
        tpl = get_analysis_prompt()
        fields_text = "\n".join([f"- {c}" for c in columns])
        data_text = json.dumps(rows[:100], ensure_ascii=False, default=str)

        analysis_prompt = f"分析类型: {analysis_type}\n"
        user_content = tpl["user_tpl"].format(fields=fields_text, data=data_text)
        messages = [
            {"role": "system", "content": tpl["system"]},
            {"role": "user", "content": f"{analysis_prompt}用户问题: {question}\n\n{user_content}"},
        ]

        llm_result = call_llm(messages)
        return json.dumps({
            "analysis_type": analysis_type,
            "reply": llm_result.get("sql", ""),
            "data_columns": columns,
            "data_row_count": len(rows),
            "tokens": llm_result.get("tokens", {}),
        }, ensure_ascii=False, default=str)

    except Exception as e:
        logger.error("analyze_data LLM failed: %s", e)
        return json.dumps({"error": f"Analysis failed: {str(e)}"})


# ── MCP JSON-RPC Handler ─────────────────────────────────────────────

TOOL_HANDLERS = {
    "query_data": handle_query_data,
    "execute_sql": handle_execute_sql,
    "analyze_data": handle_analyze_data,
}


async def handle_request(request: dict) -> dict:
    """Handle a single MCP JSON-RPC request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "datamind",
                    "version": "1.0.0",
                },
            },
        }

    elif method == "notifications/initialized":
        # Notification, no response needed
        return None

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    "isError": True,
                },
            }

        try:
            result_text = await handler(arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                },
            }
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Tool execution failed: {str(e)}"}],
                    "isError": True,
                },
            }

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


async def run_stdio_server():
    """Run MCP server over stdio (JSON-RPC)."""
    logger.info("DataMind MCP server starting on stdio...")

    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        try:
            # Read Content-Length header
            header_line = await reader.readline()
            if not header_line:
                break

            header = header_line.decode("utf-8").strip()
            if not header:
                continue

            content_length = 0
            if header.startswith("Content-Length:"):
                content_length = int(header.split(":")[1].strip())

            # Read empty line separator
            await reader.readline()

            if content_length > 0:
                body = await reader.readexactly(content_length)
                request = json.loads(body.decode("utf-8"))
            else:
                continue

            response = await handle_request(request)
            if response is not None:
                response_bytes = json.dumps(response).encode("utf-8")
                sys.stdout.write(f"Content-Length: {len(response_bytes)}\r\n\r\n")
                sys.stdout.write(response_bytes.decode("utf-8"))
                sys.stdout.flush()

        except asyncio.IncompleteReadError:
            break
        except Exception as e:
            logger.error("MCP server error: %s", e)
            break


def main():
    """Entry point for the MCP server."""
    asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()
