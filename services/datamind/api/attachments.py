"""Chat Attachments API — 多模态附件上传与访问.

文件存储在本地磁盘(ADH_UPLOAD_DIR/{user_id}/),元数据存 adh_chat_attachments。
"""

import logging
import os
import uuid

import jwt
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.shared.common.auth import decode_token, get_current_user
from services.shared.models.schemas import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()

_bearer_optional = HTTPBearer(auto_error=False)


async def get_file_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_optional),
) -> UserInfo:
    """文件下载鉴权: 支持 Authorization header 或 ?token= 查询参数。

    查询参数回退用于 <img src> / three.js 等无法携带自定义 header 的场景。
    """
    token = credentials.credentials if credentials else request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token)
        return {
            "user_id": payload.get("user_id"),
            "username": payload.get("username"),
            "role": payload.get("role", "viewer"),
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# 单文件上限 20MB,单次最多 5 个文件
MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_FILES_PER_REQUEST = 5

ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp",       # 图片
    ".csv", ".xlsx",                                 # 表格
    ".pdf", ".md", ".txt", ".docx",                  # 文档
    ".obj", ".glb", ".stl",                          # 3D 模型
}


@router.post("/upload")
async def upload_attachments(
    files: list[UploadFile] = File(...),
    workspace_id: int = Form(0),
    user: UserInfo = Depends(get_current_user),
):
    """上传聊天附件(支持一次多个,最多 5 个).

    Returns:
        {"attachments": [{id, filename, category, size, url}, ...]}
    """
    from services.datamind.multimodal import classify_extension
    from services.shared.common.config import ADH_UPLOAD_DIR
    from services.shared.common.db.metadata_db import get_metadata_conn

    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"单次最多上传 {MAX_FILES_PER_REQUEST} 个文件")

    uploaded = []
    user_dir = os.path.join(ADH_UPLOAD_DIR, str(user["user_id"]))
    os.makedirs(user_dir, exist_ok=True)

    conn = get_metadata_conn()
    try:
        for file in files:
            filename = os.path.basename(file.filename or "file")
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的文件类型: {ext}。允许: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
                )
            category = classify_extension(ext)

            content = await file.read()
            if len(content) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"文件过大(上限 20MB): {filename}")

            att_id = uuid.uuid4().hex
            storage_path = os.path.join(user_dir, f"{att_id}_{filename}")
            with open(storage_path, "wb") as f:
                f.write(content)

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO adh_chat_attachments "
                        "(id, user_id, workspace_id, filename, mime_type, category, storage_path, size, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                        (
                            att_id, user["user_id"], workspace_id, filename,
                            file.content_type or "", category, storage_path, len(content),
                        ),
                    )
                conn.commit()
            except Exception as e:
                logger.error("Save attachment meta failed (%s): %s", filename, e)
                raise HTTPException(status_code=500, detail=f"附件保存失败: {filename}")

            uploaded.append({
                "id": att_id,
                "filename": filename,
                "category": category,
                "size": len(content),
                "url": f"/api/chat/attachments/{att_id}/file",
            })
    finally:
        conn.close()

    return {"attachments": uploaded}


@router.get("/{att_id}/file")
def get_attachment_file(
    att_id: str,
    user: UserInfo = Depends(get_file_user),
):
    """下载/预览附件文件(仅属主可访问)."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT filename, mime_type, storage_path FROM adh_chat_attachments "
                "WHERE id = %s AND user_id = %s",
                (att_id, user["user_id"]),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    if not os.path.exists(row["storage_path"]):
        raise HTTPException(status_code=404, detail="附件文件已丢失")

    return FileResponse(
        row["storage_path"],
        filename=row["filename"],
        media_type=row["mime_type"] or "application/octet-stream",
    )


@router.delete("/{att_id}")
def delete_attachment(
    att_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """删除附件(仅属主)."""
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT storage_path FROM adh_chat_attachments WHERE id = %s AND user_id = %s",
                (att_id, user["user_id"]),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="附件不存在")
            cur.execute("DELETE FROM adh_chat_attachments WHERE id = %s", (att_id,))
        conn.commit()
    finally:
        conn.close()

    try:
        if row["storage_path"] and os.path.exists(row["storage_path"]):
            os.remove(row["storage_path"])
    except OSError as e:
        logger.warning("Remove attachment file failed: %s", e)
    return {"success": True}
