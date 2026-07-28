"""
AI-DataHub Metadata Sync
Incremental sync of table info and column metadata from MySQL/Doris/Elasticsearch.
- Table info → adh.adh_table_info (table_comment, business_desc, tags)
- Column metadata → adh.adh_column_metadata (column_comment, business_desc)

Preserves user-defined business_desc. Only updates rows where system fields have changed.

Usage:
    python -m sync.metadata_sync
"""

import os
import re
import sys
import time as _time
from datetime import datetime

import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.common.llm.embedding import generate_embedding, embedding_to_sql_literal
from backend.common.crypto import decrypt_password, is_encrypted
from backend.common.config import METADATA_DB_DATABASE
from backend.common.db.metadata_db import get_metadata_connection


# ---------------------------------------------------------------------------
# Region / Domain tag extraction
# ---------------------------------------------------------------------------

_REGION_SUFFIXES = {
    "_cn": "cn", "_en": "en", "_eu": "eu",
    "_jp": "jp", "_uk": "uk", "_us": "us",
}

_DOMAIN_PREFIX_RULES = [
    (r"^dim_case\b", "case"),
    (r"^t_equipment\b", "equipment"),
    (r"^dim_user\b", "user"),
    (r"^dwd_observability\b", "observability"),
    (r"^dwd_t_case\b", "case"),
    (r"^dim_hospital\b", "hospital"),
    (r"^dwd_\w+", "dwd"),
    (r"^ods_\w+", "ods"),
    (r"^adh_", "adh"),
]


def extract_region_tag(table_name: str) -> str:
    lower = table_name.lower()
    for suffix, tag in _REGION_SUFFIXES.items():
        if lower.endswith(suffix):
            return tag
    return "all"


def extract_domain_tag(table_name: str) -> str:
    lower = table_name.lower()
    for pattern, domain in _DOMAIN_PREFIX_RULES:
        if re.match(pattern, lower):
            return domain
    return "other"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_doris_conn(database="information_schema"):
    """Get a connection to the metadata database.

    When METADATA_DB_TYPE=sqlite, returns the SQLite connection.
    Otherwise, returns a pymysql connection to MySQL/Doris.
    """
    from backend.common.config import METADATA_DB_TYPE
    if METADATA_DB_TYPE == "sqlite":
        from backend.common.db.metadata_db import get_metadata_conn
        return get_metadata_conn()
    return pymysql.connect(
        host=os.environ.get("METADATA_DB_HOST", os.environ.get("DORIS_HOST", "127.0.0.1")),
        port=int(os.environ.get("METADATA_DB_PORT", os.environ.get("DORIS_PORT", "9030"))),
        user=os.environ.get("METADATA_DB_USER", os.environ.get("DORIS_USER", "root")),
        password=os.environ.get("METADATA_DB_PASSWORD", os.environ.get("DORIS_PASSWORD", "")),
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _fetch_tables(conn, schema="alliedstar"):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'",
            (schema,),
        )
        return cur.fetchall()


def _fetch_columns(conn, table_name, schema="alliedstar"):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT, COLUMN_KEY, IS_NULLABLE "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (schema, table_name),
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Elasticsearch helpers
# ---------------------------------------------------------------------------

def _build_es_client(ds_config: dict):
    """Build an Elasticsearch client from datasource config."""
    try:
        from elasticsearch import Elasticsearch
    except ImportError:
        raise RuntimeError("elasticsearch 库未安装，请执行 pip install elasticsearch")

    protocol = "https" if ds_config.get("ssl") else "http"
    es_url = f"{protocol}://{ds_config['host']}:{ds_config['port']}"
    es_kwargs = {"hosts": [es_url], "request_timeout": 30}
    if ds_config.get("ssl"):
        es_kwargs["verify_certs"] = False
        es_kwargs["ssl_show_warn"] = False
    if ds_config.get("user") and ds_config.get("password"):
        es_kwargs["basic_auth"] = (ds_config["user"], ds_config["password"])
    elif ds_config.get("user"):
        es_kwargs["basic_auth"] = (ds_config["user"], "")
    return Elasticsearch(**es_kwargs)


def _fetch_es_indices(es) -> list:
    """Fetch all indices and aliases from Elasticsearch, excluding system indices."""
    result = []
    seen = set()

    # List concrete indices
    indices = es.cat.indices(format="json", h="index,docs.count,store.size")
    for idx in indices:
        name = idx.get("index", "")
        if name.startswith("."):
            continue
        seen.add(name)
        result.append({
            "TABLE_NAME": name,
            "TABLE_COMMENT": f"docs: {idx.get('docs.count', '?')}, size: {idx.get('store.size', '?')}",
        })

    # List aliases — aliases are valid sync targets (may span multiple indices)
    try:
        aliases = es.cat.aliases(format="json", h="alias,index")
        for row in aliases:
            alias = row.get("alias", "")
            if not alias or alias.startswith(".") or alias in seen:
                continue
            seen.add(alias)
            result.append({
                "TABLE_NAME": alias,
                "TABLE_COMMENT": "alias",
            })
    except Exception:
        pass  # alias listing is best-effort

    return result


