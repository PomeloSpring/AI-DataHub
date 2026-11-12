"""回合上限口径（Phase D）：撞线要交代清楚、轮次上限必须是真的、耗时只有一套数字。

背景（会话 1783069459151 取证，复核脚本 logs/probe_max_turns_semantics.py）：
- 三个回合各跑满 300.2s 被墙钟中止，落库消息里写的却是 277.6s（浏览器 Date.now()）
  —— 同一个回合两套耗时，"到底跑满没跑满"各说各话，22.7s 偏差无法归因；
- 执行层配置里写着 max_turns=20，实测交互 resume 链路上 CLI 自报 num_turns=6 时
  已经发了 17 次工具调用 / 27 次模型请求，三个撞墙回合也从没回过 error_max_turns
  → `--max-turns` 不按工具轮次生效，配置承诺的上限是空的；
- 中止路径的 done 事件不带任何计数，用户只能反问"是单次查询慢还是轮次多"。

锁定：
① 撞线的 done 带 elapsed_ms / tool_call_count / query_count / timed_out / turn_limit_seconds；
② 中止文案自带诊断（上限、已跑时长、已发出次数、下一步），非超时中止不编造数字；
③ 工具轮次上限在本仓侧是真约束：达上限拒第 N+1 次并回收尾指令，blocked 计数进 meta；
④ 墙钟上限取 task.timeout（由执行层配置注入），服务端天花板 900s；
⑤ elapsed_ms 对外口径 = 服务端单调钟（观测开启取与 trace 同源的值）。
"""
import asyncio
import inspect
import time
from types import SimpleNamespace

import anyio

from backend.modules.mind.execution import secure_sdk
from backend.modules.mind.execution.models import ExecutionContext, ExecutionResult, ExecutionTask


# ── ① / ④ 撞线的 done 带已执行次数 ────────────────────────────────

async def _hang_stream(adapter, task, backend):
    """两条工具调用后彻底卡住：等价于会话里那个跑到 300s 被中止的回合。"""
    yield {"type": "tool_start", "tool": "mcp__datahub_query__execute_sql", "tool_call_id": "a"}
    yield {"type": "tool_start", "tool": "mcp__datahub_catalog__search_metadata", "tool_call_id": "b"}
    await anyio.sleep(30)
    yield {"type": "done", "result": ExecutionResult(success=True, output="不会到达")}


def _drain_execute_stream(monkeypatch, task):
    monkeypatch.setattr(secure_sdk, "_execute_stream", _hang_stream)
    events = []

    async def scenario():
        async for ev in secure_sdk.execute_stream(SimpleNamespace(config={}), task, "qoder"):
            events.append(ev)

    asyncio.run(scenario())
    return events


def test_timeout_done_event_carries_executed_counts(monkeypatch):
    """撞线不得只丢一句"超时"：已跑时长/已发出的工具与取数次数必须随 done 回来。"""
    task = ExecutionTask(task_id="t1", question="q", context=ExecutionContext(), timeout=1)
    events = _drain_execute_stream(monkeypatch, task)

    dones = [e for e in events if e.get("type") == "done"]
    assert len(dones) == 1, "done 必须恰好一条（中止路径也一样要收尾）"
    result = dones[0]["result"]
    assert result.success is False
    meta = result.meta
    assert meta["timed_out"] is True
    assert meta["tool_call_count"] == 2, meta
    assert meta["query_count"] == 1, "只有取数类工具计入 queries"
    assert meta["turn_limit_seconds"] == 1, "上限取 task.timeout（执行层配置注入）"
    assert meta["elapsed_ms"] >= 800, meta


def test_non_timeout_abort_reason_does_not_fabricate_numbers():
    """不是撞线（消费者断开/异常）就不要在文案里编"跑满 N 秒"。"""
    text = secure_sdk._abort_reason(300, 5000, 3, 2, False)
    assert text.startswith("执行超时或中断")
    assert "300s" not in text and "工具调用" not in text


# ── ② 中止文案自带诊断 ────────────────────────────────────────────

def test_abort_reason_states_limit_duration_and_next_step():
    text = secure_sdk._abort_reason(300, 300_283, 29, 24, True)
    assert "上限 300s" in text, text
    assert "29 次工具调用" in text and "取数 24 次" in text, text
    assert "300s" in text.replace("上限 300s", "") or "实际耗时 300s" in text, text
    # 下一步必须是可操作的：合并多站查询走扇出，或追问继续
    assert "sites" in text and "继续完成剩余部分" in text, text
    # 已流式输出的正文仍在（前端保留契约），文案不得暗示答案被丢弃
    assert "已输出的正文仍保留" in text, text


