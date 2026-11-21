"""目标端批量写入 — 同步/转换结果落地（数据工程写入通道）。

边界声明（护栏 §7，双向声明）：
- 本通道**不返回数据行、不经 LLM**，仅把 DataFrame 批量写入目标数据源的授权表；
- 连接凭据只在服务端解析使用，绝不回显到 API/前端/Agent；
- 目标表必须已存在（不隐式建表，fail-loud），写入失败显式抛出；
- 写操作落审计日志（行数/耗时/目标表名，不含数据行内容）。
"""

import logging
import time

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1000


def _get_target_conn(datasource_name: str):
    from backend.common.db import get_datasource_by_name, get_datasource_conn
    source = get_datasource_by_name(datasource_name)
    if not source:
        raise ValueError(f"目标数据源 '{datasource_name}' 不存在")
    return source, get_datasource_conn(
        source["db_type"], source["host"], source["port"],
        source["username"], source["password"], source.get("database_name"))


def _table_exists(cur, table: str, db_type: str) -> bool:
    if db_type in ("postgres", "postgresql", "pg"):
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,))
    else:
        cur.execute("SHOW TABLES LIKE %s", (table,))
    return cur.fetchone() is not None


# ════════════════════════════════════════════════════════════════════
# 物化写入：staging + 原子切换 + 目标侧对账
#
# 物化（定时物化）与 append 直写不同：覆盖写必须"要么全旧、要么全新"，
# 不能读侧看到 TRUNCATE 后的空窗或半批数据。做法一律是先把新数据写进
# staging 表，核对 staging 行数 = 读取行数，再用一条引擎原子语句切换：
# - mysql 系：RENAME TABLE（元数据原子切换，读侧无空窗）；
# - doris   ：INSERT OVERWRITE ... SELECT FROM staging（按分区原子覆盖）；
# - postgres：事务内 DROP + RENAME（PG 的 DDL 可事务，提交前对读者不可见）。
# 引擎不支持时**显式拒绝**（不退回 TRUNCATE 再写的非原子捷径）。
# ════════════════════════════════════════════════════════════════════

_MYSQL_FAMILY = {"mysql", "mariadb", "tidb"}
_PG_FAMILY = {"postgres", "postgresql", "pg"}
_DORIS_FAMILY = {"doris", "starrocks", "selectdb"}


def _family(db_type: str) -> str:
    t = (db_type or "").lower()
    if t in _MYSQL_FAMILY:
        return "mysql"
    if t in _PG_FAMILY:
        return "postgres"
    if t in _DORIS_FAMILY:
        return "doris"
    return t or "unknown"


def _q(ident: str, family: str) -> str:
    """标识符引号（目标表/列名，不接受调用方递交的原始 SQL 片段）。"""
    if family == "postgres":
        return '"' + str(ident).replace('"', '""') + '"'
    return "`" + str(ident).replace("`", "``") + "`"


def _temp_name(table: str, suffix: str) -> str:
    """临时表名：同库同名前缀 + 后缀（≤64 字符，超长截断后加短哈希避免碰撞）。"""
    base = str(table)
    if len(base) + len(suffix) <= 64:
        return base + suffix
    import hashlib
    digest = hashlib.md5(base.encode("utf-8")).hexdigest()[:6]
    return base[: 64 - len(suffix) - len(digest) - 1] + "_" + digest + suffix


def _stage_table(table: str) -> str:
    """staging 表名（装载新数据、核对后原子切换）。"""
    return _temp_name(table, "__adh_stage")


def _rows_of(df: pd.DataFrame) -> tuple[list, list]:
    columns = [str(c) for c in df.columns]
    if df is None or df.empty:
        return columns, []
    values = [tuple(None if pd.isna(v) else v for v in row)
              for row in df.itertuples(index=False, name=None)]
    return columns, values


def _count_rows(cur, table: str, family: str, where: str = "", params: tuple = ()) -> int:
    sql = f"SELECT COUNT(*) FROM {_q(table, family)}"
    if where:
        sql += f" WHERE {where}"
    cur.execute(sql, params) if params else cur.execute(sql)
    row = cur.fetchone()
    return int((row[0] if row else 0) or 0)


