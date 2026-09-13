# AI-DataHub 项目架构

> 自然语言商业智能平台 — 中文自然语言查询 → SQL → 可视化 → 洞察，无需 SQL 知识

---

## 一、整体架构总览

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Frontend (React 18 + Vite)                         │
│   Chat · Dashboard · Catalog · Admin · KnowledgeGraph · ModelLab · Workspace    │
│   Zustand Store · Tailwind CSS · ECharts · ReactFlow · Embed SDK              │
└──────────────────────────────┬──────────────────────────────────────────────────┘
                               │ HTTP / SSE
┌──────────────────────────────▼──────────────────────────────────────────────────┐
│                         Nginx API Gateway (port 80)                             │
└──┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬─────────────────────┘
   │      │      │      │      │      │      │      │      │
   ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼
┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐
│Data  ││Data  ││Data  ││Data  ││Data  ││Auth  ││AI    ││Vector││Graph │
│Mind  ││Gov   ││Flow  ││Viz   ││Cat.  ││Svc   ││Plat. ││Svc   ││Svc   │
│:8001 ││:8002 ││:8003 ││:8004 ││:8005 ││:8006 ││:8007 ││:8010 ││:8011 │
└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘
   │       │       │       │       │       │       │       │       │
   └───────┴───────┴───────┴───────┴───────┴───┬───┴───────┴───────┘
                                               │
              ┌────────────────────────────────┼────────────────────────────┐
              │         Shared Infrastructure  │                            │
              │  ┌─────────┐ ┌─────────┐ ┌─────▼─────┐ ┌───────────────┐  │
              │  │  MySQL  │ │  Doris  │ │   Redis   │ │   Oxigraph    │  │
              │  │ :3306   │ │ :9030   │ │  :6379    │ │   :7878       │  │
              │  │(元数据) │ │(向量+OLAP)│ │(缓存/锁) │ │(RDF知识图谱) │  │
              │  └─────────┘ └─────────┘ └───────────┘ └───────────────┘  │
              │  ┌─────────┐ ┌──────────┐                                  │
              │  │ Qdrant  │ │DataEngine│                                  │
              │  │(可选向量)│ │:8082(Rust)│                                  │
              │  └─────────┘ └──────────┘                                  │
              └────────────────────────────────────────────────────────────┘
