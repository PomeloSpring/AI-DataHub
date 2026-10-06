"""Ontology Modeling API — 本体模型生成/编辑/激活/检索。

挂载在 /api/catalog/ontology 前缀下:
    POST   /generate              SSE 流式生成本体草案
    GET    /models                模型列表
    GET    /models/{id}           模型详情（含三格式内容）
    PUT    /models/{id}           保存草案编辑（JSON 事实源）
    POST   /models/{id}/activate  激活并向量化
    POST   /models/{id}/archive   归档并下线对象向量
    DELETE /models/{id}           删除模型
    GET    /search                对象向量检索（调试/预览）
    POST   /import-yaml           导入 Palantir Ontology YAML 为 active 模型并入图
"""

import json
import logging
import queue
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..services import ontology_service
from services.shared.common.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _allowed_source_ids(user: dict) -> set:
    """用户可见数据源集（角色×数据源授权，唯一裁决点，fail-closed）。

    admin 同样纯角色裁决不 bypass（waker-datasource-domain §1）；
    空集=无任何源本体可见，不得当全量。
    get_current_user 返回 dict（{user_id, username, role}），不得按对象属性取值。
    """
    from services.authservice.services.role_service import role_service
    return set(role_service.get_user_allowed_datasources(int(user.get("user_id") or 0), 0))


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/generate")
def generate_ontology(req: dict):
    """LLM 生成本体草案（SSE 流式返回进度，最终事件携带 model_id）。"""
    datasource_id = int(req.get("datasource_id") or 0)
    if not datasource_id:
        raise HTTPException(status_code=400, detail="datasource_id 必填")

    q: queue.Queue = queue.Queue()

    def progress(stage: str, detail: str):
        q.put(("progress", {"stage": stage, "detail": detail}))

    def worker():
        try:
            model = ontology_service.generate_draft(
                datasource_id,
                progress_cb=progress,
                created_by=str(req.get("created_by") or ""),
            )
            q.put(("done", {"model_id": model["id"], "object_count": model["object_count"]}))
        except Exception as e:
            logger.error("ontology generate failed: %s", e)
            q.put(("error", {"message": str(e)}))
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            item = q.get()
            if item is None:
                break
            event, data = item
            yield _sse_event(event, data)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/generate-business-assets")
