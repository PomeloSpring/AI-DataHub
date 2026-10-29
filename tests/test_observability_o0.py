"""LLM 可观测 O0 地基 —— 离线单测(不触碰真实数据库)。

覆盖:
  - OBSERVABILITY_ENABLED=false 时全链路 no-op(begin 返回 None、记录/收尾不写库)。
  - credit/token 跨多次 llm_call 正确累加;缺失 credit 记 None(≠0)。
  - 会话累计 credit(set_session_credits)与请求级 credit 分列存,不相加。
  - set_result + finalize 的结果合并(agent 路径回写答案/状态)。
  - llm_call 的 duration_ms = 单次模型请求窗口(非“自回合起点的累计偏移”),
    且工具执行时间不得计入下一条模型请求(时耗归因口径)。
  - Qoder 适配器埋点辅助 _obs_record_assistant / _obs_record_result 解析 SDK 字段。

全部用 monkeypatch 注入假 store,不落真库。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import backend.common.config as config_mod
from backend.observability import recorder as rec_mod
from backend.observability import store as store_mod
from backend import observability


class _InlineExecutor:
    """让后台 flush 同步执行,便于断言。"""

    def submit(self, fn, *a, **k):
        fn(*a, **k)
        return None


@pytest.fixture
def captured(monkeypatch):
    """开启可观测、同步落库、捕获 save_trace 入参;用例结束清理上下文。"""
    calls = []

    monkeypatch.setattr(config_mod, "OBSERVABILITY_ENABLED", True, raising=False)
    monkeypatch.setattr(rec_mod, "_get_flush_pool", lambda: _InlineExecutor())
    monkeypatch.setattr(store_mod, "save_trace", lambda summary, spans: calls.append((summary, spans)))

    from backend.observability.context import set_recorder
    set_recorder(None)
    yield calls
    set_recorder(None)


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.setattr(config_mod, "OBSERVABILITY_ENABLED", False, raising=False)
    from backend.observability.context import set_recorder
    set_recorder(None)
    yield
    set_recorder(None)


def test_disabled_is_full_noop(disabled):
    # 未开启:begin 返回 None,记录/收尾安全 no-op,不抛异常
    assert observability.begin(entrypoint="chat", user_id=1) is None
    observability.record_llm_call(credits=1.0, input_tokens=10, output_tokens=5)
    observability.set_result(status="success", final_answer="x")
    observability.finalize()
    assert observability.message_uuid() is None


def test_credits_and_tokens_accumulate(captured):
    rec = observability.begin(entrypoint="chat", user_id=7, username="u", question="q")
    assert rec is not None and rec.meta["trace_id"]

    observability.record_llm_call(name="r1", model_ref="m", input_tokens=100, output_tokens=40, credits=0.5)
    observability.record_llm_call(name="r2", model_ref="m", input_tokens=30, output_tokens=10, credits=0.25)
    observability.finalize(status="success", final_answer="done")

    assert len(captured) == 1
    summary, spans = captured[0]
    assert len(spans) == 2
    assert summary["llm_call_count"] == 2
    assert summary["span_count"] == 2
    assert summary["input_tokens"] == 130
    assert summary["output_tokens"] == 50
    assert summary["total_tokens"] == 180
    assert abs(summary["credits"] - 0.75) < 1e-9
    assert summary["status"] == "success"
    assert summary["final_answer"] == "done"


def test_missing_credit_stays_none(captured):
    observability.begin(entrypoint="chat", user_id=1)
    # 只报 token、无 credit(旧 CLI/未登录):credits 必须保持 NULL,而非 0
    observability.record_llm_call(name="r", input_tokens=5, output_tokens=3)
    observability.finalize()

    summary, spans = captured[0]
    assert summary["credits"] is None
    assert summary["cost_amount"] is None
    assert spans[0]["credits"] is None
    assert summary["input_tokens"] == 5


def test_session_credits_not_merged_with_request_level(captured):
    observability.begin(entrypoint="chat", user_id=1)
    observability.record_llm_call(name="r", credits=0.4)
    observability.set_session_credits(12.5, {"model-x": {"credits": 12.5}})
    observability.finalize()

    summary, _ = captured[0]
    assert abs(summary["credits"] - 0.4) < 1e-9        # 本轮请求级之和
    assert abs(summary["session_credits"] - 12.5) < 1e-9  # 会话累计单列,不相加


def test_set_result_merges_into_finalize(captured):
    observability.begin(entrypoint="chat", user_id=1, question="hi")
    observability.record_llm_call(name="r", input_tokens=1, output_tokens=1)
    # agent 路径:子生成器回写结果,入口 finalize 不带显式入参也应采纳
    observability.set_result(status="error", error="boom", final_answer="partial")
    observability.finalize()

    summary, _ = captured[0]
    assert summary["status"] == "error"
    assert summary["error_message"] == "boom"
    assert summary["final_answer"] == "partial"


def test_explicit_finalize_overrides_set_result(captured):
    observability.begin(entrypoint="chat", user_id=1)
    observability.set_result(status="error", final_answer="stored")
    observability.finalize(status="success", final_answer="explicit")

    summary, _ = captured[0]
    assert summary["status"] == "success"
    assert summary["final_answer"] == "explicit"


def test_qoder_helpers_parse_sdk_fields(captured):
    from backend.modules.mind.execution.adapters import qoder_sdk_adapter as qmod

    observability.begin(entrypoint="chat", user_id=1)

    class _Assistant:
        model = "claude-x"
        usage = {"input_tokens": 200, "output_tokens": 50,
                 "credits": 0.7, "original_credits": 0.9, "billable": True}

    class _Result:
        total_credits = 33.0
        model_usage = {"claude-x": {"credits": 33.0}}

    qmod._obs_record_assistant(_Assistant())
    qmod._obs_record_result(_Result())
    observability.finalize()

    summary, spans = captured[0]
    # llm_call 产物 + ResultMessage 收尾产物(新增 kind=result)
    assert [s["kind"] for s in spans] == ["llm_call", "result"]
    sp = spans[0]
    assert sp["name"] == "qoder_llm_request"
    assert sp["model_ref"] == "claude-x"
    assert sp["input_tokens"] == 200 and sp["output_tokens"] == 50
    assert abs(sp["credits"] - 0.7) < 1e-9
    assert sp["billable"] is True
    assert abs(summary["credits"] - 0.7) < 1e-9
    assert abs(summary["session_credits"] - 33.0) < 1e-9


def test_qoder_helpers_tolerate_missing_usage(disabled):
    # 未开启时 Qoder 辅助也不应抛异常
    from backend.modules.mind.execution.adapters import qoder_sdk_adapter as qmod

    class _NoUsage:
        pass

    qmod._obs_record_assistant(_NoUsage())
    qmod._obs_record_result(_NoUsage())


# ── 回合 credit / 模型名 / token 的真实来源补算 ─────────────────────

def _stub_baseline(monkeypatch, status, value=None):
    """假扮 store.prior_session_credits，并记录被调用次数。"""
    called = []

    def _fake(conversation_id, trace_id):
        called.append((conversation_id, trace_id))
        return (status, value)

    monkeypatch.setattr(store_mod, "prior_session_credits", _fake)
    return called


def test_turn_credits_derived_from_session_cumulative_delta(captured, monkeypatch):
    # 流式模式下 CLI 不给请求级 credit，只给会话累计 → 本回合 credit = 累计增量
    called = _stub_baseline(monkeypatch, "ok", 4.584346)
    observability.begin(entrypoint="agent", user_id=1, conversation_id=900)
    observability.record_llm_call(name="r", model_ref="Qwen3.7-Plus")
    observability.set_session_credits(5.101405, {"qmodel": {"credits": 5.101405}})
    observability.finalize()

    summary, _ = captured[0]
    assert called and called[0][0] == 900
    assert abs(summary["credits"] - (5.101405 - 4.584346)) < 1e-6
    assert abs(summary["session_credits"] - 5.101405) < 1e-9  # 累计仍单列，不被覆盖


def test_first_turn_counts_full_cumulative(captured, monkeypatch):
    _stub_baseline(monkeypatch, "first")
    observability.begin(entrypoint="agent", user_id=1, conversation_id=900)
    observability.set_session_credits(2.461372, {"qmodel": {"credits": 2.461372}})
    observability.finalize()
    assert abs(captured[0][0]["credits"] - 2.461372) < 1e-9


def test_credit_reset_within_conversation_counts_full(captured, monkeypatch):
    # 清上下文/新建 session 后累计回落：不能算出负数，也不能拿旧基线抵扣
    _stub_baseline(monkeypatch, "ok", 7.602884)
    observability.begin(entrypoint="agent", user_id=1, conversation_id=900)
    observability.set_session_credits(3.804337, {"qmodel": {"credits": 3.804337}})
    observability.finalize()
    assert abs(captured[0][0]["credits"] - 3.804337) < 1e-9


def test_unknown_baseline_keeps_credit_none(captured, monkeypatch):
    # 基线查不到时不猜测：credit 保持 NULL（前端显示 —），绝不拿累计值充数
    _stub_baseline(monkeypatch, "unknown")
    observability.begin(entrypoint="agent", user_id=1, conversation_id=900)
    observability.set_session_credits(6.595985, {"qmodel": {"credits": 6.595985}})
    observability.finalize()

    summary, _ = captured[0]
    assert summary["credits"] is None
    assert abs(summary["session_credits"] - 6.595985) < 1e-9


def test_request_level_credit_wins_over_cumulative_delta(captured, monkeypatch):
    called = _stub_baseline(monkeypatch, "ok", 1.0)
    observability.begin(entrypoint="agent", user_id=1, conversation_id=900)
    observability.record_llm_call(name="r", credits=0.21)
    observability.set_session_credits(9.0, {"qmodel": {"credits": 9.0}})
    observability.finalize()

    summary, _ = captured[0]
    assert abs(summary["credits"] - 0.21) < 1e-9
    assert called == []  # 已有请求级值，不查基线


def test_model_ref_backfilled_from_llm_call_span(captured):
    # 入口未传 model_ref（走默认模型）→ 回填执行层上报的真实生效模型
    observability.begin(entrypoint="agent", user_id=1, model_ref="")
    observability.record_llm_call(name="r", model_ref="Qwen3.7-Plus")
    observability.finalize()
    assert captured[0][0]["model_ref"] == "Qwen3.7-Plus"


def test_explicit_model_ref_not_overwritten(captured):
    observability.begin(entrypoint="agent", user_id=1, model_ref="Efficient")
    observability.record_llm_call(name="r", model_ref="Qwen3.7-Plus")
    observability.finalize()
    assert captured[0][0]["model_ref"] == "Efficient"


def test_result_usage_tokens_fill_when_request_level_zero(captured):
    observability.begin(entrypoint="agent", user_id=1)
    observability.record_llm_call(name="r", model_ref="m", input_tokens=0, output_tokens=0)
    observability.set_session_credits(
        None, None, usage={"input_tokens": 51200, "output_tokens": 640})
    observability.finalize()

    summary, _ = captured[0]
    assert summary["input_tokens"] == 51200
    assert summary["output_tokens"] == 640
    assert summary["total_tokens"] == 51840


def test_cost_amount_only_from_cli_or_configured_rate(captured, monkeypatch):
    monkeypatch.setattr(config_mod, "OBSERVABILITY_CREDIT_COST_RATE", 0, raising=False)
    observability.begin(entrypoint="agent", user_id=1)
    observability.record_llm_call(name="r", credits=0.5)
    observability.finalize()
    # 单价未配置 + CLI 未报成本 → 保持 NULL（显示 —），不臆造价格
    assert captured[0][0]["cost_amount"] is None

    monkeypatch.setattr(config_mod, "OBSERVABILITY_CREDIT_COST_RATE", 0.04, raising=False)
    monkeypatch.setattr(config_mod, "OBSERVABILITY_CREDIT_COST_CURRENCY", "CNY", raising=False)
    observability.begin(entrypoint="agent", user_id=1)
    observability.record_llm_call(name="r", credits=0.5)
    observability.finalize()
    summary = captured[1][0]
    assert abs(summary["cost_amount"] - 0.02) < 1e-9
    assert summary["cost_currency"] == "CNY"     # 折算币种随行携带，不把人民币当美元展示

    monkeypatch.setattr(config_mod, "OBSERVABILITY_CREDIT_COST_RATE", 0, raising=False)
    observability.begin(entrypoint="agent", user_id=1)
    observability.set_session_credits(None, None, cost_usd=1.25)
    observability.finalize()
    summary = captured[2][0]
    # CLI 上报的成本是美元，直存并标 USD（不被配置币种覆盖）
    assert abs(summary["cost_amount"] - 1.25) < 1e-9
    assert summary["cost_currency"] == "USD"


# ── 账号 credit 配额（Qoder 不回报美元，credit 配额是“实际消耗”的唯一可信口径）──

def test_quota_bucket_normalization():
    from backend.modules.mind.execution.qoder_quota import _normalize_buckets

    info = {
        "userQuota": {"total": 100.0, "used": 40.0, "percentage": 40.0, "unit": "credits"},
        "addOnQuota": {"total": 50, "used": 10, "remaining": 40, "unit": "credits",
                       "detailUrl": "https://example.invalid/x"},
        # 组织资源包用 cap 而非 total，且不给 remaining —— 均需收敛
        "orgResourcePackage": {"cap": 6000.0, "used": 5334.0, "percentage": 89.0,
                               "available": True, "unit": "credits"},
    }
    buckets = {b["source"]: b for b in _normalize_buckets(info)}
    assert list(buckets) == ["userQuota", "addOnQuota", "orgResourcePackage"]
    assert buckets["userQuota"]["remaining"] == 60.0        # total-used 推导
    assert buckets["addOnQuota"]["detail_url"] == "https://example.invalid/x"
    assert buckets["orgResourcePackage"]["total"] == 6000.0  # cap → total
    assert buckets["orgResourcePackage"]["remaining"] == 666.0


def test_quota_normalization_skips_empty_buckets():
    from backend.modules.mind.execution.qoder_quota import _normalize_buckets

    # 只有 orgResourcePackage 的账号（实测企业版就是这样）不得被空桶噪出多条
    assert [b["source"] for b in _normalize_buckets(
        {"userQuota": {}, "orgResourcePackage": {"cap": 6000.0, "used": 5334.0}})] == ["orgResourcePackage"]
    # 无任何额度数值 → 空清单（上层据此返回 available=false，不画空进条）
    assert _normalize_buckets({"userQuota": {"unit": "credits"}}) == []


def test_quota_failure_is_explicit_not_empty(monkeypatch):
    """读不到配额必须带原因对外暴露，不得回退成空数据（no-silent-degradation）。"""
    import asyncio

    from backend.modules.mind.execution import qoder_quota as qmod

    async def _unavailable():
        return {"available": False, "reason": "执行层凭据未配置"}

    monkeypatch.setattr(qmod, "_fetch_from_cli", _unavailable)
    payload = asyncio.run(qmod.read_quota(refresh=True))
    assert payload["available"] is False
    assert payload["reason"] == "执行层凭据未配置"


def test_record_span_keeps_artifacts_without_token_accumulation(captured):
    observability.begin(entrypoint="chat", user_id=1)
    observability.record_llm_call(name="r", input_tokens=10, output_tokens=5)
    observability.record_span(
        kind="tool_call", name="run_semantic_query",
        status="success", duration_ms=120,
        input_text='{"sql": "SELECT 1"}', output_text="[{\"a\":1}]",
    )
    observability.record_span(
        kind="tool_call", name="execute", status="error", error_text="boom",
    )
    observability.finalize()

    summary, spans = captured[0]
    assert len(spans) == 3
    # 产物 span 不计入 llm_call_count/token 汇总
    assert summary["llm_call_count"] == 1
    assert summary["span_count"] == 3
    tool_spans = [s for s in spans if s["kind"] == "tool_call"]
    assert tool_spans[0]["input_text"] == '{"sql": "SELECT 1"}'
    assert tool_spans[0]["output_text"] == '[{"a":1}]'
    assert tool_spans[0]["total_tokens"] == 0 and tool_spans[0]["credits"] is None
    assert tool_spans[1]["status"] == "error" and tool_spans[1]["error_text"] == "boom"


def test_qoder_tool_and_assistant_output_spans(captured):
    import json as _json

    from backend.modules.mind.execution.adapters import qoder_sdk_adapter as qmod

    observability.begin(entrypoint="agent", user_id=1)

    class _Text:
        pass
    _Text.__name__ = "TextBlock"
    _Text.text = "生成如下 SQL"

    class _ToolUse:
        pass
    _ToolUse.__name__ = "ToolUseBlock"
    _ToolUse.name = "mcp__datamind__run_semantic_query"
    _ToolUse.input = {"sql": "SELECT 1"}

    class _Assistant:
        model = "claude-x"
        content = [_Text(), _ToolUse()]
        usage = {}

    qmod._obs_record_assistant(_Assistant())

    # 工具结果 span:入参从 tracker.arguments_of 取回,输出/错误/耗时来自事件
    from backend.modules.mind.execution.stream_utils import ToolEventTracker

    class _Use:
        id = "tc-1"
        name = "run_semantic_query"
        input = {"sql": "SELECT 1"}

    tracker = ToolEventTracker()
    tracker.on_tool_use(_Use())
    ev = {
        "tool_call_id": "tc-1", "tool": "run_semantic_query",
        "output": "1 row", "error": "", "elapsed": 0.42,
    }
    qmod._obs_record_tool_result(ev, tracker.arguments_of("tc-1"))
    observability.finalize()

    summary, spans = captured[0]
    kinds = [s["kind"] for s in spans]
    assert "assistant" in kinds and "tool_call" in kinds
    asst = next(s for s in spans if s["kind"] == "assistant")
    payload = _json.loads(asst["output_text"])
    assert payload["text"] == "生成如下 SQL"
    assert payload["tool_calls"][0]["arguments"] == {"sql": "SELECT 1"}
    tool = next(s for s in spans if s["kind"] == "tool_call")
    assert _json.loads(tool["input_text"]) == {"sql": "SELECT 1"}
    assert tool["output_text"] == "1 row" and tool["status"] == "success"
    assert tool["duration_ms"] == 420


# ── llm_call 时长口径：单次请求窗口，不是累计偏移 ──────────────────

class _Clock:
    """假时钟：只按显式 advance 推进，使 duration 断言与真机速度/负载无关。"""

    def __init__(self):
        self.t = 1_000.0

    def monotonic(self):
        return self.t

    def advance(self, ms):
        self.t += ms / 1000.0


@pytest.fixture
def clocked(captured, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(rec_mod, "time", clock)
    return captured, clock


def test_llm_call_duration_is_request_window_not_cumulative(clocked):
    """回归锁定：前置阶段（建连/资源绑定）不得算进第一次模型请求。

    历史缺陷：add_llm_call 未传时长时回落为 `now - 回合起点`，于是一轮 52s 的
    回合里 8 条 llm_call 写出 13619/24304/51563… 递增的累计值（相加 394s），
    管理端拿它做时耗归因会全盘错。
    """
    captured, clock = clocked
    observability.begin(entrypoint="chat", user_id=1)
    clock.advance(8_000)                       # 前置阶段：会话领取 + SDK 建连
    observability.mark_model_window_start()    # 执行层在真正下发请求前标注
    clock.advance(5_500)                       # 第一次模型请求真耗 5.5s
    observability.record_llm_call(name="r1", model_ref="m")
    clock.advance(300)                         # 同一次请求内的后续消息
    observability.record_llm_call(name="r2", model_ref="m")
    observability.finalize()

    spans = captured[0][1]
    assert [s["duration_ms"] for s in spans if s["kind"] == "llm_call"] == [5_500, 300]


def test_tool_time_not_counted_into_next_llm_call(clocked):
    """工具执行时间不得计入下一条模型请求；各 llm_call 之和不得超过回合总时长。"""
    captured, clock = clocked
    observability.begin(entrypoint="chat", user_id=1)
    clock.advance(1_000)
    observability.mark_model_window_start()
    clock.advance(4_000)
    observability.record_llm_call(name="r1", model_ref="m")
    clock.advance(300)
    observability.record_span(kind="tool_call", name="mcp__datahub_semantic__knowledge_search",
                              duration_ms=5_200)   # 工具已完成（结果回到模型那一刻）
    clock.advance(4_600)
    observability.record_llm_call(name="r2", model_ref="m")
    clock.advance(8_400)
    observability.record_llm_call(name="r3", model_ref="m")
    observability.finalize()

    summary, spans = captured[0]
    durs = [s["duration_ms"] for s in spans if s["kind"] == "llm_call"]
    assert durs == [4_000, 4_600, 8_400]      # 旧口径会写成 5_000/10_500/18_900 的累计值
    assert sum(durs) <= summary["duration_ms"]  # 不可能出现“子项相加 394s / 回合 52s”


def test_explicit_duration_and_phase_span_are_respected(clocked):
    """显式传的时长不被窗口计算覆写；kind="phase" 不切模型窗口（它不是请求边界）。"""
    captured, clock = clocked
    observability.begin(entrypoint="chat", user_id=1)
    clock.advance(600)
    observability.record_span(kind="phase", name="sdk_connect", duration_ms=600)
    clock.advance(200)
    observability.record_llm_call(name="windowed", model_ref="m")
    observability.record_llm_call(name="measured", model_ref="m", duration_ms=42)
    clock.advance(150)
    observability.record_llm_call(name="after", model_ref="m")
    observability.finalize()

    spans = captured[0][1]
    assert spans[0]["kind"] == "phase" and spans[0]["duration_ms"] == 600
    by_name = {s["name"]: s["duration_ms"] for s in spans if s["kind"] == "llm_call"}
    assert by_name["windowed"] == 800         # 未标注时从回合起点算（含 phase），phase 不重置锚点
    assert by_name["measured"] == 42          # 显式值不被覆写
    assert by_name["after"] == 150            # 上一条记录后锚点已前推


def test_unmarked_first_call_is_visibly_warned(clocked, caplog):
    """未接入窗口标注的调用方：值仍可用（从回合起点），但必须把口径提醒出来。"""
    captured, clock = clocked
    with caplog.at_level("WARNING", logger="observability.recorder"):
        observability.begin(entrypoint="chat", user_id=1)
        clock.advance(13_600)
        observability.record_llm_call(name="r", model_ref="m")
        observability.finalize()

    assert captured[0][1][0]["duration_ms"] == 13_600
    assert "未经 mark_model_window_start 标注" in caplog.text

