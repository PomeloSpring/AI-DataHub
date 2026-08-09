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
"""

import json
import logging
import queue
import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..services import ontology_service

logger = logging.getLogger(__name__)
router = APIRouter()


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


@router.get("/models")
def list_models(datasource_id: int = Query(None, description="按数据源筛选")):
    return {"items": ontology_service.list_models(datasource_id)}


@router.get("/models/{model_id}")
def get_model(model_id: int):
    model = ontology_service.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    return model


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


@router.delete("/models/{model_id}")
def delete_model(model_id: int):
    return {"success": ontology_service.delete_model(model_id)}


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
