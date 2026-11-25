"""目标端批量写入 — 同步/转换结果落地（数据工程写入通道）。

边界声明（护栏 §7，双向声明）：
- 本通道**不返回数据行、不经 LLM**，仅把 DataFrame 批量写入目标数据源的授权表；
- 连接凭据只在服务端解析使用，绝不回显到 API/前端/Agent；
- 目标表必须已存在（不隐式建表，fail-loud），写入失败显式抛出；
- 写操作落审计日志（行数/耗时/目标表名，不含数据行内容）。
"""

import logging
import re
import time
from datetime import date, timedelta

import pandas as pd

# 写侧闸门（同表串行 + 源级并发档位）：追加/覆盖/分区覆盖三条路径都靠它，
# 漏 import 会让执行期直接 NameError（写入全挂），属于必须在模块层绑定的依赖。
from backend.modules.flow.dag.write_gate import (
    DEFAULT_LOCK_WAIT_SEC,
    acquire_target_write,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1000


def _resolve_target(datasource_name: str) -> dict:
    """目标数据源登记行（写闸门的档位配置也从这里取）。"""
    from backend.common.db import get_datasource_by_name

    source = get_datasource_by_name(datasource_name)
    if not source:
        raise ValueError(f"目标数据源 '{datasource_name}' 不存在")
    return source


def _connect_target(source: dict, timeout_sec: int | None = None):
    """目标库连接。读超时与取数同一口径（显式预算 > 数据源高级配置 > 缺省）。

    写通道不能沿用交互式 30s 天花板：Doris 建 staging / OVERWRITE 大分区是慢
    DDL+大事务，健康但慢的引擎会被客户端读超时判成 2013「连接丢失」，而服务端
    DDL 已落地 —— 表现成「写失败但表被占」，下一次写入再撞 already exists。
    """
    from backend.common.db import get_datasource_conn
    from backend.common.db.datasource_db import resolve_timeouts

    connect_timeout, read_timeout = resolve_timeouts(source, timeout_sec=timeout_sec)
    return get_datasource_conn(
        source["db_type"], source["host"], source["port"],
        source["username"], source["password"], source.get("database_name"),
        read_timeout=read_timeout, connect_timeout=connect_timeout)


def _get_target_conn(datasource_name: str, timeout_sec: int | None = None):
    """目标库连接（登记行 + 连接）。append 等旧通道沿用本入口。"""
    source = _resolve_target(datasource_name)
    return source, _connect_target(source, timeout_sec=timeout_sec)


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


def _stage_token() -> str:
    """每次写入独立的 staging 后缀。

    固定名 `{table}__adh_stage` 让「上一次写入中断遗留的表」和「本次写入」直接
    互踩：客户端 2013 超时并不代表服务端没建表，下一次 DROP+CREATE 在 Doris 上
    会报 `CREATE TABLE LIKE ... already exists`，整轮 day 档 5 个站点连锁失败。
    配合 finally 的尽力清理，6 位随机足以隔离；进程被强杀才会留下垃圾表。
    """
    import uuid
    return uuid.uuid4().hex[:6]


def _stage_table(table: str, token: str = "") -> str:
    """staging 表名（装载新数据、核对后原子切换）。"""
    return _temp_name(table, "__adh_stage" + (f"_{token}" if token else ""))


def _rows_of(df: pd.DataFrame) -> tuple[list, list]:
    columns = [str(c) for c in df.columns]
    if df is None or df.empty:
        return columns, []
    values = [tuple(None if pd.isna(v) else v for v in row)
              for row in df.itertuples(index=False, name=None)]
    return columns, values


def _scalar(row):
    """取单值：兼容普通 cursor（tuple）与 DictCursor（dict）两种返回形态。

    目标数据源（尤其 Doris/PG）常用 DictCursor，fetchone() 返回 dict，
    按 row[0] 取列会抛 KeyError，表现为“写入失败：0”这类误导性报错。
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    return row[0]


def _count_rows(cur, table: str, family: str, where: str = "", params: tuple = ()) -> int:
    sql = f"SELECT COUNT(*) AS c FROM {_q(table, family)}"
    if where:
        sql += f" WHERE {where}"
    cur.execute(sql, params) if params else cur.execute(sql)
    return int(_scalar(cur.fetchone()) or 0)


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


def _fixed_pairs(fixed) -> list[tuple[str, object]]:
    """固定分区列（如 region='cn'）：只接受列名标识符与标量值。

    多站点共用一张 DWS 表时，站点是**分区键的一部分**（Doris 的
    `INSERT OVERWRITE PARTITION` 会替换整个分区，同分区多站必然互相覆盖）。
    值不进 SQL 文本：谓词一律参数化；分区定义用 `_doris_literal` 做白名单渲染。
    """
    if not fixed:
        return []
    if not isinstance(fixed, dict):
        raise ValueError("分区固定列须为 {列名: 值} 映射")
    out: list[tuple[str, object]] = []
    for key, value in fixed.items():
        name = str(key or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"分区固定列名不合法: {key!r}（只允许字母/数字/下划线）")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError(f"分区固定列 '{name}' 的值须为字符串或整数")
        if isinstance(value, str) and len(value) > 64:
            raise ValueError(f"分区固定列 '{name}' 的值过长（≤64）")
        out.append((name, value))
    return out


def _doris_literal(value) -> str:
    """Doris 分区定义里的字面量（分区区间必须写进 DDL 文本，不能参数化）。

    用白名单字符集而不是转义拼串：站点编码/整数编号这类受控值才允许出现，
    任何引号/逗号/括号直接拒绝（不给出注入面）。
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"Doris 分区固定值须为字符串或整数: {value!r}")
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_.:\-]+", text):
        raise ValueError(f"Doris 分区固定值含不安全字符: {value!r}")
    return "'" + text + "'"


