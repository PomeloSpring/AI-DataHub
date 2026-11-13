"""数据源取数并发闸门（`backend/core/query_gate.py`）的行为契约。

锁死的八件事，缺一件就等于这个功能没做：
1. 上限内直接放行，超上限**排队**而不是立刻拒；名额按 FIFO 交接，绝不越上限；
2. 排队超过预算才拒，且文案可操作（上限/位次/已等时长/下一步），不含 host/端口/账号；
3. 同线程嵌套取数不重复占名额（否则 limit=1 时自己等自己 → 必然排队超时，假死锁）；
4. 异常路径必须归还名额；release 幂等（重复归还不把 active 压成负数）；
5. 计数不变式破了显式拒接，不"当没排队"放行；
6. 按数据源分桶隔离；上限/预算可就地改（配置改完立即生效）；
7. 上限来自数据源 `extra_config`，且只在建桶时读一次库；取消配置要回缺省（不能“取消了也不生效”）；
8. 接线到位：`execute_query` / ES 分页口真过闸门，拒绝文案不被通用“连接不可用”覆盖。
"""
import threading
import time

import pytest

from backend.core import query_gate as qg


@pytest.fixture(autouse=True)
def clean_gates():
    qg.reset_gates()
    yield
    qg.reset_gates()


def _hold(gate, seconds, out=None, entering=None):
    """在一个线程里取一个名额并占住若干秒（out 收回到的 waited_ms 或异常）。

    `entering` 在**调 acquire 之前**置位：否则主线程看不到“正在排队”这个中间态。
    """
    if entering is not None:
        entering.set()
    try:
        handle = gate.acquire()
    except BaseException as exc:                      # noqa: BLE001 - 测试里要回传异常
        if out is not None:
            out.append(exc)
        return
    if out is not None:
        out.append(handle.waited_ms)
    time.sleep(seconds)
    gate.release(handle)


def _start(gate, seconds, out=None, entering=None):
    t = threading.Thread(target=_hold, args=(gate, seconds, out, entering), daemon=True)
    t.start()
    return t


# ── 1. 放行 / 排队 / 交接 ─────────────────────────────────────────────

def test_within_limit_acquires_immediately():
    """两个**不同线程**（= 两个请求）在上限内各自拿到名额，都不排队。

    同线程嵌套不占第二个名额（重入语义），所以这里必须开线程模拟并发。
    """
    gate = qg.SourceGate(1, limit=2, wait_s=5)
    got = []
    both = threading.Barrier(3)

    def worker():
        handle = gate.acquire()
        got.append(handle.waited_ms)
        both.wait(5)
        gate.release(handle)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    both.wait(5)
    assert gate.active == 2
    assert got == [0, 0]
    for t in threads:
        t.join(5)
    assert gate.active == 0


def test_third_caller_queues_and_gets_slot_on_release():
    """满员时后来者排队而不是被拒：释放一个就交接给队首。"""
    gate = qg.SourceGate(1, limit=1, wait_s=5)
    holder = gate.acquire()

    waited = []
    entering = threading.Event()
    t = _start(gate, 0.05, waited, entering)
    entering.wait(2)
    time.sleep(0.1)                                   # 让等待者真的进队
    assert gate.queued == 1, "满员时必须能在队列里看到后来者（不是直接拒）"

    gate.release(holder)
    t.join(5)
    assert not t.is_alive()
    assert len(waited) == 1 and isinstance(waited[0], int), waited
    assert waited[0] >= 0
    assert gate.active == 0 and gate.queued == 0


def test_fifo_order_among_multiple_waiters():
    """三个等待者必须按进入顺序拿到名额：位次是真序号，公平性靠它撑。"""
    gate = qg.SourceGate(7, limit=1, wait_s=5)
    order = []
    lock = threading.Lock()

    def worker(idx):
        handle = gate.acquire()
        with lock:
            order.append(idx)
        time.sleep(0.03)
        gate.release(handle)

    holder = gate.acquire()
    threads = []
    for i in range(3):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        threads.append(t)
        t.start()
        time.sleep(0.06)                              # 逐个入队，顺序可判定
    assert gate.queued == 3
    gate.release(holder)
    for t in threads:
        t.join(5)
    assert order == [0, 1, 2]


def test_concurrent_load_never_exceeds_limit():
    """16 线程抢 4 个槽：观察到的并发占用数不得超过上限，也不得有人被饿死。"""
    gate = qg.SourceGate(11, limit=4, wait_s=30)
    peak = []
    lock = threading.Lock()
    done = []

    def worker():
        handle = gate.acquire()
        with lock:
            peak.append(gate.active)
            done.append(1)
        time.sleep(0.02)
        gate.release(handle)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(done) == 16
    assert max(peak) <= 4
    assert gate.active == 0


