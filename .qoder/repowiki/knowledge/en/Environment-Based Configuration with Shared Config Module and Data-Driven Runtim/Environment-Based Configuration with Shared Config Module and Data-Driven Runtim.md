---
kind: configuration_system
name: Environment-Based Configuration with Shared Config Module and Data-Driven Runtime Settings
category: configuration_system
scope:
    - '**'
source_files:
    - services/shared/common/config.py
    - services/shared/common/db/metadata_db.py
    - services/shared/common/db/datasource_db.py
    - .env.example
    - services/.env
    - docker-compose.yml
    - data/brand_settings.json
    - frontend/src/stores/brandStore.ts
    - frontend/src/config/pipeline.ts
    - frontend/src/config/pipeline-monitoring.ts
    - services/datamind/config/agents/ai_assistant/skill.yaml
    - services/datamind/config/agents/orchestrator/rules.md
---

## Overview

AI-DataHub uses a layered configuration system centered on environment variables loaded from `.env` files, supplemented by data-driven runtime settings stored in the metadata database and JSON files. There is no centralized configuration framework; instead, each Python microservice imports a shared `services/shared/common/config.py` module that reads `os.environ` (populated via `python-dotenv`) and exposes typed constants.

## Environment Loading Strategy

The single source of truth for environment loading lives in `services/shared/common/config.py`. On import it attempts to load `services/.env` first, then falls back to `backend/.env`, using `dotenv.load_dotenv(..., override=True)`. If `python-dotenv` is unavailable, the import is silently ignored and all values fall back to defaults or existing process environment variables. The root `.env.example` documents every supported variable; the deployed `services/.env` holds actual secrets.

Docker Compose at the repo root (`docker-compose.yml`) also injects a top-level `.env` file into the backend container via `env_file: ./.env` and mounts it read-only as `/app/.env`, so the same file drives both local dev and containerized runs.

## Configuration Categories

### Infrastructure & Databases
- **Metadata DB** (`METADATA_DB_*`): Host/port/user/password/database, with legacy `DORIS_*` aliases still resolved for backward compatibility. `METADATA_DB_TYPE` selects between `mysql`, `doris`, or `sqlite` implementations in `metadata_db.py`, which provides a unified `MetadataDB` interface backed by `dbutils.PooledDB` (MySQL/Doris) or an in-process SQLite connection.
- **Vector DB** (`VECTOR_DB_*`): Separate from metadata storage; `VECTOR_DB_TYPE` can be `doris`, `default` (in-memory numpy), or another MySQL-compatible store. Vector pool is initialized lazily and independently from the metadata pool.
- **Redis** (`REDIS_URL`): Celery broker/result backend and distributed lock target.
- **Neo4j** (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`): Optional graph database for knowledge graph features.
- **Qdrant** (`QDRANT_*`): Optional alternative vector store.
- **Gateway** (`GATEWAY_URL`, `GATEWAY_TIMEOUT`, `GATEWAY_ENABLED`): Rust SQL engine with row-level security enforcement.

### Application Secrets
- `ADH_SECRET_KEY` (with `CHATBI_SECRET_KEY` fallback) signs JWTs and encrypts passwords.
- `ADH_DEFAULT_ADMIN_PASSWORD` seeds the initial admin account.

### LLM & Embedding
- `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL` configure the Anthropic client.
- `EMBEDDING_MODEL_PATH`, `EMBEDDING_DIM`, `EMBEDDING_HF_ENDPOINT`, `EMBEDDING_MODEL_CACHE_DIR` control local embedding model loading from HuggingFace.
- Langfuse observability keys (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`) are auto-enabled when both keys are present.

### Service Ports
`SERVICE_PORTS` and `MCP_PORTS` dictionaries define default ports for each microservice (`datamind`, `datagov`, `dataflow`, `dataviz`, `datacatalog`, `authservice`, `vectorservice`, `graphservice`). These are used by orchestration scripts and MCP clients.

## Data-Driven Runtime Configuration

Beyond static env vars, several settings are loaded from the metadata database at runtime:

- **Datasource connections**: `get_datasource_by_id()` in `datasource_db.py` queries `adh_datasources`, decrypts passwords if flagged via `is_encrypted()`/`decrypt_password()`, and returns a connection factory for MySQL/Doris/Elasticsearch.
- **LLM model config**: `model_config_service.py` falls back to `.env` defaults when no database record exists, allowing per-deployment overrides without code changes.
- **Brand settings**: `data/brand_settings.json` plus the frontend `brandStore.ts` fetches `/admin/brand` from the API to dynamically set app name, logo URL, favicon, and document title at runtime.
- **Agent skills & rules**: Under `services/datamind/config/agents/<agent>/skill.yaml` and `system.md`, plus `rules/` and `skills/` directories, agents are configured declaratively via YAML/Markdown — routing patterns, input schemas, retry limits, and behavioral rules — loaded by `config/loader.py` and `config/agent_loader.py`.

## Frontend Configuration

Frontend configuration is split into two layers:

1. **Build-time constants** in `frontend/src/config/pipeline.ts` and `pipeline-monitoring.ts`: pipeline modes (`auto|quick|deep|agent`), stage definitions, timeouts, and performance thresholds are hard-coded TypeScript constants consumed by the UI.
2. **Runtime state** via Zustand stores (`brandStore.ts`, `themeStore.ts`, `workspaceStore.ts`) that fetch settings from backend APIs and apply them to the DOM (favicon, document title, theme).

## Conventions & Constraints

- All new service configuration should be added to `services/shared/common/config.py` as an `os.getenv(...)` constant with sensible defaults; services must not call `os.getenv` directly elsewhere.
- Legacy `CHATBI_*` / `DORIS_*` variables are preserved as fallbacks but marked deprecated in comments — new deployments should use `ADH_*` / `METADATA_DB_*` / `VECTOR_DB_*`.
- Database credentials stored in the metadata database are encrypted with AES via `crypto.py`; plaintext passwords must never be persisted.
- `HF_ENDPOINT` is automatically injected into the process environment when missing, ensuring HuggingFace model downloads work behind proxies.
- Docker Compose always mounts `.env` as a read-only volume into containers, preventing accidental runtime edits inside containers.
- Agent configuration under `datamind/config/` follows a strict directory-per-agent layout with paired `skill.yaml` + `system.md` files, enabling hot-reload of agent behavior without code changes.