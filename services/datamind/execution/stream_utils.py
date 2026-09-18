"""执行层流式可观测工具 — 工具事件跟踪与输出限长.

参考项目(ChatBI)streamProcessor 的设计:工具输出在 chat 时间线上
展示时做限长与摘要,避免大结果撑爆前端;ToolEventTracker 供
claude/qoder SDK 适配器共享,将 SDK 消息流中的 ToolUseBlock /
ToolResultBlock 映射为 tool_start / tool_result 流事件,并累计
完整 tool_calls 供 done 事件持久化(历史消息回放)。
"""

import logging
import time

logger = logging.getLogger(__name__)

# ── 工具输出展示限长 ──────────────────────────────────────────────

DEFAULT_TOOL_DISPLAY_LIMIT = 2000
TOOL_OUTPUT_LIMITS: dict[str, int] = {
    "execute_sql": 8000,
    "get_table_schema": 8000,
    "search_metadata": 4000,
    "knowledge_search": 4000,
}
TRUNCATE_SUFFIX = "\n... (truncated)"


def tool_display_limit(tool: str) -> int:
    """按工具名取展示长度上限;mcp__server__tool 匹配尾段工具名."""
    name = (tool or "").rsplit("__", 1)[-1]
    return TOOL_OUTPUT_LIMITS.get(name, DEFAULT_TOOL_DISPLAY_LIMIT)


def truncate_for_display(text: str, limit: int) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATE_SUFFIX


def extract_result_text(content) -> str:
    """ToolResultBlock.content → 展示文本.

    content 可能是 str,也可能是 TextBlock/dict 列表(SDK 差异),
    逐项提取 text 后拼接。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or ""))
        return "\n".join(p for p in parts if p)
    return str(content)


def cap_arguments(arguments, max_str_len: int = 300) -> dict:
    """工具入参限长:字符串值截断,避免大参数(如文件内容)撑爆 SSE."""
    if not isinstance(arguments, dict):
        return {}
    out = {}
    for k, v in arguments.items():
        if isinstance(v, str) and len(v) > max_str_len:
            out[k] = v[:max_str_len] + "..."
        else:
            out[k] = v
    return out


# ── 工具事件跟踪器 ────────────────────────────────────────────────


class ToolEventTracker:
    """跟踪 SDK 消息流中的工具调用,产出前端时间线事件.

    用法(适配器 _consume_stream 内):
    - AssistantMessage.ToolUseBlock → on_tool_use()  → yield tool_start
    - UserMessage.ToolResultBlock   → on_tool_result() → yield tool_result
    结束后 calls / count 写入 ExecutionResult.meta,供 done 事件
    携带完整 tool_calls 与统计信息。
    """

    def __init__(self):
        self._t0: dict[str, float] = {}
        self._names: dict[str, str] = {}
        self._records: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.count = 0

    def on_tool_use(self, block) -> dict:
        """ToolUseBlock → tool_start 事件."""
        tool_call_id = getattr(block, "id", "") or f"tc_{self.count + 1}"
        name = getattr(block, "name", "") or "unknown"
        arguments = cap_arguments(getattr(block, "input", None))
        self.count += 1
        self._t0[tool_call_id] = time.time()
        self._names[tool_call_id] = name
        record = {
            "step": self.count,
            "tool": name,
            "arguments": arguments,
            "result": None,
            "result_preview": None,
            "error": None,
            "elapsed": None,
        }
        self._records[tool_call_id] = record
        self.calls.append(record)
        logger.info("[ExecLayer] tool start #%d: %s (%s)", self.count, name, tool_call_id)
        return {
            "type": "tool_start",
            "tool_call_id": tool_call_id,
            "tool": name,
            "arguments": arguments,
        }

    def on_tool_result(self, block) -> dict | None:
        """ToolResultBlock → tool_result 事件;未跟踪过的 id 返回 None."""
        tool_call_id = getattr(block, "tool_use_id", "") or ""
        record = self._records.get(tool_call_id)
        name = self._names.get(tool_call_id) or getattr(block, "name", "") or "unknown"
        raw = extract_result_text(getattr(block, "content", None))
        is_error = bool(getattr(block, "is_error", False))
        output = truncate_for_display(raw, tool_display_limit(name))
        elapsed = None
        t0 = self._t0.pop(tool_call_id, None)
        if t0 is not None:
            elapsed = round(time.time() - t0, 3)
        if record is not None:
            record["result"] = output
            record["result_preview"] = output[:200] if output else None
            record["error"] = output if is_error else None
            record["elapsed"] = elapsed
            if is_error:
                record["result"] = None
        logger.info(
            "[ExecLayer] tool result: %s (%s) error=%s elapsed=%s",
            name, tool_call_id, is_error, elapsed,
        )
        return {
            "type": "tool_result",
            "tool_call_id": tool_call_id,
            "tool": name,
            "output": output,
            "error": output if is_error else "",
            "elapsed": elapsed,
        }

    def update_meta(self, meta: dict) -> None:
        """done 前调用:把完整工具调用清单与统计写入 meta."""
        if self.calls:
            meta["tool_calls"] = self.calls
            meta["tool_call_count"] = self.count

    def arguments_of(self, tool_call_id: str) -> dict:
        """按 tool_call_id 取回工具入参(供可观测记录 SQL/参数等产物输入)."""
        rec = self._records.get(tool_call_id)
        return (rec or {}).get("arguments") or {}
