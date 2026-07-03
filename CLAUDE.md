# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-DataHub is a natural language business intelligence platform with a **Multi-Agent architecture**. It converts Chinese natural language queries into SQL, executes them against Apache Doris/MySQL/Elasticsearch, and returns analyzed results with visualizations.

**Core Design Principle: Prompt Engineering is the heart of Agent mode.** Agent behavior is primarily controlled through prompts (`config/agents/`), not code. Code provides safety rails (SQL validation, sensitive data filtering), but LLM decision-making is driven by well-crafted prompts.

## Tech Stack

- **Backend**: Python 3.9+, FastAPI, pymysql, Anthropic SDK
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, Zustand, ECharts, ReactFlow
- **Database**: Apache Doris (analytical queries + vectors), MySQL (metadata)
- **AI**: Multi-provider LLM via Anthropic SDK (configurable), text2vec-base-chinese embeddings (768-dim)
- **Observability**: Langfuse (LLM tracing, token usage, cost monitoring)

## Architecture

### Multi-Agent System

```
Orchestrator (Main Agent)
├── data_analysis — SQL generation + execution + analysis
├── log_analysis — ES log/metric/trace analysis
├── traffic — UV/PV, page views, time distribution, bounce rate
├── user_profiling — Geography, device, new/returning users, segmentation
├── funnel — Conversion funnel, step-by-step drop-off analysis
├── retention — Cohort retention, user lifecycle, churn prediction
├── anomaly — Statistical anomaly detection, outlier identification
├── trend — Time series trends, growth rates, seasonality
├── report — LLM-driven report generation (style-aware)
└── {custom agents} — DB-configured, file-prompted
```

**Orchestrator**: Pure orchestration — intent analysis, agent selection, context assembly, reflection, error correction, summary. Does NOT execute SQL or queries directly. Supports **parallel agent dispatch**: when the LLM returns multiple agent calls in one round, they execute concurrently via `asyncio.gather()`.

**Sub-Agents**: Domain-specific execution. Each has:
- `config/agents/{name}/skill.yaml` — metadata, route_patterns, max_retries, max_iterations
- `config/agents/{name}/system.md` — system prompt (source of truth)
- DB record (`adh_agents`) — runtime state (is_active, datasource_ids, mcp_server_ids)

### Agent Loop (Tool Calling)

`backend/agent/agent_loop.py` provides a reusable LLM-driven tool calling loop:
- Calls LLM with available tools
- Executes tool calls
- **Soft limit**: when approaching `max_iterations`, injects a summary request to let the LLM gracefully conclude
- Detects doom loops (repetitive tool calls)
- Supports cancellation and timeout
- Returns partial results on hard limit exceeded (not empty error)
- Tracks tool_calls_log and total_tokens

### Agent Configuration Loading Priority

| Field | Source | Priority |
|-------|--------|----------|
| description | skill.yaml > DB | File first |
| system_prompt | system.md > DB | File first |
| route_patterns | skill.yaml only | File only |
| max_retries | DB config > skill.yaml > rules.md default | DB can override |
| max_iterations | DB config > skill.yaml > default (10) | DB can override |
| is_active, datasource_ids, mcp_server_ids | DB only | Runtime state |

### Prompt Structure

```
config/
├── agents/
│   ├── orchestrator/      — Main agent: system.md + rules.md
│   ├── data_analysis/     — SQL agent: skill.yaml + system.md
│   ├── log_analysis/      — ES agent: skill.yaml + system.md
│   ├── traffic/           — Traffic analysis agent
│   ├── user_profiling/    — User profiling agent
│   ├── funnel/            — Funnel analysis agent
│   ├── retention/         — Retention analysis agent
│   ├── anomaly/           — Anomaly detection agent
│   ├── trend/             — Trend analysis agent
│   └── report/            — Report generation agent
├── templates/             — Report style templates (LLM reference, not Jinja2)
├── skills/
│   ├── nl2sql/            — NL2SQL prompts (system, rules, examples, dialects/)
│   ├── analysis/          — Data analysis prompts
│   ├── chart/             — Chart generation prompts
│   ├── correction/        — SQL correction prompts
│   └── prediction/        — Data prediction prompts
└── rules/                 — Shared rules (date-handling, limit-policy, null-handling, sql-safety)
```

**Loading**: `loader.py` loads from files. DB (`adh_prompts`) can override for dynamic updates.

### Three Query Modes

1. **Quick** — RAG + LLM SQL gen + execute (fast, single pass)
2. **Deep** — Loop Engineering with metadata supplement loops
3. **Agent** — Multi-Agent with tool calling, autonomous planning, error correction, parallel dispatch

