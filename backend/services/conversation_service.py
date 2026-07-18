"""Conversation Service — conversation history persistence.

Handles saving and retrieving conversation history from database.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from backend.config.ai_assistant_config import (
    AI_ASSISTANT_LOG_RETENTION_DAYS,
    AI_ASSISTANT_MAX_HISTORY_LENGTH
)

logger = logging.getLogger(__name__)


class ConversationService:
    """会话服务类"""

    def __init__(self):
        """初始化服务"""
        pass

    async def save_message(
        self,
        session_id: str,
        user_id: int,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """保存消息到数据库

        Args:
            session_id: 会话ID
            user_id: 用户ID
            role: 角色（user/assistant）
            content: 消息内容
            metadata: 元数据

        Returns:
            bool: 是否保存成功
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO adh_conversation_history
                        (session_id, user_id, role, content, metadata)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (
                        session_id,
                        user_id,
                        role,
                        content,
                        str(metadata) if metadata else None
                    ))
                conn.commit()
                return True
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error saving message: {e}")
            return False

    async def get_session_messages(
        self,
        session_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """获取会话消息历史

        Args:
            session_id: 会话ID
            limit: 返回消息数量限制

        Returns:
            list: 消息列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, session_id, user_id, role, content, metadata, created_at
                        FROM adh_conversation_history
                        WHERE session_id = %s
                        ORDER BY created_at ASC
                        LIMIT %s
                    """, (session_id, limit))

                    messages = []
                    for row in cur.fetchall():
                        messages.append({
                            "id": row["id"],
                            "session_id": row["session_id"],
                            "user_id": row["user_id"],
                            "role": row["role"],
                            "content": row["content"],
                            "metadata": row["metadata"],
                            "created_at": row["created_at"].isoformat() if row["created_at"] else None
                        })

                    return messages
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error getting session messages: {e}")
            return []

    async def get_user_sessions(
        self,
        user_id: int,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取用户的会话列表

        Args:
            user_id: 用户ID
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            list: 会话列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 获取会话列表（按最后消息时间排序）
                    cur.execute("""
                        SELECT
                            session_id,
                            COUNT(*) as message_count,
                            MIN(created_at) as started_at,
                            MAX(created_at) as last_message_at,
                            SUBSTRING_INDEX(GROUP_CONCAT(content ORDER BY created_at DESC), ',', 1) as last_message
                        FROM adh_conversation_history
                        WHERE user_id = %s
                        GROUP BY session_id
                        ORDER BY last_message_at DESC
                        LIMIT %s OFFSET %s
                    """, (user_id, limit, offset))

                    sessions = []
                    for row in cur.fetchall():
                        sessions.append({
                            "session_id": row["session_id"],
                            "message_count": row["message_count"],
                            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                            "last_message_at": row["last_message_at"].isoformat() if row["last_message_at"] else None,
                            "last_message": row["last_message"][:100] if row["last_message"] else ""
                        })

                    return sessions
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error getting user sessions: {e}")
            return []

    async def delete_session(self, session_id: str, user_id: int) -> bool:
        """删除会话

        Args:
            session_id: 会话ID
            user_id: 用户ID（用于权限验证）

        Returns:
            bool: 是否删除成功
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 验证会话属于该用户
                    cur.execute("""
                        SELECT COUNT(*) as count
                        FROM adh_conversation_history
                        WHERE session_id = %s AND user_id = %s
                    """, (session_id, user_id))

                    result = cur.fetchone()
                    if not result or result["count"] == 0:
                        return False

                    # 删除会话消息
                    cur.execute("""
                        DELETE FROM adh_conversation_history
                        WHERE session_id = %s
                    """, (session_id,))

                conn.commit()
                return True
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            return False

    async def cleanup_old_sessions(self, days: int = None) -> int:
        """清理旧会话

        Args:
            days: 保留天数（默认使用配置值）

        Returns:
            int: 清理的会话数量
        """
        if days is None:
            days = AI_ASSISTANT_LOG_RETENTION_DAYS

        try:
            from backend.common.db.metadata_db import get_metadata_conn

            cutoff_date = datetime.now() - timedelta(days=days)

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    # 删除旧消息
                    cur.execute("""
                        DELETE FROM adh_conversation_history
                        WHERE created_at < %s
                    """, (cutoff_date,))

                    deleted_count = cur.rowcount

                conn.commit()
                logger.info(f"Cleaned up {deleted_count} old conversation messages")
                return deleted_count
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error cleaning up old sessions: {e}")
            return 0

    async def get_session_count(self, user_id: int) -> int:
        """获取用户会话数量

        Args:
            user_id: 用户ID

        Returns:
            int: 会话数量
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(DISTINCT session_id) as count
                        FROM adh_conversation_history
                        WHERE user_id = %s
                    """, (user_id,))

                    result = cur.fetchone()
                    return result["count"] if result else 0
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error getting session count: {e}")
            return 0

    async def get_message_count(self, session_id: str) -> int:
        """获取会话消息数量

        Args:
            session_id: 会话ID

        Returns:
            int: 消息数量
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*) as count
                        FROM adh_conversation_history
                        WHERE session_id = %s
                    """, (session_id,))

                    result = cur.fetchone()
                    return result["count"] if result else 0
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error getting message count: {e}")
            return 0

    async def search_messages(
        self,
        user_id: int,
        query: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """搜索消息

        Args:
            user_id: 用户ID
            query: 搜索关键词
            limit: 返回数量限制

        Returns:
            list: 消息列表
        """
        try:
            from backend.common.db.metadata_db import get_metadata_conn

            conn = get_metadata_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, session_id, role, content, created_at
                        FROM adh_conversation_history
                        WHERE user_id = %s AND content LIKE %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    """, (user_id, f"%{query}%", limit))

                    messages = []
                    for row in cur.fetchall():
                        messages.append({
                            "id": row["id"],
                            "session_id": row["session_id"],
                            "role": row["role"],
                            "content": row["content"],
                            "created_at": row["created_at"].isoformat() if row["created_at"] else None
                        })

                    return messages
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"Error searching messages: {e}")
            return []
