"""用户记忆管理 API — 固定槽位 + 浮动条目 CRUD（REST 页面用）。

存储与校验的**单一事实源**是 ``backend/core/memory_service.py``，与 AS-BOT 的
memory 工具组（save_memory/list_memory/delete_memory）走同一套规则：
REST 写入标 ``source='manual'``，对话内 AI 学习标 ``source='auto'``。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.common.auth import get_current_user
from backend.core import memory_service

router = APIRouter()

CONTENT_MAX = memory_service.CONTENT_MAX
FLOATING_LIMIT = memory_service.FLOATING_LIMIT


class MemoryUpdateRequest(BaseModel):
    content: str


class MemoryCreateRequest(BaseModel):
    slot_key: str
    content: str


# 内容合法性（非空/长度/禁会话上下文/禁查询结果）统一由 memory_service.validate_content
# 裁决并在 handler 里转成 HTTPException —— 不在 pydantic validator 里报错：
# 那样会返回 422 结构化 detail（数组），前端 toast 直接 detail 会渲染成 [object]。


def _http_error(value_error: ValueError) -> HTTPException:
    """服务层 ValueError → 可读 HTTP 错误（上限冲突 409，不存在/无权 404，其余 400）。"""
    msg = str(value_error)
    if "已达上限" in msg:
        return HTTPException(409, msg)
    if "不存在" in msg or "无权" in msg:
        return HTTPException(404, msg)
    return HTTPException(400, msg)


def _item(r: dict) -> dict:
    return {
        "id": r["id"], "slot_key": r["slot_key"], "content": r["content"],
        "source": r["source"], "entry_no": r.get("entry_no"),
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
    }


@router.get("/")
def list_memory(user: dict = Depends(get_current_user)):
    """列出当前用户的全部记忆（固定槽位 + 浮动条目）。"""
    rows = memory_service.list_entries(int(user["user_id"]))
    fixed, floating = memory_service.split_entries(rows)
    fixed_items = [dict(_item(r), label=memory_service.FIXED_SLOTS[r["slot_key"]]) for r in fixed]
    floating_items = [
        dict(_item(r), label=memory_service.FLOATING_CATEGORIES[r["slot_key"]]) for r in floating
    ]
    return {
        "fixed_slots": fixed_items,
        "floating_entries": floating_items,
        "fixed_slot_defs": [{"key": k, "label": v} for k, v in memory_service.FIXED_SLOTS.items()],
        "floating_defs": [{"key": k, "label": v} for k, v in memory_service.FLOATING_CATEGORIES.items()],
        "floating_limit": FLOATING_LIMIT,
        "content_max": CONTENT_MAX,
    }


@router.post("/")
def create_memory(req: MemoryCreateRequest, user: dict = Depends(get_current_user)):
    """新增/更新记忆。固定槽位 upsert 覆盖，浮动条目限量追加。"""
    try:
        result = memory_service.save_entry(
            int(user["user_id"]), req.slot_key, req.content, source="manual")
    except ValueError as exc:
        raise _http_error(exc) from exc
    return {"success": True, **result}


@router.put("/{memory_id}")
def update_memory(memory_id: int, req: MemoryUpdateRequest, user: dict = Depends(get_current_user)):
    """更新指定记忆内容（只能改自己的）。"""
    try:
        result = memory_service.update_entry(int(user["user_id"]), memory_id, req.content)
    except ValueError as exc:
        raise _http_error(exc) from exc
    return {"success": True, "id": result["id"]}


@router.delete("/{memory_id}")
def delete_memory(memory_id: int, user: dict = Depends(get_current_user)):
    """删除指定记忆（只能删自己的）。"""
    if not memory_service.delete_entry(int(user["user_id"]), memory_id):
        raise HTTPException(404, "记忆不存在或无权删除")
    return {"success": True, "id": memory_id}
