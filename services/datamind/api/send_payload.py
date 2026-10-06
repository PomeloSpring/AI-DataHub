"""发送请求双模解析 — JSON(无附件)与 multipart/form-data(附件随消息上传).

附件即会话工作区文件:随消息以 files 字段上传,服务端在会话目录就绪后落盘
workspace/uploads/,不再有附件 ID 与附件元数据表。

- application/json: 走既有 Pydantic 模型(AS-BOT/无附件请求),不得携带 attachments;
- multipart/form-data: `payload` 字段为 JSON 字符串(同模型字段),
  `files` 字段为附件文件,校验后以 [{filename, category, size, content}] 挂到 .attachments。
"""
import json
import logging
import os

from fastapi import HTTPException, Request
from pydantic import ValidationError

from services.datamind.multimodal import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    MAX_FILES_PER_REQUEST,
    classify_extension,
)

logger = logging.getLogger(__name__)


def _validate_upload(filename: str, content: bytes) -> dict:
    """单个上传文件校验,返回附件描述;不合规抛 HTTPException(可诊断 400)."""
    name = os.path.basename(filename or "file")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext}。允许: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    if not content:
        raise HTTPException(status_code=400, detail=f"附件为空文件: {name}")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"文件过大(上限 20MB): {name}")
    return {
        "filename": name,
        "category": classify_extension(ext),
        "size": len(content),
        "content": content,
    }


def _build_params(model_cls, payload: dict):
    try:
        return model_cls.model_validate(payload)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"请求参数无效: {e.errors()[0].get('msg', '')}") from None


async def parse_send_request(request: Request, model_cls):
    """按 content-type 解析发送请求,返回模型实例(.attachments 携带上传文件描述)."""
    ctype = (request.headers.get("content-type") or "").lower()

    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        raw = form.get("payload")
        if not isinstance(raw, str) or not raw.strip():
            raise HTTPException(status_code=400, detail="multipart 请求缺少 payload(JSON 字符串)字段")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"payload 不是合法 JSON: {e}") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload 必须是 JSON 对象")
        params = _build_params(model_cls, payload)

        files = form.getlist("files")
        if len(files) > MAX_FILES_PER_REQUEST:
            raise HTTPException(
                status_code=400, detail=f"单次最多上传 {MAX_FILES_PER_REQUEST} 个文件")
        uploads = []
        for f in files:
            content = await f.read()
            uploads.append(_validate_upload(getattr(f, "filename", "") or "file", content))
        params.attachments = uploads
        return params

    # JSON:既有模型直读;附件必须随 multipart 的 files 字段上传
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=415, detail="请求必须为 application/json 或 multipart/form-data") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    if body.get("attachments"):
        raise HTTPException(
            status_code=400,
            detail="附件已不再使用附件 ID:请改用 multipart/form-data,以 files 字段随消息上传附件",
        )
    return _build_params(model_cls, body)
