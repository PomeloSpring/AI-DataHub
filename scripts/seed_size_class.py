#!/usr/bin/env python3
"""回填表成本分级 (Phase 0 — semantic layer seed)

依据源库 information_schema 的行数/存储量,给 adh_table_info 打:
    est_rows / size_class (small|large|huge) / query_mode / allow_full_scan

分类规则:
    - 能取到统计信息: 按行+字节阈值分级
        small: rows <= 100_000 且 bytes <= 100MB
        large: rows <= 5_000_000 且 bytes <= 2GB
        huge : 其余
    - 取不到统计信息(源不可达/表已删/未注册数据源):
        mysql / postgresql (RDS 原始源) -> 保守判定 huge 且 allow_full_scan=0
        其他 (doris 等分析库)          -> large
    - query_mode: doris -> materialized; 其余 -> raw_source
    - allow_full_scan: huge 一律 0, 其余 1

使用方式(先 dry-run 看效果, 再实写):
    PYTHONPATH=. python scripts/seed_size_class.py --dry-run
    PYTHONPATH=. python scripts/seed_size_class.py
    PYTHONPATH=. python scripts/seed_size_class.py --datasource-id 1780478236183
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 阈值 (行 / 字节)
SMALL_ROWS = 100_000
SMALL_BYTES = 100 * 1024 * 1024          # 100 MB
LARGE_ROWS = 5_000_000
LARGE_BYTES = 2 * 1024 * 1024 * 1024     # 2 GB

RAW_SOURCE_DB_TYPES = {"mysql", "postgresql", "postgres", "sqlserver", "oracle"}


def _classify(rows: int, data_bytes: int) -> str:
    if rows is not None and data_bytes is not None:
        if rows <= SMALL_ROWS and data_bytes <= SMALL_BYTES:
            return "small"
        if rows <= LARGE_ROWS and data_bytes <= LARGE_BYTES:
            return "large"
        return "huge"
    if rows is not None:  # 只有行数 (ES 等)
        if rows <= SMALL_ROWS:
            return "small"
        if rows <= LARGE_ROWS:
            return "large"
        return "huge"
    return ""


def _default_class(db_type: str) -> tuple[str, int]:
    """无统计信息时的保守默认 (size_class, allow_full_scan)."""
    if (db_type or "").lower() in RAW_SOURCE_DB_TYPES:
        return "huge", 0
    return "large", 1


def _query_source_stats(ds_config: dict) -> dict[str, dict]:
    """连源库 information_schema 拉 TABLE_ROWS / (DATA_LENGTH+INDEX_LENGTH)。

    返回 {table_name: {"rows": int|None, "bytes": int|None}}; 失败返回 {}。
    """
    import pymysql

    db_type = (ds_config.get("db_type") or "mysql").lower()
    schema = ds_config.get("database") or ""
    if db_type not in ("mysql", "doris") or not schema:
        return {}
    try:
        conn = pymysql.connect(
            host=ds_config["host"], port=int(ds_config["port"]),
            user=ds_config["user"], password=ds_config["password"],
            database="information_schema", charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=10,
        )
    except Exception as e:
        print(f"  [warn] datasource {ds_config.get('id')} unreachable: {e}")
        return {}
    stats: dict[str, dict] = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH "
                "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s",
                (schema,),
            )
            for r in cur.fetchall():
                rows = r.get("TABLE_ROWS")
                if rows is not None and rows < 0:
                    rows = None
                data_len = r.get("DATA_LENGTH") or 0
                idx_len = r.get("INDEX_LENGTH") or 0
                stats[r["TABLE_NAME"]] = {
                    "rows": rows, "bytes": int(data_len) + int(idx_len),
                }
    except Exception as e:
        print(f"  [warn] information_schema query failed for {schema}: {e}")
    finally:
        conn.close()
    return stats


def _get_ds_config(meta_conn, ds_id: int) -> dict:
    """从 adh_datasources 读取源库连接配置(密码按需解密)。

    不依赖 sync.metadata_sync(已 DEPRECATED, 且其元数据连接走 DORIS_HOST),
    这里直接用元数据库连接。

    回退:若源 host:port 与元数据库相同(同一实例上的不同 schema),且存储的密码
    无法解密,则直接复用环境变量里的元数据库凭证,以保障 information_schema 可查。
    """
    from services.shared.common.crypto import decrypt_password, is_encrypted
    with meta_conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, db_type, host, port, username, password, database_name, `ssl` "
            "FROM adh_datasources WHERE id = %s",
            (ds_id,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"datasource {ds_id} not found")
    password = row["password"] or ""
    user = row["username"] or ""
    decrypted_ok = True
    if password and is_encrypted(password):
        try:
            password = decrypt_password(password)
        except ValueError as e:
            print(f"  [warn] failed to decrypt password for datasource {ds_id}: {e}")
            decrypted_ok = False
    # 同实例回退:复用元数据库环境变量凭证(通常只有 METADATA_DB_USER@<本沙盒 IP> 可登入)
    if not decrypted_ok:
        meta_host = os.environ.get("METADATA_DB_HOST", "")
        meta_port = str(os.environ.get("METADATA_DB_PORT", ""))
        if str(row["host"]) == meta_host and str(row["port"]) == meta_port:
            print(f"  [info] datasource {ds_id} shares host with metadata DB; "
                  f"falling back to METADATA_DB_* credentials")
            user = os.environ.get("METADATA_DB_USER", user)
            password = os.environ.get("METADATA_DB_PASSWORD", password)
    return {
        "id": row["id"],
        "db_type": (row.get("db_type") or "mysql").lower(),
        "host": row["host"],
        "port": row["port"],
        "user": user,
        "password": password,
        "database": row.get("database_name") or "",
        "ssl": bool(row.get("ssl", 0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="seed size_class / query_mode / allow_full_scan")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写库")
    parser.add_argument("--datasource-id", type=int, default=None, help="仅处理指定数据源")
    args = parser.parse_args()

    from services.shared.common.db.metadata_db import get_metadata_conn

    conn = get_metadata_conn()
    cur = conn.cursor()

    where = "WHERE t.is_active = 1"
    params: list = []
    if args.datasource_id:
        where += " AND t.datasource_id = %s"
        params.append(args.datasource_id)
    cur.execute(
        f"SELECT t.id, t.datasource_id, t.table_name, t.est_rows, t.size_class, "
        f"       d.db_type "
        f"FROM adh_table_info t LEFT JOIN adh_datasources d "
        f"  ON d.id = t.datasource_id {where}",
        params,
    )
    tables = cur.fetchall()
    print(f"[seed_size_class] {len(tables)} active table(s), "
          f"dry_run={args.dry_run}, at {datetime.now():%F %T}")

    # 按数据源分组拉统计, 每个源只连一次
    stats_by_ds: dict[int, dict] = {}
    plan_rows: list[dict] = []
    for t in tables:
        ds_id = t["datasource_id"]
        db_type = t.get("db_type") or ""
        if ds_id not in stats_by_ds:
            stats_by_ds[ds_id] = {}
            try:
                cfg = _get_ds_config(conn, ds_id)
                stats_by_ds[ds_id] = _query_source_stats(cfg)
                if stats_by_ds[ds_id]:
                    print(f"  [ok] datasource {ds_id} ({db_type}): "
                          f"{len(stats_by_ds[ds_id])} table stats from information_schema")
            except Exception as e:
                print(f"  [warn] datasource {ds_id} config unavailable: {e}")
        st = stats_by_ds[ds_id].get(t["table_name"]) or {}
        rows, data_bytes = st.get("rows"), st.get("bytes")
        size_class = _classify(rows, data_bytes)
        allow_full_scan: int | None = None
        if not size_class:
            size_class, allow_full_scan = _default_class(db_type)
        if allow_full_scan is None:
            allow_full_scan = 0 if size_class == "huge" else 1
        query_mode = "materialized" if (db_type or "").lower() == "doris" else "raw_source"
        plan_rows.append({
            "id": t["id"], "table_name": t["table_name"], "datasource_id": ds_id,
            "est_rows": rows, "size_class": size_class,
            "query_mode": query_mode, "allow_full_scan": allow_full_scan,
        })

    # 汇总打印
    dist: dict[str, int] = {}
    for p in plan_rows:
        dist[p["size_class"]] = dist.get(p["size_class"], 0) + 1
    print(f"[seed_size_class] distribution: {dist}")
    preview = sorted(plan_rows, key=lambda x: (x["size_class"], x["table_name"]))
    for p in preview[:10]:
        print(f"  {p['size_class']:<5} rows={str(p['est_rows']):>10} "
              f"full_scan={p['allow_full_scan']} {p['datasource_id']}/{p['table_name']}")
    if len(preview) > 10:
        print(f"  ... {len(preview) - 10} more")

    if args.dry_run:
        print("[seed_size_class] dry-run, nothing written")
        conn.close()
        return 0

    written = 0
    with conn.cursor() as upd:
        for p in plan_rows:
            upd.execute(
                "UPDATE adh_table_info SET est_rows=%s, size_class=%s, "
                "query_mode=%s, allow_full_scan=%s WHERE id=%s",
                (p["est_rows"], p["size_class"], p["query_mode"],
                 p["allow_full_scan"], p["id"]),
            )
            written += 1
    conn.commit()
    conn.close()
    print(f"[seed_size_class] updated {written} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