def generate_business_assets(user: dict = Depends(get_current_user)):
    """自动生成/刷新业务本体（数据资产路由图）。

    从数据源、数据集、同步任务、数据安全配置、数据质量、用户权限策略与血缘
    自动构建路由索引层（确定性生成，无 LLM）；落库走 save_draft/activate 级联。
    返回生成摘要与显式告警（治理数据未对齐项，不静默吞）。"""
    from ..services import business_asset_ontology
    try:
        return business_asset_ontology.generate_business_asset_ontology(
            created_by=str((user or {}).get("username") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("business asset ontology generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="业务本体自动生成未完成，请查看服务端日志") from e


@router.get("/models")
def list_models(
    datasource_id: int = Query(None, description="按数据源筛选"),
    include_archived: bool = Query(False, description="是否包含归档版本（默认排除，归档版本走版本管理）"),
    user: dict = Depends(get_current_user),
):
    # 可见性：源本体按用户数据源权限分配；业务/系统本体全员可见
    allowed = _allowed_source_ids(user)
    return {"items": ontology_service.list_models(
        datasource_id, include_archived=include_archived,
        allowed_datasource_ids=allowed)}


@router.get("/models/{model_id}")
def get_model(model_id: int, user: dict = Depends(get_current_user)):
    model = ontology_service.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    if not ontology_service.can_view_model(model, _allowed_source_ids(user)):
        # 不泄露存在性：不可见即 404（不回 403 提示“存在但无权”）
        raise HTTPException(status_code=404, detail="模型不存在")
    return model


@router.get("/models/{model_id}/versions")
def list_versions(model_id: int):
    """同一模型（datasource_id + name 归组）的全部版本，含归档；用于「版本管理」视图。"""
    try:
        return {"items": ontology_service.list_versions(model_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/models/{model_id}")
def save_model(model_id: int, req: dict):
    """保存草案编辑：提交 JSON 事实源，服务端重派生 YAML/MD。"""
    json_content = req.get("json_content")
    if not json_content:
        raise HTTPException(status_code=400, detail="json_content 必填")
    try:
        return ontology_service.save_draft(model_id, json_content, name=req.get("name"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/{model_id}/activate")
def activate_model(model_id: int):
    """激活模型：旧 active 归档，对象 MD 段向量化入向量库。"""
    try:
        return ontology_service.activate(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/{model_id}/archive")
def archive_model(model_id: int):
    try:
        return ontology_service.archive(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/{model_id}/restore")
def restore_model(model_id: int):
    """还原已归档模型为草案（归档数据可见可逆）。"""
    try:
        return ontology_service.restore(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/models/{model_id}/kb-sync")
def sync_model_kb(model_id: int):
    """手动把生效模型的脱敏语义文档同步到目标知识库（结果落水位线）。

    同步失败显式报错并写服务端日志，不静默吞成成功；成功/失败均落
    adh_ontology_kb_sync_state 供任务监控/徽章展示。
    """
    from ..services import ontology_kb_sync
    try:
        return ontology_kb_sync.sync_model_to_qmind(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("manual kb sync failed for model %s: %s", model_id, e, exc_info=True)
        raise HTTPException(status_code=502, detail="知识库同步未完成，请查看服务端日志") from e


@router.delete("/models/{model_id}")
def delete_model(model_id: int):
    return {"success": ontology_service.delete_model(model_id)}


@router.post("/import-yaml")
def import_yaml(req: dict):
    """导入 Palantir Ontology YAML（ontology/*.yaml）为 active 本体模型并重建图谱。

    body: {datasource_id: int, dir?: str, created_by?: str, rebuild_graph?: bool}
    """
    from ..services import ontology_yaml_import

    datasource_id = int(req.get("datasource_id") or 0)
    if not datasource_id:
        raise HTTPException(status_code=400, detail="datasource_id 必填")
    try:
        return ontology_yaml_import.import_palantir_yaml(
            dir_path=req.get("dir"),
            datasource_id=datasource_id,
            created_by=str(req.get("created_by") or ""),
            rebuild_graph=bool(req.get("rebuild_graph", True)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("ontology import-yaml failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/{model_id}/sync-status")
def model_sync_status(model_id: int):
    """模型的图谱/知识库同步状态(工作区头部徽章, 只读容错)。"""
    from ..services import ontology_kb_sync

    return ontology_kb_sync.model_sync_status(model_id)


@router.get("/kb-options")
def kb_options():
    """业务本体可选目标知识库清单(active qmind)。"""
    from ..services import ontology_kb_sync

    return {"items": ontology_kb_sync.list_bindable_kbs()}


@router.put("/models/{model_id}/kb")
def set_model_kb(model_id: int, req: dict):
    """为模型选定目标知识库(业务本体同步去向); 改绑后自动重推/下线旧库。"""
    try:
        return ontology_service.set_model_kb(model_id, req.get("kb_id"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/search")
def search_objects(
    q: str = Query(..., min_length=1, description="检索关键词"),
    datasource_id: int = Query(0),
    limit: int = Query(5, ge=1, le=20),
):
    """对象向量检索（调试与前端预览）。"""
    hits = ontology_service.search_objects(q, datasource_id=datasource_id, limit=limit)
    return {
        "items": [
            {
                "object_key": h["object_key"],
                "display_name": h["display_name"],
                "aliases": h.get("aliases", ""),
                "description": h.get("description", ""),
                "distance": h.get("distance"),
                "object": h.get("object"),
            }
            for h in hits
        ]
    }
