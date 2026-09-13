"""RDF/SPARQL utilities for Oxigraph-backed GraphRAG.

Provides:
- OxigraphClient: HTTP client for Oxigraph SPARQL endpoint
- Namespaces: standard RDF namespace prefixes
- ontology_to_rdf: convert JSON ontology models to RDF triples
"""

from services.shared.common.rdf.namespaces import ADH_NS, SCHEMA_NS, OWL_NS, RDFS_NS, SKOS_NS, XSD_NS
from services.shared.common.rdf.sparql_client import OxigraphClient

__all__ = [
    "OxigraphClient",
    "ADH_NS", "SCHEMA_NS", "OWL_NS", "RDFS_NS", "SKOS_NS", "XSD_NS",
]
