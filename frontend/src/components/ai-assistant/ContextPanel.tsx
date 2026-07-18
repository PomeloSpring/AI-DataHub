import React, { useEffect, useState } from 'react';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';

interface PageContext {
  page: string;
  module: string;
  subModule?: string;
}

interface ContextInfo {
  title: string;
  description: string;
  icon: string;
}

// 页面上下文信息映射
const contextInfoMap: Record<string, ContextInfo> = {
  datasource: {
    title: '数据源管理',
    description: '配置和管理数据库连接',
    icon: '🗄️'
  },
  agent: {
    title: 'Agent管理',
    description: '配置和管理AI代理',
    icon: '🤖'
  },
  workflow: {
    title: '工作流配置',
    description: '配置和管理工作流',
    icon: '⚙️'
  },
  'scheduled-tasks': {
    title: '定时任务',
    description: '配置和管理定时任务',
    icon: '⏰'
  },
  chat: {
    title: '数据查询',
    description: '自然语言数据查询',
    icon: '💬'
  },
  dashboard: {
    title: '仪表盘',
    description: '数据可视化仪表盘',
    icon: '📊'
  },
  history: {
    title: '查询历史',
    description: '查看历史查询记录',
    icon: '📜'
  },
  unknown: {
    title: '未知页面',
    description: '当前页面信息',
    icon: '❓'
  }
};

// 常见问题映射
const commonQuestionsMap: Record<string, string[]> = {
  datasource: [
    '如何配置MySQL数据源？',
    '数据源连接失败怎么办？',
    '如何测试数据源连接？',
    '支持哪些数据库类型？'
  ],
  agent: [
    '什么是Agent？',
    '如何配置Agent？',
    'Agent的工作原理是什么？',
    '如何优化Agent性能？'
  ],
  workflow: [
    '如何创建工作流？',
    '工作流节点有哪些类型？',
    '如何调试工作流？',
    '工作流支持并行执行吗？'
  ],
  'scheduled-tasks': [
    '如何创建定时任务？',
    '定时任务支持哪些触发方式？',
    '如何查看任务执行日志？',
    '任务执行失败怎么办？'
  ],
  chat: [
    '如何提高查询准确性？',
    '查询结果为空怎么办？',
    '如何查看生成的SQL？',
    '支持哪些查询类型？'
  ],
  dashboard: [
    '如何创建仪表盘？',
    '如何添加图表？',
    '如何设置自动刷新？',
    '如何分享仪表盘？'
  ]
};

// 快捷操作映射
const quickActionsMap: Record<string, Array<{ label: string; icon: string; action: string }>> = {
  datasource: [
    { label: '新建数据源', icon: '➕', action: 'create' },
    { label: '测试连接', icon: '🔌', action: 'test' },
    { label: '查看文档', icon: '📖', action: 'docs' }
  ],
  agent: [
    { label: '新建Agent', icon: '➕', action: 'create' },
    { label: '配置提示词', icon: '📝', action: 'prompt' },
    { label: '查看日志', icon: '📋', action: 'logs' }
  ],
  workflow: [
    { label: '新建工作流', icon: '➕', action: 'create' },
    { label: '导入工作流', icon: '📥', action: 'import' },
    { label: '查看示例', icon: '💡', action: 'examples' }
  ]
};

const ContextPanel: React.FC = () => {
  const { currentContext, sendMessage } = useAIAssistantStore();
  const [contextInfo, setContextInfo] = useState<ContextInfo | null>(null);

  useEffect(() => {
    if (currentContext?.module) {
      setContextInfo(contextInfoMap[currentContext.module] || contextInfoMap.unknown);
    }
  }, [currentContext]);

  const module = currentContext?.module || 'unknown';
  const commonQuestions = commonQuestionsMap[module] || [];
  const quickActions = quickActionsMap[module] || [];

  const handleQuestionClick = (question: string) => {
    sendMessage(question, currentContext);
  };

  const handleActionClick = (action: string) => {
    // TODO: 实现快捷操作
    console.log('Quick action:', action);
  };

  return (
    <div className="h-full overflow-y-auto">
      {/* 当前页面信息 */}
      <div className="p-4 border-b border-gray-100">
        <div className="flex items-center space-x-3">
          <span className="text-3xl">{contextInfo?.icon || '❓'}</span>
          <div>
            <h4 className="font-semibold text-gray-800">
              {contextInfo?.title || '未知页面'}
            </h4>
            <p className="text-sm text-gray-500">
              {contextInfo?.description || ''}
            </p>
          </div>
        </div>

        {/* 页面路径 */}
        {currentContext && (
          <div className="mt-3 flex items-center text-xs text-gray-400">
            <span>{currentContext.module}</span>
            {currentContext.subModule && (
              <>
                <span className="mx-1">/</span>
                <span>{currentContext.subModule}</span>
              </>
            )}
          </div>
        )}
      </div>

      {/* 快捷操作 */}
      {quickActions.length > 0 && (
        <div className="p-4 border-b border-gray-100">
          <h5 className="text-sm font-medium text-gray-700 mb-3">快捷操作</h5>
          <div className="grid grid-cols-3 gap-2">
            {quickActions.map((action, idx) => (
              <button
                key={idx}
                onClick={() => handleActionClick(action.action)}
                className="flex flex-col items-center p-3 rounded-lg
                         bg-gray-50 hover:bg-gray-100 transition-colors"
              >
                <span className="text-xl mb-1">{action.icon}</span>
                <span className="text-xs text-gray-600">{action.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 常见问题 */}
      {commonQuestions.length > 0 && (
        <div className="p-4 border-b border-gray-100">
          <h5 className="text-sm font-medium text-gray-700 mb-3">常见问题</h5>
          <div className="space-y-2">
            {commonQuestions.map((question, idx) => (
              <button
                key={idx}
                onClick={() => handleQuestionClick(question)}
                className="w-full text-left px-3 py-2 text-sm text-gray-600
                         bg-gray-50 rounded-lg hover:bg-blue-50 hover:text-blue-600
                         transition-colors flex items-center space-x-2"
              >
                <span className="text-gray-400">❓</span>
                <span>{question}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 相关文档 */}
      <div className="p-4">
        <h5 className="text-sm font-medium text-gray-700 mb-3">相关文档</h5>
        <div className="space-y-2">
          <a
            href="#"
            className="block px-3 py-2 text-sm text-gray-600 bg-gray-50 rounded-lg
                     hover:bg-blue-50 hover:text-blue-600 transition-colors"
          >
            📖 {contextInfo?.title || '功能'} 使用指南
          </a>
          <a
            href="#"
            className="block px-3 py-2 text-sm text-gray-600 bg-gray-50 rounded-lg
                     hover:bg-blue-50 hover:text-blue-600 transition-colors"
          >
            📋 API接口文档
          </a>
          <a
            href="#"
            className="block px-3 py-2 text-sm text-gray-600 bg-gray-50 rounded-lg
                     hover:bg-blue-50 hover:text-blue-600 transition-colors"
          >
            💡 最佳实践
          </a>
        </div>
      </div>

      {/* 提示信息 */}
      <div className="p-4">
        <div className="bg-blue-50 rounded-lg p-3">
          <div className="flex items-start space-x-2">
            <span className="text-blue-500">💡</span>
            <div className="text-xs text-blue-700">
              <p className="font-medium mb-1">提示</p>
              <p>切换到「对话」标签，可以向AI助手提问关于{contextInfo?.title || '当前页面'}的问题。</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ContextPanel;
