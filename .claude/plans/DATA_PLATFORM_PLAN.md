# AI-DataHub 数据中台 — 微服务架构改造计划

## Context

当前 AI-DataHub 是一个单体 FastAPI 应用，包含 NL2SQL、多 Agent 分析、Dashboard、元数据管理等模块。目标是将其改造为**可独立部署、也可 All-in-One 部署**的微服务架构数据中台，面向 SaaS 产品化，补齐数据质量、数据血缘、数据同步、指标管理、标签管理等缺失模块。

**核心约束**：向量检索（Doris HNSW）和知识图谱（Neo4j）是全服务共享的基础设施，不能被拆散到各服务内部。

---

## 一、整体架构

```
                        ┌──────────────────────────┐
                        │       API Gateway         │
                        │    (Nginx / Kong)         │
                        │  路由 · 限流 · JWT校验     │
                        └────────────┬──────────────┘
                                     │
         ┌───────────┬───────────┬───┴───┬───────────┬───────────┐
         ▼           ▼           ▼       ▼           ▼           ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ DataMind │ │ DataGov  │ │ DataFlow │ │ DataViz  │ │DataCatalog│
   │ AI引擎   │ │ 数据治理 │ │ 数据集成 │ │ 可视化   │ │ 数据目录  │
   │ Python   │ │ Python   │ │ Python   │ │ Python   │ │ Python   │
   │          │ │          │ │ +Airflow │ │          │ │          │
   └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
        │            │            │            │            │
        └────────────┴────────────┴─────┬──────┴────────────┘
                                        ▼
                   ┌────────────────────────────────────┐
                   │      Shared Infrastructure         │
                   │                                    │
                   │  VectorStore (Doris/Milvus)        │
                   │  KnowledgeGraph (Neo4j)            │
                   │  MetadataDB (MySQL)                │
                   │  EventBus (Redis Streams/RabbitMQ) │
                   │  Cache (Redis)                     │
                   │  ObjectStorage (MinIO)             │
                   └────────────────────────────────────┘
```

### 部署模式

| 模式 | 说明 | 场景 |
|------|------|------|
| **All-in-One** | 单 Docker Compose，所有服务 + 共享基础设施一个命令启动 | 开发/小规模部署 |
| **独立部署** | 每个服务独立容器，K8s 编排，按需水平扩展 | 生产/SaaS |
| **混合部署** | AI 引擎独立扩展（GPU），其他服务合并不署 | 中等规模 |

---

## 二、服务拆分（6 个业务服务 + 1 个共享层）

### 2.1 DataMind — AI 引擎服务（Python/FastAPI）

**职责**：所有 AI/LLM 相关能力的统一出口

| 子模块 | 来源 | 说明 |
|--------|------|------|
| NL2SQL | 现有 `nl2sql/` | Quick/Deep/Agent 三种模式 |
| Agent 编排 | 现有 `agent/` | Orchestrator + 9 个子 Agent |
| RAG 检索 | 现有 `rag/` | 向量 + BM25 + 混合检索 |
| 知识库管理 | 现有 `services/knowledge_service.py` | 文档上传、分块、向量化 |
| LLM 客户端 | 现有 `common/llm/` | 多模型支持、Langfuse 追踪 |
| Prompt 管理 | 现有 `config/` + `adh_prompts` | Prompt 版本管理 |

**API 端点**：
- `POST /api/chat/send/stream` — NL2SQL 对话（SSE）
- `POST /api/agent/dispatch` — Agent 分发
- `GET /api/knowledge/documents` — 知识库管理
- `POST /api/pipeline/execute` — Pipeline 执行

**数据归属**：`adh_conversations`, `adh_agents`, `adh_prompts`, `adh_prompt_versions`, `adh_knowledge_*`, `adh_sql_corrections`, `adh_pipeline_metrics`

---

### 2.2 DataGov — 数据治理服务（Python/FastAPI）

**职责**：数据质量、数据标准、数据血缘、数据安全

| 子模块 | 状态 | 说明 |
|--------|------|------|
| 数据质量 | 🆕 新建 | 质量规则引擎、校验任务、质量报告 |
| 数据血缘 | 🆕 新建 | SQL 解析 → 表/字段级血缘图 |
| 数据标准 | 🆕 新建 | 命名规范、编码标准、度量标准 |
| 数据安全 | 扩展现有 | 敏感数据分级、脱敏规则、访问控制 |
| 术语管理 | 现有 `adh_business_terms` | 业务术语映射 |

**API 端点**：
- `POST /api/quality/rules` — 质量规则 CRUD
- `POST /api/quality/execute` — 执行质量检查
- `GET /api/lineage/tables/{name}` — 表血缘查询
- `GET /api/lineage/columns/{table}.{col}` — 字段血缘查询
- `GET /api/standards` — 数据标准管理

**新增数据表**：
- `adh_quality_rules` — 质量规则定义（类型: 完整性/唯一性/准确性/时效性/自定义SQL）
- `adh_quality_results` — 质量检查结果
- `adh_quality_reports` — 质量报告（定期生成）
- `adh_lineage_nodes` — 血缘节点（表/字段/ETL任务）
- `adh_lineage_edges` — 血缘边（依赖关系）
- `adh_data_standards` — 数据标准定义
- `adh_sensitive_fields` — 敏感字段标记和脱敏规则

---

### 2.3 DataFlow — 数据集成服务（Python/FastAPI + Apache Airflow）

**职责**：数据同步、ETL 编排、任务调度、通知推送

**核心设计**：不自研调度引擎，直接集成 **Apache Airflow** 作为工作流调度和数据同步的执行引擎，DataFlow 作为薄封装层对接业务系统。

