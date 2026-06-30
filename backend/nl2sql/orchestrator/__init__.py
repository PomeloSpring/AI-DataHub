"""Pipeline — Quick-Deep dual-mode query pipeline.

Quick Mode: Fast direct LLM call, skips RAG (~2s target).
Deep Mode: Full RAG + Loop Engineering (~30s budget).
"""

from backend.nl2sql.orchestrator.pipeline_orchestrator import execute_pipeline

__all__ = ["execute_pipeline"]
