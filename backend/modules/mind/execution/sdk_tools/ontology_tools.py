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

                    # 源本体已退役（WS2 合并为 business）：选源时计非系统本体（业务），未选时计系统本体。
                    # 真正的按数据源收口由取数/绑定层（binding_resolver/resource_guard/scoped_metadata）保证。
                    model_filter = "AND kind <> 'system'" if datasource_id else "AND kind = 'system'"
                    cur.execute(
                        f"SELECT COUNT(*) as cnt FROM adh_ontology_models WHERE status = 'active' {model_filter}",
                        (),
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
    from backend.core.perm_link import require_write_perm

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

    # 辅助生成的知识库：服务端建任务时已校验并写入 task_binding，这里只负责透传检索范围。
    # kb_ids 不进 LLM 视野（护栏 §7），也不接受工具入参覆盖。
    binding = (getattr(ctx, "extra", None) or {}).get("task_binding") or {}
    kb_ids = [int(x) for x in (binding.get("kb_ids") or []) if str(x).strip().isdigit()]

    def _run():
        from backend.modules.catalog.services import ontology_service
        return ontology_service.build_generation_batches(target, kb_ids)

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
    """提交归纳出的业务对象，落成该业务本体草案 — 菜单与功能权限码把关后直执行。

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
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    model_id = int(args.get("model_id") or 0)
    json_content = args.get("json_content")

    if not model_id:
        return _text({"error": "model_id 必填"}, is_error=True)
    if not json_content:
        return _text({"error": "json_content 必填"}, is_error=True)
    try:
        # 2026-10-27 拍板：系统本体不可编辑（kind='system' 拒），业务本体放行；
        # 写权限由权限码裁决（ontology:save，fail-closed），不碰生效内容、不级联（只存草案）。
        _assert_model_editable(model_id)
        _require_write_perm("ontology:save", "保存本体模型")
    except PermissionError as e:
        return _text({"error": str(e)}, is_error=True)
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)

    def _run():
        from backend.modules.catalog.services import ontology_service
        # 走**只存草案**：不碰生效内容、不级联，等 publish 才生效（两段式第一段）
        return ontology_service.save_draft_only(
            model_id,
            json_content if isinstance(json_content, str) else json.dumps(json_content, ensure_ascii=False),
            name=args.get("name"),
            created_by=str(getattr(ctx, "username", "") or ""),
        )

    model = await asyncio.to_thread(_run)
    return _text({"success": True, "model_id": model["id"]})


def _dict_publish_decision(args, ctx, row_kind: str):
    """字典写的发布判定：默认**不生效**，要生效必须显式发布且持 ontology:publish。

    为什么：新建的指标/维度会立即进入 get_metrics / planner / binding_resolver
    （它们都按 is_active=1 过滤），口径错了（如挂在错对象上、恒返回 0）会直接污染线上取数。
    未发布的行以 is_active=0 落库：列表页可见可编，但语义检索/查询拿不到，不影响现有功能。
    """
    if not args.get("publish"):
        return 0, (f"{row_kind}行已落库为**未发布**状态（is_active=0）："
                   f"不参与语义检索与语义查询，不影响现有取数；"
                   f"确认口径无误后传 publish=true 才会生效")
    try:
        _require_write_perm("ontology:publish", f"发布{row_kind}字典行")
    except PermissionError as e:
        raise ValueError(str(e)) from None
    return 1, f"{row_kind}行已发布生效（is_active=1），对语义检索与查询可见"


async def add_ontology_objects(args):
    """在本体模型中**新增对象**（只增不删，落草案）— 权限码把关后直执行。

    与 save_ontology_model 的区别：本工具只追加对象，不接受删除/裁剪已有定义；
    改动只存草案，需 publish_ontology_model 发布才生效。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    model_id = int(args.get("model_id") or 0)
    objects = args.get("objects")
    if not model_id:
        return _text({"error": "model_id 必填"}, is_error=True)
    if isinstance(objects, str):
        try:
            objects = json.loads(objects)
        except ValueError as e:
            return _text({"error": f"objects 不是合法 JSON: {e}"}, is_error=True)
    if not isinstance(objects, list) or not objects:
        return _text({"error": "objects 必填（要新增的对象数组）"}, is_error=True)
    bad = [o for o in objects if not isinstance(o, dict) or not str(o.get("key") or "").strip()]
    if bad:
        return _text({"error": "每个对象必须含非空 key"}, is_error=True)
    try:
        _reject_datasource_arg(args)
        _assert_model_editable(model_id)   # 系统本体不可编辑（业务本体放行）
        _require_write_perm("ontology:create", "新增本体对象")
    except (ValueError, PermissionError) as e:
        return _text({"error": str(e)}, is_error=True)

    def _run():
        from backend.modules.catalog.services import ontology_service
        import copy
        model = ontology_service.get_model(model_id)
        if not model:
            raise ValueError("模型不存在")
        # 与 update_ontology_object 同口径：**已有草案则基于草案合并**。
        # 早先这里直读生效版，连续两次 add 会把前一次草案里的对象顶掉（只增不删的语义不成立）。
        base_json = (ontology_service.get_draft(model_id).get("draft")
                     or model.get("json_content") or "{}")
        doc = json.loads(base_json)
        if not isinstance(doc, dict):
            doc = {}
        merged = copy.deepcopy(doc)
        existing = {str(o.get("key") or "").strip().lower(): o for o in (merged.get("objects") or [])}
        added, skipped = [], []
        for obj in objects:
            key = str(obj.get("key") or "").strip()
            if key.lower() in existing:
                # 已存在的对象**不覆盖**（只增不删；修改请走 save_ontology_model）
                skipped.append(key)
                continue
            merged.setdefault("objects", []).append(obj)
            existing[key.lower()] = obj
            added.append(key)
        if not added:
            return {"added": [], "skipped": skipped,
                    "note": "所有对象均已存在；如需修改已有对象请用 save_ontology_model"}
        saved = ontology_service.save_draft_only(
            model_id, json.dumps(merged, ensure_ascii=False),
            created_by=str(getattr(ctx, "username", "") or ""))
        return {"added": added, "skipped": skipped, "model_id": saved["id"],
                "draft_pending": True,
                "note": "对象已加入草案，未生效；需发布后才级联到对象表/图谱/知识库"}

    try:
        result = await asyncio.to_thread(_run)
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)
    return _text(result)