def _insert_batches(cur, table: str, columns: list[str], values: list[tuple],
                    family: str, batch_size: int) -> int:
    if not values:
        return 0
    col_sql = ", ".join(_q(c, family) for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = (f"INSERT INTO {_q(table, family)} ({col_sql}) "
                  f"VALUES ({placeholders})")
    written = 0
    size = max(1, int(batch_size))
    for offset in range(0, len(values), size):
        chunk = values[offset:offset + size]
        cur.executemany(insert_sql, chunk)
        written += len(chunk)
    return written


def _partition_where(column: str, value: str, family: str) -> tuple[str, tuple]:
    """分区对账谓词：业务日期列按 DATE(col)=值 匹配（mysql/doris 均支持 DATE()）。"""
    if family == "postgres":
        return f"{_q(column, family)}::date = %s::date", (value,)
    return f"DATE({_q(column, family)}) = %s", (value,)


def _create_stage(cur, stage: str, table: str, family: str) -> None:
    cur.execute(f"DROP TABLE IF EXISTS {_q(stage, family)}")
    if family == "postgres":
        cur.execute(f"CREATE TABLE {_q(stage, family)} (LIKE {_q(table, family)} INCLUDING ALL)")
    else:
        cur.execute(f"CREATE TABLE {_q(stage, family)} LIKE {_q(table, family)}")


def _drop_stage(cur, stage: str, family: str) -> None:
    try:
        cur.execute(f"DROP TABLE IF EXISTS {_q(stage, family)}")
    except Exception:  # noqa: BLE001 清理失败不掩盖主结果（主结果已提交）
        logger.warning("[TargetWriter] staging 表清理失败: %s", stage)


def write_dataframe_atomic(df: pd.DataFrame, datasource_name: str, table: str, *,
                           write_mode: str = "overwrite", partition: dict | None = None,
                           batch_size: int = DEFAULT_BATCH_SIZE,
                           context: str = "") -> dict:
    """物化写入（覆盖/分区覆盖/追加），返回 {rows_written, reconciled, ...}。

    - write_mode=append：追加，直接走既有 write_dataframe（无需原子性）；
    - write_mode=overwrite：全表覆盖，staging + 原子切换；
    - write_mode=partition_overwrite：按 partition={column,value,target_partition}
      覆盖单个分区（staging + 原子覆盖/事务内替换）。

    空结果集在覆盖语义下**必须把目标清空**（旧数据不能留），不得跳过写入；
    写入行数必须与目标表核对相等，否则抛错（执行体回报不算成功依据）。
    """
    if write_mode not in ("append", "overwrite", "partition_overwrite"):
        raise ValueError(f"物化写入模式不合法: {write_mode}")
    columns, values = _rows_of(df)

    if write_mode == "append":
        # 追加无需原子切换：直接走既有写入通道（失败即抛，不谎报）
        written = write_dataframe(df, datasource_name, table, "append", batch_size, context)
        return {"rows_written": written, "reconciled": True, "strategy": "append",
                "target_count": None}

    source, conn = _get_target_conn(datasource_name)
    db_type = str(source.get("db_type") or "mysql")
    family = _family(db_type)

    if family not in ("mysql", "doris", "postgres"):
        conn.close()
        raise ValueError(
            f"目标引擎 '{db_type}' 的原子覆盖尚未实现（当前支持 MySQL 系 / Doris / PostgreSQL）。"
            "请改选受支持的目标数据源；平台不会用 TRUNCATE 再写的非原子捷径冒充覆盖写。")

    part_col = ""
    part_value = ""
    if write_mode == "partition_overwrite":
        cfg = partition or {}
        part_col = str(cfg.get("column") or "")
        part_value = str(cfg.get("value") or "")
        if not part_col or not part_value:
            conn.close()
            raise ValueError("分区覆盖必须指定分区列与分区日期值")

    stage = _stage_table(table)
    start = time.time()
    try:
        with conn.cursor() as cur:
            if not _table_exists(cur, table, db_type):
                raise ValueError(
                    f"目标表 '{table}' 在数据源 '{datasource_name}' 中不存在，请先建表")
            if not columns:
                raise ValueError("物化结果没有列，无法写入目标表")

            # ── MySQL 系：分区覆盖用单事务 DELETE+INSERT（InnoDB 提交前对读者不可见）
            if family == "mysql" and write_mode == "partition_overwrite":
                where, params = _partition_where(part_col, part_value, family)
                cur.execute(f"DELETE FROM {_q(table, family)} WHERE {where}", params)
                written = _insert_batches(cur, table, columns, values, family, batch_size)
                conn.commit()
                with conn.cursor() as verify:
                    where2, params2 = _partition_where(part_col, part_value, family)
                    target_count = _count_rows(verify, table, family, where2, params2)
                return _verify_or_raise(written, target_count, "mysql-txn", context,
                                        datasource_name, table, start)

            # ── PG：分区覆盖同样走事务（DELETE+INSERT）
            if family == "postgres" and write_mode == "partition_overwrite":
                where, params = _partition_where(part_col, part_value, family)
                cur.execute(f"DELETE FROM {_q(table, family)} WHERE {where}", params)
                written = _insert_batches(cur, table, columns, values, family, batch_size)
                conn.commit()
                with conn.cursor() as verify:
                    where2, params2 = _partition_where(part_col, part_value, family)
                    target_count = _count_rows(verify, table, family, where2, params2)
                return _verify_or_raise(written, target_count, "postgres-txn", context,
                                        datasource_name, table, start)

            # ── Doris：分区覆盖必须给分区名（INSERT OVERWRITE ... PARTITION）
            if family == "doris" and write_mode == "partition_overwrite":
                target_partition = str((partition or {}).get("target_partition") or "")
                if not target_partition:
                    raise ValueError("Doris 目标的分区覆盖必须提供分区名（target_partition）")

            # ── staging：建表（LIKE 目标表）+ 批量写入 + 核对
            _create_stage(cur, stage, table, family)
            written = _insert_batches(cur, stage, columns, values, family, batch_size)
            stage_count = _count_rows(cur, stage, family)
            if stage_count != written:
                raise ValueError(
                    f"staging 行数核对不一致：写入 {written} 行，staging 实际 {stage_count} 行")

            col_sql = ", ".join(_q(c, family) for c in columns)
            if family == "mysql":
                old = _temp_name(table, "__adh_old")
                cur.execute(f"DROP TABLE IF EXISTS {_q(old, family)}")
                cur.execute(
                    f"RENAME TABLE {_q(table, family)} TO {_q(old, family)}, "
                    f"{_q(stage, family)} TO {_q(table, family)}")
                cur.execute(f"DROP TABLE {_q(old, family)}")
                strategy = "mysql-rename"
            elif family == "postgres":
                cur.execute(f"DROP TABLE {_q(table, family)}")
                cur.execute(f"ALTER TABLE {_q(stage, family)} RENAME TO {_q(table, family)}")
                strategy = "postgres-rename"
            else:  # doris：整表/单分区原子覆盖
                part_sql = ""
                if write_mode == "partition_overwrite":
                    part_sql = f" PARTITION({target_partition})"
                overwrite = (f"INSERT OVERWRITE TABLE {_q(table, family)}{part_sql} "
                             f"({col_sql}) SELECT {col_sql} FROM {_q(stage, family)}")
                if values:
                    cur.execute(overwrite)
                else:
                    # 空结果集：Doris 的 OVERWRITE 需要 SELECT 行；用 DELETE 分区/TRUNCATE 表
                    if write_mode == "partition_overwrite":
                        where, params = _partition_where(part_col, part_value, family)
                        cur.execute(f"DELETE FROM {_q(table, family)} WHERE {where}", params)
                    else:
                        cur.execute(f"TRUNCATE TABLE {_q(table, family)}")
                _drop_stage(cur, stage, family)
                strategy = "doris-overwrite"
            conn.commit()

            with conn.cursor() as verify:
                if write_mode == "partition_overwrite":
                    where2, params2 = _partition_where(part_col, part_value, family)
                    target_count = _count_rows(verify, table, family, where2, params2)
                else:
                    target_count = _count_rows(verify, table, family)
            return _verify_or_raise(written, target_count, strategy, context,
                                    datasource_name, table, start)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("[TargetWriter] %s 物化写入失败 target=%s.%s mode=%s",
                         context, datasource_name, table, write_mode)
        raise
    finally:
        conn.close()


def _verify_or_raise(written: int, target_count: int, strategy: str, context: str,
                     datasource_name: str, table: str, start: float) -> dict:
    elapsed_ms = int((time.time() - start) * 1000)
    if int(target_count) != int(written):
        raise ValueError(
            f"目标侧对账不一致：写入 {written} 行，目标表实际 {target_count} 行"
            "（平台不拿写入回报当成功依据，请检查目标表写入与分区谓词）")
    logger.info(
        "[TargetWriter] %s 物化完成 target=%s.%s strategy=%s rows=%d reconciled=1 (%dms)",
        context, datasource_name, table, strategy, written, elapsed_ms)
    return {"rows_written": written, "reconciled": True, "strategy": strategy,
            "target_count": int(target_count), "elapsed_ms": elapsed_ms}


def write_dataframe(df: pd.DataFrame, datasource_name: str, table: str,
                    write_mode: str = "append", batch_size: int = DEFAULT_BATCH_SIZE,
                    context: str = "") -> int:
    """批量写入 DataFrame 到目标表，返回写入行数。

    write_mode:
    - append: 追加；
    - overwrite: 先 TRUNCATE 再写（全量刷新语义）。
    """
    if write_mode not in ("append", "overwrite"):
        raise ValueError(f"写入模式不合法: {write_mode}")
    if df is None or df.empty:
        logger.info("[TargetWriter] %s 空结果集，跳过写入（mode=%s）", context, write_mode)
        return 0

    columns = [str(c) for c in df.columns]
    col_sql = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO `{table}` ({col_sql}) VALUES ({placeholders})"
    values = [tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False, name=None)]

    source, conn = _get_target_conn(datasource_name)
    start = time.time()
    try:
        with conn.cursor() as cur:
            if not _table_exists(cur, table, source.get("db_type", "mysql")):
                raise ValueError(
                    f"目标表 '{table}' 在数据源 '{datasource_name}' 中不存在，请先建表")
            if write_mode == "overwrite":
                cur.execute(f"TRUNCATE TABLE `{table}`")
            written = 0
            for offset in range(0, len(values), max(1, batch_size)):
                chunk = values[offset:offset + max(1, batch_size)]
                cur.executemany(insert_sql, chunk)
                written += len(chunk)
        conn.commit()
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "[TargetWriter] %s 写入完成 target=%s.%s mode=%s rows=%d (%dms)",
            context, datasource_name, table, write_mode, written, elapsed_ms)
        return written
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("[TargetWriter] %s 写入失败 target=%s.%s mode=%s",
                         context, datasource_name, table, write_mode)
        raise
    finally:
        conn.close()
