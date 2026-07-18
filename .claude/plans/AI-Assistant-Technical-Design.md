# AI助手对话框技术设计文档

## 1. 项目概述

### 1.1 项目背景

AI-DataHub是一个自然语言商业智能平台，采用多代理架构。为了帮助用户更好地使用系统功能，需要开发一个独立的AI助手对话框，提供配置辅助、文档查询和问题解答功能。

### 1.2 核心需求

- **悬浮对话框**：可在任何页面显示，不影响用户操作
- **权限控制**：基于用户角色，只有特定权限的用户可以看到
- **功能范围**：配置辅助 + 文档查询 + 问题解答（不改代码，只辅助配置）
- **知识库**：项目文档 + 配置指南 + 数据库元数据 + 用户操作记录 + 自定义文档
- **GraphRAG + Neo4j**：构建多模态知识图谱，提供智能检索
- **管理功能**：知识库管理页面，支持向量化更新

### 1.3 技术选型

| 组件 | 技术方案 | 说明 |
|------|----------|------|
| 后端框架 | FastAPI | 与主项目一致 |
| 前端框架 | React 18 + TypeScript | 与主项目一致 |
| 向量数据库 | Doris HNSW | 复用现有基础设施 |
| 图数据库 | Neo4j | 存储知识图谱关系 |
| RAG框架 | GraphRAG | 微软GraphRAG + Neo4j |
| 状态管理 | Zustand | 与主项目一致 |
| UI组件 | Tailwind CSS | 与主项目一致 |

## 2. 系统架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend (React)                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              AI Assistant Floating Box               │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐ │   │
│  │  │ Chat UI │  │ Context │  │ Knowledge Manager   │ │   │
│  │  └─────────┘  └─────────┘  └─────────────────────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Backend (FastAPI)                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              AI Assistant API Service                │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐ │   │
│  │  │ Auth    │  │ Chat    │  │ Knowledge Service   │ │   │
│  │  └─────────┘  └─────────┘  └─────────────────────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
│                              │                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              GraphRAG Engine                         │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐ │   │
│  │  │ Neo4j   │  │ Vector  │  │ LLM Service        │ │   │
│  │  └─────────┘  └─────────┘  └─────────────────────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 模块划分

```
backend/
├── api/
│   └── ai_assistant.py          # AI助手API端点
├── services/
│   └── ai_assistant_service.py  # AI助手业务逻辑
├── rag/
│   ├── graph_rag/               # GraphRAG模块
│   │   ├── __init__.py
│   │   ├── neo4j_store.py       # Neo4j存储层
│   │   ├── graph_builder.py     # 知识图谱构建
│   │   ├── graph_retriever.py   # 图检索引擎
│   │   └── graph_embeddings.py  # 图嵌入生成
│   └── knowledge/               # 知识库管理
│       ├── __init__.py
│       ├── document_loader.py   # 文档加载器
│       ├── chunk_processor.py   # 文档分块处理
│       └── vector_indexer.py    # 向量索引构建
├── config/
│   └── agents/
│       └── ai_assistant/        # AI助手代理配置
│           ├── skill.yaml
│           └── system.md
└── models/
    └── ai_assistant.py          # 数据模型

frontend/
├── components/
│   └── ai-assistant/            # AI助手组件
│       ├── AIFloatingBox.tsx    # 悬浮框主组件
│       ├── ChatInterface.tsx    # 聊天界面
│       ├── ContextPanel.tsx     # 上下文面板
│       ├── KnowledgeManager.tsx # 知识库管理
│       └── PermissionGuard.tsx  # 权限守卫
├── stores/
│   └── aiAssistantStore.ts      # 状态管理
├── api/
│   └── aiAssistant.ts           # API客户端
└── hooks/
    └── useAIAssistant.ts        # 自定义Hook
```

## 3. GraphRAG + Neo4j 多模态知识图谱设计

### 3.1 知识图谱架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Knowledge Graph Schema                   │
├─────────────────────────────────────────────────────────────┤
│  Node Types:                                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Document    │  │ Concept     │  │ Configuration      │ │
│  │ (文档节点)   │  │ (概念节点)   │  │ (配置节点)         │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Table       │  │ Column      │  │ Business Term      │ │
│  │ (表节点)     │  │ (字段节点)   │  │ (业务术语节点)      │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Agent       │  │ Workflow    │  │ User Action        │ │
│  │ (代理节点)   │  │ (工作流节点) │  │ (用户操作节点)      │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  Edge Types:                                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ CONTAINS    │  │ RELATES_TO  │  │ DEPENDS_ON         │ │
│  │ (包含关系)   │  │ (关联关系)   │  │ (依赖关系)         │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ USES        │  │ MAPS_TO     │  │ SIMILAR_TO         │ │
│  │ (使用关系)   │  │ (映射关系)   │  │ (相似关系)         │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Neo4j Schema设计