### Scheduled Tasks System

```
backend/
├── api/scheduled_task.py      — REST API (tasks, channels, templates, reports)
├── services/scheduled_task_service.py — CRUD service
├── tasks/
│   ├── executor.py            — Task execution (SQL/Agent/MCP modes)
│   └── notification.py        — Notification sender (DingTalk/Feishu/WeCom/Email/Webhook)
```

**Execution modes**:
- **SQL mode**: Direct SQL execution against datasource
- **Agent mode**: Orchestrator-driven multi-agent execution
- **MCP mode**: Agent execution with MCP server context

**Report generation**: LLM-driven (not Jinja2 placeholders). Loads template as style reference, sends data + style to LLM for intelligent report generation with analysis insights.

**Configuration**: task_config supports multi-select for datasource_ids, mcp_server_ids, agent_names. max_iterations configurable per-task.

### Langfuse Integration

LLM observability via Langfuse (`@observe` decorator):
- `backend/common/llm/langfuse_client.py` — Singleton client, eagerly initialized
- `backend/common/llm/llm_client.py` — `@observe(as_type="generation")` on all 4 LLM functions
- Automatic tracing of Anthropic SDK calls (including streaming + thinking blocks)
- Config: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` in `.env`

### Database Separation

- **Metadata DB** (MySQL): table/column/term metadata, user data, config
- **Vector DB** (Doris): HNSW vector embeddings for RAG retrieval
- Connections: `get_metadata_conn()` / `get_vector_conn()` — separate pools

### Workspace (Multi-Tenant)

- **Workspace Service**: `backend/services/workspace_service.py` (v1), `workspace_service_v2.py` (v2)
- **Workspace API**: `backend/api/workspace.py` (v1), `workspace_v2.py` (v2)
- **Isolation**: Datasources, agents, dashboards, users per workspace
- **Frontend**: `WorkspaceManager.tsx`, `WorkspaceSelector.tsx`, `WorkspaceLayout.tsx`

## Project Structure

```
backend/
├── agent/                  # Agent implementations
│   ├── base.py             # BaseAgent, AgentResult
│   ├── agent_loop.py       # Tool calling loop (soft limit, doom loop, timeout, cancel)
│   ├── configurable_agent.py — DB-configured agent (loads prompts from files)
│   ├── data_analysis_agent.py — Built-in data analysis agent
│   ├── sql_agent.py        # SQL execution agent
│   └── router.py           # Agent routing (route_patterns from skill.yaml)
├── api/                    # FastAPI route handlers
│   ├── chat.py             # Chat + feedback + pipeline routing
│   ├── pipeline.py         # Quick/Deep/Agent pipeline endpoints
│   ├── admin.py            # Admin CRUD operations
│   ├── admin_workflow.py   # Workflow config & execution logs
│   ├── dashboard.py        # Dashboard CRUD
│   ├── scheduled_task.py   # Scheduled tasks, channels, templates, reports
│   ├── workspace.py        # Workspace management (v1)
│   ├── workspace_v2.py     # Workspace management (v2)
│   ├── embed.py            # Embed integration API
│   ├── mcp_market.py       # MCP server marketplace
│   ├── history.py          # Query audit history
│   ├── auth.py             # JWT authentication
│   └── ...
├── services/               # Business logic layer
│   ├── workspace_service.py    # Workspace service (v1)
│   ├── workspace_service_v2.py # Workspace service (v2)
│   └── scheduled_task_service.py — Scheduled task CRUD + report/channel/template management
├── tasks/                  # Background task execution
│   ├── executor.py         — Task executor (SQL/Agent/MCP modes, LLM report generation)
│   └── notification.py     — Notification sender (DingTalk/Feishu/WeCom/Email/Webhook)
├── common/                 # Shared infrastructure
│   ├── config.py           # Environment config (ADH_*, METADATA_DB_*, VECTOR_DB_*, LANGFUSE_*)
│   ├── db/                 # MetadataDB + VectorDB connection pools
│   ├── llm/                # LLM client, embedding, token estimation, Langfuse client
│   ├── vector/             # VectorStore abstraction (Doris HNSW)
│   ├── crypto.py           # AES-256-GCM encryption
│   └── ttl_cache.py        # LRU cache
├── config/                 # Prompt & Agent configurations (file-based)
│   ├── agents/             # Agent configs (skill.yaml + system.md per agent)
│   ├── templates/          — Report style templates (LLM reference)
│   ├── skills/             # Skill prompts (nl2sql, analysis, chart, etc.)
│   ├── rules/              # Shared rules
│   ├── loader.py           # Prompt loader
│   └── agent_loader.py     # Agent config loader + graph builder
├── connectors/             # External connectors
│   └── es_connector.py     # Elasticsearch connector
├── mcp_client/             # MCP (Model Context Protocol) client
│   ├── client.py           # MCP server connection management
│   ├── registry.py         # MCP tool registry
│   └── tools.py            # MCP tool execution
├── models/                 # Pydantic schemas
├── nl2sql/                 # NL2SQL domain
│   ├── intent/             # Intent classification + query rewriting
│   ├── orchestrator/       # Pipeline orchestration (quick, deep, agent)
│   │   ├── quick_pipeline.py
│   │   ├── deep_pipeline.py
│   │   ├── agent_pipeline.py  — Agent orchestration with parallel dispatch
│   │   ├── pipeline_orchestrator.py
│   │   └── workflow/       # Loop Engine workflow
│   ├── prompt/             # Prompt construction (M-Schema, ER, terms)
│   └── sql/                # SQL validation, execution, semantic checking
├── rag/                    # RAG retrieval (vector + BM25 + strategies)
│   ├── rag_retriever.py
│   ├── rag_retriever_v2.py
│   ├── table_selector.py
│   ├── bm25.py
│   ├── terminology_manager.py
│   └── strategies/         # Retrieval strategies (full_table, column_first, two_stage, bidirectional)
└── templates/              # Legacy YAML templates (being migrated to config/)
```

## Key Design Decisions

### Prompt > Code for Agent Behavior
- Agent routing rules → `rules.md`
- Agent capabilities → `skill.yaml`
- Agent system prompts → `system.md`
- Code only handles safety rails (SQL validation, LIMIT enforcement, sensitive data filtering)

### Defensive Code for Safety
- `validate_and_fix()` — enforces LIMIT, blocks SELECT *, checks sensitive fields
- `_is_retryable_error()` — prevents infinite retries on non-recoverable errors
- No data = no LLM call (prevents hallucination)

### Error Handling
- Sub-agents handle internal retries (configurable via max_retries)
- Main agent sees `retryable` flag and decides: retry / switch agent / inform user
- Raw error messages are NOT shown to users — only business-level descriptions

### Agent Loop Safety
- **Soft limit**: approaching max_iterations → inject summary request, let LLM gracefully conclude
- **Hard limit**: max_iterations exceeded → return partial results (not empty error)
- Doom loop detection: same tool called N times consecutively → abort
- Timeout: per-agent timeout prevents hung executions
- Cancellation: user can cancel running agent tasks
- Token tracking: input/output tokens accumulated per loop

### Parallel Agent Dispatch
- Orchestrator can dispatch multiple sub-agents in parallel (asyncio.gather)
- LLM decides parallel vs serial: multiple agent calls in one round = parallel, one at a time = serial
- Other tools (MCP) always execute serially

## Common Commands

```bash
# Backend
cd backend && python main.py                    # Start FastAPI on port 8000

