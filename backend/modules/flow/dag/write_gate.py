"""目标侧写入闸门 —— 让物化写入「排队可退让」，不把目标引擎打挂。

两类风暴（实测：日档 5 站点连续 `2013 Lost connection`，最终 Doris FE 整机拒连）：
1. **同表并发写**：staging DDL 与 `INSERT OVERWRITE` 同时打同一张目标表，Doris
   元数据版本互撞、BE 写入队列堆积；
2. **同集群并发写**：3 个业务域 DAG × 5 站 × 3 档，celery 4 个 worker 能同时开多路
   写入 —— 取数侧有 `query_gate` 按源限流，**写侧原本没有任何上界**。

做法 = 元数据库命名锁（MySQL `GET_LOCK`，会话级：进程被强杀/连接断开即自动释放，
不留死锁；同一会话可持有多把锁，MySQL 5.7+/MariaDB 10.0.2+ 均支持）：
- **表级锁**：同一张目标表同一时刻只有一个写入者（staging/OVERWRITE 不互撞）；
- **Doris 源级档位**：默认 2 档（`adh_datasources.extra_config.mate_max_concurrency`，1~8），
  MySQL/PG 目标只取表级锁（写入开销小，不需要源级档位）。

排队超预算一律 fail-loud（`WriteBusy` / `error_code=TARGET_BUSY`）：不静默跳过、
不无限期挂在锁上，也**不「没拿到锁也照写」**——那等于闸门不存在。
闸门设施本身不可用（元数据库不支持 `GET_LOCK`）同样拒跑，不允许在无并发保护下
打目标引擎（no-silent-degradation §1）。
"""

from __future__ import annotations

import hashlib
import logging
import time

logger = logging.getLogger(__name__)

DEFAULT_DORIS_WRITE_SLOTS = 2
MAX_WRITE_SLOTS = 8
DEFAULT_LOCK_WAIT_SEC = 600
POLL_INTERVAL_SEC = 1.0
DORIS_FAMILY = "doris"


class WriteBusy(RuntimeError):
    """目标表/集群写入排队超出预算（可诊断，不静默跳过）。"""

    error_code = "TARGET_BUSY"

    def __init__(self, message: str):
        super().__init__(message)


class WriteGateUnavailable(RuntimeError):
    """写入闸门不可用（元数据库不支持命名锁）：拒跑，不在无保护下写目标。"""

    error_code = "TARGET_WRITE_GATE_UNAVAILABLE"

    def __init__(self, message: str):
        super().__init__(message)


def normalize_slots(value) -> int:
    """Doris 写入并发档位：未配置取缺省，配错/越界 fail-loud（不静默吃成默认值）。

    显式拒绝 bool/小数：`int(1.5)` 会静默截成 1（把配置吃成另一个值），
    与 `int_or_default` 对 0/负数的口径同源。
    """
    if value is None or value == "":
        return DEFAULT_DORIS_WRITE_SLOTS
    if isinstance(value, bool):
        raise ValueError(
            f"物化写入并发档位须为 1~{MAX_WRITE_SLOTS} 的整数（收到 {value!r}）")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(
            f"物化写入并发档位须为整数（收到 {value!r}，不接受小数截断）")
    try:
        slots = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"物化写入并发档位须为 1~{MAX_WRITE_SLOTS} 的整数（收到 {value!r}）") from exc
    if not 1 <= slots <= MAX_WRITE_SLOTS:
        raise ValueError(
            f"物化写入并发档位须在 1~{MAX_WRITE_SLOTS} 之间（收到 {slots}）")
    return slots


def _lock_name(kind: str, key: str, slot: int | None = None) -> str:
    """锁名（≤64 字符）：kind + 键的短哈希（不把库名/表名原样塞进锁名）。"""
    digest = hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:16]
    suffix = "" if slot is None else f"_s{slot}"
    return f"adh_mate_{kind}_{digest}{suffix}"


def _row_scalar(row):
    """兼容 tuple / DictCursor(dict) 两种 fetchone 形态。"""
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    return row[0]


