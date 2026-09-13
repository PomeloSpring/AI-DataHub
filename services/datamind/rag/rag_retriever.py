"""RAG Retriever — keyword/BM25 metadata retrieval from RAG tables (no embeddings).

The search pipeline is single-route: ``retrieve_all``/``retrieve_with_strategy``
delegate to the GraphRAG strategy (agentic SPARQL grounding over the Oxigraph
knowledge graph, then deterministic by-name hydration). The granularity helpers
below (``retrieve_sql_templates`` / ``retrieve_business_terms`` /
``retrieve_table_relations``) are keyword/LIKE based and back the agent tools;
they no longer read vector columns or generate embeddings.

Falls back to information_schema when RAG tables are empty.
"""

import logging
from collections import OrderedDict
from contextlib import contextmanager

import pymysql

from services.shared.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
    DORIS_DATABASE,
)
from services.shared.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)



# ── RAG results cache (LRU, max 128 entries) ─────────────────────────

_RAG_CACHE: OrderedDict[str, dict] = OrderedDict()
_RAG_CACHE_MAX = 128


def _rag_cache_key(question: str, target_tables: list[str] = None, keywords: list[str] = None, datasource_id: int = 0, strategy_name: str = None) -> str:
    tt = ",".join(sorted(target_tables)) if target_tables else ""
    kw = ",".join(sorted(keywords)) if keywords else ""
    st = f"|s:{strategy_name}" if strategy_name else ""
    return f"{question}|{tt}|{kw}|ds{datasource_id}{st}"


@contextmanager
def _get_connection():
    """Connection to metadata database (may be MySQL or Doris)."""
    conn = get_metadata_conn()
    try:
        yield conn
    finally:
        conn.close()


# ── Keyword helpers (reuse table_selector tokenization) ──────────────

def _search_tokens(question: str, keywords: list[str] = None) -> list[str]:
    """Derive a small set of search tokens from a question / keyword list.

    Uses jieba keyword extraction + dynamic synonym expansion (shared with the
    table selector). Returns de-duplicated, non-empty tokens capped for LIKE.
    """
    from services.datamind.rag.table_selector import _extract_keywords, _expand_synonyms

    raw: list[str] = []
    if keywords:
        raw.extend(keywords)
    if question:
        raw.extend(_extract_keywords(question))

    raw = [t.strip() for t in raw if t and t.strip()]
    if not raw:
        return []

    try:
        raw = _expand_synonyms(raw)
    except Exception as e:  # pragma: no cover - synonym expansion is best-effort
        logger.debug("Synonym expansion skipped: %s", e)

    seen: set[str] = set()
    tokens: list[str] = []
    for t in raw:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            tokens.append(t)
    return tokens[:8]


def _like_or(tokens: list[str], columns: list[str]) -> tuple[str, list]:
    """Build an OR-of-LIKE condition across columns for each token.

    Returns ``(condition_sql, params)``; condition is empty when no tokens.
    """
    conditions: list[str] = []
    params: list = []
    for tok in tokens:
        like = f"%{tok}%"
        for col in columns:
            conditions.append(f"{col} LIKE %s")
            params.append(like)
    if not conditions:
        return "", []
    return "(" + " OR ".join(conditions) + ")", params


_rules_column_checked = False


def _ensure_rules_column():
    """Check if 'rules' column exists in adh_sql_templates, add it if not."""
    global _rules_column_checked
    if _rules_column_checked:
        return
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'adh_sql_templates' AND COLUMN_NAME = 'rules'",
                    (METADATA_DB_DATABASE,),
                )
                if not cur.fetchone():
                    logger.info("Adding 'rules' column to adh_sql_templates...")
                    cur.execute("ALTER TABLE adh_sql_templates ADD COLUMN rules STRING DEFAULT ''")
                    conn.commit()
                    logger.info("Successfully added 'rules' column")
                _rules_column_checked = True
    except Exception as e:
        logger.warning("Failed to check/add 'rules' column: %s", e)


