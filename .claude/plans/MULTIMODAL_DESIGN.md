# 多模态处理设计方案

## 一、总体架构设计

### 1.1 设计目标

在现有 AI-DataHub 平台上扩展多模态能力，包括：
- **数据源扩展**：新增文件系统（本地/NFS）、对象存储（OSS/S3/MinIO）作为数据源
- **文件处理**：支持图片（OpenCV）、文档（PDF/Excel/CSV）、音视频等文件的解析与索引
- **多模态 Chat**：AI Chat 支持图片/文件上传输入，LLM 根据能力返回文本/图片分析
- **能力检测**：自动检测接入 LLM 的多模态支持能力（vision、audio 等）

### 1.2 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (React)                         │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ Chat UI  │  │ File Upload  │  │ Multi-modal Result View  │  │
│  │ (文本+图片)│  │ (拖拽/选择)   │  │ (图片/表格/文档/音视频)    │  │
│  └──────────┘  └──────────────┘  └──────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                     API Layer (FastAPI)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ /chat/send   │  │ /files/upload│  │ /datasources (新增类型)│  │
│  │ (多模态消息)   │  │ (文件上传API) │  │ (FS/OSS 配置管理)     │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    Service Layer                                 │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────┐  │
│  │ MultimodalLLM  │  │ FileProcessor  │  │ FileDataSource   │  │
│  │ Service        │  │ Service        │  │ Service          │  │
│  │ (能力检测+消息构建)│ │ (解析/OCR/向量化)│  │ (FS/OSS 连接管理) │  │
│  └────────────────┘  └────────────────┘  └──────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                   Connector Layer                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ MySQL    │  │ Doris    │  │ ES       │  │ FileSystem   │   │
│  │Connector │  │Connector │  │Connector │  │ Connector    │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘   │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────────┐ │
│  │ OSS/S3   │  │ OpenCV   │  │ Document Parser              │ │
│  │ Connector│  │ Processor│  │ (PDF/Excel/CSV/Image)        │ │
│  └──────────┘  └──────────┘  └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## 二、数据源扩展设计

### 2.1 新增数据源类型

扩展 `DatasourceCreate` 模型，新增 `db_type` 枚举值：

| db_type | 说明 | 连接参数 |
|---------|------|---------|
| `mysql` | MySQL (已有) | host, port, username, password, database |
| `doris` | Apache Doris (已有) | host, port, username, password, database |
| `elasticsearch` | ES (已有) | host, port, username, password |
| `filesystem` | 本地/NFS 文件系统 | root_path, watch_changes |
| `oss` | 阿里云 OSS | endpoint, bucket, access_key, secret_key |
| `s3` | AWS S3 / MinIO | endpoint, bucket, access_key, secret_key, region |
| `ftp` | FTP/SFTP | host, port, username, password, root_path |

### 2.2 数据模型变更

```sql
-- 扩展 adh_datasources 表
ALTER TABLE adh_datasources ADD COLUMN db_type VARCHAR(50) DEFAULT 'mysql';
-- 已有 db_type 字段，只需扩展枚举值

-- 新增文件元数据表
CREATE TABLE adh_file_metadata (
    id BIGINT PRIMARY KEY,
    datasource_id INT NOT NULL,
    file_path VARCHAR(1000) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_type VARCHAR(50),           -- image/pdf/excel/csv/video/audio
    mime_type VARCHAR(100),
    file_size BIGINT,
    content_hash VARCHAR(64),        -- SHA256 for dedup
    extracted_text LONGTEXT,         -- 提取的文本内容
    embedding BLOB,                  -- 向量嵌入
    ocr_text LONGTEXT,              -- OCR 识别文本 (图片/扫描件)
    metadata_json LONGTEXT,          -- 额外元数据 (EXIF, sheet names, etc.)
    status VARCHAR(20) DEFAULT 'pending', -- pending/processing/ready/error
    error_message TEXT,
    workspace_id INT DEFAULT 0,
    created_at DATETIME,
    updated_at DATETIME,
    INDEX idx_datasource (datasource_id),
    INDEX idx_file_type (file_type),
    INDEX idx_status (status),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 文件处理任务表
CREATE TABLE adh_file_processing_tasks (
    id BIGINT PRIMARY KEY,
    file_id BIGINT NOT NULL,
    task_type VARCHAR(50),           -- ocr/embedding/extract/index
    status VARCHAR(20) DEFAULT 'pending',
    result_json LONGTEXT,
    error_message TEXT,
    started_at DATETIME,
    completed_at DATETIME,
    created_at DATETIME,
    INDEX idx_file (file_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 2.3 Pydantic 模型

```python
# backend/models/schemas.py 新增