# ── 2. 排队超预算：显式、可读、不泄露 ─────────────────────────────────

def test_queue_timeout_is_actionable_and_leak_free():
    gate = qg.SourceGate(3, limit=1, wait_s=0.2, label="prd-cn-mysql")
    holder = gate.acquire()
    out = []
    entering = threading.Event()

    def waiter():
        entering.set()
        try:
            handle = gate.acquire()
        except BaseException as exc:                  # noqa: BLE001
            out.append(exc)
            return
        gate.release(handle)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    entering.wait(2)
    t.join(5)
    gate.release(holder)

    assert len(out) == 1 and isinstance(out[0], qg.QueryQueueTimeout)
    message = str(out[0])
    for must in ("并发上限 1", "排队", "稍后重试", "prd-cn-mysql"):
        assert must in message, must
    for leak in ("3306", "host", "password", "10.0.", "root"):
        assert leak not in message, leak


def test_waiter_gone_from_queue_after_timeout():
    """超时放弃必须出队：留在队里会占住位次，后来者会等一个不存在的人。"""
    gate = qg.SourceGate(4, limit=1, wait_s=0.15)
    holder = gate.acquire()
    out = []

    def waiter():
        try:
            h = gate.acquire()
        except BaseException as exc:                  # noqa: BLE001
            out.append(exc)
            return
        gate.release(h)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    t.join(5)
    assert isinstance(out[0], qg.QueryQueueTimeout)
    assert gate.queued == 0
    gate.release(holder)
    assert gate.active == 0
    handle = gate.acquire()          # 队里没有幽灵，下一个能直接拿到
    assert handle.waited_ms == 0
    gate.release(handle)


# ── 3. 同线程嵌套不重复占位 ───────────────────────────────────────────

def test_nested_same_thread_does_not_take_second_slot():
    """limit=1 时一次请求里串着取多个数据集是常态：重复占位就是自己等自己（假死锁）。"""
    gate = qg.gate_for(5, limit=1, wait_s=0.3, label="x")
    with qg.query_slot(5, limit=1, wait_s=0.3, label="x"):
        with qg.query_slot(5, limit=1, wait_s=0.3, label="x"):
            assert gate.active == 1
    assert gate.active == 0
    assert gate._held_stack() == []


def test_nested_depth_bookkeeping_returns_to_zero():
    gate = qg.SourceGate(6, limit=2, wait_s=1)
    outer = gate.acquire()
    inner = gate.acquire()
    inner2 = gate.acquire()
    assert gate.active == 1                     # 只有外层真占位
    gate.release(inner2)
    gate.release(inner)
    assert gate.active == 1
    gate.release(outer)
    assert gate.active == 0


# ── 4. 异常与幂等归还 ─────────────────────────────────────────────────

def test_exception_inside_slot_still_releases():
    with pytest.raises(RuntimeError, match="boom"):
        with qg.query_slot(8, limit=1, wait_s=1):
            raise RuntimeError("boom")
    gate = qg.gate_for(8)
    assert gate.active == 0
    handle = gate.acquire()                     # 没有泄漏，下一次立刻拿到
    gate.release(handle)


def test_double_release_does_not_go_negative():
    gate = qg.SourceGate(9, limit=1, wait_s=1)
    handle = gate.acquire()
    gate.release(handle)
    gate.release(handle)                        # 幂等：第二次不动计数
    assert gate.active == 0


# ── 5. 不变式破了必须拒接 ─────────────────────────────────────────────

def test_leaked_release_does_not_permanently_bypass_gate():
    """拿了不还（漏 release）不能把本线程变成“永远免限流”：上一个句柄一旦归还，
    后续 acquire 必须重新真占位。重入靠句柄栈而不是计数器就是为了这一点。"""
    gate = qg.SourceGate(13, limit=1, wait_s=0.15)
    leaked = gate.acquire()
    nested = gate.acquire()               # 同线程：重入记账
    gate.release(nested)
    gate.release(leaked)
    assert gate.active == 0

    out = []                                # 新线程必须重新排队，而不是直接放行

    def other_worker():
        try:
            h = gate.acquire()
        except BaseException as exc:                  # noqa: BLE001
            out.append(exc)
            return
        out.append(h)
        gate.release(h)

    holder = gate.acquire()
    other = threading.Thread(target=other_worker, daemon=True)
    other.start()
    other.join(5)
    assert len(out) == 1 and isinstance(out[0], qg.QueryQueueTimeout), \
        "该线程被误当成已在队列里（旁路了闸门）"
    gate.release(holder)


