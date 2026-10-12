"""
AI-DataHub Metadata Sync  [DEPRECATED]

Incremental sync of table info and column metadata from MySQL/Doris.
- Table info → adh.adh_table_info (table_comment, business_desc, tags)
- Column metadata → adh.adh_column_metadata (column_comment, business_desc)

Preserves user-defined business_desc. Only updates rows where system fields have changed.

Usage:
    python -m backend.modules.catalog.services.metadata_sync
"""

import re
import time as _time
from datetime import datetime

import pymysql

from backend.common.crypto import decrypt_password, is_encrypted
from backend.common.config import (
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

def _apply_sync(ds_id: int, dst_conn, tables: list, fetch_columns) -> None:
    """通用元数据回写（MySQL/Doris 与 SLS 共用）：diff adh_table_info / adh_column_metadata。

    tables: [{TABLE_NAME, TABLE_COMMENT}]；fetch_columns(table_name) -> [{COLUMN_NAME, DATA_TYPE,
    COLUMN_COMMENT, COLUMN_KEY, IS_NULLABLE}]。src_conn/dst_conn 生命周期由调用方
    （_sync_mysql_metadata / _sync_sls_metadata）负责，本函数只管回写 + commit/rollback。
    更新分支只用系统字段判变更、保留人工 business_desc 与派生 region_tag/domain_tag
    （同步不得覆盖人工标注——见 test_domain_tag_derivation）。
    """
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
                cur.execute("DELETE FROM adh_table_info WHERE id = %s", (r["id"],))
                cur.execute(
                    "INSERT INTO adh_table_info "
                    "(id, datasource_id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, is_active, sync_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r["id"], ds_id, r["table_name"], r["table_comment"], r["table_business_desc"],
                     r.get("keywords") or "", r["region_tag"], r["domain_tag"], r["is_active"], now),
                )

            for r in tables_to_insert:
                row_id = int(_time.time() * 1000000) + tables_to_insert.index(r)
                cur.execute(
                    "INSERT INTO adh_table_info "
                    "(id, datasource_id, table_name, table_comment, table_business_desc, keywords, region_tag, domain_tag, is_active, sync_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s)",
                    (row_id, ds_id, r["table_name"], r["table_comment"], "", "",
                     r["region_tag"], r["domain_tag"], now),
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
            columns = fetch_columns(table_name)
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
                cur.execute("DELETE FROM adh_column_metadata WHERE id = %s", (r["id"],))
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r["id"], ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], r["business_desc"], r.get("keywords") or "",
                     r["is_key"], r["is_nullable"], r["is_active"], now),
                )

            for r in cols_to_insert:
                row_id = int(_time.time() * 1000000) + cols_to_insert.index(r) + 500000
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)",
                    (row_id, ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], "", "", r["is_key"], r["is_nullable"],
                     now),
                )

        dst_conn.commit()
        print(f"[metadata_sync] Done — "
              f"tables: {len(tables)} ({len(tables_to_insert)} new, {len(tables_to_update)} updated, {len(tables_to_delete)} deleted), "
              f"columns: {len(cols_to_insert)} new, {len(cols_to_update)} updated, {len(cols_to_delete)} deleted.")
    except Exception as exc:
        dst_conn.rollback()
        print(f"[metadata_sync] ERROR: {exc}")
        raise


def _sync_mysql_metadata(ds_id: int, ds_config: dict) -> None:
    """Full sync for MySQL/Doris datasources（information_schema 探查 + 通用回写）。"""
    src_conn = pymysql.connect(
        host=ds_config["host"], port=ds_config["port"],
        user=ds_config["user"], password=ds_config["password"],
        database="information_schema",
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    dst_conn = _get_doris_conn(METADATA_DB_DATABASE)
    target_schema = ds_config["database"]
    try:
        tables = _fetch_tables(src_conn, target_schema)
        _apply_sync(ds_id, dst_conn, tables,
                    lambda t: _fetch_columns(src_conn, t, target_schema))
    finally:
        src_conn.close()
        dst_conn.close()


# ---------------------------------------------------------------------------
# Sync logic — SLS（阿里云日志服务，PG 协议接入）
# ---------------------------------------------------------------------------
# SLS 的 PG 端点支持 information_schema（官方文档"查询元数据"），但 LogStore 映射在
# 同名 project 的 schema 下，且不填 table_catalog / 不支持 ::regclass/obj_description/
# ordinal_position。故 SLS 专用 fetcher 只用文档保证的字段，输出与 MySQL 同构的
# {TABLE_NAME/TABLE_COMMENT} / {COLUMN_NAME/DATA_TYPE/COLUMN_COMMENT/COLUMN_KEY/IS_NULLABLE}，
# 以复用 _apply_sync（含 tag/business_desc 保留）。SLS 不支持主键 → COLUMN_KEY 恒 ''。

def _fetch_tables_sls(conn, project_schema: str) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name AS \"TABLE_NAME\", '' AS \"TABLE_COMMENT\" "
            "FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name",
            (project_schema,),
        )
        return [dict(r) for r in (cur.fetchall() or [])]