class FileDatasourceConfig(BaseModel):
    """文件数据源配置"""
    root_path: Optional[str] = ""       # 本地文件系统根路径
    watch_changes: bool = False          # 是否监听文件变化
    allowed_extensions: Optional[list[str]] = None  # 允许的文件扩展名
    max_file_size_mb: int = 100          # 最大文件大小 (MB)

class OSSDatasourceConfig(BaseModel):
    """OSS/S3 数据源配置"""
    endpoint: str                        # OSS endpoint
    bucket: str                          # Bucket 名称
    access_key: str
    secret_key: str
    region: Optional[str] = ""
    prefix: Optional[str] = ""           # 对象前缀过滤
    allowed_extensions: Optional[list[str]] = None

class FileMetadata(BaseModel):
    id: int
    datasource_id: int
    file_path: str
    file_name: str
    file_type: str
    mime_type: Optional[str] = ""
    file_size: int
    status: str
    extracted_text: Optional[str] = ""
    ocr_text: Optional[str] = ""
    workspace_id: int = 0
    created_at: datetime
    updated_at: datetime

class FileUploadRequest(BaseModel):
    datasource_id: Optional[int] = 0
    workspace_id: Optional[int] = 0

class MultimodalMessage(BaseModel):
    """多模态消息"""
    role: str                           # user / assistant
    content: str                        # 文本内容
    attachments: Optional[list[dict]] = []  # 附件列表
    # attachment 格式: {"type": "image|file", "url": "...", "name": "...", "mime_type": "..."}
```

## 三、文件处理服务设计

### 3.1 文件处理器架构

```python
# backend/services/file_processor_service.py

class FileProcessorService:
    """统一文件处理服务"""

    # 处理器注册表
    PROCESSORS = {
        # 图片类
        'image/jpeg': ImageProcessor,
        'image/png': ImageProcessor,
        'image/webp': ImageProcessor,
        'image/gif': ImageProcessor,
        # 文档类
        'application/pdf': PDFProcessor,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ExcelProcessor,
        'application/vnd.ms-excel': ExcelProcessor,
        'text/csv': CSVProcessor,
        'text/plain': TextProcessor,
        # 视频类
        'video/mp4': VideoProcessor,
        'video/avi': VideoProcessor,
        # 音频类
        'audio/mp3': AudioProcessor,
        'audio/wav': AudioProcessor,
    }

    async def process_file(self, file_path: str, mime_type: str) -> ProcessingResult:
        """处理文件，返回提取的文本/结构化数据"""
        processor = self.PROCESSORS.get(mime_type)
        if not processor:
            return ProcessingResult(error=f"不支持的文件类型: {mime_type}")
        return await processor.process(file_path)
```

### 3.2 OpenCV 图片处理器

```python
# backend/processors/image_processor.py

class ImageProcessor:
    """基于 OpenCV 的图片处理器"""

    async def process(self, file_path: str) -> ProcessingResult:
        """
        处理流程:
        1. 读取图片 (cv2.imread)
        2. 基础信息提取 (尺寸、通道、格式)
        3. OCR 文字识别 (可选: pytesseract / PaddleOCR)
        4. 图片描述生成 (LLM vision)
        5. 向量嵌入 (CLIP / 本地 embedding)
        """
        import cv2

        img = cv2.imread(file_path)
        if img is None:
            return ProcessingResult(error="无法读取图片")

        h, w = img.shape[:2]
        channels = img.shape[2] if len(img.shape) > 2 else 1
        metadata = {
            "width": w, "height": h, "channels": channels,
            "file_size": os.path.getsize(file_path),
        }

        # OCR 识别 (如果图片包含文字)
        ocr_text = await self._ocr(img)

        # 生成图片描述 (如果 LLM 支持 vision)
        description = await self._describe_image(file_path)

        return ProcessingResult(
            extracted_text=description or ocr_text,
            ocr_text=ocr_text,
            metadata=metadata,
        )

    async def _ocr(self, img) -> str:
        """OCR 文字识别"""
        try:
            import pytesseract
            return pytesseract.image_to_string(img, lang='chi_sim+eng')
        except ImportError:
            logger.warning("pytesseract not installed, skipping OCR")
            return ""

    async def _describe_image(self, file_path: str) -> str:
        """使用 LLM Vision 生成图片描述"""
        # 检查 LLM 是否支持 vision
        # 如果支持，将图片 base64 发送给 LLM
        pass