def _try_lock(conn, name: str) -> bool:
    """非阻塞取锁（wait=0）。元数据库不支持命名锁时上抛，不静默放行。"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) AS l", (name,))
            return int(_row_scalar(cur.fetchone()) or 0) == 1
    except Exception as exc:  # noqa: BLE001 设施不可用 = 拒跑（不能假装有闸门）
        raise WriteGateUnavailable(
            "物化写入闸门不可用（元数据库不支持 GET_LOCK 命名锁）；"
            "为避免多路写入把目标引擎打挂，本次写入已拒绝") from exc


def _release_lock(conn, name: str) -> None:
    """尽力释放：失败只告警，不掩盖主结果（连接断开时锁随会话自动失效）。"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s) AS r", (name,))
            cur.fetchone()
    except Exception:  # noqa: BLE001
        logger.warning("[WriteGate] 释放写入锁失败（连接可能已断开）: %s", name)


def _sleep_until(deadline: float) -> None:
    time.sleep(min(POLL_INTERVAL_SEC, max(0.05, deadline - time.monotonic())))


def _wait_lock(conn, name: str, deadline: float) -> bool:
    while True:
        if _try_lock(conn, name):
            return True
        if time.monotonic() >= deadline:
            return False
        _sleep_until(deadline)


def _wait_slot(conn, datasource_name: str, slots: int, deadline: float,
               table: str) -> str:
    """占用该数据源的一个写入档位；满员排队，超预算 fail-loud。"""
    while True:
        for slot in range(slots):
            name = _lock_name("ds", datasource_name, slot)
            if _try_lock(conn, name):
                return name
        if time.monotonic() >= deadline:
            raise WriteBusy(
                f"目标数据源 '{datasource_name}' 的物化写入已达并发上限 {slots} 档"
                f"（{table} 排队超时）。平台不会在超出档位时继续开写；"
                "请错开调度，或调「数据源 → 高级配置 → mate_max_concurrency」（1~8）")
        _sleep_until(deadline)


def _open_lock_conn():
    """闸门专用元数据连接（会话级锁必须挂在一条活连接上）。"""
    from backend.common.db.metadata_db import get_metadata_conn

    return get_metadata_conn()


class WriteGuard:
    """已持有的写入锁；`release()` 幂等（异常路径可重复调用）。"""

    def __init__(self, conn, names: list[str], scope: dict):
        self._conn = conn
        self._names = list(names)
        self._released = False
        self.scope = dict(scope)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        for name in reversed(self._names):
            _release_lock(self._conn, name)
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            logger.warning("[WriteGate] 闸门连接关闭失败（锁已随会话释放）")

    def __enter__(self) -> "WriteGuard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.release()
        return False


def acquire_target_write(*, datasource_name: str, family: str, table: str,
                         wait_sec: float = DEFAULT_LOCK_WAIT_SEC,
                         slots=None, conn_factory=None) -> WriteGuard:
    """取得目标表写入权（失败即抛，绝不"没锁也写"）。

    - 所有引擎：表级锁（同表串行）；
    - Doris 系：额外占用数据源级档位（默认 2 档），把"多域同时开写"收敛成有上界的排队。
    """
    wait = max(0.0, float(wait_sec if wait_sec is not None else DEFAULT_LOCK_WAIT_SEC))
    deadline = time.monotonic() + wait
    conn = (conn_factory or _open_lock_conn)()
    names: list[str] = []
    scope = {"datasource": datasource_name, "table": table, "family": family,
             "wait_sec": wait}
    try:
        if family == DORIS_FAMILY:
            limit = normalize_slots(slots)
            slot_name = _wait_slot(conn, datasource_name, limit, deadline, table)
            names.append(slot_name)
            scope["slots"] = limit
        table_lock = _lock_name("tbl", f"{datasource_name}|{table}")
        if not _wait_lock(conn, table_lock, deadline):
            raise WriteBusy(
                f"写目标表 {datasource_name}.{table} 排队超时：同一张表有另一批物化正在写。"
                "平台已拒跑本次写入（不会在无并发保护下开写）；请稍后重试或错开调度")
        names.append(table_lock)
        return WriteGuard(conn, names, scope)
    except BaseException:
        for name in reversed(names):
            _release_lock(conn, name)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        raise