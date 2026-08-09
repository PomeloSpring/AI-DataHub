---
kind: logging_system
name: Per-Service Python `logging` with `LOG_LEVEL` Env and File Sinks
category: logging_system
scope:
    - '**'
source_files:
    - services/aiplatform/main.py
    - services/datamind/main.py
    - services/datagov/main.py
    - services/dataflow/main.py
    - services/shared/graphservice/main.py
    - services/shared/vectorservice/main.py
    - services/dataviz/main.py
    - services/authservice/main.py
    - services/datacatalog/main.py
    - services/shared/common/config.py
---

## What system/approach is used

The repository uses the **Python standard library `logging` module** exclusively — no third-party logging framework (e.g. structlog, loguru, python-json-logger) is configured. Each FastAPI microservice initializes its own root logger via `logging.basicConfig(...)` in its entry-point `main.py`, and every module obtains a per-module logger through `logging.getLogger(__name__)`. There is no shared logging configuration module; each service owns its formatter, level, and handler setup.

Log output is written to **files under the monorepo `logs/` directory**, one file per running process (e.g. `aiplatform.log`, `authservice.log`, `datacatalog.log`, `dataflow.log`, `datagov.log`, `datamind.log`, `dataviz.log`, `frontend.log`, `graphservice.log`, `vectorservice.log`). The per-process `start.sh` scripts in each service directory redirect stdout/stderr to these files at the OS level rather than configuring Python handlers to write to disk.

## Key files and packages

- Per-service entry points that configure logging:
  - `services/aiplatform/main.py` — `basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")`
  - `services/datamind/main.py` — reads `LOG_LEVEL` env, maps via `getattr(logging, _log_level, logging.INFO)`, adds explicit `datefmt="%Y-%m-%d %H:%M:%S"`
  - `services/datagov/main.py` — same pattern as datamind
  - `services/dataflow/main.py` — `basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), ...)`
  - `services/shared/graphservice/main.py` — same pattern
  - `services/shared/vectorservice/main.py` — hardcodes `level=logging.INFO`, format includes `[%(levelname)s]`
  - `services/dataviz/main.py` — hardcodes `level=logging.INFO`, slightly different field order in format
  - `services/authservice/main.py` — hardcodes `level=logging.INFO`, omits brackets around level
  - `services/datacatalog/main.py` — hardcodes `level=logging.INFO`, no custom format (uses default)
- Module-level loggers throughout each service's `api/` and `services/` subpackages, created via `logger = logging.getLogger(__name__)`.
- `services/shared/common/config.py` — central environment configuration for the platform (ports, DBs, Langfuse, etc.) but does **not** define a LOG_LEVEL constant; services read `LOG_LEVEL` directly from `os.environ`.
- `logs/` directory — runtime sink location for all service logs (one `.log` per process).

## Architecture and conventions

1. **Per-process initialization**: Every service calls `logging.basicConfig(...)` exactly once in its `main.py` before any other code imports application modules. This configures the root logger for that process only.
2. **Level control via environment variable**: Services that support dynamic levels read `LOG_LEVEL` from the environment (`os.getenv("LOG_LEVEL", "INFO")`). Some services hardcode `logging.INFO` instead of reading the env var, so the effective default is still INFO but cannot be overridden without changing source.
3. **Module-scoped loggers**: Business logic files obtain a logger via `logging.getLogger(__name__)`, producing hierarchical names such as `services.aiplatform.api.agents`, `services.datamind.nl2sql.orchestrator.pipeline_orchestrator`, etc. This lets operators filter by service or even by module path.
4. **Structured-ish fields**: Log lines are plain text with a fixed template containing timestamp, level, logger name, and message. No JSON envelope or structured fields (no `extra={...}` payloads). Correlation IDs or request IDs are not injected into the log context.
5. **File sink via process redirection**: The `logs/` directory exists at repo root and contains one log file per service process. The Python handlers are not explicitly configured to write to those files; instead, the service `start.sh` scripts (one per service under `services/<service>/start.sh`) launch the process with stdout/stderr redirected to `../../logs/<service>.log`. Uvicorn's own access logs also flow through this channel.
6. **Lifespan/startup hooks**: Most services use FastAPI's `lifespan` (or `@app.on_event("startup")` / `shutdown`) to emit startup/shutdown messages using the service logger.
7. **No shared logging utility**: The `services/shared/common/` package provides shared config, DB, LLM, vector, and cache utilities, but deliberately does not provide a centralized logger factory or formatter. Each service duplicates the `basicConfig` call.

## Conventions and constraints

- **Convention observed**: Every service's `main.py` calls `logging.basicConfig(...)` early, then creates a module-level `logger = logging.getLogger(__name__)` (or a named logger like `getLogger("datamind")`). All business modules follow the same pattern.
- **Convention observed**: Log level is intended to be controlled via the `LOG_LEVEL` environment variable; however, enforcement is inconsistent — some services read it, others hardcode `INFO`.
- **Convention observed**: One log file per service process lives under `logs/`; there is no rotation, retention policy, or separate error/warning stream configured in code.
- **Constraint enforced by process model**: Because each service runs as an independent process (via `uvicorn.run` or `python main.py`), `logging.basicConfig` applies only within that process boundary — cross-process correlation is not possible through the logger hierarchy alone.
- **Constraint observed**: There is no global formatter or handler registry; if a new service is added, its author must manually add a `basicConfig` call and a corresponding `logs/<service>.log` entry in the startup script. No lint rule or shared base class enforces this automatically.