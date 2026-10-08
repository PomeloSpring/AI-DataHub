"""ontology 工具组 — 本体模型与元数据管理工具.

handler 为 SDK 无关的纯函数, 由 build_ontology_server(backend) 包装.

只读工具(无需权限码把关):
- search_ontology      — 搜索已有本体对象
- get_ontology_model   — 获取本体模型详情
- list_ontology_models — 列出所有本体模型
- get_metadata_summary — 获取元数据摘要(表数/列数/术语数)

写操作工具(菜单与功能权限码把关后直执行, 不再走审批回路):
- generate_ontology_draft — 取本体归纳素材(分批, 不调 LLM) (ontology:generate)
- save_ontology_draft     — 提交归纳对象落成草案 (ontology:generate)
- save_ontology_model     — 保存本体模型编辑 (ontology:save)
- activate_ontology_model — 激活本体模型 (ontology:activate)
- import_ontology_yaml    — 导入 Palantir YAML (ontology:import)

本体生成唯一回路：generate_ontology_draft 取素材 → 会话内 agent(qoder) 归纳 →
save_ontology_draft 提交；工具本身**不调用任何 LLM**（归纳属 AS-BOT 会话任务，
不得在工具内另起第二条 LLM 生成通道）。

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
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.modules.catalog.services import ontology_service

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
        from backend.modules.catalog.services import ontology_service

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
        from backend.modules.catalog.services import ontology_service

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
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        from backend.common.db.metadata_db import get_metadata_conn

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

                    model_filter = (
                        "AND (kind = 'business' OR (kind = 'source' AND datasource_id = %s))"
                        if datasource_id
                        else "AND kind = 'system'"
                    )
                    cur.execute(
                        f"SELECT COUNT(*) as cnt FROM adh_ontology_models WHERE status = 'active' {model_filter}",
                        params if datasource_id else (),
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
# 写操作工具 handler (菜单与功能权限码把关, 直执行)
# ═══════════════════════════════════════════════════════════════════

def _require_write_perm(perm_code: str, label: str) -> None:
    """写动作直执行前的权限码把关（fail-closed，拒绝原因可解释）。"""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.modules.mind.execution.perm_link import require_write_perm

    ctx = get_execution_context()
    require_write_perm(getattr(ctx, "user_id", 0) or 0, getattr(ctx, "workspace_id", 0) or 0,
                       perm_code, label)


async def generate_ontology_draft(args):
    """取本体归纳素材（分批）— 素材提供工具，**不调用 LLM**。

    归纳由 AS-BOT 会话内的 agent 完成（唯一回路：取素材 → 归纳 → save_ontology_draft）。
    本体生成的域约束（as-bot-system-waker §2）：目标源固定为执行上下文会话绑定源
    ctx.datasource_id（0=系统本体域），**不接受** LLM 传数据源标识（_reject_datasource_arg）；
    业务源仅限『生成本体模型』任务绑定会话取素材（assert_ontology_draft_scope，fail-closed）。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.modules.mind.execution.resource_guard import assert_ontology_draft_scope

    ctx = get_execution_context()
    try:
        _reject_datasource_arg(args)
        target = assert_ontology_draft_scope(ctx)
        _require_write_perm("ontology:generate", "生成本体草案")
    except (ValueError, PermissionError) as e:
        return _text({"error": str(e)}, is_error=True)

    def _run():
        from backend.modules.catalog.services import ontology_service
        return ontology_service.build_generation_batches(target)

    try:
        batches = await asyncio.to_thread(_run)
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)

    batch = int(args.get("batch") or 0)
    if batch < 0 or batch >= len(batches):
        return _text({"error": f"batch 越界：有效范围 0..{len(batches) - 1}"}, is_error=True)

    from backend.modules.catalog.services.ontology_service import ONTOLOGY_SCHEMA_SPEC
    return _text({
        "batch": batch,
        "total_batches": len(batches),
        "material": batches[batch],
        "spec": ONTOLOGY_SCHEMA_SPEC,
        "note": ("请归纳本批材料的业务对象（结构见 spec），逐批调用 save_ontology_draft 提交："
                 f"第 1 批用 append=false，第 2 批起 append=true；共 {len(batches)} 批"),
    })


