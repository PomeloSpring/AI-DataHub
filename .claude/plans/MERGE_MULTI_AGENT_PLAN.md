# 合并方案：multi-agent-enhancement → ai-create-view

## 概述

将 `origin/feature/multi-agent-enhancement` 分支的新功能引入当前 `feature/ai-create-view` 分支（已迁移到微服务架构）。

**核心挑战**：源分支是单体 `backend/` 结构，目标分支已拆分为 `services/` 微服务。需要按功能模块映射到对应服务。

---

## 服务映射关系

| 源路径 (multi-agent) | 目标服务 | 说明 |
|---|---|---|
| `backend/agent/` | `services/datamind/agent/` | Agent 核心（agent_loop, configurable_agent, data_analysis_agent） |
| `backend/nl2sql/` | `services/datamind/nl2sql/` | NL2SQL 管道（agent_pipeline, agent_constants, prompt_builder, query_executor） |
| `backend/config/` | `services/datamind/config/` | Agent 配置、Skill Loader、分析技能 |
| `backend/rag/` | `services/datamind/rag/` | RAG 检索（metadata_cache） |
| `backend/common/` | `services/shared/common/` | 共享基础库（auth, llm, db, vector） |
| `backend/mcp_client/` | `services/shared/mcp_client/` | MCP 客户端 |
| `backend/models/` | `services/shared/models/` | 数据模型 |
| `backend/api/admin.py` | 按功能拆分到各服务 | 审计日志增强、RLS、角色等 |
| `backend/services/sandbox_*` | `services/aiplatform/` | 沙箱执行环境（新服务模块） |
| `backend/services/rls_service.py` | `services/authservice/` | 行级安全策略 |
| `backend/services/role_service.py` | `services/authservice/` | 角色权限管理 |
| `backend/services/quality_service.py` | `services/datagov/` | 数据质量审查 |
| `backend/services/knowledge_service.py` | `services/datacatalog/` | 知识库管理 |
| `backend/services/modeling_service.py` | `services/datacatalog/` | 数据建模 |
| `backend/services/gateway_client.py` | `services/shared/common/` | DataFusion Gateway 客户端 |
| `backend/services/code_validator.py` | `services/aiplatform/` | 代码安全校验 |
| `backend/tasks/` | `services/datamind/` 或独立服务 | 定时任务调度 |
| `datafusion-gateway/` | `services/gateway/` 或独立部署 | Rust DataFusion 网关 |
| `docker/mysql/` | `services/shared/migrations/` | 数据库迁移脚本 |
| `frontend/` | `frontend/` | 前端（路径不变） |

---

## 分阶段合并计划

### Phase 1：基础设施层（shared）

**目标**：更新共享库，为上层服务提供新能力

#### 1.1 共享库更新
```
services/shared/common/
├── auth.py              ← +log_audit() 审计日志函数
├── config.py            ← +GATEWAY_URL 等新配置项
├── db/metadata_db.py    ← 连接池微调
├── vector/
│   ├── base.py          ← 向量存储基类（如有变更）
│   └── qdrant_store.py  ← 新增：Qdrant 向量存储适配器
└── llm/
    └── langfuse_client.py ← Langfuse 集成（如有变更）
```

#### 1.2 MCP 客户端更新
```
services/shared/mcp_client/
├── client.py            ← MCP 连接管理增强
├── registry.py          ← 工具注册增强
└── tools.py             ← 工具执行增强
```

#### 1.3 数据模型更新
```
services/shared/models/
└── schemas.py           ← 新增 Sandbox、RLS、Role、Skill 等 schema
```

#### 1.4 数据库迁移
```
services/shared/migrations/
├── skills_migration.sql         ← 新增：adh_skills 表
├── sandbox_migration.sql        ← 新增：sandbox 配置表
├── sandbox_log_migration.sql    ← 新增：sandbox 执行日志表
├── rls_migration.sql            ← 新增：RLS 策略表
├── role_migration.sql           ← 新增：角色表
├── role_permission_migration.sql ← 新增：角色权限表
├── quality_migration.sql        ← 新增：质量审查表
├── knowledge_migration.sql      ← 新增：知识库表
├── modeling_migration.sql       ← 新增：数据建模表
├── audit_log_migration.sql      ← 审计日志增强（+module 字段）
├── webhook_trigger_migration.sql ← 新增：Webhook 触发器表
└── scheduled_task_migration.sql  ← 定时任务表更新
```

