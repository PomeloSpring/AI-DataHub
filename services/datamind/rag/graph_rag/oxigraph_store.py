"""Oxigraph Store — RDF triple store operations for GraphRAG.

Replaces the previous Neo4j store with Oxigraph SPARQL-based operations.
Uses named graphs per datasource for isolation: ``<adh:ds:{id}>``.

Node model (RDF triples):
    adh:table:{name}       a adh:Table ;  rdfs:label "..." ; adh:comment "..." .
    adh:col:{table}.{col}  a adh:Column ; rdfs:label "..." ; adh:dataType "..." .
    adh:table:{t} adh:hasColumn adh:col:{t}.{c} .
    adh:table:{t1} adh:join adh:table:{t2} .
    adh:term:{name}        a adh:Term ;   adh:mapsTo adh:col:{t}.{c} .
    adh:metric:{name}      a adh:Metric ; adh:defines adh:col:{t}.{c} .
    adh:sqltpl:{id}        a adh:SQLTemplate ; adh:sqlText "..." ;
                           adh:intentKeywords "..." ; adh:touchTable adh:table:{t} .
"""

import logging
from typing import Any

from services.shared.common.rdf.sparql_client import OxigraphClient, get_sparql_client
from services.shared.common.rdf.namespaces import ADH_NS

logger = logging.getLogger(__name__)