def retrieve_sql_templates(question: str, limit: int = 5, vec_literal: str = None, datasource_id: int = 0) -> list[dict]:
    """Retrieve matching SQL templates by keyword (intent_keywords / name / description).

    ``vec_literal`` is accepted for backward compatibility with existing callers
    but is ignored — retrieval is keyword/LIKE based, no embedding.
    """
    _ensure_rules_column()

    tokens = _search_tokens(question)
    if not tokens:
        return []

    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                conditions = ["is_active = 1"]
                params: list = []
                if datasource_id:
                    conditions.append("(datasource_id = %s OR datasource_id = 0)")
                    params.extend([datasource_id, datasource_id])
                where, like_params = _like_or(
                    tokens, ["intent_keywords", "template_name", "description"],
                )
                if where:
                    conditions.append(where)
                    params.extend(like_params)
                where_sql = "WHERE " + " AND ".join(conditions)
                cur.execute(
                    f"""
                    SELECT template_id, template_name, category, intent_keywords,
                           sql_template, variables, description, rules, usage_count
                    FROM adh_sql_templates
                    {where_sql}
                    ORDER BY usage_count DESC
                    LIMIT {int(limit)}
                    """,
                    params,
                )
                rows = cur.fetchall()
                logger.debug("Retrieved %d sql_templates by keyword", len(rows))
                return rows
    except Exception as e:
        logger.warning("Keyword search (sql_templates) failed: %s", e)
        return []


def retrieve_business_terms(
    question: str,
    limit: int = 20,
    keywords: list[str] = None,
    vec_literal: str = None,
    datasource_id: int = 0,
) -> list[dict]:
    """Retrieve matching business terms by keyword (term_cn / term_en / aliases).

    ``vec_literal`` is accepted for backward compatibility but is ignored.
    """
    tokens = _search_tokens(question, keywords)
    if not tokens:
        return []

    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                conditions = ["is_active = 1"]
                params: list = []
                if datasource_id:
                    conditions.append("(datasource_id = %s OR datasource_id = 0)")
                    params.extend([datasource_id, datasource_id])
                where, like_params = _like_or(
                    tokens, ["term_cn", "term_en", "term_aliases", "description"],
                )
                if where:
                    conditions.append(where)
                    params.extend(like_params)
                where_sql = "WHERE " + " AND ".join(conditions)
                cur.execute(
                    f"""
                    SELECT term_cn, term_en, term_aliases, term_type,
                           target_table, target_column, calculation, description
                    FROM adh_business_terms
                    {where_sql}
                    LIMIT {int(limit)}
                    """,
                    params,
                )
                return cur.fetchall()
    except Exception as e:
        logger.warning("Keyword search (business_terms) failed: %s", e)
        return []


def retrieve_saved_datasets(question: str, limit: int = 5) -> list[dict]:
    """Retrieve matching saved datasets from Playground.

    Searches adh_saved_queries where is_dataset=1 by keyword match.
    """
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                # Search by keyword match in name, description, and dataset_keywords
                keywords_sql = question.replace("'", "''")
                cur.execute(f"""
                    SELECT id, name, description, sql_query, dataset_keywords
                    FROM adh_saved_queries
                    WHERE is_dataset = 1
                      AND (name LIKE '%{keywords_sql}%'
                           OR description LIKE '%{keywords_sql}%'
                           OR dataset_keywords LIKE '%{keywords_sql}%')
                    LIMIT {limit}
                """)
                return cur.fetchall()
    except Exception as e:
        logger.warning("RAG dataset search failed: %s", e)
        return []


