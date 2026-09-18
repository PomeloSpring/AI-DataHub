"""LLM 可观测 O0 地基 —— 离线单测(不触碰真实数据库)。

覆盖:
  - OBSERVABILITY_ENABLED=false 时全链路 no-op(begin 返回 None、记录/收尾不写库)。
  - credit/token 跨多次 llm_call 正确累加;缺失 credit 记 None(≠0)。
  - 会话累计 credit(set_session_credits)与请求级 credit 分列存,不相加。
  - set_result + finalize 的结果合并(agent 路径回写答案/状态)。
  - Qoder 适配器埋点辅助 _obs_record_assistant / _obs_record_result 解析 SDK 字段。

全部用 monkeypatch 注入假 store,不落真库。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import services.shared.common.config as config_mod
from services.shared.observability import recorder as rec_mod
from services.shared.observability import store as store_mod
from services.shared import observability


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

    from services.shared.observability.context import set_recorder
    set_recorder(None)
    yield calls
    set_recorder(None)


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.setattr(config_mod, "OBSERVABILITY_ENABLED", False, raising=False)
    from services.shared.observability.context import set_recorder
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
    assert summary["cost_usd"] is None
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
    from services.datamind.execution.adapters import qoder_sdk_adapter as qmod

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
    from services.datamind.execution.adapters import qoder_sdk_adapter as qmod

    class _NoUsage:
        pass

    qmod._obs_record_assistant(_NoUsage())
    qmod._obs_record_result(_NoUsage())


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

    from services.datamind.execution.adapters import qoder_sdk_adapter as qmod

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
    from services.datamind.execution.stream_utils import ToolEventTracker

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
