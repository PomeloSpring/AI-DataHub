---
kind: dependency_management
name: Per-Service Python Requirements + Frontend npm Lockfiles (No Monorepo Dependency Orchestration)
category: dependency_management
scope:
    - '**'
source_files:
    - services/shared/common/requirements.txt
    - services/aiplatform/requirements.txt
    - services/dataflow/requirements.txt
    - services/datamind/requirements.txt
    - services/datamind/Dockerfile
    - frontend/package.json
    - frontend/package-lock.json
    - frontend/sdk/package.json
---

## What system/approach is used

This monorepo uses a **per-package, per-service dependency declaration** model with no centralized lockfile or shared dependency graph:

- **Python services**: each FastAPI microservice under `services/<name>/` declares its own `requirements.txt`, and the shared library lives in `services/shared/` with its own `requirements.txt`. Dockerfiles install dependencies by copying both the service-specific and shared `requirements.txt` files and running `pip install -r ...`.
- **Frontend**: the main app (`frontend/package.json`) and the embeddable SDK (`frontend/sdk/package.json`) are separate npm packages, each with its own `package.json`. The frontend root ships a committed `package-lock.json` (npm v3 lockfile) that pins every transitive dependency. The SDK package does not ship a lockfile.
- There is **no** `go.mod`, `Cargo.toml`, `pnpm-workspace.yaml`, `yarn.lock` at the repo root, no `vendor/` directory for Go or other vendored third-party code, and no private registry configuration visible in the repository.

## Key files and packages

- **Python per-service manifests**
  - `services/aiplatform/requirements.txt` — FastAPI, uvicorn, pymysql, pydantic, python-dotenv
  - `services/dataflow/requirements.txt` — FastAPI, uvicorn, pymysql, httpx, python-dotenv, dbutils, pydantic
  - `services/datamind/requirements.txt` — FastAPI, uvicorn[standard], pydantic, python-jose[cryptography], python-multipart; comments explicitly state that backend dependencies (pymysql, pandas, anthropic, langfuse, numpy) are "already installed in the main backend environment" via `sys.path`
  - `services/shared/common/requirements.txt` — the shared base: fastapi>=0.110.0, uvicorn[standard]>=0.29.0, pymysql, PyJWT, python-dotenv, DBUtils, httpx, pydantic>=2.6.0, python-multipart, mcp>=1.0.0, starlette>=0.36.0
- **Docker build integration**
  - Each service's `Dockerfile` copies `services/shared/common/requirements.txt` first, then the service's own `requirements.txt`, and installs them with `pip install --no-cache-dir -r ...`. Example: `services/datamind/Dockerfile`.
- **Frontend manifests**
  - `frontend/package.json` — declares all runtime and dev dependencies using caret ranges (e.g. `react^18.3.1`, `@radix-ui/*^1.x`, `vite^6.0.0`).
  - `frontend/package-lock.json` — committed npm lockfile pinning exact versions of every transitive dependency.
  - `frontend/sdk/package.json` — standalone publishable SDK package (`@chatbi/sdk`) with minimal deps (typescript, vite as devDependencies).

## Architecture and conventions

- **Shared Python base**: `services/shared/common/requirements.txt` centralizes the common web stack (FastAPI, uvicorn, PyMySQL, PyJWT, httpx, pydantic, MCP). Services add only their incremental needs on top. This is enforced by the Dockerfiles which always install the shared requirements before the service-specific ones.
- **Loose version ranges**: Python manifests use `>=X.Y.Z` minimums rather than pinned versions or exact pins. For example `fastapi>=0.100.0` vs `fastapi>=0.110.0` across services. This means builds can resolve different minor/patch versions unless constrained by a higher-level tool (not present here).
- **Frontend lockfile-only pinning**: The frontend relies entirely on `package-lock.json` to freeze transitive versions. Runtime dependencies use caret ranges in `package.json`; the lockfile is what guarantees deterministic installs.
- **No workspace manager**: There is no pnpm/yarn workspaces or npm workspaces setup. The two frontend packages (`frontend/` and `frontend/sdk/`) are independent npm projects built separately.
- **No vendoring**: No `vendor/`, `third_party/`, or equivalent directories exist. All Python and JS dependencies are fetched from PyPI / npm registries at build time.

## Conventions and constraints

Observed patterns (descriptive):
- Every deployable Python service has its own `requirements.txt` plus a `Dockerfile` that layers shared then service-specific installs.
- The `datamind` service's `requirements.txt` documents an explicit convention that backend libraries are consumed via `sys.path` injection rather than pip, indicating a multi-layered Python import layout where some services depend on sibling code outside the package manager.
- Frontend dependencies are declared with caret ranges (`^`) allowing minor/patch upgrades within the major version; determinism comes from the committed `package-lock.json`.
- The SDK package is intentionally minimal and decoupled from the main frontend dependency tree.

Constraints / rules enforced by the codebase:
- Docker images must include both `services/shared/common/requirements.txt` and the service-specific `requirements.txt` because the Dockerfiles copy and install both in that order (enforced by the build scripts).
- The `datamind` Dockerfile sets `ENV PYTHONPATH=/app` and copies `backend/` and `config/` alongside `services/`, enforcing that this service imports shared/backend modules directly from the filesystem path rather than through pip.
- No private registry or proxy configuration is present in any manifest or Dockerfile; all packages are resolved from default public registries (PyPI, npmjs.org).

Note: there is no evidence of automated update tooling (Dependabot, Renovate), no `Pipfile`/`Pipfile.lock`, no `pyproject.toml`, no `setup.py`, and no Go module management.