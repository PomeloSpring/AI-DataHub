"""两种 SDK 共用的受限工具注册、会话生命周期和执行前校验。"""
import asyncio
import json
import logging
import os
import anyio
from contextlib import aclosing
from pathlib import Path

from fastapi import HTTPException
from services.datamind.execution.models import ExecutionResult
from services.datamind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
from services.datamind.execution.tool_policy import check_tool
from services.datamind.execution.session_workspace import run_owned_sync

logger = logging.getLogger(__name__)


def build_options(adapter, task, backend):
    from services.datamind.execution.sdk_tools.compat import load_sdk
    from services.datamind.execution.sdk_tools import build_tool_servers
    from services.datamind.execution.sdk_tools.workspace_tools import build_workspace_server
    from services.datamind.execution.prompt_composer import compose_system_prompt
    from services.datamind.execution import wakers
    sdk = load_sdk(backend)
    ctx = task.context
    runtime = ctx.extra.get("secure_runtime")
    if runtime is None:
        raise PermissionError("SDK 启动前必须绑定可信会话")
    policy = runtime.policy
    groups = build_tool_servers(backend, [], selection=policy.selection)
    servers = groups["servers"]
    if policy.standard:
        servers["datahub_workspace"] = build_workspace_server(backend, runtime)
    if any(policy.external.values()):
        external = ctx.extra.get("external_servers")
        if external is None:
            raise ValueError("自定义 MCP 工具尚未完成安全初始化")
        servers.update(external)

    async def permit(name, args, context):
        try:
            await run_owned_sync(check_tool, ctx, name)
            return sdk.PermissionResultAllow(updated_input=args)
        except Exception:
            logger.exception("SDK 权限校验拒绝: %s", name)
            return sdk.PermissionResultDeny(message="当前会话未授权该工具或权限已变化")

    async def before_tool(data, tool_use_id, context):
        name = data.get("tool_name", "")
        try:
            await run_owned_sync(check_tool, ctx, name)
            return {}
        except Exception:
            logger.exception("SDK 执行前守卫拒绝: %s", name)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": "当前会话未授权该工具或权限已变化"}}

    env = {k: None if backend == "qoder" else "" for k in os.environ if k not in ("PATH", "LANG", "LC_ALL")}
    home = str(runtime.runtime_dir)
    env.update({"HOME": home, "XDG_CONFIG_HOME": home, "TMPDIR": str(runtime.runtime_dir / "tmp"),
                "QODER_CONFIG_DIR": home + "/qoder", "CLAUDE_CONFIG_DIR": home + "/claude"})
    prompt = compose_system_prompt(
        policy.waker, [policy.waker], ctx.username, ctx.user_role,
        knowledge_bases=wakers.load_knowledge_bases(policy.waker.get("knowledge_base_ids") or []),
        skills=wakers.load_skills(wakers.collect_skill_names([policy.waker])),
        user_id=ctx.user_id, workspace_id=ctx.workspace_id, datasource_id=ctx.datasource_id,
        capabilities=policy.manifest(),
        report_theme_id=ctx.extra.get("report_theme") or "",
    )
    common = dict(tools=[], allowed_tools=sorted(policy.allowed), permission_mode="default",
                  setting_sources=[], cwd=str(runtime.workspace), add_dirs=[], env=env,
                  mcp_servers=servers, agents={}, system_prompt=prompt, resume=runtime.sdk_session_id or None,
                  max_turns=int(adapter.config.get("max_turns") or 20), include_partial_messages=True,
                  can_use_tool=permit, hooks={"PreToolUse": [sdk.HookMatcher(hooks=[before_tool])]})
    if backend == "qoder":
        from qoder_agent_sdk.auth import AccessTokenAuthOptions
        token = (adapter.config.get("env") or {}).get("QODER_PERSONAL_ACCESS_TOKEN") or os.environ.get("QODER_PERSONAL_ACCESS_TOKEN")
        if not token:
            raise ValueError("Qoder 模型凭据未配置")
        return sdk.QoderAgentOptions(**common, cli_path=adapter.cli_path,
                                    model=ctx.extra.get("model_ref") or adapter.model or None,
                                    auth=AccessTokenAuthOptions(access_token=token),
                                    strict_mcp_config=True, allowed_mcp_server_names=list(servers), skills=[])
    llm_env, model = adapter._resolve_llm(task)
    env.update(llm_env)
    return sdk.ClaudeAgentOptions(**common, cli_path=adapter.cli_path, model=model or adapter.model or None)


