"""
Rebuild all RAG table embeddings using the text2vec model.

When VECTOR_DB_TYPE=default: writes embeddings to METADATA_DB (persist).
When VECTOR_DB_TYPE=doris: writes embeddings to Doris vector DB.
When VECTOR_DB_TYPE=qdrant: writes embeddings to Qdrant vector DB.

Usage:
    python -m sync.rebuild_vectors
"""

import json
import os
import sys
import time as _time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.common.config import VECTOR_DB_TYPE
from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal
from backend.common.db.metadata_db import get_metadata_connection

BATCH_SIZE = 50

# Qdrant mode: use VectorStore abstraction
_vector_store = None


def _get_vector_store():
    """Get VectorStore instance (lazy, for qdrant mode only)."""
    global _vector_store
    if _vector_store is None:
        from backend.common.vector import get_vector_store
        _vector_store = get_vector_store()
    return _vector_store


def _update_embedding(conn, table: str, row_id: int, embedding: list[float]):
    """Update embedding column in METADATA_DB (for default/doris mode)."""
    emb_json = json.dumps(embedding)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {table} SET embedding = %s WHERE id = %s",
            (emb_json, row_id)
        )


def _upsert_to_qdrant(table: str, row: dict, embedding: list[float]):
    """Write a single record with embedding to Qdrant."""
    store = _get_vector_store()
    data = {**row, "embedding": embedding}
    store.upsert(table=table, id_column="id", id_value=row["id"], data=data)


def _rebuild_table_info():
    """Rebuild embeddings for adh_table_info."""
    print("[rebuild] adh_table_info ...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_metadata_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, datasource_id, table_name, table_comment, "
                "table_business_desc, keywords, region_tag, domain_tag, "
                "is_active, sync_time "
                "FROM adh_table_info"
            )
            rows = cur.fetchall()

    total = len(rows)
    if total == 0:
        print("  No rows to rebuild.")
        return

    updated = 0
    for row in rows:
        embed_text = (
            f"{row['table_name']} {row.get('table_comment') or ''} "
            f"{row.get('table_business_desc') or ''} {row.get('keywords') or ''} "
            f"{row.get('region_tag') or ''} {row.get('domain_tag') or ''}"
        )
        embedding = generate_embedding(embed_text)

        if VECTOR_DB_TYPE == "qdrant":
            _upsert_to_qdrant("adh_table_info", row, embedding)
        else:
            with get_metadata_connection() as conn:
                _update_embedding(conn, "adh_table_info", row["id"], embedding)
                conn.commit()

        updated += 1
        if updated % BATCH_SIZE == 0:
            print(f"  table_info: {updated}/{total}")

    print(f"[rebuild] adh_table_info done — {updated} rows.")


def _rebuild_column_metadata():
    """Rebuild embeddings for adh_column_metadata."""
    print("[rebuild] adh_column_metadata ...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_metadata_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, datasource_id, table_name, column_name, data_type, "
                "column_comment, business_desc, is_key, is_nullable, is_active, sync_time "
                "FROM adh_column_metadata"
            )
            rows = cur.fetchall()

    total = len(rows)
    if total == 0:
        print("  No rows to rebuild.")
        return

    updated = 0
    for row in rows:
        embed_text = (
            f"{row['table_name']} {row['column_name']} "
            f"{row.get('column_comment') or ''} {row.get('business_desc') or ''} "
            f"{row['data_type']}"
        )
        embedding = generate_embedding(embed_text)

        if VECTOR_DB_TYPE == "qdrant":
            _upsert_to_qdrant("adh_column_metadata", row, embedding)
        else:
            with get_metadata_connection() as conn:
                _update_embedding(conn, "adh_column_metadata", row["id"], embedding)
                conn.commit()

        updated += 1
        if updated % BATCH_SIZE == 0:
            print(f"  column_metadata: {updated}/{total}")

    print(f"[rebuild] adh_column_metadata done — {updated} rows.")


