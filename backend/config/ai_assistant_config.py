"""AI Assistant Configuration — settings for AI assistant module."""

import os
from pathlib import Path

# ── 基础配置 ──────────────────────────────────────────────────────────

# 是否启用AI助手
AI_ASSISTANT_ENABLED = os.getenv("AI_ASSISTANT_ENABLED", "true").lower() == "true"

# AI助手最大token数
AI_ASSISTANT_MAX_TOKENS = int(os.getenv("AI_ASSISTANT_MAX_TOKENS", "4096"))

# AI助手温度参数
AI_ASSISTANT_TEMPERATURE = float(os.getenv("AI_ASSISTANT_TEMPERATURE", "0.7"))

# ── 权限配置 ──────────────────────────────────────────────────────────

# 允许访问AI助手的角色
AI_ASSISTANT_ROLES = {"admin", "configurator", "viewer"}

# 允许管理知识库的角色
AI_ASSISTANT_MANAGE_ROLES = {"admin", "configurator"}

# 允许删除文档的角色
AI_ASSISTANT_DELETE_ROLES = {"admin"}

# ── 知识库配置 ────────────────────────────────────────────────────────

# 知识库文档目录
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", str(Path(__file__).parent.parent.parent / "docs"))

# 知识库同步间隔（秒）
KNOWLEDGE_SYNC_INTERVAL = int(os.getenv("KNOWLEDGE_SYNC_INTERVAL", "3600"))  # 1小时

# 文档分块大小
KNOWLEDGE_CHUNK_SIZE = int(os.getenv("KNOWLEDGE_CHUNK_SIZE", "512"))

# 文档分块重叠大小
KNOWLEDGE_CHUNK_OVERLAP = int(os.getenv("KNOWLEDGE_CHUNK_OVERLAP", "50"))

# 文档分块分隔符
KNOWLEDGE_CHUNK_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ".", "!", "?"]

# 支持的文档类型
KNOWLEDGE_SUPPORTED_TYPES = {".md", ".txt", ".rst", ".json", ".yaml", ".yml"}

# ── 向量配置 ──────────────────────────────────────────────────────────

# 向量维度
AI_ASSISTANT_VECTOR_DIM = int(os.getenv("AI_ASSISTANT_VECTOR_DIM", "768"))

# 向量搜索数量限制
AI_ASSISTANT_VECTOR_SEARCH_LIMIT = int(os.getenv("AI_ASSISTANT_VECTOR_SEARCH_LIMIT", "10"))

# 向量相似度阈值
AI_ASSISTANT_VECTOR_SIMILARITY_THRESHOLD = float(os.getenv("AI_ASSISTANT_VECTOR_SIMILARITY_THRESHOLD", "0.7"))

# ── 会话配置 ──────────────────────────────────────────────────────────

# 会话历史最大长度
AI_ASSISTANT_MAX_HISTORY_LENGTH = int(os.getenv("AI_ASSISTANT_MAX_HISTORY_LENGTH", "50"))

# 会话超时时间（秒）
AI_ASSISTANT_SESSION_TIMEOUT = int(os.getenv("AI_ASSISTANT_SESSION_TIMEOUT", "1800"))  # 30分钟

# ── 上下文配置 ────────────────────────────────────────────────────────

# 上下文检索数量
AI_ASSISTANT_CONTEXT_LIMIT = int(os.getenv("AI_ASSISTANT_CONTEXT_LIMIT", "5"))

# 上下文最大长度
AI_ASSISTANT_CONTEXT_MAX_LENGTH = int(os.getenv("AI_ASSISTANT_CONTEXT_MAX_LENGTH", "2000"))

# ── 日志配置 ──────────────────────────────────────────────────────────

# 是否记录对话历史
AI_ASSISTANT_LOG_CONVERSATIONS = os.getenv("AI_ASSISTANT_LOG_CONVERSATIONS", "true").lower() == "true"

# 对话历史保留天数
AI_ASSISTANT_LOG_RETENTION_DAYS = int(os.getenv("AI_ASSISTANT_LOG_RETENTION_DAYS", "30"))

# ── 缓存配置 ──────────────────────────────────────────────────────────

# 是否启用缓存
AI_ASSISTANT_CACHE_ENABLED = os.getenv("AI_ASSISTANT_CACHE_ENABLED", "true").lower() == "true"

# 缓存TTL（秒）
AI_ASSISTANT_CACHE_TTL = int(os.getenv("AI_ASSISTANT_CACHE_TTL", "3600"))  # 1小时

# 缓存最大大小
AI_ASSISTANT_CACHE_MAX_SIZE = int(os.getenv("AI_ASSISTANT_CACHE_MAX_SIZE", "1000"))

# ── 知识来源配置 ──────────────────────────────────────────────────────

# 知识来源定义
KNOWLEDGE_SOURCES = {
    "docs": {
        "name": "项目文档",
        "type": "directory",
        "path": KNOWLEDGE_BASE_PATH,
        "file_types": [".md", ".txt", ".rst"],
        "auto_sync": True,
        "sync_interval": "1h"
    },
    "database": {
        "name": "数据库元数据",
        "type": "database",
        "tables": ["adh_table_info", "adh_column_metadata", "adh_business_terms"],
        "auto_sync": True,
        "sync_interval": "5m"
    },
    "config": {
        "name": "配置指南",
        "type": "directory",
        "path": str(Path(__file__).parent / "agents"),
        "file_types": [".md", ".yaml"],
        "auto_sync": True,
        "sync_interval": "30m"
    }
}

# ── 提示词模板 ────────────────────────────────────────────────────────

# 系统提示词模板
SYSTEM_PROMPT_TEMPLATE = """你是一个AI助手，专门帮助用户配置和使用AI-DataHub系统。

你的职责：
1. 回答用户关于系统功能的问题
2. 指导用户完成配置操作
3. 解释系统概念和术语
4. 提供最佳实践建议

重要限制：
- 你不能修改代码或执行命令
- 你只能提供配置指导，不能直接修改配置
- 你不能访问或修改用户数据
- 你只能使用提供的工具来帮助用户

{context_info}

{knowledge_context}

请基于以上信息回答用户的问题。如果涉及配置操作，请提供详细的步骤指导。
"""

# 上下文信息模板
CONTEXT_INFO_TEMPLATE = """当前页面上下文：
- 页面：{page}
- 模块：{module}
{sub_module_info}
"""

# 知识上下文模板
KNOWLEDGE_CONTEXT_TEMPLATE = """相关知识库内容：
{sources}
"""
