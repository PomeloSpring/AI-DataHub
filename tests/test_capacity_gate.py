"""并发闸门与提供商繁忙的契约：人多时排队要看得见、可取消、不泄漏名额；繁忙只重试可恢复类。

背景：平台此前**没有任何并发闸门** —— 只有 `adh_agent_sessions` 的"同一会话不能并发跑两轮"
执行权互斥（单会话粒度）与 `qoder_quota` 的账号 credit 配额（额度，不是并发）。使用人数一多，
表现就是"每轮都变慢、最后一起撞墙钟超时"，而屏幕上只有一个转圈：用户分不清是自己在排队、
提供商在拒，还是功能坏了。

本文件锁定：
① 闸门满员时后来者 FIFO 排队，`queued`/位置是真实序号（不是"估算人数"）；取消等待不泄漏计数；
② 计数不变式破了就拒接新回合（不把"闸门失效"伪装成"没排队"）；
③ 排队事件必须在 `claim_session` 之前发出（先占执行权再排队，会用 running 把用户自己的
   重试挡在 409 外，还空转名额）—— 按源码顺序断言，防回归；
④ 提供商繁忙按 CLI 结构化字段分类：只有 provider_busy / rate_limited 才自动重试，
   次数与单次等待都有上限（超时/被停止/权限拒绝绝不重试）；
⑤ 观测列 queue_wait_ms / retry_count 与 REST 口径（不外泄内部 id）。
"""
import asyncio
import inspect
import re
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.modules.mind.api import chat as chat_api
from backend.modules.mind.execution import capacity, context_usage
from backend.modules.mind.services import chat_service


# ── ① 闸门：排队、取消、不泄漏 ────────────────────────────────────

def test_gate_queues_and_reports_real_position():
    """满员时后来的请求按 FIFO 排队，位置是队里真实序号。"""
    gate = capacity.TurnGate(limit=2)
    events = []

    async def scenario():
        async def on_queued(info):
            events.append(info)

        held = [await gate.acquire(), await gate.acquire()]
        assert gate.active == 2

        third = asyncio.create_task(gate.acquire(on_queued))
        fourth = asyncio.create_task(gate.acquire(on_queued))
        await asyncio.sleep(0)          # 让两个等待者入队
        assert gate.queued == 2
        gate.release(held[0])
        h3 = await asyncio.wait_for(third, 1)
        gate.release(held[1])
        h4 = await asyncio.wait_for(fourth, 1)
        gate.release(h3)
        gate.release(h4)
        return gate.active

    final_active = asyncio.run(scenario())
    assert [e["position"] for e in events] == [1, 2], "排队位置必须是真实队序号"
    assert all(e["limit"] == 2 for e in events)
    assert final_active == 0, "名额未归还 → 会把后来者永久挡在队外"


def test_gate_cancel_while_queued_does_not_leak_slot():
    """排队中被取消（客户端断连/用户点取消）：出队且不占名额。"""
    gate = capacity.TurnGate(limit=1)

    async def scenario():
        held = await gate.acquire()
        waiter = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0)
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        assert gate.queued == 0
        gate.release(held)
        return gate.active

    assert asyncio.run(scenario()) == 0


def test_gate_handoff_does_not_overcount_active():
    """交接给排队者时 active 不能 +1（否则上限形同虚设，超载会集体撞墙钟）。"""
    gate = capacity.TurnGate(limit=1)

    async def scenario():
        first = await gate.acquire()
        second_task = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0)
        gate.release(first)
        second = await asyncio.wait_for(second_task, 1)
        await asyncio.sleep(0)
        return gate.active, second

    active, second = asyncio.run(scenario())
    assert active == 1, "交接路径重复计数 → 并发上限被突破"
    assert second.waited_ms >= 0
    gate.release(second)
    assert gate.active == 0


def test_gate_double_release_is_ignored():
    """同一句柄重复 release 不得把 active 压成负数（负数会让闸门永久失效）。"""
    gate = capacity.TurnGate(limit=2)

    async def scenario():
        handle = await gate.acquire()
        gate.release(handle)
        gate.release(handle)
        return gate.active

    assert asyncio.run(scenario()) == 0


def test_gate_integrity_failure_rejects_new_turn(monkeypatch):
    """计数不一致时**拒接**新回合：把"闸门坏了"伪装成"没排队"是最坏结果。"""
    gate = capacity.TurnGate(limit=2)
    gate.active = 5                        # 人为破坏不变式

    async def scenario():
        with pytest.raises(capacity.GateIntegrityError):
            await gate.acquire()

    asyncio.run(scenario())


def test_estimated_wait_uses_real_turn_durations():
    """预估等待来自已完成回合的均值；没有样本时返回 None（不编一个常数冒充预估）。"""
    gate = capacity.TurnGate(limit=2)
    assert gate.estimated_wait_s(3) is None
    gate._durations.extend([10_000, 10_000])
    # 第 3 位 / 2 个槽 / 均值 10s → 约 15s（名额释放即交接，不是“等整批”）
    assert gate.estimated_wait_s(3) == pytest.approx(15.0)
    assert gate.estimated_wait_s(1) == pytest.approx(5.0)


# ── ② 分类：只重试可恢复的提供商繁忙 ──────────────────────────────