```

---

## 二、微服务清单

| 服务 | 端口 | MCP 端口 | 职责 |
|------|------|----------|------|
| **DataMind** | 8001 | 31001 | AI 引擎：NL2SQL、Agent 编排、RAG 检索、执行层调度、Chat SSE |
| **DataGov** | 8002 | 31002 | 数据治理：质量规则、数据血缘、安全分级、脱敏策略 |
| **DataFlow** | 8003 | 31003 | 数据集成：元数据同步、数据源管理 |
| **DataViz** | 8004 | 31004 | 可视化：仪表盘 CRUD、图表配置、交叉筛选、自动刷新 |
| **DataCatalog** | 8005 | 31005 | 数据目录：表/列元数据、标签、术语表、指标管理 |
| **AuthService** | 8006 | 31006 | 认证授权：JWT、RBAC、列级权限、审计日志 |
| **AIPlatform** | 8007 | — | 平台管理：MCP 服务器、Agent 配置、模型实验室、Embed、品牌、缓存 |
| **VectorService** | 8010 | 31010 | 向量检索：Doris HNSW / Qdrant / 内存 numpy，Embedding 生成 |
| **GraphService** | 8011 | — | 知识图谱：Oxigraph RDF/SPARQL 实体/关系管理、图查询、可视化数据 |

---

## 三、中间件与基础设施

### 3.1 数据存储层

| 中间件 | 版本 | 用途 | 部署方式 |
|--------|------|------|----------|
| **MySQL** | 8.0 | 元数据存储（用户/权限/工作空间/数据源/会话/配置） | Docker / 远程 |
| **Apache Doris** | — | OLAP 分析引擎 + HNSW 向量检索（双角色） | 远程集群 |
| **Redis** | 7-alpine | 缓存、Celery Broker、分布式锁 | Docker |
| **Oxigraph** | latest | 轻量级 RDF 三元组存储，SPARQL 1.1 合规（知识图谱/本体建模） | Docker（~50MB 镜像，RocksDB 持久化） |
| **Qdrant** | v1.12.1 | 可选向量数据库（替代 Doris 向量检索） | Docker |

### 3.2 AI / LLM 层

| 组件 | 说明 |
|------|------|
| **Anthropic Claude** | 主力 LLM 提供商，通过 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 配置代理 |
| **Embedding Model** | `shibing624/text2vec-base-chinese`（768 维），HuggingFace Mirror 下载 |
| **Multi-Provider LLM** | 支持多模型配置（`adh_llm_models` 表），运行时动态切换 |

### 3.3 执行层（Agent Execution Layer）

| 适配器 | 模式 | 说明 |
|--------|------|------|
| **BuiltInAdapter** | 内置 | 系统内置 Agent Loop（quick/deep pipeline） |
| **ClaudeSDKAdapter** | SDK | Claude Agent SDK，进程内 MCP + 自定义工具 |
| **QoderSDKAdapter** | SDK | Qoder Agent SDK，session 多轮恢复，in-process tools |
| **CLIProcessAdapter** | 子进程 | qodercli CLI 纯文本输出（降级模式） |

**SDK 内置工具集**（通过 `sdk_tools/` 注入执行层）：
- **catalog_tools**: `search_metadata`, `get_table_schema`, `list_datasources`
- **query_tools**: `execute_sql`（封装 validate_sql + execute_query_with_permission，200 行上限）
- **semantic_tools**: `get_metrics`, `get_glossary`, `query_by_tags`, `knowledge_search`

### 3.4 认证与安全

| 组件 | 说明 |
|------|------|
| **JWT 认证** | AuthService 签发，`ADH_SECRET_KEY` 签名 |
| **RBAC** | 角色/权限管理，工作空间级隔离 |
| **AES 加密** | 数据源密码加密存储（`crypto.py`） |
| **列级脱敏** | 敏感数据分级 + 查询时自动脱敏 |

### 3.5 外部平台集成

| 平台 | 说明 |
|------|------|
| **MCP (Model Context Protocol)** | 工具协议标准，每个服务暴露 MCP 端口，支持 stdio/sse/http 传输 |

### 3.6 共享基础设施库（`services/shared/`）

| 模块 | 路径 | 职责 |
|------|------|------|
| **common/config** | `shared/common/config.py` | 集中环境变量加载（所有服务通过此模块读取配置） |
| **common/db** | `shared/common/db/` | MetadataDB 连接池（PooledDB）、向量池 |
| **common/llm** | `shared/common/llm/` | LLM 客户端封装（多 provider） |
| **common/vector** | `shared/common/vector/` | 向量存储抽象（Doris HNSW / Qdrant / numpy） |
| **common/crypto** | `shared/common/crypto.py` | AES 加解密 |
| **common/auth** | `shared/common/auth.py` | JWT 验证中间件 |
| **common/engine_client** | `shared/common/engine_client.py` | DataEngine Rust 引擎 HTTP 客户端 |
| **common/rls_loader** | `shared/common/rls_loader.py` | 行级安全策略加载 |
| **common/cache** | `shared/common/cache/` | TTL 缓存 |
| **mcp_client** | `shared/mcp_client/` | MCP 客户端（注册、工具发现、调用） |
| **connectors** | `shared/connectors/` | 数据源连接器（ES 等） |
| **models** | `shared/models/` | 共享 Pydantic + ORM 模型 |

---

## 四、前端架构

```
frontend/src/
├── api/              # API 客户端层（14 个 API 模块）
├── components/       # 共享组件（30+ 组件）
│   ├── ToolCallTimeline    # 工具调用时间线（执行可观测）
│   ├── ThinkingBlock       # 思考过程折叠块
│   ├── ERDiagram           # ER 图可视化
│   ├── graph/              # 知识图谱可视化
│   ├── chat/               # 聊天专用组件
│   ├── editor/             # SQL/代码编辑器
│   └── ui/                 # 基础 UI 原子组件
├── pages/            # 页面路由
│   ├── Chat.tsx            # 聊天主页（Quick/Deep/Agent 三模式）
│   ├── Dashboard.tsx       # 仪表盘
│   ├── DashboardEditor.tsx # 仪表盘编辑器
│   ├── KnowledgeGraph.tsx  # 知识图谱
│   ├── WorkspaceManager*.tsx # 工作空间管理
│   ├── admin/              # 管理后台
│   ├── catalog/            # 数据目录
│   ├── lineage/            # 数据血缘
│   └── quality/            # 数据质量
├── stores/           # Zustand 状态管理
│   ├── chatStore.ts        # 聊天状态（SSE 流、消息、工具调用）
│   ├── brandStore.ts       # 品牌配置
│   ├── themeStore.ts       # 主题
│   └── workspaceStore.ts   # 工作空间
├── config/           # 构建时常量
├── hooks/            # 自定义 Hooks
├── styles/           # Tailwind CSS + 主题 Token
└── sdk/              # Embed SDK（Web Components 可嵌入库）
```

### 前端技术栈

| 技术 | 版本/说明 |
|------|-----------|
| React | 18 |
| TypeScript | 5.x |
| Vite | 构建工具 |
| Tailwind CSS | 样式系统（多主题 Token 化设计） |
| Zustand | 状态管理 |
| ECharts | 图表可视化 |
| ReactFlow | 工作流/图谱可视化 |
| React | 18 |

---

## 五、数据流架构

### 5.1 Chat 查询流（Agent 模式）

```
User Input
    │
    ▼
