---
kind: business_term
name: Business Glossary
category: business_term
scope:
    - '**'
---

### 工作空间
- Definition：Multi-tenant workspace that isolates datasources, agents, dashboards, users, and permissions per tenant. Two versions exist: v1 (`WorkspaceManager.tsx`) and v2 (`WorkspaceManagerV2.tsx`) with separate API routes and migration scripts.
- Aliases：workspace、v1 workspace、v2 workspace

### Prompt Manager
- Definition：Admin feature for editing and version-controlling agent/system prompts stored as markdown files. Accessed via `/admin/prompts` and backed by the datamind pipeline execution infrastructure.
- Aliases：prompts、prompt management

### Workflow Config
- Definition：Admin feature for configuring Loop Engineering pipeline steps (steps + edges). Supports CRUD plus update and execute operations that aggregate datamind's SSE pipeline.
- Aliases：workflow、loop workflow

### MCP Market
- Definition：Admin feature for browsing and installing MCP (Model Context Protocol) servers that extend agent capabilities with external tools.
- Aliases：mcp market、MCP server marketplace

### Quick / Deep / Agent
- Definition：Three query modes of the NL2SQL pipeline: Quick (RAG → SQL → Execute), Deep (Loop Engineering with metadata supplement), and Agent (multi-agent autonomous tool calling with reflection and retry).
- Aliases：quick mode、deep mode、agent mode

### 查询历史
- Definition：Query history feature that stores past NL2SQL executions with SQL, results, and feedback. Requires a `query_type` column in the history table; missing columns cause silent empty responses.
- Aliases：history、query history

### 质量报告
- Definition：Data quality reports rendered via dashboard-driven rendering. Backend must align response schema with the actual database table columns (e.g., `rule_name`, `generated_at`).
- Aliases：quality report、quality reports

### 数据源管理
- Definition：Datasource management feature for connecting and configuring data sources (MySQL, Doris, Elasticsearch) used by agents and pipelines.
- Aliases：datasource、datasources

### 模型中心
- Definition：Admin feature for configuring LLM providers and models (Anthropic Claude and others) consumed by the multi-agent system.
- Aliases：model center、model lab