# Frontend
cd frontend && npm run dev                      # Dev server on port 3000

# Database sync
cd sync && python metadata_sync.py              # Sync table metadata
cd sync && python rebuild_vectors_v2.py         # Rebuild vector embeddings

# Database migration
cd docker/mysql && mysql -u root -p < init.sql
cd docker/mysql && mysql -u root -p < workspace_migration.sql
cd docker/mysql && mysql -u root -p < scheduled_task_migration.sql
cd docker/mysql && mysql -u root -p < report_agent_migration.sql
cd docker/mysql && mysql -u root -p < analysis_agents_migration.sql
```

## Important Notes

- **Doris DUPLICATE KEY tables do not support UPDATE** — use UNIQUE KEY for tables that need updates
- **Always sanitize DataFrame rows** before JSON serialization (Timestamp → isoformat, Decimal → float)
- **Business terms are DB-driven** — use `terminology_manager.py`, don't hardcode
- **Vector search uses Doris** (`VECTOR_DB_*` config), metadata uses MySQL (`METADATA_DB_*` config) — separate connection pools
- **Agent prompts are file-first** — edit `config/agents/{name}/system.md` to change agent behavior, DB is for runtime state only
- **Feedback loop** — User feedback (👍👎) is injected into prompt context for subsequent queries
- **Workspace isolation** — Datasources, agents, and dashboards are scoped to workspaces
- **Langfuse tracing** — All LLM calls are automatically traced via `@observe` decorator
- **Report generation** — LLM-driven with template as style reference, not Jinja2 placeholder substitution
