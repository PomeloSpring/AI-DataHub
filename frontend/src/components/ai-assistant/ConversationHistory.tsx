import React, { useState, useEffect } from 'react';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';
import { useAuthStore } from '../../stores/authStore';
import client from '../../api/client';

interface Session {
  session_id: string;
  message_count: number;
  started_at: string;
  last_message_at: string;
  last_message: string;
}

interface Message {
  id: number;
  session_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

const ConversationHistory: React.FC = () => {
  const { user } = useAuthStore();
  const { sendMessage } = useAIAssistantStore();

  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Message[]>([]);

  // 加载会话列表
  useEffect(() => {
    loadSessions();
  }, []);

  const loadSessions = async () => {
    try {
      setIsLoading(true);
      const response = await client.get('/ai-assistant/conversations', {
        params: { limit: 50 }
      });
      setSessions(response.data.sessions || []);
    } catch (error) {
      console.error('Load sessions error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  // 加载会话消息
  const loadMessages = async (sessionId: string) => {
    try {
      setIsLoading(true);
      const response = await client.get(`/ai-assistant/conversations/${sessionId}`);
      setMessages(response.data.messages || []);
      setSelectedSession(sessionId);
    } catch (error) {
      console.error('Load messages error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  // 删除会话
  const deleteSession = async (sessionId: string) => {
    if (!confirm('确定要删除这个会话吗？')) return;

    try {
      await client.delete(`/ai-assistant/conversations/${sessionId}`);
      await loadSessions();
      if (selectedSession === sessionId) {
        setSelectedSession(null);
        setMessages([]);
      }
    } catch (error) {
      console.error('Delete session error:', error);
    }
  };

  // 搜索消息
  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchResults([]);
      return;
    }

    try {
      const response = await client.get('/ai-assistant/conversations/search', {
        params: { query: searchQuery, limit: 20 }
      });
      setSearchResults(response.data.messages || []);
    } catch (error) {
      console.error('Search error:', error);
    }
  };

  // 继续对话
  const continueConversation = (sessionId: string) => {
    // 切换到对话tab并加载会话
    const store = useAIAssistantStore.getState();
    store.clearMessages();
    // 这里可以添加加载历史消息的逻辑
    // 切换到对话tab需要通过父组件控制
  };

  // 格式化时间
  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();

    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`;

    return date.toLocaleDateString('zh-CN', {
      month: 'short',
      day: 'numeric'
    });
  };

  // 截取消息摘要
  const truncateMessage = (message: string, maxLength: number = 50) => {
    if (!message) return '';
    return message.length > maxLength
      ? message.substring(0, maxLength) + '...'
      : message;
  };

  // 渲染会话列表
  const renderSessionList = () => {
    if (isLoading) {
      return (
        <div className="flex items-center justify-center py-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
        </div>
      );
    }

    if (sessions.length === 0) {
      return (
        <div className="text-center text-gray-500 py-8">
          <div className="text-4xl mb-2">📭</div>
          <p>暂无对话历史</p>
        </div>
      );
    }

    return (
      <div className="space-y-2">
        {sessions.map((session) => (
          <div
            key={session.session_id}
            onClick={() => loadMessages(session.session_id)}
            className={`p-3 rounded-lg cursor-pointer transition-colors ${
              selectedSession === session.session_id
                ? 'bg-blue-50 border border-blue-200'
                : 'bg-gray-50 hover:bg-gray-100 border border-transparent'
            }`}
          >
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-gray-800 truncate">
                  {truncateMessage(session.last_message)}
                </div>
                <div className="flex items-center space-x-2 mt-1 text-xs text-gray-500">
                  <span>{session.message_count} 条消息</span>
                  <span>•</span>
                  <span>{formatTime(session.last_message_at)}</span>
                </div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  deleteSession(session.session_id);
                }}
                className="text-gray-400 hover:text-red-500 transition-colors p-1"
                title="删除会话"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>
          </div>
        ))}
      </div>
    );
  };

  // 渲染消息详情
  const renderMessageDetail = () => {
    if (!selectedSession) {
      return (
        <div className="text-center text-gray-500 py-8">
          <div className="text-4xl mb-2">👆</div>
          <p>选择一个会话查看详情</p>
        </div>
      );
    }

    if (isLoading) {
      return (
        <div className="flex items-center justify-center py-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
        </div>
      );
    }

    return (
      <div className="space-y-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-blue-500 text-white'
                  : 'bg-gray-100 text-gray-800'
              }`}
            >
              <div className="whitespace-pre-wrap break-words">
                {msg.content}
              </div>
              <div
                className={`text-xs mt-1 ${
                  msg.role === 'user' ? 'text-blue-100' : 'text-gray-400'
                }`}
              >
                {new Date(msg.created_at).toLocaleString('zh-CN', {
                  month: 'short',
                  day: 'numeric',
                  hour: '2-digit',
                  minute: '2-digit'
                })}
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  };

  // 渲染搜索结果
  const renderSearchResults = () => {
    if (!searchQuery) return null;

    if (searchResults.length === 0) {
      return (
        <div className="text-center text-gray-500 py-4">
          <p className="text-sm">未找到相关结果</p>
        </div>
      );
    }

    return (
      <div className="space-y-2">
        {searchResults.map((msg) => (
          <div
            key={msg.id}
            className="p-2 bg-gray-50 rounded-lg cursor-pointer hover:bg-gray-100"
            onClick={() => loadMessages(msg.session_id)}
          >
            <div className="flex items-center space-x-2 text-xs text-gray-500 mb-1">
              <span className={msg.role === 'user' ? 'text-blue-500' : 'text-green-500'}>
                {msg.role === 'user' ? '用户' : 'AI'}
              </span>
              <span>•</span>
              <span>{formatTime(msg.created_at)}</span>
            </div>
            <div className="text-sm text-gray-700 line-clamp-2">
              {msg.content}
            </div>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div className="h-full flex flex-col">
      {/* 搜索栏 */}
      <div className="p-3 border-b border-gray-200">
        <div className="flex items-center space-x-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="搜索对话历史..."
            className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg
                     focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          <button
            onClick={handleSearch}
            className="px-3 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </button>
        </div>
      </div>

      {/* 内容区域 */}
      <div className="flex-1 overflow-y-auto">
        {searchQuery ? (
          // 搜索结果
          <div className="p-3">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-sm font-medium text-gray-700">搜索结果</h4>
              <button
                onClick={() => {
                  setSearchQuery('');
                  setSearchResults([]);
                }}
                className="text-xs text-gray-500 hover:text-gray-700"
              >
                清除
              </button>
            </div>
            {renderSearchResults()}
          </div>
        ) : selectedSession ? (
          // 消息详情
          <div className="p-3">
            <div className="flex items-center justify-between mb-3">
              <button
                onClick={() => {
                  setSelectedSession(null);
                  setMessages([]);
                }}
                className="flex items-center text-sm text-gray-600 hover:text-gray-800"
              >
                <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
                返回列表
              </button>
              <button
                onClick={() => continueConversation(selectedSession)}
                className="text-xs px-3 py-1 bg-blue-500 text-white rounded hover:bg-blue-600"
              >
                继续对话
              </button>
            </div>
            {renderMessageDetail()}
          </div>
        ) : (
          // 会话列表
          <div className="p-3">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-sm font-medium text-gray-700">对话历史</h4>
              <button
                onClick={loadSessions}
                className="text-xs text-gray-500 hover:text-gray-700"
              >
                刷新
              </button>
            </div>
            {renderSessionList()}
          </div>
        )}
      </div>

      {/* 底部信息 */}
      <div className="p-3 border-t border-gray-200 bg-gray-50">
        <div className="text-xs text-gray-500 text-center">
          共 {sessions.length} 个会话
        </div>
      </div>
    </div>
  );
};

export default ConversationHistory;
