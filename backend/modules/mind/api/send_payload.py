"""发送请求双模解析 — JSON(无附件)与 multipart/form-data(附件随消息上传).

附件即会话工作区文件:随消息以 files 字段上传,服务端在会话目录就绪后落盘
workspace/uploads/,不再有附件 ID 与附件元数据表。

- application/json: 走既有 Pydantic 模型(AS-BOT/无附件请求),不得携带 attachments;
- multipart/form-data: `payload` 字段为 JSON 字符串(同模型字段),
  `files` 字段为附件文件,校验后以 [{filename, category, size, content}] 挂到 .attachments。

聊天框 `@` 引用的既有文件走 `cloud_files` 字段(JSON/multipart 都可带)，在这里统一
展开成 attachments 的第三种形态（见 _apply_cloud_refs），下游 place_attachments 一处消费。
"""
import json
import logging
import os

from fastapi import HTTPException, Request
from pydantic import ValidationError

from backend.modules.mind.multimodal import (
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


def _apply_cloud_refs(params, user_id: int) -> None:
    """把 `@` 选中的文件引用展开成附件描述符，追加进 .attachments（与上传附件同链路）。

    口径（产品决策 2026-11-04）：`@` 的候选只有**归档资产**（账号级、跨工作空间）与
    **本会话工作区既有文件**。自动上云的副本是备份层，不作为可共享清单摊给用户 ——
    归档才是"我决定留着、可以跨会话复用"的资产动作。

    展开形态：
      - source=asset   → {"cloud_asset_id": id, "filename": ...}，由 place_attachments
        从对象存储取回内容并落盘当前会话 uploads/（属主校验在 SQL 条件里，拿不到即拒）;
      - source=session → {"path": rel}，复用既有工作区文件校验（防穿越由 resolve 守卫）。
    校验前置在这里而不是等执行层报错：用户点的是刚搜出来的清单项，取不到/超限必须
    当场看见（400/404 带文件名），不能变成"发出去了但 Agent 说它没这个文件"。
    """
    refs = list(getattr(params, "cloud_files", None) or [])
    if not refs:
        return
    if not user_id or int(user_id) <= 0:
        raise HTTPException(status_code=401, detail="缺少可信身份，无法引用云空间文件")
    from backend.modules.mind.api.user_assets import find_asset
    from backend.modules.mind.multimodal.loader import normalize_rel_path

    out = list(params.attachments or [])
    for item in refs:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="`@` 引用格式无效，请重新选择文件")
        source = (item.get("source") or "").strip()
        ref = str(item.get("ref") or "").strip()
        if not ref:
            raise HTTPException(status_code=400, detail="`@` 引用缺少文件标识，请重新选择")
        if source == "asset":
            row = find_asset(ref, int(user_id))
            if not row:
                raise HTTPException(status_code=404,
                                    detail=f"归档资产已不存在或无权访问，请重新 @ 选择: {ref}")
            if int(row.get("size") or 0) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"「{row['filename']}」超过附件大小限制(20MB)，无法作为本轮上下文")
            out.append({"cloud_asset_id": ref, "filename": row["filename"]})
        elif source == "session":
            rel = normalize_rel_path(ref)
            if not rel:
                raise HTTPException(status_code=400, detail=f"`@` 引用路径无效: {ref}")
            out.append({"path": rel})
        else:
            raise HTTPException(status_code=400,
                                detail=f"未知的 @ 引用来源「{source}」，请重新在输入框选择文件")
    if len(out) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"单轮最多携带 {MAX_FILES_PER_REQUEST} 个附件（含 @ 引用的文件）")
    params.attachments = out


def _build_params(model_cls, payload: dict):
    try:
        return model_cls.model_validate(payload)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"请求参数无效: {e.errors()[0].get('msg', '')}") from None


async def parse_send_request(request: Request, model_cls, user_id: int = 0):
    """按 content-type 解析发送请求,返回模型实例(.attachments 携带上传文件描述).

    user_id 来自服务端已解析的可信身份（不取自请求体），用于 `@` 引用的属主校验。
    """
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
        _apply_cloud_refs(params, user_id)
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
    params = _build_params(model_cls, body)
    # `@` 引用的是"已在云空间/工作区里的文件"，不需要上传字节，因此 JSON 形态合法
    _apply_cloud_refs(params, user_id)
    return params
