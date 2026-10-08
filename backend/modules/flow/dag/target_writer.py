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