def materialize_attachments(task, runtime):
    if not task.attachments:
        return
    from services.datamind.multimodal.loader import load_attachments
    ids = [a["id"] for a in task.attachments]
    rows = load_attachments(ids, task.context.user_id)
    if {r["id"] for r in rows} != set(ids):
        raise PermissionError("附件不存在或无权访问")
    output = []
    for row in rows:
        if int(row.get("workspace_id") or 0) != task.context.workspace_id:
            raise PermissionError("附件不属于当前工作空间")
        if int(row.get("size") or 0) > 20_000_000:
            raise ValueError("附件超过会话大小限制")
        if row.get("storage_type") == "object":
            from services.shared.common.object_storage import get_object_storage
            storage = get_object_storage()
            if not storage.is_object_storage:
                raise RuntimeError("附件对象存储不可用")
            data = storage.download_bytes(row["storage_path"])
        else:
            from services.shared.common.config import ADH_UPLOAD_DIR
            source = Path(row["storage_path"]).resolve()
            base = Path(ADH_UPLOAD_DIR).resolve()
            if base not in source.parents or source.stat().st_size > 20_000_000:
                raise PermissionError("附件存储路径无效")
            data = source.read_bytes()
        if data is None or len(data) > 20_000_000:
            raise ValueError("附件缺失或超过大小限制")
        import uuid
        name = "attachment_" + uuid.uuid4().hex + Path(row["filename"]).suffix[:16]
        target = runtime.workspace / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if os.geteuid() == 0:
            os.chown(target, 65534, 65534)
        output.append({"filename": row["filename"], "category": row["category"], "path": "/workspace/" + name})
    task.attachments = output


async def execute_stream(adapter, task, backend):
    """SDK 的连接、接收与关闭由同一任务持有；断流等待清理，不提供跨节点流恢复。"""
    queue = asyncio.Queue(maxsize=64)
    ready = asyncio.get_running_loop().create_future()

    async def pump():
        with anyio.CancelScope() as scope:
            ready.set_result(scope)
            completed = False
            try:
                with anyio.move_on_after(min(task.timeout or 300, 900)):
                    async with aclosing(_execute_stream(adapter, task, backend)) as stream:
                        async for event in stream:
                            await queue.put(event)
                            completed = completed or event.get("type") == "done"
            except Exception:
                logger.exception("SDK 执行任务异常退出")
            if not completed:
                await queue.put({"type": "done", "result": ExecutionResult(
                    success=False, error="执行超时或中断，请检查会话状态；不会自动重放请求")})
            await queue.put(None)

    owner = asyncio.create_task(pump())
    try:
        scope = await asyncio.shield(ready)
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        with anyio.CancelScope(shield=True):
            scope = await asyncio.shield(ready)
            scope.cancel()
            await asyncio.shield(owner)


