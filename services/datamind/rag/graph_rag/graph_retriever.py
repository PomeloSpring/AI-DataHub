"""Graph Retriever — SPARQL-based graph retrieval for GraphRAG.

Replaces the previous Neo4j/Cypher-based retriever with SPARQL queries
against Oxigraph. All methods maintain the same interface as before.
"""

import logging
from typing import Any

from services.shared.common.rdf.sparql_client import get_sparql_client, OxigraphClient
from services.shared.common.rdf.namespaces import ADH_NS, SPARQL_PREFIXES
from services.datamind.rag.graph_rag.oxigraph_store import OxigraphStore, _safe

logger = logging.getLogger(__name__)


class GraphRetriever:
    """SPARQL-based graph retriever for enhanced RAG."""

    def __init__(self, store: OxigraphStore = None, client: OxigraphClient = None):
        self._store = store or OxigraphStore(client)
        self._client = client or get_sparql_client()

    # ── Table relationships ──────────────────────────────────────────

    def find_related_tables(self, table_name: str, max_depth: int = 2,
                            datasource_id: int = 0) -> list[dict[str, Any]]:
        """Find tables related to the given table via JOIN relationships.

        Uses SPARQL property paths for transitive traversal.
        """
        table_iri = f"{ADH_NS}table:{_safe(table_name)}"
        graph = self._store.graph_uri(datasource_id)

        sparql = f"""
            SELECT ?name ?comment ?businessDesc ?distance WHERE {{
                GRAPH <{graph}> {{
                    <{table_iri}> (<{ADH_NS}join>)/({ADH_NS}join>)* ?related .
                    ?related a <{ADH_NS}Table> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> ?name .
                    OPTIONAL {{ ?related <{ADH_NS}comment> ?comment }}
                    OPTIONAL {{ ?related <{ADH_NS}businessDesc> ?businessDesc }}
                }}
            }}
            LIMIT 20
        """
        try:
            rows = self._client.query(sparql)
            return [
                {
                    "name": r.get("name", ""),
                    "comment": r.get("comment", ""),
                    "business_desc": r.get("businessDesc", ""),
                    "distance": r.get("distance", 1),
                }
                for r in rows
                if r.get("name") != table_name
            ]
        except Exception as e:
            logger.error("find_related_tables failed: %s", e)
            return []

    def find_path_between_tables(self, start_table: str, end_table: str,
                                 max_length: int = 4,
                                 datasource_id: int = 0) -> list[dict[str, Any]]:
        """Find join paths between two tables."""
        s_iri = f"{ADH_NS}table:{_safe(start_table)}"
        e_iri = f"{ADH_NS}table:{_safe(end_table)}"
        graph = self._store.graph_uri(datasource_id)

        sparql = f"""
            SELECT ?path WHERE {{
                GRAPH <{graph}> {{
                    <{s_iri}> (<{ADH_NS}join>)+ <{e_iri}> .
                }}
            }}
            LIMIT 5
        """
        try:
            rows = self._client.query(sparql)
            return [{"start": start_table, "end": end_table, "connected": True}] if rows else []
        except Exception as e:
            logger.error("find_path_between_tables failed: %s", e)
            return []

    def get_table_importance(self, limit: int = 20,
                             datasource_id: int = 0) -> list[dict[str, Any]]:
        """Get tables ranked by number of join connections."""
        graph = self._store.graph_uri(datasource_id)

        sparql = f"""
            SELECT ?name ?comment (COUNT(?join) AS ?connections) WHERE {{
                GRAPH <{graph}> {{
                    ?t a <{ADH_NS}Table> ;
                       <http://www.w3.org/2000/01/rdf-schema#label> ?name .
                    OPTIONAL {{ ?t <{ADH_NS}join> ?join }}
                    OPTIONAL {{ ?t <{ADH_NS}comment> ?comment }}
                }}
            }}
            GROUP BY ?name ?comment
            ORDER BY DESC(?connections)
            LIMIT {limit}
        """
        try:
            rows = self._client.query(sparql)
            return [
                {
                    "name": r.get("name", ""),
                    "comment": r.get("comment", ""),
                    "connections": int(r.get("connections", 0)),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("get_table_importance failed: %s", e)
            return []

    def find_tables_by_column(self, column_name: str, limit: int = 20,
                              datasource_id: int = 0) -> list[dict[str, Any]]:
        """Find tables that contain a column matching the given name."""
        graph = self._store.graph_uri(datasource_id)

        sparql = f"""
            SELECT ?tableName ?tableComment ?columnName WHERE {{
                GRAPH <{graph}> {{
                    ?col a <{ADH_NS}Column> ;
                         <http://www.w3.org/2000/01/rdf-schema#label> ?columnName ;
                         <{ADH_NS}tableName> ?tableName .
                    FILTER(CONTAINS(LCASE(?columnName), LCASE("{_esc_sparql(column_name)}")))
                    OPTIONAL {{
                        ?t a <{ADH_NS}Table> ;
                           <http://www.w3.org/2000/01/rdf-schema#label> ?tableName ;
                           <{ADH_NS}comment> ?tableComment .
                    }}
                }}
            }}
            LIMIT {limit}
        """
        try:
            rows = self._client.query(sparql)
            result = {}
            for r in rows:
                tname = r.get("tableName", "")
                if tname not in result:
                    result[tname] = {
                        "name": tname,
                        "comment": r.get("tableComment", ""),
                        "columns": [],
                    }
                result[tname]["columns"].append(r.get("columnName", ""))
            return list(result.values())
        except Exception as e:
            logger.error("find_tables_by_column failed: %s", e)
            return []

    def find_term_mappings(self, term_name: str,
                           datasource_id: int = 0) -> list[dict[str, Any]]:
        """Find business term → column mappings."""
        graph = self._store.graph_uri(datasource_id)
        term_filter = _esc_sparql(term_name)

        sparql = f"""
            SELECT ?termCn ?description ?calculation ?tableName ?columnName ?dataType WHERE {{
                GRAPH <{graph}> {{
                    ?term a <{ADH_NS}Term> ;
                          <{ADH_NS}mapsTo> ?col .
                    ?col a <{ADH_NS}Column> .
                    ?col <{ADH_NS}tableName> ?tableName .
                    ?term <{ADH_NS}nameCn> ?termCn .
                    FILTER(
                        CONTAINS(LCASE(?termCn), LCASE("{term_filter}")) ||
                        EXISTS {{ ?term <{ADH_NS}nameEn> ?en . FILTER(CONTAINS(LCASE(?en), LCASE("{term_filter}"))) }}
                    )
                    OPTIONAL {{ ?term <{ADH_NS}comment> ?description }}
                    OPTIONAL {{ ?term <{ADH_NS}calculation> ?calculation }}
                    OPTIONAL {{ ?col <http://www.w3.org/2000/01/rdf-schema#label> ?columnName }}
                    OPTIONAL {{ ?col <{ADH_NS}dataType> ?dataType }}
                }}
            }}
        """
        try:
            rows = self._client.query(sparql)
            return [
                {
                    "term_name": r.get("termCn", ""),
                    "description": r.get("description", ""),
                    "calculation": r.get("calculation", ""),
                    "table_name": r.get("tableName", ""),
                    "column_name": r.get("columnName", ""),
                    "data_type": r.get("dataType", ""),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("find_term_mappings failed: %s", e)
            return []

    def search_nodes(self, query_text: str, node_types: list[str] = None,
                     limit: int = 10, datasource_id: int = 0) -> list[dict[str, Any]]:
        """Search for nodes by label/comment/description."""
        graph = self._store.graph_uri(datasource_id)
        q = _esc_sparql(query_text)

        type_filter = ""
        if node_types:
            type_conditions = " || ".join(
                f'(?type = <{ADH_NS}{t}>)' for t in node_types
            )
            type_filter = f"FILTER({type_conditions})"

        sparql = f"""
            SELECT ?iri ?label ?type ?comment WHERE {{
                GRAPH <{graph}> {{
                    ?iri a ?type ;
                         <http://www.w3.org/2000/01/rdf-schema#label> ?label .
                    {type_filter}
                    FILTER(
                        CONTAINS(LCASE(?label), LCASE("{q}"))
                    )
                    OPTIONAL {{ ?iri <{ADH_NS}comment> ?comment }}
                }}
            }}
            LIMIT {limit}
        """
        try:
            rows = self._client.query(sparql)
            return [
                {
                    "iri": r.get("iri", ""),
                    "label": r.get("label", ""),
                    "type": r.get("type", "").replace(ADH_NS, ""),
                    "comment": r.get("comment", ""),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("search_nodes failed: %s", e)
            return []

    def get_context_for_query(self, query_text: str, max_tables: int = 5,
                              max_depth: int = 2,
                              datasource_id: int = 0) -> dict[str, Any]:
        """Get graph context relevant to a natural language query.

        Combines node search, related table traversal, and importance ranking.
        """
        try:
            # 1. Search for matching nodes
            nodes = self.search_nodes(query_text, limit=10, datasource_id=datasource_id)

            # 2. Extract table names from results
            table_names = set()
            for node in nodes:
                ntype = node.get("type", "")
                if ntype == "Table":
                    table_names.add(node.get("label", ""))
                elif ntype == "Column":
                    # Column label is the column name; need to find its table
                    col_results = self.find_tables_by_column(
                        node.get("label", ""), limit=5, datasource_id=datasource_id
                    )
                    for cr in col_results:
                        table_names.add(cr.get("name", ""))

            # 3. Get related tables
            related_tables = []
            for tname in list(table_names)[:max_tables]:
                related = self.find_related_tables(tname, max_depth, datasource_id)
                related_tables.extend(related)

            # 4. Get important tables
            important_tables = self.get_table_importance(10, datasource_id)

            return {
                "direct_tables": list(table_names),
                "related_tables": related_tables,
                "important_tables": important_tables,
                "nodes": nodes,
            }
        except Exception as e:
            logger.error("get_context_for_query failed: %s", e)
            return {
                "direct_tables": [],
                "related_tables": [],
                "important_tables": [],
                "nodes": [],
            }

    def find_business_domain_tables(self, domain: str,
                                    datasource_id: int = 0) -> list[dict[str, Any]]:
        """Find tables belonging to a business domain."""
        graph = self._store.graph_uri(datasource_id)
        d = _esc_sparql(domain)

        sparql = f"""
            SELECT ?name ?comment ?businessDesc (COUNT(?col) AS ?columnCount) WHERE {{
                GRAPH <{graph}> {{
                    ?t a <{ADH_NS}Table> ;
                       <http://www.w3.org/2000/01/rdf-schema#label> ?name .
                    FILTER(
                        CONTAINS(LCASE(?name), LCASE("{d}")) ||
                        EXISTS {{ ?t <{ADH_NS}comment> ?c . FILTER(CONTAINS(LCASE(?c), LCASE("{d}"))) }} ||
                        EXISTS {{ ?t <{ADH_NS}businessDesc> ?bd . FILTER(CONTAINS(LCASE(?bd), LCASE("{d}"))) }}
                    )
                    OPTIONAL {{ ?t <{ADH_NS}comment> ?comment }}
                    OPTIONAL {{ ?t <{ADH_NS}businessDesc> ?businessDesc }}
                    OPTIONAL {{ ?t <{ADH_NS}hasColumn> ?col }}
                }}
            }}
            GROUP BY ?name ?comment ?businessDesc
            ORDER BY DESC(?columnCount)
            LIMIT 20
        """
        try:
            rows = self._client.query(sparql)
            return [
                {
                    "name": r.get("name", ""),
                    "comment": r.get("comment", ""),
                    "business_desc": r.get("businessDesc", ""),
                    "column_count": int(r.get("columnCount", 0)),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("find_business_domain_tables failed: %s", e)
            return []

    def get_table_dependencies(self, table_name: str,
                               datasource_id: int = 0) -> dict[str, Any]:
        """Get table dependency relationships (join graph)."""
        table_iri = f"{ADH_NS}table:{_safe(table_name)}"
        graph = self._store.graph_uri(datasource_id)

        try:
            # Tables that depend on this table (incoming joins)
            dep_query = f"""
                SELECT ?name WHERE {{
                    GRAPH <{graph}> {{
                        ?dependent <{ADH_NS}join> <{table_iri}> .
                        ?dependent <http://www.w3.org/2000/01/rdf-schema#label> ?name .
                    }}
                }}
            """
            dependents = [r["name"] for r in self._client.query(dep_query)]

            # Tables this table depends on (outgoing joins)
            deps_query = f"""
                SELECT ?name WHERE {{
                    GRAPH <{graph}> {{
                        <{table_iri}> <{ADH_NS}join> ?dependency .
                        ?dependency <http://www.w3.org/2000/01/rdf-schema#label> ?name .
                    }}
                }}
            """
            dependencies = [r["name"] for r in self._client.query(deps_query)]

            return {
                "table": table_name,
                "dependents": dependents,
                "dependencies": dependencies,
                "dependent_count": len(dependents),
                "dependency_count": len(dependencies),
            }
        except Exception as e:
            logger.error("get_table_dependencies failed: %s", e)
            return {
                "table": table_name,
                "dependents": [],
                "dependencies": [],
                "dependent_count": 0,
                "dependency_count": 0,
            }

    # ── Ontology-based search (new capability) ───────────────────────

    def search_by_ontology(self, class_name: str, property_name: str = None,
                           datasource_id: int = 0) -> list[dict[str, Any]]:
        """Search for instances of an ontology class.

        Args:
            class_name: OWL class name (e.g., "Shipment").
            property_name: Optional property to filter on.

        Returns:
            List of matching instances with their property values.
        """
        class_iri = f"{ADH_NS}{_safe(class_name)}"
        graph = self._store.graph_uri(datasource_id)

        if property_name:
            prop_iri = f"{ADH_NS}{_safe(property_name)}"
            sparql = f"""
                SELECT ?instance ?label ?value WHERE {{
                    GRAPH <{graph}> {{
                        ?instance a <{class_iri}> ;
                                  <http://www.w3.org/2000/01/rdf-schema#label> ?label ;
                                  <{prop_iri}> ?value .
                    }}
                }}
                LIMIT 50
            """
        else:
            sparql = f"""
                SELECT ?instance ?label WHERE {{
                    GRAPH <{graph}> {{
                        ?instance a <{class_iri}> ;
                                  <http://www.w3.org/2000/01/rdf-schema#label> ?label .
                    }}
                }}
                LIMIT 50
            """
        try:
            return self._client.query(sparql)
        except Exception as e:
            logger.error("search_by_ontology failed: %s", e)
            return []


def _esc_sparql(text: str) -> str:
    """Escape text for embedding in SPARQL string literals."""
    return (text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r"))
