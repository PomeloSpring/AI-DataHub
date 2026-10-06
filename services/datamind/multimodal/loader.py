"""Attachment loader — 会话工作区附件文件的落盘、解析与多模态 content blocks 构建.

附件即会话工作区文件(uploads/ 子目录下),无独立附件存储与元数据表:
- 随消息上传的文件经 write_upload_file 落盘,派生图经 save_derived_file 写回;
- 引用与解析一律经 resolve_workspace_file 在工作区内解析(防目录穿越/符号链接逃逸);
- 生命周期随会话目录清理,历史消息里以工作区相对路径引用。
"""

import base64
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Anthropic 单张图片上限 5MB,超限自动压缩
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# 附件落盘子目录(相对会话工作区)
UPLOADS_SUBDIR = "uploads"

# 会话文件引用允许的前导前缀(与 /api/chat/session-file 伺服口径一致)
_PATH_PREFIXES = ("/workspace/", "/workspace", "workspace/", "./", "/")


def normalize_rel_path(rel_path: str) -> str:
    """剥离 /workspace/ 等前缀,返回工作区相对路径(不含前导斜杠)."""
    rel = (rel_path or "").strip()
    for prefix in _PATH_PREFIXES:
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    return rel.lstrip("/")


def resolve_workspace_file(workspace_dir, rel_path) -> Path:
    """在会话工作区内解析文件路径,防目录穿越/符号链接逃逸.

    不做存在性检查(由调用方决定 404/报错文案);非法/缺失路径抛 ValueError。
    """
    ws_dir = Path(workspace_dir).resolve()
    rel = normalize_rel_path(rel_path)
    if not rel:
        raise ValueError("缺少文件路径")
    target = (ws_dir / rel).resolve()
    if target != ws_dir and ws_dir not in target.parents:
        raise ValueError("非法文件路径")
    # 逐级拒绝符号链接逃逸(与会话目录守卫一致)
    for p in [target, *target.parents]:
        if p == ws_dir:
            break
        if p.is_symlink():
            raise ValueError("非法文件路径")
    return target


def write_upload_file(workspace_dir, filename: str, data: bytes) -> dict:
    """把上传/派生文件写入会话工作区 uploads/,重名自动去重.

    返回 {filename, category, path(工作区相对), size};
    文件名非法/类型不支持抛 ValueError。写入沿用 O_EXCL|O_NOFOLLOW 安全落盘,
    root 下降权到 nobody(与会话工作区文件属主一致)。
    """
    from services.datamind.multimodal import classify_extension

    name = os.path.basename(filename or "").strip()
    if not name or name in (".", ".."):
        raise ValueError("附件文件名无效")
    ext = os.path.splitext(name)[1].lower()
    category = classify_extension(ext)
    if not category:
        raise ValueError(f"不支持的文件类型: {ext}")

    uploads = Path(workspace_dir) / UPLOADS_SUBDIR
    uploads.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.geteuid() == 0:
        os.chown(uploads, 65534, 65534)

    stem, suffix = os.path.splitext(name)
    fd = None
    for n in range(0, 1000):
        target_name = name if n == 0 else f"{stem}_{n}{suffix}"
        target = uploads / target_name
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            continue
        break
    else:
        raise ValueError("同名附件过多,请重命名后重试")

    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if os.geteuid() == 0:
        os.chown(target, 65534, 65534)
    return {
        "filename": target_name,
        "category": category,
        "path": f"{UPLOADS_SUBDIR}/{target_name}",
        "size": len(data),
    }


def save_derived_file(workspace_dir, img_bytes: bytes, filename: str) -> dict:
    """保存派生图像(OpenCV 处理产物)到同一会话工作区,返回附件描述 dict."""
    return write_upload_file(workspace_dir, filename, img_bytes)


# ── 图片 block 构建 ──────────────────────────────────────────────

def _prepare_image_data(att: dict, workspace_dir) -> tuple[str, str] | None:
    """读取工作区图片并返回 (base64_data, media_type);超过 5MB 时渐进压缩."""
    from services.datamind.multimodal import IMAGE_MEDIA_TYPES

    if not att.get("path"):
        return None
    try:
        target = resolve_workspace_file(workspace_dir, att["path"])
    except ValueError:
        return None
    if not target.is_file():
        return None
    data = target.read_bytes()

    ext = os.path.splitext(att.get("filename") or target.name)[1].lower()
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

def build_user_content(question: str, attachments: list[dict], workspace_dir) -> list | str:
    """构建用户消息 content:无附件返回字符串,有附件返回 content blocks 列表.

    attachments 元素契约 {filename, category, path},path 为工作区相对路径。
    - image: Vision 模型转 base64 image block,否则降级为 OpenCV 摘要文本
    - table: pandas 解析为列结构 + 预览文本(即时解析,不缓存)
    - document: 抽取文本(即时解析,不缓存)
    - model3d: 仅文本说明(渲染在前端)
    解析/读取失败显式写入块文案,不静默吞掉。
    """
    if not attachments:
        return question

    blocks: list[dict] = []
    for att in attachments:
        category = att.get("category", "")
        filename = att.get("filename", "")
        rel_path = att.get("path", "")

        if category == "image":
            image_block = None
            prepared = _prepare_image_data(att, workspace_dir)
            if prepared:
                b64, media_type = prepared
                image_block = {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                }
            if image_block:
                blocks.append({
                    "type": "text",
                    "text": f"[用户上传了图片附件: {filename}, 工作区路径: {rel_path}]",
                })
                blocks.append(image_block)
            else:
                # 图片读取失败/压缩超限 → OpenCV 摘要降级(显式标注降级事实)
                try:
                    from services.datamind.multimodal.opencv_tools import image_info
                    info = image_info(att, workspace_dir)
                    blocks.append({
                        "type": "text",
                        "text": f"[用户上传了图片附件: {filename}, 工作区路径: {rel_path}。"
                                f"图片未能注入(读取失败或超过大小限制),OpenCV 分析摘要: "
                                f"{_json_dump(info)}。可调用 image_info / image_process / detect_table_region 工具进一步处理]",
                    })
                except Exception as e:
                    blocks.append({"type": "text",
                                   "text": f"[用户上传了图片附件: {filename},但无法解析: {e}]"})

        elif category == "table":
            try:
                from services.datamind.multimodal.table_parser import parse_table_file
                target = resolve_workspace_file(workspace_dir, rel_path)
                meta = parse_table_file(str(target), filename)
            except Exception as e:
                meta = {"error": f"[表格文件 {filename} 解析失败: {e}]"}
            blocks.append({"type": "text",
                           "text": meta.get("preview_text") or meta.get("error")
                           or f"[表格文件 {filename} 解析为空]"})

        elif category == "document":
            try:
                from services.datamind.multimodal.doc_parser import extract_document_text
                target = resolve_workspace_file(workspace_dir, rel_path)
                meta = extract_document_text(str(target), filename)
            except Exception as e:
                meta = {"text": f"[文档文件 {filename} 解析失败: {e}]"}
            blocks.append({
                "type": "text",
                "text": f"[用户上传了文档附件: {filename}]\n{meta.get('text', '(文档内容为空)')}",
            })

        elif category == "model3d":
            blocks.append({
                "type": "text",
                "text": (
                    f"[用户上传了3D模型附件: {filename}, 工作区路径: {rel_path},"
                    f"前端已提供 three.js 预览,如需分析文件内容可读取该文件]"
                ),
            })

    blocks.append({"type": "text", "text": question})
    return blocks


def _json_dump(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)
