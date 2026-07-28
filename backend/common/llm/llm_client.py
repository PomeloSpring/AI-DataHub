"""
LLM Client — Multi-provider LLM API client.

Supports Anthropic Claude, OpenAI-compatible APIs.
Reads model config from database (adh_llm_models), falls back to .env.

Langfuse integration: @observe(as_type="generation") decorator on each function.
Langfuse 4.x automatically intercepts Anthropic SDK calls (including stream()),
aggregates chunks, and captures thinking blocks. No manual Langfuse calls needed.
"""

import asyncio
import json
import logging
import time
from typing import Generator
from functools import partial

from anthropic import Anthropic
from langfuse import observe

logger = logging.getLogger(__name__)

# Client cache: model_id -> Anthropic client
_clients: dict[int, Anthropic] = {}


def clear_clients_cache():
    """Clear cached LLM clients. Call after model config changes."""
    _clients.clear()


def _get_client_for_model(model_config: dict) -> Anthropic:
    """Get or create an Anthropic client for the given model config."""
    model_id = model_config.get("id", 0)
    if model_id in _clients:
        return _clients[model_id]

    client = Anthropic(
        api_key=model_config["api_key"],
        base_url=model_config["base_url"],
    )
    _clients[model_id] = client
    return client


def _get_model_config(model_id: int = None) -> dict:
    """Get model config from database API."""
    from backend.api.model_config import get_llm_model_config
    return get_llm_model_config(model_id)


def _get_client() -> Anthropic:
    """Compatibility: get client for the default model."""
    config = _get_model_config()
    return _get_client_for_model(config)


@observe(as_type="generation")
def generate_sql(messages: list[dict], max_tokens: int = 4096, model_id: int = None) -> dict:
    """Call the LLM to generate SQL from a prompt.

    Args:
        messages: List of message dicts with 'role' and 'content'.
        max_tokens: Maximum tokens in the response.
        model_id: Specific model ID to use. None = default model.

    Returns:
        Dict with 'sql', 'thinking', 'tokens'.
    """
    config = _get_model_config(model_id)
    client = _get_client_for_model(config)
    model_name = config["model_name"]
    supports_thinking = bool(config.get("supports_thinking", 1))
    effective_max_tokens = max_tokens or config.get("max_tokens", 4096)

    # Separate system message
    system_text = None
    filtered_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            filtered_messages.append(msg)

    try:
        kwargs = dict(model=model_name, max_tokens=effective_max_tokens, messages=filtered_messages)
        if system_text:
            kwargs["system"] = system_text

        # Try enabling extended thinking
        if supports_thinking:
            try:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2048}
                response = client.messages.create(**kwargs)
            except Exception as thinking_err:
                logger.info("Thinking mode not supported, retrying without: %s", thinking_err)
                kwargs.pop("thinking", None)
                response = client.messages.create(**kwargs)
        else:
            response = client.messages.create(**kwargs)

        # Extract thinking and text
        thinking_text = ""
        raw = ""
        for block in response.content:
            if hasattr(block, "thinking"):
                thinking_text = block.thinking
            elif hasattr(block, "text"):
                raw = block.text.strip()

        if not raw:
            raise RuntimeError("LLM 未返回文本内容")

        # Strip markdown code fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        # Token usage
        usage = getattr(response, "usage", None)
        tokens = {
            "input": getattr(usage, "input_tokens", 0) if usage else 0,
            "output": getattr(usage, "output_tokens", 0) if usage else 0,
        }
        tokens["total"] = tokens["input"] + tokens["output"]

        return {"sql": raw, "thinking": thinking_text, "tokens": tokens}
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        raise RuntimeError(f"LLM 生成失败: {e}") from e


@observe(as_type="generation")
def generate_sql_stream(messages: list[dict], max_tokens: int = 4096, model_id: int = None) -> Generator[tuple, None, None]:
    """Stream LLM generation, yielding (event_type, data) tuples.

    Yields:
        ("thinking", str) — model reasoning text chunk
        ("token", str)    — generated text chunk
        ("done", dict)    — final metadata (tokens usage)
    """
    config = _get_model_config(model_id)
    client = _get_client_for_model(config)
    model_name = config["model_name"]
    supports_thinking = bool(config.get("supports_thinking", 1))
    effective_max_tokens = max_tokens or config.get("max_tokens", 4096)

    system_text = None
    filtered_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            filtered_messages.append(msg)

    kwargs = dict(model=model_name, max_tokens=effective_max_tokens, messages=filtered_messages)
    if system_text:
        kwargs["system"] = system_text

    try:
        if supports_thinking:
            try:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2048}
                stream_ctx = client.messages.stream(**kwargs)
            except Exception as thinking_err:
                logger.info("Thinking mode not supported for streaming, retrying without: %s", thinking_err)
                kwargs.pop("thinking", None)
                stream_ctx = client.messages.stream(**kwargs)
        else:
            stream_ctx = client.messages.stream(**kwargs)

        with stream_ctx as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if hasattr(block, "type") and block.type == "thinking":
                        pass
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if hasattr(delta, "thinking") and delta.thinking:
                        yield ("thinking", delta.thinking)
                    elif hasattr(delta, "text") and delta.text:
                        yield ("token", delta.text)
                elif event.type == "message_stop":
                    pass

            final_message = stream.get_final_message()
            usage = getattr(final_message, "usage", None)
            tokens = {
                "input": getattr(usage, "input_tokens", 0) if usage else 0,
                "output": getattr(usage, "output_tokens", 0) if usage else 0,
            }
            tokens["total"] = tokens["input"] + tokens["output"]
            yield ("done", tokens)

    except Exception as e:
        logger.error("LLM streaming failed: %s", e)
        raise RuntimeError(f"LLM 流式生成失败: {e}") from e