def _rebuild_sql_templates():
    """Rebuild embeddings for adh_sql_templates."""
    print("[rebuild] adh_sql_templates ...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_metadata_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, datasource_id, template_id, template_name, category, "
                "intent_keywords, sql_template, variables, rules, description, "
                "usage_count, is_active, created_at, updated_at "
                "FROM adh_sql_templates"
            )
            rows = cur.fetchall()

    total = len(rows)
    if total == 0:
        print("  No rows to rebuild.")
        return

    updated = 0
    for row in rows:
        embed_text = f"{row['template_name']} {row.get('intent_keywords') or ''} {row.get('description') or ''}"
        embedding = generate_embedding(embed_text)

        if VECTOR_DB_TYPE == "qdrant":
            _upsert_to_qdrant("adh_sql_templates", row, embedding)
        else:
            with get_metadata_connection() as conn:
                _update_embedding(conn, "adh_sql_templates", row["id"], embedding)
                conn.commit()

        updated += 1
        if updated % BATCH_SIZE == 0:
            print(f"  templates: {updated}/{total}")

    print(f"[rebuild] adh_sql_templates done — {updated} rows.")


def _rebuild_business_terms():
    """Rebuild embeddings for adh_business_terms."""
    print("[rebuild] adh_business_terms ...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_metadata_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, datasource_id, term_cn, term_en, term_aliases, term_type, "
                "target_table, target_column, calculation, description, "
                "usage_count, is_active, created_at, updated_at "
                "FROM adh_business_terms"
            )
            rows = cur.fetchall()

    total = len(rows)
    if total == 0:
        print("  No rows to rebuild.")
        return

    updated = 0
    for row in rows:
        embed_text = (
            f"{row['term_cn']} {row.get('term_en') or ''} "
            f"{row.get('term_aliases') or ''} {row.get('description') or ''}"
        )
        embedding = generate_embedding(embed_text)

        if VECTOR_DB_TYPE == "qdrant":
            _upsert_to_qdrant("adh_business_terms", row, embedding)
        else:
            with get_metadata_connection() as conn:
                _update_embedding(conn, "adh_business_terms", row["id"], embedding)
                conn.commit()

        updated += 1
        if updated % BATCH_SIZE == 0:
            print(f"  terms: {updated}/{total}")

    print(f"[rebuild] adh_business_terms done — {updated} rows.")


def _rebuild_table_relations():
    """Rebuild embeddings for adh_table_relations."""
    print("[rebuild] adh_table_relations ...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_metadata_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, datasource_id, source_table, source_column, "
                "target_table, target_column, relation_type, join_type, "
                "description, is_active, created_at, updated_at "
                "FROM adh_table_relations"
            )
            rows = cur.fetchall()

    total = len(rows)
    if total == 0:
        print("  No rows to rebuild.")
        return

    updated = 0
    for row in rows:
        embed_text = (
            f"{row['source_table']} {row['source_column']} "
            f"{row['target_table']} {row['target_column']} "
            f"{row.get('description') or ''}"
        )
        embedding = generate_embedding(embed_text)

        if VECTOR_DB_TYPE == "qdrant":
            _upsert_to_qdrant("adh_table_relations", row, embedding)
        else:
            with get_metadata_connection() as conn:
                _update_embedding(conn, "adh_table_relations", row["id"], embedding)
                conn.commit()

        updated += 1
        if updated % BATCH_SIZE == 0:
            print(f"  relations: {updated}/{total}")

    print(f"[rebuild] adh_table_relations done — {updated} rows.")


def rebuild_all():
    """Full rebuild: regenerate all embeddings."""
    print("=" * 60)
    print("RAG Vector Rebuild")
    print(f"VECTOR_DB_TYPE: {VECTOR_DB_TYPE}")
    print("=" * 60)

    # Pre-load model
    from backend.common.llm.embedding import _get_model
    model = _get_model()
    if model is None:
        print("WARNING: Model failed to load, will use hash fallback.")
    else:
        print("Model loaded successfully.")

    print("\n[step 1] Rebuilding table_info embeddings ...")
    _rebuild_table_info()

    print("\n[step 2] Rebuilding column_metadata embeddings ...")
    _rebuild_column_metadata()

    print("\n[step 3] Rebuilding sql_templates embeddings ...")
    _rebuild_sql_templates()

    print("\n[step 4] Rebuilding business_terms embeddings ...")
    _rebuild_business_terms()

    print("\n[step 5] Rebuilding table_relations embeddings ...")
    _rebuild_table_relations()

    dest = "Qdrant" if VECTOR_DB_TYPE == "qdrant" else "METADATA_DB"
    print("\n" + "=" * 60)
    print(f"All done! Embeddings saved to {dest}.")
    if VECTOR_DB_TYPE == "qdrant":
        print("Qdrant collections are ready for search.")
    else:
        print("Restart the application to load them into memory.")
    print("=" * 60)


if __name__ == "__main__":
    rebuild_all()
