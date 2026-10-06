"""GraphRAG 检索失败可诊断性回归（no-silent-degradation）。

背景：agentic-sparql 依赖 LLM 生成 SPARQL；LLM 失败（如 adh_llm_models 无配置、
认证不可用）时此前静默返回空结果——调用方无法区分「真没有命中」与「检索链路坏了」。
实测暴露：4 条系统场景 eval 用例断言 hit 时全空，根因是环境无 LLM 模型配置，
而返回体没有任何失败标注。

锁住的行为：
1. grounding 失败（异常或 trace 含 error）→ result 带 degraded=True + warnings（可诊断）；
2. grounding 正常 → 不标注 degraded（可区分正常/失败路径）；
3. 失败原因含底层错误（LLM 认证失败等），不吞成空话。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datamind.rag.strategies import graphrag as gr  # noqa: E402


class _FakeStore:
    def health(self):
        return True


def _patch(monkeypatch, grounding_result=None, grounding_raise=None):
    monkeypatch.setattr(gr, "OxigraphStore", lambda: _FakeStore(), raising=False)
    monkeypatch.setattr(
        "services.datamind.rag.graph_rag.oxigraph_store.OxigraphStore", _FakeStore)

    class _FakeAgentic:
        def __init__(self, store=None):
            pass

        def ground(self, *a, **kw):
            if grounding_raise:
                raise grounding_raise
            return grounding_result or {}

    monkeypatch.setattr(
        "services.datamind.rag.graph_rag.agentic_sparql.AgenticSparqlRetriever", _FakeAgentic)
    # hydration 全部置空（本测试只关心失败标注，不关心命中内容）
    for fn in ("_hydrate_tables", "_hydrate_columns", "_hydrate_templates",
               "_hydrate_terms", "_hydrate_relations"):
        monkeypatch.setattr(gr.GraphRagStrategy, fn, lambda self, *a, **kw: [])


class TestDegradedAnnotation:
    def test_trace_error_marks_degraded(self, monkeypatch):
        """LLM 调用失败（trace 记 error）→ degraded + 原因，不得静默当无结果。"""
        _patch(monkeypatch, grounding_result={
            "tables": [], "sql_templates": [], "business_terms": [], "metrics": [],
            "submitted": False, "turns": 1,
            "trace": [{"turn": 1, "error": "LLM call failed: Could not resolve authentication method"}]})
        result = gr.GraphRagStrategy().retrieve("审批", keywords=["审批"], datasource_id=0)
        assert result.get("degraded") is True
        assert any("authentication" in w for w in result.get("warnings") or [])

    def test_grounding_exception_marks_degraded(self, monkeypatch):
        _patch(monkeypatch, grounding_raise=RuntimeError("LLM 生成失败"))
        result = gr.GraphRagStrategy().retrieve("审批", keywords=["审批"], datasource_id=0)
        assert result.get("degraded") is True
        assert result.get("warnings")

    def test_clean_grounding_not_marked(self, monkeypatch):
        """正常 grounding 不标 degraded（正常/失败路径可区分）。"""
        _patch(monkeypatch, grounding_result={
            "tables": ["t_case_records"], "sql_templates": [], "business_terms": [],
            "metrics": [], "submitted": True, "turns": 2, "trace": [{"turn": 1}]})
        result = gr.GraphRagStrategy().retrieve("审批", keywords=["审批"], datasource_id=0)
        assert not result.get("degraded")
        assert not result.get("warnings")