async def save_ontology_draft(args):
    """提交归纳出的业务对象，落成该源本体草案 — 菜单与功能权限码把关后直执行。

    分批归纳时第一批 append=false（替换旧草案），后续批次 append=true（并入合并）；
    同身份对象由服务端确定性合并（合并事实随 warnings 显式带回），同名不同主表
    冲突直接中止（宁缺勿错，需人工裁决）。域约束同 generate_ontology_draft：
    目标源=会话绑定源，业务源仅限『生成本体模型』任务绑定会话。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.modules.mind.execution.resource_guard import assert_ontology_draft_scope

    ctx = get_execution_context()
    objects = args.get("objects")
    if isinstance(objects, str):
        try:
            objects = json.loads(objects)
        except ValueError as e:
            return _text({"error": f"objects 不是合法 JSON: {e}"}, is_error=True)
    if not isinstance(objects, list) or not objects:
        return _text({"error": "objects 必填（归纳出的对象数组）"}, is_error=True)

    try:
        _reject_datasource_arg(args)
        target = assert_ontology_draft_scope(ctx)
        _require_write_perm("ontology:generate", "生成本体草案")
    except (ValueError, PermissionError) as e:
        return _text({"error": str(e)}, is_error=True)

    def _run():
        from backend.modules.catalog.services import ontology_service
        return ontology_service.save_generated_draft(
            target, objects,
            domain=str(args.get("domain") or ""),
            description=str(args.get("description") or ""),
            append=bool(args.get("append")),
            created_by=ctx.username if ctx else "",
        )

    try:
        model = await asyncio.to_thread(_run)
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)

    warnings = model.get("generation_warnings") or []
    return _text({
        "success": True,
        "model_id": model["id"],
        "object_count": model.get("object_count", 0),
        "merged": len(warnings),
        "warnings": warnings,
        "note": ("草案已落库；系统本体如需生效请继续调用 activate_ontology_model，"
                 "业务源草案请用户在建模页检查后激活"),
    })


async def save_ontology_model(args):
    """保存本体模型编辑 — 菜单与功能权限码把关后直执行。"""
    model_id = int(args.get("model_id") or 0)
    json_content = args.get("json_content")

    if not model_id:
        return _text({"error": "model_id 必填"}, is_error=True)
    if not json_content:
        return _text({"error": "json_content 必填"}, is_error=True)
    try:
        _assert_system_model(model_id)   # 域约束：仅系统本体（执行前拒）
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)
    try:
        _require_write_perm("ontology:save", "保存本体模型")
    except PermissionError as e:
        return _text({"error": str(e)}, is_error=True)

    def _run():
        from backend.modules.catalog.services import ontology_service
        return ontology_service.save_draft(
            model_id,
            json_content if isinstance(json_content, str) else json.dumps(json_content, ensure_ascii=False),
            name=args.get("name"),
        )

    model = await asyncio.to_thread(_run)
    return _text({"success": True, "model_id": model["id"]})


async def activate_ontology_model(args):
    """激活本体模型 — 菜单与功能权限码把关后直执行。"""
    model_id = int(args.get("model_id") or 0)
    if not model_id:
        return _text({"error": "model_id 必填"}, is_error=True)
    try:
        _assert_system_model(model_id)   # 域约束：仅系统本体（执行前拒）
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)
    try:
        _require_write_perm("ontology:activate", "激活本体模型")
    except PermissionError as e:
        return _text({"error": str(e)}, is_error=True)

    def _run():
        from backend.modules.catalog.services import ontology_service
        return ontology_service.activate(model_id)

    model = await asyncio.to_thread(_run)
    return _text({"success": True, "model_id": model["id"],
                  "status": model.get("status"),
                  "note": "旧 active 模型已归档，对象已写入元数据库并重建图谱"})


async def import_ontology_yaml(args):
    """导入 Palantir YAML — 菜单与功能权限码把关后直执行。

    同 generate_ontology_draft：限系统本体域，不接受 LLM 传数据源标识。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    _reject_datasource_arg(args)
    _require_write_perm("ontology:import", "导入本体 YAML")
    datasource_id = 0  # 系统本体域固定值，服务端注入

    def _run():
        from backend.modules.catalog.services import ontology_yaml_import
        return ontology_yaml_import.import_palantir_yaml(
            dir_path=args.get("dir"),
            datasource_id=datasource_id,
            created_by=ctx.username if ctx else "",
            rebuild_graph=True,
        )

    result = await asyncio.to_thread(_run)
    return _text({"success": True, "model_id": result.get("model_id")})