```
┌─────────────────────────────────────────────────────────┐
│                  DataFlow 服务（薄封装层）                │
│                                                          │
│  ┌────────────────────┐    ┌──────────────────────────┐ │
│  │  业务 API 层       │    │  Airflow 集成层           │ │
│  │                    │    │                          │ │
│  │  同步任务 CRUD     │───▶│  DAG 动态生成             │ │
│  │  工作流管理        │    │  任务触发 & 状态查询      │ │
│  │  通知配置          │    │  日志收集                 │ │
│  └────────────────────┘    └──────────────────────────┘ │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │  Airflow DAG 模板库                                │ │
│  │                                                    │ │
│  │  • sync_mysql_to_doris  — MySQL → Doris 全量/增量  │ │
│  │  • sync_es_to_doris     — ES → Doris 数据同步      │ │
│  │  • sync_api_extract     — API 数据抽取             │ │
│  │  • quality_check        — 数据质量检查任务          │ │
│  │  • lineage_refresh      — 血缘关系刷新             │ │
│  │  • notification_dag     — 通知推送任务              │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
          │                        │
          ▼                        ▼
   Airflow REST API          Airflow Workers
   (DAG 管理/触发)           (任务执行)
```

**集成 Airflow 的理由**：
- Python 栈，与项目技术栈统一
- DAG 编排成熟，支持依赖管理、重试、超时、告警
- K8s Executor 支持弹性扩缩容
- REST API 完善，可通过 API 动态创建/触发 DAG
- 丰富的 Operator 生态（BashOperator, PythonOperator, JDBCOperator 等）
- 现有 Celery Beat 可平滑迁移到 Airflow Scheduler

**API 端点**：
- `POST /api/sync/tasks` — 创建同步任务（自动生成 Airflow DAG）
- `GET /api/sync/tasks` — 同步任务列表
- `POST /api/sync/tasks/{id}/run` — 手动触发同步
- `GET /api/sync/tasks/{id}/logs` — 执行日志（代理到 Airflow）
- `POST /api/workflow/execute` — 执行工作流
- `GET /api/workflow/{id}/status` — 工作流状态（查询 Airflow）
- `GET /api/scheduled/tasks` — 定时任务管理（兼容现有 API）
- `POST /api/notification/send` — 通知推送

**新增数据表**：
- `adh_sync_tasks` — 同步任务定义（源/目标/映射/调度，与 Airflow DAG ID 关联）
- `adh_sync_logs` — 同步执行日志（从 Airflow 回写）
- `adh_workflow_executions` — 工作流执行实例
- `adh_workflow_node_logs` — 节点执行日志

**保留现有表**：`adh_scheduled_tasks`, `adh_scheduled_logs`, `adh_notification_channels`

**Airflow 部署方式**：
- Docker Compose 模式：Airflow 作为容器一起部署
- K8s 模式：Airflow 使用 KubernetesExecutor，Worker 按需创建 Pod
- Airflow WebUI 独立访问，同时通过 DataFlow API 提供统一入口

---

### 2.4 DataViz — 可视化服务（Python/FastAPI）

**职责**：Dashboard、图表、报表、数据大屏

| 子模块 | 来源 | 说明 |
|--------|------|------|
| Dashboard | 现有 `api/dashboard.py` | CRUD + 图表管理 |
| 报表生成 | 现有 `tasks/executor.py` | LLM 驱动的智能报表 |
| 数据大屏 | 现有 `pages/Screen.tsx` | 全屏展示 |
| 报表模板 | 现有 `adh_report_templates` | 模板管理 |

**API 端点**：
- `GET/POST /api/dashboards` — Dashboard CRUD
- `GET/POST /api/charts` — 图表 CRUD
- `POST /api/reports/generate` — 报表生成
- `GET /api/reports/{id}` — 报表查看（公开链接）

**数据归属**：`adh_dashboards`, `adh_charts`, `adh_chart_snapshots`, `adh_saved_queries`, `adh_report_templates`, `adh_reports`

---

### 2.5 DataCatalog — 数据目录服务（Python/FastAPI）

**职责**：元数据管理、数据发现、业务术语、指标管理、标签管理

| 子模块 | 状态 | 说明 |
|--------|------|------|
| 元数据管理 | 现有 `adh_table_info`, `adh_column_metadata` | 表/字段元数据 |
| 数据发现 | 🆕 新建 | 全文搜索 + 向量搜索（调用 VectorService）+ 标签筛选 |
| 指标管理 | 🆕 新建 | 指标目录、口径定义、派生指标 |
| 标签管理 | 🆕 新建 | 标签体系、标签圈人、标签服务 |
| 术语管理 | 现有 `adh_business_terms` | 业务术语 |

**API 端点**：
- `GET /api/catalog/search` — 全局数据搜索
- `GET /api/catalog/tables` — 表目录
- `GET /api/catalog/metrics` — 指标目录
- `POST /api/metrics` — 指标 CRUD
- `POST /api/tags` — 标签 CRUD
- `POST /api/tags/query` — 标签圈人查询
- `GET /api/catalog/glossary` — 业务术语

**新增数据表**：
- `adh_metrics` — 指标定义（名称/口径/计算公式/维度/粒度/负责人）
- `adh_metric_dimensions` — 指标维度
- `adh_tags` — 标签定义（名称/类型/规则/数据源）
- `adh_tag_values` — 标签值（实体+标签+值）
- `adh_tag_categories` — 标签分类目录

---

### 2.6 AuthService — 认证授权服务（Python/FastAPI）

**职责**：用户管理、认证、授权、工作空间、审计

| 子模块 | 来源 | 说明 |
|--------|------|------|
| 用户管理 | 现有 `adh_users` | CRUD + 角色 |
| JWT 认证 | 现有 `common/auth.py` | 登录/注册/Token刷新 |
| 工作空间 | 现有 `services/workspace_service_v2.py` | 多租户隔离 |
| RBAC | 🆕 新建 | 角色权限矩阵 |
| 审计日志 | 现有 `adh_audit_logs` | 操作追溯 |

