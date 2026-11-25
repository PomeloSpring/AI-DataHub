"""目标写入闸门（write_gate）：同表串行 + Doris 源级档位（data-sync-engineering §4-4）。

锁定四件事：
1. 同一张目标表不能两路同时写（表级锁），抢不到且超预算 → TARGET_BUSY（显式拒跑，
   绝不「没拿到锁也照写」）；
2. Doris 源级档位满 → TARGET_BUSY，且提示处置入口（mate_max_concurrency）；
3. 非 Doris 目标只取表级锁，不占源级档位（MySQL/PG 写入轻，不该被 Doris 档位卡住）；
4. 闸门设施本身不可用（元数据库不支持 GET_LOCK）→ 拒跑，不在无保护下写目标。
"""
import pytest

from backend.modules.flow.dag import write_gate as wg


class _FakeServer:
    """模拟 MySQL 命名锁：name -> 持有该锁的会话 id 集合。"""

    def __init__(self, supports_lock=True):
        self.supports_lock = supports_lock
        self.holders: dict[str, set[int]] = {}
        self._next_id = 1

    def new_conn(self):
        conn = _FakeConn(self, self._next_id)
        self._next_id += 1
        return conn

    def try_lock(self, conn_id: int, name: str) -> int:
        current = self.holders.setdefault(name, set())
        if current and conn_id not in current:
            return 0
        current.add(conn_id)
        return 1

    def release(self, conn_id: int, name: str) -> int:
        current = self.holders.get(name) or set()
        if conn_id not in current:
            return 0
        current.discard(conn_id)
        if not current:
            self.holders.pop(name, None)
        return 1


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn._pending = (sql, params)

    def fetchone(self):
        sql, params = self._conn._pending
        if sql.startswith("SELECT GET_LOCK"):
            if not self._conn.server.supports_lock:
                raise RuntimeError("FUNCTION GET_LOCK does not exist")
            return (self._conn.server.try_lock(self._conn.conn_id, params[0]),)
        if sql.startswith("SELECT RELEASE_LOCK"):
            return (self._conn.server.release(self._conn.conn_id, params[0]),)
        raise AssertionError(f"未预期的 SQL: {sql}")


class _FakeConn:
    def __init__(self, server, conn_id):
        self.server = server
        self.conn_id = conn_id
        self.closed = False
        self._pending = None

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        if self.closed:
            return
        self.closed = True
        # 会话断开 → 该会话持有的所有命名锁自动释放（MySQL 语义）
        for name in list(self.server.holders):
            self.server.release(self.conn_id, name)


def _factory(server):
    return server.new_conn


def test_same_table_write_is_serialized_and_times_out():
    server = _FakeServer()
    first = wg.acquire_target_write(datasource_name="doris_main", family="mysql",
                                    table="dwh.ods_order_daily", wait_sec=5,
                                    conn_factory=_factory(server))
    with pytest.raises(wg.WriteBusy) as err:
        wg.acquire_target_write(datasource_name="doris_main", family="mysql",
                                table="dwh.ods_order_daily", wait_sec=0.2,
                                conn_factory=_factory(server))
    assert "同一张表" in str(err.value)
    assert wg.WriteBusy.error_code == "TARGET_BUSY"

    first.release()
    # 释放后同表可再写
    again = wg.acquire_target_write(datasource_name="doris_main", family="mysql",
                                    table="dwh.ods_order_daily", wait_sec=1,
                                    conn_factory=_factory(server))
    again.release()


def test_doris_slots_limit_concurrent_writes():
    server = _FakeServer()
    a = wg.acquire_target_write(datasource_name="doris_main", family="doris",
                                table="dws.a", wait_sec=1, slots=1,
                                conn_factory=_factory(server))
    # 不同表，但同一 Doris 源档位已满 → 拒（这正是「3 域 × 5 站同时开写」的收敛点）
    with pytest.raises(wg.WriteBusy) as err:
        wg.acquire_target_write(datasource_name="doris_main", family="doris",
                                table="dws.b", wait_sec=0.2, slots=1,
                                conn_factory=_factory(server))
    assert "并发上限 1 档" in str(err.value)
    assert "mate_max_concurrency" in str(err.value)

    a.release()
    b = wg.acquire_target_write(datasource_name="doris_main", family="doris",
                                table="dws.b", wait_sec=1, slots=1,
                                conn_factory=_factory(server))
    b.release()


def test_second_slot_allows_parallel_write_on_doris():
    server = _FakeServer()
    a = wg.acquire_target_write(datasource_name="doris_main", family="doris",
                                table="dws.a", wait_sec=1, slots=2,
                                conn_factory=_factory(server))
    b = wg.acquire_target_write(datasource_name="doris_main", family="doris",
                                table="dws.b", wait_sec=1, slots=2,
                                conn_factory=_factory(server))
    assert a.scope["slots"] == 2 and b.scope["slots"] == 2
    a.release()
    b.release()


def test_non_doris_target_ignores_source_slots():
    """MySQL/PG 目标不占源级档位：同源多表并行写不该被 Doris 档位卡住。"""
    server = _FakeServer()
    a = wg.acquire_target_write(datasource_name="mysql_main", family="mysql",
                                table="db.a", wait_sec=1, conn_factory=_factory(server))
    b = wg.acquire_target_write(datasource_name="mysql_main", family="mysql",
                                table="db.b", wait_sec=1, conn_factory=_factory(server))
    assert "slots" not in a.scope and "slots" not in b.scope
    a.release()
    b.release()


def test_release_is_idempotent():
    server = _FakeServer()
    guard = wg.acquire_target_write(datasource_name="doris_main", family="doris",
                                    table="dws.a", wait_sec=1, slots=2,
                                    conn_factory=_factory(server))
    guard.release()
    guard.release()  # 二次释放不得抛错，也不得误放别人的锁
    assert server.holders == {}
    assert guard._conn.closed is True


def test_lock_gate_unavailable_refuses_to_write():
    """元数据库不支持 GET_LOCK 时拒跑，不静默放行（否则闸门等于不存在）。"""
    server = _FakeServer(supports_lock=False)
    with pytest.raises(wg.WriteGateUnavailable) as err:
        wg.acquire_target_write(datasource_name="doris_main", family="doris",
                                table="dws.a", wait_sec=1, slots=2,
                                conn_factory=_factory(server))
    assert "不支持 GET_LOCK" in str(err.value)
    assert wg.WriteGateUnavailable.error_code == "TARGET_WRITE_GATE_UNAVAILABLE"


@pytest.mark.parametrize("value,expect", [(None, 2), ("", 2), (1, 1), ("3", 3), (8, 8)])
def test_normalize_slots(value, expect):
    assert wg.normalize_slots(value) == expect


@pytest.mark.parametrize("value", [0, -1, 9, "abc", 1.5])
def test_normalize_slots_rejects_illegal(value):
    with pytest.raises(ValueError):
        wg.normalize_slots(value)