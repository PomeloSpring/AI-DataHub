# AI助手实现指南

## 📋 实现状态

### ✅ 已完成

1. **后端模块结构**
   - `backend/api/ai_assistant.py` - API端点
   - `backend/services/ai_assistant_service.py` - 服务层
   - `backend/models/ai_assistant.py` - 数据模型
   - `backend/config/ai_assistant_config.py` - 配置文件
   - `backend/config/agents/ai_assistant/` - 代理配置

2. **前端组件**
   - `frontend/src/components/ai-assistant/AIFloatingBox.tsx` - 悬浮框主组件
   - `frontend/src/components/ai-assistant/ChatInterface.tsx` - 聊天界面
   - `frontend/src/components/ai-assistant/ContextPanel.tsx` - 上下文面板
   - `frontend/src/components/ai-assistant/KnowledgeManager.tsx` - 知识库管理
   - `frontend/src/stores/aiAssistantStore.ts` - 状态管理

3. **数据库迁移**
   - `docker/mysql/ai_assistant_migration.sql` - 数据库表结构

4. **API路由注册**
   - 已在 `backend/main.py` 中注册路由

### 🔄 进行中

- 集成LLM服务
- 测试和调试

### ⏳ 待完成

- 知识库向量化功能
- 文档上传和管理
- 性能优化

## 🚀 部署步骤

### 1. 数据库迁移

```bash
# 执行数据库迁移
cd docker/mysql
mysql -u root -p < ai_assistant_migration.sql
```

### 2. 环境变量配置

在 `.env` 文件中添加以下配置：

```bash
# AI助手配置
AI_ASSISTANT_ENABLED=true
AI_ASSISTANT_MAX_TOKENS=4096
AI_ASSISTANT_TEMPERATURE=0.7

# 知识库配置
KNOWLEDGE_BASE_PATH=/path/to/docs
KNOWLEDGE_SYNC_INTERVAL=3600
KNOWLEDGE_CHUNK_SIZE=512

# 向量配置
AI_ASSISTANT_VECTOR_DIM=768
AI_ASSISTANT_VECTOR_SEARCH_LIMIT=10
```

### 3. 启动服务

```bash
# 后端
cd backend
python main.py

# 前端
cd frontend
npm run dev
```

## 📁 文件结构

```
AI-DataHub/
├── backend/
│   ├── api/
│   │   └── ai_assistant.py          # API端点
│   ├── services/
│   │   └── ai_assistant_service.py  # 服务层
│   ├── models/
│   │   └── ai_assistant.py          # 数据模型
│   ├── config/
│   │   ├── ai_assistant_config.py   # 配置文件
│   │   └── agents/
│   │       └── ai_assistant/        # 代理配置
│   │           ├── skill.yaml
│   │           └── system.md
│   └── main.py                      # 路由注册
├── frontend/
│   └── src/
│       ├── components/
│       │   └── ai-assistant/        # 前端组件
│       │       ├── AIFloatingBox.tsx
│       │       ├── ChatInterface.tsx
│       │       ├── ContextPanel.tsx
│       │       ├── KnowledgeManager.tsx
│       │       └── index.ts
│       ├── stores/
│       │   └── aiAssistantStore.ts  # 状态管理
│       └── App.tsx                  # 集成悬浮框
└── docker/
    └── mysql/
        └── ai_assistant_migration.sql  # 数据库迁移
```

## 🔧 API接口

### 聊天接口

```http
POST /api/ai-assistant/chat
Content-Type: application/json

{
  "message": "如何配置数据源？",
  "context": {
    "page": "datasource",
    "module": "admin"
  }
}
```

### 获取上下文

```http
GET /api/ai-assistant/context?page=datasource&module=admin
```

### 知识库状态

```http
GET /api/ai-assistant/knowledge/status
```

### 更新知识库

```http
POST /api/ai-assistant/knowledge/update
Content-Type: application/json

{
  "source": "docs",
  "force": false
}
```

## 🎯 功能特性

### 1. 悬浮对话框

- 固定在页面右下角
- 支持拖拽移动（可选）
- 最小化/最大化切换
- 响应式设计

### 2. 上下文感知

- 自动识别当前页面
- 显示相关配置指南
- 提供常见问题
- 快捷操作入口

### 3. 知识库管理

- 文档上传和管理
- 自动向量化
- 同步状态监控
- 多来源支持

### 4. 权限控制

- 基于角色的访问控制
- admin：完全访问
- configurator：配置和查看
- viewer：仅查看

## 🔍 使用示例

### 示例1：配置指导

**用户**：如何配置MySQL数据源？

**AI助手**：
配置MySQL数据源的步骤：

1. 进入「数据源管理」页面
2. 点击「新建数据源」按钮
3. 选择数据库类型为「MySQL」
4. 填写以下信息：
   - 主机地址：数据库服务器IP
   - 端口：默认3306
   - 数据库名：要连接的数据库
   - 用户名：数据库用户名
   - 密码：数据库密码
5. 点击「测试连接」验证配置
6. 保存配置

注意事项：
- 确保数据库用户有SELECT权限
- 建议使用只读账号
- 生产环境建议配置SSL连接

### 示例2：概念解释

**用户**：什么是Agent？

**AI助手**：
Agent是AI-DataHub中的智能代理，负责处理特定类型的查询任务。

主要特点：
- 每个Agent专注于特定领域（如数据分析、日志分析等）
- 使用LLM进行智能决策
- 支持工具调用和自主规划
- 可配置重试和迭代次数

常见Agent：
- data_analysis：数据分析Agent，用于SQL生成和执行
- log_analysis：日志分析Agent，用于ES日志查询
- traffic：流量分析Agent，用于UV/PV统计

配置位置：管理后台 → Agent管理

## 🐛 故障排除

### 问题1：AI助手不显示

**可能原因**：
- 用户权限不足
- 前端组件未正确加载

**解决方案**：
1. 检查用户角色是否在 `AI_ASSISTANT_ROLES` 中
2. 检查浏览器控制台是否有错误
3. 确认组件已正确导入

### 问题2：聊天无响应

**可能原因**：
- API接口未正确配置
- LLM服务未连接
- 网络问题

**解决方案**：
1. 检查后端服务是否运行
2. 查看后端日志是否有错误
3. 测试API接口是否正常

### 问题3：知识库同步失败

**可能原因**：
- 数据库连接问题
- 文档路径错误
- 权限不足

**解决方案**：
1. 检查数据库连接配置
2. 确认文档路径存在
3. 检查文件读取权限

## 📈 性能优化

### 1. 缓存优化

- 启用会话缓存
- 配置合理的缓存TTL
- 使用Redis缓存（可选）

### 2. 向量检索优化

- 调整向量维度
- 优化索引策略
- 使用批量检索

### 3. LLM调用优化

- 使用流式响应
- 配置合理的超时时间
- 实现重试机制

## 🔒 安全考虑

### 1. 输入验证

- 限制消息长度
- 过滤敏感信息
- 防止SQL注入

### 2. 权限控制

- 严格的角色验证
- API访问认证
- 请求频率限制

### 3. 数据保护

- 对话历史加密
- 敏感信息脱敏
- 定期清理日志

## 📝 更新日志

- **2026-07-18**：初始版本实现
  - 后端API和服务层
  - 前端悬浮框组件
  - 数据库表结构
  - 权限控制

---

**维护者**：AI-DataHub Team
**最后更新**：2026-07-18