Chat.tsx ──POST /api/chat/send/agent/stream──▶ DataMind (chat_service.py)
    │                                              │
    │                                              ▼
    │                                     ExecutionLayerManager
    │                                              │
    │                              ┌───────────────┼───────────────┐
    │                              ▼               ▼               ▼
    │                     ClaudeSDKAdapter  QoderSDKAdapter  CLIProcessAdapter
    │                              │               │               │
    │                              └───────────────┼───────────────┘
    │                                              ▼
    │                                     SDK Tools (in-process)
    │                                     ├── search_metadata
    │                                     ├── get_table_schema
    │                                     ├── execute_sql
    │                                     └── knowledge_search
    │                                              │
    │◀────── SSE: tool_start / tool_result ────────┘
    │◀────── SSE: thinking / token / done ─────────┘
    ▼
Chat.tsx 渲染
├── ToolCallTimeline（工具调用时间线）
├── ThinkingBlock（思考过程）
├── 执行统计摘要条（轮次/工具次数/耗时）
└── ECharts 可视化
```

### 5.2 NL2SQL Pipeline（Quick/Deep 模式）

```
User Question
    │
    ▼
DataMind Pipeline Orchestrator
    │
    ├── RAG 检索 ──▶ VectorService ──▶ Doris HNSW / Qdrant
    │                                       │
    ├── 元数据加载 ──▶ DataCatalog ◀────────┘
    │
    ├── LLM 推理 ──▶ Anthropic Claude
    │
    ├── SQL 校验 ──▶ DataEngine (Rust) ──▶ MDL/RLS/方言转译
    │
    ├── 查询执行 ──▶ Doris / MySQL / ES
    │
    └── 自动可视化 ──▶ ECharts 配置生成
```

---

## 六、部署架构

### 6.1 本地开发（Shell 脚本）

```bash
./start-all.sh          # 启动所有微服务 + 前端
./stop-all.sh           # 停止所有服务
./restart-all.sh        # 重启所有服务
```

- 共享虚拟环境：`./venv/bin/python`
- 进程管理：PID 文件（`pids/`）+ 日志文件（`logs/`）
- 前端：`npm run dev`（Vite HMR）

### 6.2 Docker Compose（生产部署）

```bash
# 最小部署（后端 + 前端）
docker compose up -d

# 完整部署（含 MySQL）
docker compose -f docker-compose.full.yml up -d