def _partition_where(column: str, value: str, family: str,
                     fixed=None) -> tuple[str, tuple]:
    """分区对账谓词：固定分区列等值 + 业务日期列按半开区间 [d, d+1) 匹配。

    为什么不用 `DATE(col)=…` / `CAST(col AS DATE)=…`：把列包进函数后，Doris 不能
    做分区裁剪、MySQL/PG 也用不上索引 —— 每次对账都退化成全表扫描。物化一天要
    对账几百个分区（3 域 × 5 站 × 日/月/年），累计就是持续给引擎加压（历史缺陷：
    日档 5 站连续 2013 超时）。裸列半开区间与“日期相等”语义一致，但能走分区裁剪。

    `fixed` 非空时（多站同表）把站点/版本等分区键一起纳入，否则同表其它站的行
    会被算进本次对账（覆盖写会误判成功）。
    """
    qcol = _q(column, family)
    try:
        lo_date = date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError(f"分区日期不合法: {value!r}（须为 YYYY-MM-DD）") from exc
    lo = lo_date.isoformat()
    hi = (lo_date + timedelta(days=1)).isoformat()
    conds: list[str] = []
    params: list[object] = []
    for name, fixed_value in _fixed_pairs(fixed):
        conds.append(f"{_q(name, family)} = %s")
        params.append(fixed_value)
    if family == "postgres":
        conds.append(f"{qcol} >= %s::date AND {qcol} < %s::date")
    else:
        conds.append(f"{qcol} >= %s AND {qcol} < %s")
    params.extend([lo, hi])
    return " AND ".join(conds), tuple(params)


def _assert_fixed_values(columns: list[str], values: list[tuple], fixed=None) -> None:
    """写前校验：结果里的固定分区列必须全等于配置值（防跨站串写）。

    数据集写错站点（例如把 prd-cn 的正文挂到 en 节点）时，行会落到别人的分区：
    先按配置覆盖、再被下一站覆盖，读侧看到的是“某站数据消失”且无人报错。
    这里在写之前 fail-loud，代价是一次全列扫描（行数受 max_rows 预算约束）。
    """
    pairs = _fixed_pairs(fixed)
    if not pairs:
        return
    index = {str(c): i for i, c in enumerate(columns)}
    for name, want in pairs:
        pos = index.get(name)
        if pos is None:
            raise ValueError(
                f"物化结果缺少固定分区列 '{name}'（partition.fixed 要求该列与配置值一致）")
        for row in values:
            got = row[pos]
            if str(got) != str(want):
                raise ValueError(
                    f"物化结果的分区列 '{name}' 与配置值不一致（配置 {want!r}，实际 {got!r}），"
                    "拒绝写入：否则会覆盖其它站点的分区")