def retrieve_table_relations(
    question: str,
    limit: int = 20,
    target_tables: list[str] = None,
    vec_literal: str = None,
    datasource_id: int = 0,
) -> list[dict]:
    """Retrieve matching table relations by target-tables or question keywords.

    Relations involving ``target_tables`` are matched directly; otherwise active
    relations are matched by keyword against description / table names.
    ``vec_literal`` is accepted for backward compatibility but is ignored.
    """
    tokens = _search_tokens(question)
    if not target_tables and not tokens:
        return []

    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                conditions = ["is_active = 1"]
                params: list = []
                if datasource_id:
                    conditions.append("(datasource_id = %s OR datasource_id = 0)")
                    params.extend([datasource_id, datasource_id])

                sub: list[str] = []
                if target_tables:
                    for t in target_tables:
                        sub.append("(source_table = %s OR target_table = %s)")
                        params.extend([t, t])
                if tokens:
                    where_kw, kw_params = _like_or(
                        tokens, ["description", "source_table", "target_table"],
                    )
                    if where_kw:
                        sub.append(where_kw)
                        params.extend(kw_params)
                if sub:
                    conditions.append("(" + " OR ".join(sub) + ")")

                where_sql = "WHERE " + " AND ".join(conditions)
                cur.execute(
                    f"""
                    SELECT source_table, source_column, target_table, target_column,
                           relation_type, join_type, description
                    FROM adh_table_relations
                    {where_sql}
                    LIMIT {int(limit)}
                    """,
                    params,
                )
                return cur.fetchall()
    except Exception as e:
        logger.warning("Keyword search (table_relations) failed: %s", e)
        return []


def _normalize_table_keyword(kw: str) -> list[str]:
    """Generate search variants for a table keyword.

    E.g. 'cases' -> ['cases', 'case'], '案例' -> ['案例']
    Strips trailing 's'/'es' for English words to improve LIKE matching.
    """
    variants = [kw]
    lower = kw.lower()
    # Strip common English plural suffixes
    if lower.endswith("ies") and len(lower) > 3:
        variants.append(kw[:-3] + "y")
    elif lower.endswith("ses") and len(lower) > 3:
        variants.append(kw[:-2])
    elif lower.endswith("s") and not lower.endswith("ss") and len(lower) > 2:
        variants.append(kw[:-1])
    return list(set(variants))


def _fallback_from_information_schema(
    target_tables: list[str] = None,
) -> dict:
    """Fallback: fetch table/column metadata directly from information_schema.

    Used when RAG tables are empty. If target_tables are specified but no exact
    match, tries LIKE matching with plural-stripping for better fuzzy matching.
    Returns dict with 'table_info' and 'column_metadata' keys.
    """
    result = {"table_info": [], "column_metadata": []}
    try:
        conn = pymysql.connect(
            host=DORIS_HOST, port=DORIS_PORT, user=DORIS_USER,
            password=DORIS_PASSWORD, database="information_schema",
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10, read_timeout=30,
        )
        try:
            with conn.cursor() as cur:
                tables = []
                if target_tables:
                    # Step 1: Try exact match
                    placeholders = ", ".join(["%s"] * len(target_tables))
                    cur.execute(
                        f"SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
                        f"WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                        f"AND TABLE_NAME IN ({placeholders})",
                        [DORIS_DATABASE] + target_tables,
                    )
                    tables = cur.fetchall()

                    # Step 2: If no exact match, try LIKE with plural-stripping
                    if not tables:
                        like_conditions = []
                        like_params = []
                        for t in target_tables:
                            for variant in _normalize_table_keyword(t):
                                like_conditions.append("TABLE_NAME LIKE %s")
                                like_params.append(f"%{variant}%")
                        like_where = " OR ".join(like_conditions)
                        cur.execute(
                            f"SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
                            f"WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                            f"AND ({like_where}) "
                            f"LIMIT 20",
                            [DORIS_DATABASE] + like_params,
                        )
                        tables = cur.fetchall()
                        if tables:
                            logger.info(
                                "LIKE match found %d tables for target_tables=%s: %s",
                                len(tables), target_tables,
                                [t["TABLE_NAME"] for t in tables],
                            )

                # Step 3: If still no match and no target_tables, get all tables
                if not tables and not target_tables:
                    cur.execute(
                        "SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                        "LIMIT 50",
                        [DORIS_DATABASE],
                    )
                    tables = cur.fetchall()

                for tbl in tables:
                    result["table_info"].append({
                        "table_name": tbl["TABLE_NAME"],
                        "table_comment": tbl.get("TABLE_COMMENT") or "",
                        "table_business_desc": "",
                        "region_tag": "",
                        "domain_tag": "",
                    })

                # Fetch columns for each table
                for tbl in tables:
                    cur.execute(
                        "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT, COLUMN_KEY "
                        "FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                        "ORDER BY ORDINAL_POSITION",
                        (DORIS_DATABASE, tbl["TABLE_NAME"]),
                    )
                    for col in cur.fetchall():
                        result["column_metadata"].append({
                            "table_name": tbl["TABLE_NAME"],
                            "column_name": col["COLUMN_NAME"],
                            "data_type": col["DATA_TYPE"],
                            "column_comment": col.get("COLUMN_COMMENT") or "",
                            "business_desc": "",
                            "is_key": "true" if col["COLUMN_KEY"] == "PRI" else "false",
                        })

                logger.info(
                    "Fallback metadata from information_schema: %d tables, %d columns",
                    len(result["table_info"]), len(result["column_metadata"]),
                )
        finally:
            conn.close()
    except Exception as e:
        logger.error("Fallback metadata retrieval failed: %s", e)
    return result