@observe(as_type="generation")
def generate_with_tools(
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 4096,
    model_id: int = None,
) -> dict:
    """Call LLM with tool definitions, supporting tool_use responses.

    Args:
        messages: Conversation messages
        tools: List of tool definitions in Anthropic format
        max_tokens: Max response tokens
        model_id: Model ID to use

    Returns:
        Dict with keys:
        - text: str (final text response, may be empty if tool_use)
        - thinking: str
        - tool_uses: list of {id, name, input} (tool calls requested)
        - tokens: dict
    """
    config = _get_model_config(model_id)
    client = _get_client_for_model(config)
    model_name = config["model_name"]
    supports_thinking = bool(config.get("supports_thinking", 1))
    effective_max_tokens = max_tokens or config.get("max_tokens", 4096)

    system_text = None
    filtered_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            filtered_messages.append(msg)

    kwargs = dict(
        model=model_name,
        max_tokens=effective_max_tokens,
        messages=filtered_messages,
        tools=tools,
    )
    if system_text:
        kwargs["system"] = system_text

    try:
        if supports_thinking:
            try:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2048}
                response = client.messages.create(**kwargs)
            except Exception as thinking_err:
                logger.info("Thinking mode not supported, retrying without: %s", thinking_err)
                kwargs.pop("thinking", None)
                response = client.messages.create(**kwargs)
        else:
            response = client.messages.create(**kwargs)

        thinking_text = ""
        text_parts = []
        tool_uses = []

        for block in response.content:
            if hasattr(block, "thinking"):
                thinking_text = block.thinking
            elif hasattr(block, "text"):
                text_parts.append(block.text.strip())
            elif hasattr(block, "type") and block.type == "tool_use":
                tool_uses.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        usage = getattr(response, "usage", None)
        tokens = {
            "input": getattr(usage, "input_tokens", 0) if usage else 0,
            "output": getattr(usage, "output_tokens", 0) if usage else 0,
        }
        tokens["total"] = tokens["input"] + tokens["output"]

        return {
            "text": "\n".join(text_parts),
            "thinking": thinking_text,
            "tool_uses": tool_uses,
            "tokens": tokens,
            "stop_reason": response.stop_reason,
        }
    except Exception as e:
        logger.error("LLM call with tools failed: %s", e)
        raise RuntimeError(f"LLM 生成失败: {e}") from e


@observe(as_type="generation")
def generate_with_tools_stream(
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 4096,
    model_id: int = None,
) -> Generator[tuple, None, None]:
    """Stream LLM call with tool definitions, yielding events in real time.

    Args:
        messages: Conversation messages (system message included).
        tools: Tool definitions in Anthropic format.
        max_tokens: Max response tokens.
        model_id: Model ID to use.

    Yields:
        ("thinking", str) — thinking text chunk
        ("token", str)    — text chunk
        ("tool_use", {"id": str, "name": str, "input": dict}) — tool call block
        ("done", {"input": int, "output": int, "total": int, "stop_reason": str})
    """
    config = _get_model_config(model_id)
    client = _get_client_for_model(config)
    model_name = config["model_name"]
    supports_thinking = bool(config.get("supports_thinking", 1))
    effective_max_tokens = max_tokens or config.get("max_tokens", 4096)

    system_text = None
    filtered_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            filtered_messages.append(msg)

    kwargs = dict(
        model=model_name,
        max_tokens=effective_max_tokens,
        messages=filtered_messages,
        tools=tools,
    )
    if system_text:
        kwargs["system"] = system_text

    try:
        if supports_thinking:
            try:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2048}
                stream_ctx = client.messages.stream(**kwargs)
            except Exception as thinking_err:
                logger.info("Thinking mode not supported for tool stream, retrying without: %s", thinking_err)
                kwargs.pop("thinking", None)
                stream_ctx = client.messages.stream(**kwargs)
        else:
            stream_ctx = client.messages.stream(**kwargs)

        with stream_ctx as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if hasattr(block, "type") and block.type == "tool_use":
                        pass
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if hasattr(delta, "thinking") and delta.thinking:
                        yield ("thinking", delta.thinking)
                    elif hasattr(delta, "text") and delta.text:
                        yield ("token", delta.text)
                    elif hasattr(delta, "partial_json") and delta.partial_json:
                        pass
                elif event.type == "content_block_stop":
                    pass
                elif event.type == "message_stop":
                    pass

            # After stream completes, extract tool_use blocks from final message
            final_message = stream.get_final_message()
            for block in final_message.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    yield ("tool_use", {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input if isinstance(block.input, dict) else {},
                    })

            usage = getattr(final_message, "usage", None)
            tokens = {
                "input": getattr(usage, "input_tokens", 0) if usage else 0,
                "output": getattr(usage, "output_tokens", 0) if usage else 0,
            }
            tokens["total"] = tokens["input"] + tokens["output"]
            tokens["stop_reason"] = final_message.stop_reason
            yield ("done", tokens)

    except Exception as e:
        logger.error("LLM streaming with tools failed: %s", e)
        raise RuntimeError(f"LLM 流式生成失败: {e}") from e


# ── Async Wrappers ────────────────────────────────────────────────────
# These run synchronous LLM calls in a thread pool to avoid blocking
# the async event loop during SSE streaming.

async def async_generate_sql(messages: list[dict], max_tokens: int = 4096, model_id: int = None) -> dict:
    """Async wrapper for generate_sql — runs in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, partial(generate_sql, messages, max_tokens, model_id)
    )


async def async_generate_with_tools(
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 4096,
    model_id: int = None,
) -> dict:
    """Async wrapper for generate_with_tools — runs in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, partial(generate_with_tools, messages, tools, max_tokens, model_id)
    )
