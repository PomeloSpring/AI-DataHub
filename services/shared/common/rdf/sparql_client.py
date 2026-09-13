"""Oxigraph SPARQL HTTP client.

Wraps the Oxigraph REST API for SPARQL query/update and RDF data loading.
Oxigraph exposes a Fuseki-compatible endpoint layout:
    GET/POST /query   — SPARQL SELECT / ASK / CONSTRUCT / DESCRIBE
    POST       /update — SPARQL UPDATE (INSERT DATA, DELETE, etc.)
    PUT        /store   — Bulk RDF load (Turtle / N-Quads / RDF-XML)
    DELETE     /store   — Clear named graph

Usage:
    client = OxigraphClient()  # reads OXIGRAPH_URL from config
    rows = client.query("SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 10")
    client.update("INSERT DATA { adh:Foo a owl:Class }")
"""

import logging
from typing import Any

import httpx

from services.shared.common.config import OXIGRAPH_URL
from services.shared.common.rdf.namespaces import SPARQL_PREFIXES

logger = logging.getLogger(__name__)

# Timeout: Oxigraph is fast for small graphs, but bulk loads may take longer
_DEFAULT_TIMEOUT = 30.0
_BULK_TIMEOUT = 120.0


class OxigraphClient:
    """Synchronous HTTP client for Oxigraph SPARQL endpoint."""

    def __init__(self, base_url: str = None, timeout: float = _DEFAULT_TIMEOUT):
        self._base_url = (base_url or OXIGRAPH_URL).rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout)

    # ── SPARQL SELECT / ASK ──────────────────────────────────────────

    def query(self, sparql: str, prefix: bool = True) -> list[dict[str, Any]]:
        """Execute a SPARQL SELECT or ASK query.

        Args:
            sparql: SPARQL query string. If prefix=True, standard ADH prefixes
                    are prepended automatically.
            prefix: Whether to prepend standard namespace prefixes.

        Returns:
            For SELECT: list of dicts, e.g. [{"s": "...", "p": "..."}, ...]
            For ASK: [{"result": True/False}]
        """
        full_query = f"{SPARQL_PREFIXES}\n{sparql}" if prefix else sparql
        resp = self._client.get(
            "/query",
            params={"query": full_query},
            headers={"Accept": "application/sparql-results+json"},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("boolean") is not None:
            return [{"result": data["boolean"]}]

        bindings = data.get("results", {}).get("bindings", [])
        return [
            {var: _extract_value(binding[var]) for var in binding}
            for binding in bindings
        ]

    # ── SPARQL CONSTRUCT / DESCRIBE ──────────────────────────────────

    def construct(self, sparql: str, prefix: bool = True) -> list[tuple[str, str, str]]:
        """Execute a SPARQL CONSTRUCT or DESCRIBE query.

        Returns:
            List of (subject, predicate, object) IRI/literal strings.
        """
        full_query = f"{SPARQL_PREFIXES}\n{sparql}" if prefix else sparql
        resp = self._client.get(
            "/query",
            params={"query": full_query},
            headers={"Accept": "application/n-triples"},
        )
        resp.raise_for_status()
        return _parse_ntriples(resp.text)

    # ── SPARQL UPDATE ────────────────────────────────────────────────

    def update(self, sparql: str, prefix: bool = True) -> None:
        """Execute a SPARQL UPDATE (INSERT DATA, DELETE, etc.).

        Args:
            sparql: SPARQL UPDATE string.
            prefix: Whether to prepend standard namespace prefixes.
        """
        full_query = f"{SPARQL_PREFIXES}\n{sparql}" if prefix else sparql
        resp = self._client.post(
            "/update",
            data={"update": full_query},
        )
        resp.raise_for_status()
        logger.debug("SPARQL UPDATE executed successfully")

    # ── Bulk RDF loading ─────────────────────────────────────────────

    def load_rdf(self, turtle_str: str, graph_uri: str = None,
                 content_type: str = "text/turtle") -> None:
        """Bulk-load RDF data into Oxigraph.

        Args:
            turtle_str: RDF data serialized as Turtle (or N-Quads, RDF-XML).
            graph_uri: Named graph IRI. If None, loads into default graph.
            content_type: MIME type of the input data.
        """
        params = {}
        if graph_uri:
            params["graph"] = graph_uri
        resp = self._client.put(
            "/store",
            content=turtle_str.encode("utf-8"),
            headers={"Content-Type": content_type},
            params=params,
            timeout=_BULK_TIMEOUT,
        )
        resp.raise_for_status()
        logger.info("Loaded RDF data into Oxigraph (graph=%s, %d bytes)",
                     graph_uri or "default", len(turtle_str))

    def clear_graph(self, graph_uri: str) -> None:
        """Clear all triples in a named graph."""
        resp = self._client.delete(
            "/store",
            params={"graph": graph_uri},
        )
        resp.raise_for_status()
        logger.info("Cleared named graph: %s", graph_uri)

    def clear_default_graph(self) -> None:
        """Clear all triples in the default graph."""
        resp = self._client.delete("/store")
        resp.raise_for_status()
        logger.info("Cleared default graph")

    # ── Health / stats ───────────────────────────────────────────────

    def health(self) -> bool:
        """Check if Oxigraph is reachable."""
        try:
            resp = self._client.get("/", timeout=5.0)
            return resp.status_code < 500
        except Exception:
            return False

    def count_triples(self, graph_uri: str = None) -> int:
        """Count total triples in a graph (or default graph)."""
        if graph_uri:
            sparql = f"SELECT (COUNT(*) AS ?c) FROM <{graph_uri}> WHERE {{ ?s ?p ?o }}"
        else:
            sparql = "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }"
        rows = self.query(sparql)
        return int(rows[0]["c"]) if rows else 0

    def list_named_graphs(self) -> list[str]:
        """List all named graphs in the store."""
        sparql = "SELECT ?g WHERE { GRAPH ?g { ?s ?p ?o } } GROUP BY ?g"
        return [row["g"] for row in self.query(sparql)]

    # ── Context manager ──────────────────────────────────────────────

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ── Module-level singleton (lazy) ────────────────────────────────────

_default_client: OxigraphClient = None


def get_sparql_client() -> OxigraphClient:
    """Get or create the default OxigraphClient singleton."""
    global _default_client
    if _default_client is None:
        _default_client = OxigraphClient()
    return _default_client


# ── Internal helpers ─────────────────────────────────────────────────

def _extract_value(sparql_binding: dict) -> str:
    """Extract the string value from a SPARQL JSON binding."""
    vtype = sparql_binding.get("type", "literal")
    value = sparql_binding.get("value", "")
    if vtype == "uri":
        return value
    return value


def _parse_ntriples(text: str) -> list[tuple[str, str, str]]:
    """Parse N-Triples format into (s, p, o) tuples.

    Minimal parser — handles IRIs (<...>) and literals ("..."^^<type>).
    """
    triples = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = _split_nt_line(line)
        if len(parts) >= 3:
            triples.append((parts[0], parts[1], parts[2]))
    return triples


def _split_nt_line(line: str) -> list[str]:
    """Split a single N-Triples line into components."""
    tokens = []
    i = 0
    while i < len(line):
        if line[i] in (" ", "\t"):
            i += 1
            continue
        if line[i] == "<":
            end = line.index(">", i)
            tokens.append(line[i + 1:end])
            i = end + 1
        elif line[i] == '"':
            # Find closing quote (handle escaped quotes)
            j = i + 1
            while j < len(line):
                if line[j] == '"' and line[j - 1] != "\\":
                    break
                j += 1
            # Check for datatype or language tag
            if j + 1 < len(line) and line[j + 1] == "^":
                # ^^<datatype>
                dt_start = line.index("<", j + 2)
                dt_end = line.index(">", dt_start)
                tokens.append(line[i:j + 1])  # keep literal with quotes
                i = dt_end + 1
            elif j + 1 < len(line) and line[j + 1] == "@":
                # @lang
                k = j + 2
                while k < len(line) and line[k] not in (" ", "\t", "."):
                    k += 1
                tokens.append(line[i:j + 1])
                i = k
            else:
                tokens.append(line[i:j + 1])
                i = j + 1
        elif line[i] == ".":
            break
        else:
            # Bare IRI or keyword
            j = i
            while j < len(line) and line[j] not in (" ", "\t", "."):
                j += 1
            tokens.append(line[i:j])
            i = j
    return tokens
