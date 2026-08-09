"""Table file parser — CSV/Excel 解析为结构摘要文本.

解析结果用于注入 LLM 上下文(列结构 + 前 N 行预览),
不建临时数据库表。
"""

import logging

logger = logging.getLogger(__name__)

# 预览行数与文本截断上限
PREVIEW_ROWS = 20
MAX_PREVIEW_CHARS = 6000


def parse_table_file(path: str, filename: str) -> dict:
    """解析 CSV/XLSX 文件,返回结构化摘要.

    Returns:
        {
            "columns": [{"name": str, "dtype": str}],
            "row_count": int,
            "preview_text": str,  # Markdown 风格摘要,可直接注入 LLM
        }
    """
    import pandas as pd

    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    try:
        if ext == "csv":
            df = pd.read_csv(path, nrows=500)
        elif ext in ("xlsx", "xls"):
            df = pd.read_excel(path, nrows=500)
        else:
            raise ValueError(f"不支持的表格文件类型: .{ext}")
    except Exception as e:
        logger.warning("Parse table file failed (%s): %s", filename, e)
        return {
            "columns": [],
            "row_count": 0,
            "preview_text": f"[表格文件 {filename} 解析失败: {e}]",
            "error": str(e),
        }

    columns = [{"name": str(c), "dtype": str(df[c].dtype)} for c in df.columns]
    preview = df.head(PREVIEW_ROWS).to_string(max_colwidth=32)
    preview_text = (
        f"表格文件 {filename}: 共 {len(df)} 行(最多读取500行), {len(df.columns)} 列\n"
        f"列结构: {', '.join(c['name'] + '(' + c['dtype'] + ')' for c in columns)}\n"
        f"前 {min(PREVIEW_ROWS, len(df))} 行数据:\n{preview}"
    )
    if len(preview_text) > MAX_PREVIEW_CHARS:
        preview_text = preview_text[:MAX_PREVIEW_CHARS] + "\n…(已截断)"

    return {
        "columns": columns,
        "row_count": int(len(df)),
        "preview_text": preview_text,
    }