def _ensure_doris_partition(cur, table: str, partition_name: str, value: str,
                            key_columns: list[str] | None = None, fixed=None) -> None:
    """分区覆盖前按需建分区（Doris 的 INSERT OVERWRITE PARTITION 要求分区已存在）。

    物化回填历史时（日档 365 天 / 月档 12 个月 / 年档 2 年）分区不可能预建；
    动态分区又会按 start 自动淘汰历史，所以统一走显式 ADD PARTITION IF NOT EXISTS：
    幂等可重跑，且不会被自动删除。分区名与日期都做严格校验（不拼 SQL 片段）。

    多站同表时表的分区键是复合键（如 `(region, stat_date)`）：`key_columns` 给出
    分区键列（顺序须与 DDL 一致），固定值从 `fixed` 取；区间定义写成
    `VALUES [(1,'2026-01-01'),(1,'2026-01-02'))`。不收字符串拼接的原始 SQL 片段。
    """
    name = str(partition_name or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(
            f"Doris 分区名不合法: {partition_name!r}（只允许字母/数字/下划线）")
    try:
        lo_date = date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError(f"分区日期不合法: {value!r}（须为 YYYY-MM-DD）") from exc
    lo = lo_date.isoformat()
    hi = (lo_date + timedelta(days=1)).isoformat()
    keys = [str(k) for k in (key_columns or [])]
    if keys:
        pairs = dict(_fixed_pairs(fixed))
        missing = [k for k in keys if k not in pairs]
        if missing:
            raise ValueError(
                f"分区键列缺少固定值: {missing}（partition.fixed 必须给出这些列的值）")
        lo_values = ", ".join([_doris_literal(pairs[k]) for k in keys] + [f"'{lo}'"])
        hi_values = ", ".join([_doris_literal(pairs[k]) for k in keys] + [f"'{hi}'"])
        cur.execute(
            f"ALTER TABLE {_q(table, 'doris')} ADD PARTITION IF NOT EXISTS {name} "
            f"VALUES [({lo_values}),({hi_values}))")
        return
    cur.execute(
        f"ALTER TABLE {_q(table, 'doris')} ADD PARTITION IF NOT EXISTS {name} "
        f"VALUES [('{lo}'),('{hi}'))")


# Doris 影子表可作排序键的标量类型（key 列必须是列定义的前缀）。
_DORIS_KEY_TYPES = {
    "BOOLEAN", "TINYINT", "SMALLINT", "INT", "INTEGER", "BIGINT", "LARGEINT",
    "DATE", "DATEV2", "DATETIME", "DATETIMEV2", "CHAR", "VARCHAR", "STRING",
    "TEXT", "DECIMAL",
}
# 聚合中间态/嵌套类型不能当普通列搬进影子表（文本写入不回去，且会改变列语义）。
_DORIS_UNSUPPORTED_TYPES = {
    "BITMAP", "HLL", "QUANTILE_STATE", "AGG_STATE", "ARRAY", "MAP", "STRUCT",
}
_DORIS_TYPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\(\s*\d+(?:\s*,\s*\d+)?\s*\))?")


def _is_nullable(flag) -> bool:
    return str(flag or "").strip().lower() in ("yes", "true", "1", "y")


def _desc_column(row) -> tuple[str, str, bool]:
    """DESC 一行 → (列名, 类型, 是否可为空)；兼容 tuple 与 DictCursor。"""
    if isinstance(row, dict):
        name = row.get("Field") or row.get("field")
        typ = row.get("Type") or row.get("type")
        nullable = row.get("Null") if "Null" in row else (row.get("null") or row.get("IS_NULLABLE"))
    else:
        name, typ = row[0], row[1]
        nullable = row[2] if len(row) >= 3 else "YES"
    return str(name or "").strip(), str(typ or "").strip(), _is_nullable(nullable)