```cypher
// 文档节点
CREATE (d:Document {
  id: 'doc_001',
  title: '数据源配置指南',
  content: '...',
  doc_type: 'guide',
  source: 'wiki',
  created_at: datetime(),
  updated_at: datetime(),
  embedding: [0.1, 0.2, ...]  // 768维向量
})

// 概念节点
CREATE (c:Concept {
  id: 'concept_001',
  name: '数据源',
  description: '数据库连接配置',
  category: 'configuration',
  embedding: [0.1, 0.2, ...]
})

// 配置节点
CREATE (cfg:Configuration {
  id: 'config_001',
  name: 'MySQL数据源配置',
  config_type: 'datasource',
  parameters: {...},
  embedding: [0.1, 0.2, ...]
})

// 表节点（复用现有元数据）
CREATE (t:Table {
  id: 'table_orders',
  name: 'orders',
  comment: '订单表',
  business_desc: '存储所有订单信息',
  datasource_id: 1,
  embedding: [0.1, 0.2, ...]
})

// 字段节点
CREATE (col:Column {
  id: 'col_orders_id',
  name: 'id',
  table_name: 'orders',
  data_type: 'bigint',
  comment: '订单ID',
  business_desc: '唯一标识符',
  is_key: 'true',
  embedding: [0.1, 0.2, ...]
})

// 业务术语节点
CREATE (bt:BusinessTerm {
  id: 'term_gmv',
  name_cn: 'GMV',
  name_en: 'Gross Merchandise Volume',
  aliases: '成交额,交易额',
  calculation: 'SUM(order_amount)',
  description: '总成交金额',
  embedding: [0.1, 0.2, ...]
})

// 代理节点
CREATE (a:Agent {
  id: 'agent_data_analysis',
  name: 'data_analysis',
  display_name: '数据分析',
  description: '检索数据库元数据、生成SQL、执行查询',
  is_active: true,
  embedding: [0.1, 0.2, ...]
})

// 工作流节点
CREATE (wf:Workflow {
  id: 'workflow_001',
  name: '销售数据分析',
  description: '...',
  steps: [...],
  embedding: [0.1, 0.2, ...]
})

// 用户操作节点
CREATE (ua:UserAction {
  id: 'action_001',
  user_id: 123,
  action_type: 'configure_datasource',
  target: 'mysql_production',
  timestamp: datetime(),
  success: true,
  details: {...}
})
```

### 3.3 关系设计

```cypher
// 文档包含概念
(d:Document)-[:CONTAINS]->(c:Concept)

// 概念关联配置
(c:Concept)-[:RELATES_TO]->(cfg:Configuration)

// 表包含字段
(t:Table)-[:HAS_COLUMN]->(col:Column)

// 业务术语映射到字段
(bt:BusinessTerm)-[:MAPS_TO]->(col:Column)

// 代理使用表
(a:Agent)-[:USES]->(t:Table)

// 工作流依赖代理
(wf:Workflow)-[:DEPENDS_ON]->(a:Agent)

// 文档相似关系（基于向量相似度）
(d1:Document)-[:SIMILAR_TO {score: 0.85}]->(d2:Document)

// 用户操作记录
(ua:UserAction)-[:PERFORMED_BY]->(u:User)
(ua:UserAction)-[:TARGETS]->(cfg:Configuration)
```

### 3.4 GraphRAG检索流程

```python
# GraphRAG检索流程
def graph_rag_retrieve(query: str, user_context: dict) -> list[dict]:
    """
    1. 向量检索：找到最相关的文档/概念节点
    2. 图遍历：从相关节点出发，遍历关联节点
    3. 上下文组装：将检索结果组装成上下文
    4. LLM生成：基于上下文生成回答
    """
    
    # Step 1: 向量检索入口节点
    query_embedding = generate_embedding(query)
    entry_nodes = neo4j_store.vector_search(
        embedding=query_embedding,
        node_types=["Document", "Concept", "Configuration"],
        limit=10
    )
    
    # Step 2: 图遍历扩展
    expanded_context = []
    for node in entry_nodes:
        # 遍历2跳内的关联节点
        related = neo4j_store.traverse(
            start_node=node["id"],
            max_depth=2,
            relationship_types=["CONTAINS", "RELATES_TO", "HAS_COLUMN", "MAPS_TO"]
        )
        expanded_context.extend(related)
    
    # Step 3: 上下文组装
    context = assemble_context(entry_nodes, expanded_context, user_context)
    
    # Step 4: LLM生成
    response = llm_generate(query, context)
    
    return response
```

### 3.5 知识图谱构建流程

```python
# 知识图谱构建流程
def build_knowledge_graph():
    """
    1. 文档加载：从/docs目录加载文档
    2. 文档分块：将文档分成适当大小的块
    3. 实体提取：使用LLM提取实体和关系
    4. 向量生成：为每个实体生成向量嵌入
    5. 图谱构建：将实体和关系写入Neo4j
    """
    
    # Step 1: 加载文档
    documents = load_documents("/docs")
    
    # Step 2: 文档分块
    chunks = chunk_documents(documents, chunk_size=512, overlap=50)
    
    # Step 3: 实体提取
    entities_relations = []
    for chunk in chunks:
        # 使用LLM提取实体和关系
        extracted = llm_extract_entities(chunk)
        entities_relations.extend(extracted)
    
    # Step 4: 向量生成
    for entity in entities_relations:
        entity["embedding"] = generate_embedding(entity["description"])
    
    # Step 5: 写入Neo4j
    neo4j_store.batch_insert(entities_relations)
```