def _get_columns_for_tables(table_names: list[str], datasource_id: int = 0) -> list[dict]:
    """Get ALL columns for the given tables from adh_column_metadata."""
    if not table_names:
        return []
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                placeholders = ", ".join(["%s"] * len(table_names))
                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                cur.execute(
                    f"SELECT table_name, column_name, data_type, "
                    f"column_comment, business_desc, is_key "
                    f"FROM adh_column_metadata "
                    f"WHERE is_active = 1 AND table_name IN ({placeholders}) {ds_filter} "
                    f"ORDER BY table_name, column_name",
                    table_names,
                )
                return cur.fetchall()
    except Exception as e:
        logger.warning("Failed to get columns for tables %s: %s", table_names, e)
        return []


def _get_table_info_for_names(table_names: list[str], datasource_id: int = 0) -> list[dict]:
    """Get table_info rows for specific table names from adh_table_info."""
    if not table_names:
        return []
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                placeholders = ", ".join(["%s"] * len(table_names))
                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                cur.execute(
                    f"SELECT table_name, table_comment, table_business_desc, "
                    f"region_tag, domain_tag "
                    f"FROM adh_table_info "
                    f"WHERE is_active = 1 AND table_name IN ({placeholders}) {ds_filter}",
                    table_names,
                )
                rows = cur.fetchall()
                # Preserve the order from table_names
                name_order = {name: i for i, name in enumerate(table_names)}
                rows.sort(key=lambda r: name_order.get(r["table_name"], 999))
                return rows
    except Exception as e:
        logger.warning("Failed to get table_info for names %s: %s", table_names, e)
        return []


def retrieve_all(
    question: str,
    target_tables: list[str] = None,
    keywords: list[str] = None,
    selected_tables: list[str] = None,
    datasource_id: int = 0,
) -> dict:
    """Retrieve RAG metadata via the GraphRAG route (no embeddings/vectors).

    Delegates to ``retrieve_with_strategy(strategy_name="graphrag")``: agentic
    SPARQL grounding over the knowledge graph, then deterministic hydration of
    grounded entities into the uniform result dict consumed by prompt_builder.

    Args:
        question: User's question.
        target_tables: Tables from intent classifier (legacy, passed as candidates).
        keywords: Business keywords for grounding hints / term filtering.
        selected_tables: Pre-selected tables (passed as grounding candidates).
        datasource_id: Filter metadata by this datasource.

    Returns:
        Dict with table_info, column_metadata, business_terms, table_relations,
        sql_templates, saved_datasets, rag_source.
    """
    return retrieve_with_strategy(
        question=question,
        selected_tables=selected_tables,
        target_tables=target_tables,
        keywords=keywords,
        datasource_id=datasource_id,
        strategy_name="graphrag",
    )