```

### 3.3 文档处理器

```python
# backend/processors/document_processor.py

class PDFProcessor:
    """PDF 文档处理器"""
    async def process(self, file_path: str) -> ProcessingResult:
        """
        处理流程:
        1. 文本提取 (PyPDF2 / pdfplumber)
        2. 表格提取 (pdfplumber)
        3. 图片提取 (fitz / pdf2image)
        4. 向量嵌入
        """
        import pdfplumber

        texts = []
        tables = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    texts.append(text)
                page_tables = page.extract_tables()
                tables.extend(page_tables)

        return ProcessingResult(
            extracted_text="\n\n".join(texts),
            metadata={"pages": len(texts), "tables": len(tables)},
        )


class ExcelProcessor:
    """Excel 处理器"""
    async def process(self, file_path: str) -> ProcessingResult:
        import pandas as pd

        xls = pd.ExcelFile(file_path)
        sheets_data = {}
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
            sheets_data[sheet_name] = {
                "columns": list(df.columns),
                "rows": len(df),
                "sample": df.head(10).to_dict(orient='records'),
            }

        return ProcessingResult(
            extracted_text=json.dumps(sheets_data, ensure_ascii=False, default=str),
            metadata={"sheets": xls.sheet_names},
        )


class CSVProcessor:
    """CSV 处理器"""
    async def process(self, file_path: str) -> ProcessingResult:
        import pandas as pd

        df = pd.read_csv(file_path, nrows=1000)
        return ProcessingResult(
            extracted_text=df.head(50).to_string(),
            metadata={"columns": list(df.columns), "rows": len(df)},
        )
```

## 四、多模态 LLM 服务设计

### 4.1 LLM 能力检测

```python
# backend/services/multimodal_llm_service.py

class LLMCapabilities(BaseModel):
    """LLM 能力描述"""
    supports_vision: bool = False       # 是否支持图片输入
    supports_audio: bool = False        # 是否支持音频输入
    supports_file: bool = False         # 是否支持文件输入
    supports_image_generation: bool = False  # 是否支持图片生成
    max_image_size_mb: int = 20         # 最大图片大小
    supported_image_formats: list[str] = ["jpeg", "png", "webp", "gif"]
    model_name: str = ""
    provider: str = ""


class MultimodalLLMService:
    """多模态 LLM 服务"""

    # 已知模型能力映射
    MODEL_CAPABILITIES = {
        # Anthropic Claude
        "claude-3-opus": LLMCapabilities(supports_vision=True, provider="anthropic"),
        "claude-3-sonnet": LLMCapabilities(supports_vision=True, provider="anthropic"),
        "claude-3-haiku": LLMCapabilities(supports_vision=True, provider="anthropic"),
        "claude-3-5-sonnet": LLMCapabilities(supports_vision=True, provider="anthropic"),
        "claude-sonnet-4": LLMCapabilities(supports_vision=True, provider="anthropic"),
        "claude-opus-4": LLMCapabilities(supports_vision=True, provider="anthropic"),
        # OpenAI GPT-4V
        "gpt-4o": LLMCapabilities(supports_vision=True, provider="openai"),
        "gpt-4-vision-preview": LLMCapabilities(supports_vision=True, provider="openai"),
        # Gemini
        "gemini-pro-vision": LLMCapabilities(supports_vision=True, provider="google"),
        "gemini-1.5-pro": LLMCapabilities(supports_vision=True, provider="google"),
    }

    def get_capabilities(self, model_name: str) -> LLMCapabilities:
        """获取模型的多模态能力"""
        # 先查精确匹配
        if model_name in self.MODEL_CAPABILITIES:
            return self.MODEL_CAPABILITIES[model_name]

        # 模糊匹配
        for key, caps in self.MODEL_CAPABILITIES.items():
            if key in model_name or model_name in key:
                return caps

        # 默认：仅支持文本
        return LLMCapabilities(model_name=model_name)

    def build_multimodal_message(
        self,
        text: str,
        images: list[dict] = None,  # [{"data": base64, "media_type": "image/jpeg"}]
        model_name: str = "",
    ) -> dict:
        """构建多模态消息 (Anthropic 格式)"""
        caps = self.get_capabilities(model_name)

        if not caps.supports_vision or not images:
            return {"role": "user", "content": text}

        # 构建 Anthropic 多模态 content blocks
        content = []
        for img in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["media_type"],
                    "data": img["data"],
                },
            })
        content.append({"type": "text", "text": text})

        return {"role": "user", "content": content}