## 4. 前端悬浮框设计

### 4.1 悬浮框组件架构

```typescript
// AIFloatingBox.tsx
import React, { useState, useEffect } from 'react';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';
import { useAuthStore } from '../../stores/authStore';
import ChatInterface from './ChatInterface';
import ContextPanel from './ContextPanel';
import KnowledgeManager from './KnowledgeManager';
import PermissionGuard from './PermissionGuard';

const AIFloatingBox: React.FC = () => {
  const { user } = useAuthStore();
  const { 
    isOpen, 
    toggleBox, 
    currentContext,
    messages,
    sendMessage 
  } = useAIAssistantStore();
  
  const [activeTab, setActiveTab] = useState<'chat' | 'context' | 'knowledge'>('chat');
  
  // 检查用户权限
  if (!user || !hasAIAssistantPermission(user.role)) {
    return null;
  }
  
  return (
    <PermissionGuard requiredRole="ai_assistant">
      <div className="fixed bottom-4 right-4 z-50">
        {/* 悬浮按钮 */}
        <button
          onClick={toggleBox}
          className="w-14 h-14 bg-blue-500 rounded-full shadow-lg 
                     hover:bg-blue-600 transition-all duration-300
                     flex items-center justify-center"
        >
          <AIIcon className="w-8 h-8 text-white" />
        </button>
        
        {/* 对话框 */}
        {isOpen && (
          <div className="absolute bottom-16 right-0 w-96 h-[600px] 
                         bg-white rounded-lg shadow-xl border
                         flex flex-col">
            {/* 标题栏 */}
            <div className="flex items-center justify-between p-4 border-b">
              <h3 className="text-lg font-semibold">AI助手</h3>
              <button onClick={toggleBox} className="text-gray-500">
                <CloseIcon />
              </button>
            </div>
            
            {/* Tab导航 */}
            <div className="flex border-b">
              <TabButton 
                active={activeTab === 'chat'} 
                onClick={() => setActiveTab('chat')}
              >
                对话
              </TabButton>
              <TabButton 
                active={activeTab === 'context'} 
                onClick={() => setActiveTab('context')}
              >
                上下文
              </TabButton>
              <TabButton 
                active={activeTab === 'knowledge'} 
                onClick={() => setActiveTab('knowledge')}
              >
                知识库
              </TabButton>
            </div>
            
            {/* 内容区域 */}
            <div className="flex-1 overflow-hidden">
              {activeTab === 'chat' && <ChatInterface />}
              {activeTab === 'context' && <ContextPanel />}
              {activeTab === 'knowledge' && <KnowledgeManager />}
            </div>
          </div>
        )}
      </div>
    </PermissionGuard>
  );
};

export default AIFloatingBox;
```

### 4.2 聊天界面组件

```typescript
// ChatInterface.tsx
import React, { useState, useRef, useEffect } from 'react';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  context?: any;
}

const ChatInterface: React.FC = () => {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { messages, sendMessage, isLoading, currentContext } = useAIAssistantStore();
  
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };
  
  useEffect(scrollToBottom, [messages]);
  
  const handleSend = async () => {
    if (!input.trim() || isLoading) return;
    
    await sendMessage(input, currentContext);
    setInput('');
  };
  
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };
  
  return (
    <div className="flex flex-col h-full">
      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        {isLoading && <LoadingIndicator />}
        <div ref={messagesEndRef} />
      </div>
      
      {/* 输入区域 */}
      <div className="border-t p-4">
        <div className="flex items-center space-x-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="输入你的问题..."
            className="flex-1 resize-none border rounded-lg p-2 
                       focus:outline-none focus:ring-2 focus:ring-blue-500"
            rows={2}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            className="px-4 py-2 bg-blue-500 text-white rounded-lg
                       hover:bg-blue-600 disabled:opacity-50"
          >
            发送
          </button>
        </div>
        
        {/* 快捷操作 */}
        <div className="mt-2 flex flex-wrap gap-2">
          <QuickAction 
            label="如何配置数据源？" 
            onClick={() => setInput('如何配置数据源？')} 
          />
          <QuickAction 
            label="Agent是什么？" 
            onClick={() => setInput('Agent是什么？')} 
          />
          <QuickAction 
            label="查看当前页面帮助" 
            onClick={() => sendMessage('当前页面的帮助信息', currentContext)} 
          />
        </div>
      </div>
    </div>
  );
};

export default ChatInterface;
```

### 4.3 上下文感知组件