def _deep_merge_properties(base: dict, overlay: dict) -> dict:
    """Deep-merge two ES mapping property dicts.

    When an alias points to multiple indices, each index may have slightly
    different fields. We union all fields; for conflicting nested objects
    we recurse, for conflicting leaf fields the first seen type wins.
    """
    merged = dict(base)
    for key, overlay_val in overlay.items():
        if key not in merged:
            merged[key] = overlay_val
            continue
        base_val = merged[key]
        # Both are nested objects — recurse
        if "properties" in base_val and "properties" in overlay_val:
            base_val["properties"] = _deep_merge_properties(
                base_val["properties"], overlay_val["properties"]
            )
        # else: first seen type wins (leaf conflict)
    return merged


def _flatten_es_properties(properties: dict, prefix: str = "") -> list:
    """Recursively flatten ES mapping properties into a flat field list.

    Handles nested objects and multi-fields. Returns list of
    {COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT, COLUMN_KEY, IS_NULLABLE}.
    """
    fields = []
    for field_name, field_info in properties.items():
        full_name = f"{prefix}{field_name}" if not prefix else f"{prefix}.{field_name}"
        es_type = field_info.get("type", "object")

        # Multi-fields (e.g. keyword sub-field of text)
        multi_fields = field_info.get("fields", {})
        if multi_fields:
            # Use the primary type, note multi-fields in comment
            sub_types = ", ".join(f"{k}:{v.get('type', '?')}" for k, v in multi_fields.items() if k != "keyword")
            comment = f"multi-fields: {sub_types}" if sub_types else ""
        else:
            comment = ""

        # Nested object — recurse
        if es_type == "object" or "properties" in field_info:
            sub_props = field_info.get("properties", {})
            if sub_props:
                fields.extend(_flatten_es_properties(sub_props, full_name))
            else:
                fields.append({
                    "COLUMN_NAME": full_name,
                    "DATA_TYPE": "object",
                    "COLUMN_COMMENT": comment,
                    "COLUMN_KEY": "false",
                    "IS_NULLABLE": "true",
                })
        else:
            fields.append({
                "COLUMN_NAME": full_name,
                "DATA_TYPE": es_type,
                "COLUMN_COMMENT": comment,
                "COLUMN_KEY": "false",
                "IS_NULLABLE": "true",
            })

    return fields


def _extract_properties_from_mapping(idx_mapping: dict) -> dict:
    """Extract properties dict from a single index mapping response.

    Handles different ES versions:
      ES 7.x:  {"mappings": {"_doc": {"properties": {...}}}}
      ES 7.x+: {"mappings": {"properties": {...}}}
      ES 8.x:  {"mappings": {"properties": {...}}}
    """
    mappings = idx_mapping.get("mappings", {})
    if not mappings:
        return {}

    # Try direct: {"mappings": {"properties": {...}}}
    if "properties" in mappings:
        return mappings["properties"]

    # Try with type name: {"mappings": {"_doc": {"properties": {...}}}}
    for key, val in mappings.items():
        if isinstance(val, dict) and "properties" in val:
            return val["properties"]

    return {}


def _fetch_es_fields(es, index_name: str) -> list:
    """Fetch field metadata for an ES index or alias via _mapping API.

    When index_name is an alias pointing to multiple indices, merges all
    underlying indices' schemas into a unified field list.
    Returns list of {COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT, COLUMN_KEY, IS_NULLABLE}.
    """
    mapping = es.indices.get_mapping(index=index_name)

    # Merge properties from all indices in the response (alias may span multiple)
    merged_properties = {}
    for _idx_name, idx_mapping in mapping.items():
        properties = _extract_properties_from_mapping(idx_mapping)
        if properties:
            merged_properties = _deep_merge_properties(merged_properties, properties)

    if not merged_properties:
        # Diagnostic: print what we got back to help debug
        print(f"[metadata_sync] WARNING: no properties found for '{index_name}'. "
              f"Mapping response keys: {list(mapping.keys())}. "
              f"Raw structure sample: {str(mapping)[:500]}")
        return []
    return _flatten_es_properties(merged_properties)


# ---------------------------------------------------------------------------
# Datasource config
# ---------------------------------------------------------------------------