def test_integrity_violation_refuses_new_query():
    """active 越界说明闸门自己坏了：此时放行=假装没超载，事后无从归因。"""
    gate = qg.SourceGate(10, limit=2, wait_s=1)
    gate.active = 3
    with pytest.raises(qg.GateIntegrityError):
        gate.acquire()


def test_integrity_negative_also_refuses():
    gate = qg.SourceGate(12, limit=2, wait_s=1)
    gate.active = -1
    with pytest.raises(qg.GateIntegrityError):
        gate.acquire()


# ── 6. 分桶隔离与配置生效 ─────────────────────────────────────────────

def test_buckets_are_isolated_per_datasource():
    a = qg.gate_for(21, limit=1, wait_s=0.1)
    qg.gate_for(22, limit=1, wait_s=0.1)
    holder = a.acquire()
    other = qg.gate_for(22)
    handle = other.acquire()                    # 另一个源不受影响
    assert other.active == 1 and a.active == 1
    a.release(holder)
    other.release(handle)


def test_limit_and_wait_update_in_place():
    """管理员在配置页改上限要立即生效（同一闸门对象，不重启、不丢在跑的查询）。"""
    gate = qg.gate_for(23, limit=2)
    assert gate.limit == 2
    again = qg.gate_for(23, limit=6, wait_s=0.5)
    assert again is gate and again.limit == 6 and again.wait_s == 0.5


def test_limits_are_clamped_to_sane_range():
    assert qg.SourceGate(1, limit=0).limit == qg.LIMIT_FLOOR
    assert qg.SourceGate(1, limit=10_000).limit == qg.LIMIT_CEIL
    assert qg.SourceGate(1, limit=None).limit == qg.default_limit()
    assert qg.SourceGate(1, wait_s=-5).wait_s == qg.WAIT_FLOOR


# ── 7. 等待预估：没样本就不编数 ───────────────────────────────────────

def test_estimated_wait_none_without_samples():
    gate = qg.SourceGate(24, limit=2)
    assert gate.estimated_wait_s(3) is None
    assert gate.snapshot()["estimated_wait_s"] is None


def test_estimated_wait_uses_recent_durations():
    gate = qg.SourceGate(25, limit=2)
    handle = gate.acquire()
    time.sleep(0.02)
    gate.release(handle)
    est = gate.estimated_wait_s(2)
    assert est is not None and est > 0
    assert gate.snapshot()["avg_query_ms"] > 0


def test_snapshot_reports_water_level():
    gate = qg.gate_for(26, limit=1, wait_s=1, label="prd-es")
    handle = gate.acquire()
    snap = qg.snapshot_all()
    row = next(s for s in snap if s["datasource_id"] == 26)
    assert row["label"] == "prd-es" and row["limit"] == 1 and row["active"] == 1
    gate.release(handle)


# ── 8. 配置面：上限来自数据源 extra_config ──────────────────────────

def _stub_rows(monkeypatch, rows):
    """把 `get_datasource_by_id` 换成内存表，并记录被读了几次。"""
    import backend.common.db.datasource_db as dsdb

    calls = []

    def fake(ds_id):
        calls.append(int(ds_id or 0))
        return rows.get(int(ds_id or 0))

    monkeypatch.setattr(dsdb, "get_datasource_by_id", fake)
    return calls


def test_gate_reads_limit_from_extra_config_once_per_bucket(monkeypatch):
    calls = _stub_rows(monkeypatch, {7: {"id": 7, "name": "prd-cn-mysql",
                                        "extra_config": {"max_concurrency": 3,
                                                          "queue_wait_sec": 0}}})
    gate = qg.gate_for_source(7)
    assert gate.limit == 3
    assert gate.wait_s == 0.0                       # 0 是“不排队直接拒”，不能被当成没配而退回缺省
    assert gate.label == "prd-cn-mysql"             # 提示里只出现业务名（ui-resource-display）
    qg.gate_for_source(7)
    assert calls == [7]                             # 建桶后不再读库（取数不众外多一条 SELECT）


def test_missing_knob_uses_relaxed_default(monkeypatch):
    _stub_rows(monkeypatch, {8: {"id": 8, "name": "prd-es", "extra_config": None}})
    gate = qg.gate_for_source(8)
    assert gate.limit == qg.default_limit() and gate.wait_s == qg.default_wait_s()