**API 端点**：
- `POST /api/auth/login` — 登录
- `POST /api/auth/refresh` — Token 刷新
- `GET /api/users` — 用户管理
- `GET /api/workspaces` — 工作空间管理
- `GET /api/roles` — 角色管理
- `GET /api/audit/logs` — 审计日志

**新增数据表**：
- `adh_roles` — 角色定义
- `adh_permissions` — 权限定义
- `adh_role_permissions` — 角色-权限关联

**保留现有表**：`adh_users`, `adh_workspaces`, `adh_workspace_users`, `adh_audit_logs`

---

### 2.7 Shared Infrastructure — 共享基础设施层

**这是关键设计**：向量检索和知识图谱作为独立的共享服务，不绑定任何业务服务。

| 组件 | 部署方式 | 说明 |
|------|----------|------|
| **VectorService** | 独立服务 (Python) | 封装 Doris/Milvus 向量检索，提供 gRPC/HTTP 接口 |
| **GraphService** | 独立服务 (Python) | 封装 Neo4j 知识图谱，提供 gRPC/HTTP 接口 |
| **EventBus** | Redis Streams | 服务间异步通信（元数据变更、质量检查触发等） |
| **MetadataDB** | MySQL | 各服务共享 Schema，通过 `workspace_id` 隔离 |
| **Cache** | Redis | 分布式缓存、Session、限流计数 |
| **ObjectStorage** | MinIO | 文件存储（知识库文档、报表导出等） |

**共享访问方式**：
```
任何业务服务 ──HTTP/gRPC──▶ VectorService ──▶ Doris HNSW
任何业务服务 ──HTTP/gRPC──▶ GraphService ──▶ Neo4j
任何业务服务 ──Redis───────▶ EventBus (pub/sub)
```

---

### 2.8 MCP 协议层 — AI 工具可调用的服务接口

**设计原则**：每个业务服务同时暴露 **REST API**（内部通信 + 前端）和 **MCP Server**（外部 AI 工具集成），复用同一套 Service 层代码。

```
┌─────────────────────────────────────────────────────────┐
│               外部 AI 工具（MCP Client）                  │
│   Claude Desktop · Cursor · Windsurf · 自定义 Agent      │
│         │              │              │                  │
│         ▼              ▼              ▼                  │
│   ┌─────────┐   ┌─────────┐   ┌─────────┐              │
│   │DataMind │   │DataGov  │   │DataCatalog│             │
│   │MCP:31001│   │MCP:31002│   │MCP:31005 │             │
│   └────┬────┘   └────┬────┘   └────┬─────┘             │
│        │ MCP 协议（SSE/stdio）                            │
└────────┼─────────────┼─────────────┼────────────────────┘
         │             │             │
┌────────┼─────────────┼─────────────┼────────────────────┐
│        ▼             ▼             ▼     内部服务层       │
│   ┌─────────┐   ┌─────────┐   ┌─────────┐              │
│   │DataMind │   │DataGov  │   │DataCatalog│             │
│   │REST:8001│   │REST:8002│   │REST:8005 │             │
│   └─────────┘   └─────────┘   └─────────┘              │
│        REST/HTTP（内部通信）                               │
└─────────────────────────────────────────────────────────┘
```

**每个服务暴露的 MCP 能力**：

| MCP Server | Tools（操作） | Resources（数据） |
|------------|--------------|-------------------|
| **DataMind** | `query_data` 自然语言查询、`execute_sql` 执行SQL、`analyze_data` 多维分析 | `datamind://agents` Agent列表、`datamind://conversations/{id}` 对话历史 |
| **DataGov** | `check_quality` 质量检查、`get_lineage` 血缘查询、`check_compliance` 合规检查 | `gov://quality/reports` 质量报告、`gov://standards` 数据标准 |
| **DataFlow** | `create_sync_task` 创建同步、`run_task` 触发执行、`get_task_status` 状态查询 | `flow://tasks` 任务列表、`flow://tasks/{id}/logs` 执行日志 |
| **DataViz** | `create_dashboard` 创建看板、`generate_report` 生成报表 | `viz://dashboards` 看板列表、`viz://reports/{id}` 报表 |
| **DataCatalog** | `search_metadata` 元数据搜索、`get_table_schema` 表结构、`get_metrics` 指标查询、`query_tags` 标签圈人 | `catalog://tables` 表目录、`catalog://metrics` 指标目录、`catalog://glossary` 术语表 |
| **AuthService** | `create_user` 创建用户、`check_permission` 权限检查 | `auth://users` 用户列表、`auth://workspaces` 工作空间 |

**实现模式**：每个服务的 `main.py` 同时启动 FastAPI app 和 MCP Server，Service 层代码共享：

```python
# services/datacatalog/main.py
app = FastAPI()           # REST API（端口 8005）
mcp = Server("datacatalog")  # MCP Server（端口 31005）

# REST 路由和 MCP Tool 共用同一个 service
@app.get("/api/catalog/tables")
@mcp.tool()
async def list_tables(workspace_id: int):
    return await catalog_service.list_tables(workspace_id)
```

**MCP 传输方式**：
- 开发环境：SSE（HTTP Stream）— 便于调试
- 生产环境：Streamable HTTP — 更高效
- 本地工具集成：stdio — Claude Desktop 等本地客户端

---

## 三、服务间通信（双协议）

### 3.1 内部通信 — REST/HTTP

服务间通过 HTTP REST 调用，使用服务发现（K8s Service 或 Consul）。

```
# DataViz 需要执行 SQL 查询
DataViz ──POST /api/agent/sql-execute──▶ DataMind

# DataCatalog 需要向量搜索
DataCatalog ──POST /api/vector/search──▶ VectorService

# DataGov 需要获取元数据
DataGov ──GET /api/catalog/tables──▶ DataCatalog
```

