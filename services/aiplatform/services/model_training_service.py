"""Model Training Service — Fine-tune embedding model using feedback data.

Migrated from backend/api/model_train.py.
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

from services.shared.common.db import DBConnection, execute_query

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "models"
)


def get_feedback_stats() -> dict:
    """Get feedback statistics."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM adh_search_feedback WHERE satisfied = 1")
            positive = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM adh_search_feedback WHERE satisfied = 0")
            negative = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM adh_search_feedback")
            total = cur.fetchone()["cnt"]
    return {"positive": positive, "negative": negative, "total": total}


def list_model_versions() -> list:
    """List existing model versions."""
    versions = []
    if os.path.isdir(MODELS_DIR):
        for name in sorted(os.listdir(MODELS_DIR)):
            path = os.path.join(MODELS_DIR, name)
            if os.path.isdir(path):
                has_config = os.path.exists(os.path.join(path, "config.json"))
                versions.append({
                    "name": name,
                    "path": path,
                    "is_valid": has_config,
                    "created": datetime.fromtimestamp(os.path.getctime(path)).strftime("%Y-%m-%d %H:%M"),
                })
    return versions


def get_training_stats() -> dict:
    """Get feedback and training data statistics."""
    stats = get_feedback_stats()
    versions = list_model_versions()

    sample_count = 0
    try:
        samples = build_training_data()
        sample_count = len(samples)
    except Exception as e:
        logger.warning("Failed to build training data: %s", e)

    from services.shared.common.config import EMBEDDING_MODEL_PATH
    return {
        "feedback": stats,
        "training_samples": sample_count,
        "versions": versions,
        "current_model": EMBEDDING_MODEL_PATH,
    }


def build_training_data() -> list:
    """Build (query, positive_text, negative_text) triplets from feedback."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT f.question, f.tables_used, f.expected_table, f.top_tables,
                       t.table_name, t.table_comment, t.keywords
                FROM adh_search_feedback f
                LEFT JOIN adh_table_info t ON FIND_IN_SET(t.table_name, f.tables_used) > 0
                    AND t.datasource_id = f.datasource_id AND t.is_active = 1
                WHERE f.satisfied = 1 AND f.tables_used != ''
            """)
            positive_rows = cur.fetchall()

            cur.execute("""
                SELECT f.question, f.tables_used, f.expected_table, f.top_tables,
                       t.table_name, t.table_comment, t.keywords
                FROM adh_search_feedback f
                LEFT JOIN adh_table_info t ON FIND_IN_SET(t.table_name, f.tables_used) > 0
                    AND t.datasource_id = f.datasource_id AND t.is_active = 1
                WHERE f.satisfied = 0 AND f.tables_used != ''
            """)
            negative_rows = cur.fetchall()

    triplets = []

    for row in positive_rows:
        query = row["question"]
        pos_text = f"{row['table_name']} {row.get('table_comment') or ''} {row.get('keywords') or ''}"
        if not pos_text.strip() or not row["table_name"]:
            continue
        used = set((row["tables_used"] or "").split(","))
        top = (row["top_tables"] or "").split(",")
        for t in top:
            if t and t not in used:
                triplets.append({
                    "query": query, "positive": pos_text.strip(),
                    "negative_table": t, "source": "positive_feedback",
                })

    for row in negative_rows:
        query = row["question"]
        neg_text = f"{row['table_name']} {row.get('table_comment') or ''} {row.get('keywords') or ''}"
        if not neg_text.strip() or not row["table_name"]:
            continue
        if row["expected_table"]:
            exp = execute_query(
                "SELECT table_name, table_comment, keywords FROM adh_table_info "
                "WHERE table_name = %s AND is_active = 1 LIMIT 1",
                (row["expected_table"],), fetchone=True,
            )
            if exp:
                pos_text = f"{exp['table_name']} {exp.get('table_comment') or ''} {exp.get('keywords') or ''}"
                triplets.append({
                    "query": query, "positive": pos_text.strip(),
                    "negative": neg_text.strip(), "source": "negative_feedback_with_expected",
                })
        else:
            top = (row["top_tables"] or "").split(",")
            for t in top:
                if t and t != row["table_name"]:
                    triplets.append({
                        "query": query, "positive_table": t,
                        "negative": neg_text.strip(), "source": "negative_feedback_top",
                    })

    # Resolve unresolved table references
    unresolved = set()
    for t in triplets:
        if "negative_table" in t:
            unresolved.add(t["negative_table"])
        if "positive_table" in t:
            unresolved.add(t["positive_table"])

    table_cache = {}
    if unresolved:
        placeholders = ", ".join(["%s"] * len(unresolved))
        rows = execute_query(
            f"SELECT table_name, table_comment, keywords FROM adh_table_info "
            f"WHERE table_name IN ({placeholders}) AND is_active = 1",
            list(unresolved),
        )
        for r in rows:
            table_cache[r["table_name"]] = (
                f"{r['table_name']} {r.get('table_comment') or ''} {r.get('keywords') or ''}".strip()
            )

    final = []
    for t in triplets:
        if "negative_table" in t:
            neg_text = table_cache.get(t["negative_table"], "")
            if neg_text and t["positive"]:
                final.append({"query": t["query"], "positive": t["positive"], "negative": neg_text})
        elif "positive_table" in t:
            pos_text = table_cache.get(t["positive_table"], "")
            if pos_text and t["negative"]:
                final.append({"query": t["query"], "positive": pos_text, "negative": t["negative"]})
        elif "positive" in t and "negative" in t:
            final.append({"query": t["query"], "positive": t["positive"], "negative": t["negative"]})

    return final


def delete_version(version_name: str) -> bool:
    """Delete a model version."""
    import shutil
    path = os.path.join(MODELS_DIR, version_name)
    if not os.path.isdir(path):
        return False
    shutil.rmtree(path)
    return True
