---
kind: build_system
name: Multi-Service Monorepo Build & Deployment via Docker Compose and Shell Orchestration
category: build_system
scope:
    - '**'
source_files:
    - docker-compose.yml
    - docker-compose.full.yml
    - services/docker-compose.yml
    - Dockerfile
    - frontend/Dockerfile
    - services/authservice/Dockerfile
    - services/shared/Dockerfile.template
    - start-all.sh
    - stop-all.sh
    - restart-all.sh
    - services/.env
    - frontend/package.json
    - frontend/nginx.conf
    - docker/mysql/init.sql
    - services/shared/migrations/001_data_platform_tables.sql
---

## Overview

AI-DataHub is a monorepo that builds and deploys nine FastAPI microservices plus a React/Vite frontend through two complementary build/deploy strategies: (1) containerized orchestration with Docker Compose, and (2) native process management via shell scripts using `uvicorn` directly. There is no Makefile or CI pipeline in the repository; build logic lives in per-service `Dockerfile`s, a shared template, and root-level shell scripts.

## Container Builds

### Python microservices
Each service under `services/<name>/` ships its own `Dockerfile` built from the repo root context (`context: ..`). The pattern is uniform:
- Base image `python:3.11-slim` (the root `Dockerfile` uses `python:3.12-slim`).
- Install system `gcc` for native extensions.
- `pip install --no-cache-dir` first on `services/shared/common/requirements.txt`, then on the service's own `requirements.txt` — this layers dependencies to maximize Docker cache reuse.
- Copies `services/shared/`, the service directory, `backend/`, and `config/` into `/app`.
- Sets `ENV PYTHONPATH=/app` and `ENV SERVICE_PORT=<port>`.
- Exposes both the HTTP port and an MCP port (e.g. `8006` + `31006`).
- Runs via `CMD ["python", "-m", "uvicorn", "services.<module>.main:app", "--host", "0.0.0.0", "--port", "<SERVICE_PORT>"]`.

A shared `services/shared/Dockerfile.template` documents the intended pattern and shows how each service should be templated (replace `SERVICE_MODULE` / `SERVICE_PORT_VALUE`).

### Frontend
`frontend/Dockerfile` is a two-stage Node/Nginx build:
1. `node:20-alpine` stage runs `npm ci` then `npm run build` (which executes `tsc && vite build` per `package.json`).
2. `nginx:alpine` stage serves the static `dist/` assets, configured by `frontend/nginx.conf`.

The root `docker-compose.yml` builds the frontend image and depends on the backend being healthy before starting it.

### Multi-stage compose files
- `docker-compose.yml` — minimal stack: one backend container + frontend, sharing a bridge network `chatbi` and a named volume `embedding-cache` for HuggingFace model caching.
- `docker-compose.full.yml` — adds MySQL 8.0 with healthcheck, mounts `./docker/mysql/init.sql` as entrypoint init, and wires environment variables for metadata DB, vector DB, LLM keys, and embedding model path.
- `services/docker-compose.yml` — the full microservice stack. It defines an `x-common-env` YAML anchor holding all shared env vars (MySQL, Doris, Redis, Neo4j, Anthropic, Langfuse, embedding config). Each service service block reuses `<<: *common-env` and declares its own `SERVICE_PORT` / `MCP_PORT`. Services are built from their per-service `Dockerfile` at the repo root context. Infrastructure services include `redis:7-alpine` and `neo4j:5-community` with persistent volumes.

## Native (non-container) Development

`start-all.sh` is the single entry point for local development without Docker. Key behaviors:
- Discovers services from a hard-coded array of `name:module:port` tuples covering all nine microservices plus the frontend.
- Uses a shared virtualenv at `$PROJECT_ROOT/venv/bin/python` with `PYTHONPATH=$PROJECT_ROOT` so imports resolve across services.
- Starts each service via `nohup python -m uvicorn <module>:app --host 0.0.0.0 --port <port> --log-level info`, logging to `logs/<service>.log` and writing PIDs to `pids/<service>.pid`.
- Checks ports with `lsof` before starting to avoid conflicts; skips already-running processes.
- The frontend is started via `npm run dev` inside `frontend/`, waiting up to 15 seconds for Vite to bind port 3000.
- Supports subcommands: `all`, `status`, `frontend`, or a specific service name.
- Loads environment from `services/.env` via `set -a; source ...; set +a` before launching services.
- Companion scripts `stop-all.sh`, `restart-all.sh`, `start.sh`, `stop.sh` mirror start/stop/status behavior.

## Configuration & Environment

Environment is centralized in `services/.env` (metadata DB, vector DB, secrets, embedding model, Langfuse, Neo4j, AI assistant flags) and passed into containers via `environment:` blocks or `env_file: .env` in compose files. Defaults are provided inline in compose YAMLs using `${VAR:-default}` syntax, allowing override via `.env` or host environment.

## Database & Schema Initialization

SQL migrations live in `docker/mysql/*.sql` (per-database init scripts mounted into MySQL's `docker-entrypoint-initdb.d`) and `services/shared/migrations/*.sql` (versioned migration files used by application code). Additional schema seeds live in `sync/create_adh_tables.sql` and `docker/doris/init.sql`, `docker/sqlite/init.sql`, `docker/neo4j/start.sh`.

## Versioning & Artifacts

- Frontend version is declared in `frontend/package.json` (`"version": "0.0.0"`) and built into a static `dist/` bundle served by Nginx.
- Python services have no package manifest beyond per-service `requirements.txt`; there is no `setup.py`, `pyproject.toml`, or published wheel/pip package — services are deployed as Docker images built from source.
- No CI/CD configuration (GitHub Actions, Jenkinsfile, etc.) was found in the repository; deployment appears to be manual via `docker compose` commands.

## Conventions Observed

- Every Python service exposes both an HTTP API port and an MCP port (e.g. 8001/31001), configured via `SERVICE_PORT` and `MCP_PORT` environment variables.
- Shared Python dependencies are always installed first in Docker layers to leverage cache invalidation semantics.
- All services import via absolute module paths rooted at `/app` (`services.authservice.main:app`), enabled by `ENV PYTHONPATH=/app`.
- Health checks use HTTP endpoints (`/api/health`) rather than TCP probes for service readiness.
- Logging and process state are file-based (`logs/`, `pids/`) in native mode; logs rotate only by append.
- The frontend SDK under `frontend/sdk/` has its own `package.json` and `vite.config.ts`, indicating a separately buildable embeddable library alongside the main app.