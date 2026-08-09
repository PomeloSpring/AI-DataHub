---
kind: external_dependency
name: MySQL — Metadata & Application Database
slug: mysql
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
---

MySQL 8.0 is the primary metadata database storing table/column/term definitions, workspace data, scheduled tasks, and user/role information. It is configured via `METADATA_DB_*` env vars and initialized through SQL scripts under `docker/mysql/` (init.sql, workspace_migration*.sql, scheduled_task_migration.sql, etc.). The full compose stack provisions it with `max-connections=200` and utf8mb4 charset.