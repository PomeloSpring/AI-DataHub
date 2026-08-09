---
kind: external_dependency
name: Hugging Face Mirror — Embedding Model Host
slug: huggingface-hub-hf-mirror
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
---

The embedding model `shibing624/text2vec-base-chinese` (768-dim) is downloaded from Hugging Face Hub via the mirror endpoint `https://hf-mirror.com` (configured via `HF_ENDPOINT`). This is required in China-region deployments where direct access to huggingface.co is blocked.