def retrieve_tables_metadata(
    table_names: list[str],
    datasource_id: int = 0,
) -> dict:
    """Directly retrieve metadata for specific table names (no search).

    Use this when table names are already known (e.g. LLM requested specific
    tables for metadata supplementation).

    Args:
        table_names: Exact table names to retrieve.
        datasource_id: Filter metadata by this datasource.

    Returns:
        Dict with 'table_info', 'column_metadata', 'table_relations' keys.
    """
    if not table_names:
        return {"table_info": [], "column_metadata": [], "table_relations": []}

    table_info = _get_table_info_for_names(table_names, datasource_id)
    column_metadata = _get_columns_for_tables(table_names, datasource_id)

    # Also fetch relations involving these tables (useful for JOIN guidance)
    table_relations = []
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                # Match relations where source or target is in the requested tables
                like_conditions = []
                like_params = []
                for t in table_names:
                    like_conditions.append(f"(source_table = %s OR target_table = %s)")
                    like_params.extend([t, t])
                where_like = " OR ".join(like_conditions)
                cur.execute(
                    f"SELECT source_table, source_column, target_table, target_column, "
                    f"relation_type, join_type, description "
                    f"FROM adh_table_relations "
                    f"WHERE is_active = 1 AND ({where_like}) {ds_filter} "
                    f"LIMIT 20",
                    like_params,
                )
                table_relations = cur.fetchall()
    except Exception as e:
        logger.warning("Failed to get table relations for %s: %s", table_names, e)

    logger.info(
        "retrieve_tables_metadata: tables=%s → table_info=%d, columns=%d, relations=%d",
        table_names, len(table_info), len(column_metadata), len(table_relations),
    )
    return {
        "table_info": table_info,
        "column_metadata": column_metadata,
        "table_relations": table_relations,
    }


def increment_template_usage(template_id: str) -> None:
    """Increment usage_count for a template."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_sql_templates SET usage_count = usage_count + 1 WHERE template_id = %s",
                    (template_id,),
                )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to increment template usage: %s", e)


def increment_term_usage(term_cn: str) -> None:
    """Increment usage_count for a business term."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_business_terms SET usage_count = usage_count + 1 WHERE term_cn = %s",
                    (term_cn,),
                )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to increment term usage: %s", e)


# ── Strategy-aware retrieval entry point ──────────────────────────

def retrieve_with_strategy(
    question: str,
    selected_tables: list[str] = None,
    target_tables: list[str] = None,
    keywords: list[str] = None,
    datasource_id: int = 0,
    strategy_name: str = None,
    model_id: int = None,
) -> dict:
    """Retrieve metadata using the specified strategy.

    This is the main entry point for strategy-based retrieval. When no strategy
    is specified it resolves from config (defaults to graphrag).

    Args:
        question: User's question.
        selected_tables: Pre-selected tables from table_selector.
        target_tables: Tables from intent classifier (legacy).
        keywords: Business keywords for term filtering.
        datasource_id: Filter metadata by this datasource.
        strategy_name: Strategy name; None resolves from model/system config.
        model_id: LLM model ID. Used to read retrieval_strategy from model
                  config when strategy_name is not specified.

    Returns:
        Dict with table_info, column_metadata, business_terms, table_relations,
        sql_templates, saved_datasets, rag_source.
    """
    from services.datamind.rag.strategies import get_strategy, get_strategy_from_config

    # Resolve strategy
    if strategy_name:
        strategy = get_strategy(strategy_name)
    else:
        strategy = get_strategy_from_config(model_id=model_id)

    # Check cache (include strategy name in key)
    cache_key = _rag_cache_key(
        question, selected_tables or target_tables, keywords, datasource_id,
        strategy_name=strategy.name,
    )
    if cache_key in _RAG_CACHE:
        _RAG_CACHE.move_to_end(cache_key)
        logger.info("RAG cache hit (strategy=%s): %s", strategy.name, question[:50])
        return _RAG_CACHE[cache_key]

    # Execute strategy
    result = strategy.retrieve(
        question=question,
        selected_tables=selected_tables,
        target_tables=target_tables,
        keywords=keywords,
        datasource_id=datasource_id,
    )

    # Cache result
    _RAG_CACHE[cache_key] = result
    if len(_RAG_CACHE) > _RAG_CACHE_MAX:
        _RAG_CACHE.popitem(last=False)

    return result
