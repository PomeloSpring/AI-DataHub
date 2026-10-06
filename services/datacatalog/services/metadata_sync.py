"""
AI-DataHub Metadata Sync  [DEPRECATED]

Incremental sync of table info and column metadata from MySQL/Doris.
- Table info → adh.adh_table_info (table_comment, business_desc, tags)
- Column metadata → adh.adh_column_metadata (column_comment, business_desc)

Preserves user-defined business_desc. Only updates rows where system fields have changed.

Usage:
    python -m services.datacatalog.services.metadata_sync
"""

import re
import time as _time
from datetime import datetime

import pymysql

from services.shared.common.crypto import decrypt_password, is_encrypted
from services.shared.common.config import (
    METADATA_DB_HOST, METADATA_DB_PORT, METADATA_DB_USER,
    METADATA_DB_PASSWORD, METADATA_DB_DATABASE,
)


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
    """表名**前缀**启发式推断业务域（仅用于新建行的初始值）。

    局限：规则只认 dim_/dwd_/ods_/adh_ 前缀，对 `t_*` 这类业务表一条都不命中，
    全部回落 'other'。真正的业务域事实源是 `adh_tag_values` 的人工标注，
    由 `tags_service.derive_table_domain_tags` 派生回写 domain_tag。
    因此本函数的产出**不得覆盖已有行**（见 sync_tables 的变更判定）。
    """
    lower = table_name.lower()
    for pattern, domain in _DOMAIN_PREFIX_RULES:
        if re.match(pattern, lower):
            return domain
    return "other"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_doris_conn(database=None):
    """连接元数据库（存放 adh_table_info / adh_column_metadata 等元数据表）。

    历史上此处读 DORIS_* 环境变量（默认 127.0.0.1:9030）；现统一以
    METADATA_DB_* 为唯一配置源（config.py 已将 DORIS_* 标记为 deprecated
    别名）。否则未配 DORIS_* 的部署会静默回落本地地址连不上，
    同步一行都写不进去且报错难以定位。
    """
    return pymysql.connect(
        host=METADATA_DB_HOST,
        port=METADATA_DB_PORT,
        user=METADATA_DB_USER,
        password=METADATA_DB_PASSWORD,
        database=database or METADATA_DB_DATABASE,
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
                # 只按表注释判变更；region_tag/domain_tag 不参与判定也不覆盖——
                # 它们是派生缓存(事实源 = adh_tag_values 人工标注)。元数据同步的
                # 表名启发式一旦写回就会冲掉人工标注(历史: 249 张表全 'other')。
                changed = (old.get("table_comment") or "") != table_comment
                if changed:
                    tables_to_update.append({
                        "id": old["id"],
                        "table_name": table_name,
                        "table_comment": table_comment,
                        "table_business_desc": old.get("table_business_desc") or "",
                        "keywords": old.get("keywords") or "",
                        # 保留已有值：同步不覆盖派生/人工标注
                        "region_tag": old.get("region_tag") or region_tag,
                        "domain_tag": old.get("domain_tag") or domain_tag,
                        "is_active": old.get("is_active", 1),
                    })

        tables_to_delete = set(existing_tables.keys()) - fresh_table_names

        with dst_conn.cursor() as cur:
            for tname in tables_to_delete:
                cur.execute("DELETE FROM adh_table_info WHERE table_name = %s AND datasource_id = %s", (tname, ds_id))

            for r in tables_to_update:
                vec_literal = _placeholder_embedding()
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
                vec_literal = _placeholder_embedding()
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
                vec_literal = _placeholder_embedding()
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
                vec_literal = _placeholder_embedding()
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
# Public API — auto-dispatch by db_type
# ---------------------------------------------------------------------------

_EMBEDDING_DIM = 768


def _placeholder_embedding() -> str:
    """Zero-vector placeholder for the logically-disabled embedding column.

    The column is retained (Doris ARRAY<FLOAT> NOT NULL) but is no longer
    produced by an embedding model nor read by any retrieval path.
    """
    return "[" + ", ".join(["0.0"] * _EMBEDDING_DIM) + "]"


def sync_metadata(ds_id: int = 0) -> None:
    """Incremental sync: sync table info and column metadata.

    Dispatches to the MySQL/Doris handler based on datasource type.
    """
    if not ds_id:
        raise ValueError("datasource_id is required")

    ds_config = _get_datasource_config(ds_id)
    db_type = ds_config["db_type"]

    if db_type in ("mysql", "doris"):
        _sync_mysql_metadata(ds_id, ds_config)
    else:
        raise ValueError(f"暂不支持从 {db_type} 类型数据源同步元数据")


def sync_table_columns(ds_id: int, table_name: str) -> dict:
    """Sync column metadata for a single table.

    Dispatches to the MySQL/Doris handler based on datasource type.
    Returns a summary dict with counts of inserted/updated/deleted columns.
    """
    if not ds_id:
        raise ValueError("datasource_id is required")
    if not table_name:
        raise ValueError("table_name is required")

    ds_config = _get_datasource_config(ds_id)
    db_type = ds_config["db_type"]

    if db_type in ("mysql", "doris"):
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
                vec_literal = _placeholder_embedding()
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
                vec_literal = _placeholder_embedding()
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
                vec_literal = _placeholder_embedding()
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