### 3.2 异步通信 — Redis Streams

事件驱动的解耦通信：

```
# 元数据同步完成 → 通知其他服务
DataCatalog ──PUBLISH metadata.updated──▶ EventBus
                                        ├─▶ DataMind (更新 RAG 索引)
                                        ├─▶ DataGov (更新血缘)
                                        └─▶ VectorService (重建向量)

# 数据质量检查结果
DataGov ──PUBLISH quality.checked──▶ EventBus
                                    └─▶ DataViz (质量报告可视化)
```

### 3.3 API Gateway 路由规则

```yaml
routes:
  # AI 引擎（需要较长超时）
  - prefix: /api/chat/
    service: datamind:8001
    timeout: 300s
  - prefix: /api/agent/
    service: datamind:8001
    timeout: 600s
  - prefix: /api/pipeline/
    service: datamind:8001
    timeout: 600s

  # 数据治理
  - prefix: /api/quality/
    service: datagov:8002
  - prefix: /api/lineage/
    service: datagov:8002
  - prefix: /api/standards/
    service: datagov:8002

  # 数据集成
  - prefix: /api/sync/
    service: dataflow:8003
  - prefix: /api/workflow/
    service: dataflow:8003
  - prefix: /api/scheduled/
    service: dataflow:8003

  # 可视化
  - prefix: /api/dashboard/
    service: dataviz:8004
  - prefix: /api/charts/
    service: dataviz:8004
  - prefix: /api/reports/
    service: dataviz:8004

  # 数据目录
  - prefix: /api/catalog/
    service: datacatalog:8005
  - prefix: /api/metrics/
    service: datacatalog:8005
  - prefix: /api/tags/
    service: datacatalog:8005
  - prefix: /api/metadata/
    service: datacatalog:8005

  # 认证授权
  - prefix: /api/auth/
    service: authservice:8006
  - prefix: /api/users/
    service: authservice:8006
  - prefix: /api/workspaces/
    service: authservice:8006

# MCP Server 端口映射（独立端口，不走 Gateway）
mcp_servers:
  datamind:    "datamind:31001"      # MCP over SSE
  datagov:     "datagov:31002"
  dataflow:    "dataflow:31003"
  dataviz:     "dataviz:31004"
  datacatalog: "datacatalog:31005"
  authservice: "authservice:31006"
```

---

## 四、目录结构

```
AI-DataHub/
├── services/                       # 微服务目录
│   ├── gateway/                    # API Gateway (Nginx/Kong 配置)
│   │   ├── nginx.conf
│   │   ├── Dockerfile
│   │   └── lua/                    # Kong/Nginx Lua 插件
│   │
│   ├── datamind/                   # AI 引擎服务 (Python/FastAPI)
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   ├── agent/                  # ← 从 backend/agent/ 迁移
│   │   ├── nl2sql/                 # ← 从 backend/nl2sql/ 迁移
│   │   ├── rag/                    # ← 从 backend/rag/ 迁移
│   │   ├── config/                 # ← 从 backend/config/ 迁移
│   │   ├── common/llm/             # ← 从 backend/common/llm/ 迁移
│   │   ├── api/
│   │   │   ├── chat.py
│   │   │   ├── agent.py
│   │   │   ├── knowledge.py
│   │   │   └── pipeline.py
│   │   └── services/
│   │       ├── chat_service.py
│   │       ├── knowledge_service.py
│   │       └── agent_service.py
│   │
│   ├── datagov/                    # 数据治理服务 (Python/FastAPI)
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   ├── api/
│   │   │   ├── quality.py          # 数据质量 API
│   │   │   ├── lineage.py          # 数据血缘 API
│   │   │   ├── standards.py        # 数据标准 API
│   │   │   └── security.py         # 数据安全 API
│   │   ├── services/
│   │   │   ├── quality_engine.py   # 质量规则引擎
│   │   │   ├── lineage_parser.py   # SQL 解析 → 血缘
│   │   │   ├── lineage_service.py  # 血缘图查询
│   │   │   └── sensitive_detector.py
│   │   └── models/
│   │
│   ├── dataflow/                   # 数据集成服务 (Python/FastAPI + Airflow)
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   ├── api/
│   │   │   ├── sync.py             # 同步任务 API
│   │   │   ├── workflow.py         # 工作流 API
│   │   │   ├── scheduled.py        # 定时任务 API
│   │   │   └── notification.py     # 通知推送 API
│   │   ├── services/
│   │   │   ├── airflow_client.py   # Airflow REST API 客户端
│   │   │   ├── dag_generator.py    # 动态 DAG 生成器
│   │   │   ├── sync_service.py     # 同步任务业务逻辑
│   │   │   └── notification_service.py
│   │   └── dags/                   # Airflow DAG 模板
│   │       ├── sync_mysql_to_doris.py
│   │       ├── sync_es_to_doris.py
│   │       ├── quality_check.py
│   │       └── notification_dag.py
│   │
│   ├── dataviz/                    # 可视化服务 (Python/FastAPI)
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   ├── api/
│   │   │   ├── dashboard.py
│   │   │   ├── chart.py
│   │   │   └── report.py
│   │   └── services/
│   │       ├── dashboard_service.py
│   │       └── report_service.py
│   │
│   ├── datacatalog/                # 数据目录服务 (Python/FastAPI)
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   ├── api/
│   │   │   ├── catalog.py          # 数据目录搜索 API
│   │   │   ├── metadata.py         # 元数据管理 API
│   │   │   ├── metrics.py          # 指标管理 API
│   │   │   ├── tags.py             # 标签管理 API
│   │   │   └── glossary.py         # 业务术语 API
│   │   └── services/
│   │       ├── metadata_service.py
│   │       ├── metrics_service.py
│   │       ├── tags_service.py
│   │       └── search_service.py
│   │
│   ├── authservice/                # 认证授权服务 (Python/FastAPI)
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   ├── api/
│   │   │   ├── auth.py             # 登录/注册/Token
│   │   │   ├── users.py            # 用户管理
│   │   │   ├── workspaces.py       # 工作空间管理
│   │   │   ├── roles.py            # 角色权限
│   │   │   └── audit.py            # 审计日志
│   │   └── services/
│   │       ├── auth_service.py
│   │       ├── user_service.py
│   │       ├── workspace_service.py
│   │       ├── rbac_service.py
│   │       └── audit_service.py
│   │
│   └── shared/                     # 共享基础设施服务
│       ├── vectorservice/          # 向量检索服务 (Python)
│       │   ├── main.py
│       │   ├── Dockerfile
│       │   └── store/
│       │       ├── doris_store.py
│       │       └── milvus_store.py
│       └── graphservice/           # 知识图谱服务 (Python)
│           ├── main.py
│           ├── Dockerfile
│           └── store/
│               ├── neo4j_store.py
│               └── graph_ops.py
│
├── frontend/                       # 前端（保持不变，Gateway 统一代理）
│
├── deploy/                         # 部署配置
│   ├── docker-compose.yml          # All-in-One 部署（含 Airflow）
│   ├── docker-compose.dev.yml      # 开发环境
│   └── k8s/                        # K8s 部署清单
│       ├── namespace.yaml
│       ├── gateway/
│       ├── datamind/
│       ├── datagov/
│       ├── dataflow/
│       ├── dataviz/
│       ├── datacatalog/
│       ├── authservice/
│       ├── shared/
│       ├── airflow/                # Airflow K8s 部署（Executor, Scheduler, Webserver）
│       └── infrastructure/         # MySQL, Redis, Neo4j, Doris
│
├── docker/                         # 基础设施 Docker 配置（保留）
│   ├── mysql/
│   ├── doris/
│   ├── neo4j/
│   └── redis/
│
└── backend/                        # 原单体后端（渐进式迁移，最终废弃）
```

