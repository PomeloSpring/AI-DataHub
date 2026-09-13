"""Graph Context Service — enhance RAG with knowledge graph context.

Provides graph-augmented retrieval using SPARQL-based Oxigraph queries.
"""

import logging
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, field

from services.datamind.rag.graph_rag.graph_retriever import GraphRetriever
from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore

logger = logging.getLogger(__name__)


@dataclass
class GraphContext:
    """Graph context for a query."""
    tables: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[Dict[str, Any]] = field(default_factory=list)
    terms: List[Dict[str, Any]] = field(default_factory=list)
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    dimensions: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        """Convert to prompt context string."""
        sections = []

        if self.tables:
            tables_str = "\n".join([
                f"- {t.get('name')}: {t.get('comment', '')} {t.get('business_desc', '')}"
                for t in self.tables
            ])
            sections.append(f"## 相关表\n{tables_str}")

        if self.columns:
            columns_str = "\n".join([
                f"- {c.get('table_name')}.{c.get('name')} ({c.get('data_type', '')}): {c.get('comment', '')}"
                for c in self.columns[:20]
            ])
            sections.append(f"## 相关字段\n{columns_str}")

        if self.terms:
            terms_str = "\n".join([
                f"- {t.get('name_cn')}: {t.get('description', '')} → {t.get('target_table', '')}.{t.get('target_column', '')}"
                for t in self.terms
            ])
            sections.append(f"## 业务术语\n{terms_str}")

        if self.metrics:
            metrics_str = "\n".join([
                f"- {m.get('name')}: {m.get('formula', '')} ({m.get('unit', '')}) - {m.get('description', '')}"
                for m in self.metrics
            ])
            sections.append(f"## 业务指标\n{metrics_str}")

        if self.dimensions:
            dims_str = "\n".join([
                f"- {d.get('name')}: {d.get('description', '')} 层级={d.get('hierarchy', '')}"
                for d in self.dimensions
            ])
            sections.append(f"## 分析维度\n{dims_str}")

        if self.relations:
            rels_str = "\n".join([
                f"- {r.get('source')} --[{r.get('type')}]--> {r.get('target')}"
                for r in self.relations[:30]
            ])
            sections.append(f"## 关联关系\n{rels_str}")

        return "\n\n".join(sections)

    def get_all_table_names(self) -> List[str]:
        """Get all table names."""
        table_names = set()
        for t in self.tables:
            table_names.add(t.get('name', ''))
        for c in self.columns:
            if c.get('table_name'):
                table_names.add(c['table_name'])
        return list(table_names)


class GraphContextService:
    """Graph context service using SPARQL-based Oxigraph retrieval."""

    def __init__(self, store: Optional[OxigraphStore] = None):
        self._store = store or OxigraphStore()
        self._retriever = GraphRetriever(store=self._store)

    def get_context_for_query(
        self,
        query: str,
        datasource_id: Optional[int] = None,
        max_tables: int = 10,
        max_depth: int = 2
    ) -> GraphContext:
        """Get graph context for a natural language query.

        Args:
            query: User query text.
            datasource_id: Datasource scope.
            max_tables: Max tables to include.
            max_depth: Max graph traversal depth.

        Returns:
            GraphContext with tables, columns, terms, metrics, relations.
        """
        context = GraphContext()
        ds_id = datasource_id or 0

        try:
            # Use the GraphRetriever for comprehensive context
            graph_ctx = self._retriever.get_context_for_query(
                query, max_tables=max_tables, max_depth=max_depth,
                datasource_id=ds_id,
            )

            # Map retriever results to GraphContext
            for t in graph_ctx.get("direct_tables", []):
                context.tables.append({"name": t, "type": "table"})

            for r in graph_ctx.get("related_tables", []):
                context.tables.append({
                    "name": r.get("name", ""),
                    "comment": r.get("comment", ""),
                    "business_desc": r.get("business_desc", ""),
                    "type": "table",
                })

            # Get term mappings from query keywords
            for keyword in query.split()[:5]:
                terms = self._retriever.find_term_mappings(keyword, ds_id)
                for t in terms:
                    context.terms.append({
                        "name_cn": t.get("term_name", ""),
                        "description": t.get("description", ""),
                        "target_table": t.get("table_name", ""),
                        "target_column": t.get("column_name", ""),
                        "type": "term",
                    })

            # Get table relations
            table_names = context.get_all_table_names()[:max_tables]
            if table_names:
                context.relations = self._get_table_relations(table_names, ds_id)

            # Trim
            context = self._trim_context(context, max_tables)
            return context

        except Exception as e:
            logger.error("Failed to get graph context: %s", e, exc_info=True)
            return context

    def get_context_for_tables(
        self,
        table_names: List[str],
        datasource_id: Optional[int] = None
    ) -> GraphContext:
        """Get graph context for specific tables."""
        context = GraphContext()
        ds_id = datasource_id or 0

        try:
            for table_name in table_names[:10]:
                # Find related tables
                related = self._retriever.find_related_tables(table_name, max_depth=1, datasource_id=ds_id)
                for r in related:
                    context.tables.append({
                        "name": r.get("name", ""),
                        "comment": r.get("comment", ""),
                        "type": "table",
                    })

                # Find term mappings
                terms = self._retriever.find_term_mappings(table_name, ds_id)
                for t in terms:
                    context.terms.append({
                        "name_cn": t.get("term_name", ""),
                        "description": t.get("description", ""),
                        "target_table": t.get("table_name", ""),
                        "target_column": t.get("column_name", ""),
                        "type": "term",
                    })

            # Get table relations
            context.relations = self._get_table_relations(table_names[:10], ds_id)
            return context

        except Exception as e:
            logger.error("Failed to get context for tables: %s", e, exc_info=True)
            return context

    def _get_table_relations(self, table_names: List[str],
                             datasource_id: int = 0) -> List[Dict[str, Any]]:
        """Get JOIN relations between a set of tables."""
        relations = []
        seen = set()

        for table_name in table_names:
            deps = self._retriever.get_table_dependencies(table_name, datasource_id)
            for dep_name in deps.get("dependencies", []):
                if dep_name in table_names:
                    key = tuple(sorted([table_name, dep_name]))
                    if key not in seen:
                        seen.add(key)
                        relations.append({
                            "source": table_name,
                            "target": dep_name,
                            "type": "JOIN",
                        })

        return relations

    def _trim_context(self, context: GraphContext, max_tables: int) -> GraphContext:
        """Trim context to reasonable size."""
        context.tables = self._deduplicate(context.tables, 'name')[:max_tables]
        context.columns = self._deduplicate(context.columns, 'name')[:50]
        context.terms = self._deduplicate(context.terms, 'name_cn')[:10]
        context.metrics = self._deduplicate(context.metrics, 'name')[:10]
        context.dimensions = self._deduplicate(context.dimensions, 'name')[:10]
        context.relations = context.relations[:30]
        return context

    def _deduplicate(self, items: List[Dict], key: str) -> List[Dict]:
        """Deduplicate by key."""
        seen = set()
        result = []
        for item in items:
            item_key = item.get(key)
            if item_key and item_key not in seen:
                seen.add(item_key)
                result.append(item)
        return result
