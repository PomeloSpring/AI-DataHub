"""Multimodal processing package — 聊天附件(图片/表格/文档/3D模型)解析与注入.

附件即会话工作区文件(workspace/uploads/ 下,随会话清理),无独立附件存储与元数据表:
- 发送接口上传校验与落盘(EXT_CATEGORY_MAP / 上传约束 / loader.write_upload_file)
- 工作区内文件解析与多模态 content blocks 构建(loader)
- 表格/PDF/文档解析(table_parser / doc_parser)
- OpenCV 图像分析工具(opencv_tools),派生图写回同一工作区
"""

# 扩展名 → 附件类别映射
EXT_CATEGORY_MAP = {
    # 图片(Vision)
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".webp": "image",
    # 表格文件
    ".csv": "table", ".xlsx": "table",
    # 文档
    ".pdf": "document", ".md": "document", ".txt": "document", ".docx": "document",
    # 3D 模型
    ".obj": "model3d", ".glb": "model3d", ".stl": "model3d",
}

# Anthropic image block 支持的 media type
IMAGE_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

CATEGORY_LABELS = {
    "image": "图片",
    "table": "表格文件",
    "document": "文档",
    "model3d": "3D模型",
}

# ── 上传约束(发送接口解析与落盘校验共用) ──
MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_FILES_PER_REQUEST = 5
ALLOWED_EXTENSIONS = set(EXT_CATEGORY_MAP)


def classify_extension(ext: str) -> str | None:
    """按扩展名返回附件类别;不支持的类型返回 None."""
    return EXT_CATEGORY_MAP.get(ext.lower())
