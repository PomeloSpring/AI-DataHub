"""RAG Retriever v2 — uses VectorStore abstraction instead of raw SQL.

Drop-in replacement for rag_retriever.py. Same function signatures, same return values.
Uses common.vector.get_vector_store() for vector search.

Only the vector search functions are converted. Non-vector operations
(e.g., information_schema fallback, usage increment) remain as-is.
"""

import logging
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

import pymysql

from backend.common.config import (
    DORIS_DATABASE, METADATA_DB_TYPE,
    VECTOR_DB_HOST, VECTOR_DB_PORT, VECTOR_DB_USER, VECTOR_DB_PASSWORD,
)
from backend.common.db.metadata_db import get_metadata_connection
from backend.common.vector import get_vector_store
from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal

logger = logging.getLogger(__name__)

VECTOR_SEARCH_LIMIT = 20


# ── RAG results cache (LRU, max 128 entries) ─────────────────────────

_RAG_CACHE: OrderedDict[str, dict] = OrderedDict()
_RAG_CACHE_MAX = 128


def _rag_cache_key(question: str, target_tables: list[str] = None, keywords: list[str] = None, datasource_id: int = 0, strategy_name: str = None) -> str:
    tt = ",".join(sorted(target_tables)) if target_tables else ""
    kw = ",".join(sorted(keywords)) if keywords else ""
    st = f"|s:{strategy_name}" if strategy_name else ""
    return f"{question}|{tt}|{kw}|ds{datasource_id}{st}"


def _fuzzy_match_table(table_name: str, target_tables: list[str]) -> bool:
    """Check if table_name matches any target_tables keyword (case-insensitive substring)."""
    lower = table_name.lower()
    for kw in target_tables:
        for variant in _normalize_table_keyword(kw):
            if variant.lower() in lower:
                return True
    return False


def _build_ds_filter(datasource_id: int) -> dict:
    """Build datasource filter dict for VectorStore."""
    if datasource_id:
        return {"_raw": f"(datasource_id = {datasource_id} OR datasource_id = 0)"}
    return {}


def retrieve_table_info(
    question: str,
    limit: int = 20,
    target_tables: list[str] = None,
    vec_literal: str = None,
    datasource_id: int = 0,
    user_id: int = 0,
    workspace_id: int = 0,
) -> list[dict]:
    """Retrieve matching table-level info via vector similarity."""
    store = get_vector_store()
    query_embedding = generate_embedding(question)

    filters = {"is_active": 1}
    if datasource_id:
        filters["_raw"] = f"(datasource_id = {datasource_id} OR datasource_id = 0)"

    rows = store.search(
        table="adh_table_info",
        query_embedding=query_embedding,
        limit=limit,
        filters=filters,
        output_columns=["table_name", "table_comment", "table_business_desc", "region_tag", "domain_tag"],
    )

    # If target_tables specified, boost matching tables to top
    if target_tables and rows:
        matched = [r for r in rows if _fuzzy_match_table(r["table_name"], target_tables)]
        unmatched = [r for r in rows if not _fuzzy_match_table(r["table_name"], target_tables)]
        rows = matched + unmatched

    # Apply RLS: filter out tables the user has no access to
    if user_id and workspace_id and datasource_id and rows:
        rows = _apply_rls_table_filter(rows, user_id, workspace_id, datasource_id)

    return rows