def _reject_datasource_arg(args):
    """拒收 LLM 传入的 datasource_id（守 §2 黑盒：LLM 拿不到真实 id，只会猜）。

    出现即报错而非静默丢弃 —— 让 LLM 知道这个参数不该传，
    同时服务端日志可定位是哪个工具描述误导了它。
    """
    if isinstance(args, dict) and args.get("datasource_id") not in (None, "", 0, "0"):
        raise ValueError("该工具不接受数据源标识参数；数据源由服务端按系统本体域注入")


def _assert_system_model(model_id: int) -> None:
    """AS-BOT 本体写操作域约束：目标模型必须是系统本体（kind='system'）。

    判别口径用 **kind**，不用 datasource_id=0（x3 归属键改造）：
    业务本体的 datasource_id 也是 0（本体归属已按业务域而非数据源），
    旧口径会把业务本体误判为系统本体从而放行写入。
    """
    from backend.common.db.metadata_db import get_metadata_conn

    if not model_id:
        raise ValueError("model_id 必填")
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, kind FROM adh_ontology_models WHERE id = %s", (int(model_id),))
            row = cur.fetchone()
    if not row:
        raise ValueError(f"本体模型不存在: {model_id}")
    kind = str(row.get("kind") or "")
    if kind != "system":
        raise ValueError(
            f"AS-BOT 仅可操作系统本体（kind='system'），目标模型「{row.get('name')}」"
            f"是{kind or '未分类'}本体；业务侧建模属用户在业务工作空间的操作，不经本通道")


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
    # 写操作工具 (菜单与功能权限码把关, 直执行)
    {
        "name": "generate_ontology_draft",
        "description": (
            "Fetch one batch of modeling material (tables, columns, known relations, "
            "business terms/metrics) for ontology draft induction, plus the required "
            "output structure (spec). This tool does NOT use LLM — YOU induce the "
            "business objects from the material and submit them via save_ontology_draft "
            "(first batch append=false, later batches append=true)."
        ),
        "schema": {
            # 目标源由服务端按会话绑定源注入；刻意不暴露数据源标识（waker-datasource-domain §2）。
            "batch": Annotated[Optional[int], "0-based batch index; omit for the first batch"],
        },
        "handler": generate_ontology_draft,
        "readonly": True,
    },
    {
        "name": "save_ontology_draft",
        "description": (
            "Submit induced ontology objects to create/replace the ontology draft of the "
            "session's bound source. Server-side deterministic validation merges duplicate "
            "objects by identity and aborts on same-key/different-primary-table conflicts. "
            "Returns model_id, object_count and merge warnings."
        ),
        "schema": {
            "objects": Annotated[list, "Induced ontology objects (structure per spec from generate_ontology_draft)"],
            "domain": Annotated[Optional[str], "Business domain name of the source"],
            "description": Annotated[Optional[str], "2-3 sentence business overview"],
            "append": Annotated[Optional[bool], "true = merge into current draft (batch 2+); false/omit = replace draft"],
        },
        "handler": save_ontology_draft,
        "readonly": False,
    },
    {
        "name": "save_ontology_model",
        "description": (
            "Save edits to an ontology model's JSON content."
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
            "Objects will be written to the metadata DB and the knowledge graph rebuilt."
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
            "Import Palantir Ontology YAML files as an active ontology model."
        ),
        "schema": {
            "dir": Annotated[Optional[str], "Directory path (default: ontology/)"],
        },
        "handler": import_ontology_yaml,
        "readonly": False,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_ontology_server(backend: str = "qoder", tool_names=None):
    """构建 ontology 进程内 MCP server.

    tool_names 给定时只注册被选中的工具(AS-BOT 粒度的逐个工具权限控制);
    为空则注册本组全部工具.
    """
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

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