---

## 五、菜单与页面规划

### 5.1 设计原则

- **用户/管理员分离**：普通用户使用「数据工作台」，管理员额外使用「系统管理」
- **按服务域分组**：菜单分组对应微服务边界，用户自然理解功能归属
- **渐进式暴露**：基础功能默认可见，高级功能（治理、集成）按需展示

### 5.2 WorkspaceLayout — 数据工作台（普通用户主界面）

```
┌─────────────────────────────────────────────┐
│  📊 数据工作台                     [工作空间▼] │
├─────────────────────────────────────────────┤
│                                              │
│  💬 数据分析                    ← DataMind   │
│     ├── Chat 智能问答    /ws/:id/chat        │
│     └── Agent 分析任务   /ws/:id/agent       │
│                                              │
│  📈 可视化                      ← DataViz    │
│     ├── 看板设计         /ws/:id/page        │
│     ├── 报表中心         /ws/:id/reports     │
│     └── 数据大屏         /screen/:id         │
│                                              │
│  📋 数据目录                    ← DataCatalog│
│     ├── 表 & 字段        /ws/:id/catalog     │
│     ├── 指标中心         /ws/:id/metrics     │
│     ├── 标签管理         /ws/:id/tags        │
│     └── 业务术语         /ws/:id/glossary    │
│                                              │
│  🔍 数据质量                    ← DataGov    │
│     ├── 质量概览         /ws/:id/quality     │
│     ├── 质量规则         /ws/:id/quality/rules│
│     └── 数据血缘         /ws/:id/lineage     │
│                                              │
│  🔄 数据同步                    ← DataFlow   │
│     ├── 同步任务         /ws/:id/sync        │
│     └── 执行日志         /ws/:id/sync/logs   │
│                                              │
│  ─────────────────────────────               │
│  📜 查询历史                   /ws/:id/history│
│  ⚙️ 工作空间设置        /ws/:id/settings     │
│                                              │
│  ─────────────────────────────               │
│  🔧 系统管理（仅admin）        /system        │
└─────────────────────────────────────────────┘
```

### 5.3 SystemLayout — 系统管理（管理员界面）

```
┌─────────────────────────────────────────────┐
│  🔧 系统管理                    [返回工作空间] │
├─────────────────────────────────────────────┤
│                                              │
│  ── 数据源 ──────────────────                │
│  🗄️ 数据源管理            /system/datasources│
│  📊 表元数据               /system/metadata   │
│  🔗 表关联                /system/relations   │
│  📝 SQL 模板              /system/templates   │
│                                              │
│  ── AI 配置 ─────────────────                │
│  🧠 模型中心               /system/models     │
│  🤖 Agent 管理            /system/mcp-agent  │
│  🔌 MCP Server 管理       /system/mcp-servers│
│  ⚡ 工作流编排             /system/workflows  │
│  💬 Prompt 管理           /system/prompts    │
│  📚 知识库                /system/knowledge-base│
│  🕸️ 知识图谱              /system/knowledge-graph│
│                                              │
│  ── 数据治理 ─────────────────               │
│  ✅ 质量规则配置         /system/quality-rules│
│  📐 数据标准             /system/standards   │
│  🔒 敏感数据管理         /system/sensitive    │
│  🌊 血缘配置             /system/lineage-config│
│                                              │
│  ── 数据集成 ─────────────────               │
│  🔄 同步任务管理          /system/sync-tasks  │
│  📦 Airflow 管理          /system/airflow     │
│  🔔 通知渠道             /system/notification-channels│
│  📄 报告模板             /system/report-templates│
│                                              │
│  ── 权限管理 ─────────────────               │
│  👥 用户管理              /system/users       │
│  🏢 工作空间管理          /system/workspaces  │
│  🔐 角色权限             /system/roles        │
│  📋 审计日志             /system/audit        │
│                                              │
│  ── 系统 ─────────────────────               │
│  ⚙️ 系统设置             /system/settings     │
│  📊 系统监控             /system/monitoring   │
└─────────────────────────────────────────────┘
```