def test_sync_reverts_to_default_when_knob_cleared(monkeypatch):
    """管理员清空旋钮必须真的回到缺省：只“取新值不取缺省”就是取消了也不生效。"""
    _stub_rows(monkeypatch, {9: {"id": 9, "name": "s", "extra_config": {"max_concurrency": 5}}})
    gate = qg.gate_for_source(9)
    assert gate.limit == 5
    _stub_rows(monkeypatch, {9: {"id": 9, "name": "s", "extra_config": {}}})
    snap = qg.sync_datasource_gate(9)
    assert gate.limit == qg.default_limit() and snap["limit"] == gate.limit


def test_unreadable_datasource_row_still_limits(monkeypatch):
    """元数据库读不到时退缺省上限而不是“免限流”：旁路配置读不得把取数直接杀掉，
    但也绝不能变成“闸门没了”。主链路的真实错因由取数自己报。"""
    import backend.common.db.datasource_db as dsdb

    def boom(_ds_id):
        raise RuntimeError("metadata db down")

    monkeypatch.setattr(dsdb, "get_datasource_by_id", boom)
    gate = qg.gate_for_source(11)
    assert gate.limit == qg.default_limit()


def test_config_bounds_match_gate_clamps():
    """配置面区间与闸门 clamp 边界同源（两处各写一套数字迟早不一致）。"""
    from backend.modules.catalog.services import datasource_service as dsvc

    assert dsvc.GATE_EXTRA_NUMBERS["max_concurrency"][:2] == (qg.LIMIT_FLOOR, qg.LIMIT_CEIL)
    assert dsvc.GATE_EXTRA_NUMBERS["queue_wait_sec"][:2] == (int(qg.WAIT_FLOOR), int(qg.WAIT_CEIL))
    # 两类源都得有这个旋钮：真正“没连接池”的是 SQL 型，只给 ES 配等于需求没落地
    assert "max_concurrency" in dsvc.SQL_EXTRA_NUMBERS
    assert "max_concurrency" in dsvc.ES_EXTRA_NUMBERS


def test_saved_extra_config_validates_range():
    from backend.modules.catalog.services import datasource_service as dsvc

    assert dsvc._validated_sql_extra("mysql", {"max_concurrency": 4, "queue_wait_sec": 0}) == \
        {"max_concurrency": 4, "queue_wait_sec": 0}
    with pytest.raises(ValueError, match="最大并发查询数"):
        dsvc._validated_sql_extra("mysql", {"max_concurrency": 0})
    with pytest.raises(ValueError, match="最大并发查询数"):
        dsvc._validated_sql_extra("mysql", {"max_concurrency": qg.LIMIT_CEIL + 1})
    es = dsvc._validated_es_extra("es", {"allowed_aliases": ["logs"], "max_concurrency": 2})
    assert es["max_concurrency"] == 2


def test_queue_errors_declare_themselves_as_business_safe():
    """透传靠抛错方显式声明（不按文案猜），两个容量退让异常都得自带类别。"""
    for cls in (qg.QueryQueueTimeout, qg.GateIntegrityError):
        assert cls.exec_category == "capacity_queue"
        assert cls.safe_business_message is True


# ── 9. 接线：取数口真过闸门，文案不被通用提示覆盖 ───────────────────────

def _saturated_source(monkeypatch, ds_id, *, wait_sec=0, concurrency=1):
    _stub_rows(monkeypatch, {ds_id: {"id": ds_id, "name": "prd-cn-mysql",
                                     "extra_config": {"max_concurrency": concurrency,
                                                      "queue_wait_sec": wait_sec}}})
    return qg.gate_for_source(ds_id)


def _run_in_thread(fn):
    out = []

    def target():
        try:
            out.append(("ok", fn()))
        except BaseException as exc:                       # noqa: BLE001
            out.append(("err", exc))

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(10)
    assert out, "工作线程未返回（排队/死锁）"
    return out[0]


def test_execute_query_refuses_when_source_saturated(monkeypatch):
    """满员且排队超预算 → 可读拒，且**不得**把语句下发到源。

    名额由另一个线程占着：同线程嵌套是重入（不占第二位），在主线程里占位再查
    会走重入分支，测不出限流（这不是实现问题，是并发语义本身）。
    """
    import pandas

    import backend.core.query_executor as qe

    gate = _saturated_source(monkeypatch, 31)
    seen = []

    def fake_exec(sql, datasource_id, **_kw):
        seen.append(sql)
        return pandas.DataFrame(), 1, 1

    monkeypatch.setattr(qe, "_is_es_datasource", lambda _i: False)
    monkeypatch.setattr(qe, "_execute_via_semantic_execution", fake_exec)

    holder = gate.acquire()
    kind, exc = _run_in_thread(
        lambda: qe.execute_query("SELECT 1 LIMIT 10", datasource_id=31))
    assert kind == "err" and isinstance(exc, qg.QueryQueueTimeout)
    assert not seen, "被容量拒的查询不得下发到源"
    assert "并发上限" in str(exc) and "prd-cn-mysql" in str(exc)
    gate.release(holder)
    assert _run_in_thread(lambda: qe.execute_query("SELECT 1 LIMIT 10", datasource_id=31))[0] == "ok"
    assert seen and gate.active == 0                       # 腾出名额后能排到，不泄漏