def _get_datasource_config(ds_id: int) -> dict:
    """Read datasource connection config from adh_datasources table."""
    conn = _get_doris_conn(METADATA_DB_DATABASE)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT db_type, host, port, username, password, database_name, `ssl` "
                "FROM adh_datasources WHERE id = %s",
                (ds_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Datasource {ds_id} not found")
            # Decrypt password if it's encrypted
            password = row["password"] or ""
            if password and is_encrypted(password):
                try:
                    password = decrypt_password(password)
                except ValueError as e:
                    print(f"[metadata_sync] WARNING: Failed to decrypt password for datasource {ds_id}: {e}")
            return {
                "db_type": row.get("db_type") or "mysql",
                "host": row["host"],
                "port": row["port"],
                "user": row["username"],
                "password": password,
                "database": row.get("database_name") or "alliedstar",
                "ssl": bool(row.get("ssl", 0)),
            }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sync logic — MySQL / Doris (information_schema)
# ---------------------------------------------------------------------------

def _sync_mysql_metadata(ds_id: int, ds_config: dict) -> None:
    """Full sync for MySQL/Doris datasources."""
    src_conn = pymysql.connect(
        host=ds_config["host"], port=ds_config["port"],
        user=ds_config["user"], password=ds_config["password"],
        database="information_schema",
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    dst_conn = _get_doris_conn(METADATA_DB_DATABASE)
    target_schema = ds_config["database"]

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tables = _fetch_tables(src_conn, target_schema)

        # ── 1. Sync table info ──────────────────────────────────────────
        existing_tables = {}
        with dst_conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, is_active "
                "FROM adh_table_info WHERE datasource_id = %s",
                (ds_id,),
            )
            for row in cur.fetchall():
                existing_tables[row["table_name"]] = row

        fresh_table_names = set()
        tables_to_insert = []
        tables_to_update = []

        for tbl in tables:
            table_name = tbl["TABLE_NAME"]
            fresh_table_names.add(table_name)
            table_comment = tbl.get("TABLE_COMMENT", "") or ""
            region_tag = extract_region_tag(table_name)
            domain_tag = extract_domain_tag(table_name)

            old = existing_tables.get(table_name)
            if old is None:
                tables_to_insert.append({
                    "table_name": table_name,
                    "table_comment": table_comment,
                    "region_tag": region_tag,
                    "domain_tag": domain_tag,
                })
            else:
                changed = (
                    (old.get("table_comment") or "") != table_comment
                    or (old.get("region_tag") or "") != region_tag
                    or (old.get("domain_tag") or "") != domain_tag
                )
                if changed:
                    tables_to_update.append({
                        "id": old["id"],
                        "table_name": table_name,
                        "table_comment": table_comment,
                        "table_business_desc": old.get("table_business_desc") or "",
                        "keywords": old.get("keywords") or "",
                        "region_tag": region_tag,
                        "domain_tag": domain_tag,
                        "is_active": old.get("is_active", 1),
                    })

        tables_to_delete = set(existing_tables.keys()) - fresh_table_names

        with dst_conn.cursor() as cur:
            for tname in tables_to_delete:
                cur.execute("DELETE FROM adh_table_info WHERE table_name = %s AND datasource_id = %s", (tname, ds_id))

            for r in tables_to_update:
                embed_text = _table_embed_text(r['table_name'], r['table_comment'], r.get('keywords') or "", "", r['domain_tag'])
                vec_literal = _make_embedding(embed_text)
                cur.execute("DELETE FROM adh_table_info WHERE id = %s", (r["id"],))
                cur.execute(
                    "INSERT INTO adh_table_info "
                    "(id, datasource_id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r["id"], ds_id, r["table_name"], r["table_comment"], r["table_business_desc"],
                     r.get("keywords") or "", r["region_tag"], r["domain_tag"], r["is_active"], now, vec_literal),
                )

            for r in tables_to_insert:
                row_id = int(_time.time() * 1000000) + tables_to_insert.index(r)
                embed_text = _table_embed_text(r['table_name'], r['table_comment'], "", "", r['domain_tag'])
                vec_literal = _make_embedding(embed_text)
                cur.execute(
                    "INSERT INTO adh_table_info "
                    "(id, datasource_id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)",
                    (row_id, ds_id, r["table_name"], r["table_comment"], "", "",
                     r["region_tag"], r["domain_tag"], now, vec_literal),
                )

        # ── 2. Sync column metadata ─────────────────────────────────────
        existing_cols = {}
        with dst_conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, column_name, data_type, column_comment, business_desc, keywords, "
                "is_key, is_nullable, is_active "
                "FROM adh_column_metadata WHERE datasource_id = %s",
                (ds_id,),
            )
            for row in cur.fetchall():
                key = (row["table_name"], row["column_name"])
                existing_cols[key] = row

        fresh_col_keys = set()
        cols_to_insert = []
        cols_to_update = []

        for tbl in tables:
            table_name = tbl["TABLE_NAME"]
            columns = _fetch_columns(src_conn, table_name, target_schema)
            for col in columns:
                col_name = col["COLUMN_NAME"]
                key = (table_name, col_name)
                fresh_col_keys.add(key)

                is_key = "true" if col["COLUMN_KEY"] == "PRI" else "false"
                col_comment = col.get("COLUMN_COMMENT", "") or ""
                data_type = col["DATA_TYPE"]
                is_nullable = col["IS_NULLABLE"]

                old = existing_cols.get(key)
                if old is None:
                    cols_to_insert.append({
                        "table_name": table_name,
                        "column_name": col_name,
                        "data_type": data_type,
                        "column_comment": col_comment,
                        "is_key": is_key,
                        "is_nullable": is_nullable,
                    })
                else:
                    changed = (
                        (old.get("data_type") or "") != data_type
                        or (old.get("column_comment") or "") != col_comment
                        or (old.get("is_key") or "") != is_key
                        or (old.get("is_nullable") or "") != is_nullable
                    )
                    if changed:
                        cols_to_update.append({
                            "id": old["id"],
                            "table_name": table_name,
                            "column_name": col_name,
                            "data_type": data_type,
                            "column_comment": col_comment,
                            "business_desc": old.get("business_desc") or "",
                            "keywords": old.get("keywords") or "",
                            "is_key": is_key,
                            "is_nullable": is_nullable,
                            "is_active": old.get("is_active", 1),
                        })

        cols_to_delete = set(existing_cols.keys()) - fresh_col_keys

        with dst_conn.cursor() as cur:
            for (tn, cn) in cols_to_delete:
                cur.execute(
                    "DELETE FROM adh_column_metadata WHERE table_name = %s AND column_name = %s AND datasource_id = %s",
                    (tn, cn, ds_id),
                )

            for r in cols_to_update:
                embed_text = _col_embed_text(r['table_name'], r['column_name'], "", r['column_comment'], r.get('keywords') or "")
                vec_literal = _make_embedding(embed_text)
                cur.execute("DELETE FROM adh_column_metadata WHERE id = %s", (r["id"],))
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r["id"], ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], r["business_desc"], r.get("keywords") or "",
                     r["is_key"], r["is_nullable"], r["is_active"], now, vec_literal),
                )

            for r in cols_to_insert:
                row_id = int(_time.time() * 1000000) + cols_to_insert.index(r) + 500000
                embed_text = _col_embed_text(r['table_name'], r['column_name'], "", r['column_comment'], "")
                vec_literal = _make_embedding(embed_text)
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)",
                    (row_id, ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], "", "", r["is_key"], r["is_nullable"],
                     now, vec_literal),
                )

        dst_conn.commit()

        # ── 3. Sync embeddings to vector store (Qdrant etc.) ──────────────
        _sync_all_embeddings_to_vector_store(ds_id)

        print(f"[metadata_sync] Done — "
              f"tables: {len(tables)} ({len(tables_to_insert)} new, {len(tables_to_update)} updated, {len(tables_to_delete)} deleted), "
              f"columns: {len(cols_to_insert)} new, {len(cols_to_update)} updated, {len(cols_to_delete)} deleted.")
    except Exception as exc:
        dst_conn.rollback()
        print(f"[metadata_sync] ERROR: {exc}")
        raise
    finally:
        src_conn.close()
        dst_conn.close()