### 5.4 新增页面清单

| 页面 | 路由 | 所属服务 | 说明 |
|------|------|----------|------|
| **数据质量概览** | `/ws/:id/quality` | DataGov | 质量得分、趋势图、问题 TOP10、维度雷达图 |
| **质量规则管理** | `/ws/:id/quality/rules` | DataGov | 规则列表、创建/编辑、启用/禁用、手动触发 |
| **数据血缘** | `/ws/:id/lineage` | DataGov | 可视化血缘图（ReactFlow DAG）、节点搜索、路径追踪 |
| **指标中心** | `/ws/:id/metrics` | DataCatalog | 指标目录树、口径定义、维度管理、指标详情 |
| **标签管理** | `/ws/:id/tags` | DataCatalog | 标签分类树、标签 CRUD、标签圈人查询、标签服务 |
| **数据同步** | `/ws/:id/sync` | DataFlow | 同步任务列表、创建向导（源→目标映射）、手动触发 |
| **同步日志** | `/ws/:id/sync/logs` | DataFlow | 执行历史表格、详情（行数/耗时/错误） |
| **角色权限** | `/system/roles` | AuthService | 角色 CRUD、权限矩阵配置 |
| **审计日志** | `/system/audit` | AuthService | 操作日志表格、筛选（用户/操作/时间） |
| **数据标准** | `/system/standards` | DataGov | 标准目录、规则配置、合规检查结果 |
| **敏感数据管理** | `/system/sensitive` | DataGov | 敏感字段列表、脱敏规则配置、分级标记 |
| **Airflow 管理** | `/system/airflow` | DataFlow | Airflow WebUI 嵌入（iframe）或简化状态页 |
| **MCP Server** | `/system/mcp-servers` | 共享 | MCP Server 状态、工具列表、连接测试 |
| **系统监控** | `/system/monitoring` | 共享 | 服务健康状态、Langfuse LLM 监控、资源使用 |

### 5.5 前端文件结构

```
frontend/src/
├── pages/
│   ├── Chat.tsx                    # 现有
│   ├── Dashboard.tsx               # 现有
│   ├── Analysis.tsx                # 现有
│   ├── History.tsx                 # 现有
│   ├── Login.tsx                   # 现有
│   ├── Profile.tsx                 # 现有
│   ├── Screen.tsx                  # 现有
│   │
│   ├── quality/                    # 🆕 数据质量
│   │   ├── QualityOverview.tsx     # 质量概览
│   │   └── QualityRules.tsx        # 质量规则管理
│   │
│   ├── lineage/                    # 🆕 数据血缘
│   │   └── LineageGraph.tsx        # 血缘可视化
│   │
│   ├── catalog/                    # 🆕 数据目录
│   │   ├── MetricsCenter.tsx       # 指标中心
│   │   ├── TagsManager.tsx         # 标签管理
│   │   └── Glossary.tsx            # 业务术语
│   │
│   ├── sync/                       # 🆕 数据同步
│   │   ├── SyncTasks.tsx           # 同步任务
│   │   └── SyncLogs.tsx            # 同步日志
│   │
│   ├── admin/                      # 现有 admin 页面
│   │   ├── DataManagement.tsx      # 现有
│   │   ├── MCPAgentConfig.tsx      # 现有
│   │   ├── ModelCenter.tsx         # 现有
│   │   ├── WorkflowConfig.tsx      # 现有
│   │   ├── WorkflowEditor.tsx      # 现有
│   │   ├── PromptManager.tsx       # 现有
│   │   ├── KnowledgeBase.tsx       # 现有
│   │   ├── ScheduledTasks.tsx      # 现有
│   │   ├── NotificationChannels.tsx# 现有
│   │   ├── ReportTemplates.tsx     # 现有
│   │   ├── Roles.tsx               # 🆕 角色权限
│   │   ├── AuditLog.tsx            # 🆕 审计日志
│   │   ├── Standards.tsx           # 🆕 数据标准
│   │   ├── SensitiveData.tsx       # 🆕 敏感数据
│   │   ├── AirflowManager.tsx      # 🆕 Airflow 管理
│   │   ├── MCPServers.tsx          # 🆕 MCP Server 管理
│   │   └── SystemMonitor.tsx       # 🆕 系统监控
│   │
│   └── WorkspaceManagerV2.tsx      # 现有
│
├── components/
│   ├── WorkspaceLayout.tsx         # 修改：新增菜单分组
│   ├── SystemLayout.tsx            # 修改：新增菜单分组
│   └── ...                         # 现有组件
```

---

## 六、数据库 Schema 变更

### 5.1 新增表 — 数据治理

