"""Chat Attachments API — 多模态附件上传与访问.

文件存储后端:
- 对象存储(S3 兼容): storage_type='object', storage_path 存 object key
- 本地磁盘(回退): storage_type='local', storage_path 存绝对路径
通过 services.shared.common.object_storage 统一抽象.
"""

import logging
import os
import uuid

import jwt
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
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
    from services.shared.common.db.metadata_db import get_metadata_conn
    from services.shared.common.object_storage import get_object_storage

    storage = get_object_storage()
    storage_type = "object" if storage.is_object_storage else "local"

    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"单次最多上传 {MAX_FILES_PER_REQUEST} 个文件")

    uploaded = []
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
            # 对象存储 key 格式: users/{user_id}/{att_id}_{filename}
            object_key = f"users/{user['user_id']}/{att_id}_{filename}"

            # 上传到对象存储(或本地回退)
            storage.upload_bytes(object_key, content, content_type=file.content_type or "application/octet-stream")

            # storage_path: 对象存储存 key,本地存绝对路径(兼容旧逻辑)
            storage_path = object_key if storage.is_object_storage else storage._local_path(object_key)

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO adh_chat_attachments "
                        "(id, user_id, workspace_id, filename, mime_type, category, "
                        "storage_path, storage_type, size, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                        (
                            att_id, user["user_id"], workspace_id, filename,
                            file.content_type or "", category, storage_path,
                            storage_type, len(content),
                        ),
                    )
                conn.commit()
            except Exception as e:
                logger.error("Save attachment meta failed (%s): %s", filename, e)
                # 回滚已上传的文件
                storage.delete(object_key)
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
    from services.shared.common.object_storage import get_object_storage

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT filename, mime_type, storage_path, storage_type "
                "FROM adh_chat_attachments WHERE id = %s AND user_id = %s",
                (att_id, user["user_id"]),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")

    storage = get_object_storage()
    storage_type = row.get("storage_type", "local")
    media_type = row["mime_type"] or "application/octet-stream"

    if storage_type == "object" or storage.is_object_storage:
        # 对象存储模式: 优先返回预签名 URL 重定向,或流式下载
        object_key = row["storage_path"]
        if storage.is_object_storage:
            data = storage.download_bytes(object_key)
            if data is None:
                raise HTTPException(status_code=404, detail="附件文件已丢失")
            return Response(content=data, media_type=media_type,
                            headers={"Content-Disposition": f'inline; filename="{row["filename"]}"'})
        # 对象存储不可用但记录是 object 类型: 尝试本地回退
        local_path = storage._local_path(object_key)
        if not os.path.exists(local_path):
            raise HTTPException(status_code=404, detail="附件文件已丢失")
        return FileResponse(local_path, filename=row["filename"], media_type=media_type)
    else:
        # 本地模式(旧数据兼容)
        local_path = row["storage_path"]
        if not os.path.exists(local_path):
            raise HTTPException(status_code=404, detail="附件文件已丢失")
        return FileResponse(local_path, filename=row["filename"], media_type=media_type)


@router.delete("/{att_id}")
def delete_attachment(
    att_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """删除附件(仅属主)."""
    from services.shared.common.db.metadata_db import get_metadata_conn
    from services.shared.common.object_storage import get_object_storage

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT storage_path, storage_type FROM adh_chat_attachments "
                "WHERE id = %s AND user_id = %s",
                (att_id, user["user_id"]),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="附件不存在")
            cur.execute("DELETE FROM adh_chat_attachments WHERE id = %s", (att_id,))
        conn.commit()
    finally:
        conn.close()

    # 删除文件
    storage = get_object_storage()
    storage_type = row.get("storage_type", "local")
    if storage_type == "object" and storage.is_object_storage:
        storage.delete(row["storage_path"])
    elif storage_type == "local":
        try:
            if row["storage_path"] and os.path.exists(row["storage_path"]):
                os.remove(row["storage_path"])
        except OSError as e:
            logger.warning("Remove attachment file failed: %s", e)
    return {"success": True}
