"""GraphRAG 无 LLM 检索回归（agentic grounding 退役后的行为锁定）。

背景：agentic-sparql（LLM 单发生成 SPARQL 做 grounding）已退役，检索策略改为
确定性 by-name 候选表元数据回填。锁住的行为：
1. 无 LLM/无外部依赖也能按候选表出候选（grounding 不再是前置条件）；
2. keywords/知识库对象 key 仍参与候选挑选（命中线索者优先，确定性排序）；
3. 检索结果结构保持（table_info/column_metadata/... + rag_source=graphrag），
   grounding 调试键置空，且不再产出 degraded/LLM 失败标注。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.mind.rag.strategies import graphrag as gr  # noqa: E402

def _patch_hydration(monkeypatch):
    """hydration 全部打桩（本测试只关心候选挑选与结果结构，不关心命中内容）。"""
    calls = {}

    def _tables(self, tables, datasource_id):
        calls["tables"] = list(tables)
        return [{"name": t} for t in tables]

    monkeypatch.setattr(gr.GraphRagStrategy, "_hydrate_tables", _tables)
    for fn in ("_hydrate_columns", "_hydrate_templates",
               "_hydrate_terms", "_hydrate_relations"):
        monkeypatch.setattr(gr.GraphRagStrategy, fn, lambda self, *a, **kw: [])
    return calls

class TestNoLlmRetrieval:

    def test_no_agentic_sparql_dependency(self):
        """策略模块不得再引用已退役的 agentic_sparql（LLM grounding）。"""
        assert "agentic_sparql" not in open(gr.__file__, encoding="utf-8").read()