```sql
-- 数据质量规则
CREATE TABLE adh_quality_rules (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    rule_type ENUM('completeness','uniqueness','accuracy','timeliness','consistency','custom') NOT NULL,
    target_table VARCHAR(200),
    target_column VARCHAR(200),
    rule_config JSON NOT NULL,           -- 规则参数（阈值、表达式等）
    severity ENUM('low','medium','high','critical') DEFAULT 'medium',
    is_active TINYINT DEFAULT 1,
    created_by BIGINT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_target (target_table)
);

-- 数据质量检查结果
CREATE TABLE adh_quality_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    rule_id BIGINT NOT NULL,
    workspace_id BIGINT DEFAULT 0,
    check_time DATETIME NOT NULL,
    passed TINYINT NOT NULL,
    total_rows BIGINT,
    failed_rows BIGINT,
    pass_rate DECIMAL(5,2),
    detail JSON,                          -- 详细失败记录采样
    elapsed_ms INT,
    INDEX idx_rule (rule_id),
    INDEX idx_time (check_time),
    INDEX idx_workspace (workspace_id)
);

-- 数据质量报告
CREATE TABLE adh_quality_reports (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    report_date DATE NOT NULL,
    total_rules INT,
    passed_rules INT,
    failed_rules INT,
    overall_score DECIMAL(5,2),
    summary JSON,                         -- 各维度得分
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_workspace_date (workspace_id, report_date)
);

-- 血缘节点
CREATE TABLE adh_lineage_nodes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    node_type ENUM('table','column','etl_job','report','metric') NOT NULL,
    node_id VARCHAR(500) NOT NULL,        -- 如 "datasource_id.schema.table_name"
    node_name VARCHAR(500),
    metadata JSON,
    UNIQUE KEY uk_node (workspace_id, node_type, node_id)
);

-- 血缘边
CREATE TABLE adh_lineage_edges (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    source_node_id BIGINT NOT NULL,
    target_node_id BIGINT NOT NULL,
    edge_type ENUM('transform','derive','join','aggregate','filter') DEFAULT 'transform',
    transform_expr TEXT,                  -- 转换表达式
    confidence DECIMAL(3,2) DEFAULT 1.00, -- 置信度（自动解析 vs 手动标注）
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_source (source_node_id),
    INDEX idx_target (target_node_id),
    INDEX idx_workspace (workspace_id)
);

-- 数据标准
CREATE TABLE adh_data_standards (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    standard_type ENUM('naming','encoding','measurement','format') NOT NULL,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    rule_config JSON NOT NULL,
    is_active TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id)
);

-- 敏感字段
CREATE TABLE adh_sensitive_fields (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    datasource_id BIGINT NOT NULL,
    table_name VARCHAR(200) NOT NULL,
    column_name VARCHAR(200) NOT NULL,
    sensitivity_level ENUM('low','medium','high','critical') DEFAULT 'medium',
    mask_type ENUM('full','partial','hash','none') DEFAULT 'partial',
    mask_config JSON,
    INDEX idx_workspace (workspace_id),
    UNIQUE KEY uk_field (datasource_id, table_name, column_name)
);
```

### 5.2 新增表 — 指标管理

```sql
-- 指标定义
CREATE TABLE adh_metrics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(200) NOT NULL,
    display_name VARCHAR(200),
    description TEXT,
    metric_type ENUM('basic','derived','composite') DEFAULT 'basic',
    calculation_type ENUM('sum','count','avg','max','min','count_distinct','custom') NOT NULL,
    expression TEXT,                      -- 计算公式（派生指标）
    unit VARCHAR(50),                     -- 单位（次、元、%）
    data_type VARCHAR(50) DEFAULT 'decimal',
    target_table VARCHAR(200),
    target_column VARCHAR(200),
    dimensions JSON,                      -- 可分析维度列表
    granularity ENUM('minute','hour','day','week','month','quarter','year') DEFAULT 'day',
    owner_id BIGINT,
    is_active TINYINT DEFAULT 1,
    tags JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_type (metric_type),
    INDEX idx_owner (owner_id)
);

-- 指标维度
CREATE TABLE adh_metric_dimensions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    metric_id BIGINT NOT NULL,
    dimension_name VARCHAR(200) NOT NULL,
    dimension_column VARCHAR(200),
    dimension_table VARCHAR(200),
    dimension_type ENUM('categorical','temporal','geographical','numerical') DEFAULT 'categorical',
    INDEX idx_metric (metric_id)
);
```

### 5.3 新增表 — 标签管理

```sql
-- 标签分类
CREATE TABLE adh_tag_categories (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(100) NOT NULL,
    parent_id BIGINT,
    sort_order INT DEFAULT 0,
    INDEX idx_workspace (workspace_id)
);

-- 标签定义
CREATE TABLE adh_tags (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    category_id BIGINT,
    name VARCHAR(200) NOT NULL,
    tag_type ENUM('manual','rule','computed','ml') DEFAULT 'manual',
    entity_type ENUM('user','table','column','metric','custom') DEFAULT 'user',
    rule_config JSON,                     -- 规则标签的配置（SQL条件等）
    data_type ENUM('string','number','boolean','date','enum') DEFAULT 'string',
    enum_values JSON,                     -- 枚举值列表
    description TEXT,
    is_active TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_category (category_id),
    INDEX idx_entity_type (entity_type)
);

-- 标签值
CREATE TABLE adh_tag_values (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    tag_id BIGINT NOT NULL,
    entity_id VARCHAR(500) NOT NULL,      -- 实体ID（用户ID、表名等）
    value VARCHAR(500),
    confidence DECIMAL(3,2) DEFAULT 1.00,
    source ENUM('manual','rule','ml','import') DEFAULT 'manual',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tag (tag_id),
    INDEX idx_entity (entity_id),
    INDEX idx_workspace (workspace_id),
    UNIQUE KEY uk_tag_entity (tag_id, entity_id)
);
```

### 5.4 新增表 — 数据同步

```sql
-- 同步任务
CREATE TABLE adh_sync_tasks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    source_type VARCHAR(50) NOT NULL,     -- mysql, postgres, api, file
    source_config JSON NOT NULL,
    target_type VARCHAR(50) NOT NULL,     -- doris, mysql, es
    target_config JSON NOT NULL,
    sync_mode ENUM('full','incremental','cdc') DEFAULT 'incremental',
    column_mapping JSON,
    schedule_cron VARCHAR(50),
    is_active TINYINT DEFAULT 1,
    owner_id BIGINT,
    last_run_at DATETIME,
    last_status VARCHAR(20),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_owner (owner_id)
);

-- 同步日志
CREATE TABLE adh_sync_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    sync_task_id BIGINT NOT NULL,
    workspace_id BIGINT DEFAULT 0,
    status ENUM('running','success','failed','cancelled') NOT NULL,
    trigger_type ENUM('schedule','manual','retry') DEFAULT 'schedule',
    rows_read BIGINT DEFAULT 0,
    rows_written BIGINT DEFAULT 0,
    rows_failed BIGINT DEFAULT 0,
    error_message TEXT,
    elapsed_ms INT,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    INDEX idx_task (sync_task_id),
    INDEX idx_workspace (workspace_id)
);
```