```typescript
// ContextPanel.tsx
import React, { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';

interface PageContext {
  page: string;
  module: string;
  subModule?: string;
  params?: Record<string, any>;
}

const ContextPanel: React.FC = () => {
  const location = useLocation();
  const { currentContext, updateContext } = useAIAssistantStore();
  
  // 自动识别当前页面上下文
  useEffect(() => {
    const context = analyzePageContext(location.pathname);
    updateContext(context);
  }, [location.pathname]);
  
  const analyzePageContext = (pathname: string): PageContext => {
    // 解析路由路径，识别当前模块
    const segments = pathname.split('/').filter(Boolean);
    
    if (pathname.includes('/admin/datasource')) {
      return { page: 'datasource', module: 'admin', subModule: 'datasource' };
    }
    if (pathname.includes('/admin/agent')) {
      return { page: 'agent', module: 'admin', subModule: 'agent' };
    }
    if (pathname.includes('/admin/workflow')) {
      return { page: 'workflow', module: 'admin', subModule: 'workflow' };
    }
    if (pathname.includes('/chat')) {
      return { page: 'chat', module: 'chat' };
    }
    if (pathname.includes('/dashboard')) {
      return { page: 'dashboard', module: 'dashboard' };
    }
    
    return { page: 'unknown', module: 'unknown' };
  };
  
  return (
    <div className="p-4 space-y-4">
      <h4 className="font-semibold text-gray-700">当前上下文</h4>
      
      {/* 当前页面信息 */}
      <div className="bg-gray-50 rounded-lg p-3">
        <div className="text-sm text-gray-500">当前页面</div>
        <div className="font-medium">{getPageName(currentContext.page)}</div>
      </div>
      
      {/* 相关配置 */}
      <div className="space-y-2">
        <h5 className="text-sm font-medium text-gray-600">相关配置</h5>
        {getRelatedConfigs(currentContext).map((config, idx) => (
          <RelatedConfigCard key={idx} config={config} />
        ))}
      </div>
      
      {/* 常见问题 */}
      <div className="space-y-2">
        <h5 className="text-sm font-medium text-gray-600">常见问题</h5>
        {getCommonQuestions(currentContext).map((q, idx) => (
          <QuickQuestion key={idx} question={q} />
        ))}
      </div>
    </div>
  );
};

export default ContextPanel;
```

## 5. 权限控制设计

### 5.1 权限模型

```python
# 权限角色定义
AI_ASSISTANT_ROLES = {
    "admin": {
        "can_access": True,
        "can_manage_knowledge": True,
        "can_configure": True,
        "can_view_logs": True
    },
    "configurator": {
        "can_access": True,
        "can_manage_knowledge": False,
        "can_configure": True,
        "can_view_logs": False
    },
    "viewer": {
        "can_access": True,
        "can_manage_knowledge": False,
        "can_configure": False,
        "can_view_logs": False
    },
    "user": {
        "can_access": False,
        "can_manage_knowledge": False,
        "can_configure": False,
        "can_view_logs": False
    }
}
```

### 5.2 权限检查中间件

```python
# backend/common/ai_assistant_auth.py
from functools import wraps
from fastapi import HTTPException, Depends
from backend.common.auth import get_current_user

def require_ai_assistant_permission(permission: str = "can_access"):
    """检查AI助手权限的装饰器"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            user = await get_current_user()
            role = user.get("role", "user")
            
            from backend.config.ai_assistant_config import AI_ASSISTANT_ROLES
            role_config = AI_ASSISTANT_ROLES.get(role, {})
            
            if not role_config.get(permission, False):
                raise HTTPException(
                    status_code=403,
                    detail="没有访问AI助手的权限"
                )
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# 使用示例
@router.post("/chat")
@require_ai_assistant_permission("can_access")
async def chat(request: ChatRequest):
    # 处理聊天请求
    pass

@router.post("/knowledge/update")
@require_ai_assistant_permission("can_manage_knowledge")
async def update_knowledge(request: UpdateRequest):
    # 更新知识库
    pass
```

### 5.3 前端权限守卫

```typescript
// PermissionGuard.tsx
import React from 'react';
import { useAuthStore } from '../../stores/authStore';

interface PermissionGuardProps {
  requiredRole: string;
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

const PermissionGuard: React.FC<PermissionGuardProps> = ({ 
  requiredRole, 
  children, 
  fallback = null 
}) => {
  const { user } = useAuthStore();
  
  const hasPermission = (role: string): boolean => {
    const roleHierarchy: Record<string, string[]> = {
      'admin': ['admin', 'configurator', 'viewer'],
      'configurator': ['configurator', 'viewer'],
      'viewer': ['viewer'],
      'user': []
    };
    
    return roleHierarchy[user?.role || 'user']?.includes(role) || false;
  };
  
  if (!user || !hasPermission(requiredRole)) {
    return <>{fallback}</>;
  }
  
  return <>{children}</>;
};

export default PermissionGuard;
```

## 6. 知识库管理设计

### 6.1 知识库管理界面