def _apply_rls_table_filter(rows: list, user_id: int, workspace_id: int, datasource_id: int) -> list:
    """Filter out tables that the user has RLS restrictions on (all columns hidden)."""
    try:
        from backend.services.rls_service import rls_service
        filtered = []
        for row in rows:
            tname = row.get("table_name", "")
            policies = rls_service.get_effective_policies(user_id, workspace_id, datasource_id, tname)
            # Skip table if ALL columns are hidden (no visible data)
            if policies.get("hidden_columns"):
                # Check if all columns in this table are hidden
                conn_tmp = None
                try:
                    from backend.common.db.metadata_db import get_metadata_conn
                    conn_tmp = get_metadata_conn()
                    with conn_tmp.cursor() as cur:
                        cur.execute(
                            "SELECT COUNT(*) as cnt FROM adh_column_metadata WHERE table_name = %s AND datasource_id = %s AND is_active = 1",
                            (tname, datasource_id)
                        )
                        total_cols = cur.fetchone()["cnt"]
                        if total_cols > 0 and len(policies["hidden_columns"]) >= total_cols:
                            continue  # All columns hidden — skip table
                finally:
                    if conn_tmp:
                        conn_tmp.close()
            filtered.append(row)
        return filtered
    except Exception as e:
        logger.debug("RLS table filter skipped: %s", e)
        return rows


_TIME_COLUMN_PATTERNS = ("time", "date", "月", "日", "年", "创建时间", "更新时间", "扫描时间",
                          "create_time", "update_time", "scan_time", "created_at", "updated_at")


