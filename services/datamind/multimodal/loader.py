"""Attachment loader — 附件元数据加载与多模态 content blocks 构建.

将 adh_chat_attachments 记录转换为可注入 Anthropic messages 的
content blocks(图片 base64 block / 表格与文档解析文本 block)。
"""

import base64
import json
import logging
import os
import uuid

logger = logging.getLogger(__name__)

# Anthropic 单张图片上限 5MB,超限自动压缩
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def load_attachments(att_ids: list[str], user_id: int = 0) -> list[dict]:
    """按 ID 批量加载附件元数据(校验归属).

    Args:
        att_ids: 附件 ID 列表
        user_id: 非 0 时仅返回该用户的附件
    """
    if not att_ids:
        return []
    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(att_ids))
            sql = (
                "SELECT id, user_id, workspace_id, filename, mime_type, category, "
                "storage_path, size, parsed_meta FROM adh_chat_attachments "
                f"WHERE id IN ({placeholders})"
            )
            params = list(att_ids)
            if user_id:
                sql += " AND user_id = %s"
                params.append(user_id)
            cur.execute(sql, params)
            rows = cur.fetchall() or []
    finally:
        conn.close()

    result = []
    for r in rows:
        meta = r.get("parsed_meta")
        if isinstance(meta, str) and meta:
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = None
        r["parsed_meta"] = meta
        result.append(r)
    return result


def _update_parsed_meta(att_id: str, meta: dict) -> None:
    from services.shared.common.db.metadata_db import get_metadata_conn

    try:
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_chat_attachments SET parsed_meta = %s WHERE id = %s",
                    (json.dumps(meta, ensure_ascii=False, default=str), att_id),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Update parsed_meta failed (%s): %s", att_id, e)


def save_derived_attachment(source_att: dict, img_bytes: bytes, filename: str, meta: dict = None) -> dict:
    """保存派生图像(OpenCV 处理产物)为新附件记录,返回附件行 dict."""
    from services.shared.common.config import ADH_UPLOAD_DIR

    att_id = uuid.uuid4().hex
    user_dir = os.path.join(ADH_UPLOAD_DIR, str(source_att.get("user_id", 0)))
    os.makedirs(user_dir, exist_ok=True)
    storage_path = os.path.join(user_dir, f"{att_id}_{filename}")
    with open(storage_path, "wb") as f:
        f.write(img_bytes)

    ext = os.path.splitext(filename)[1].lower()
    from services.datamind.multimodal import classify_extension
    category = classify_extension(ext) or "image"

    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_chat_attachments "
                "(id, user_id, workspace_id, filename, mime_type, category, storage_path, size, parsed_meta, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                (
                    att_id, source_att.get("user_id", 0), source_att.get("workspace_id", 0),
                    filename, "", category, storage_path, len(img_bytes),
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": att_id,
        "user_id": source_att.get("user_id", 0),
        "workspace_id": source_att.get("workspace_id", 0),
        "filename": filename,
        "category": category,
        "storage_path": storage_path,
        "size": len(img_bytes),
    }


# ── 图片 block 构建 ──────────────────────────────────────────────

def _prepare_image_data(att: dict) -> tuple[str, str] | None:
    """读取图片并返回 (base64_data, media_type);超过 5MB 时渐进压缩."""
    from services.datamind.multimodal import IMAGE_MEDIA_TYPES

    path = att.get("storage_path", "")
    if not path or not os.path.exists(path):
        return None
    ext = os.path.splitext(att.get("filename", path))[1].lower()
    media_type = IMAGE_MEDIA_TYPES.get(ext, "image/jpeg")

    with open(path, "rb") as f:
        data = f.read()
    if len(data) <= MAX_IMAGE_BYTES:
        return base64.b64encode(data).decode("ascii"), media_type

    # 超限:用 OpenCV 渐进压缩为 JPEG
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        scale = 0.75
        for _ in range(6):
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok and len(buf) <= MAX_IMAGE_BYTES:
                return base64.b64encode(buf.tobytes()).decode("ascii"), "image/jpeg"
        return None
    except Exception as e:
        logger.warning("Image compress failed (%s): %s", att.get("filename"), e)
        return None


# ── 多模态 content blocks ────────────────────────────────────────

def build_user_content(question: str, attachments: list[dict], supports_vision: bool = True):
    """构建用户消息 content:无附件返回字符串,有附件返回 content blocks 列表.

    - image: Vision 模型转 base64 image block,否则降级为 OpenCV 摘要文本
    - table: pandas 解析为列结构 + 预览文本
    - document: 抽取文本
    - model3d: 仅文本说明(渲染在前端)
    """
    if not attachments:
        return question

    blocks: list[dict] = []
    for att in attachments:
        category = att.get("category", "")
        filename = att.get("filename", "")

        if category == "image":
            image_block = None
            if supports_vision:
                prepared = _prepare_image_data(att)
                if prepared:
                    b64, media_type = prepared
                    image_block = {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    }
            if image_block:
                blocks.append({
                    "type": "text",
                    "text": f"[用户上传了图片附件: {filename}, attachment_id={att.get('id', '')}]",
                })
                blocks.append(image_block)
            else:
                # 模型不支持 Vision 或图片读取失败 → OpenCV 摘要降级
                try:
                    from services.datamind.multimodal.opencv_tools import image_info
                    info = image_info(att)
                    blocks.append({
                        "type": "text",
                        "text": f"[用户上传了图片附件: {filename}, attachment_id={att.get('id', '')}。当前模型不支持图片理解,OpenCV 分析摘要: {json.dumps(info, ensure_ascii=False)}。可调用 image_info / image_process / detect_table_region 工具进一步处理]",
                    })
                except Exception as e:
                    blocks.append({"type": "text", "text": f"[用户上传了图片附件: {filename},但无法解析: {e}]"})

        elif category == "table":
            meta = att.get("parsed_meta") or {}
            if not meta.get("preview_text"):
                from services.datamind.multimodal.table_parser import parse_table_file
                meta = parse_table_file(att.get("storage_path", ""), filename)
                _update_parsed_meta(att["id"], meta)
            blocks.append({"type": "text", "text": meta.get("preview_text", f"[表格文件 {filename} 解析为空]")})

        elif category == "document":
            meta = att.get("parsed_meta") or {}
            if not meta.get("text"):
                from services.datamind.multimodal.doc_parser import extract_document_text
                meta = extract_document_text(att.get("storage_path", ""), filename)
                _update_parsed_meta(att["id"], meta)
            blocks.append({
                "type": "text",
                "text": f"[用户上传了文档附件: {filename}]\n{meta.get('text', '(文档内容为空)')}",
            })

        elif category == "model3d":
            blocks.append({
                "type": "text",
                "text": (
                    f"[用户上传了3D模型附件: {filename}, attachment_id={att.get('id', '')},"
                    f"文件位于 {att.get('storage_path', '')}。"
                    f"前端已提供 three.js 预览,如需分析文件内容可读取该文件]"
                ),
            })

    blocks.append({"type": "text", "text": question})
    return blocks