# ── ③ 工具轮次上限：本仓侧是真约束 ────────────────────────────────

def test_tool_round_gate_blocks_over_limit_with_wrapup_instruction():
    rounds = {"limit": 3, "count": 0, "blocked": 0}
    allowed = [secure_sdk.enforce_tool_round_limit(rounds, "execute_sql")[0] for _ in range(5)]
    assert allowed == [True, True, True, False, False], allowed
    assert rounds["count"] == 5 and rounds["blocked"] == 2, rounds

    ok, reason = secure_sdk.enforce_tool_round_limit({"limit": 1, "count": 1, "blocked": 1}, "execute_sql")
    assert ok is False
    assert "已达上限 1 次" in reason and "不要再发起新的工具调用" in reason, reason
    # 收尾指令要给出出口（继续追问），不是把模型堵死
    assert "继续追问" in reason, reason


def test_round_limit_warning_only_when_blocked():
    assert secure_sdk._round_limit_warning({"limit": 20, "count": 5, "blocked": 0}) == ""
    text = secure_sdk._round_limit_warning({"limit": 20, "count": 23, "blocked": 3})
    assert "上限 20 次" in text, text
    assert "20 次查询" in text, text          # 已完成次数 = count - blocked
    assert "sites" in text and "追问" in text, text


def test_round_gate_is_wired_into_pre_tooluse_hook_and_meta():
    """计数函数若无接线点就等于没做（配置面/代码面必须同源）。"""
    options_src = inspect.getsource(secure_sdk.build_options)
    assert "enforce_tool_round_limit(rounds, name)" in options_src
    assert 'ctx.extra["_tool_rounds"] = rounds' in options_src
    assert 'adapter.config.get("max_turns")' in options_src, "上限值必须读执行层配置"

    stream_src = inspect.getsource(secure_sdk._execute_stream)
    assert 'meta["tool_rounds"]' in stream_src and "_round_limit_warning(rounds)" in stream_src


def test_wall_ceiling_is_declared_and_applied():
    src = inspect.getsource(secure_sdk.execute_stream)
    assert "_TURN_WALL_CEILING_S" in src
    assert secure_sdk._TURN_WALL_CEILING_S == 900
    assert secure_sdk.DEFAULT_MAX_TOOL_ROUNDS == 20, "与执行层 max_turns 缺省值同源"


# ── ⑤ elapsed_ms 单一口径（服务端单调钟）─────────────────────────

def test_server_elapsed_prefers_trace_recorder(monkeypatch):
    from backend import observability
    from backend.modules.mind.services import chat_service as cs

    monkeypatch.setattr(observability, "turn_elapsed_ms", lambda: 300_283)
    assert cs._server_elapsed_ms(SimpleNamespace(meta={"elapsed_ms": 999})) == 300_283

    # 观测未开启：退回执行层自己的单调钟（仍是服务端时间），绝不回落浏览器计时
    monkeypatch.setattr(observability, "turn_elapsed_ms", lambda: None)
    assert cs._server_elapsed_ms(SimpleNamespace(meta={"elapsed_ms": 277_600})) == 277_600
    assert cs._server_elapsed_ms(SimpleNamespace(meta={})) == 0


def test_recorder_elapsed_ms_shares_trace_clock():
    from backend.observability.recorder import TraceRecorder

    rec = TraceRecorder({"trace_id": "t"})
    time.sleep(0.02)
    assert rec.elapsed_ms() >= 15


def test_done_event_passes_warnings_and_elapsed_to_frontend():
    """done 的 warnings 不得硬编码空列表：执行层给出的"本轮触顶收尾"必须看得见。"""
    src = inspect.getsource(chat_service_dispatch())
    assert '"warnings": list(result.meta.get("warnings") or [])' in src, "成功与中止两条路都要透传告警"
    assert '"elapsed_ms": _server_elapsed_ms(result)' in src
    assert "task.timeout = _layer_timeout" in src, "墙钟上限必须由执行层配置注入"


def chat_service_dispatch():
    from backend.modules.mind.services.chat_service import ChatService
    return ChatService._try_dispatch_via_execution_layer