async def update_ontology_object(args):
    """按 key **新增或覆盖单个对象**（服务端合并，落草案）。

    为什么需要它：读接口出于脉敏只回对象摘要（properties 为空），而 save_ontology_model
    要求提交**全量 JSON** —— 这个读写不对称使 AS-BOT 根本无法安全地改一个对象
    （拿不到全量就没法重建，拿摘要重建会抹掉其他对象的属性/关系）。
    本工具只替换指定 key 的那一个对象，其余对象由服务端原样保留，
    **结构上不可能写坏其他对象**，因此不需要调用方持有全量定义。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    model_id = int(args.get("model_id") or 0)
    obj = args.get("object")
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError as e:
            return _text({"error": f"object 不是合法 JSON: {e}"}, is_error=True)
    if not model_id:
        return _text({"error": "model_id 必填"}, is_error=True)
    if not isinstance(obj, dict) or not str(obj.get("key") or "").strip():
        return _text({"error": "object 必填且需含非空 key"}, is_error=True)
    try:
        _reject_datasource_arg(args)
        _assert_model_editable(model_id)   # 系统本体不可编辑（业务本体放行）
        _require_write_perm("ontology:save", "修改本体对象")
    except (ValueError, PermissionError) as e:
        return _text({"error": str(e)}, is_error=True)

    def _run():
        from backend.modules.catalog.services import ontology_service
        import copy
        model = ontology_service.get_model(model_id)
        if not model:
            raise ValueError("模型不存在")
        # 已有草案则基于草案合并（连续改多个对象不互相覆盖）
        base_json = ontology_service.get_draft(model_id).get("draft") or model.get("json_content") or "{}"
        doc = json.loads(base_json)
        if not isinstance(doc, dict):
            doc = {}
        objects = copy.deepcopy(doc.get("objects") or [])
        key = str(obj.get("key") or "").strip()
        idx = next((i for i, o in enumerate(objects)
                    if str(o.get("key") or "").strip().lower() == key.lower()), None)
        if idx is None:
            objects.append(obj)
            mode = "created"
        else:
            objects[idx] = obj
            mode = "updated"
        doc["objects"] = objects
        saved = ontology_service.save_draft_only(
            model_id, json.dumps(doc, ensure_ascii=False),
            created_by=str(getattr(ctx, "username", "") or ""))
        return {"mode": mode, "key": key, "model_id": saved["id"],
                "object_count": len(objects), "draft_pending": True,
                "note": f"对象「{key}」已{('新建' if mode == 'created' else '更新')}到草案，"
                        f"其余 {len(objects) - 1} 个对象未动；需 publish_ontology_model 发布才生效"}

    try:
        result = await asyncio.to_thread(_run)
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)
    return _text(result)


async def publish_ontology_model(args):
    """发布本体草案（生效 + 级联）— 权限码把关后直执行。

    **发布前必须先用 ask_user 向用户确认**（用户拍板：发布在 chat 中确认）。
    未带 confirmed=true 时本工具不会发布，只回提示，供你先去问用户。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    model_id = int(args.get("model_id") or 0)
    if not model_id:
        return _text({"error": "model_id 必填"}, is_error=True)
    if not args.get("confirmed"):
        return _text({
            "needs_user_confirmation": True,
            "model_id": model_id,
            "note": "发布会把草案提升为生效版并级联（重展开对象/重建图谱/同步知识库）。"
                    "请先用 ask_user 向用户确认，拿到同意后再带 confirmed=true 调用本工具",
        })
    try:
        _reject_datasource_arg(args)
        _assert_model_editable(model_id)   # 系统本体不可编辑（业务本体放行）
        _require_write_perm("ontology:publish", "发布本体草案")
    except (ValueError, PermissionError) as e:
        return _text({"error": str(e)}, is_error=True)

    def _run():
        from backend.modules.catalog.services import ontology_service
        return ontology_service.publish_draft(
            model_id, published_by=str(getattr(ctx, "username", "") or ""))

    try:
        result = await asyncio.to_thread(_run)
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)
    return _text({"success": True, "model_id": result.get("id"),
                  "object_count": result.get("object_count"),
                  "note": "已发布生效，并完成级联（对象表/图谱/知识库）"})


