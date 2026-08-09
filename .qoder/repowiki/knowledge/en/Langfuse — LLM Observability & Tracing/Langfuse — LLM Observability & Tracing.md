---
kind: external_dependency
name: Langfuse — LLM Observability & Tracing
slug: langfuse
category: external_dependency
category_hints:
    - vendor_identity
    - auth_protocol
scope:
    - '**'
---

Langfuse provides LLM call tracing, token usage tracking, and cost monitoring. It is enabled when both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set; the base URL defaults to `https://cloud.langfuse.com` but can be overridden via `LANGFUSE_BASE_URL`. In this project's runtime it points to a local instance at `http://localhost:13000`.