```

### 4.2 LLM Client 扩展

```python
# backend/common/llm/llm_client.py 新增

@observe(as_type="generation")
def generate_with_vision(
    messages: list[dict],
    max_tokens: int = 4096,
    model_id: int = None,
) -> dict:
    """调用 LLM 的 vision 能力，支持图片输入。

    messages 中的 content 可以是:
    - str: 纯文本
    - list[dict]: Anthropic 多模态 content blocks
    """
    config = _get_model_config(model_id)
    client = _get_client_for_model(config)
    model_name = config["model_name"]

    # 检查模型是否支持 vision
    from backend.services.multimodal_llm_service import MultimodalLLMService
    mm_service = MultimodalLLMService()
    caps = mm_service.get_capabilities(model_name)
    if not caps.supports_vision:
        raise RuntimeError(f"模型 {model_name} 不支持图片输入")

    system_text = None
    filtered_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            filtered_messages.append(msg)

    kwargs = dict(model=model_name, max_tokens=max_tokens, messages=filtered_messages)
    if system_text:
        kwargs["system"] = system_text

    try:
        response = client.messages.create(**kwargs)
        # ... 解析响应 (复用现有逻辑)
        text = ""
        thinking = ""
        for block in response.content:
            if hasattr(block, "thinking"):
                thinking = block.thinking
            elif hasattr(block, "text"):
                text += block.text

        usage = response.usage
        tokens = {
            "input": usage.input_tokens if usage else 0,
            "output": usage.output_tokens if usage else 0,
        }
        tokens["total"] = tokens["input"] + tokens["output"]

        return {"text": text.strip(), "thinking": thinking, "tokens": tokens}
    except Exception as e:
        logger.error("Vision LLM call failed: %s", e)
        raise
```

## 五、Chat API 多模态扩展

### 5.1 消息模型扩展

```python
# backend/models/schemas.py

class ChatRequest(BaseModel):
    question: str
    history: Optional[list[dict]] = []
    datasource_id: Optional[int] = 0
    model_id: Optional[int] = None
    workflow_id: Optional[int] = None
    pipeline_mode: Optional[str] = None
    retrieval_strategy: Optional[str] = None
    mcp_tools: list[str] = []
    workspace_id: Optional[int] = 0
    # 新增多模态字段
    attachments: Optional[list[dict]] = []  # 附件列表
    # attachment: {"type": "image|file", "data": "base64...", "name": "xxx.png", "mime_type": "image/png"}
```

### 5.2 Chat 流程扩展

```python
# backend/api/chat.py 新增

async def _process_multimodal_input(
    attachments: list[dict],
    datasource_id: int = 0,
) -> dict:
    """处理多模态输入附件。

    Returns:
        {
            "images": [{"data": base64, "media_type": "image/jpeg"}],
            "file_context": "提取的文件文本内容",
            "file_summaries": [{"name": "xxx.pdf", "summary": "..."}],
        }
    """
    from backend.services.file_processor_service import FileProcessorService

    processor = FileProcessorService()
    images = []
    file_texts = []
    file_summaries = []

    for att in attachments:
        att_type = att.get("type", "")
        data = att.get("data", "")
        name = att.get("name", "unknown")
        mime_type = att.get("mime_type", "")

        if att_type == "image" and data:
            # 图片：保留 base64 用于 LLM vision
            images.append({
                "data": data,
                "media_type": mime_type or "image/jpeg",
                "name": name,
            })
        elif att_type == "file" and data:
            # 文件：保存临时文件并处理
            import tempfile, base64
            file_bytes = base64.b64decode(data)
            suffix = Path(name).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name

            result = await processor.process_file(tmp_path, mime_type)
            if result.extracted_text:
                file_texts.append(f"--- 文件: {name} ---\n{result.extracted_text}")
                file_summaries.append({"name": name, "summary": result.extracted_text[:500]})

            os.unlink(tmp_path)

    return {
        "images": images,
        "file_context": "\n\n".join(file_texts),
        "file_summaries": file_summaries,
    }


