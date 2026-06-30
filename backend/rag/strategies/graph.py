"""Graph Strategy — NetworkX-based graph traversal for metadata retrieval.

Builds a NetworkX directed graph from DB metadata (tables, columns, terms, relations),
uses vector search to find entry nodes, then ego_graph traversal to expand related metadata.
Only returns columns that are actually reached by traversal (not all columns).
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

import networkx as nx

from backend.rag.strategies.base import RetrievalStrategy, empty_result

logger = logging.getLogger(__name__)

# ── Graph cache ───────────────────────────────────────────────────

_graph_cache: dict[int, tuple[nx.DiGraph, float]] = {}  # ds_id → (graph, timestamp)
_GRAPH_TTL_SECONDS = 300  # 5 minutes


def _build_graph(datasource_id: int = 0) -> nx.DiGraph:
    """Build NetworkX directed graph from DB metadata.

    Node types: table, column, term
    Edge types: has_column (table→column), join (table↔table), maps_to (term→column)

    Each node has a 'type' attribute and type-specific properties.
    Each edge has a 'type' attribute.
    """
    from backend.common.db.metadata_db import get_vector_conn

    conn = get_vector_conn()

    ds_t = f"AND (t.datasource_id = {datasource_id} OR t.datasource_id = 0)" if datasource_id else ""
    ds_c = f"AND (c.datasource_id = {datasource_id} OR c.datasource_id = 0)" if datasource_id else ""
    ds_tm = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
    ds_r = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""

    G = nx.DiGraph()

    try:
        with conn.cursor() as cur:
            # ── Tables ──
            cur.execute(f"""
                SELECT table_name, table_comment, table_business_desc
                FROM adh_table_info t
                WHERE t.is_active = 1 {ds_t}
            """)
            for row in cur.fetchall():
                G.add_node(f"table:{row['table_name']}", type="table",
                           name=row["table_name"],
                           comment=row.get("table_comment", ""),
                           desc=row.get("table_business_desc", ""))

            # ── Columns ──
            cur.execute(f"""
                SELECT c.table_name, c.column_name, c.data_type,
                       c.column_comment, c.business_desc, c.is_key
                FROM adh_column_metadata c
                WHERE c.is_active = 1 {ds_c}
            """)
            for row in cur.fetchall():
                tname = row["table_name"]
                cname = row["column_name"]
                col_id = f"col:{tname}.{cname}"
                table_id = f"table:{tname}"

                G.add_node(col_id, type="column", table=tname, name=cname,
                           data_type=row["data_type"],
                           comment=row.get("column_comment", ""),
                           desc=row.get("business_desc", ""),
                           is_key=row.get("is_key", "false"))

                # Edge: table → column
                if table_id in G:
                    G.add_edge(table_id, col_id, type="has_column")

            # ── Business terms ──
            cur.execute(f"""
                SELECT term_cn, term_en, term_aliases, target_table, target_column,
                       calculation, description
                FROM adh_business_terms
                WHERE is_active = 1 {ds_tm}
            """)
            for row in cur.fetchall():
                term_id = f"term:{row['term_cn']}"
                G.add_node(term_id, type="term", name=row["term_cn"],
                           en=row.get("term_en", ""),
                           aliases=row.get("term_aliases", ""),
                           target_table=row.get("target_table", ""),
                           target_column=row.get("target_column", ""),
                           calculation=row.get("calculation", ""),
                           desc=row.get("description", ""))

                # Edge: term → target column
                tgt_table = row.get("target_table", "")
                tgt_col = row.get("target_column", "")
                if tgt_table and tgt_col:
                    col_id = f"col:{tgt_table}.{tgt_col}"
                    G.add_edge(term_id, col_id, type="maps_to")

            # ── Table relations (JOINs) ──
            cur.execute(f"""
                SELECT source_table, source_column, target_table, target_column,
                       relation_type, join_type, description
                FROM adh_table_relations
                WHERE is_active = 1 {ds_r}
            """)
            for row in cur.fetchall():
                src_id = f"table:{row['source_table']}"
                tgt_id = f"table:{row['target_table']}"
                if src_id in G and tgt_id in G:
                    join_attrs = dict(
                        type="join",
                        source_column=row["source_column"],
                        target_column=row["target_column"],
                        relation_type=row.get("relation_type", "1:N"),
                        join_type=row.get("join_type", "INNER"),
                        description=row.get("description", ""),
                    )
                    # Bidirectional
                    G.add_edge(src_id, tgt_id, **join_attrs)
                    G.add_edge(tgt_id, src_id, **join_attrs)

    finally:
        conn.close()

    logger.info("[graph] built NetworkX graph: %d nodes, %d edges (ds=%d)",
                G.number_of_nodes(), G.number_of_edges(), datasource_id)
    return G


def _get_graph(datasource_id: int = 0) -> nx.DiGraph:
    """Get cached graph or build a new one."""
    now = time.time()
    if datasource_id in _graph_cache:
        g, ts = _graph_cache[datasource_id]
        if now - ts < _GRAPH_TTL_SECONDS:
            return g

    G = _build_graph(datasource_id)
    _graph_cache[datasource_id] = (G, now)
    return G


def clear_graph_cache(datasource_id: int = None):
    """Clear the graph cache (call after metadata changes)."""
    if datasource_id is not None:
        _graph_cache.pop(datasource_id, None)
    else:
        _graph_cache.clear()


def _find_entry_nodes(G: nx.DiGraph, question: str, selected_tables: list[str],
                      datasource_id: int) -> set[str]:
    """Find entry table nodes via multiple channels."""
    entry_tables = set()

    # Channel 1: pre-selected tables
    if selected_tables:
        entry_tables.update(selected_tables)

    # Channel 2: keyword + vector table selection
    from backend.rag.table_selector import select_tables
    from backend.rag.rag_retriever import retrieve_table_info

    if not selected_tables:
        keyword_tables = select_tables(question, top_k=5, datasource_id=datasource_id)
        entry_tables.update(keyword_tables)

    vector_tables = retrieve_table_info(question, 10, datasource_id=datasource_id)
    for t in vector_tables:
        entry_tables.add(t["table_name"])

    # Channel 3: business term matching → target tables
    question_lower = question.lower()
    for node_id, data in G.nodes(data=True):
        if data.get("type") != "term":
            continue
        term_name = data.get("name", "").lower()
        aliases = (data.get("aliases", "") or "").lower()
        if term_name in question_lower or any(
            a.strip() in question_lower for a in aliases.split(",") if a.strip()
        ):
            tgt_table = data.get("target_table", "")
            if tgt_table:
                entry_tables.add(tgt_table)

    # Convert to graph node IDs
    return {f"table:{t}" for t in entry_tables if f"table:{t}" in G}


class GraphStrategy(RetrievalStrategy):
    """NetworkX graph: vector search for entry nodes → ego_graph traversal.

    Only returns columns reached by traversal, not all columns from discovered tables.
    """

    name = "graph"

    def retrieve(
        self,
        question: str,
        selected_tables: list[str] = None,
        target_tables: list[str] = None,
        keywords: list[str] = None,
        datasource_id: int = 0,
    ) -> dict:
        from backend.rag.rag_retriever import (
            _get_table_info_for_names,
            retrieve_sql_templates,
            retrieve_business_terms,
            retrieve_saved_datasets,
        )
        from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal

        G = _get_graph(datasource_id)
        vec_literal = embedding_to_sql_literal(generate_embedding(question))

        # ── Step 1: Find entry nodes ──
        entry_node_ids = _find_entry_nodes(G, question, selected_tables, datasource_id)
        if not entry_node_ids:
            logger.warning("[graph] no entry nodes found")
            return empty_result("graph:no_entry")

        # ── Step 2: ego_graph traversal (radius=1) from each entry node ──
        discovered_ids: set[str] = set()
        for nid in entry_node_ids:
            # ego_graph includes the center node + all neighbors within radius
            ego = nx.ego_graph(G.to_undirected(), nid, radius=1)
            discovered_ids.update(ego.nodes())

        # ── Step 3: Extract discovered tables and columns ──
        discovered_tables: set[str] = set()
        discovered_columns: set[tuple[str, str]] = set()  # (table, column)

        for nid in discovered_ids:
            data = G.nodes[nid]
            ntype = data.get("type")
            if ntype == "table":
                discovered_tables.add(data["name"])
            elif ntype == "column":
                discovered_columns.add((data["table"], data["name"]))

        # Also include JOIN-connected tables (1-hop from entry tables)
        for nid in list(entry_node_ids):
            for successor in G.successors(nid):
                sdata = G.nodes[successor]
                if sdata.get("type") == "table":
                    discovered_tables.add(sdata["name"])
                    # Include columns of JOIN-connected tables
                    for col_succ in G.successors(successor):
                        cdata = G.nodes[col_succ]
                        if cdata.get("type") == "column":
                            discovered_columns.add((cdata["table"], cdata["name"]))

        logger.info("[graph] entry=%s, tables=%s, columns=%d",
                    [G.nodes[n]["name"] for n in entry_node_ids],
                    list(discovered_tables), len(discovered_columns))

        if not discovered_tables:
            return empty_result("graph:no_tables")

        # ── Step 4: Build column_metadata from discovered columns only ──
        # Get full metadata for discovered tables
        from backend.rag.rag_retriever import _get_columns_for_tables
        all_columns = _get_columns_for_tables(list(discovered_tables), datasource_id)

        # Filter: only columns reached by graph traversal + key columns
        column_metadata = [
            c for c in all_columns
            if (c["table_name"], c["column_name"]) in discovered_columns
            or c.get("is_key") == "true"
        ]

        # Ensure minimum coverage: at least key columns per table
        tables_with_cols = {c["table_name"] for c in column_metadata}
        for c in all_columns:
            if c["table_name"] not in tables_with_cols and c.get("is_key") == "true":
                column_metadata.append(c)

        # ── Step 5: Table info ──
        table_info = _get_table_info_for_names(list(discovered_tables), datasource_id)

        # ── Step 6: Relations between discovered tables ──
        table_relations = []
        seen_rels = set()
        discovered_set = discovered_tables
        for src_table in discovered_tables:
            src_id = f"table:{src_table}"
            if src_id not in G:
                continue
            for _, tgt_id, edata in G.out_edges(src_id, data=True):
                if edata.get("type") != "join":
                    continue
                tgt_data = G.nodes[tgt_id]
                if tgt_data.get("type") != "table":
                    continue
                tgt_table = tgt_data["name"]
                if tgt_table not in discovered_set:
                    continue
                rel_key = tuple(sorted([src_table, tgt_table]))
                if rel_key not in seen_rels:
                    seen_rels.add(rel_key)
                    table_relations.append({
                        "source_table": src_table,
                        "source_column": edata.get("source_column", ""),
                        "target_table": tgt_table,
                        "target_column": edata.get("target_column", ""),
                        "relation_type": edata.get("relation_type", "1:N"),
                        "join_type": edata.get("join_type", "INNER"),
                        "description": edata.get("description", ""),
                    })

        # ── Step 7: Parallel searches for templates, terms, datasets ──
        sql_templates, business_terms, saved_datasets = _parallel_search(
            question, keywords, list(discovered_tables), vec_literal, datasource_id
        )

        return {
            "table_info": table_info,
            "column_metadata": column_metadata,
            "sql_templates": sql_templates,
            "business_terms": business_terms,
            "table_relations": table_relations,
            "saved_datasets": saved_datasets,
            "rag_source": "graph",
        }


def _parallel_search(question, keywords, table_names, vec_literal, datasource_id):
    from backend.rag.rag_retriever import (
        retrieve_sql_templates,
        retrieve_business_terms,
        retrieve_saved_datasets,
    )

    sql_templates, business_terms, saved_datasets = [], [], []

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_sql = pool.submit(retrieve_sql_templates, question, 5, vec_literal, datasource_id)
        f_terms = pool.submit(retrieve_business_terms, question, 20, keywords, vec_literal, datasource_id)
        f_ds = pool.submit(retrieve_saved_datasets, question)

        for name, future in [("sql_templates", f_sql), ("business_terms", f_terms), ("saved_datasets", f_ds)]:
            try:
                result = future.result()
                if name == "sql_templates": sql_templates = result
                elif name == "business_terms": business_terms = result
                else: saved_datasets = result
            except Exception as e:
                logger.warning("%s failed: %s", name, e)

    return sql_templates, business_terms, saved_datasets