class OxigraphStore:
    """Oxigraph-backed RDF store for the knowledge graph."""

    def __init__(self, client: OxigraphClient = None):
        self._client = client or get_sparql_client()

    # ── Named graph helpers ──────────────────────────────────────────

    @staticmethod
    def graph_uri(datasource_id: int = 0) -> str:
        return f"{ADH_NS}ds:{datasource_id}"

    # ── Node CRUD ────────────────────────────────────────────────────

    def create_table_node(self, name: str, comment: str = "",
                          business_desc: str = "", datasource_id: int = 0):
        iri = f"{ADH_NS}table:{_safe(name)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}Table> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(name)}" ;
                        <{ADH_NS}comment> "{_esc(comment)}" ;
                        <{ADH_NS}businessDesc> "{_esc(business_desc)}" .
                }}
            }}
        """)

    def create_column_node(self, table: str, column: str, data_type: str = "",
                           comment: str = "", datasource_id: int = 0):
        col_iri = f"{ADH_NS}col:{_safe(table)}.{_safe(column)}"
        tbl_iri = f"{ADH_NS}table:{_safe(table)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{col_iri}> a <{ADH_NS}Column> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(column)}" ;
                        <{ADH_NS}dataType> "{_esc(data_type)}" ;
                        <{ADH_NS}comment> "{_esc(comment)}" ;
                        <{ADH_NS}tableName> "{_esc(table)}" .
                    <{tbl_iri}> <{ADH_NS}hasColumn> <{col_iri}> .
                }}
            }}
        """)

    def create_term_node(self, name_cn: str, name_en: str = "",
                         description: str = "", calculation: str = "",
                         datasource_id: int = 0):
        iri = f"{ADH_NS}term:{_safe(name_cn)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}Term> ;
                        <{ADH_NS}nameCn> "{_esc(name_cn)}" ;
                        <{ADH_NS}nameEn> "{_esc(name_en)}" ;
                        <{ADH_NS}comment> "{_esc(description)}" ;
                        <{ADH_NS}calculation> "{_esc(calculation)}" .
                }}
            }}
        """)

    def create_metric_node(self, name: str, description: str = "",
                           datasource_id: int = 0):
        iri = f"{ADH_NS}metric:{_safe(name)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}Metric> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(name)}" ;
                        <{ADH_NS}comment> "{_esc(description)}" .
                }}
            }}
        """)

    def create_datasource_node(self, ds_id: int, name: str,
                               db_type: str = ""):
        iri = f"{ADH_NS}datasource:{ds_id}"
        graph = self.graph_uri(0)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}DataSource> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(name)}" ;
                        <{ADH_NS}dbType> "{_esc(db_type)}" .
                }}
            }}
        """)

    # ── Relationship CRUD ────────────────────────────────────────────

    def create_join_relation(self, table1: str, table2: str,
                             join_type: str = "", datasource_id: int = 0):
        t1 = f"{ADH_NS}table:{_safe(table1)}"
        t2 = f"{ADH_NS}table:{_safe(table2)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{t1}> <{ADH_NS}join> <{t2}> .
                    <{t2}> <{ADH_NS}join> <{t1}> .
                }}
            }}
        """)

    def create_term_mapping(self, term_name: str, table: str, column: str,
                            datasource_id: int = 0):
        term_iri = f"{ADH_NS}term:{_safe(term_name)}"
        col_iri = f"{ADH_NS}col:{_safe(table)}.{_safe(column)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{term_iri}> <{ADH_NS}mapsTo> <{col_iri}> .
                }}
            }}
        """)

    def create_metric_column_relation(self, metric_name: str, table: str,
                                      column: str, datasource_id: int = 0):
        m_iri = f"{ADH_NS}metric:{_safe(metric_name)}"
        col_iri = f"{ADH_NS}col:{_safe(table)}.{_safe(column)}"
        graph = self.graph_uri(datasource_id)
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{m_iri}> <{ADH_NS}defines> <{col_iri}> .
                }}
            }}
        """)

    def create_sql_template_node(self, template_id: str, name: str, sql: str,
                                 intent_keywords: str = "", category: str = "",
                                 description: str = "", variables: str = "",
                                 rules: str = "", tables: list[str] = None,
                                 datasource_id: int = 0):
        """Create an adh:SQLTemplate node with its text/keywords and touched tables.

        Templates become first-class graph nodes so the retriever can ground a
        question against them via SPARQL (no vector search needed).
        """
        iri = f"{ADH_NS}sqltpl:{_safe(str(template_id))}"
        graph = self.graph_uri(datasource_id)
        touch_triples = ""
        for t in (tables or []):
            tbl_iri = f"{ADH_NS}table:{_safe(t)}"
            touch_triples += f"\n                    <{iri}> <{ADH_NS}touchTable> <{tbl_iri}> ."
        self._client.update(f"""
            INSERT DATA {{
                GRAPH <{graph}> {{
                    <{iri}> a <{ADH_NS}SQLTemplate> ;
                        <http://www.w3.org/2000/01/rdf-schema#label> "{_esc(name)}" ;
                        <{ADH_NS}sqlText> "{_esc(sql)}" ;
                        <{ADH_NS}intentKeywords> "{_esc(intent_keywords)}" ;
                        <{ADH_NS}category> "{_esc(category)}" ;
                        <{ADH_NS}comment> "{_esc(description)}" ;
                        <{ADH_NS}variables> "{_esc(variables)}" ;
                        <{ADH_NS}rules> "{_esc(rules)}" .{touch_triples}
                }}
            }}
        """)

    # ── Bulk operations ──────────────────────────────────────────────

    def load_turtle(self, turtle_str: str, datasource_id: int = 0):
        """Bulk-load Turtle RDF into a named graph."""
        graph = self.graph_uri(datasource_id)
        self._client.load_rdf(turtle_str, graph_uri=graph)

    def clear_graph(self, datasource_id: int = 0):
        """Clear all triples in a datasource's named graph."""
        graph = self.graph_uri(datasource_id)
        try:
            self._client.clear_graph(graph)
        except Exception as e:
            logger.warning("Failed to clear graph %s: %s", graph, e)

    # ── Stats ────────────────────────────────────────────────────────

    def count_triples(self, datasource_id: int = 0) -> int:
        return self._client.count_triples(self.graph_uri(datasource_id))

    def health(self) -> bool:
        return self._client.health()


# ── Helpers ──────────────────────────────────────────────────────────

def _safe(name: str) -> str:
    """Sanitize name for use in RDF IRI local name."""
    if not name:
        return "unnamed"
    result = ""
    for ch in name:
        if ch.isalnum() or ch == "_":
            result += ch
        else:
            result += "_"
    if result and result[0].isdigit():
        result = "_" + result
    return result or "unnamed"


def _esc(text: str) -> str:
    """Escape text for RDF literal."""
    return (text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r"))