def _fetch_columns_sls(conn, table_name: str, project_schema: str = None) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name AS \"COLUMN_NAME\", data_type AS \"DATA_TYPE\", "
            "'' AS \"COLUMN_COMMENT\", '' AS \"COLUMN_KEY\", 'YES' AS \"IS_NULLABLE\" "
            "FROM information_schema.columns WHERE table_name = %s ORDER BY column_name",
            (table_name,),
        )
        return [dict(r) for r in (cur.fetchall() or [])]


def _open_sls_conn(ds_config: dict):
    """SLS 走 psycopg2（simple 协议，客户端参数插值），复用 datasource_db PG 工厂。"""
    from backend.common.db.datasource_db import get_datasource_conn

    return get_datasource_conn(
        "sls", ds_config["host"], ds_config["port"],
        ds_config["user"], ds_config["password"], ds_config["database"],
        ssl=bool(ds_config.get("ssl", False)),
    )


def _sync_sls_metadata(ds_id: int, ds_config: dict) -> None:
    """Full sync for SLS（PG 协议）datasources：LogStore→表、字段→列元数据。"""
    src_conn = _open_sls_conn(ds_config)
    dst_conn = _get_doris_conn(METADATA_DB_DATABASE)
    project_schema = ds_config["database"]
    try:
        tables = _fetch_tables_sls(src_conn, project_schema)
        _apply_sync(ds_id, dst_conn, tables,
                    lambda t: _fetch_columns_sls(src_conn, t, project_schema))
    finally:
        src_conn.close()
        dst_conn.close()


def sync_metadata(ds_id: int = 0) -> None:
    """Incremental sync: sync table info and column metadata.

    Dispatches by datasource type: mysql/doris → information_schema(pymysql);
    sls → PG 协议 information_schema(psycopg2)。其余类型 fail-loud 拒绝。
    """
    if not ds_id:
        raise ValueError("datasource_id is required")

    ds_config = _get_datasource_config(ds_id)
    db_type = ds_config["db_type"]

    if db_type in ("mysql", "doris"):
        _sync_mysql_metadata(ds_id, ds_config)
    elif db_type == "sls":
        _sync_sls_metadata(ds_id, ds_config)
    else:
        raise ValueError(f"暂不支持从 {db_type} 类型数据源同步元数据")


def sync_table_columns(ds_id: int, table_name: str) -> dict:
    """Sync column metadata for a single table.

    Dispatches by datasource type (mysql/doris 与 sls)。Returns a summary dict
    with counts of inserted/updated/deleted columns.
    """
    if not ds_id:
        raise ValueError("datasource_id is required")
    if not table_name:
        raise ValueError("table_name is required")

    ds_config = _get_datasource_config(ds_id)
    db_type = ds_config["db_type"]

    if db_type in ("mysql", "doris"):
        return _sync_mysql_table_columns(ds_id, ds_config, table_name)
    elif db_type == "sls":
        return _sync_sls_table_columns(ds_id, ds_config, table_name)
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
                description = f"{fk['source_table']}.{fk['source_column']} 关联 {fk['target_table']}.{fk['target_column']}"
                cur.execute(
                    "INSERT INTO adh_table_relations "
                    "(id, datasource_id, source_table, source_column, target_table, target_column, "
                    "relation_type, join_type, description, is_active, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)",
                    (row_id, ds_id, fk["source_table"], fk["source_column"],
                     fk["target_table"], fk["target_column"], "1:N", "INNER",
                     description, now, now),
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


def _apply_columns_sync(ds_id: int, dst_conn, table_name: str, columns: list) -> dict:
    """单表列元数据通用回写（MySQL/Doris 与 SLS 共用）；返回 summary dict。

    columns: [{COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT, COLUMN_KEY, IS_NULLABLE}]；
    dst_conn 生命周期由调用方负责，本函数只管回写 + commit/rollback（保留人工 business_desc）。
    """
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
                cur.execute("DELETE FROM adh_column_metadata WHERE id = %s", (r["id"],))
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (r["id"], ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], r["business_desc"], r.get("keywords") or "",
                     r["is_key"], r["is_nullable"], r["is_active"], now),
                )

            for idx, r in enumerate(cols_to_insert):
                row_id = int(_time.time() * 1000000) + idx + 500000
                cur.execute(
                    "INSERT INTO adh_column_metadata "
                    "(id, datasource_id, table_name, column_name, data_type, column_comment, "
                    "business_desc, keywords, is_key, is_nullable, is_active, sync_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)",
                    (row_id, ds_id, r["table_name"], r["column_name"], r["data_type"],
                     r["column_comment"], "", "", r["is_key"], r["is_nullable"],
                     now),
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
        columns = _fetch_columns(src_conn, table_name, target_schema)
        return _apply_columns_sync(ds_id, dst_conn, table_name, columns)
    finally:
        src_conn.close()
        dst_conn.close()


def _sync_sls_table_columns(ds_id: int, ds_config: dict, table_name: str) -> dict:
    """Sync column metadata for a single SLS（PG 协议）表/LogStore。"""
    src_conn = _open_sls_conn(ds_config)
    dst_conn = _get_doris_conn(METADATA_DB_DATABASE)
    project_schema = ds_config["database"]
    try:
        columns = _fetch_columns_sls(src_conn, table_name, project_schema)
        return _apply_columns_sync(ds_id, dst_conn, table_name, columns)
    finally:
        src_conn.close()
        dst_conn.close()


if __name__ == "__main__":
    sync_metadata()