async def _execute_stream(adapter, task, backend):
    import time as _time
    from services.datamind.execution.session_workspace import claim_session
    from services.datamind.execution.sdk_tools.compat import load_sdk
    from services.datamind.execution.stream_utils import ToolEventTracker
    from services.datamind.execution.resource_guard import bind_resources
    runtime = None
    client = None
    context_token = None
    result = None
    final = None
    streamed = False
    query_started = False
    starting_sdk = False
    texts = []
    tracker = ToolEventTracker()
    _t0 = _time.time()
    try:
        runtime = await run_owned_sync(claim_session, task, backend, adapter.config)
        logger.info("[timing] claim_session: %.1fms", (_time.time() - _t0) * 1000)
        _t1 = _time.time()
        await run_owned_sync(bind_resources, task.context, runtime.policy)
        logger.info("[timing] bind_resources: %.1fms", (_time.time() - _t1) * 1000)
        _t1 = _time.time()
        if set(runtime.policy.standard) & {"read", "write", "edit", "glob", "grep", "bash"}:
            from services.aiplatform.services.sandbox_executor import SessionToolSandbox
            await run_owned_sync(SessionToolSandbox.ensure_available)
        logger.info("[timing] sandbox_ensure: %.1fms", (_time.time() - _t1) * 1000)
        _t1 = _time.time()
        await run_owned_sync(materialize_attachments, task, runtime)
        logger.info("[timing] materialize_attachments: %.1fms", (_time.time() - _t1) * 1000)
        _t1 = _time.time()
        if runtime.policy.external:
            from services.datamind.execution.sdk_tools.external_tools import build_external_servers
            task.context.extra["external_servers"] = await build_external_servers(backend, task.context, runtime)
        logger.info("[timing] external_servers: %.1fms", (_time.time() - _t1) * 1000)
        _t1 = _time.time()
        options = build_options(adapter, task, backend)
        context_token = set_execution_context(task.context)
        sdk = load_sdk(backend)
        client_type = sdk.QoderSDKClient if backend == "qoder" else sdk.ClaudeSDKClient
        starting_sdk = True
        client = client_type(options=options)
        await client.connect()
        logger.info("[timing] sdk_connect: %.1fms", (_time.time() - _t1) * 1000)
        _t1 = _time.time()
        await run_owned_sync(runtime.record_client, client)
        yield {"type": "capabilities", "data": runtime.policy.manifest()}
        prompt = adapter._prompt_with_attachments(task, include_history=not runtime.sdk_session_id)
        logger.info("[timing] prompt_build: %.1fms", (_time.time() - _t1) * 1000)
        query_started = True
        _t1 = _time.time()
        await client.query(prompt)
        logger.info("[timing] client_query_sent: %.1fms", (_time.time() - _t1) * 1000)
        iterator = client.receive_response().__aiter__()
        while True:
            try:
                msg = await iterator.__anext__()
            except StopAsyncIteration:
                break
            kind = type(msg).__name__
            if kind == "StreamEvent":
                delta = (getattr(msg, "event", {}) or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    streamed = True
                    yield {"type": "token", "text": delta["text"]}
                elif delta.get("type") == "thinking_delta":
                    yield {"type": "thinking", "text": delta.get("thinking", "")}
            elif kind == "AssistantMessage":
                from services.datamind.execution.adapters.qoder_sdk_adapter import _obs_record_assistant
                _obs_record_assistant(msg)
                for block in getattr(msg, "content", []) or []:
                    if type(block).__name__ == "TextBlock":
                        texts.append(block.text)
                        if not streamed:
                            yield {"type": "token", "text": block.text}
                    elif type(block).__name__ == "ToolUseBlock":
                        yield tracker.on_tool_use(block)
            elif kind == "UserMessage":
                for block in (msg.content if isinstance(msg.content, list) else []):
                    if type(block).__name__ == "ToolResultBlock":
                        event = tracker.on_tool_result(block)
                        from services.datamind.execution.adapters.qoder_sdk_adapter import _obs_record_tool_result
                        _obs_record_tool_result(event, tracker.arguments_of(event["tool_call_id"]))
                        yield event
            elif kind == "ResultMessage":
                final = msg
                from services.datamind.execution.adapters.qoder_sdk_adapter import _obs_record_result
                _obs_record_result(msg)
        if final is None or getattr(final, "is_error", False):
            raise RuntimeError("模型执行未正常完成")
        meta = {"capabilities": runtime.policy.manifest(), "num_turns": getattr(final, "num_turns", None),
                "duration_ms": getattr(final, "duration_ms", None)}
        tracker.update_meta(meta)
        result = ExecutionResult(success=True, output=getattr(final, "result", "") or "".join(texts), meta=meta)
    except HTTPException as exc:
        result = ExecutionResult(success=False, error=str(exc.detail), meta={"status_code": exc.status_code})
    except (PermissionError, ValueError) as exc:
        logger.exception("Waker 执行配置或权限错误")
        result = ExecutionResult(success=False, error=("模型执行失败，请联系管理员查看日志" if starting_sdk else str(exc)))
    except Exception:
        logger.exception("Waker 安全执行失败")
        result = ExecutionResult(success=False, error="执行未完成，请检查会话状态、工具配置或联系管理员查看日志")
    finally:
        # 领取线程不可被取消；即使 await 未交回，也必须接管它已写入的 runtime。
        runtime = runtime or task.context.extra.get("secure_runtime")
        with anyio.CancelScope(shield=True):
            stopped = client is None
            if runtime is not None:
                runtime.closing = True
            if client is not None:
                try:
                    process = getattr(getattr(client, "_transport", None), "_process", None)
                    if process is not None and runtime is not None and not runtime.sdk_pid:
                        await run_owned_sync(runtime.record_client, client)
                    await client.disconnect()
                    stopped = True
                except BaseException:
                    logger.exception("无法确认 SDK 已停止，会话保持阻断")
            if runtime is not None and stopped:
                try:
                    pending = list(runtime.tool_tasks)
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    await run_owned_sync(runtime.confirm_stopped)
                    await run_owned_sync(runtime.finish, getattr(final, "session_id", "") if final else "",
                                         bool(result and result.success), not query_started)
                except Exception:
                    logger.exception("会话停止核验或状态提交失败")
                    stopped = False
            if runtime is not None and not stopped:
                result = ExecutionResult(success=False, error="无法确认执行已停止，会话已阻断，请联系管理员")
            if context_token is not None:
                ExecutionContextVar.reset(context_token)
    if result is not None:
        yield {"type": "done", "result": result}