def retrieve_column_metadata(
    question: str,
    limit: int = 50,
    target_tables: list[str] = None,
    vec_literal: str = None,
    datasource_id: int = 0,
) -> list[dict]:
    """Retrieve matching column metadata via vector similarity."""
    store = get_vector_store()
    query_embedding = generate_embedding(question)

    fetch_limit = max(limit, 100)

    filters = {"is_active": 1}
    if datasource_id:
        filters["_raw"] = f"(datasource_id = {datasource_id} OR datasource_id = 0)"

    all_rows = store.search(
        table="adh_column_metadata",
        query_embedding=query_embedding,
        limit=fetch_limit,
        filters=filters,
        output_columns=["table_name", "column_name", "data_type", "column_comment", "business_desc", "is_key"],
    )

    # Identify matched tables (fuzzy match on target_tables)
    matched_tables = set()
    if target_tables:
        for r in all_rows:
            if _fuzzy_match_table(r["table_name"], target_tables):
                matched_tables.add(r["table_name"])

    # Separate into priority groups
    matched_cols = []
    time_cols = []
    other_cols = []

    for r in all_rows:
        is_matched = r["table_name"] in matched_tables
        col_lower = r["column_name"].lower()
        comment_lower = (r.get("column_comment") or "").lower()
        is_time = any(p in col_lower or p in comment_lower for p in _TIME_COLUMN_PATTERNS)

        if is_matched and is_time:
            time_cols.append(r)
        elif is_matched:
            matched_cols.append(r)
        else:
            other_cols.append(r)

    # Merge: matched tables first (time cols boosted), then others
    rows = time_cols + matched_cols + other_cols

    # Deduplicate by (table_name, column_name)
    seen = set()
    deduped = []
    for r in rows:
        key = (r["table_name"], r["column_name"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped[:limit]


# ── Column metadata cache and BM25 index ─────────────────────────────

_columns_cache: dict[int, list[dict]] = {}
_columns_bm25_cache: dict[int, "BM25"] = {}


def _get_all_columns(datasource_id: int = 0) -> list[dict]:
    """Get all active columns from adh_column_metadata (cached per datasource)."""
    from backend.rag.metadata_cache import get_cached_columns, set_cached_columns

    # Try distributed cache first
    cached = get_cached_columns(datasource_id)
    if cached is not None:
        return cached

    # Also check local in-memory cache
    if datasource_id in _columns_cache:
        return _columns_cache[datasource_id]

    try:
        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                cur.execute(
                    f"SELECT table_name, column_name, data_type, "
                    f"column_comment, business_desc, is_key "
                    f"FROM adh_column_metadata "
                    f"WHERE is_active = 1 {ds_filter} "
                    f"ORDER BY table_name, column_name"
                )
                rows = cur.fetchall()
                _columns_cache[datasource_id] = rows
                set_cached_columns(datasource_id, rows)
                return rows
    except Exception as e:
        logger.warning("Failed to get all columns: %s", e)
        return []


def _build_columns_bm25_index(datasource_id: int) -> "BM25":
    """Build BM25 index from cached column metadata."""
    from backend.rag.bm25 import BM25
    from backend.rag.table_selector import _tokenize_text

    if datasource_id in _columns_bm25_cache:
        return _columns_bm25_cache[datasource_id]

    all_columns = _get_all_columns(datasource_id)
    if not all_columns:
        bm25 = BM25()
        bm25.index([])
        _columns_bm25_cache[datasource_id] = bm25
        return bm25

    documents = []
    for col in all_columns:
        text_parts = [
            col.get("column_name", ""),
            col.get("column_comment", ""),
            col.get("business_desc", ""),
        ]
        doc_text = " ".join(p for p in text_parts if p)
        documents.append(_tokenize_text(doc_text))

    bm25 = BM25()
    bm25.index(documents)
    _columns_bm25_cache[datasource_id] = bm25
    logger.info("Built BM25 index for %d columns (ds=%d)", len(all_columns), datasource_id)
    return bm25


def _bm25_search_columns(keywords: list[str], top_k: int, datasource_id: int) -> list[tuple[str, str]]:
    """BM25 sparse retrieval for columns. Returns list of (table_name, column_name)."""
    from backend.rag.bm25 import BM25
    from backend.rag.table_selector import _tokenize_text

    bm25 = _build_columns_bm25_index(datasource_id)
    if bm25.is_empty:
        return []

    query_tokens = []
    for kw in keywords:
        query_tokens.append(kw.lower())
        query_tokens.extend(_tokenize_text(kw))
    query_tokens = list(set(query_tokens))

    results = bm25.search(query_tokens, top_k=top_k)
    if not results:
        return []

    all_columns = _get_all_columns(datasource_id)
    return [(all_columns[idx]["table_name"], all_columns[idx]["column_name"]) for idx, _ in results]


def _vector_search_columns(query_embedding: list[float], limit: int = 50, datasource_id: int = 0) -> list[tuple[str, str]]:
    """Vector dense retrieval for columns. Returns list of (table_name, column_name)."""
    store = get_vector_store()

    filters = {"is_active": 1}
    if datasource_id:
        filters["_raw"] = f"(datasource_id = {datasource_id} OR datasource_id = 0)"

    rows = store.search(
        table="adh_column_metadata",
        query_embedding=query_embedding,
        limit=limit,
        filters=filters,
        output_columns=["table_name", "column_name"],
    )

    return [(r["table_name"], r["column_name"]) for r in rows]


def select_columns(
    question: str,
    keywords: list[str] = None,
    top_k: int = 50,
    vector_literal: str = None,
    datasource_id: int = 0,
) -> list[dict]:
    """Select relevant columns using BM25 sparse + vector dense hybrid retrieval."""
    from backend.rag.bm25 import rrf_merge
    from backend.rag.table_selector import _extract_keywords, _expand_synonyms

    if keywords is None:
        keywords = _extract_keywords(question)
        keywords = _expand_synonyms(keywords)

    # Generate embedding if not provided
    query_embedding = None
    if vector_literal:
        # Convert SQL literal back to list (approximate)
        try:
            query_embedding = generate_embedding(question)
        except Exception:
            query_embedding = None
    else:
        try:
            query_embedding = generate_embedding(question)
        except Exception:
            query_embedding = None

    # Step 1: BM25 sparse retrieval
    bm25_columns = _bm25_search_columns(keywords, top_k * 2, datasource_id)
    logger.debug("BM25 columns: %d results", len(bm25_columns))

    # Step 2: Vector dense retrieval
    vector_columns = []
    if query_embedding:
        vector_columns = _vector_search_columns(query_embedding, top_k * 2, datasource_id)
        logger.debug("Vector columns: %d results", len(vector_columns))

    # Step 3: RRF fusion
    bm25_ids = [f"{t}.{c}" for t, c in bm25_columns]
    vector_ids = [f"{t}.{c}" for t, c in vector_columns]

    rankings = []
    weights = []
    if bm25_ids:
        rankings.append(bm25_ids)
        weights.append(1.0)
    if vector_ids:
        rankings.append(vector_ids)
        weights.append(1.0)

    if not rankings:
        logger.warning("No BM25 or vector results for columns")
        return []

    merged = rrf_merge(rankings, weights=weights)
    logger.info("Column RRF: bm25=%d, vector=%d, merged=%d", len(bm25_ids), len(vector_ids), len(merged))

    # Step 4: Get full column metadata for merged results
    all_columns = _get_all_columns(datasource_id)
    columns_map = {}
    for col in all_columns:
        key = f"{col['table_name']}.{col['column_name']}"
        columns_map[key] = col

    result = []
    for item_id, score in merged[:top_k]:
        if item_id in columns_map:
            result.append(columns_map[item_id])

    # Boost: time-related columns from matched tables
    matched_tables = {c["table_name"] for c in result}
    time_patterns = ("time", "date", "月", "日", "年", "创建时间", "更新时间",
                     "create_time", "update_time", "created_at", "updated_at")
    for col in all_columns:
        if col["table_name"] in matched_tables:
            col_name_lower = col["column_name"].lower()
            if any(p in col_name_lower for p in time_patterns):
                key = f"{col['table_name']}.{col['column_name']}"
                if key not in {f"{c['table_name']}.{c['column_name']}" for c in result}:
                    result.append(col)

    logger.info("select_columns: returned %d columns from %d tables", len(result), len(matched_tables))
    return result[:top_k]


_rules_column_checked = False


def _ensure_rules_column():
    """Check if 'rules' column exists in adh_sql_templates, add it if not."""
    global _rules_column_checked
    if _rules_column_checked:
        return
    try:
        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                if METADATA_DB_TYPE == "sqlite":
                    # SQLite: use PRAGMA to check column
                    cur.execute("PRAGMA table_info(adh_sql_templates)")
                    columns = [row["name"] if isinstance(row, dict) else row[1] for row in cur.fetchall()]
                    if "rules" not in columns:
                        cur.execute("ALTER TABLE adh_sql_templates ADD COLUMN rules TEXT DEFAULT ''")
                        conn.commit()
                else:
                    cur.execute(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = 'rules'",
                        (METADATA_DB_DATABASE, "adh_sql_templates"),
                    )
                    if not cur.fetchone():
                        logger.info("Adding 'rules' column to %s...", "adh_sql_templates")
                        cur.execute("ALTER TABLE adh_sql_templates ADD COLUMN rules STRING DEFAULT ''")
                        conn.commit()
                        logger.info("Successfully added 'rules' column")
                _rules_column_checked = True
    except Exception as e:
        logger.warning("Failed to check/add 'rules' column: %s", e)


def retrieve_sql_templates(question: str, limit: int = 5, vec_literal: str = None, datasource_id: int = 0) -> list[dict]:
    """Retrieve matching SQL templates via vector similarity."""
    _ensure_rules_column()

    store = get_vector_store()
    query_embedding = generate_embedding(question)

    filters = {"is_active": 1}
    if datasource_id:
        filters["_raw"] = f"(datasource_id = {datasource_id} OR datasource_id = 0)"

    rows = store.search(
        table="adh_sql_templates",
        query_embedding=query_embedding,
        limit=limit,
        filters=filters,
        output_columns=["template_id", "template_name", "category", "intent_keywords",
                        "sql_template", "variables", "description", "rules", "usage_count"],
    )

    logger.debug("Retrieved %d sql_templates, rules present: %s",
                 len(rows), [bool(r.get("rules")) for r in rows])
    return rows


def retrieve_business_terms(
    question: str,
    limit: int = 20,
    keywords: list[str] = None,
    vec_literal: str = None,
    datasource_id: int = 0,
) -> list[dict]:
    """Retrieve matching business terms via vector similarity."""
    store = get_vector_store()
    query_embedding = generate_embedding(question)

    filters = {"is_active": 1}
    if datasource_id:
        filters["_raw"] = f"(datasource_id = {datasource_id} OR datasource_id = 0)"

    # If keywords provided, also search by keyword match
    if keywords:
        keyword_conditions = []
        for kw in keywords[:5]:
            escaped = kw.replace("'", "''")
            keyword_conditions.append(
                f"(term_cn LIKE '%{escaped}%' OR term_en LIKE '%{escaped}%' OR term_aliases LIKE '%{escaped}%')"
            )
        if keyword_conditions:
            keyword_sql = " OR ".join(keyword_conditions)
            existing_raw = filters.get("_raw", "")
            if existing_raw:
                filters["_raw"] = f"({existing_raw}) AND ({keyword_sql})"
            else:
                filters["_raw"] = keyword_sql

    rows = store.search(
        table="adh_business_terms",
        query_embedding=query_embedding,
        limit=limit,
        filters=filters,
        output_columns=["term_cn", "term_en", "term_aliases", "term_type",
                        "target_table", "target_column", "calculation", "description"],
    )

    return rows


def retrieve_saved_datasets(question: str, limit: int = 5) -> list[dict]:
    """Retrieve matching saved datasets from Playground."""
    try:
        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                keywords_sql = question.replace("'", "''")
                cur.execute(f"""
                    SELECT id, name, description, sql_query, dataset_keywords
                    FROM {"adh_saved_queries"}
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
    """Retrieve matching table relations via vector similarity."""
    store = get_vector_store()
    query_embedding = generate_embedding(question)

    filters = {"is_active": 1}
    if datasource_id:
        filters["_raw"] = f"(datasource_id = {datasource_id} OR datasource_id = 0)"

    rows = store.search(
        table="adh_table_relations",
        query_embedding=query_embedding,
        limit=limit,
        filters=filters,
        output_columns=["source_table", "source_column", "target_table", "target_column",
                        "relation_type", "join_type", "description"],
    )

    # If target_tables specified, boost matching relations to top
    if target_tables and rows:
        matched = []
        unmatched = []
        for r in rows:
            src = r["source_table"].lower()
            tgt = r["target_table"].lower()
            if any(t.lower() in src or src in t.lower() for t in target_tables) or \
               any(t.lower() in tgt or tgt in t.lower() for t in target_tables):
                matched.append(r)
            else:
                unmatched.append(r)
        rows = matched + unmatched

    return rows


def retrieve_filtered(
    question: str,
    target_tables: list[str] = None,
    keywords: list[str] = None,
    datasource_id: int = 0,
) -> dict:
    """Run targeted vector retrievals with table and keyword filtering."""
    table_info = retrieve_table_info(question, target_tables=target_tables, datasource_id=datasource_id)
    column_metadata = retrieve_column_metadata(question, target_tables=target_tables, datasource_id=datasource_id)

    rag_source = "vector_search"
    if not table_info and not column_metadata:
        logger.warning("RAG filtered search returned empty metadata, falling back to information_schema")
        fallback = _fallback_from_information_schema(target_tables=target_tables)
        table_info = fallback["table_info"]
        column_metadata = fallback["column_metadata"]
        rag_source = "information_schema_fallback"

    return {
        "table_info": table_info,
        "column_metadata": column_metadata,
        "sql_templates": retrieve_sql_templates(question, datasource_id=datasource_id),
        "business_terms": retrieve_business_terms(question, keywords=keywords, datasource_id=datasource_id),
        "table_relations": retrieve_table_relations(question, target_tables=target_tables, datasource_id=datasource_id),
        "saved_datasets": retrieve_saved_datasets(question),
        "rag_source": rag_source,
    }


def _normalize_table_keyword(kw: str) -> list[str]:
    """Generate search variants for a table keyword."""
    variants = [kw]
    lower = kw.lower()
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
    """Fallback: fetch table/column metadata directly from information_schema."""
    result = {"table_info": [], "column_metadata": []}

    # information_schema is MySQL/Doris only — skip for SQLite
    from backend.common.config import METADATA_DB_TYPE
    if METADATA_DB_TYPE == "sqlite":
        logger.debug("Skipping information_schema fallback (SQLite mode)")
        return result

    try:
        conn = pymysql.connect(
            host=VECTOR_DB_HOST, port=VECTOR_DB_PORT, user=VECTOR_DB_USER,
            password=VECTOR_DB_PASSWORD, database="information_schema",
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10, read_timeout=30,
        )
        try:
            with conn.cursor() as cur:
                tables = []
                if target_tables:
                    placeholders = ", ".join(["%s"] * len(target_tables))
                    cur.execute(
                        f"SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
                        f"WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                        f"AND TABLE_NAME IN ({placeholders})",
                        [DORIS_DATABASE] + target_tables,
                    )
                    tables = cur.fetchall()

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
        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                placeholders = ", ".join(["%s"] * len(table_names))
                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                cur.execute(
                    f"SELECT table_name, column_name, data_type, "
                    f"column_comment, business_desc, is_key "
                    f"FROM {"adh_column_metadata"} "
                    f"WHERE is_active = 1 AND table_name IN ({placeholders}) {ds_filter} "
                    f"ORDER BY table_name, column_name",
                    table_names,
                )
                return cur.fetchall()
    except Exception as e:
        logger.warning("Failed to get columns for tables %s: %s", table_names, e)
        return []


def retrieve_all(
    question: str,
    target_tables: list[str] = None,
    keywords: list[str] = None,
    selected_tables: list[str] = None,
    datasource_id: int = 0,
) -> dict:
    """Retrieve RAG metadata: table schema, SQL templates, business terms."""
    cache_key = _rag_cache_key(question, selected_tables or target_tables, keywords, datasource_id)
    if cache_key in _RAG_CACHE:
        _RAG_CACHE.move_to_end(cache_key)
        logger.info("RAG cache hit: %s", question[:50])
        return _RAG_CACHE[cache_key]

    # Step 1: Get table info
    if selected_tables:
        logger.info("RAG: using pre-selected tables: %s", selected_tables)
        table_info = _get_table_info_for_names(selected_tables, datasource_id)
        rag_source = "keyword_selected"
    else:
        table_info = retrieve_table_info(question, 20, target_tables, datasource_id=datasource_id)
        rag_source = "vector_search"

    # Step 2: Get ALL columns for the selected tables
    top_table_names = [t["table_name"] for t in table_info]
    column_metadata = _get_columns_for_tables(top_table_names, datasource_id)

    # Step 3: Parallel searches
    sql_templates = []
    business_terms = []
    table_relations = []
    saved_datasets = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_sql = pool.submit(retrieve_sql_templates, question, 5, None, datasource_id)
        f_terms = pool.submit(retrieve_business_terms, question, 20, keywords, None, datasource_id)
        f_rels = pool.submit(retrieve_table_relations, question, 20, top_table_names, None, datasource_id)
        f_ds = pool.submit(retrieve_saved_datasets, question)
        try:
            sql_templates = f_sql.result()
        except Exception as e:
            logger.warning("sql_templates failed: %s", e)
        try:
            business_terms = f_terms.result()
        except Exception as e:
            logger.warning("business_terms failed: %s", e)
        try:
            table_relations = f_rels.result()
        except Exception as e:
            logger.warning("table_relations failed: %s", e)
        try:
            saved_datasets = f_ds.result()
        except Exception as e:
            logger.warning("saved_datasets failed: %s", e)

    # Fallback
    if not table_info and not column_metadata:
        logger.warning("RAG returned empty metadata, falling back to information_schema")
        fallback_tables = selected_tables or target_tables
        fallback = _fallback_from_information_schema(target_tables=fallback_tables)
        table_info = fallback["table_info"]
        column_metadata = fallback["column_metadata"]
        rag_source = "information_schema_fallback"
        if not table_info and not column_metadata:
            logger.error("Fallback also returned empty! tables=%s may not exist in database=%s",
                         fallback_tables, DORIS_DATABASE)
            rag_source = "empty"

    result = {
        "table_info": table_info,
        "column_metadata": column_metadata,
        "sql_templates": sql_templates,
        "business_terms": business_terms,
        "table_relations": table_relations,
        "saved_datasets": saved_datasets,
        "rag_source": rag_source,
    }

    _RAG_CACHE[cache_key] = result
    if len(_RAG_CACHE) > _RAG_CACHE_MAX:
        _RAG_CACHE.popitem(last=False)

    return result


def _get_table_info_for_names(table_names: list[str], datasource_id: int = 0) -> list[dict]:
    """Get table_info rows for specific table names from adh_table_info."""
    if not table_names:
        return []
    try:
        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                placeholders = ", ".join(["%s"] * len(table_names))
                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                cur.execute(
                    f"SELECT table_name, table_comment, table_business_desc, "
                    f"region_tag, domain_tag "
                    f"FROM {"adh_table_info"} "
                    f"WHERE is_active = 1 AND table_name IN ({placeholders}) {ds_filter}",
                    table_names,
                )
                rows = cur.fetchall()
                name_order = {name: i for i, name in enumerate(table_names)}
                rows.sort(key=lambda r: name_order.get(r["table_name"], 999))
                return rows
    except Exception as e:
        logger.warning("Failed to get table_info for names %s: %s", table_names, e)
        return []


def retrieve_tables_metadata(
    table_names: list[str],
    datasource_id: int = 0,
) -> dict:
    """Directly retrieve metadata for specific table names, skipping vector search."""
    if not table_names:
        return {"table_info": [], "column_metadata": [], "table_relations": []}

    table_info = _get_table_info_for_names(table_names, datasource_id)
    column_metadata = _get_columns_for_tables(table_names, datasource_id)

    table_relations = []
    try:
        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                like_conditions = []
                like_params = []
                for t in table_names:
                    like_conditions.append(f"(source_table = %s OR target_table = %s)")
                    like_params.extend([t, t])
                where_like = " OR ".join(like_conditions)
                ds_filter = f"AND (datasource_id = {datasource_id} OR datasource_id = 0)" if datasource_id else ""
                cur.execute(
                    f"SELECT source_table, source_column, target_table, target_column, "
                    f"relation_type, join_type, description "
                    f"FROM {"adh_table_relations"} "
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
        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {"adh_sql_templates"} SET usage_count = usage_count + 1 WHERE template_id = %s",
                    (template_id,),
                )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to increment template usage: %s", e)


def increment_term_usage(term_cn: str) -> None:
    """Increment usage_count for a business term."""
    try:
        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {"adh_business_terms"} SET usage_count = usage_count + 1 WHERE term_cn = %s",
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
    """Retrieve metadata using the specified strategy."""
    from backend.rag.strategies import get_strategy, get_strategy_from_config

    if strategy_name:
        strategy = get_strategy(strategy_name)
    else:
        strategy = get_strategy_from_config(model_id=model_id)

    cache_key = _rag_cache_key(
        question, selected_tables or target_tables, keywords, datasource_id,
        strategy_name=strategy.name,
    )
    if cache_key in _RAG_CACHE:
        _RAG_CACHE.move_to_end(cache_key)
        logger.info("RAG cache hit (strategy=%s): %s", strategy.name, question[:50])
        return _RAG_CACHE[cache_key]

    result = strategy.retrieve(
        question=question,
        selected_tables=selected_tables,
        target_tables=target_tables,
        keywords=keywords,
        datasource_id=datasource_id,
    )

    _RAG_CACHE[cache_key] = result
    if len(_RAG_CACHE) > _RAG_CACHE_MAX:
        _RAG_CACHE.popitem(last=False)

    return result