def _build_multimodal_messages(
    question: str,
    images: list[dict],
    file_context: str,
    table_info: list,
    column_metadata: list,
    # ... 其他 NL2SQL 参数
) -> list[dict]:
    """构建包含图片和文件上下文的 LLM 消息。"""

    # 1. 构建系统提示 (包含文件上下文)
    system_parts = [base_system_prompt]
    if file_context:
        system_parts.append(f"\n<file-context>\n{file_context}\n</file-context>")

    # 2. 构建用户消息 (包含图片)
    if images:
        # 使用 Anthropic 多模态格式
        from backend.services.multimodal_llm_service import MultimodalLLMService
        mm_service = MultimodalLLMService()
        user_msg = mm_service.build_multimodal_message(
            text=question,
            images=images,
        )
    else:
        user_msg = {"role": "user", "content": question}

    # 3. 组装消息列表
    messages = [
        {"role": "system", "content": "\n".join(system_parts)},
    ]
    # 添加历史消息
    for hist in history[-4:]:
        messages.append({"role": hist["role"], "content": hist["content"][:300]})
    messages.append(user_msg)

    return messages
```

### 5.3 文件上传 API

```python
# backend/api/files.py (新增)

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from backend.api.auth import get_current_user

router = APIRouter()

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    datasource_id: int = Form(0),
    workspace_id: int = Form(0),
    user: UserInfo = Depends(get_current_user),
):
    """上传文件并处理。

    返回文件 ID 和处理状态，前端可用于后续引用。
    """
    # 验证文件大小
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:  # 100MB
        raise HTTPException(400, "文件大小超过 100MB 限制")

    # 保存文件
    import hashlib
    content_hash = hashlib.sha256(content).hexdigest()
    save_path = f"data/uploads/{workspace_id}/{content_hash}_{file.filename}"
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(content)

    # 创建文件元数据记录
    file_id = int(time.time() * 1000000)
    # INSERT INTO adh_file_metadata ...

    # 异步处理文件 (OCR, 文本提取, 向量化)
    from backend.services.file_processor_service import FileProcessorService
    processor = FileProcessorService()
    # 可选: 后台任务处理
    # background_tasks.add_task(processor.process_file, save_path, file.content_type)

    return {
        "file_id": file_id,
        "file_name": file.filename,
        "file_size": len(content),
        "mime_type": file.content_type,
        "status": "uploaded",
    }


@router.get("/list")
async def list_files(
    datasource_id: int = 0,
    workspace_id: int = 0,
    user: UserInfo = Depends(get_current_user),
):
    """列出已上传/索引的文件。"""
    # SELECT FROM adh_file_metadata ...
    pass


@router.get("/{file_id}")
async def get_file_info(file_id: int, user: UserInfo = Depends(get_current_user)):
    """获取文件详情和处理结果。"""
    pass


@router.delete("/{file_id}")
async def delete_file(file_id: int, user: UserInfo = Depends(get_current_user)):
    """删除文件及其元数据。"""
    pass
```

## 六、文件系统/OSS 数据源连接器

### 6.1 文件系统连接器

```python
# backend/connectors/filesystem_connector.py

class FilesystemConnector:
    """本地/NFS 文件系统连接器"""

    def __init__(self, config: dict):
        self.root_path = config.get("root_path", "/")
        self.allowed_extensions = config.get("allowed_extensions", [])

    def list_files(self, path: str = "", recursive: bool = False) -> list[dict]:
        """列出目录下的文件"""
        import os
        from pathlib import Path

        target = Path(self.root_path) / path
        if not target.exists():
            return []

        files = []
        pattern = "**/*" if recursive else "*"
        for f in target.glob(pattern):
            if f.is_file():
                ext = f.suffix.lower()
                if self.allowed_extensions and ext not in self.allowed_extensions:
                    continue
                files.append({
                    "name": f.name,
                    "path": str(f.relative_to(self.root_path)),
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "extension": ext,
                })
        return files

    def read_file(self, path: str) -> bytes:
        """读取文件内容"""
        target = Path(self.root_path) / path
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        return target.read_bytes()

    def get_file_info(self, path: str) -> dict:
        """获取文件信息"""
        target = Path(self.root_path) / path
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        stat = target.stat()
        return {
            "name": target.name,
            "path": path,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "extension": target.suffix.lower(),
        }