def _doris_shadow_ddl(cur, stage: str, table: str) -> str:
    """Doris staging 影子表建表语句（显式列定义，**不复制分区**）。

    历史缺陷：`CREATE TABLE stage LIKE target` 会把目标表的 Column Definition /
    Partitions / Table Properties 一起复制（Doris/StarRocks 文档口径）。旧表按
    (region_id, stat_date) 日/月/年回看累积了几百个分区，而物化是“每分区一次
    staging 建表+删表”——单日 700+ 次 CREATE 就是反复搬运几百个分区与 tablet，
    FE/BE 直接被拖垮（实测：日档 5 站连续 2013 超时、FE 最终拒连）。

    影子表只需“能装下同样列的一张小表”：按 DESC 显式生成列定义、单桶、单副本、
    不带任何分区；写完再用 `INSERT OVERWRITE ... PARTITION(p)` 装入目标分区。
    首列必须是可作 key 的非空标量列（Doris 要求 key 列为 schema 前缀），否则拒跑
    —— 不退回 LIKE（那等于把分区复制风暴又请回来）。
    """
    cur.execute(f"DESC {_q(table, 'doris')}")
    rows = cur.fetchall() or []
    if not rows:
        raise ValueError(f"无法读取目标表 '{table}' 的列定义（DESC 为空），拒绝写入")
    columns = [_desc_column(row) for row in rows]
    for name, typ, _ in columns:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"目标表 '{table}' 列名不合法: {name!r}")
        if not _DORIS_TYPE_RE.fullmatch(typ):
            raise ValueError(f"目标表 '{table}' 列 '{name}' 类型不合法: {typ!r}")
        if typ.split("(")[0].strip().upper() in _DORIS_UNSUPPORTED_TYPES:
            raise ValueError(
                f"目标表 '{table}' 含聚合/嵌套类型列 '{name}'（{typ}）：无法建立文本写入的"
                "影子表，请改用不含量化/嵌套列的明细或聚合表")

    head, head_type, head_nullable = columns[0]
    if head_type.split("(")[0].strip().upper() not in _DORIS_KEY_TYPES or head_nullable:
        raise ValueError(
            f"目标表 '{table}' 首列 '{head}'（{head_type}{' NULL' if head_nullable else ''}）"
            "不能作为 Doris 影子表排序键：staging 不再用 CREATE TABLE LIKE（会复制全部分区），"
            "请保留一个 NOT NULL 的 INT/VARCHAR/DATE/DECIMAL 首列")

    cols_sql = ", ".join(
        f"{_q(name, 'doris')} {typ} {'NULL' if nullable else 'NOT NULL'}"
        for name, typ, nullable in columns)
    return (f"CREATE TABLE {_q(stage, 'doris')} ({cols_sql}) ENGINE=OLAP "
            f"DUPLICATE KEY({_q(head, 'doris')}) DISTRIBUTED BY RANDOM BUCKETS 1 "
            f"PROPERTIES (\"replication_num\" = \"1\")")


def _create_stage(cur, stage: str, table: str, family: str) -> None:
    cur.execute(f"DROP TABLE IF EXISTS {_q(stage, family)}")
    if family == "postgres":
        cur.execute(f"CREATE TABLE {_q(stage, family)} (LIKE {_q(table, family)} INCLUDING ALL)")
    elif family == "doris":
        cur.execute(_doris_shadow_ddl(cur, stage, table))
    else:
        cur.execute(f"CREATE TABLE {_q(stage, family)} LIKE {_q(table, family)}")


def _drop_stage(cur, stage: str, family: str) -> None:
    try:
        cur.execute(f"DROP TABLE IF EXISTS {_q(stage, family)}")
    except Exception:  # noqa: BLE001 清理失败不掩盖主结果（主结果已提交）
        logger.warning("[TargetWriter] staging 表清理失败: %s", stage)


def _lock_wait_sec(timeout_sec) -> float:
    """排队预算：不超过本次写入的剩余预算，也不超过闸门缺省（10 分钟）。"""
    if timeout_sec is None or "" == timeout_sec:
        return DEFAULT_LOCK_WAIT_SEC
    return max(1.0, min(float(timeout_sec), float(DEFAULT_LOCK_WAIT_SEC)))