async def activate_ontology_model(args):
    """激活本体模型 — 菜单与功能权限码把关后直执行。"""
    model_id = int(args.get("model_id") or 0)
    if not model_id:
        return _text({"error": "model_id 必填"}, is_error=True)
    try:
        _assert_model_editable(model_id)   # 域约束：系统本体不可编辑（执行前拒）
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

    与 generate/save_ontology_draft 同源：目标由服务端按会话绑定注入，**不接受 LLM 传数据源标识**；
    业务源需「生成本体模型」任务会话绑定（权限码 ontology:import 把关，无码即拒）。
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


def _assert_model_editable(model_id: int) -> None:
    """AS-BOT 本体写操作域约束：目标模型**不得是系统本体**（kind='system'）。

    2026-10-27 拍板（反转旧「仅系统本体可写」硬禁令）：系统本体随项目版本发布、
    不由数据驱动，AS-BOT 不得编辑；**业务本体可编辑**（写权限由菜单与
    功能权限码在 handler 内把关，生效由草案/发布两段式控制）。
    源本体已退役（kind='source' 合并为 'business'），域判别只剩 system 与非 system；
    用 **kind** 判，不用 datasource_id（业务本体的 datasource_id 也可能为 0）。
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
    if kind == "system":
        raise ValueError(
            f"系统本体「{row.get('name')}」随项目版本发布、不由数据驱动，不支持通过 AS-BOT 编辑；"
            f"AS-BOT 可编辑业务本体")


# 指标/维度字典写工具（权限码 ontology:save 把关，直执行）
#
# 字典是指标口径的唯一权威（建模规范 §3），与本体 doc 是两个写入对象：
#   * 本体写工具受“系统本体不可编辑”域约束（_assert_model_editable：kind='system' 拒，
#     业务本体放行，2026-10-27 拍板；源本体退役后非 system 即业务）；
#   * 字典写工具不受该约束，但 bound_object_key 必须指向当前生效对象（服层
#     _require_object_keys fail-closed），悬空即拒；
#   * 只落库 + 审计，图谱/上云级联由用户在建模页显式触发（受 graph:manage/kb:manage）。

def _dict_payload(args: dict, ctx) -> dict:
    """字典行公共字段；归属与审计字段一律服务端注入，不接受 LLM 传值。"""
    aliases = args.get("aliases")
    if isinstance(aliases, str):
        try:
            aliases = json.loads(aliases)
        except ValueError:
            aliases = [s.strip() for s in aliases.split(",") if s.strip()]
    return {
        "display_name": str(args.get("display_name") or "").strip(),
        "bound_object_key": str(args.get("bound_object_key") or "").strip(),
        "description": str(args.get("description") or "").strip(),
        "aliases": aliases if isinstance(aliases, list) else [],
        # 字典行是跨数据源/跨工作空间口径：datasource_id 与 workspace_id 恒为 0，
        # 物理/业务归属由 bound_object_key 决定（不得从会话空间继承，否则同一字典会被空间碎片化）
        "datasource_id": 0,
        "workspace_id": 0,
        "owner": str(getattr(ctx, "username", "") or ""),   # 审计：谁写的，服务端注入
    }


def _dict_row(table: str, row_id: int) -> dict:
    """回读落库结果（只取业务字段），用于把“是否已绑到单一主表”显式告知调用方。"""
    from backend.common.db.metadata_db import get_metadata_conn

    cols = ("name, name_en, target_table, target_column, agg_type, bound_object_key"
            if table == "adh_metrics" else
            "name, name_en, target_table, target_column, category, bound_object_key")
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {cols} FROM {table} WHERE id = %s", (int(row_id),))
            return dict(cur.fetchone() or {})
    finally:
        conn.close()


async def save_metric(args):
    """创建/更新指标字典行（adh_metrics）—— 有 id 则更新，无 id 则创建。"""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        _reject_datasource_arg(args)
        _require_write_perm("ontology:save", "保存指标字典")
    except (ValueError, PermissionError) as e:
        return _text({"error": str(e)}, is_error=True)

    payload = _dict_payload(args, ctx)
    payload["name"] = str(args.get("name") or "").strip()          # 英文标识 → name_en
    payload["formula"] = str(args.get("formula") or "").strip()
    payload["agg_type"] = str(args.get("agg_type") or "").strip().lower()
    payload["unit"] = str(args.get("unit") or "").strip()
    payload["category"] = str(args.get("category") or "").strip() or "业务"
    # 口径标签（基础/衍生/复合等）与业务域分类是两个维度：category 只存业务域，
    # 标签落 tags 列，不得再往 category 里写 basic/derived/composite。
    tags = args.get("tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = [s.strip() for s in tags.split(",") if s.strip()]
    payload["tags"] = [str(t).strip() for t in tags if str(t).strip()] if isinstance(tags, list) else []
    missing = [k for k in ("display_name", "bound_object_key", "formula", "agg_type")
               if not payload.get(k)]
    if missing:
        return _text({"error": "指标必填项缺失: " + ", ".join(missing)
                      + "（口径唯一登记在字典，formula 与 agg_type 不可省）"}, is_error=True)

    metric_id = int(args.get("id") or 0)
    try:
        payload["is_active"], publish_note = _dict_publish_decision(args, ctx, "指标")
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)

    def _run():
        from backend.modules.catalog.services import metrics_service
        if metric_id:
            metrics_service.update_metric(metric_id, payload)
            return metric_id
        return int(metrics_service.create_metric(payload)["id"])

    try:
        row_id = await asyncio.to_thread(_run)
        row = await asyncio.to_thread(_dict_row, "adh_metrics", row_id)
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)

    out = {"success": True, "id": row_id, "metric": row.get("name"),
           "is_active": bool(payload["is_active"]),
           "target_table_bound": bool(row.get("target_table"))}
    if not out["target_table_bound"]:
        out["warning"] = ("该指标未能自动绑定到归属对象的单一主表（formula 不是合法单表聚合或"
                          "对象主表不唯一），语义层可能无法直接编译；请在建模页核对口径。")
    out["note"] = publish_note + "；图谱与云端知识库需用户在建模页显式点「同步图谱/同步知识库」才会更新"
    return _text(out)


async def save_dimension(args):
    """创建/更新维度字典行（adh_dimensions）—— 物理列由服务端按属性**业务名**解析。"""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    try:
        _reject_datasource_arg(args)
        _require_write_perm("ontology:save", "保存维度字典")
    except (ValueError, PermissionError) as e:
        return _text({"error": str(e)}, is_error=True)

    payload = _dict_payload(args, ctx)
    payload["name_en"] = str(args.get("name_en") or "").strip()
    payload["category"] = str(args.get("category") or "").strip() or "属性"
    prop_name = str(args.get("property_name") or "").strip()
    value_labels = args.get("value_labels")
    if isinstance(value_labels, str):
        try:
            value_labels = json.loads(value_labels)
        except ValueError:
            return _text({"error": "value_labels 不是合法 JSON 对象"}, is_error=True)
    payload["value_labels"] = value_labels if isinstance(value_labels, dict) else None

    missing = [k for k in ("display_name", "bound_object_key") if not payload.get(k)]
    if missing:
        return _text({"error": "维度必填项缺失: " + ", ".join(missing)}, is_error=True)
    if not prop_name:
        # 不传 property_name 会落成无物理列的空维度，取数时永远解析不出列（静默失效）。
        # 维度必须含 target_column（ontology-modeling §3），故直接拒。
        return _text({"error": "必须传 property_name（对象属性的业务中文名，如 创建时间）："
                                "维度靠它解析物理列，不传会落成取数不可用的空维度"}, is_error=True)
    try:
        payload["is_active"], publish_note = _dict_publish_decision(args, ctx, "维度")
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)

    resolve_note: dict = {}

    def _resolve_and_run():
        from backend.modules.catalog.services import metrics_service
        if prop_name:
            got = metrics_service.resolve_object_property(payload["bound_object_key"], prop_name)
            if not got.get("matched"):
                if got.get("reason") == "property_column_not_found":
                    # 属性名命中但本体登记的物理列在对象绑定表上不存在（建模字段错配）：
                    # 必须提醒用户回炉，不能退而猜一个列（宁缺勿错）
                    raise ValueError(
                        f"属性「{prop_name}」在对象「{payload['bound_object_key']}」的本体定义里"
                        "登记的物理列，与其当前绑定表的实存列不一致（建模字段错配），"
                        "因此不会写入维度。请先在本体建模页修正该属性的物理列映射后再建维度。")
                # 宁缺勿错：名字对不上就回抛该对象的可用属性业务名，绝不模糊绑一个列
                raise ValueError(
                    f"在对象「{payload['bound_object_key']}」上找不到属性「{prop_name}」；"
                    f"可用属性业务名: {(got.get('available') or [])[:30] or '（该对象无已登记属性）'}"
                    + "；若属性确实缺失，请先在本体模型中补该属性再建维度")
            payload["target_table"] = got["table"]
            payload["target_column"] = got["column"]
            if not got.get("verified"):
                # 列元数据拿不到（如跳站部署目标源未同步）时不假装"已对着元数据校验"
                resolve_note["unverified"] = (
                    "提示：目标源列元数据未同步，物理列未经验证（仅按对象绑定表推导），"
                    "建议同步该数据源元数据后核对维度列。")
        dim_id = int(args.get("id") or 0)
        if dim_id:
            metrics_service.update_dimension(dim_id, payload)
            return dim_id
        return int(metrics_service.create_dimension(payload)["id"])

    try:
        row_id = await asyncio.to_thread(_resolve_and_run)
        row = await asyncio.to_thread(_dict_row, "adh_dimensions", row_id)
    except ValueError as e:
        return _text({"error": str(e)}, is_error=True)

    return _text({"success": True, "id": row_id, "dimension": row.get("name"),
                  "is_active": bool(payload["is_active"]),
                  "bound_column": bool(row.get("target_column")),
                  "note": publish_note + "；图谱与云端知识库需用户在建模页显式点「同步图谱/同步知识库」才会更新"
                          + (("；" + resolve_note["unverified"])
                             if resolve_note.get("unverified") else "")})


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
    {
        "name": "add_ontology_objects",
        "description": (
            "在本体模型中**新增对象**（只增不删，落草案）。传 model_id 与 objects 数组"
            "（每项含 key/display_name/aliases/description/properties）。"
            "已存在的同 key 对象不会被覆盖（只增不删，修改请用 save_ontology_model）。"
            "改动只存**草案**，需 publish_ontology_model 发布后才生效并级联。"
            "不接受数据源参数；物理列映射不由你手敲，由服务端对着元数据生成/校验。"
        ),
        "schema": {
            "model_id": Annotated[int, "目标本体模型 ID"],
            "objects": Annotated[list, "要新增的对象数组（每项含 key/display_name/properties 等）"],
        },
        "handler": add_ontology_objects,
        "readonly": False,
    },
    {
        "name": "update_ontology_object",
        "description": (
            "按 key **新增或覆盖单个本体对象**（服务端合并，只动这一个对象）。"
            "传 model_id 与 object（含 key/display_name/aliases/description/properties/links）。"
            "**这是修改已有对象的首选工具**：不需要你持有模型的完整 JSON，"
            "其余对象由服务端原样保留，不会抹掉它们的属性与关系。"
            "改动只存**草案**，需 publish_ontology_model 发布后才生效。不接受数据源参数。"
        ),
        "schema": {
            "model_id": Annotated[int, "目标本体模型 ID"],
            "object": Annotated[dict, "要新建/覆盖的对象定义（必含 key）"],
        },
        "handler": update_ontology_object,
        "readonly": False,
    },
    {
        "name": "publish_ontology_model",
        "description": (
            "发布本体草案（生效 + 级联：重展开对象/重建图谱/同步知识库）。"
            "**发布前必须先用 ask_user 向用户确认**：不带 confirmed=true 调用只会返回"
            "确认提示，拿到用户同意后再带 confirmed=true 重新调用。"
            "需要 ontology:publish 权限。生效版在草案写入后被他人改动时会拒发布（不覆盖他人工作）。"
        ),
        "schema": {
            "model_id": Annotated[int, "目标本体模型 ID"],
            "confirmed": Annotated[Optional[bool], "是否已通过 ask_user 得到用户确认；未确认时传 false/省略，不会发布"],
        },
        "handler": publish_ontology_model,
        "readonly": False,
    },
    {
        "name": "save_metric",
        "description": (
            "创建/更新**指标字典行**（adh_metrics，指标口径的唯一权威）。传 id 则更新，不传则创建。"
            "必填：display_name(业务名)、bound_object_key(归属对象，必须是已激活对象)、"
            "formula(聚合口径，列名只能来自已查到的表结构/对象属性，不得臆造)、"
            "agg_type(sum/count/avg/min/max/custom)。可选：name(英文标识)、unit、description、"
            "aliases、category(业务域分类，如 可观测/客户/案例，默认 业务)、tags(口径标签，如 基础/衍生/复合)。"
            "不接受数据源参数（字典是跨源口径，归属由对象决定）。"
            "category 与 tags 是两个维度：业务域写进 category，基础/衍生/复合这类标签写进 tags，互不混用。"
            "只落库+审计；图谱与云端知识库的级联由用户在建模页显式触发。"
        ),
        "schema": {
            "display_name": Annotated[str, "指标业务名（如 案例数量）"],
            "bound_object_key": Annotated[str, "归属对象 key（必须已激活，如 case）"],
            "formula": Annotated[str, "聚合口径（如 COUNT(pc_case.id)），列名需来自工具已返回的结构"],
            "agg_type": Annotated[str, "sum/count/count_distinct/avg/min/max/custom"],
            "id": Annotated[Optional[int], "更新已有指标时传其 id；新增不传"],
            "name": Annotated[Optional[str], "英文标识（如 case_count）"],
            "unit": Annotated[Optional[str], "单位（例/人/元）"],
            "description": Annotated[Optional[str], "口径文字说明"],
            "aliases": Annotated[Optional[list], "别名清单（同义词）"],
            "category": Annotated[Optional[str], "业务域分类（如 可观测/客户/案例；默认 业务）"],
            "tags": Annotated[Optional[list], "口径标签（如 基础/衍生/复合；与业务域分类正交）"],
            "publish": Annotated[Optional[bool], "true=立即发布生效（需 ontology:publish）；省略/false=只落库为未发布，不参与语义检索与查询"],
        },
        "handler": save_metric,
        "readonly": False,
    },
    {
        "name": "save_dimension",
        "description": (
            "创建/更新**维度字典行**（adh_dimensions）。传 id 则更新，不传则创建。"
            "必填：display_name(业务名)、bound_object_key(归属对象)；"
            "强烈建议传 property_name：对象属性的**业务中文名**（如 创建时间），"
            "服务端会从本体属性解析出物理列（物理列不得手敲；名字对不上会回抛该对象可用属性名让你选）。"
            "可选：category(时间/属性/状态等)、name_en、aliases、value_labels(枚举 码→业务标签)、description。"
            "不接受数据源参数；图谱/知识库级联由用户在建模页显式触发。"
        ),
        "schema": {
            "display_name": Annotated[str, "维度业务名（如 注册日期）"],
            "bound_object_key": Annotated[str, "归属对象 key（必须已激活，如 user）"],
            "property_name": Annotated[Optional[str], "对象属性的业务中文名（如 创建时间）——服务端据此解析物理列"],
            "id": Annotated[Optional[int], "更新已有维度时传其 id；新增不传"],
            "category": Annotated[Optional[str], "分类：时间/属性/状态等（默认 属性）"],
            "name_en": Annotated[Optional[str], "英文标识"],
            "aliases": Annotated[Optional[list], "别名清单（同义词）"],
            "value_labels": Annotated[Optional[dict], "枚举码→业务标签（与本体 enum / 字典 value_labels 同构）"],
            "description": Annotated[Optional[str], "维度说明"],
            "publish": Annotated[Optional[bool], "true=立即发布生效（需 ontology:publish）；省略/false=只落库为未发布，不参与语义检索与查询"],
        },
        "handler": save_dimension,
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
