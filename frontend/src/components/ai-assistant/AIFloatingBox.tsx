import React, { useState, useEffect, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';
import ChatInterface from './ChatInterface';
import ConversationHistory from './ConversationHistory';

// 权限检查
const AI_ASSISTANT_ROLES = ['admin', 'configurator', 'viewer'];

// 不显示AI助手的页面路径
const EXCLUDED_PATHS = ['/chat', '/ws/*/chat'];

interface AIFloatingBoxProps {
  className?: string;
}

const AIFloatingBox: React.FC<AIFloatingBoxProps> = ({ className = '' }) => {
  const location = useLocation();
  const { user } = useAuthStore();
  const {
    isOpen,
    toggleBox,
    updateContext,
    currentContext
  } = useAIAssistantStore();

  const [activeTab, setActiveTab] = useState<'chat' | 'history'>('chat');

  // 检查用户权限
  const hasPermission = user && AI_ASSISTANT_ROLES.includes(user.role);

  // 检查当前页面是否在排除列表中
  const isExcludedPage = useCallback(() => {
    const pathname = location.pathname;
    return EXCLUDED_PATHS.some(pattern => {
      if (pattern.includes('*')) {
        const regex = new RegExp(pattern.replace('*', '.*'));
        return regex.test(pathname);
      }
      return pathname === pattern;
    });
  }, [location.pathname]);

  // 自动识别当前页面上下文
  useEffect(() => {
    if (!hasPermission || isExcludedPage()) return;

    const context = analyzePageContext(location.pathname);
    updateContext(context);
  }, [location.pathname, hasPermission, updateContext, isExcludedPage]);

  // 分析页面上下文
  const analyzePageContext = (pathname: string) => {
    // 系统配置页面
    if (pathname.includes('/system/knowledge-base')) {
      return { page: 'knowledge-base', module: 'system', subModule: 'knowledge-base', title: '知识库管理' };
    }
    if (pathname.includes('/system/datasources')) {
      return { page: 'datasources', module: 'system', subModule: 'datasources', title: '数据源管理' };
    }
    if (pathname.includes('/system/metadata')) {
      return { page: 'metadata', module: 'system', subModule: 'metadata', title: '表元数据' };
    }
    if (pathname.includes('/system/relations')) {
      return { page: 'relations', module: 'system', subModule: 'relations', title: '表关联' };
    }
    if (pathname.includes('/system/templates')) {
      return { page: 'templates', module: 'system', subModule: 'templates', title: 'SQL模板' };
    }
    if (pathname.includes('/system/terms')) {
      return { page: 'terms', module: 'system', subModule: 'terms', title: '业务术语' };
    }
    if (pathname.includes('/system/models')) {
      return { page: 'models', module: 'system', subModule: 'models', title: '模型中心' };
    }
    if (pathname.includes('/system/mcp-agent')) {
      return { page: 'mcp-agent', module: 'system', subModule: 'mcp-agent', title: 'MCP/Agent配置' };
    }
    if (pathname.includes('/system/workflows')) {
      return { page: 'workflows', module: 'system', subModule: 'workflows', title: '工作流配置' };
    }
    if (pathname.includes('/system/workflow-editor')) {
      return { page: 'workflow-editor', module: 'system', subModule: 'workflow-editor', title: '工作流编排' };
    }
    if (pathname.includes('/system/prompts')) {
      return { page: 'prompts', module: 'system', subModule: 'prompts', title: 'Prompt管理' };
    }
    if (pathname.includes('/system/scheduled-tasks')) {
      return { page: 'scheduled-tasks', module: 'system', subModule: 'scheduled-tasks', title: '定时任务' };
    }
    if (pathname.includes('/system/notification-channels')) {
      return { page: 'notification-channels', module: 'system', subModule: 'notification-channels', title: '通知渠道' };
    }
    if (pathname.includes('/system/report-templates')) {
      return { page: 'report-templates', module: 'system', subModule: 'report-templates', title: '报告模板' };
    }
    if (pathname.includes('/system/users')) {
      return { page: 'users', module: 'system', subModule: 'users', title: '用户管理' };
    }
    if (pathname.includes('/system/settings')) {
      return { page: 'settings', module: 'system', subModule: 'settings', title: '系统设置' };
    }
    if (pathname.includes('/system')) {
      return { page: 'system', module: 'system', title: '系统配置' };
    }

    // 工作空间页面
    if (pathname.includes('/chat')) {
      return { page: 'chat', module: 'chat', title: '数据查询' };
    }
    if (pathname.includes('/dashboard')) {
      return { page: 'dashboard', module: 'dashboard', title: '仪表盘' };
    }
    if (pathname.includes('/history')) {
      return { page: 'history', module: 'history', title: '查询历史' };
    }
    if (pathname.includes('/page')) {
      return { page: 'page', module: 'dashboard', title: '数据看板' };
    }

    // 其他页面
    if (pathname.includes('/profile')) {
      return { page: 'profile', module: 'profile', title: '个人资料' };
    }
    if (pathname.includes('/workspaces')) {
      return { page: 'workspaces', module: 'workspace', title: '工作空间管理' };
    }

    return { page: 'unknown', module: 'unknown', title: '未知页面' };
  };

  // 如果没有权限或在排除页面，不渲染
  if (!hasPermission || isExcludedPage()) {
    return null;
  }

  return (
    <div
      className={`fixed bottom-6 right-6 z-[9999] ${className}`}
    >
      {/* 悬浮按钮 */}
      {!isOpen && (
        <button
          onClick={toggleBox}
          className="w-14 h-14 bg-blue-500 rounded-full shadow-lg
                     hover:bg-blue-600 transition-all duration-300
                     flex items-center justify-center
                     hover:scale-110 active:scale-95"
          title="AI助手"
        >
          <svg
            className="w-8 h-8 text-white"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
            />
          </svg>
        </button>
      )}

      {/* 对话框 */}
      {isOpen && (
        <div
          className="w-96 h-[600px] bg-white rounded-lg shadow-xl border border-gray-200
                     flex flex-col overflow-hidden"
        >
          {/* 标题栏 */}
          <div className="flex items-center justify-between p-4 bg-blue-500 text-white select-none">
            <div className="flex items-center space-x-2">
              <svg
                className="w-6 h-6"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
                />
              </svg>
              <h3 className="text-lg font-semibold">AI助手</h3>
            </div>
            <button
              onClick={toggleBox}
              className="text-white hover:text-gray-200 transition-colors"
            >
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Tab导航 */}
          <div className="flex border-b border-gray-200 bg-gray-50">
            <button
              onClick={() => setActiveTab('chat')}
              className={`flex-1 px-4 py-3 text-sm font-medium transition-colors
                ${activeTab === 'chat'
                  ? 'text-blue-600 border-b-2 border-blue-600 bg-white'
                  : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
            >
              💬 对话
            </button>
            <button
              onClick={() => setActiveTab('history')}
              className={`flex-1 px-4 py-3 text-sm font-medium transition-colors
                ${activeTab === 'history'
                  ? 'text-blue-600 border-b-2 border-blue-600 bg-white'
                  : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
            >
              📜 历史
            </button>
          </div>

          {/* 内容区域 */}
          <div className="flex-1 overflow-hidden">
            {activeTab === 'chat' && <ChatInterface />}
            {activeTab === 'history' && <ConversationHistory />}
          </div>
        </div>
      )}
    </div>
  );
};

export default AIFloatingBox;