# 全微服务部署（含所有中间件）
cd services && docker compose up -d
```

### 6.3 中间件端口汇总

| 中间件 | 端口 | 协议 |
|--------|------|------|
| Nginx Gateway | 80 | HTTP |
| Frontend (Vite/Nginx) | 3000 | HTTP |
| DataMind | 8001 / 31001 | HTTP / MCP |
| DataGov | 8002 / 31002 | HTTP / MCP |
| DataFlow | 8003 / 31003 | HTTP / MCP |
| DataViz | 8004 / 31004 | HTTP / MCP |
| DataCatalog | 8005 / 31005 | HTTP / MCP |
| AuthService | 8006 / 31006 | HTTP / MCP |
| AIPlatform | 8007 | HTTP |
| VectorService | 8010 / 31010 | HTTP / MCP |
| GraphService | 8011 | HTTP |
| DataEngine (Rust) | 8082 | HTTP |
| MySQL | 3306 | TCP |
| Apache Doris | 9030 | MySQL Protocol |
| Redis | 6379 | TCP |
| Oxigraph | 7878 | HTTP (SPARQL) |
| Qdrant HTTP | 6333 | HTTP |
| Qdrant gRPC | 6334 | gRPC |

---

## 七、配置管理

### 7.1 环境变量（`services/.env`）

```ini
# 元数据库
METADATA_DB_TYPE=mysql
METADATA_DB_HOST=...
METADATA_DB_PORT=3306

# 向量数据库
VECTOR_DB_TYPE=doris          # doris | qdrant | default(numpy)
VECTOR_DB_HOST=...

# LLM
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=...        # 支持代理
ANTHROPIC_MODEL=claude-sonnet-4-20250514

# Embedding
EMBEDDING_MODEL_PATH=shibing624/text2vec-base-chinese
EMBEDDING_DIM=768
HF_ENDPOINT=https://hf-mirror.com

# 知识图谱
OXIGRAPH_URL=http://localhost:7878

# DataEngine
ENGINE_SERVER_URL=http://localhost:8082
ENGINE_ENABLED=true
```

### 7.2 运行时配置（数据库驱动）

| 配置项 | 存储位置 | 说明 |
|--------|----------|------|
| 数据源连接 | `adh_datasources` 表 | 密码 AES 加密存储 |
| LLM 模型配置 | `adh_llm_models` 表 | 多 provider 动态切换 |
| MCP 服务器 | `adh_mcp_servers` 表 | stdio/sse/http 传输 |
| Agent 技能 | `datamind/config/agents/` | YAML + Markdown 声明式 |
| 品牌设置 | `data/brand_settings.json` | 应用名/Logo/主题色 |
| 工作空间执行层 | `adh_workspace_execution_layers` | 工作空间绑定执行层配置 |

---

## 八、可观测性架构

```
┌─────────────────────────────────────────────────────────────┐
│                    用户侧执行可观测设计                      │
├─────────────────────────────────────────────────────────────┤
│ ToolCallTimeline 组件                                       │
│ ├── tool_start 事件                                         │
│ ├── tool_result 事件                                        │
│ └── 实时 pending→完成                                       │
│                                                             │
│ 执行统计摘要条                                              │
│ ├── N 轮推理                                                │
│ ├── N 次工具调用                                            │
│ └── 耗时 Xs                                                │
│                                                             │
│ 历史回放（消息持久化）                                      │
│ └── 刷新后时间线完整恢复                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 九、技术栈汇总

| 层 | 技术 |
|----|------|
| **前端** | React 18, TypeScript, Vite, Tailwind CSS, Zustand, ECharts, ReactFlow |
| **后端** | Python 3.12, FastAPI, Uvicorn, SQLAlchemy, Pydantic |
| **AI/LLM** | Anthropic Claude SDK, Qoder Agent SDK, text2vec-base-chinese |
| **数据库** | MySQL 8.0, Apache Doris, Oxigraph (RDF) |
| **向量检索** | Doris HNSW, Qdrant, NumPy (内存) |
| **缓存** | Redis 7 |
| **安全** | JWT, RBAC, AES |
| **工具协议** | MCP (Model Context Protocol) |
| **SQL 引擎** | DataEngine (Rust) — MDL/RLS/方言转译 |
| **部署** | Docker Compose, Shell 脚本, Nginx |
| **嵌入** | Web Components Embed SDK |