---

### Phase 2：AI 核心服务（datamind）

**目标**：引入 Multi-Agent 增强、Skill 系统、分析技能

#### 2.1 Agent 核心增强
```
services/datamind/agent/
├── agent_loop.py        ← 并行工具执行 + doom loop 检测增强
├── base.py              ← max_time_seconds 60→120
├── configurable_agent.py ← 内置 run_code 工具 + _execute_tool 分发
└── data_analysis_agent.py ← max_iterations 15, max_time 180, load_analysis_skill
```

#### 2.2 NL2SQL 管道增强
```
services/datamind/nl2sql/
├── orchestrator/
│   ├── agent_constants.py   ← +load_analysis_skill 工具定义
│   ├── agent_pipeline.py    ← +sandbox_coder rules 注入 + propose_code 工具
│   ├── deep_pipeline.py     ← 微调
│   └── pipeline_orchestrator.py ← 微调
├── prompt/
│   └── prompt_builder.py    ← +分析技能上下文注入
└── sql/
    ├── query_executor.py    ← 查询执行增强
    └── rls_filter.py        ← 新增：RLS 行级过滤
```

#### 2.3 RAG 增强
```
services/datamind/rag/
├── rag_retriever.py     ← 检索增强
├── rag_retriever_v2.py  ← V2 检索增强
├── table_selector.py    ← 表选择器增强
└── metadata_cache.py    ← 新增：元数据缓存层
```

#### 2.4 Skill 系统（核心新增）
```
services/datamind/config/
├── skill_loader.py              ← 新增：Skill 加载器（文件+DB 优先级合并）
├── agents/
│   ├── orchestrator/system.md   ← +代码执行能力 + sandbox_coder rules
│   ├── data_analysis/
│   │   ├── skill.yaml           ← +分析技能路由模式
│   │   └── system.md            ← +分析技能使用规则
│   └── sandbox_coder/           ← 新增：代码生成 Agent
│       ├── skill.yaml
│       └── system.md
└── skills/                      ← 新增：分析技能目录（从 agents/ 迁移）
    ├── anomaly/
    │   ├── skill.yaml           ← +category: analysis
    │   └── system.md
    ├── funnel/
    ├── retention/
    ├── traffic/
    ├── trend/
    └── user_profiling/
```

#### 2.5 定时任务增强
```
services/datamind/services/
└── scheduled_task_service.py  ← 定时任务 CRUD 增强
```

---

### Phase 3：新增服务模块

#### 3.1 沙箱服务 → `services/aiplatform/`
```
services/aiplatform/
├── services/
│   ├── sandbox_service.py     ← 沙箱管理（CRUD、默认沙箱）
│   ├── sandbox_executor.py    ← 沙箱执行器（Docker 容器执行）
│   ├── docker_executor.py     ← Docker 容器管理
│   └── code_validator.py      ← AST 代码安全校验
├── api/
│   └── sandbox.py             ← 沙箱 API 端点
└── ...
```

#### 3.2 权限服务 → `services/authservice/`
```
services/authservice/
├── services/
│   ├── rls_service.py         ← 行级安全策略 CRUD + 策略匹配
│   └── role_service.py        ← 角色权限管理 CRUD
├── api/
│   ├── rls.py                 ← RLS API 端点
│   └── roles.py               ← 角色 API 端点
└── ...
```

#### 3.3 数据治理增强 → `services/datagov/`
```
services/datagov/
├── services/
│   └── quality_service.py     ← 数据质量审查服务
├── api/
│   └── quality.py             ← 质量审查 API 端点
└── ...
```

#### 3.4 数据目录增强 → `services/datacatalog/`
```
services/datacatalog/
├── services/
│   ├── knowledge_service.py   ← 知识库管理（已有，需合并增强）
│   └── modeling_service.py    ← 数据建模服务
├── api/
│   ├── knowledge.py           ← 知识库 API（已有，需合并增强）
│   └── modeling.py            ← 建模 API 端点
└── ...
```