```typescript
// KnowledgeManager.tsx
import React, { useState, useEffect } from 'react';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';

const KnowledgeManager: React.FC = () => {
  const { 
    knowledgeBase, 
    loadKnowledgeBase, 
    updateKnowledgeBase,
    isLoading 
  } = useAIAssistantStore();
  
  const [activeTab, setActiveTab] = useState<'documents' | 'sync' | 'settings'>('documents');
  
  useEffect(() => {
    loadKnowledgeBase();
  }, []);
  
  return (
    <div className="h-full flex flex-col">
      {/* 标题栏 */}
      <div className="p-4 border-b">
        <h4 className="font-semibold">知识库管理</h4>
        <p className="text-sm text-gray-500">管理AI助手的知识库内容</p>
      </div>
      
      {/* Tab导航 */}
      <div className="flex border-b">
        <button
          onClick={() => setActiveTab('documents')}
          className={`px-4 py-2 text-sm ${
            activeTab === 'documents' 
              ? 'border-b-2 border-blue-500 text-blue-600' 
              : 'text-gray-500'
          }`}
        >
          文档管理
        </button>
        <button
          onClick={() => setActiveTab('sync')}
          className={`px-4 py-2 text-sm ${
            activeTab === 'sync' 
              ? 'border-b-2 border-blue-500 text-blue-600' 
              : 'text-gray-500'
          }`}
        >
          同步状态
        </button>
        <button
          onClick={() => setActiveTab('settings')}
          className={`px-4 py-2 text-sm ${
            activeTab === 'settings' 
              ? 'border-b-2 border-blue-500 text-blue-600' 
              : 'text-gray-500'
          }`}
        >
          设置
        </button>
      </div>
      
      {/* 内容区域 */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 'documents' && <DocumentsList />}
        {activeTab === 'sync' && <SyncStatus />}
        {activeTab === 'settings' && <KnowledgeSettings />}
      </div>
      
      {/* 操作按钮 */}
      <div className="p-4 border-t">
        <button
          onClick={() => updateKnowledgeBase()}
          disabled={isLoading}
          className="w-full px-4 py-2 bg-green-500 text-white rounded-lg
                     hover:bg-green-600 disabled:opacity-50"
        >
          {isLoading ? '更新中...' : '更新知识库'}
        </button>
      </div>
    </div>
  );
};

// 文档列表组件
const DocumentsList: React.FC = () => {
  const { documents, deleteDocument } = useAIAssistantStore();
  
  return (
    <div className="p-4 space-y-3">
      {documents.map((doc) => (
        <div key={doc.id} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
          <div>
            <div className="font-medium">{doc.title}</div>
            <div className="text-sm text-gray-500">{doc.type} • {doc.size}</div>
          </div>
          <div className="flex space-x-2">
            <button className="text-blue-500 hover:text-blue-700">编辑</button>
            <button 
              onClick={() => deleteDocument(doc.id)}
              className="text-red-500 hover:text-red-700"
            >
              删除
            </button>
          </div>
        </div>
      ))}
    </div>
  );
};

// 同步状态组件
const SyncStatus: React.FC = () => {
  const { syncStatus, triggerSync } = useAIAssistantStore();
  
  return (
    <div className="p-4 space-y-4">
      <div className="bg-blue-50 p-4 rounded-lg">
        <h5 className="font-medium text-blue-800">同步状态</h5>
        <div className="mt-2 space-y-2">
          <div className="flex justify-between">
            <span>文档数量</span>
            <span>{syncStatus.documentCount}</span>
          </div>
          <div className="flex justify-between">
            <span>向量数量</span>
            <span>{syncStatus.vectorCount}</span>
          </div>
          <div className="flex justify-between">
            <span>最后同步</span>
            <span>{syncStatus.lastSync}</span>
          </div>
        </div>
      </div>
      
      <button
        onClick={() => triggerSync()}
        className="w-full px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
      >
        立即同步
      </button>
    </div>
  );
};

export default KnowledgeManager;
```

### 6.2 知识库配置

```yaml
# config/ai_assistant/knowledge_config.yaml
knowledge_base:
  # 文档来源
  sources:
    - name: "project_docs"
      type: "directory"
      path: "/docs"
      file_types: [".md", ".txt", ".rst"]
      auto_sync: true
      sync_interval: "1h"
    
    - name: "wiki"
      type: "git"
      repo: "https://github.com/org/wiki.git"
      branch: "main"
      auto_sync: true
      sync_interval: "6h"
    
    - name: "database_metadata"
      type: "database"
      tables: ["adh_table_info", "adh_column_metadata", "adh_business_terms"]
      auto_sync: true
      sync_interval: "5m"
  
  # 分块配置
  chunking:
    chunk_size: 512
    chunk_overlap: 50
    separators: ["\n\n", "\n", "。", "！", "？", ".", "!", "?"]
  
  # 向量配置
  embedding:
    model: "text2vec-base-chinese"
    dimension: 768
    batch_size: 100
  
  # Neo4j配置
  neo4j:
    uri: "bolt://localhost:7687"
    user: "neo4j"
    password: "${NEO4J_PASSWORD}"
    database: "ai_assistant"
```

## 7. API设计

### 7.1 API端点