# ---------------------------------------------------------------------------
# Sync logic — Elasticsearch (_mapping API)
# ---------------------------------------------------------------------------

def _sync_es_metadata(ds_id: int, ds_config: dict) -> None:
    """Full sync for Elasticsearch datasource — syncs all indices."""
    es = _build_es_client(ds_config)
    dst_conn = _get_doris_conn(METADATA_DB_DATABASE)

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        indices = _fetch_es_indices(es)

        # ── 1. Sync table info ──────────────────────────────────────────
        existing_tables = {}
        with dst_conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, is_active "
                "FROM adh_table_info WHERE datasource_id = %s",
                (ds_id,),
            )
            for row in cur.fetchall():
                existing_tables[row["table_name"]] = row

        fresh_table_names = set()
        tables_to_insert = []
        tables_to_update = []

        for tbl in indices:
            table_name = tbl["TABLE_NAME"]
            fresh_table_names.add(table_name)
            table_comment = tbl.get("TABLE_COMMENT", "") or ""
            region_tag = extract_region_tag(table_name)
            domain_tag = extract_domain_tag(table_name)

            old = existing_tables.get(table_name)
            if old is None:
                tables_to_insert.append({
                    "table_name": table_name,
                    "table_comment": table_comment,
                    "region_tag": region_tag,
                    "domain_tag": domain_tag,
                })
            else:
                changed = (
                    (old.get("table_comment") or "") != table_comment
                    or (old.get("region_tag") or "") != region_tag
                    or (old.get("domain_tag") or "") != domain_tag
                )
                if changed:
                    tables_to_update.append({
                        "id": old["id"],
                        "table_name": table_name,
                        "table_comment": table_comment,
                        "table_business_desc": old.get("table_business_desc") or "",
                        "keywords": old.get("keywords") or "",
                        "region_tag": region_tag,
                        "domain_tag": domain_tag,
                        "is_active": old.get("is_active", 1),
                    })

        tables_to_delete = set(existing_tables.keys()) - fresh_table_names

        with dst_conn.cursor() as cur:
            for tname in tables_to_delete:
                cur.execute("DELETE FROM adh_table_info WHERE table_name = %s AND datasource_id = %s", (tname, ds_id))

            for r in tables_to_update:
                embed_text = _table_embed_text(r['table_name'], r['table_comment'], r.get('keywords') or "", "", r['domain_tag'])
                vec_literal = _make_embedding(embed_text)
                cur.execute("DELETE FROM adh_table_info WHERE id = %s", (r["id"],))
                cur.execute(
                    "INSERT INTO adh_table_info "
                    "(id, datasource_id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r["id"], ds_id, r["table_name"], r["table_comment"], r["table_business_desc"],
                     r.get("keywords") or "", r["region_tag"], r["domain_tag"], r["is_active"], now, vec_literal),
                )

            for idx, r in enumerate(tables_to_insert):
                row_id = int(_time.time() * 1000000) + idx
                embed_text = _table_embed_text(r['table_name'], r['table_comment'], "", "", r['domain_tag'])
                vec_literal = _make_embedding(embed_text)
                cur.execute(
                    "INSERT INTO adh_table_info "
                    "(id, datasource_id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)",
                    (row_id, ds_id, r["table_name"], r["table_comment"], "", "",
                     r["region_tag"], r["domain_tag"], now, vec_literal),
                )

        # ── 2. Sync column metadata ─────────────────────────────────────
        existing_cols = {}
        with dst_conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, column_name, data_type, column_comment, business_desc, keywords, "
                "is_key, is_nullable, is_active "
                "FROM adh_column_metadata WHERE datasource_id = %s",
                (ds_id,),
            )
            for row in cur.fetchall():
                key = (row["table_name"], row["column_name"])
                existing_cols[key] = row

        fresh_col_keys = set()
        cols_to_insert = []
        cols_to_update = []

        for tbl in indices:
            table_name = tbl["TABLE_NAME"]
            columns = _fetch_es_fields(es, table_name)
            for col in columns:
                col_name = col["COLUMN_NAME"]
                key = (table_name, col_name)
                fresh_col_keys.add(key)

                is_key = col.get("COLUMN_KEY", "false")
                col_comment = col.get("COLUMN_COMMENT", "") or ""
                data_type = col["DATA_TYPE"]
                is_nullable = col.get("IS_NULLABLE", "true")

                old = existing_cols.get(key)
                if old is None:
                    cols_to_insert.append({
                        "table_name": table_name,
                        "column_name": col_name,
                        "data_type": data_type,
                        "column_comment": col_comment,
                        "is_key": is_key,
                        "is_nullable": is_nullable,
                    })
                else:
                    changed = (
                        (old.get("data_type") or "") != data_type
                        or (old.get("column_comment") or "") != col_comment
                    )
                    if changed:
                        cols_to_update.append({
                            "id": old["id"],
                            "table_name": table_name,
                            "column_name": col_name,
                            "data_type": data_type,
                            "column_comment": col_comment,
                            "business_desc": old.get("business_desc") or "",
                            "keywords": old.get("keywords") or "",
                            "is_key": is_key,
                            "is_nullable": is_nullable,
                            "is_active": old.get("is_active", 1),
                        })

        cols_to_delete = set(existing_cols.keys()) - fresh_col_keys

        with dst_conn.cursor() as cur:
            for (tn, cn) in cols_to_delete:
                cur.execute(
                    "DELETE FROM adh_column_metadata WHERE table_name = %s AND column_name = %s AND datasource_id = %s",
                    (tn, cn, ds_id),
                )

            for r in cols_to_update:
                embed_text = _col_embed_text(r['table_name'], r['column_name'], "", r['column_comment'], r.get('keywords') or "")
                vec_literal = _make_embedding(embed_text)
                cur.execute("DELETE FROM adh_column_metadata WHERE id = %s", (r["id"],))
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r["id"], ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], r["business_desc"], r.get("keywords") or "",
                     r["is_key"], r["is_nullable"], r["is_active"], now, vec_literal),
                )

            for idx, r in enumerate(cols_to_insert):
                row_id = int(_time.time() * 1000000) + idx + 500000
                embed_text = _col_embed_text(r['table_name'], r['column_name'], "", r['column_comment'], "")
                vec_literal = _make_embedding(embed_text)
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)",
                    (row_id, ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], "", "", r["is_key"], r["is_nullable"],
                     now, vec_literal),
                )

        dst_conn.commit()

        # Sync embeddings to vector store (Qdrant etc.)
        _sync_all_embeddings_to_vector_store(ds_id)

        print(f"[metadata_sync:es] Done — "
              f"tables: {len(indices)} ({len(tables_to_insert)} new, {len(tables_to_update)} updated, {len(tables_to_delete)} deleted), "
              f"columns: {len(cols_to_insert)} new, {len(cols_to_update)} updated, {len(cols_to_delete)} deleted.")
    except Exception as exc:
        dst_conn.rollback()
        print(f"[metadata_sync:es] ERROR: {exc}")
        raise
    finally:
        es.close()
        dst_conn.close()


