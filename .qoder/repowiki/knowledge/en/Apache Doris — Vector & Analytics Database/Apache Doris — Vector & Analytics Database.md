---
kind: external_dependency
name: Apache Doris — Vector & Analytics Database
slug: apache-doris
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
---

Apache Doris is the vector database used for HNSW vector search and analytics queries. It is configured via `VECTOR_DB_*` environment variables (host/port/user/password/database) in `services/.env`, with a default fallback to MySQL when not set. The vectorservice module connects to it for embedding storage and retrieval; the shared config also exposes legacy `DORIS_*` aliases for backward compatibility.