```python
# backend/api/ai_assistant.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter(prefix="/api/ai-assistant", tags=["AI Assistant"])

# 请求模型
class ChatRequest(BaseModel):
    message: str
    context: Optional[dict] = None
    session_id: Optional[str] = None

class KnowledgeUpdateRequest(BaseModel):
    source: str
    force: bool = False

class DocumentUploadRequest(BaseModel):
    title: str
    content: str
    doc_type: str
    tags: List[str] = []

# 响应模型
class ChatResponse(BaseModel):
    message: str
    context: Optional[dict] = None
    sources: List[dict] = []
    suggestions: List[str] = []

class KnowledgeStatusResponse(BaseModel):
    document_count: int
    vector_count: int
    graph_node_count: int
    last_sync: str
    sync_status: str

# API端点
@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """发送消息给AI助手"""
    from backend.services.ai_assistant_service import AIAssistantService
    
    service = AIAssistantService()
    response = await service.chat(
        message=request.message,
        context=request.context,
        session_id=request.session_id
    )
    
    return response

@router.get("/context")
async def get_context(page: str, module: str):
    """获取当前页面的上下文信息"""
    from backend.services.ai_assistant_service import AIAssistantService
    
    service = AIAssistantService()
    context = await service.get_page_context(page, module)
    
    return context

@router.post("/knowledge/update")
async def update_knowledge(request: KnowledgeUpdateRequest):
    """更新知识库"""
    from backend.services.ai_assistant_service import AIAssistantService
    
    service = AIAssistantService()
    result = await service.update_knowledge(
        source=request.source,
        force=request.force
    )
    
    return result

@router.get("/knowledge/status", response_model=KnowledgeStatusResponse)
async def get_knowledge_status():
    """获取知识库状态"""
    from backend.services.ai_assistant_service import AIAssistantService
    
    service = AIAssistantService()
    status = await service.get_knowledge_status()
    
    return status

@router.post("/knowledge/documents")
async def upload_document(request: DocumentUploadRequest):
    """上传文档到知识库"""
    from backend.services.ai_assistant_service import AIAssistantService
    
    service = AIAssistantService()
    result = await service.upload_document(
        title=request.title,
        content=request.content,
        doc_type=request.doc_type,
        tags=request.tags
    )
    
    return result

@router.delete("/knowledge/documents/{doc_id}")
async def delete_document(doc_id: str):
    """删除文档"""
    from backend.services.ai_assistant_service import AIAssistantService
    
    service = AIAssistantService()
    result = await service.delete_document(doc_id)
    
    return result

@router.get("/suggestions")
async def get_suggestions(context: str):
    """获取基于上下文的建议问题"""
    from backend.services.ai_assistant_service import AIAssistantService
    
    service = AIAssistantService()
    suggestions = await service.get_suggestions(context)
    
    return suggestions
```

### 7.2 服务层实现

```python
# backend/services/ai_assistant_service.py
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class AIAssistantService:
    """AI助手服务"""
    
    def __init__(self):
        from backend.rag.graph_rag.neo4j_store import Neo4jStore
        from backend.common.llm.llm_client import call_llm
        from backend.common.vector import get_vector_store
        
        self.neo4j = Neo4jStore()
        self.vector_store = get_vector_store()
        self.llm = call_llm
    
    async def chat(
        self, 
        message: str, 
        context: Optional[dict] = None,
        session_id: Optional[str] = None
    ) -> dict:
        """处理聊天请求"""
        try:
            # 1. 检索相关知识
            relevant_docs = await self._retrieve_knowledge(message, context)
            
            # 2. 组装提示词
            prompt = self._build_prompt(message, relevant_docs, context)
            
            # 3. 调用LLM
            response = await self.llm(prompt)
            
            # 4. 提取建议
            suggestions = await self._extract_suggestions(message, response, context)
            
            return {
                "message": response,
                "context": context,
                "sources": relevant_docs,
                "suggestions": suggestions
            }
        except Exception as e:
            logger.error(f"Chat error: {e}")
            raise HTTPException(status_code=500, detail="处理请求时出错")
    
    async def _retrieve_knowledge(
        self, 
        query: str, 
        context: Optional[dict] = None
    ) -> List[dict]:
        """检索相关知识"""
        # 1. 向量检索
        query_embedding = self._generate_embedding(query)
        vector_results = self.vector_store.search(
            table="adh_knowledge_chunks",
            query_embedding=query_embedding,
            limit=5
        )
        
        # 2. 图检索（如果有上下文）
        graph_results = []
        if context:
            graph_results = self.neo4j.retrieve_context(
                query=query,
                context=context,
                max_depth=2
            )
        
        # 3. 合并结果
        all_results = vector_results + graph_results
        
        # 4. 去重和排序
        unique_results = self._deduplicate_results(all_results)
        
        return unique_results[:10]
    
    def _build_prompt(
        self, 
        message: str, 
        docs: List[dict],
        context: Optional[dict] = None
    ) -> str:
        """构建提示词"""
        prompt = """你是一个AI助手，专门帮助用户配置和使用AI-DataHub系统。

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

"""
        
        # 添加上下文信息
        if context:
            prompt += f"\n当前页面上下文：\n"
            prompt += f"- 页面：{context.get('page', 'unknown')}\n"
            prompt += f"- 模块：{context.get('module', 'unknown')}\n"
            if context.get('subModule'):
                prompt += f"- 子模块：{context['subModule']}\n"
        
        # 添加检索到的文档
        if docs:
            prompt += "\n相关知识库内容：\n"
            for i, doc in enumerate(docs[:5], 1):
                prompt += f"\n{i}. {doc.get('title', '')}\n"
                prompt += f"   {doc.get('content', '')[:500]}\n"
        
        # 添加用户问题
        prompt += f"\n用户问题：{message}\n"
        prompt += "\n请基于以上信息回答用户的问题。如果涉及配置操作，请提供详细的步骤指导。"
        
        return prompt
    
    async def get_page_context(self, page: str, module: str) -> dict:
        """获取页面上下文"""
        # 从知识库中检索相关配置信息
        context_query = f"{module} {page} 配置 帮助"
        relevant_docs = await self._retrieve_knowledge(context_query)
        
        return {
            "page": page,
            "module": module,
            "related_docs": relevant_docs,
            "common_questions": self._get_common_questions(page, module)
        }
    
    async def update_knowledge(self, source: str, force: bool = False) -> dict:
        """更新知识库"""
        try:
            # 1. 加载文档
            documents = self._load_documents(source)
            
            # 2. 分块处理
            chunks = self._chunk_documents(documents)
            
            # 3. 生成向量
            vectors = self._generate_vectors(chunks)
            
            # 4. 更新向量数据库
            self._update_vector_db(chunks, vectors)
            
            # 5. 更新知识图谱
            self._update_knowledge_graph(chunks)
            
            return {
                "success": True,
                "document_count": len(documents),
                "chunk_count": len(chunks),
                "updated_at": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Knowledge update error: {e}")
            raise HTTPException(status_code=500, detail="更新知识库时出错")
    
    async def get_knowledge_status(self) -> dict:
        """获取知识库状态"""
        # 查询向量数据库统计
        vector_stats = self._get_vector_stats()
        
        # 查询Neo4j统计
        graph_stats = self.neo4j.get_stats()
        
        return {
            "document_count": vector_stats.get("document_count", 0),
            "vector_count": vector_stats.get("vector_count", 0),
            "graph_node_count": graph_stats.get("node_count", 0),
            "last_sync": vector_stats.get("last_sync", ""),
            "sync_status": "healthy"
        }
```

