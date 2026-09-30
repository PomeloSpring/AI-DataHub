"""DataMind MCP Server — Exposes AI capabilities as MCP tools.

权威形态：SSE 端点（由 main.py 挂载为 /mcp，注册于系统配置 MCP 服务菜单）。
stdio 形态已废弃（分帧为 LSP 风格 Content-Length，与 MCP 标准 newline-delimited
不兼容，标准客户端无法连接）；仅保留代码供内部调试，不再维护。

This MCP server provides three tools:
- query_data: Natural language data query (NL2SQL)
- execute_sql: Direct SQL execution against a datasource
- analyze_data: Multi-dimensional data analysis

安全约定（数据护城河）：三个工具的取数全部经治理入口——
query_data/analyze_data 走 NL2SQL 管道（内部 execute_query_with_permission），
execute_sql 直接走 execute_query_with_permission；身份为系统调用
(user_id=0 → sensitive_only 基线，护栏 §2)，无 RBAC/RLS 但敏感基线强制生效。
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
    except PermissionError as e:
        # 治理层拒绝是面向用户的可诊断提示，直接回显（含错因，护栏 §7 例外）。
        result = {"error": str(e)}
    except Exception:
        # 原始报错可能含连接串/主机等物理信息，仅进服务端日志（护栏 §7）。
        logger.exception("query_data failed")
        result = {"error": "查询执行失败，请联系管理员查看服务端日志"}

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
    """Execute SQL directly against the datasource (治理入口，统一取数护栏 §1)。"""
    from services.datamind.nl2sql.sql.query_executor import execute_query_with_permission

    sql = arguments.get("sql", "")
    datasource_id = arguments.get("datasource_id", 0)

    if not sql:
        return json.dumps({"error": "sql is required"})

    try:
        # 系统调用身份（user_id=0）：只套敏感基线，不做 RBAC/RLS（护栏 §2）。
        # 严禁回退到裸 execute_query/get_connection 直连（护栏 §12）。
        df, elapsed_ms, row_count = execute_query_with_permission(
            sql, datasource_id, "sql", {"user_id": 0, "username": "mcp"}, 0)
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

    except PermissionError as e:
        # 治理拒绝（安全校验/权限）是可操作提示，回显给 MCP 客户端。
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception:
        # 原始报错可能含主机/账号等物理信息，仅进服务端日志（护栏 §7）。
        logger.exception("execute_sql failed")
        return json.dumps({"error": "SQL 执行失败，请联系管理员查看服务端日志"}, ensure_ascii=False)


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
    except PermissionError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception:
        logger.exception("analyze_data query failed")
        return json.dumps({"error": "查询执行失败，请联系管理员查看服务端日志"}, ensure_ascii=False)

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
    """Entry point for the (deprecated) stdio MCP server."""
    asyncio.run(run_stdio_server())


def create_mcp_app():
    """SSE 形态的 MCP 端点（内置 MCP 服务的权威暴露形态）。

    挂载：datamind/main.py -> app.mount("/mcp", create_mcp_app())
    对外端点：/mcp/sse（SSE 连接）+ /mcp/messages（JSON-RPC POST）。
    位于 /api/ 权限中间件之外（外部 AI 客户端无法携带 JWT），靠内网边界防护；
    取数安全由工具内部的治理入口保证（见模块 docstring）。
    """
    from mcp.types import TextContent, Tool

    from ..shared.common.mcp_base import create_mcp_server, create_mcp_starlette_app

    server = create_mcp_server(
        "datamind",
        "DataMind MCP Server: NL2SQL query, governed SQL execution, data analysis",
    )

    @server.list_tools()
    async def _list_tools() -> list:
        return [Tool(**spec) for spec in TOOLS]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list:
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            text = json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
        else:
            try:
                text = await handler(arguments or {})
            except Exception:
                # handler 自身已按治理口径处理业务异常；此处仅兜结构性异常，不回显原始报错。
                logger.exception("MCP tool %s failed", name)
                text = json.dumps({"error": "工具执行失败，请联系管理员查看服务端日志"}, ensure_ascii=False)
        return [TextContent(type="text", text=text)]

    return create_mcp_starlette_app(server, sse_path="/sse", message_path="/messages")


if __name__ == "__main__":
    main()