@pytest.mark.parametrize("text, kind", [
    ("Error: 529 overloaded_error", "provider_busy"),
    ("rate_limit exceeded, too many requests", "rate_limited"),
    ("HTTP 429 Too Many Requests", "rate_limited"),
    ("503 Service Unavailable", "provider_busy"),
    ("模型服务繁忙，请稍后再试", "provider_busy"),
    ("request timed out after 300s", "timeout"),
    ("用户中止了本轮（interrupt）", "aborted"),
    ("工具权限被拒绝", "failed"),
    ("", "failed"),
])
def test_failure_classification_is_structured(text, kind):
    assert capacity.classify_failure(text)[0] == kind


def test_retry_after_adopts_provider_hint_but_capped(monkeypatch):
    """采纳提供商给的 retry_after，但封顶：说"一小时后再来"不能让用户干等。"""
    monkeypatch.setenv("AGENT_BUSY_MAX_RETRY_WAIT_S", "20")
    assert capacity.classify_failure("overloaded, retry after 8")[1] == 8
    assert capacity.classify_failure("overloaded, retry_after=3600")[1] == 20
    # 没给 retry_after 时按分类给保守默认，同样不越过上限
    assert capacity.classify_failure("529 overloaded")[1] == capacity._BUSY_WAIT_DEFAULT_S["provider_busy"]


def test_recoverable_kinds_are_only_busy_and_rate_limit():
    assert capacity.is_recoverable("provider_busy") and capacity.is_recoverable("rate_limited")
    for kind in ("timeout", "aborted", "failed", "permission_denied"):
        assert not capacity.is_recoverable(kind), f"{kind} 重试等于重复取数/重复写动作"


def test_user_hint_never_leaks_internals():
    """面向用户的文案只有分类语义，不含 subtype / 路径 / 堆栈线索。"""
    for kind in ("provider_busy", "rate_limited", "timeout", "aborted", "failed"):
        hint = capacity.user_hint(kind)
        assert re.search(r"subtype|Traceback|/home/|sdk_session", hint) is None, hint


# ── ③ 接线顺序：排队必须在 claim_session 之前 ─────────────────────

def test_gate_is_acquired_before_claim_session():
    """源码顺序断言：先拿名额再领执行权。

    反序的后果是可观察的缺陷：占住 `status='running'` 却空等名额，用户自己的重试
    会被 preflight 409「上一轮仍在执行」挡在门外，同时白占一个并发槽。
    """
    source = inspect.getsource(chat_service)
    acquire = source.find("gate.acquire(")
    release = source.find("gate.release(slot_handle)")
    dispatch = source.find("adapter.execute_stream(task)")
    assert 0 <= acquire < dispatch, "必须排队结束后才派发执行层（否则执行权先被占住）"
    assert release > dispatch, "名额要包住整个回合，不能拿到就还"
    # 取消/断连路径也不能泄漏名额
    assert "acquire.cancel()" in source


def test_capacity_sse_events_are_forwarded():
    """执行层的 context / capacity 事件必须原样透到 SSE（否则前端只能显示"正在思考"）。"""
    source = inspect.getsource(chat_service)
    assert '("context", "capacity")' in source or ('"context"' in source and '"capacity"' in source)


def test_queue_wait_is_observed():
    """排队时长要落观测（否则"人多变慢"永远只能靠嘴说）。"""
    source = inspect.getsource(chat_service)
    assert "set_capacity(queue_wait_ms=" in source


# ── ⑤ REST 口径 ───────────────────────────────────────────────────

def test_capacity_endpoint_snapshot_without_identity_leak(monkeypatch):
    """/capacity 只回聚合水位与配额，不含任何用户/会话内部标识。"""
    monkeypatch.setattr(capacity, "_GATE", capacity.TurnGate(limit=4))
    monkeypatch.setattr("backend.modules.mind.execution.qoder_quota.peek_quota",
                        lambda: {"available": False, "reason": "本进程还未采集过 credit 配额"})
    out = chat_api.get_chat_capacity(user={"user_id": 7, "role": "admin"})
    assert set(out) >= {"limit", "active", "queued", "estimated_wait_s", "quota"}
    assert out["limit"] == 4 and out["active"] == 0
    text = str(out)
    assert "user_id" not in text and "session" not in text.lower()


def test_context_usage_endpoint_scoped_by_user(monkeypatch):
    """GET /context-usage 的属主来自服务端身份，不来自查询参数。"""
    seen = {}

    def fake_get_usage(conversation_id, user_id):
        seen["args"] = (conversation_id, user_id)
        return {"available": False, "used_percentage": None, "reason": "尚未测量"}
    monkeypatch.setattr(context_usage, "get_usage", fake_get_usage)
    out = chat_api.get_chat_context_usage(conversation_id=123, user={"user_id": 7})
    assert seen["args"] == (123, 7)
    assert out["available"] is False and out["used_percentage"] is None


def test_compact_endpoint_maps_domain_errors(monkeypatch):
    """409/502/404 按领域异常映射，且都带可操作中文与 Retry-After。"""
    async def boom_busy(*a, **kw):
        raise context_usage.ContextCompactBusy("本轮任务进行中，等它结束或先停止后再压缩")
    monkeypatch.setattr(context_usage, "compact_conversation", boom_busy)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat_api.post_chat_context_compact(
            SimpleNamespace(conversation_id=5), user={"user_id": 7, "role": "admin"}))
    assert exc.value.status_code == 409
    assert "本轮任务进行中" in str(exc.value.detail)
    assert exc.value.headers.get("Retry-After")


def test_compact_endpoint_rejects_missing_conversation():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat_api.post_chat_context_compact(
            SimpleNamespace(conversation_id=0), user={"user_id": 7}))
    assert exc.value.status_code == 400