```

### 6.2 OSS 连接器

```python
# backend/connectors/oss_connector.py

class OSSConnector:
    """阿里云 OSS / S3 / MinIO 连接器"""

    def __init__(self, config: dict):
        self.endpoint = config["endpoint"]
        self.bucket = config["bucket"]
        self.access_key = config["access_key"]
        self.secret_key = config["secret_key"]
        self.region = config.get("region", "")
        self.prefix = config.get("prefix", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import oss2
            auth = oss2.Auth(self.access_key, self.secret_key)
            self._client = oss2.Bucket(auth, self.endpoint, self.bucket)
        return self._client

    def list_files(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        """列出 OSS 对象"""
        client = self._get_client()
        full_prefix = f"{self.prefix}{prefix}"
        objects = []
        for obj in oss2.ObjectIterator(client, prefix=full_prefix, max_keys=max_keys):
            objects.append({
                "name": obj.key.split("/")[-1],
                "path": obj.key,
                "size": obj.size,
                "modified": obj.last_modified,
                "etag": obj.etag,
            })
        return objects

    def read_file(self, key: str) -> bytes:
        """读取 OSS 对象"""
        client = self._get_client()
        result = client.get_object(key)
        return result.read()

    def get_signed_url(self, key: str, expires: int = 3600) -> str:
        """生成签名 URL"""
        client = self._get_client()
        return client.sign_url('GET', key, expires)
```

## 七、前端设计

### 7.1 Chat 输入组件扩展

```tsx
// 前端 Chat.tsx 扩展

// 新增附件状态
const [attachments, setAttachments] = useState<Attachment[]>([]);
const fileInputRef = useRef<HTMLInputElement>(null);

interface Attachment {
  id: string;
  type: 'image' | 'file';
  name: string;
  mime_type: string;
  data: string;  // base64
  preview?: string;  // 图片预览 URL
  size: number;
}

// 文件选择处理
const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
  const files = e.target.files;
  if (!files) return;

  for (const file of files) {
    // 验证文件大小
    if (file.size > 50 * 1024 * 1024) {
      toast.error(`文件 ${file.name} 超过 50MB 限制`);
      continue;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const base64 = (reader.result as string).split(',')[1];
      const isImage = file.type.startsWith('image/');
      setAttachments(prev => [...prev, {
        id: `${Date.now()}-${Math.random()}`,
        type: isImage ? 'image' : 'file',
        name: file.name,
        mime_type: file.type,
        data: base64,
        preview: isImage ? reader.result as string : undefined,
        size: file.size,
      }]);
    };
    reader.readAsDataURL(file);
  }
};

// 发送消息时携带附件
const handleSend = () => {
  if (!input.trim() && attachments.length === 0) return;

  sendMessage({
    question: input,
    attachments: attachments.map(a => ({
      type: a.type,
      data: a.data,
      name: a.name,
      mime_type: a.mime_type,
    })),
  });

  setInput('');
  setAttachments([]);
};
```

### 7.2 多模态消息渲染

```tsx
// 新增组件: MultimodalMessage.tsx

function MultimodalMessage({ message }: { message: ChatMessage }) {
  return (
    <div className="space-y-2">
      {/* 图片附件 */}
      {message.attachments?.filter(a => a.type === 'image').map(img => (
        <img
          key={img.id}
          src={`data:${img.mime_type};base64,${img.data}`}
          alt={img.name}
          className="max-w-md rounded-lg border"
        />
      ))}

      {/* 文件附件 */}
      {message.attachments?.filter(a => a.type === 'file').map(file => (
        <div key={file.id} className="flex items-center gap-2 p-2 bg-gray-100 rounded">
          <FileText className="h-4 w-4" />
          <span className="text-sm">{file.name}</span>
          <span className="text-xs text-gray-500">{formatFileSize(file.size)}</span>
        </div>
      ))}

      {/* 文本内容 (Markdown) */}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {message.content}
      </ReactMarkdown>

      {/* 图片分析结果 */}
      {message.image_analysis && (
        <div className="p-3 bg-blue-50 rounded-lg">
          <h4 className="text-sm font-medium mb-1">图片分析</h4>
          <p className="text-sm">{message.image_analysis}</p>
        </div>
      )}
    </div>
  );
}
```

### 7.3 文件数据源管理页面

```tsx
// frontend/src/pages/admin/FileDatasources.tsx

export default function FileDatasources() {
  return (
    <div className="space-y-6">
      <h2 className="text-xl font-bold">文件数据源</h2>

      {/* 添加文件系统数据源 */}
      <Card>
        <CardHeader>
          <CardTitle>添加文件数据源</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="filesystem">
            <TabsList>
              <TabsTrigger value="filesystem">本地文件系统</TabsTrigger>
              <TabsTrigger value="oss">阿里云 OSS</TabsTrigger>
              <TabsTrigger value="s3">AWS S3 / MinIO</TabsTrigger>
            </TabsList>

            <TabsContent value="filesystem">
              {/* 文件系统配置表单 */}
              <FormField label="根路径" placeholder="/data/files" />
              <FormField label="监听变化" type="switch" />
            </TabsContent>

            <TabsContent value="oss">
              {/* OSS 配置表单 */}
              <FormField label="Endpoint" placeholder="oss-cn-hangzhou.aliyuncs.com" />
              <FormField label="Bucket" />
              <FormField label="Access Key" />
              <FormField label="Secret Key" type="password" />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* 文件浏览器 */}
      <Card>
        <CardHeader>
          <CardTitle>文件浏览器</CardTitle>
        </CardHeader>
        <CardContent>
          <FileBrowser datasourceId={selectedDsId} />
        </CardContent>
      </Card>
    </div>
  );
}
```

## 八、依赖包规划

### 8.1 新增 Python 依赖

```txt
# requirements.txt 新增

