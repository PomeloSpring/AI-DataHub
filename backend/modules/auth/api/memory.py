"""用户记忆管理 API — 固定槽位 + 浮动条目 CRUD（REST 页面用）。

与 AS-BOT memory 工具共用同一张 adh_user_memory 表与同一套校验规则。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from backend.common.auth import get_current_user

router = APIRouter()

# 固定槽位 / 浮动条目定义（与 memory_tools.py 同源）
FIXED_SLOTS = {
    "business_domain": "业务域",
    "preferred_chart": "默认图表",
    "default_time_grain": "时间粒度",
    "output_format": "输出格式",
    "number_precision": "数字精度",
    "role_context": "角色背景",
}
FLOATING_CATEGORIES = {
    "correction": "口径纠错",
    "workflow": "工作习惯",
}
FLOATING_LIMIT = 5
CONTENT_MAX = 300

import re
_SESSION_STATE_PATTERN = re.compile(
    r"(正在|上次|刚才|刚刚|前面|之前那个|当前会话|这个对话|上一轮|前面那)", re.IGNORECASE
)


class MemoryUpdateRequest(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("记忆内容不能为空")
        if len(v) > CONTENT_MAX:
            raise ValueError(f"记忆内容超过 {CONTENT_MAX} 字符上限")
        if _SESSION_STATE_PATTERN.search(v):
            raise ValueError("禁止记录会话状态/临时上下文，只能记稳定事实（偏好/口径/习惯）")
        return v


class MemoryCreateRequest(BaseModel):
    slot_key: str
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        return MemoryUpdateRequest.validate_content(v)


@router.get("/")
def list_memory(user: dict = Depends(get_current_user)):
    """列出当前用户的全部记忆（固定槽位 + 浮动条目）。"""
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, slot_key, content, source, created_at, updated_at "
                "FROM adh_user_memory WHERE user_id=%s ORDER BY slot_key",
                (int(user["user_id"]),))
            rows = cur.fetchall()
    finally:
        conn.close()
    fixed = []
    floating = []
    for r in rows:
        item = {
            "id": r["id"], "slot_key": r["slot_key"], "content": r["content"],
            "source": r["source"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
        }
        if r["slot_key"] in FIXED_SLOTS:
            item["label"] = FIXED_SLOTS[r["slot_key"]]
            fixed.append(item)
        elif r["slot_key"] in FLOATING_CATEGORIES:
            item["label"] = FLOATING_CATEGORIES[r["slot_key"]]
            floating.append(item)
    return {
        "fixed_slots": fixed,
        "floating_entries": floating,
        "fixed_slot_defs": [{"key": k, "label": v} for k, v in FIXED_SLOTS.items()],
        "floating_defs": [{"key": k, "label": v} for k, v in FLOATING_CATEGORIES.items()],
        "floating_limit": FLOATING_LIMIT,
        "content_max": CONTENT_MAX,
    }


@router.post("/")
def create_memory(req: MemoryCreateRequest, user: dict = Depends(get_current_user)):
    """新增/更新记忆。固定槽位 upsert 覆盖，浮动条目限量。"""
    uid = int(user["user_id"])
    slot_key = req.slot_key.strip()
    if slot_key not in FIXED_SLOTS and slot_key not in FLOATING_CATEGORIES:
        raise HTTPException(400, f"未知 slot_key: {slot_key}")
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            if slot_key in FLOATING_CATEGORIES:
                cur.execute(
                    "SELECT COUNT(*) AS c FROM adh_user_memory WHERE user_id=%s AND slot_key IN ({})".format(
                        ",".join(["%s"] * len(FLOATING_CATEGORIES))),
                    (uid, *FLOATING_CATEGORIES.keys()))
                row = cur.fetchone()
                count = int((row or {}).get("c") or 0)
                if count >= FLOATING_LIMIT:
                    raise HTTPException(409, f"浮动条目已达上限 {FLOATING_LIMIT} 条，请先删除后再保存")
                cur.execute(
                    "INSERT INTO adh_user_memory (user_id, slot_key, content, source) VALUES (%s,%s,%s,'manual')",
                    (uid, slot_key, req.content))
            else:
                cur.execute(
                    "INSERT INTO adh_user_memory (user_id, slot_key, content, source) VALUES (%s,%s,%s,'manual') "
                    "ON DUPLICATE KEY UPDATE content=VALUES(content), source=VALUES(source), updated_at=NOW()",
                    (uid, slot_key, req.content))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "slot_key": slot_key, "content": req.content}


@router.put("/{memory_id}")
def update_memory(memory_id: int, req: MemoryUpdateRequest, user: dict = Depends(get_current_user)):
    """更新指定记忆内容（只能改自己的）。"""
    uid = int(user["user_id"])
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adh_user_memory SET content=%s, source='manual' WHERE id=%s AND user_id=%s",
                (req.content, memory_id, uid))
            if cur.rowcount == 0:
                raise HTTPException(404, "记忆不存在或无权修改")
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "id": memory_id}


@router.delete("/{memory_id}")
def delete_memory(memory_id: int, user: dict = Depends(get_current_user)):
    """删除指定记忆（只能删自己的）。"""
    uid = int(user["user_id"])
    from backend.common.db.metadata_db import get_metadata_conn
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_user_memory WHERE id=%s AND user_id=%s", (memory_id, uid))
            if cur.rowcount == 0:
                raise HTTPException(404, "记忆不存在或无权删除")
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "id": memory_id}