def _write_slots(source: dict):
    """Doris 源级写入档位（`adh_datasources.extra_config.mate_max_concurrency`）。"""
    from backend.common.db.datasource_db import source_extra

    return source_extra(source).get("mate_max_concurrency")


def write_dataframe_atomic(df: pd.DataFrame, datasource_name: str, table: str, *,
                           write_mode: str = "overwrite", partition: dict | None = None,
                           batch_size: int = DEFAULT_BATCH_SIZE,
                           context: str = "",
                           timeout_sec: int | None = None) -> dict:
    """物化写入（覆盖/分区覆盖/追加），返回 {rows_written, reconciled, ...}。

    - write_mode=append：追加，直接走既有 write_dataframe（无需原子性）；
    - write_mode=overwrite：全表覆盖，staging + 原子切换；
    - write_mode=partition_overwrite：按 partition={column,value,target_partition}
      覆盖单个分区（staging + 原子覆盖/事务内替换）。

    空结果集在覆盖语义下**必须把目标清空**（旧数据不能留），不得跳过写入；
    写入行数必须与目标表核对相等，否则抛错（执行体回报不算成功依据）。

    `timeout_sec`：本次写入的读超时预算（批处理调用方下递剩余预算）；
    不传则按数据源高级配置 → 缺省 30s，与旧行为逐字一致。

    写侧闸门（write_gate）：同表串行 + Doris 源级并发档位。物化一天要写几百个
    分区，多域/多站同时开写会把目标引擎打挂（实测 2013 超时→FE 拒连）；排队
    超预算直接报 TARGET_BUSY，不在无并发保护下开写。
    """
    if write_mode not in ("append", "overwrite", "partition_overwrite"):
        raise ValueError(f"物化写入模式不合法: {write_mode}")
    columns, values = _rows_of(df)

    source = _resolve_target(datasource_name)
    db_type = str(source.get("db_type") or "mysql")
    family = _family(db_type)

    if write_mode == "append":
        # 追加无需原子切换：直接走既有写入通道（失败即抛，不谎报），但同样受写闸门约束
        guard = acquire_target_write(
            datasource_name=datasource_name, family=family, table=table,
            wait_sec=_lock_wait_sec(timeout_sec), slots=_write_slots(source))
        try:
            written = _write_plain(df, source, datasource_name, table, "append",
                                   batch_size, context)
        finally:
            guard.release()
        return {"rows_written": written, "reconciled": True, "strategy": "append",
                "target_count": None}

    if family not in ("mysql", "doris", "postgres"):
        raise ValueError(
            f"目标引擎 '{db_type}' 的原子覆盖尚未实现（当前支持 MySQL 系 / Doris / PostgreSQL）。"
            "请改选受支持的目标数据源；平台不会用 TRUNCATE 再写的非原子捷径冒充覆盖写。")

    part_col = ""
    part_value = ""
    fixed = None
    key_columns: list[str] = []
    if write_mode == "partition_overwrite":
        cfg = partition or {}
        part_col = str(cfg.get("column") or "")
        part_value = str(cfg.get("value") or "")
        fixed = cfg.get("fixed") or None
        key_columns = [str(k) for k in (cfg.get("key_columns") or [])]
        if not part_col or not part_value:
            raise ValueError("分区覆盖必须指定分区列与分区日期值")
        # 固定分区列（站点/版本）必须在结果里且值一致：否则会静默覆盖别的分区
        _assert_fixed_values(columns, values, fixed)

    stage = _stage_table(table, _stage_token())
    # 先拿写闸门再连目标库：锁等待不占目标连接，也不会在排队期间开着空闲写入连接
    guard = acquire_target_write(
        datasource_name=datasource_name, family=family, table=table,
        wait_sec=_lock_wait_sec(timeout_sec), slots=_write_slots(source))
    try:
        conn = _connect_target(source, timeout_sec=timeout_sec)
    except Exception:
        guard.release()
        raise
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
                where, params = _partition_where(part_col, part_value, family, fixed)
                cur.execute(f"DELETE FROM {_q(table, family)} WHERE {where}", params)
                written = _insert_batches(cur, table, columns, values, family, batch_size)
                conn.commit()
                with conn.cursor() as verify:
                    where2, params2 = _partition_where(part_col, part_value, family, fixed)
                    target_count = _count_rows(verify, table, family, where2, params2)
                return _verify_or_raise(written, target_count, "mysql-txn", context,
                                        datasource_name, table, start)

            # ── PG：分区覆盖同样走事务（DELETE+INSERT）
            if family == "postgres" and write_mode == "partition_overwrite":
                where, params = _partition_where(part_col, part_value, family, fixed)
                cur.execute(f"DELETE FROM {_q(table, family)} WHERE {where}", params)
                written = _insert_batches(cur, table, columns, values, family, batch_size)
                conn.commit()
                with conn.cursor() as verify:
                    where2, params2 = _partition_where(part_col, part_value, family, fixed)
                    target_count = _count_rows(verify, table, family, where2, params2)
                return _verify_or_raise(written, target_count, "postgres-txn", context,
                                        datasource_name, table, start)

            # ── Doris：分区覆盖必须给分区名（INSERT OVERWRITE ... PARTITION）
            if family == "doris" and write_mode == "partition_overwrite":
                target_partition = str((partition or {}).get("target_partition") or "")
                if not target_partition:
                    raise ValueError("Doris 目标的分区覆盖必须提供分区名（target_partition）")
                # 回填历史月份/年份时分区不存在：先幂等建分区，否则 OVERWRITE 直接失败
                _ensure_doris_partition(cur, table, target_partition, part_value,
                                        key_columns, fixed)

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
                        where, params = _partition_where(part_col, part_value, family, fixed)
                        cur.execute(f"DELETE FROM {_q(table, family)} WHERE {where}", params)
                    else:
                        cur.execute(f"TRUNCATE TABLE {_q(table, family)}")
                _drop_stage(cur, stage, family)
                strategy = "doris-overwrite"
            conn.commit()

            with conn.cursor() as verify:
                if write_mode == "partition_overwrite":
                    where2, params2 = _partition_where(part_col, part_value, family, fixed)
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
        # 失败/中断路径也要尽力清掉 staging：残留表会让下一次写入撞
        # 「already exists」（day 档整轮连锁失败就是这个），但它不得掩盖主错误
        # （主结果已提交或已抛出）。连接已超时/断开时清理会失败，仅告警。
        try:
            with conn.cursor() as cleanup:
                _drop_stage(cleanup, stage, family)
        except Exception:  # noqa: BLE001 清理失败不掩盖主结果
            logger.warning("[TargetWriter] staging 清理未执行（连接可能已断开）: %s", stage)
        conn.close()
        guard.release()


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

    写入闸门（write_gate）在这里也生效：sync/sql_task 节点直走本入口，同样不能绕开
    同表串行 + Doris 源级档位（“多域同时开写打挂目标引擎”不是物化专用风险）。
    原子写路径（`write_dataframe_atomic`）已持锁，内部调 `_write_plain`，不重复取锁
    ——同一会话重复 GET_LOCK 是重入计数，但跨会话必死锁，所以分开两条入口。
    """
    if write_mode not in ("append", "overwrite"):
        raise ValueError(f"写入模式不合法: {write_mode}")
    if df is None or df.empty:
        logger.info("[TargetWriter] %s 空结果集，跳过写入（mode=%s）", context, write_mode)
        return 0

    source = _resolve_target(datasource_name)
    family = _family(str(source.get("db_type") or "mysql"))
    guard = acquire_target_write(
        datasource_name=datasource_name, family=family, table=table,
        slots=_write_slots(source))
    try:
        return _write_plain(df, source, datasource_name, table, write_mode,
                            batch_size, context)
    finally:
        guard.release()


def _write_plain(df: pd.DataFrame, source: dict, datasource_name: str, table: str,
                 write_mode: str, batch_size: int, context: str) -> int:
    """实际写入（调用方已持写闸门）。"""
    columns = [str(c) for c in df.columns]
    col_sql = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO `{table}` ({col_sql}) VALUES ({placeholders})"
    values = [tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False, name=None)]

    conn = _connect_target(source)
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