## 8. 实现计划

### 8.1 阶段一：基础架构（1-2周）

**目标**：搭建基础框架，实现基本对话功能

**任务**：
1. 创建后端模块结构
2. 实现基础API端点
3. 创建前端悬浮框组件
4. 实现权限控制
5. 集成现有LLM服务

**交付物**：
- 基础后端服务
- 前端悬浮框组件
- 权限控制系统

### 8.2 阶段二：GraphRAG集成（2-3周）

**目标**：集成Neo4j，实现知识图谱检索

**任务**：
1. 安装和配置Neo4j
2. 实现知识图谱构建
3. 集成GraphRAG检索
4. 实现文档加载和分块
5. 实现向量索引

**交付物**：
- Neo4j集成
- GraphRAG检索引擎
- 知识图谱构建工具

### 8.3 阶段三：知识库管理（1-2周）

**目标**：实现知识库管理功能

**任务**：
1. 创建知识库管理界面
2. 实现文档上传和管理
3. 实现知识库同步
4. 添加监控和日志
5. 优化性能

**交付物**：
- 知识库管理界面
- 文档管理功能
- 同步状态监控

### 8.4 阶段四：优化和完善（1周）

**目标**：优化用户体验，完善功能

**任务**：
1. 优化响应速度
2. 添加更多上下文感知
3. 实现智能建议
4. 添加使用统计
5. 编写文档

**交付物**：
- 优化后的系统
- 用户文档
- 部署指南

## 9. 部署和配置

### 9.1 环境要求

```bash
# Python依赖
pip install neo4j graphrag fastapi uvicorn

# Neo4j安装
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:5.0

# 环境变量
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="your_password"
```

### 9.2 配置文件

```yaml
# backend/.env
# AI助手配置
AI_ASSISTANT_ENABLED=true
AI_ASSISTANT_MAX_TOKENS=4096
AI_ASSISTANT_TEMPERATURE=0.7

# Neo4j配置
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# 知识库配置
KNOWLEDGE_BASE_PATH=/docs
KNOWLEDGE_SYNC_INTERVAL=1h
KNOWLEDGE_CHUNK_SIZE=512
```

### 9.3 启动命令

```bash
# 后端
cd backend
python main.py

# 前端
cd frontend
npm run dev

# Neo4j
docker start neo4j
```

## 10. 监控和维护

### 10.1 监控指标

- 对话响应时间
- 知识库同步状态
- Neo4j连接状态
- 向量检索性能
- 用户使用统计

### 10.2 日志配置

```python
# 日志配置
LOGGING = {
    'version': 1,
    'handlers': {
        'ai_assistant': {
            'class': 'logging.FileHandler',
            'filename': 'logs/ai_assistant.log',
            'level': 'INFO'
        }
    },
    'loggers': {
        'ai_assistant': {
            'handlers': ['ai_assistant'],
            'level': 'INFO'
        }
    }
}
```

### 10.3 备份策略

- Neo4j数据库定期备份
- 向量数据库定期备份
- 配置文件版本控制
- 日志定期归档

## 11. 安全考虑

### 11.1 数据安全

- 用户对话内容加密存储
- 敏感信息脱敏处理
- 访问日志完整记录
- 定期安全审计

### 11.2 访问控制

- 基于角色的权限控制
- API访问认证
- 请求频率限制
- 异常访问检测

### 11.3 隐私保护

- 用户数据本地存储
- 不上传敏感信息
- 数据保留期限设置
- 用户数据删除功能

## 12. 扩展性考虑

### 12.1 水平扩展

- 支持多实例部署
- 负载均衡配置
- 数据库读写分离
- 缓存层优化

