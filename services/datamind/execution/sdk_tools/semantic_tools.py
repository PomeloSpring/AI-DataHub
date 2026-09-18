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
    """对象级语义目录: 每个本体对象可用的指标/维度(含别名与枚举业务标签)。

    只暴露字典层信息(name/aliases/unit/枚举标签), 不回显 target_table 等物理细节。
    """
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from services.shared.semantics.mdl_compiler import _get_metrics, _get_dimensions

        ds_id = (ctx.datasource_id if ctx else 0) or 0
        keyword = (args.get("keyword") or "").strip().lower()
        metrics = await asyncio.to_thread(_get_metrics, ds_id)
        dims = await asyncio.to_thread(_get_dimensions, ds_id)

        def _match(row):
            if not keyword:
                return True
            hay = {str(row.get("name") or ""), str(row.get("name_en") or "")}
            hay |= {str(a) for a in (row.get("aliases") or [])}
            return any(keyword in h.lower() for h in hay if h)

        objects: dict[str, dict] = {}

        def bucket(key: str) -> dict:
            return objects.setdefault(key or "(未绑定对象)",
                                      {"object": key or "", "metrics": [], "dimensions": []})

        for m in metrics:
            if not _match(m):
                continue
            row = {"name": m["name"], "aliases": m.get("aliases") or [],
                   "unit": m.get("unit") or "", "description": m.get("description") or ""}
            if m.get("formula"):
                row["calculation"] = m["formula"]
            bucket(m.get("bound_object_key")).setdefault("metrics", []).append(row)
        for d in dims:
            if not _match(d):
                continue
            bucket(d.get("bound_object_key")).setdefault("dimensions", []).append({
                "name": d["name"], "aliases": d.get("aliases") or [],
                "is_time": (d.get("category") or "") == "时间",
                "enum": d.get("value_labels") or {},
                "description": d.get("description") or "",
            })
        # 排掉 keyword 未命中的空桶
        items = [v for v in objects.values() if v["metrics"] or v["dimensions"]]
        return _text({"objects": items, "total": len(items),
                      "usage": "dimensions/metrics 直接复制此处 name 或其 aliases; 按天/月趋势选 is_time=true 的维度并配 time_grain; "
                               "enum 字段列出码值→业务名, filter 可传业务名也可传码值, 结果自动显示业务名; "
                               "若报错'无法解析', 从返回的可用清单改名重试。"})
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
    try:
        from services.datamind.rag.qmind_retriever import qmind_retrieve
        result = await asyncio.to_thread(
            qmind_retrieve,
            args.get("question", ""),
            args.get("datasource_id") or (ctx.datasource_id if ctx else 0) or 0,
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
            "List the semantic catalog grouped by ontology object: for each object the usable "
            "metrics (name/aliases/unit/calculation) and dimensions (name/aliases/is_time/enum "
            "value labels). ALWAYS copy metric/dimension names (or their aliases) from this "
            "catalog into run_semantic_query instead of inventing names; use is_time dimensions "
            "with time_grain for trends; enum maps stored codes to business labels "
            "(filters accept either). Optional keyword narrows by name/alias."
        ),
        "schema": {"keyword": Annotated[Optional[str], "Metric/dimension name keyword; empty means list all"]},
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
            "Retrieve authoritative business context for a natural-language question. "
            "Defaults to the bound QMind knowledge base (Qoder knowledge retrieval); when no "
            "callable QMind source is configured it falls back to hybrid metadata retrieval "
            "(BM25 + vector + graph) returning candidate tables/columns and business terms. "
            "Prefer this before writing SQL filters or interpreting business metrics."
        ),
        "schema": {
            "question": Annotated[str, "Natural language question, e.g. '上个月的销售额'"],
            "datasource_id": Annotated[Optional[int], "Optional datasource id to narrow retrieval"],
        },
        "handler": knowledge_search,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_semantic_server(backend: str = "qoder", tool_names=None):
    """构建 semantic 进程内 MCP server(qoder / claude).

    Phase 3 将 run_semantic_query 归入本组（与 retrieval 类工具同层），
    保证 agent 只需开启 “semantic” 即可拿到完整“发现对象 + 下意”工具集。
    tool_names 给定时只注册被选中的工具(waker 粒度逐个控制)。
    """
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool
    from services.datamind.execution.sdk_tools.semantic_query import (
        TOOL_SPECS as SEMANTIC_QUERY_SPECS,
    )

    specs = list(TOOL_SPECS) + list(SEMANTIC_QUERY_SPECS)
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in specs if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_semantic", tools)
