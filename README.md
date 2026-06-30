# 🧠 AI-DataHub

> **Natural Language Business Intelligence Platform with Multi-Agent Architecture**

Ask questions in plain Chinese, get data insights with visualizations. No SQL knowledge required.

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg)](https://reactjs.org)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ✨ What It Does

```
User: "近一个月每个医院的扫描次数趋势"

AI-DataHub:
  1. 🔍 理解意图 → 需要统计扫描次数，按医院分组，时间范围近一个月
  2. 📊 检索元数据 → 找到 tableA 表，确认 hospital_name、scan_time 字段
  3. 🧠 生成 SQL → SELECT hospital_name, COUNT(*) ... WHERE scan_time >= ... GROUP BY ...
  4. ⚡ 执行查询 → 返回 15 行数据
  5. 📈 推荐图表 → 折线图（趋势分析）
  6. 💡 分析结果 → "本月扫描量整体平稳，A 医院扫描量最高..."
```

## 🏗️ Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Frontend (React 18)                        │
│  Chat │ Dashboard │ Admin │ Playground │ Workspace │ History      │
├──────────────────────────────────────────────────────────────────┤
│                        API Layer (FastAPI)                         │
│  /chat │ /pipeline │ /admin │ /dashboard │ /workspace │ /embed    │
├──────────────────────────────────────────────────────────────────┤
│                     Orchestration Layer                            │
│  ┌─────────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │  Pipeline        │  │  Agent Loop  │  │  Workspace Service   │ │
│  │  Orchestrator    │  │  (Tool Call) │  │  (Multi-Tenant)      │ │
│  └────────┬────────┘  └──────┬───────┘  └──────────────────────┘ │
├───────────┼──────────────────┼────────────────────────────────────┤
│           ▼                  ▼                                     │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                  Multi-Agent System                         │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌────────────────────┐ │  │
│  │  │ Orchestrator │  │ Data        │  │ Custom Agents      │ │  │
│  │  │ (Routing +   │  │ Analysis    │  │ (DB-configured,    │ │  │
│  │  │  Reflection) │  │ Agent       │  │  file-prompted)    │ │  │
│  │  └─────────────┘  │ - SQL Gen   │  │ - Log Analysis     │ │  │
│  │                    │ - Execute   │  │ - MCP Tools        │ │  │
│  │                    │ - Analyze   │  │ - Custom Logic     │ │  │
│  │                    └─────────────┘  └────────────────────┘ │  │
│  └────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│                       Data Layer                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │  Doris   │  │  MySQL   │  │ Elasticsearch│  │  MCP Servers│ │
│  │ (Analytics│  │(Metadata │  │ (Logs/Metrics│  │  (External  │ │
│  │  Vectors) │  │  Config) │  │   Traces)    │  │   Tools)    │ │
│  └──────────┘  └──────────┘  └──────────────┘  └─────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Multi-Agent System

```
Orchestrator (Main Agent)
├── Intent Analysis → Agent Selection
├── Context Assembly (history, feedback, metadata)
├── Reflection & Error Correction
└── Summary Generation
    │
    ├── data_analysis_agent
    │   ├── SQL Generation (NL2SQL)
    │   ├── Query Execution (Doris/MySQL/ES)
    │   ├── Result Analysis & Chart Recommendation
    │   └── Self-Correction on SQL Errors
    │
    ├── log_analysis
    │   ├── ES Log Query (by Index, _id, traceId)
    │   ├── Metrics Trend Analysis
    │   └── Distributed Trace Analysis
    │
    └── {custom agents} — DB-configured, file-prompted
        ├── MCP Tool Integration
        └── Domain-Specific Logic
```

### Agent Loop (Tool Calling)

```
AgentLoop.run()
  │
  ├── 1. Build messages (system + history + question)
  ├── 2. Call LLM with tools
  │     ├── No tool calls → Return final answer
  │     └── Has tool calls → Execute tools
  ├── 3. Doom Loop Detection (repetitive tool calls)
  ├── 4. Timeout & Cancellation Support
  └── 5. Return AgentResult (reply, sql, data, tool_calls, tokens)
```

### Prompt-Driven Design

> **Core Principle: Prompt Engineering is the heart of Agent mode.** Agent behavior is controlled through prompts, not code. Code provides safety rails; LLM decisions are driven by well-crafted prompts.

```
config/
├── agents/
│   ├── orchestrator/          ← Main agent
│   │   ├── system.md          ← Orchestration strategy
│   │   └── rules.md           ← Scheduling rules, constraints
│   ├── data_analysis/         ← SQL agent
│   │   ├── skill.yaml         ← Capabilities, route patterns, retry config
│   │   └── system.md          ← Data analysis instructions
│   └── log_analysis/          ← ES agent
│       ├── skill.yaml         ← ES/metrics/traces capabilities
│       └── system.md          ← Observability analysis instructions
├── skills/
│   ├── nl2sql/                ← NL2SQL prompts (system, rules, examples, dialects/)
│   ├── analysis/              ← Data analysis prompts
│   ├── chart/                 ← Chart generation prompts
│   ├── correction/            ← SQL correction prompts
│   └── prediction/            ← Data prediction prompts
└── rules/                     ← Shared rules (date-handling, limit-policy, null-handling, sql-safety)
```

- **Add a new agent**: Create a directory with `skill.yaml` + `system.md`, restart
- **Change agent behavior**: Edit `system.md`, restart
- **Tune retry logic**: Edit `skill.yaml` or override in admin page

### Three Query Modes

| Mode | Pipeline | Use Case |
|------|----------|----------|
| **Quick** | RAG → SQL → Execute | Simple queries, fast response |
| **Deep** | Loop Engineering with metadata supplement | Complex queries, multi-table |
| **Agent** | Multi-Agent with autonomous tool calling | Full autonomy, error recovery |

### RAG Retrieval Pipeline

```
Question → BM25 Sparse + Vector Dense → RRF Fusion → Table Selection
    ↓
Metadata Retrieval (tables, columns, terms, relations)
    ↓
Prompt Construction (M-Schema + ER diagram + terminologies + examples)
    ↓
LLM SQL Generation → Validation → Execution → Analysis
```

### Workspace (Multi-Tenant)

```
System
├── Workspace A
│   ├── Datasources (isolated)
│   ├── Agents (custom config)
│   ├── Dashboards
│   └── Users & Permissions
├── Workspace B
│   └── ...
└── System Admin (cross-workspace)
```

## 🚀 Features

### Core
- 🗣️ **Natural Language to SQL** — Ask questions in Chinese, get SQL queries
- 📊 **Auto Visualization** — Recommends chart types (line, bar, pie, funnel, etc.)
- 🔄 **Self-Correction** — SQL errors trigger automatic retry with error context
- 🧠 **RAG-Enhanced** — Vector search for table metadata, business terms, SQL templates
- 🌐 **Multi-Datasource** — Doris, MySQL, Elasticsearch in one platform

### Agent System
- 🤖 **Multi-Agent Orchestration** — Main agent dispatches to specialized sub-agents
- 📝 **Prompt-First Design** — Agent behavior controlled by markdown files
- 🔁 **Intelligent Retry** — Sub-agents handle internal retries, main agent decides strategy
- 🛡️ **Anti-Hallucination** — No data = no LLM call, strict data authenticity rules
- 🔗 **Context Passing** — Main agent extracts key info from conversation history
- 🔧 **Agent Loop** — Tool calling with doom loop detection, timeout, cancellation

### Observability
- 📋 **Log Analysis** — Query ES logs by Index, _id, traceId
- 📈 **Metrics Analysis** — Trend detection, anomaly identification
- 🔍 **Trace Analysis** — Distributed tracing, latency bottleneck identification

### Enterprise
- 🔐 **JWT Authentication** — Role-based access control
- 🔒 **Sensitive Data Protection** — Column-level sensitivity classification
- 📝 **Query Audit Log** — Full query history with execution details
- 👍👎 **User Feedback** — Thumbs up/down on query results, feeds back into prompt context
- 🔌 **MCP Integration** — Model Context Protocol for external tool integration
- 📊 **Dashboard** — Drag-and-drop dashboard builder with ECharts
- 🏢 **Workspace** — Multi-tenant workspace isolation
- 🔗 **Embed Integration** — Embed ChatBI into external applications via API

### Admin
- 📝 **Prompt Manager** — Edit and version control prompts
- ⚙️ **Workflow Config** — Configure Loop Engineering pipeline steps
- 🤖 **Agent Config** — Enable/disable agents, set datasources and MCP tools
- 📊 **Model Center** — Configure LLM providers and models
- 🔌 **MCP Market** — Browse and install MCP servers
- 📋 **Data Management** — Table metadata, business terms, SQL templates

## 📸 Screenshots

<table>
<tr>
<td><img src="docs/img/Chat数据分析.png" width="400" alt="Chat 数据分析"></td>
<td><img src="docs/img/可视化UI设计.png" width="400" alt="可视化 UI 设计"></td>
</tr>
<tr>
<td align="center">Chat 数据分析</td>
<td align="center">可视化 UI 设计</td>
</tr>
<tr>
<td><img src="docs/img/日志分析.png" width="400" alt="日志分析"></td>
<td><img src="docs/img/查询历史.png" width="400" alt="查询历史"></td>
</tr>
<tr>
<td align="center">日志分析</td>
<td align="center">查询历史</td>
</tr>
<tr>
<td><img src="docs/img/元数据管理.png" width="400" alt="元数据管理"></td>
<td><img src="docs/img/业务术语管理.png" width="400" alt="业务术语管理"></td>
</tr>
<tr>
<td align="center">元数据管理</td>
<td align="center">业务术语管理</td>
</tr>
<tr>
<td><img src="docs/img/表关系管理.png" width="400" alt="表关系管理"></td>
<td><img src="docs/img/SQL模板管理.png" width="400" alt="SQL模板管理"></td>
</tr>
<tr>
<td align="center">表关系管理</td>
<td align="center">SQL 模板管理</td>
</tr>
<tr>
<td><img src="docs/img/数据源管理.png" width="400" alt="数据源管理"></td>
<td><img src="docs/img/模型中心.png" width="400" alt="模型中心"></td>
</tr>
<tr>
<td align="center">数据源管理</td>
<td align="center">模型中心</td>
</tr>
<tr>
<td><img src="docs/img/MCP_Agent管理.png" width="400" alt="MCP Agent 管理"></td>
<td><img src="docs/img/工作空间与权限管理.png" width="400" alt="工作空间与权限管理"></td>
</tr>
<tr>
<td align="center">MCP Agent 管理</td>
<td align="center">工作空间与权限管理</td>
</tr>
</table>

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.9+, FastAPI, Anthropic SDK |
| **Frontend** | React 18, TypeScript, Vite, Tailwind CSS, Zustand |
| **Database** | Apache Doris (analytics + vectors), MySQL (metadata) |
| **Search** | Elasticsearch (logs, metrics, traces) |
| **AI/ML** | Multi-provider LLM, text2vec-base-chinese embeddings (768-dim) |
| **Visualization** | ECharts, ReactFlow |
| **Integration** | MCP (Model Context Protocol), Embed API |

## 📦 Quick Start

### Prerequisites
- Python 3.9+
- Node.js 18+
- Apache Doris (or MySQL)
- Elasticsearch (optional, for log analysis)

### 1. Backend
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env  # Configure database connections
python main.py        # Starts on port 8000
```

### 2. Frontend
```bash
cd frontend
npm install
npm run dev           # Starts on port 3000
```

### 3. Database Setup
```bash
cd docker/mysql
mysql -u root -p < init.sql              # Create tables
mysql -u root -p < workspace_migration.sql  # Workspace tables (optional)

cd sync
python metadata_sync.py         # Sync table metadata
python rebuild_vectors_v2.py    # Build vector embeddings
```

## 📁 Project Structure

```
AI-DataHub/
├── backend/
│   ├── agent/                  # Multi-Agent system
│   │   ├── base.py             # BaseAgent, AgentResult
│   │   ├── agent_loop.py       # Tool calling loop (doom loop detection, timeout)
│   │   ├── configurable_agent.py  # DB-configured agent
│   │   ├── data_analysis_agent.py # Built-in data analysis agent
│   │   ├── sql_agent.py        # SQL execution agent
│   │   └── router.py           # Agent routing (route_patterns)
│   ├── api/                    # REST API endpoints
│   │   ├── chat.py             # Chat + feedback + pipeline
│   │   ├── pipeline.py         # Quick/Deep/Agent pipeline
│   │   ├── admin.py            # Admin management
│   │   ├── admin_workflow.py   # Workflow config & logs
│   │   ├── dashboard.py        # Dashboard CRUD
│   │   ├── workspace.py        # Workspace management (v1)
│   │   ├── workspace_v2.py     # Workspace management (v2)
│   │   ├── embed.py            # Embed integration API
│   │   ├── mcp_market.py       # MCP server marketplace
│   │   └── ...
│   ├── services/               # Business logic layer
│   │   ├── workspace_service.py    # Workspace service (v1)
│   │   └── workspace_service_v2.py # Workspace service (v2)
│   ├── common/                 # Shared infrastructure
│   │   ├── config.py           # Environment config
│   │   ├── db/                 # MetadataDB + VectorDB pools
│   │   ├── llm/                # LLM client, embedding, token estimation
│   │   ├── vector/             # VectorStore (Doris HNSW)
│   │   ├── crypto.py           # AES-256-GCM encryption
│   │   └── ttl_cache.py        # LRU cache
│   ├── config/                 # Prompt & Agent configurations
│   │   ├── agents/             # Agent configs (skill.yaml + system.md)
│   │   ├── skills/             # Skill prompts (nl2sql, analysis, chart, etc.)
│   │   ├── rules/              # Shared rules
│   │   ├── loader.py           # Prompt loader
│   │   └── agent_loader.py     # Agent config loader + graph builder
│   ├── connectors/             # External connectors
│   │   └── es_connector.py     # Elasticsearch connector
│   ├── mcp_client/             # MCP client integration
│   │   ├── client.py           # MCP server connection
│   │   ├── registry.py         # MCP tool registry
│   │   └── tools.py            # MCP tool execution
│   ├── models/                 # Pydantic schemas
│   ├── nl2sql/                 # NL2SQL domain
│   │   ├── intent/             # Intent classification + query rewriting
│   │   ├── orchestrator/       # Pipeline orchestration
│   │   │   ├── quick_pipeline.py    # Quick mode
│   │   │   ├── deep_pipeline.py     # Deep mode (Loop Engineering)
│   │   │   ├── agent_pipeline.py    # Agent mode
│   │   │   ├── pipeline_orchestrator.py  # Mode router
│   │   │   └── workflow/       # Loop Engine workflow
│   │   ├── prompt/             # Prompt construction (M-Schema, ER, terms)
│   │   └── sql/                # SQL validation, execution, semantic checking
│   └── rag/                    # RAG retrieval
│       ├── rag_retriever.py    # Vector + BM25 retrieval
│       ├── rag_retriever_v2.py # Enhanced retriever
│       ├── table_selector.py   # Table selection logic
│       ├── bm25.py             # BM25 sparse search
│       ├── terminology_manager.py  # Business term management
│       └── strategies/         # Retrieval strategies
│           ├── full_table.py
│           ├── column_first.py
│           ├── two_stage.py
│           └── bidirectional.py
├── frontend/
│   └── src/
│       ├── pages/              # Page components
│       │   ├── Chat.tsx        # Chat interface (Quick/Deep/Agent modes)
│       │   ├── Dashboard.tsx   # Dashboard viewer
│       │   ├── DashboardEditor.tsx  # Dashboard editor
│       │   ├── Admin.tsx       # Admin panel
│       │   ├── History.tsx     # Query history
│       │   ├── Playground.tsx  # SQL playground
│       │   ├── WorkspaceManager.tsx   # Workspace management (v1)
│       │   ├── WorkspaceManagerV2.tsx # Workspace management (v2)
│       │   ├── ModelLab.tsx    # Model testing lab
│       │   ├── ModelTrain.tsx  # Model training stats
│       │   └── admin/          # Admin sub-pages
│       │       ├── PromptManager.tsx
│       │       ├── WorkflowConfig.tsx
│       │       ├── MCPAgentConfig.tsx
│       │       ├── MCPMarket.tsx
│       │       ├── ModelCenter.tsx
│       │       ├── DataManagement.tsx
│       │       ├── IntegrationApps.tsx
│       │       └── IntegrationLogs.tsx
│       ├── components/         # Shared components
│       │   ├── Layout.tsx      # Main layout
│       │   ├── SystemLayout.tsx    # System-level layout
│       │   ├── WorkspaceLayout.tsx # Workspace layout
│       │   ├── WorkspaceSelector.tsx   # Workspace switcher (v1)
│       │   ├── WorkspaceSelectorV2.tsx # Workspace switcher (v2)
│       │   ├── ChartPicker.tsx # Chart type selector
│       │   ├── ERDiagram.tsx   # ER diagram visualization
│       │   ├── PipelineProgress.tsx   # Pipeline progress indicator
│       │   ├── WorkflowProgress.tsx   # Workflow progress indicator
│       │   └── Dashboard*.tsx  # Dashboard components
│       ├── stores/             # Zustand state management
│       │   ├── chatStore.ts    # Chat state + feedback
│       │   ├── dashboardStore.ts
│       │   ├── authStore.ts
│       │   ├── workspaceStore.ts
│       │   └── ...
│       ├── api/                # API client
│       │   ├── client.ts       # HTTP client
│       │   └── workspace.ts    # Workspace API
│       └── hooks/              # Custom React hooks
├── docker/
│   └── mysql/                  # Database init scripts
│       ├── init.sql            # Main schema
│       ├── workspace_migration.sql   # Workspace tables
│       └── workspace_migration_v2.sql
├── sync/                       # Database utilities
│   ├── metadata_sync.py        # Sync table metadata
│   └── rebuild_vectors_v2.py   # Rebuild vector embeddings
└── docs/                       # Documentation
    ├── multi-agent-design.md
    ├── workspace-design.md
    └── plan/                   # Execution plans
```

## 🔧 Configuration

### Environment Variables (.env)
```bash
# Metadata Database (MySQL)
METADATA_DB_HOST=127.0.0.1
METADATA_DB_PORT=3306
METADATA_DB_DATABASE=adh

# Vector Database (Doris)
VECTOR_DB_HOST=127.0.0.1
VECTOR_DB_PORT=9030
VECTOR_DB_DATABASE=adh

# LLM
ANTHROPIC_API_KEY=your-key
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_MODEL=claude-sonnet-4-20250514

# Embedding
EMBEDDING_MODEL_PATH=shibing624/text2vec-base-chinese
EMBEDDING_DIM=768
```

### Adding a New Agent
1. Create directory: `config/agents/my_agent/`
2. Add `skill.yaml`:
   ```yaml
   name: my_agent
   display_name: My Agent
   description: What this agent does
   datasource_type: mysql,doris
   max_retries: 2
   route_patterns:
     - "pattern1|pattern2"
   ```
3. Add `system.md` with the agent's system prompt
4. Restart — agent auto-discovered

## 📄 License

MIT License

---

<p align="center">
  <strong>Built with ❤️ for data-driven teams</strong>
</p>