def _sync_es_table_columns(ds_id: int, ds_config: dict, table_name: str) -> dict:
    """Sync column metadata for a single ES index."""
    es = _build_es_client(ds_config)
    dst_conn = _get_doris_conn(METADATA_DB_DATABASE)

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        columns = _fetch_es_fields(es, table_name)
        if not columns:
            raise ValueError(f"索引 '{table_name}' 在 Elasticsearch 中不存在或无字段")

        existing_cols = {}
        with dst_conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, column_name, data_type, column_comment, business_desc, "
                "is_key, is_nullable, is_active "
                "FROM adh_column_metadata WHERE datasource_id = %s AND table_name = %s",
                (ds_id, table_name),
            )
            for row in cur.fetchall():
                existing_cols[row["column_name"]] = row

        fresh_col_names = set()
        cols_to_insert = []
        cols_to_update = []

        for col in columns:
            col_name = col["COLUMN_NAME"]
            fresh_col_names.add(col_name)

            is_key = col.get("COLUMN_KEY", "false")
            col_comment = col.get("COLUMN_COMMENT", "") or ""
            data_type = col["DATA_TYPE"]
            is_nullable = col.get("IS_NULLABLE", "true")

            old = existing_cols.get(col_name)
            if old is None:
                cols_to_insert.append({
                    "table_name": table_name,
                    "column_name": col_name,
                    "data_type": data_type,
                    "column_comment": col_comment,
                    "is_key": is_key,
                    "is_nullable": is_nullable,
                })
            else:
                changed = (
                    (old.get("data_type") or "") != data_type
                    or (old.get("column_comment") or "") != col_comment
                )
                if changed:
                    cols_to_update.append({
                        "id": old["id"],
                        "table_name": table_name,
                        "column_name": col_name,
                        "data_type": data_type,
                        "column_comment": col_comment,
                        "business_desc": old.get("business_desc") or "",
                        "is_key": is_key,
                        "is_nullable": is_nullable,
                        "is_active": old.get("is_active", 1),
                    })

        cols_to_delete = set(existing_cols.keys()) - fresh_col_names

        with dst_conn.cursor() as cur:
            for cn in cols_to_delete:
                cur.execute(
                    "DELETE FROM adh_column_metadata WHERE table_name = %s AND column_name = %s AND datasource_id = %s",
                    (table_name, cn, ds_id),
                )

            for r in cols_to_update:
                embed_text = _col_embed_text(r['table_name'], r['column_name'], "", r['column_comment'], r.get('keywords') or "")
                vec_literal = _make_embedding(embed_text)
                cur.execute("DELETE FROM adh_column_metadata WHERE id = %s", (r["id"],))
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r["id"], ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], r["business_desc"], r.get("keywords") or "",
                     r["is_key"], r["is_nullable"], r["is_active"], now, vec_literal),
                )

            for idx, r in enumerate(cols_to_insert):
                row_id = int(_time.time() * 1000000) + idx + 500000
                embed_text = _col_embed_text(r['table_name'], r['column_name'], "", r['column_comment'], "")
                vec_literal = _make_embedding(embed_text)
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)",
                    (row_id, ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], "", "", r["is_key"], r["is_nullable"],
                     now, vec_literal),
                )

        dst_conn.commit()
        return {
            "table_name": table_name,
            "total_columns": len(columns),
            "inserted": len(cols_to_insert),
            "updated": len(cols_to_update),
            "deleted": len(cols_to_delete),
        }
    except Exception as exc:
        dst_conn.rollback()
        raise
    finally:
        es.close()
        dst_conn.close()