# 文件处理
pdfplumber>=0.10.0         # PDF 解析
openpyxl>=3.1.0            # Excel 处理
pandas>=2.0.0              # 数据处理 (已有)
python-magic>=0.4.27       # MIME 类型检测

# OpenCV
opencv-python-headless>=4.8.0  # 图像处理 (无 GUI 版本)
Pillow>=10.0.0             # 图像处理辅助

# OCR (可选)
pytesseract>=0.3.10        # Tesseract OCR
# 或 PaddleOCR>=2.7.0      # 百度 PaddleOCR (中文更好)

# OSS/S3
oss2>=2.18.0               # 阿里云 OSS
boto3>=1.28.0              # AWS S3 / MinIO

# 向量嵌入 (图片)
# clip-server>=0.8.0       # CLIP 模型 (可选)
```

### 8.2 新增前端依赖

```json
{
  "dependencies": {
    "react-dropzone": "^14.2.0",    // 文件拖拽上传
    "react-image-crop": "^10.0.0",  // 图片裁剪 (可选)
    "file-type": "^18.5.0"          // 文件类型检测
  }
}
```

## 九、实施计划

### Phase 1: 基础设施 (2-3 周)
1. 数据库迁移脚本 (adh_file_metadata, adh_file_processing_tasks)
2. 文件系统连接器 (filesystem_connector.py)
3. OSS 连接器 (oss_connector.py)
4. 文件处理服务框架 (file_processor_service.py)
5. LLM 能力检测服务 (multimodal_llm_service.py)

### Phase 2: 文件处理 (2 周)
1. 图片处理器 (OpenCV + OCR)
2. PDF 处理器 (pdfplumber)
3. Excel/CSV 处理器 (pandas)
4. 文件上传 API (backend/api/files.py)
5. 文件元数据 CRUD

### Phase 3: 多模态 Chat (2 周)
1. ChatRequest 模型扩展 (attachments 字段)
2. LLM Client 扩展 (generate_with_vision)
3. Chat API 多模态消息构建
4. 前端文件上传组件 (拖拽 + 选择)
5. 前端多模态消息渲染

### Phase 4: 高级功能 (2 周)
1. 文件数据源管理页面
2. 文件浏览器组件
3. 文件搜索 (向量 + 全文)
4. 图片分析 Agent
5. 视频处理 (OpenCV 帧提取 + 分析)

## 十、风险与注意事项

1. **LLM 能力差异**：不同模型的 vision 能力差异大，需要优雅降级
2. **文件大小限制**：base64 编码会增大约 33%，需要限制上传大小
3. **OCR 准确性**：中文 OCR 需要额外训练数据或使用 PaddleOCR
4. **存储成本**：文件存储需要规划存储策略 (本地 vs OSS)
5. **安全风险**：文件上传需要严格验证，防止恶意文件
6. **性能影响**：大文件处理可能阻塞请求，建议使用后台任务