#### 3.5 DataFusion Gateway → `services/gateway/` 或独立部署
```
services/gateway/  (或 datafusion-gateway/)
├── Cargo.toml
├── Dockerfile
├── src/
│   ├── main.rs
│   ├── api/
│   ├── engine/
│   ├── providers/
│   ├── schema/
│   ├── security/
│   └── types/
└── start.sh / stop.sh / status.sh
```

---

### Phase 4：前端页面

**目标**：引入新的管理页面

```
frontend/src/
├── api/
│   ├── audit.ts         ← 新增：审计日志 API
│   ├── rls.ts           ← 新增：RLS API
│   └── skill.ts         ← 新增：Skill API
├── components/
│   ├── CodeViewer.tsx           ← 新增：代码查看器
│   ├── ExecutionResultCard.tsx  ← 新增：执行结果卡片
│   └── McpInstallProgress.tsx   ← 新增：MCP 安装进度
└── pages/admin/
    ├── AuditLog.tsx             ← 新增：审计日志页面
    ├── RLSManagement.tsx        ← 新增：RLS 管理页面
    ├── RoleManagement.tsx       ← 新增：角色管理页面
    ├── QualityReview.tsx        ← 新增：质量审查页面
    ├── KnowledgeManagement.tsx  ← 新增：知识库管理页面
    ├── SandboxManagement.tsx    ← 新增：沙箱管理页面
    ├── MCPAgentConfig.tsx       ← 新增：MCP Agent 配置页面
    └── MCPMarket.tsx            ← MCP 市场优化
```

**路由注册**：更新 `frontend/src/App.tsx` 和 `frontend/src/components/SystemLayout.tsx` 注册新页面路由。

---

### Phase 5：配置与集成

#### 5.1 环境变量
```env
# DataFusion Gateway
GATEWAY_URL=http://localhost:50051
GATEWAY_TIMEOUT=30
GATEWAY_ENABLED=true

# Qdrant (可选)
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=

# Sandbox
SANDBOX_DEFAULT_IMAGE=python:3.11-slim
SANDBOX_NETWORK=sandbox-net
```

#### 5.2 Docker Compose 更新
- `services/docker-compose.yml` 增加 Gateway、Qdrant、Sandbox 服务定义

#### 5.3 依赖更新
- `backend/requirements.txt` → 各服务的 `requirements.txt` 更新

---

## 合并执行策略

### 推荐方式：Cherry-pick + 手动适配

由于两个分支结构差异大（单体 vs 微服务），**不建议直接 merge**，建议：

1. **逐 Phase 执行**，每个 Phase 完成后验证
2. **从源分支 cherry-pick 文件**，然后手动调整 import 路径
3. **Import 路径替换规则**：
   ```
   backend.common.        → services.shared.common.
   backend.agent.         → services.datamind.agent.
   backend.nl2sql.        → services.datamind.nl2sql.
   backend.config.        → services.datamind.config.
   backend.rag.           → services.datamind.rag.
   backend.mcp_client.    → services.shared.mcp_client.
   backend.models.        → services.shared.models.
   backend.services.      → 按功能拆分到对应服务
   backend.api.           → 按功能拆分到对应服务
   ```

### 验证检查点

| Phase | 验证项 |
|---|---|
| Phase 1 | 迁移脚本执行成功、共享库 import 正常 |
| Phase 2 | Agent 可启动、Skill 加载正常、NL2SQL 管道可用 |
| Phase 3 | 各新服务可独立启动、API 端点可访问 |
| Phase 4 | 前端页面可渲染、API 调用正常 |
| Phase 5 | 端到端流程可用、Docker Compose 全服务启动 |

---

## 风险与注意事项

1. **Import 路径**：最大工作量，需逐一替换并测试
2. **循环依赖**：微服务间通过 API 调用而非直接 import，需注意解耦
3. **数据库连接**：各服务独立连接池，需确认 metadata_db 连接方式一致
4. **配置加载**：`config/` 路径在微服务中可能不同，需调整 Path 基准
5. **定时任务**：需确定放在哪个服务中运行（建议 datamind 或独立 scheduler 服务）
6. **DataFusion Gateway**：Rust 组件，可独立部署，与 Python 服务通过 HTTP 通信
