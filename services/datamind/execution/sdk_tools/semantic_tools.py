"""semantic 工具组 — 业务语义增强(get_metrics / get_glossary / query_by_tags / knowledge_search).

handler 为 SDK 无关的纯函数,由 build_semantic_server(backend) 包装。
"""

import asyncio
import json
import logging
from typing import Annotated, Optional

from services.datamind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)


# ── 工具 handler(SDK 无关) ─────────────────────────────────────

async def get_metrics(args):
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from services.datacatalog.services import metrics_service
        result = await asyncio.to_thread(
            metrics_service.list_metrics,
            1,
            20,
            None,
            None,
            args.get("keyword") or "",
            ctx.workspace_id,
        )
        return _text(result)
    except Exception as e:
        logger.error("get_metrics error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def get_glossary(args):
    try:
        from services.datacatalog.services.term_service import TermService
        result = await asyncio.to_thread(
            TermService.list_terms,
            1,
            20,
            args.get("keyword") or "",
        )
        return _text(result)
    except Exception as e:
        logger.error("get_glossary error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def query_by_tags(args):
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from services.datacatalog.services import tags_service
        params = json.loads(args.get("tag_conditions") or "{}")
        conditions = params.get("conditions", [])
        if not conditions:
            return _text({"error": "At least one tag condition required"}, is_error=True)
        result = await asyncio.to_thread(
            tags_service.query_entities_by_tags,
            conditions,
            params.get("operator", "AND"),
            ctx.workspace_id,
        )
        return _text({"items": result, "total": len(result)})
    except json.JSONDecodeError:
        return _text({"error": "Invalid JSON in tag_conditions"}, is_error=True)
    except Exception as e:
        logger.error("query_by_tags error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def knowledge_search(args):
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    user_context = {"user_id": ctx.user_id, "username": ctx.username}
    try:
        from services.datamind.rag.rag_retriever import retrieve_with_ranger_filter
        result = await retrieve_with_ranger_filter(
            args.get("question", ""),
            args.get("datasource_id") or ctx.datasource_id or 0,
            user_context,
        )
        return _text(result)
    except Exception as e:
        logger.error("knowledge_search error: %s", e)
        return _text({"error": str(e)}, is_error=True)


# 工具元信息(name / description / schema / annotations)
TOOL_SPECS = [
    {
        "name": "get_metrics",
        "description": (
            "Look up business metric definitions (name, calculation formula/SQL, description). "
            "Use this when the question mentions business metrics (e.g. 销售额, DAU, 转化率) "
            "so you use the official calculation instead of guessing."
        ),
        "schema": {"keyword": Annotated[Optional[str], "Metric name keyword; empty means list all"]},
        "handler": get_metrics,
    },
    {
        "name": "get_glossary",
        "description": (
            "Search the business glossary for term definitions and field mappings "
            "(e.g. what '华东' or '活跃用户' means in this business). "
            "Use this to translate business terms before writing SQL filters."
        ),
        "schema": {"keyword": Annotated[Optional[str], "Term keyword in Chinese or English; empty means list all"]},
        "handler": get_glossary,
    },
    {
        "name": "query_by_tags",
        "description": (
            "Find data entities (tables etc.) by tag conditions, e.g. entities tagged '财务' AND '核心'. "
            "tag_conditions is a JSON string: {\"conditions\": [{\"tag_id\": 1}], \"operator\": \"AND\"}."
        ),
        "schema": {"tag_conditions": Annotated[str, 'JSON string, e.g. {"conditions": [{"tag_id": 1}], "operator": "AND"}']},
        "handler": query_by_tags,
    },
    {
        "name": "knowledge_search",
        "description": (
            "Retrieve relevant table/column metadata for a natural-language question via RAG "
            "(BM25 + vector + graph hybrid). Returns candidate tables and columns with business "
            "descriptions — a good complement to keyword-based search_metadata."
        ),
        "schema": {
            "question": Annotated[str, "Natural language question, e.g. '上个月的销售额'"],
            "datasource_id": Annotated[Optional[int], "Optional datasource id to narrow retrieval"],
        },
        "handler": knowledge_search,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_semantic_server(backend: str = "qoder"):
    """构建 semantic 进程内 MCP server(qoder / claude)."""
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool

    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in TOOL_SPECS
    ]
    return make_server(backend, "datahub_semantic", tools)