# ---------------------------------------------------------------------------
# Public API — auto-dispatch by db_type
# ---------------------------------------------------------------------------

def _make_embedding(text: str) -> str:
    vec = generate_embedding(text)
    return embedding_to_sql_literal(vec)


def _sync_embedding_to_vector_store(table: str, row_id: int, data: dict, embedding_text: str):
    """Write embedding to the vector store (Qdrant etc.) when not using default mode."""
    from backend.common.config import VECTOR_DB_TYPE
    if VECTOR_DB_TYPE == "default":
        return  # MemoryVectorStore auto-loads from METADATA_DB
    try:
        from backend.common.vector import get_vector_store
        store = get_vector_store()
        vec = generate_embedding(embedding_text)
        payload = {**data, "id": row_id, "embedding": vec}
        store.upsert(table=table, id_column="id", id_value=row_id, data=payload)
    except Exception as e:
        print(f"[metadata_sync] WARNING: Failed to sync embedding to vector store for {table} id={row_id}: {e}")


def _sync_all_embeddings_to_vector_store(ds_id: int):
    """Sync all embeddings for a datasource to the vector store (Qdrant etc.).

    Called after metadata sync completes. Reads from METADATA_DB and writes
    to the configured vector store. Skipped for 'default' mode since
    MemoryVectorStore auto-loads from METADATA_DB.
    """
    from backend.common.config import VECTOR_DB_TYPE
    if VECTOR_DB_TYPE == "default":
        return

    try:
        from backend.common.vector import get_vector_store
        store = get_vector_store()

        with get_metadata_connection() as conn:
            with conn.cursor() as cur:
                # Sync table_info embeddings
                cur.execute(
                    "SELECT id, datasource_id, table_name, table_comment, table_business_desc, "
                    "keywords, region_tag, domain_tag, is_active, embedding "
                    "FROM adh_table_info WHERE datasource_id = ?", (ds_id,)
                )
                table_rows = cur.fetchall()

                count = 0
                for row in table_rows:
                    emb = row.get("embedding")
                    if not emb:
                        continue
                    if isinstance(emb, str):
                        import json as _json
                        try:
                            emb = _json.loads(emb)
                        except Exception:
                            continue
                    payload = {k: v for k, v in row.items() if k not in ("id", "embedding")}
                    payload["embedding"] = emb
                    store.upsert(table="adh_table_info", id_column="id", id_value=row["id"], data=payload)
                    count += 1
                print(f"[metadata_sync] Synced {count} table_info embeddings to {VECTOR_DB_TYPE}")

                # Sync column_metadata embeddings
                cur.execute(
                    "SELECT id, datasource_id, table_name, column_name, data_type, "
                    "column_comment, business_desc, is_key, is_nullable, is_active, embedding "
                    "FROM adh_column_metadata WHERE datasource_id = ?", (ds_id,)
                )
                col_rows = cur.fetchall()

                count = 0
                for row in col_rows:
                    emb = row.get("embedding")
                    if not emb:
                        continue
                    if isinstance(emb, str):
                        import json as _json
                        try:
                            emb = _json.loads(emb)
                        except Exception:
                            continue
                    payload = {k: v for k, v in row.items() if k not in ("id", "embedding")}
                    payload["embedding"] = emb
                    store.upsert(table="adh_column_metadata", id_column="id", id_value=row["id"], data=payload)
                    count += 1
                print(f"[metadata_sync] Synced {count} column_metadata embeddings to {VECTOR_DB_TYPE}")

    except Exception as e:
        print(f"[metadata_sync] WARNING: Failed to sync embeddings to vector store: {e}")


