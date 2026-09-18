"""ontology 工具组 — 本体模型与元数据管理工具.

handler 为 SDK 无关的纯函数, 由 build_ontology_server(backend) 包装.

只读工具(无需审批):
- search_ontology      — 搜索已有本体对象
- get_ontology_model   — 获取本体模型详情
- list_ontology_models — 列出所有本体模型
- get_metadata_summary — 获取元数据摘要(表数/列数/术语数)

写操作工具(需审批, 返回 approval_required 标记):
- generate_ontology_draft — 生成本体草案
- save_ontology_model     — 保存本体模型编辑
- activate_ontology_model — 激活本体模型
- import_ontology_yaml    — 导入 Palantir YAML

安全约束: 本工具组**不包含** execute_sql 或任何直连数据源的工具.
"""

import asyncio
import json
import logging
from typing import Annotated, Optional

logger = logging.getLogger(__name__)


def _text(data, is_error: bool = False) -> dict:
    """统一 CallToolResult 构造."""
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}], **({"isError": True} if is_error else {})}


# ═══════════════════════════════════════════════════════════════════
# 只读工具 handler
# ═══════════════════════════════════════════════════════════════════

async def search_ontology(args):
    """搜索本体对象."""
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from services.datacatalog.services import ontology_service

        keyword = args.get("keyword", "").strip()
        datasource_id = args.get("datasource_id") or 0
        limit = min(args.get("limit", 10), 20)

        if not keyword:
            return _text({"error": "keyword 不能为空"}, is_error=True)

        hits = await asyncio.to_thread(
            ontology_service.search_objects, keyword, datasource_id=datasource_id, limit=limit
        )
        return _text({
            "results": [
                {
                    "object_key": h.get("object_key"),
                    "display_name": h.get("display_name"),
                    "aliases": h.get("aliases"),
                    "description": h.get("description"),
                }
                for h in hits
            ],
            "total": len(hits),
        })
    except Exception as e:
        logger.error("search_ontology error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def get_ontology_model(args):
    """获取本体模型详情."""
    try:
        from services.datacatalog.services import ontology_service

        model_id = int(args.get("model_id", 0))
        if not model_id:
            return _text({"error": "model_id 必填"}, is_error=True)

        model = await asyncio.to_thread(ontology_service.get_model, model_id)
        if not model:
            return _text({"error": f"模型 {model_id} 不存在"}, is_error=True)

        # 返回摘要而非完整 JSON, 避免过大
        doc = {}
        try:
            doc = json.loads(model.get("json_content") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        return _text({
            "id": model["id"],
            "name": model.get("name"),
            "datasource_id": model.get("datasource_id"),
            "status": model.get("status"),
            "object_count": model.get("object_count"),
            "objects_summary": [
                {
                    "key": obj.get("key") or obj.get("class"),
                    "display_name": obj.get("display_name") or obj.get("label"),
                    "primary_table": obj.get("primary_table"),
                    "description": (obj.get("description") or "")[:200],
                }
                for obj in doc.get("objects", [])
            ],
            "created_at": model.get("created_at"),
            "updated_at": model.get("updated_at"),
        })
    except Exception as e:
        logger.error("get_ontology_model error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def list_ontology_models(args):
    """列出所有本体模型."""
    try:
        from services.datacatalog.services import ontology_service

        datasource_id = args.get("datasource_id") or 0
        include_archived = bool(args.get("include_archived", False))

        models = await asyncio.to_thread(
            ontology_service.list_models, datasource_id, include_archived=include_archived
        )
        return _text({
            "models": [
                {
                    "id": m["id"],
                    "name": m.get("name"),
                    "datasource_id": m.get("datasource_id"),
                    "status": m.get("status"),
                    "object_count": m.get("object_count"),
                    "created_at": m.get("created_at"),
                    "updated_at": m.get("updated_at"),
                }
                for m in models
            ],
            "total": len(models),
        })
    except Exception as e:
        logger.error("list_ontology_models error: %s", e)
        return _text({"error": str(e)}, is_error=True)


async def get_metadata_summary(args):
    """获取元数据摘要: 表数/列数/术语数/指标数."""
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from services.shared.common.db.metadata_db import get_metadata_conn

        datasource_id = args.get("datasource_id") or 0

        def _query_summary():
            with get_metadata_conn() as conn:
                with conn.cursor() as cur:
                    ds_filter = "AND datasource_id = %s" if datasource_id else ""
                    params = (datasource_id,) if datasource_id else ()

                    cur.execute(
                        f"SELECT COUNT(*) as cnt FROM adh_table_info WHERE is_active = 1 {ds_filter}",
                        params,
                    )
                    tables = cur.fetchone().get("cnt", 0)

                    cur.execute(
                        f"SELECT COUNT(*) as cnt FROM adh_column_metadata WHERE is_active = 1 {ds_filter}",
                        params,
                    )
                    columns = cur.fetchone().get("cnt", 0)

                    cur.execute(
                        f"SELECT COUNT(*) as cnt FROM adh_business_terms WHERE is_active = 1 "
                        f"{'AND (datasource_id = %s OR datasource_id = 0)' if datasource_id else ''}",
                        params,
                    )
                    terms = cur.fetchone().get("cnt", 0)

                    cur.execute(
                        f"SELECT COUNT(*) as cnt FROM adh_metrics WHERE is_active = 1 "
                        f"{'AND (datasource_id = %s OR datasource_id = 0)' if datasource_id else ''}",
                        params,
                    )
                    metrics = cur.fetchone().get("cnt", 0)

                    cur.execute(
                        f"SELECT COUNT(*) as cnt FROM adh_ontology_models WHERE status = 'active' "
                        f"{'AND datasource_id = %s' if datasource_id else ''}",
                        params,
                    )
                    active_models = cur.fetchone().get("cnt", 0)

            return {
                "datasource_id": datasource_id,
                "tables": tables,
                "columns": columns,
                "business_terms": terms,
                "metrics": metrics,
                "active_ontology_models": active_models,
            }

        result = await asyncio.to_thread(_query_summary)
        return _text(result)
    except Exception as e:
        logger.error("get_metadata_summary error: %s", e)
        return _text({"error": str(e)}, is_error=True)


# ═══════════════════════════════════════════════════════════════════
# 写操作工具 handler (需审批)
# ═══════════════════════════════════════════════════════════════════

async def generate_ontology_draft(args):
    """生成本体草案 — 返回 approval_required 标记, 需用户审批后执行."""
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    datasource_id = int(args.get("datasource_id") or 0)
    if not datasource_id:
        return _text({"error": "datasource_id 必填"}, is_error=True)

    # 返回审批请求, 前端渲染审批卡片
    return _text({
        "approval_required": True,
        "action_key": "ontology.generate",
        "action_label": "生成本体模型草案",
        "description": f"基于数据源 {datasource_id} 的元数据, 由 LLM 生成本体模型草案。生成后需进一步审批才能激活。",
        "payload": {
            "datasource_id": datasource_id,
            "created_by": ctx.username if ctx else "",
        },
    })


async def save_ontology_model(args):
    """保存本体模型编辑 — 返回 approval_required 标记."""
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    model_id = int(args.get("model_id") or 0)
    json_content = args.get("json_content")

    if not model_id:
        return _text({"error": "model_id 必填"}, is_error=True)
    if not json_content:
        return _text({"error": "json_content 必填"}, is_error=True)

    return _text({
        "approval_required": True,
        "action_key": "ontology.save",
        "action_label": "保存本体模型",
        "description": f"保存本体模型 #{model_id} 的编辑内容。",
        "payload": {
            "model_id": model_id,
            "json_content": json_content if isinstance(json_content, str) else json.dumps(json_content, ensure_ascii=False),
            "name": args.get("name"),
        },
    })


async def activate_ontology_model(args):
    """激活本体模型 — 返回 approval_required 标记."""
    model_id = int(args.get("model_id") or 0)
    if not model_id:
        return _text({"error": "model_id 必填"}, is_error=True)

    return _text({
        "approval_required": True,
        "action_key": "ontology.activate",
        "action_label": "激活本体模型",
        "description": f"激活本体模型 #{model_id}。旧 active 模型将被归档, 对象将写入元数据库并重建图谱。",
        "payload": {"model_id": model_id},
    })


async def import_ontology_yaml(args):
    """导入 Palantir YAML — 返回 approval_required 标记."""
    from services.datamind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    datasource_id = int(args.get("datasource_id") or 0)
    if not datasource_id:
        return _text({"error": "datasource_id 必填"}, is_error=True)

    return _text({
        "approval_required": True,
        "action_key": "ontology.import_yaml",
        "action_label": "导入 Palantir YAML 本体",
        "description": f"从 ontology/ 目录导入 Palantir YAML 到数据源 {datasource_id}, 将覆盖当前 active 模型。",
        "payload": {
            "datasource_id": datasource_id,
            "dir": args.get("dir"),
            "created_by": ctx.username if ctx else "",
            "rebuild_graph": True,
        },
    })


# ═══════════════════════════════════════════════════════════════════
# 工具规格
# ═══════════════════════════════════════════════════════════════════

TOOL_SPECS = [
    # 只读工具
    {
        "name": "search_ontology",
        "description": (
            "Search ontology objects by keyword. Returns matching object classes, "
            "their display names, aliases and descriptions. Use to discover existing "
            "ontology models before creating new ones."
        ),
        "schema": {
            "keyword": Annotated[str, "Search keyword, e.g. '订单' or 'Shipment'"],
            "datasource_id": Annotated[Optional[int], "Filter by datasource (0 or omit = all)"],
            "limit": Annotated[Optional[int], "Max results (default 10, max 20)"],
        },
        "handler": search_ontology,
        "readonly": True,
    },
    {
        "name": "get_ontology_model",
        "description": (
            "Get details of a specific ontology model by ID. Returns model metadata "
            "and a summary of its objects (key, display_name, primary_table)."
        ),
        "schema": {
            "model_id": Annotated[int, "The ontology model ID"],
        },
        "handler": get_ontology_model,
        "readonly": True,
    },
    {
        "name": "list_ontology_models",
        "description": (
            "List all ontology models. Returns id, name, status, object_count for each. "
            "Use to browse available models before viewing details."
        ),
        "schema": {
            "datasource_id": Annotated[Optional[int], "Filter by datasource (0 or omit = all)"],
            "include_archived": Annotated[Optional[bool], "Include archived models (default false)"],
        },
        "handler": list_ontology_models,
        "readonly": True,
    },
    {
        "name": "get_metadata_summary",
        "description": (
            "Get a summary of metadata statistics: table count, column count, "
            "business terms count, metrics count, active ontology models count."
        ),
        "schema": {
            "datasource_id": Annotated[Optional[int], "Filter by datasource (0 or omit = all)"],
        },
        "handler": get_metadata_summary,
        "readonly": True,
    },
    # 写操作工具 (需审批)
    {
        "name": "generate_ontology_draft",
        "description": (
            "Generate an ontology model draft from metadata using LLM. "
            "REQUIRES USER APPROVAL before execution. "
            "Analyzes tables, columns, terms, metrics and relations to produce "
            "a structured ontology model."
        ),
        "schema": {
            "datasource_id": Annotated[int, "The datasource ID to generate ontology for"],
        },
        "handler": generate_ontology_draft,
        "readonly": False,
    },
    {
        "name": "save_ontology_model",
        "description": (
            "Save edits to an ontology model's JSON content. "
            "REQUIRES USER APPROVAL before execution."
        ),
        "schema": {
            "model_id": Annotated[int, "The ontology model ID to save"],
            "json_content": Annotated[str, "The full JSON content of the ontology model"],
            "name": Annotated[Optional[str], "Optional new name for the model"],
        },
        "handler": save_ontology_model,
        "readonly": False,
    },
    {
        "name": "activate_ontology_model",
        "description": (
            "Activate an ontology model. The previous active model will be archived. "
            "Objects will be written to the metadata DB and the knowledge graph rebuilt. "
            "REQUIRES USER APPROVAL before execution."
        ),
        "schema": {
            "model_id": Annotated[int, "The ontology model ID to activate"],
        },
        "handler": activate_ontology_model,
        "readonly": False,
    },
    {
        "name": "import_ontology_yaml",
        "description": (
            "Import Palantir Ontology YAML files as an active ontology model. "
            "REQUIRES USER APPROVAL before execution."
        ),
        "schema": {
            "datasource_id": Annotated[int, "Target datasource ID"],
            "dir": Annotated[Optional[str], "Directory path (default: ontology/)"],
        },
        "handler": import_ontology_yaml,
        "readonly": False,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_ontology_server(backend: str = "qoder", tool_names=None):
    """构建 ontology 进程内 MCP server.

    tool_names 给定时只注册被选中的工具(waker 粒度的逐个工具权限控制);
    为空则注册本组全部工具.
    """
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool

    specs = TOOL_SPECS
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in TOOL_SPECS if s["name"] in selected]

    tools = []
    for s in specs:
        annotations = READONLY_ANNOTATIONS if s.get("readonly") else None
        tools.append(
            make_tool(backend, s["name"], s["description"], s["schema"],
                      s["handler"], annotations=annotations)
        )
    return make_server(backend, "datahub_ontology", tools)
