---
kind: external_dependency
name: Anthropic Claude — Primary LLM Provider
slug: anthropic-claude
category: external_dependency
category_hints:
    - vendor_identity
    - auth_protocol
scope:
    - '**'
---

Anthropic's Claude model is the configured LLM provider, accessed via the Anthropic SDK using `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and `ANTHROPIC_MODEL` environment variables. The default model is `claude-sonnet-4-20250514` and the base URL defaults to `https://api.anthropic.com`.