def _table_embed_text(table_name: str, table_comment: str = "", keywords: str = "",
                      region_tag: str = "", domain_tag: str = "") -> str:
    """Concise embedding text: name + comment + keywords. Business desc NOT included."""
    parts = [table_name, table_comment or "", keywords or ""]
    return " ".join(p for p in parts if p).strip()


def _col_embed_text(table_name: str, column_name: str, data_type: str = "",
                    column_comment: str = "", keywords: str = "") -> str:
    """Concise embedding text for column: name + col + type + comment + keywords."""
    parts = [table_name, column_name, data_type or "", column_comment or "", keywords or ""]
    return " ".join(p for p in parts if p).strip()


def sync_metadata(ds_id: int = 0) -> None:
    """Incremental sync: sync table info and column metadata.

    Dispatches to MySQL/Doris or Elasticsearch handler based on datasource type.
    """
    if not ds_id:
        raise ValueError("datasource_id is required")

    ds_config = _get_datasource_config(ds_id)
    db_type = ds_config["db_type"]

    if db_type == "elasticsearch":
        _sync_es_metadata(ds_id, ds_config)
    elif db_type in ("mysql", "doris"):
        _sync_mysql_metadata(ds_id, ds_config)
    else:
        raise ValueError(f"暂不支持从 {db_type} 类型数据源同步元数据")


def sync_table_columns(ds_id: int, table_name: str) -> dict:
    """Sync column metadata for a single table/index.

    Dispatches to MySQL/Doris or Elasticsearch handler based on datasource type.
    Returns a summary dict with counts of inserted/updated/deleted columns.
    """
    if not ds_id:
        raise ValueError("datasource_id is required")
    if not table_name:
        raise ValueError("table_name is required")

    ds_config = _get_datasource_config(ds_id)
    db_type = ds_config["db_type"]

    if db_type == "elasticsearch":
        return _sync_es_table_columns(ds_id, ds_config, table_name)
    elif db_type in ("mysql", "doris"):
        return _sync_mysql_table_columns(ds_id, ds_config, table_name)
    else:
        raise ValueError(f"暂不支持从 {db_type} 类型数据源同步元数据")


# ---------------------------------------------------------------------------
# Sync logic — Table Relations (Foreign Keys)
# ---------------------------------------------------------------------------