def test_execute_query_releases_slot_on_failure(monkeypatch):
    """取数报错（方言/连不上）也必须归还名额，否则错误会把闸门永久堵死。"""
    import backend.core.query_executor as qe

    gate = _saturated_source(monkeypatch, 32, concurrency=2)

    def boom(sql, datasource_id, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(qe, "_is_es_datasource", lambda _i: False)
    monkeypatch.setattr(qe, "_execute_via_semantic_execution", boom)
    with pytest.raises(RuntimeError, match="boom"):
        qe.execute_query("SELECT 1 LIMIT 10", datasource_id=32)
    assert gate.active == 0


def test_sql_validation_failure_does_not_consume_slot(monkeypatch):
    """语法/安全校验失败不占名额：把写操作的代价转嫁给正在排队的用户不合理。"""
    gate = _saturated_source(monkeypatch, 33, concurrency=2)
    import backend.core.query_executor as qe

    with pytest.raises(ValueError):
        qe.execute_query("DELETE FROM t", datasource_id=33)
    assert gate.active == 0


def test_es_paging_path_is_gated(monkeypatch):
    """ES 分页取数不经 `execute_query`，必须自己过闸门：
    否则同一张看板的分页图能绕过上限无限打源（上限就成了只挡一半流量的假闸门）。"""
    import backend.core.es_pagination as esp
    import backend.core.query_executor as qe

    gate = _saturated_source(monkeypatch, 34)
    called = []

    class _PermResult:
        hidden_columns = []
        masked_columns = []

    monkeypatch.setattr(qe, "_is_es_datasource", lambda _i: True)
    monkeypatch.setattr(qe, "secure_read_sql",
                        lambda sql, _ds, _u, _w: (sql, _PermResult()))
    monkeypatch.setattr(esp, "query_page",
                        lambda *a, **k: called.append(a) or {"columns": [], "rows": []})
    monkeypatch.setattr("backend.core.es_client.ctx_for", lambda _i: object())

    holder = gate.acquire()
    kind, exc = _run_in_thread(
        lambda: qe.execute_es_page_with_permission(
            'SELECT "a" AS m FROM "t_x" LIMIT 20', 34, {"user_id": 7, "username": "u"}, 1,
            page_size=20, page_num=2, context_key="k"))
    assert kind == "err" and isinstance(exc, qg.QueryQueueTimeout)
    assert not called, "被容量拒的分页查询不得下发到集群"
    gate.release(holder)


def test_execute_sql_passthrough_keeps_capacity_category(monkeypatch):
    """语义侧透传：排队文案与 `capacity_queue` 类别必须原样带走，
    不得退成“系统侧问题，建议稍后重试”（那会把排查方向从容量指到网络）。"""
    from backend.semantics import execute as sx
    from backend.semantics.contract import SemanticError

    monkeypatch.setattr(sx, "_sql_audit", lambda *a, **k: None)

    def boom(*_a, **_k):
        raise qg.QueryQueueTimeout("数据源「prd-cn-mysql」当前查询较多：并发上限 1 已占满，请稍后重试")

    monkeypatch.setattr("backend.core.query_executor.execute_query_with_permission", boom)
    with pytest.raises(SemanticError) as ei:
        sx.execute_sql("SELECT 1 LIMIT 1", sx.PolicyContext(
            user_id=1, username="u", workspace_id=1, datasource_id=5))
    assert ei.value.detail["failure_category"] == "capacity_queue"
    assert "并发上限" in ei.value.message


def test_exec_fail_text_passes_queue_message_through():
    from backend.modules.viz.services.dataset_service import _exec_fail_text

    msg = "数据源「prd-cn-mysql」当前查询较多：并发上限 8 已占满，请稍后重试"
    assert _exec_fail_text(msg, "mysql", "capacity_queue") == msg
    # 类别丢了就不能假装是排队（否则任何错误都能套“并发”话术），回通用文案
    assert "并发上限" not in _exec_fail_text(msg, "mysql", "")