### 12.2 功能扩展

- 支持多种文档格式
- 支持多语言
- 支持自定义工具
- 支持插件机制

### 12.3 集成扩展

- 支持第三方知识库
- 支持外部API集成
- 支持Webhook通知
- 支持SSO集成

## 13. 后续优化事项

### 13.1 表元数据检索升级：NetworkX → Neo4j

**优先级**：中（AI助手实现后）
**状态**：📋 待讨论
**预计工作量**：2-3周

#### 背景

现有表元数据检索使用 **NetworkX内存图 + Doris HNSW** 方案，已经实现了：
- 表、字段、业务术语节点
- JOIN关系图谱
- 单跳遍历和向量检索

但存在以下限制：
- ❌ 只支持1跳遍历
- ❌ 不支持复杂路径查询
- ❌ 不支持图算法
- ❌ 内存限制，重启需重建

#### 优化目标

升级到 **Neo4j + GraphRAG** 方案，增强表元数据检索能力：

| 能力 | 现有方案 | Neo4j方案 | 提升 |
|------|----------|-----------|------|
| 多跳遍历 | ❌ 需要手写 | ✅ 原生支持 | 质变 |
| 路径查询 | ❌ 复杂实现 | ✅ Cypher支持 | 质变 |
| 图算法 | ❌ 自己实现 | ✅ 内置算法 | 质变 |
| 查询语言 | Python代码 | Cypher | 更简洁 |
| 持久化 | ❌ 内存 | ✅ 持久化 | 更可靠 |
| 扩展性 | ⚠️ 内存限制 | ✅ 百万级 | 更强 |

#### 新增能力

**1. 多跳关联发现**
```cypher
// 找出与订单表关联的所有表（最多4跳）
MATCH path = (t:Table {name: 'orders'})-[:JOIN*1..4]-(related:Table)
RETURN DISTINCT related.name, min(length(path)) as distance
ORDER BY distance
```

**2. 路径查询**
```cypher
// 找出从用户表到订单表的所有路径
MATCH paths = (u:Table {name: 'users'})-[:JOIN*1..4]-(o:Table {name: 'orders'})
RETURN paths, length(paths) as path_length
ORDER BY path_length
```

**3. 表重要性分析（PageRank）**
```cypher
CALL gds.pageRank.stream('table-graph')
YIELD nodeId, score
RETURN gds.util.asNode(nodeId).name as table, score
ORDER BY score DESC
```

**4. 业务领域聚类（社区发现）**
```cypher
CALL gds.louvain.stream('table-graph')
YIELD nodeId, communityId
RETURN communityId, collect(gds.util.asNode(nodeId).name) as tables
```

**5. 智能表推荐**
```cypher
MATCH (u:User {id: $userId})-[:QUERIED]->(t:Table)
WITH collect(t) as user_tables
UNWIND user_tables as ut
MATCH (ut)-[:JOIN*1..2]-(recommended:Table)
WHERE NOT recommended IN user_tables
RETURN recommended.name, count(*) as relevance
ORDER BY relevance DESC
```

#### 实现方案

**数据迁移**：
```python
# 从NetworkX迁移到Neo4j
def migrate_table_graph_to_neo4j(datasource_id: int):
    # 1. 构建NetworkX图（复用现有代码）
    G = _build_graph(datasource_id)
    
    # 2. 迁移节点到Neo4j
    for node_id, data in G.nodes(data=True):
        if data.get("type") == "table":
            neo4j.create_node("Table", {
                "name": data["name"],
                "comment": data.get("comment", ""),
                "datasource_id": datasource_id
            })
    
    # 3. 迁移边到Neo4j
    for src, tgt, data in G.edges(data=True):
        if data.get("type") == "join":
            neo4j.create_relationship(...)
```

**查询接口**：
```python
class TableGraphRetriever:
    def find_related_tables(self, table_name: str, max_depth: int = 3):
        """支持多跳关联查询"""
        query = """
        MATCH path = (t:Table {name: $table})-[:JOIN*1..$depth]-(related:Table)
        RETURN DISTINCT related.name, min(length(path)) as distance
        ORDER BY distance
        """
        return self.neo4j.execute(query, {"table": table_name, "depth": max_depth})
    
    def find_path(self, start: str, end: str, max_length: int = 4):
        """路径查询"""
        query = """
        MATCH paths = (t1:Table {name: $start})-[:JOIN*1..$max]-(t2:Table {name: $end})
        RETURN paths, length(paths) as path_length
        ORDER BY path_length
        """
        return self.neo4j.execute(query, {"start": start, "end": end, "max": max_length})
```

#### 讨论要点

1. **是否需要迁移**：现有NetworkX方案是否满足需求？
2. **性能对比**：Neo4j vs NetworkX的实际性能差异
3. **部署成本**：Neo4j的运维复杂度
4. **兼容性**：如何与现有系统平滑集成
5. **优先级**：是否在AI助手之后立即进行

#### 决策记录

- **2026-07-18**：记录优化事项，待AI助手实现后详细讨论
- **下一步**：AI助手实现完成后，评估是否需要此优化

---

**文档版本**：1.1
**最后更新**：2026-07-18
**作者**：AI-DataHub Team
