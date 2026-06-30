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

## Architecture

### Multi-Agent System

```
Orchestrator (Main Agent)
├── data_analysis_agent — SQL generation + execution + analysis
├── log_analysis — ES log/metric/trace analysis
└── {custom agents} — DB-configured, file-prompted
```

**Orchestrator**: Pure orchestration — intent analysis, agent selection, context assembly, reflection, error correction, summary. Does NOT execute SQL or queries directly.

**Sub-Agents**: Domain-specific execution. Each has:
- `config/agents/{name}/skill.yaml` — metadata, route_patterns, max_retries
- `config/agents/{name}/system.md` — system prompt (source of truth)
- DB record (`adh_agents`) — runtime state (is_active, datasource_ids, mcp_server_ids)

### Agent Loop (Tool Calling)

`backend/agent/agent_loop.py` provides a reusable LLM-driven tool calling loop:
- Calls LLM with available tools
- Executes tool calls
- Detects doom loops (repetitive tool calls)
- Supports cancellation and timeout
- Returns when LLM produces a final answer (no more tool calls)
- Tracks tool_calls_log and total_tokens

### Agent Configuration Loading Priority

| Field | Source | Priority |
|-------|--------|----------|
| description | skill.yaml > DB | File first |
| system_prompt | system.md > DB | File first |
| route_patterns | skill.yaml only | File only |
| max_retries | DB config > skill.yaml > rules.md default | DB can override |
| is_active, datasource_ids, mcp_server_ids | DB only | Runtime state |

### Prompt Structure

```
config/
├── agents/
│   ├── orchestrator/      — Main agent: system.md + rules.md
│   ├── data_analysis/     — SQL agent: skill.yaml + system.md
│   └── log_analysis/      — ES agent: skill.yaml + system.md
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
3. **Agent** — Multi-Agent with tool calling, autonomous planning, error correction

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
│   ├── agent_loop.py       # Tool calling loop (doom loop, timeout, cancel)
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
│   ├── workspace.py        # Workspace management (v1)
│   ├── workspace_v2.py     # Workspace management (v2)
│   ├── embed.py            # Embed integration API
│   ├── mcp_market.py       # MCP server marketplace
│   ├── history.py          # Query audit history
│   ├── auth.py             # JWT authentication
│   └── ...
├── services/               # Business logic layer
│   ├── workspace_service.py    # Workspace service (v1)
│   └── workspace_service_v2.py # Workspace service (v2)
├── common/                 # Shared infrastructure
│   ├── config.py           # Environment config (ADH_*, METADATA_DB_*, VECTOR_DB_*)
│   ├── db/                 # MetadataDB + VectorDB connection pools
│   ├── llm/                # LLM client, embedding, token estimation
│   ├── vector/             # VectorStore abstraction (Doris HNSW)
│   ├── crypto.py           # AES-256-GCM encryption
│   └── ttl_cache.py        # LRU cache
├── config/                 # Prompt & Agent configurations (file-based)
│   ├── agents/             # Agent configs (skill.yaml + system.md per agent)
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
│   │   ├── agent_pipeline.py
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
- Doom loop detection: same tool called N times consecutively → abort
- Timeout: per-agent timeout prevents hung executions
- Cancellation: user can cancel running agent tasks
- Token tracking: input/output tokens accumulated per loop

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
```

## Important Notes

- **Doris DUPLICATE KEY tables do not support UPDATE** — use UNIQUE KEY for tables that need updates
- **Always sanitize DataFrame rows** before JSON serialization (Timestamp → isoformat, Decimal → float)
- **Business terms are DB-driven** — use `terminology_manager.py`, don't hardcode
- **Vector search uses Doris** (`VECTOR_DB_*` config), metadata uses MySQL (`METADATA_DB_*` config) — separate connection pools
- **Agent prompts are file-first** — edit `config/agents/{name}/system.md` to change agent behavior, DB is for runtime state only
- **Feedback loop** — User feedback (👍👎) is injected into prompt context for subsequent queries
- **Workspace isolation** — Datasources, agents, and dashboards are scoped to workspaces
