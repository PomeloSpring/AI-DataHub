"""Document parser — PDF/Markdown/文本/docx 抽取纯文本.

抽取结果截断后注入 LLM 上下文。docx 用 zipfile 直接解析
document.xml,避免引入额外依赖。
"""

import logging
import re

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 8000


def extract_document_text(path: str, filename: str) -> dict:
    """抽取文档文本,返回 {"text": str, "truncated": bool}."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    try:
        if ext == "pdf":
            text = _extract_pdf(path)
        elif ext == "docx":
            text = _extract_docx(path)
        else:
            # md / txt 直接读取
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
    except Exception as e:
        logger.warning("Extract document failed (%s): %s", filename, e)
        return {"text": f"[文档 {filename} 解析失败: {e}]", "truncated": False, "error": str(e)}

    text = text.strip()
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS] + "\n…(内容过长已截断)"
    return {"text": text, "truncated": truncated}


def _extract_pdf(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    parts = []
    for page in reader.pages[:50]:  # 最多抽取 50 页
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def _extract_docx(path: str) -> str:
    import zipfile

    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    # 段落分隔
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\n{3,}", "\n\n", text)
