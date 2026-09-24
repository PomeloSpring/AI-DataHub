"""Qoder SDK 适配器：独立会话、精确工具授权，不保留旧版全权限执行路径。"""
import json
import logging
from typing import AsyncIterator

from services.datamind.execution.adapters.cli_adapter import CLIProcessAdapter
from services.datamind.execution.models import ExecutionResult, ExecutionTask

logger = logging.getLogger(__name__)
_OBS_ASSISTANT_TEXT_MAX = 4000


def _obs_record_assistant(msg) -> None:
    """观测是旁路，失败不影响执行。"""
    try:
        from services.shared import observability
        _obs_record_assistant_output(msg)
        usage = getattr(msg, "usage", None) or {}
        if not isinstance(usage, dict) or not usage:
            return
        observability.record_llm_call(
            name="qoder_llm_request", model_ref=getattr(msg, "model", "") or "",
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            credits=usage.get("credits"), original_credits=usage.get("original_credits"),
            billable=usage.get("billable"),
        )
    except Exception as exc:
        logger.debug("观测记录失败: %s", exc)


def _obs_record_assistant_output(msg) -> None:
    try:
        from services.shared import observability
        texts, thinkings, tools = [], [], []
        for block in getattr(msg, "content", None) or []:
            kind = type(block).__name__
            if kind == "TextBlock" and getattr(block, "text", ""):
                texts.append(block.text)
            elif kind == "ThinkingBlock" and getattr(block, "thinking", ""):
                thinkings.append(block.thinking)
            elif kind == "ToolUseBlock":
                tools.append({"tool": getattr(block, "name", "") or "unknown",
                              "arguments": getattr(block, "input", None) or {}})
        if texts or thinkings or tools:
            observability.record_span(
                kind="assistant", name=f"round_{getattr(msg, 'num_turns', None) or 'output'}",
                model_ref=getattr(msg, "model", "") or "", input_text="",
                output_text=json.dumps({"text": "\n".join(texts), "thinking": "\n".join(thinkings),
                                        "tool_calls": tools}, ensure_ascii=False, default=str)[:_OBS_ASSISTANT_TEXT_MAX],
            )
    except Exception as exc:
        logger.debug("观测记录失败: %s", exc)


def _obs_record_tool_result(ev, arguments: dict) -> None:
    try:
        from services.shared import observability
        error = ev.get("error") or ""
        observability.record_span(
            kind="tool_call", name=ev.get("tool") or "unknown", status="error" if error else "success",
            duration_ms=int((ev.get("elapsed") or 0) * 1000),
            input_text=json.dumps(arguments, ensure_ascii=False, default=str)[:_OBS_ASSISTANT_TEXT_MAX],
            output_text="" if error else (ev.get("output") or "")[:_OBS_ASSISTANT_TEXT_MAX],
            error_text=error[:_OBS_ASSISTANT_TEXT_MAX],
        )
    except Exception as exc:
        logger.debug("观测记录失败: %s", exc)


def _obs_record_result(msg) -> None:
    try:
        from services.shared import observability
        observability.set_session_credits(getattr(msg, "total_credits", None), getattr(msg, "model_usage", None))
        observability.record_span(
            kind="result", name="execution_result",
            status="success" if getattr(msg, "subtype", "") == "success" else "error",
            duration_ms=int(getattr(msg, "duration_ms", 0) or 0),
            output_text=json.dumps({"subtype": getattr(msg, "subtype", ""),
                                    "num_turns": getattr(msg, "num_turns", None),
                                    "total_credits": getattr(msg, "total_credits", None),
                                    "model_usage": getattr(msg, "model_usage", None)}, ensure_ascii=False, default=str),
        )
    except Exception as exc:
        logger.debug("观测记录失败: %s", exc)


class QoderSDKAdapter(CLIProcessAdapter):
    """执行状态只来自服务端会话，不使用进程内长会话池。"""

    def _build_options(self, task: ExecutionTask):
        from services.datamind.execution.secure_sdk import build_options
        return build_options(self, task, "qoder")

    async def execute_stream(self, task: ExecutionTask) -> AsyncIterator[dict]:
        from contextlib import aclosing
        from services.datamind.execution.secure_sdk import execute_stream
        async with aclosing(execute_stream(self, task, "qoder")) as stream:
            async for event in stream:
                yield event

    async def execute(self, task: ExecutionTask) -> ExecutionResult:
        result = ExecutionResult(success=False, error="执行层未返回结果")
        async for event in self.execute_stream(task):
            if event.get("type") == "done":
                result = event["result"]
        return result
