import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';
import { aiAssistantExecutor } from '../../utils/aiAssistantExecutor';
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
  const navigate = useNavigate();
  const { user } = useAuthStore();
  const {
    isOpen,
    toggleBox,
    updateContext,
    currentContext
  } = useAIAssistantStore();

  const [activeTab, setActiveTab] = useState<'chat' | 'history'>('chat');
  const boxRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<HTMLDivElement>(null);

  // 拖动状态
  const [isDragging, setIsDragging] = useState(false);
  const [hasDragged, setHasDragged] = useState(false);  // 是否发生了实际拖动
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [dragStartPos, setDragStartPos] = useState({ x: 0, y: 0 });
  const [boxStartPos, setBoxStartPos] = useState({ x: 0, y: 0 });

  // 拖动阈值（像素），超过这个距离才算拖动
  const DRAG_THRESHOLD = 5;

  // 检查用户权限
  const hasPermission = user && AI_ASSISTANT_ROLES.includes(user.role);

  // 设置导航函数
  useEffect(() => {
    aiAssistantExecutor.setNavigate(navigate);
  }, [navigate]);

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

  // 初始化位置（右下角）
  useEffect(() => {
    const initPosition = () => {
      const savedPosition = localStorage.getItem('ai-assistant-position');
      if (savedPosition) {
        try {
          const pos = JSON.parse(savedPosition);
          // 验证保存的位置是否在窗口范围内
          if (pos.x >= 0 && pos.x < window.innerWidth && pos.y >= 0 && pos.y < window.innerHeight) {
            setPosition(pos);
            return;
          }
        } catch {
          // 解析失败，使用默认位置
        }
      }
      // 默认位置：右下角，留出边距
      setPosition({
        x: window.innerWidth - 80,
        y: window.innerHeight - 80
      });
    };

    initPosition();

    // 监听窗口大小变化，重新调整位置
    const handleResize = () => {
      setPosition(prev => ({
        x: Math.min(prev.x, window.innerWidth - 80),
        y: Math.min(prev.y, window.innerHeight - 80)
      }));
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // 保存位置到localStorage
  useEffect(() => {
    if (position.x !== 0 || position.y !== 0) {
      localStorage.setItem('ai-assistant-position', JSON.stringify(position));
    }
  }, [position]);

  // 自动识别当前页面上下文
  useEffect(() => {
    if (!hasPermission || isExcludedPage()) return;

    const context = analyzePageContext(location.pathname);
    updateContext(context);
  }, [location.pathname, hasPermission, updateContext, isExcludedPage]);

  // 拖动开始
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();

    setIsDragging(true);
    setHasDragged(false);
    setDragStartPos({ x: e.clientX, y: e.clientY });
    setBoxStartPos({ ...position });
  }, [position]);

  // 处理点击（区分单击和拖动）
  const handleClick = useCallback((e: React.MouseEvent) => {
    // 如果发生了拖动，不触发打开操作
    if (hasDragged) {
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    // 否则打开对话框
    toggleBox();
  }, [hasDragged, toggleBox]);

  // 拖动中
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;

      const deltaX = e.clientX - dragStartPos.x;
      const deltaY = e.clientY - dragStartPos.y;

      // 检查是否超过拖动阈值
      if (Math.abs(deltaX) > DRAG_THRESHOLD || Math.abs(deltaY) > DRAG_THRESHOLD) {
        setHasDragged(true);
      }

      let newX = boxStartPos.x + deltaX;
      let newY = boxStartPos.y + deltaY;

      // 限制在窗口范围内
      const minX = isOpen ? 200 : 30;
      const maxX = window.innerWidth - (isOpen ? 200 : 30);
      const minY = 30;
      const maxY = window.innerHeight - (isOpen ? 300 : 30);

      newX = Math.max(minX, Math.min(newX, maxX));
      newY = Math.max(minY, Math.min(newY, maxY));

      setPosition({ x: newX, y: newY });
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      // 延迟重置hasDragged，确保click事件能正确判断
      setTimeout(() => {
        setHasDragged(false);
      }, 100);
    };

    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragStartPos, boxStartPos, isOpen]);

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
      ref={boxRef}
      className={`fixed z-[9999] ${className}`}
      style={{
        left: `${position.x}px`,
        top: `${position.y}px`,
        transform: 'translate(-50%, -50%)',
        transition: isDragging ? 'none' : 'left 0.1s ease, top 0.1s ease'
      }}
    >
      {/* 悬浮按钮 */}
      {!isOpen && (
        <button
          onClick={handleClick}
          onMouseDown={handleMouseDown}
          className="w-14 h-14 bg-blue-500 rounded-full shadow-lg
                     hover:bg-blue-600 transition-all duration-300
                     flex items-center justify-center
                     hover:scale-110 active:scale-95 cursor-grab active:cursor-grabbing"
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
          style={{ transform: 'translate(-50%, -50%)' }}
        >
          {/* 标题栏 - 可拖动区域 */}
          <div
            ref={dragRef}
            onMouseDown={handleMouseDown}
            className="flex items-center justify-between p-4 bg-blue-500 text-white cursor-grab active:cursor-grabbing select-none"
          >
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
