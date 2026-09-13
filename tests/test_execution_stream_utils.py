"""Unit tests for execution.stream_utils — 工具输出限长与事件跟踪器."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datamind.execution.stream_utils import (
    DEFAULT_TOOL_DISPLAY_LIMIT,
    ToolEventTracker,
    cap_arguments,
    extract_result_text,
    tool_display_limit,
    truncate_for_display,
)


class _Block:
    """模拟 SDK ToolUseBlock."""

    def __init__(self, id, name, input=None):
        self.id = id
        self.name = name
        self.input = input or {}


class _Result:
    """模拟 SDK ToolResultBlock."""

    def __init__(self, tool_use_id, content, is_error=False):
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


# ── 限长与摘要 ──────────────────────────────────────────────────

class TestLimits:
    def test_default_limit(self):
        assert tool_display_limit("Read") == DEFAULT_TOOL_DISPLAY_LIMIT

    def test_mcp_suffix_matches_limits(self):
        assert tool_display_limit("mcp__datahub_query__execute_sql") == 8000

    def test_truncate_keeps_short(self):
        assert truncate_for_display("abc", 100) == "abc"

    def test_truncate_cuts_long(self):
        out = truncate_for_display("x" * 3000, 2000)
        assert out.startswith("x" * 2000)
        assert out.endswith("(truncated)")

    def test_truncate_none(self):
        assert truncate_for_display(None, 10) == ""


class TestExtractResultText:
    def test_str(self):
        assert extract_result_text("ok") == "ok"

    def test_dict_list(self):
        assert extract_result_text([{"text": "a"}, {"text": "b"}]) == "a\nb"

    def test_block_list(self):
        class TB:
            def __init__(self, t):
                self.text = t

        assert extract_result_text([TB("a"), TB("")]) == "a"

    def test_none(self):
        assert extract_result_text(None) == ""


class TestCapArguments:
    def test_long_string_truncated(self):
        out = cap_arguments({"content": "y" * 1000, "name": "n"})
        assert len(out["content"]) == 303 and out["content"].endswith("...")
        assert out["name"] == "n"

    def test_non_dict_returns_empty(self):
        assert cap_arguments("raw") == {}
        assert cap_arguments(None) == {}


# ── 事件跟踪器 ──────────────────────────────────────────────────

class TestTracker:
    def test_start_result_roundtrip(self):
        t = ToolEventTracker()
        start = t.on_tool_use(_Block("tu1", "execute_sql", {"sql": "SELECT 1"}))
        assert start["type"] == "tool_start"
        assert start["tool_call_id"] == "tu1" and start["tool"] == "execute_sql"

        res = t.on_tool_result(_Result("tu1", [{"text": "2 rows"}]))
        assert res["type"] == "tool_result"
        assert res["output"] == "2 rows" and res["error"] == ""
        assert res["elapsed"] is not None and res["elapsed"] >= 0

        meta = {}
        t.update_meta(meta)
        assert meta["tool_call_count"] == 1
        assert meta["tool_calls"][0]["result"] == "2 rows"
        assert meta["tool_calls"][0]["step"] == 1

    def test_error_result(self):
        t = ToolEventTracker()
        t.on_tool_use(_Block("tu2", "Bash"))
        res = t.on_tool_result(_Result("tu2", "boom", is_error=True))
        assert res["error"] == "boom"
        meta = {}
        t.update_meta(meta)
        assert meta["tool_calls"][0]["error"] == "boom"
        assert meta["tool_calls"][0]["result"] is None

    def test_unknown_result_id_still_emits_event(self):
        t = ToolEventTracker()
        res = t.on_tool_result(_Result("ghost", "late result"))
        assert res is not None and res["output"] == "late result"
        assert res["elapsed"] is None

    def test_no_calls_meta_untouched(self):
        t = ToolEventTracker()
        meta = {"cli": "claude"}
        t.update_meta(meta)
        assert "tool_calls" not in meta and "tool_call_count" not in meta

    def test_multiple_calls_sequential_steps(self):
        t = ToolEventTracker()
        t.on_tool_use(_Block("a", "Read"))
        t.on_tool_use(_Block("b", "Grep"))
        assert [c["step"] for c in t.calls] == [1, 2]
        assert t.count == 2