---

## 七、实施阶段（分 5 个 Phase）

### Phase 1: 基础设施 + 共享层（2 周）

**目标**：搭建微服务骨架，共享基础设施可独立运行

- [ ] 创建 `services/` 目录结构
- [ ] 搭建 VectorService（从现有 `common/vector/` 抽取）
- [ ] 搭建 GraphService（从现有 `rag/graph_rag/` 抽取）
- [ ] 配置 Redis Streams EventBus
- [ ] 编写 `docker-compose.yml`（All-in-One）
- [ ] 配置 Nginx API Gateway
- [ ] 编写各服务 Dockerfile
- [ ] 创建 K8s 基础清单（Namespace, ConfigMap, Secret）

### Phase 2: 核心服务拆分（3 周）

**目标**：将现有单体拆分为独立服务，功能不丢失

- [ ] **AuthService**：从 `backend/api/auth.py` + `backend/common/auth.py` 迁移
  - 用户管理、JWT 认证、工作空间
  - 新增 RBAC 角色权限
- [ ] **DataMind**：从 `backend/agent/` + `backend/nl2sql/` + `backend/rag/` 迁移
  - NL2SQL Pipeline、Agent 编排、RAG 检索
  - 知识库管理、Prompt 管理
- [ ] **DataViz**：从 `backend/api/dashboard.py` 迁移
  - Dashboard CRUD、图表管理
  - 报表生成
- [ ] **DataCatalog**：从 `backend/api/admin.py` 迁移（元数据部分）
  - 元数据管理、术语管理
  - 新增数据发现搜索
- [ ] 前端 API 路由切换到 Gateway

### Phase 3: 新模块开发（4 周）

**目标**：补齐数据中台核心缺失模块

- [ ] **DataGov 数据治理服务**：
  - 数据质量规则引擎（Week 1-2）
  - 数据血缘（SQL 解析 + 图查询）（Week 2-3）
  - 数据标准、敏感数据管理（Week 3-4）
- [ ] **DataCatalog 扩展**：
  - 指标管理模块（Week 1-2）
  - 标签管理体系（Week 2-3）
  - 全局数据搜索（Week 3-4）
- [ ] **DataFlow 数据集成服务**：
  - 集成 Airflow（Docker 部署 + REST API 客户端）（Week 1）
  - 同步任务 CRUD + DAG 动态生成（Week 2）
  - 从 Celery Beat 迁移定时任务到 Airflow Scheduler（Week 3）
  - 通知推送模块（Week 4）

### Phase 4: 前端适配 + 新页面（3 周）

**目标**：前端适配微服务 API，按新菜单结构重组页面

- [ ] **菜单重组**：修改 WorkspaceLayout（数据工作台）和 SystemLayout（系统管理）菜单结构
- [ ] **路由适配**：API 路由统一走 Gateway，前端路由对齐新菜单
- [ ] **数据质量页面**（DataGov）：质量概览 + 质量规则管理
- [ ] **数据血缘页面**（DataGov）：ReactFlow 可视化血缘图
- [ ] **指标中心页面**（DataCatalog）：指标目录、口径、维度
- [ ] **标签管理页面**（DataCatalog）：标签体系树、标签圈人
- [ ] **数据同步页面**（DataFlow）：同步任务列表 + 创建向导 + 执行日志
- [ ] **权限管理页面**（AuthService）：角色权限、审计日志
- [ ] **系统管理扩展**：数据标准、敏感数据、MCP Server、系统监控

### Phase 5: K8s 部署 + 生产就绪（2 周）

**目标**：生产级部署配置

- [ ] K8s Deployment + Service + Ingress 清单
- [ ] HPA 自动扩缩容配置
- [ ] PDB (Pod Disruption Budget)
- [ ] 健康检查 + 就绪探针
- [ ] 日志收集（EFK/Loki）
- [ ] 监控告警（Prometheus + Grafana）
- [ ] CI/CD Pipeline（GitHub Actions / GitLab CI）

---

## 八、迁移策略

### 渐进式迁移（不破坏现有功能）

1. **Phase 1-2 期间**：原 `backend/` 保持运行，新服务逐步接管网关路由
2. **路由切换**：通过 Nginx 按前缀路由，可以逐个 API 切换
3. **数据库共享**：所有服务共享同一个 MySQL Schema，通过 `workspace_id` 隔离
4. **回滚方案**：Nginx 路由可随时切回原单体

```
迁移顺序：
AuthService ──▶ DataCatalog ──▶ DataViz ──▶ DataMind ──▶ DataGov ──▶ DataFlow
(最低风险)                                                    (最高风险,含Airflow集成)
```

---

## 九、验证方式

### 功能验证

- [ ] All-in-One Docker Compose 一键启动所有服务
- [ ] 前端所有现有功能正常（NL2SQL、Dashboard、Agent 分析、工作空间）
- [ ] 新功能可访问（数据质量、血缘、指标、标签）
- [ ] 向量检索和知识图谱跨服务正常工作

### 性能验证

- [ ] API 响应时间 < 200ms（非 AI 接口）
- [ ] NL2SQL 流式响应延迟 < 500ms
- [ ] 向量搜索 QPS > 100
- [ ] 并发用户 > 100

### SaaS 验证

- [ ] 多工作空间数据完全隔离
- [ ] RBAC 权限控制生效
- [ ] 审计日志完整记录
