"""Attachment loader — 附件元数据加载与多模态 content blocks 构建.

将 adh_chat_attachments 记录转换为可注入 Anthropic messages 的
content blocks(图片 base64 block / 表格与文档解析文本 block)。

存储后端透明: 通过 ObjectStorage 抽象层,自动适配对象存储或本地磁盘。
"""

import base64
import json
import logging
import os
import tempfile
import uuid

logger = logging.getLogger(__name__)

# Anthropic 单张图片上限 5MB,超限自动压缩
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _get_storage():
    from services.shared.common.object_storage import get_object_storage
    return get_object_storage()


def _resolve_file_bytes(storage_path: str, storage_type: str = "local") -> bytes | None:
    """根据 storage_type 读取文件内容为字节,返回 None 表示文件不存在."""
    storage = _get_storage()
    if storage_type == "object" and storage.is_object_storage:
        return storage.download_bytes(storage_path)
    # 本地模式(或对象存储不可用时的回退)
    if os.path.exists(storage_path):
        with open(storage_path, "rb") as f:
            return f.read()
    # 尝试作为 object key 回退
    if storage.is_object_storage:
        return storage.download_bytes(storage_path)
    return None


def _resolve_local_path(storage_path: str, storage_type: str = "local") -> str | None:
    """获取本地可用的文件路径;对象存储时下载到临时文件并返回路径."""
    storage = _get_storage()
    if storage_type == "object" and storage.is_object_storage:
        data = storage.download_bytes(storage_path)
        if data is None:
            return None
        # 写入临时文件供解析器使用
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.basename(storage_path))
        tmp.write(data)
        tmp.close()
        return tmp.name
    if os.path.exists(storage_path):
        return storage_path
    return None


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
                "storage_path, storage_type, size, parsed_meta FROM adh_chat_attachments "
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
    from services.datamind.multimodal import classify_extension
    from services.shared.common.db.metadata_db import get_metadata_conn

    storage = _get_storage()
    storage_type = "object" if storage.is_object_storage else "local"

    att_id = uuid.uuid4().hex
    object_key = f"users/{source_att.get('user_id', 0)}/{att_id}_{filename}"

    # 上传到对象存储(或本地回退)
    storage.upload_bytes(object_key, img_bytes, content_type="image/png")

    storage_path = object_key if storage.is_object_storage else storage._local_path(object_key)

    ext = os.path.splitext(filename)[1].lower()
    category = classify_extension(ext) or "image"

    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_chat_attachments "
                "(id, user_id, workspace_id, filename, mime_type, category, "
                "storage_path, storage_type, size, parsed_meta, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                (
                    att_id, source_att.get("user_id", 0), source_att.get("workspace_id", 0),
                    filename, "", category, storage_path, storage_type, len(img_bytes),
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
        "storage_type": storage_type,
        "size": len(img_bytes),
    }


# ── 图片 block 构建 ──────────────────────────────────────────────

def _prepare_image_data(att: dict) -> tuple[str, str] | None:
    """读取图片并返回 (base64_data, media_type);超过 5MB 时渐进压缩."""
    from services.datamind.multimodal import IMAGE_MEDIA_TYPES

    storage_path = att.get("storage_path", "")
    storage_type = att.get("storage_type", "local")
    if not storage_path:
        return None

    data = _resolve_file_bytes(storage_path, storage_type)
    if data is None:
        return None

    ext = os.path.splitext(att.get("filename", storage_path))[1].lower()
    media_type = IMAGE_MEDIA_TYPES.get(ext, "image/jpeg")

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
        storage_path = att.get("storage_path", "")
        storage_type = att.get("storage_type", "local")

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
            meta = att.get("parsed_meta")
            if not meta or not meta.get("preview_text"):
                from services.datamind.multimodal.table_parser import parse_table_file
                local_path = _resolve_local_path(storage_path, storage_type)
                if local_path:
                    meta = parse_table_file(local_path, filename)
                    _update_parsed_meta(att["id"], meta)
                    # 清理临时文件
                    if storage_type == "object":
                        try:
                            os.unlink(local_path)
                        except OSError:
                            pass
                else:
                    meta = {"preview_text": f"[表格文件 {filename} 无法读取]"}
            blocks.append({"type": "text", "text": (meta or {}).get("preview_text", f"[表格文件 {filename} 解析为空]")})

        elif category == "document":
            meta = att.get("parsed_meta")
            if not meta or not meta.get("text"):
                from services.datamind.multimodal.doc_parser import extract_document_text
                local_path = _resolve_local_path(storage_path, storage_type)
                if local_path:
                    meta = extract_document_text(local_path, filename)
                    _update_parsed_meta(att["id"], meta)
                    # 清理临时文件
                    if storage_type == "object":
                        try:
                            os.unlink(local_path)
                        except OSError:
                            pass
                else:
                    meta = {"text": f"[文档文件 {filename} 无法读取]"}
            blocks.append({
                "type": "text",
                "text": f"[用户上传了文档附件: {filename}]\n{(meta or {}).get('text', '(文档内容为空)')}",
            })

        elif category == "model3d":
            blocks.append({
                "type": "text",
                "text": (
                    f"[用户上传了3D模型附件: {filename}, attachment_id={att.get('id', '')},"
                    f"前端已提供 three.js 预览,如需分析文件内容可读取该文件]"
                ),
            })

    blocks.append({"type": "text", "text": question})
    return blocks
