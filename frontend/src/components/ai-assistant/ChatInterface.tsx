import React, { useState, useRef, useEffect } from 'react';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';
import { useAuthStore } from '../../stores/authStore';
import { aiAssistantExecutor } from '../../utils/aiAssistantExecutor';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;
  timestamp: Date;
  sources?: any[];
  toolCalls?: any[];
  pendingActions?: any[];
}

const ChatInterface: React.FC = () => {
  const [input, setInput] = useState('');
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { user } = useAuthStore();
  const {
    messages,
    isLoading,
    currentContext,
    sessionId,
    sendMessage,
    stopGeneration,
    clearMessages
  } = useAIAssistantStore();

  // 自动滚动到底部
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(scrollToBottom, [messages]);

  // 发送消息
  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const message = input.trim();
    setInput('');

    // 重置textarea高度
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    // sendMessage会自动处理工具调用和执行
    await sendMessage(message, currentContext);
  };

  // 处理键盘事件
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // 自动调整textarea高度
  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
  };

  // 切换思考过程展开/折叠
  const toggleThinking = (messageId: string) => {
    setExpandedThinking(prev => {
      const newSet = new Set(prev);
      if (newSet.has(messageId)) {
        newSet.delete(messageId);
      } else {
        newSet.add(messageId);
      }
      return newSet;
    });
  };

  // 快捷问题
  const quickQuestions = [
    '如何配置数据源？',
    'Agent是什么？',
    '如何创建报表？',
    '查看当前页面帮助'
  ];

  const handleQuickQuestion = (question: string) => {
    setInput(question);
    // 自动发送
    setTimeout(() => {
      sendMessage(question, currentContext);
    }, 100);
  };

  // 渲染思考过程
  const renderThinking = (msg: Message) => {
    if (!msg.thinking) return null;

    const isExpanded = expandedThinking.has(msg.id);

    return (
      <div className="mb-2">
        <button
          onClick={() => toggleThinking(msg.id)}
          className="flex items-center text-xs text-gray-500 hover:text-gray-700 transition-colors"
        >
          <svg
            className={`w-4 h-4 mr-1 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          思考过程
        </button>
        {isExpanded && (
          <div className="mt-2 p-3 bg-gray-50 rounded-lg text-xs text-gray-600 whitespace-pre-wrap border-l-2 border-gray-300">
            {msg.thinking}
          </div>
        )}
      </div>
    );
  };

  // 渲染消息
  const renderMessage = (msg: Message) => {
    const isUser = msg.role === 'user';

    return (
      <div
        key={msg.id}
        className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}
      >
        <div
          className={`max-w-[80%] rounded-lg px-4 py-2 ${
            isUser
              ? 'bg-blue-500 text-white'
              : 'bg-gray-100 text-gray-800'
          }`}
        >
          {/* 思考过程 */}
          {!isUser && renderThinking(msg)}

          {/* 消息内容 */}
          <div className="whitespace-pre-wrap break-words">
            {msg.content}
          </div>

          {/* 知识来源 */}
          {msg.sources && msg.sources.length > 0 && (
            <div className="mt-2 pt-2 border-t border-gray-200">
              <div className="text-xs text-gray-500 mb-1">参考来源：</div>
              {msg.sources.slice(0, 3).map((source, idx) => (
                <div key={idx} className="text-xs text-gray-600 truncate">
                  • {source.title}
                </div>
              ))}
            </div>
          )}

          {/* 时间戳 */}
          <div
            className={`text-xs mt-1 ${
              isUser ? 'text-blue-100' : 'text-gray-400'
            }`}
          >
            {new Date(msg.timestamp).toLocaleTimeString('zh-CN', {
              hour: '2-digit',
              minute: '2-digit'
            })}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full">
      {/* 工具栏 */}
      <div className="px-4 py-2 border-b border-gray-200 bg-gray-50">
        <div className="flex items-center justify-between">
          <div className="text-sm text-gray-600">
            {sessionId ? `会话ID: ${sessionId.substring(0, 8)}...` : '新会话'}
          </div>
          <div className="flex items-center space-x-2">
            <button
              onClick={clearMessages}
              className="flex items-center px-3 py-1.5 text-xs text-gray-600 bg-white rounded-md
                       border border-gray-300 hover:bg-gray-50 transition-colors"
              title="新对话"
            >
              <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              新对话
            </button>
          </div>
        </div>
      </div>

      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* 欢迎消息 */}
        {messages.length === 0 && (
          <div className="text-center text-gray-500 py-8">
            <div className="text-4xl mb-4">🤖</div>
            <h4 className="text-lg font-medium text-gray-700 mb-2">
              你好！我是AI助手
            </h4>
            <p className="text-sm">
              我可以帮你配置系统、解答问题、查询文档
            </p>
          </div>
        )}

        {/* 消息列表 */}
        {messages.map(renderMessage)}

        {/* 加载指示器 */}
        {isLoading && (
          <div className="flex justify-start mb-4">
            <div className="bg-gray-100 rounded-lg px-4 py-2">
              <div className="flex items-center space-x-2">
                <div className="flex space-x-1">
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }} />
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }} />
                </div>
                <span className="text-sm text-gray-500">AI正在思考...</span>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* 快捷问题 */}
      {messages.length === 0 && (
        <div className="px-4 pb-2">
          <div className="flex flex-wrap gap-2">
            {quickQuestions.map((question, idx) => (
              <button
                key={idx}
                onClick={() => handleQuickQuestion(question)}
                className="text-xs px-3 py-1.5 bg-blue-50 text-blue-600 rounded-full
                         hover:bg-blue-100 transition-colors"
              >
                {question}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 输入区域 */}
      <div className="border-t border-gray-200 p-4 bg-white">
        <div className="flex items-end space-x-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleTextareaChange}
            onKeyPress={handleKeyPress}
            placeholder="输入你的问题..."
            className="flex-1 resize-none border border-gray-300 rounded-lg px-3 py-2
                     focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                     placeholder-gray-400 text-sm"
            rows={1}
            disabled={isLoading}
          />
          {isLoading ? (
            <button
              onClick={stopGeneration}
              className="px-4 py-2 bg-red-500 text-white rounded-lg
                       hover:bg-red-600 transition-colors flex-shrink-0"
              title="停止生成"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
              </svg>
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className="px-4 py-2 bg-blue-500 text-white rounded-lg
                       hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed
                       transition-colors flex-shrink-0"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            </button>
          )}
        </div>

        {/* 底部提示 */}
        <div className="mt-2 text-xs text-gray-400 text-center">
          {isLoading ? '点击停止按钮可终止生成' : '按 Enter 发送，Shift + Enter 换行'}
        </div>
      </div>
    </div>
  );
};

export default ChatInterface;
