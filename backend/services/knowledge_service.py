"""Knowledge Service — knowledge base vectorization and management.

Handles document loading, chunking, embedding, and vector storage.
"""

import logging
import os
import uuid
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from backend.config.ai_assistant_config import (
    KNOWLEDGE_BASE_PATH,
    KNOWLEDGE_CHUNK_SIZE,
    KNOWLEDGE_CHUNK_OVERLAP,
    KNOWLEDGE_CHUNK_SEPARATORS,
    KNOWLEDGE_SUPPORTED_TYPES,
    KNOWLEDGE_SOURCES
)

logger = logging.getLogger(__name__)


class KnowledgeService:
    """知识库服务类"""

    def __init__(self):
        """初始化服务"""
        self._vector_store = None
        self._embedding_func = None

    @property
    def vector_store(self):
        """懒加载向量存储"""
        if self._vector_store is None:
            from backend.common.vector import get_vector_store
            self._vector_store = get_vector_store()
        return self._vector_store

    @property
    def embedding_func(self):
        """懒加载嵌入函数"""
        if self._embedding_func is None:
            from backend.common.llm.embedding import generate_embedding
            self._embedding_func = generate_embedding
        return self._embedding_func

    async def sync_knowledge_base(
        self,
        source: str = "all",
        force: bool = False
    ) -> Dict[str, Any]:
        """同步知识库

        Args:
            source: 知识来源（docs, database, config, all）
            force: 是否强制更新

        Returns:
            dict: 同步结果
        """
        try:
            results = {
                "success": True,
                "document_count": 0,
                "chunk_count": 0,
                "sources": []
            }

            if source == "all":
                # 同步所有来源
                for source_name, source_config in KNOWLEDGE_SOURCES.items():
                    result = await self._sync_source(source_name, source_config, force)
                    results["document_count"] += result["document_count"]
                    results["chunk_count"] += result["chunk_count"]
                    results["sources"].append({
                        "name": source_name,
                        **result
                    })
            else:
                # 同步指定来源
                if source in KNOWLEDGE_SOURCES:
                    result = await self._sync_source(source, KNOWLEDGE_SOURCES[source], force)
                    results["document_count"] = result["document_count"]
                    results["chunk_count"] = result["chunk_count"]
                    results["sources"].append({
                        "name": source,
                        **result
                    })
                else:
                    return {
                        "success": False,
                        "error": f"未知的知识来源: {source}"
                    }

            results["updated_at"] = datetime.now().isoformat()
            return results

        except Exception as e:
            logger.error(f"Sync knowledge base error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    async def _sync_source(
        self,
        source_name: str,
        source_config: Dict[str, Any],
        force: bool = False
    ) -> Dict[str, Any]:
        """同步单个知识来源

        Args:
            source_name: 来源名称
            source_config: 来源配置
            force: 是否强制更新

        Returns:
            dict: 同步结果
        """
        logger.info(f"Syncing knowledge source: {source_name}")

        if source_config["type"] == "directory":
            return await self._sync_directory_source(source_name, source_config, force)
        elif source_config["type"] == "database":
            return await self._sync_database_source(source_name, source_config, force)
        else:
            return {
                "document_count": 0,
                "chunk_count": 0,
                "error": f"不支持的来源类型: {source_config['type']}"
            }

    async def _sync_directory_source(
        self,
        source_name: str,
        source_config: Dict[str, Any],
        force: bool = False
    ) -> Dict[str, Any]:
        """同步目录来源

        Args:
            source_name: 来源名称
            source_config: 来源配置
            force: 是否强制更新

        Returns:
            dict: 同步结果
        """
        directory = source_config["path"]
        file_types = source_config.get("file_types", [".md", ".txt"])

        if not os.path.exists(directory):
            logger.warning(f"Directory not found: {directory}")
            return {
                "document_count": 0,
                "chunk_count": 0,
                "error": f"目录不存在: {directory}"
            }

        # 扫描文档
        documents = self._scan_directory(directory, file_types, source_name)
        logger.info(f"Found {len(documents)} documents in {directory}")

        # 处理文档
        total_chunks = 0
        for doc in documents:
            chunks = await self._process_document(doc, force)
            total_chunks += len(chunks)

        return {
            "document_count": len(documents),
            "chunk_count": total_chunks
        }

    async def _sync_database_source(
        self,
        source_name: str,
        source_config: Dict[str, Any],
        force: bool = False
    ) -> Dict[str, Any]:
        """同步数据库来源

        Args:
            source_name: 来源名称
            source_config: 来源配置
            force: 是否强制更新

        Returns:
            dict: 同步结果
        """
        tables = source_config.get("tables", [])
        total_docs = 0
        total_chunks = 0

        logger.info(f"Syncing database source: {source_name}, tables: {tables}, force: {force}")

        for table_name in tables:
            try:
                logger.info(f"Loading metadata for table: {table_name}")

                # 从数据库表加载元数据
                metadata = await self._load_table_metadata(table_name)
                if metadata:
                    logger.info(f"Loaded metadata for {table_name}: {len(metadata.get('columns', []))} columns")

                    # 转换为文档格式
                    doc = self._metadata_to_document(table_name, metadata)
                    logger.info(f"Created document for {table_name}: {doc['id']}")

                    # 强制更新以确保数据被写入
                    chunks = await self._process_document(doc, force=True)
                    total_docs += 1
                    total_chunks += len(chunks)

                    logger.info(f"Processed {table_name}: {len(chunks)} chunks")
                else:
                    logger.warning(f"No metadata found for table: {table_name}")
            except Exception as e:
                logger.error(f"Error syncing table {table_name}: {e}", exc_info=True)

        logger.info(f"Database sync completed: {total_docs} docs, {total_chunks} chunks")

        return {
            "document_count": total_docs,
            "chunk_count": total_chunks
        }

    def _scan_directory(
        self,
        directory: str,
        file_types: List[str],
        source: str
    ) -> List[Dict[str, Any]]:
        """扫描目录中的文档

        Args:
            directory: 目录路径
            file_types: 支持的文件类型
            source: 来源名称

        Returns:
            list: 文档列表
        """
        documents = []

        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                file_ext = os.path.splitext(file)[1].lower()

                if file_ext in file_types:
                    try:
                        # 读取文件内容
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()

                        # 计算相对路径
                        rel_path = os.path.relpath(file_path, directory)

                        # 使用相对路径作为标题，这样同名文件可以区分
                        # 例如：guides/scheduled-tasks.md
                        title = rel_path

                        # 生成文档ID（基于相对路径的hash）
                        doc_id = hashlib.md5(rel_path.encode()).hexdigest()

                        documents.append({
                            "id": doc_id,
                            "title": title,
                            "content": content,
                            "source": source,
                            "file_path": rel_path,
                            "file_size": len(content)
                        })
                    except Exception as e:
                        logger.warning(f"Error reading file {file_path}: {e}")

        return documents

    async def _process_document(
        self,
        document: Dict[str, Any],
        force: bool = False
    ) -> List[Dict[str, Any]]:
        """处理文档（分块 + 向量化）

        Args:
            document: 文档数据
            force: 是否强制更新

        Returns:
            list: 文档分块列表
        """
        doc_id = document["id"]

        # 检查是否已存在（除非强制更新）
        if not force:
            existing = await self._check_document_exists(doc_id)
            if existing:
                logger.debug(f"Document already exists, skipping: {doc_id}")
                return []

        # 如果是强制更新，先删除旧的分块
        if force:
            await self._delete_document_chunks(doc_id)

        # 分块
        chunks = self._chunk_document(document)
        logger.info(f"Document {doc_id}: {len(chunks)} chunks")

        # 向量化并存储
        for chunk in chunks:
            try:
                # 生成向量
                embedding = self.embedding_func(chunk["content"])

                # 存储到向量数据库
                self.vector_store.upsert(
                    table="adh_knowledge_chunks",
                    id_column="id",
                    id_value=chunk["id"],
                    data={
                        "id": chunk["id"],
                        "document_id": doc_id,
                        "chunk_index": chunk["index"],
                        "content": chunk["content"],
                        "chunk_size": len(chunk["content"]),
                        "metadata": str(chunk.get("metadata", {})),
                        "embedding": str(embedding),
                        "is_active": 1
                    }
                )
            except Exception as e:
                logger.error(f"Error processing chunk {chunk['id']}: {e}")

        # 保存文档记录
        await self._save_document_record(document, len(chunks))

        return chunks

    async def _delete_document_chunks(self, doc_id: str):
        """删除文档的所有分块

        Args:
            doc_id: 文档ID
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM adh_knowledge_chunks WHERE document_id = %s",
                        (doc_id,)
                    )
                conn.commit()
                logger.info(f"Deleted chunks for document: {doc_id}")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Error deleting document chunks: {e}")

    def _chunk_document(
        self,
        document: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """将文档分块

        Args:
            document: 文档数据

        Returns:
            list: 文档分块列表
        """
        content = document["content"]
        chunks = []

        # 使用分隔符分割文本
        sections = self._split_text(content, KNOWLEDGE_CHUNK_SEPARATORS)

        current_chunk = ""
        chunk_index = 0

        for section in sections:
            # 如果当前块加上新部分超过大小限制
            if len(current_chunk) + len(section) > KNOWLEDGE_CHUNK_SIZE:
                # 保存当前块
                if current_chunk:
                    chunks.append({
                        "id": f"{document['id']}_{chunk_index}",
                        "index": chunk_index,
                        "content": current_chunk.strip(),
                        "metadata": {
                            "source": document.get("source", ""),
                            "title": document.get("title", "")
                        }
                    })
                    chunk_index += 1

                # 开始新块（保留部分重叠）
                if KNOWLEDGE_CHUNK_OVERLAP > 0 and current_chunk:
                    current_chunk = current_chunk[-KNOWLEDGE_CHUNK_OVERLAP:] + section
                else:
                    current_chunk = section
            else:
                current_chunk += section

        # 保存最后一个块
        if current_chunk:
            chunks.append({
                "id": f"{document['id']}_{chunk_index}",
                "index": chunk_index,
                "content": current_chunk.strip(),
                "metadata": {
                    "source": document.get("source", ""),
                    "title": document.get("title", "")
                }
            })

        return chunks

    def _split_text(
        self,
        text: str,
        separators: List[str]
    ) -> List[str]:
        """使用分隔符分割文本

        Args:
            text: 文本内容
            separators: 分隔符列表

        Returns:
            list: 分割后的文本段落
        """
        sections = [text]

        for separator in separators:
            new_sections = []
            for section in sections:
                if separator in section:
                    parts = section.split(separator)
                    for i, part in enumerate(parts):
                        if part:
                            if i < len(parts) - 1:
                                new_sections.append(part + separator)
                            else:
                                new_sections.append(part)
                else:
                    new_sections.append(section)
            sections = new_sections

        return [s for s in sections if s.strip()]

    async def _check_document_exists(self, doc_id: str) -> bool:
        """检查文档是否已存在

        Args:
            doc_id: 文档ID

        Returns:
            bool: 是否存在
        """
        try:
            # 查询数据库
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM adh_knowledge_documents WHERE id = %s",
                        (doc_id,)
                    )
                    return cur.fetchone() is not None
            finally:
                conn.close()
        except Exception:
            return False

    async def _save_document_record(
        self,
        document: Dict[str, Any],
        chunk_count: int
    ):
        """保存文档记录

        Args:
            document: 文档数据
            chunk_count: 分块数量
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO adh_knowledge_documents
                        (id, title, content, doc_type, source, file_path, file_size, chunk_count, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        title = VALUES(title),
                        content = VALUES(content),
                        file_size = VALUES(file_size),
                        chunk_count = VALUES(chunk_count),
                        updated_at = CURRENT_TIMESTAMP
                    """, (
                        document["id"],
                        document["title"],
                        document["content"][:1000],  # 只保存摘要
                        document.get("doc_type", "guide"),
                        document.get("source", "manual"),
                        document.get("file_path", ""),
                        document.get("file_size", 0),
                        chunk_count,
                        "active"
                    ))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Error saving document record: {e}")

    async def _load_table_metadata(self, table_name: str) -> Optional[Dict[str, Any]]:
        """加载表元数据

        Args:
            table_name: 表名

        Returns:
            dict: 表元数据
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 获取表信息
                    cur.execute("""
                        SELECT table_name, table_comment, table_business_desc
                        FROM adh_table_info
                        WHERE table_name = %s AND is_active = 1
                    """, (table_name,))
                    table_info = cur.fetchone()

                    if not table_info:
                        return None

                    # 获取字段信息
                    cur.execute("""
                        SELECT column_name, data_type, column_comment, business_desc
                        FROM adh_column_metadata
                        WHERE table_name = %s AND is_active = 1
                        ORDER BY column_name
                    """, (table_name,))
                    columns = cur.fetchall()

                    return {
                        "table": table_info,
                        "columns": columns
                    }
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Error loading table metadata: {e}")
            return None

    def _metadata_to_document(
        self,
        table_name: str,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """将表元数据转换为文档格式

        Args:
            table_name: 表名
            metadata: 表元数据

        Returns:
            dict: 文档数据
        """
        table_info = metadata["table"]
        columns = metadata["columns"]

        # 构建文档内容
        content_lines = [
            f"# 表: {table_name}",
            f"",
            f"## 基本信息",
            f"- 表名: {table_name}",
            f"- 注释: {table_info.get('table_comment', '')}",
            f"- 业务描述: {table_info.get('table_business_desc', '')}",
            f"",
            f"## 字段列表",
            f""
        ]

        for col in columns:
            content_lines.append(
                f"- **{col['column_name']}** ({col['data_type']}): "
                f"{col.get('column_comment', '')} - {col.get('business_desc', '')}"
            )

        content = "\n".join(content_lines)

        return {
            "id": f"table_{table_name}",
            "title": f"表: {table_name}",
            "content": content,
            "source": "database",
            "doc_type": "metadata",
            "file_path": f"table:{table_name}",
            "file_size": len(content)
        }

    async def delete_document(self, doc_id: str) -> Dict[str, Any]:
        """删除文档

        Args:
            doc_id: 文档ID

        Returns:
            dict: 删除结果
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 删除文档分块
                    cur.execute(
                        "DELETE FROM adh_knowledge_chunks WHERE document_id = %s",
                        (doc_id,)
                    )

                    # 删除文档记录
                    cur.execute(
                        "DELETE FROM adh_knowledge_documents WHERE id = %s",
                        (doc_id,)
                    )

                conn.commit()
            finally:
                conn.close()

            return {
                "success": True,
                "message": "文档删除成功"
            }

        except Exception as e:
            logger.error(f"Error deleting document: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def get_knowledge_stats(self) -> Dict[str, Any]:
        """获取知识库统计信息

        Returns:
            dict: 统计信息
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 统计文档数量
                    cur.execute(
                        "SELECT COUNT(*) as count FROM adh_knowledge_documents WHERE status = 'active'"
                    )
                    doc_count = cur.fetchone()["count"]

                    # 统计分块数量
                    cur.execute(
                        "SELECT COUNT(*) as count FROM adh_knowledge_chunks WHERE is_active = 1"
                    )
                    chunk_count = cur.fetchone()["count"]

                    # 统计最后同步时间
                    cur.execute(
                        "SELECT MAX(updated_at) as last_sync FROM adh_knowledge_documents"
                    )
                    result = cur.fetchone()
                    last_sync = result["last_sync"] if result else None

                    return {
                        "document_count": doc_count,
                        "chunk_count": chunk_count,
                        "vector_count": chunk_count,  # 每个分块对应一个向量
                        "last_sync": last_sync.isoformat() if last_sync else None,
                        "sync_status": "idle"
                    }
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error getting knowledge stats: {e}")
            return {
                "document_count": 0,
                "chunk_count": 0,
                "vector_count": 0,
                "last_sync": None,
                "sync_status": "error"
            }

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        doc_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取文档列表

        Args:
            page: 页码
            page_size: 每页数量
            doc_type: 文档类型过滤

        Returns:
            dict: 文档列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 构建查询
                    where_clause = "WHERE status = 'active'"
                    params = []

                    if doc_type:
                        where_clause += " AND doc_type = %s"
                        params.append(doc_type)

                    # 统计总数
                    cur.execute(
                        f"SELECT COUNT(*) as count FROM adh_knowledge_documents {where_clause}",
                        params
                    )
                    total = cur.fetchone()["count"]

                    # 查询文档列表
                    offset = (page - 1) * page_size
                    cur.execute(f"""
                        SELECT id, title, doc_type, source, file_size, chunk_count,
                               created_at, updated_at, tags
                        FROM adh_knowledge_documents
                        {where_clause}
                        ORDER BY updated_at DESC
                        LIMIT %s OFFSET %s
                    """, params + [page_size, offset])

                    documents = []
                    for row in cur.fetchall():
                        documents.append({
                            "id": row["id"],
                            "title": row["title"],
                            "doc_type": row["doc_type"],
                            "source": row["source"],
                            "size": f"{row['file_size']} chars",
                            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                            "tags": row.get("tags", [])
                        })

                    return {
                        "documents": documents,
                        "total": total,
                        "page": page,
                        "page_size": page_size
                    }
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error listing documents: {e}")
            return {
                "documents": [],
                "total": 0,
                "page": page,
                "page_size": page_size
            }