def _fetch_foreign_keys(conn, schema: str) -> list:
    """Fetch foreign key relationships from MySQL information_schema.

    Returns list of dicts with keys: source_table, source_column,
    target_table (referenced table), target_column (referenced column).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME AS source_table, COLUMN_NAME AS source_column, "
            "REFERENCED_TABLE_NAME AS target_table, REFERENCED_COLUMN_NAME AS target_column "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION",
            (schema,),
        )
        return cur.fetchall()


def sync_table_relations(ds_id: int) -> dict:
    """Sync table relations (foreign keys) from MySQL/Doris datasource.

    Reads foreign keys from information_schema.KEY_COLUMN_USAGE,
    writes to adh_table_relations with embeddings.
    Returns summary dict with inserted/updated/deleted counts.
    """
    if not ds_id:
        raise ValueError("datasource_id is required")

    ds_config = _get_datasource_config(ds_id)
    db_type = ds_config["db_type"]

    if db_type not in ("mysql", "doris"):
        raise ValueError(f"表关联关系自动同步暂仅支持 MySQL/Doris 数据源，当前类型: {db_type}")

    src_conn = pymysql.connect(
        host=ds_config["host"], port=ds_config["port"],
        user=ds_config["user"], password=ds_config["password"],
        database="information_schema",
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    dst_conn = _get_doris_conn(METADATA_DB_DATABASE)
    target_schema = ds_config["database"]

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Fetch foreign keys from source
        foreign_keys = _fetch_foreign_keys(src_conn, target_schema)

        # 2. Get existing relations
        existing = {}
        with dst_conn.cursor() as cur:
            cur.execute(
                "SELECT id, source_table, source_column, target_table, target_column, "
                "relation_type, join_type, description, is_active "
                "FROM adh_table_relations WHERE datasource_id = %s",
                (ds_id,),
            )
            for row in cur.fetchall():
                key = (row["source_table"], row["source_column"],
                       row["target_table"], row["target_column"])
                existing[key] = row

        fresh_keys = set()
        to_insert = []
        to_update = []

        for fk in foreign_keys:
            key = (fk["source_table"], fk["source_column"],
                   fk["target_table"], fk["target_column"])
            fresh_keys.add(key)

            old = existing.get(key)
            if old is None:
                to_insert.append(fk)
            # If already exists, no update needed (FK metadata is static)

        to_delete_keys = set(existing.keys()) - fresh_keys

        with dst_conn.cursor() as cur:
            for key in to_delete_keys:
                cur.execute(
                    "DELETE FROM adh_table_relations "
                    "WHERE source_table = %s AND source_column = %s "
                    "AND target_table = %s AND target_column = %s AND datasource_id = %s",
                    (*key, ds_id),
                )

            for idx, fk in enumerate(to_insert):
                row_id = int(_time.time() * 1000000) + idx
                embed_text = (
                    f"{fk['source_table']}.{fk['source_column']} → "
                    f"{fk['target_table']}.{fk['target_column']} 1:N"
                )
                vec_literal = _make_embedding(embed_text)
                description = f"{fk['source_table']}.{fk['source_column']} 关联 {fk['target_table']}.{fk['target_column']}"
                cur.execute(
                    "INSERT INTO adh_table_relations "
                    "(id, datasource_id, source_table, source_column, target_table, target_column, "
                    "relation_type, join_type, description, is_active, created_at, updated_at, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s)",
                    (row_id, ds_id, fk["source_table"], fk["source_column"],
                     fk["target_table"], fk["target_column"], "1:N", "INNER",
                     description, now, now, vec_literal),
                )

        dst_conn.commit()
        result = {
            "inserted": len(to_insert),
            "updated": len(to_update),
            "deleted": len(to_delete_keys),
        }
        print(f"[metadata_sync:relations] Done — "
              f"foreign_keys: {len(foreign_keys)}, "
              f"inserted: {result['inserted']}, updated: {result['updated']}, deleted: {result['deleted']}")
        return result
    except Exception as exc:
        dst_conn.rollback()
        print(f"[metadata_sync:relations] ERROR: {exc}")
        raise
    finally:
        src_conn.close()
        dst_conn.close()


def _sync_mysql_table_columns(ds_id: int, ds_config: dict, table_name: str) -> dict:
    """Sync column metadata for a single MySQL/Doris table."""
    src_conn = pymysql.connect(
        host=ds_config["host"], port=ds_config["port"],
        user=ds_config["user"], password=ds_config["password"],
        database="information_schema",
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    dst_conn = _get_doris_conn(METADATA_DB_DATABASE)
    target_schema = ds_config["database"]

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        columns = _fetch_columns(src_conn, table_name, target_schema)
        if not columns:
            raise ValueError(f"表 '{table_name}' 在数据源中不存在或无字段")

        existing_cols = {}
        with dst_conn.cursor() as cur:
            cur.execute(
                "SELECT id, table_name, column_name, data_type, column_comment, business_desc, "
                "is_key, is_nullable, is_active "
                "FROM adh_column_metadata WHERE datasource_id = %s AND table_name = %s",
                (ds_id, table_name),
            )
            for row in cur.fetchall():
                existing_cols[row["column_name"]] = row

        fresh_col_names = set()
        cols_to_insert = []
        cols_to_update = []

        for col in columns:
            col_name = col["COLUMN_NAME"]
            fresh_col_names.add(col_name)

            is_key = "true" if col["COLUMN_KEY"] == "PRI" else "false"
            col_comment = col.get("COLUMN_COMMENT", "") or ""
            data_type = col["DATA_TYPE"]
            is_nullable = col["IS_NULLABLE"]

            old = existing_cols.get(col_name)
            if old is None:
                cols_to_insert.append({
                    "table_name": table_name,
                    "column_name": col_name,
                    "data_type": data_type,
                    "column_comment": col_comment,
                    "is_key": is_key,
                    "is_nullable": is_nullable,
                })
            else:
                changed = (
                    (old.get("data_type") or "") != data_type
                    or (old.get("column_comment") or "") != col_comment
                    or (old.get("is_key") or "") != is_key
                    or (old.get("is_nullable") or "") != is_nullable
                )
                if changed:
                    cols_to_update.append({
                        "id": old["id"],
                        "table_name": table_name,
                        "column_name": col_name,
                        "data_type": data_type,
                        "column_comment": col_comment,
                        "business_desc": old.get("business_desc") or "",
                        "is_key": is_key,
                        "is_nullable": is_nullable,
                        "is_active": old.get("is_active", 1),
                    })

        cols_to_delete = set(existing_cols.keys()) - fresh_col_names

        with dst_conn.cursor() as cur:
            for cn in cols_to_delete:
                cur.execute(
                    "DELETE FROM adh_column_metadata WHERE table_name = %s AND column_name = %s AND datasource_id = %s",
                    (table_name, cn, ds_id),
                )

            for r in cols_to_update:
                embed_text = _col_embed_text(r['table_name'], r['column_name'], "", r['column_comment'], r.get('keywords') or "")
                vec_literal = _make_embedding(embed_text)
                cur.execute("DELETE FROM adh_column_metadata WHERE id = %s", (r["id"],))
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r["id"], ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], r["business_desc"], r.get("keywords") or "",
                     r["is_key"], r["is_nullable"], r["is_active"], now, vec_literal),
                )

            for idx, r in enumerate(cols_to_insert):
                row_id = int(_time.time() * 1000000) + idx + 500000
                embed_text = _col_embed_text(r['table_name'], r['column_name'], "", r['column_comment'], "")
                vec_literal = _make_embedding(embed_text)
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)",
                    (row_id, ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], "", "", r["is_key"], r["is_nullable"],
                     now, vec_literal),
                )

        dst_conn.commit()
        return {
            "table_name": table_name,
            "total_columns": len(columns),
            "inserted": len(cols_to_insert),
            "updated": len(cols_to_update),
            "deleted": len(cols_to_delete),
        }
    except Exception as exc:
        dst_conn.rollback()
        raise
    finally:
        src_conn.close()
        dst_conn.close()


if __name__ == "__main__":
    sync_metadata()
