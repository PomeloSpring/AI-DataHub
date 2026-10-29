"""两种 SDK 共用的受限工具注册、会话生命周期和执行前校验。"""
import asyncio
import json
import logging
import os
import re
import anyio
from contextlib import aclosing
from pathlib import Path

from fastapi import HTTPException
from backend.modules.mind.execution.models import ExecutionResult
from backend.modules.mind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
from backend.modules.mind.execution.tool_policy import check_tool
from backend.modules.mind.execution.session_workspace import run_owned_sync

logger = logging.getLogger(__name__)

# 执行层 CLI 的唯一入口(项目内置 runtime, qodercn 体系): 命令名/空配置一律钉死
# 到该 wrapper，绝不交给 SDK 自由查找 —— SDK 的查找链(QODERCLI_PATH → bundled →
# PATH → ~/.npm-global → ~/.local/bin)会短路到宿主机残留的旧 Global CLI，
# 与 qodercn SDK 协议/目标不匹配，initialize 无响应卡超时(历史缺陷根因)。
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_RUNTIME_CLI = _PROJECT_ROOT / "runtime" / "qodercli" / "bin" / "qodercli"


def _sdk_cli_path(adapter) -> str:
    """解析 SDK 子进程 spawn 的 CLI 路径(按**原始配置意图**判定, 不看解析结果):
    - 配置了显式路径(绝对/相对项目根/~/$VAR)→ 用 cli_adapter 解析后的路径;
      不存在时由 SDK fail-loud(CLINotFoundError), 尊重管理员显式配置;
    - 纯命令名/空 → 项目内置 runtime wrapper(_RUNTIME_CLI); 缺失则显式报错
      (提示跑 setup.sh)。命令名绝不走 PATH 解析 —— 会短路到宿主机残留的旧
      Global CLI(如 ~/.npm-global), 与 qodercn SDK 协议不匹配致 initialize 卡死。
    """
    raw = (getattr(adapter, "config", None) or {}).get("cli_path") or ""
    if os.path.isabs(raw) or "/" in raw or raw.startswith("~"):
        return getattr(adapter, "cli_path", "") or raw
    if not _RUNTIME_CLI.is_file():
        raise FileNotFoundError(
            f"执行层 CLI 缺失: {_RUNTIME_CLI}，请先执行 bash runtime/qodercli/setup.sh")
    return str(_RUNTIME_CLI)


def resolve_access_token(env_map: dict | None = None) -> str:
    """解析执行层访问令牌(qodercn 口径): `QODERCN_PERSONAL_ACCESS_TOKEN` 优先,
    旧变量名 `QODER_PERSONAL_ACCESS_TOKEN` 兜底; 显式 env_map(如 adapter 配置)先于进程环境."""
    m = env_map or {}
    return (m.get("QODERCN_PERSONAL_ACCESS_TOKEN") or m.get("QODER_PERSONAL_ACCESS_TOKEN")
            or os.environ.get("QODERCN_PERSONAL_ACCESS_TOKEN")
            or os.environ.get("QODER_PERSONAL_ACCESS_TOKEN", ""))


# ── 启动期 CLI 核验/预热（一次性，替代每轮重复探测）─────────────────────
_CLI_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _sdk_minimum_runtime_version() -> tuple[int, int, int] | None:
    """读 SDK 自身要求的最低运行时版本; 读不到返回 None(不编造版本号)."""
    try:
        from qodercn_agent_sdk._internal.transport.subprocess_cli import MINIMUM_RUNTIME_VERSION
        match = _CLI_VERSION_RE.search(str(MINIMUM_RUNTIME_VERSION))
        return tuple(int(x) for x in match.groups()) if match else None
    except Exception:  # noqa: BLE001 — SDK 内部常量改名不属于致命错误, 由日志暴露
        logger.warning("无法读取 SDK MINIMUM_RUNTIME_VERSION, 本次启动跳过版本下限比对")
        return None


def verify_runtime_cli(timeout: int = 30) -> dict:
    """启动期核验并预热执行层 CLI; 成功后让 SDK 跳过**每轮**重复的版本探测.

    为什么要前置: qodercn SDK 每次 connect 都会先 spawn 一次 `<cli> -v` 子进程
    (实测 1.3~1.8s、~320MB RSS)，而它的结果只用于 `logger.warning` 且整段被
    `except Exception: pass` 吞掉 —— 每轮付一次 node 冷启动的钱却买一个不会生效的
    检查。CLI 路径本身已由 `_sdk_cli_path` 钉死在项目内置 wrapper，不会漂移。

    口径（不做静默降级）:
    - 核验通过 → 置 `QODER_SKIP_VERSION_CHECK`，运行期不再探测；这一次 spawn 顺带
      把 node runtime 与 CLI bundle 读进 page cache（首轮不再承担全冷启动）。
    - 核验不通过（CLI 缺失 / 非零退出 / 版本解析不出 / 低于 SDK 下限）→ **不置跳过
      位**，保持与今天完全一致的每轮探测行为，并把原因回给调用方显式暴露（web 启动
      ERROR 日志、worker 启动 WARNING）；不静默"当成没问题"。
    """
    import subprocess

    cli = str(_RUNTIME_CLI)
    if not _RUNTIME_CLI.is_file():
        return {"ok": False, "cli_path": cli,
                "reason": f"执行层 CLI 缺失: {cli}，请先执行 bash runtime/qodercli/setup.sh"}
    minimum = _sdk_minimum_runtime_version()
    try:
        proc = subprocess.run([cli, "-v"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "cli_path": cli, "reason": f"版本命令超时({timeout}s): {cli} -v"}
    except OSError as exc:
        return {"ok": False, "cli_path": cli, "reason": f"版本命令无法执行: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "cli_path": cli,
                "reason": f"版本命令退出码 {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:200]}"}
    match = _CLI_VERSION_RE.search((proc.stdout or "") + (proc.stderr or ""))
    if not match:
        return {"ok": False, "cli_path": cli,
                "reason": f"版本输出无法解析: {(proc.stdout or '').strip()[:120]!r}"}
    version = ".".join(match.groups())
    if minimum is not None and tuple(int(x) for x in match.groups()) < minimum:
        return {"ok": False, "cli_path": cli, "version": version,
                "reason": f"运行时版本 {version} 低于 SDK 要求 {'.'.join(map(str, minimum))}，"
                          f"请重跑 bash runtime/qodercli/setup.sh"}
    os.environ["QODER_SKIP_VERSION_CHECK"] = "1"
    return {"ok": True, "cli_path": cli, "version": version, "reason": ""}


def build_options(adapter, task, backend):
    from backend.modules.mind.execution.sdk_tools.compat import load_sdk
    from backend.modules.mind.execution.sdk_tools import build_tool_servers
    from backend.modules.mind.execution.sdk_tools.workspace_tools import build_workspace_server
    from backend.modules.mind.execution.prompt_composer import compose_system_prompt
    from backend.modules.mind.execution import as_bots
    from backend.modules.mind.execution.quick_paths import match_quick_paths
    sdk = load_sdk(backend)
    ctx = task.context
    runtime = ctx.extra.get("secure_runtime")
    if runtime is None:
        raise PermissionError("SDK 启动前必须绑定可信会话")
    policy = runtime.policy
    groups = build_tool_servers(backend, [], selection=policy.selection,
                                function_names=policy.function_names)
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

    env = {k: None for k in os.environ if k not in ("PATH", "LANG", "LC_ALL")}
    home = str(runtime.runtime_dir)
    env.update({"HOME": home, "XDG_CONFIG_HOME": home, "TMPDIR": str(runtime.runtime_dir / "tmp"),
                "QODERCN_CONFIG_DIR": home + "/qoder-cn"})
    prompt = compose_system_prompt(
        policy.as_bot, [policy.as_bot], ctx.username, ctx.user_role,
        # 提示词组装属轮次起点：知识库元信息走配置事实缓存（写时失效 + 短 TTL）。
        # 知识库里“内容”仍由检索工具实时读取，缓存的只是 id/name/kb_type 清单。
        knowledge_bases=as_bots.load_knowledge_bases(
            policy.as_bot.get("knowledge_base_ids") or [], use_cache=True),
        skills=as_bots.load_skills(as_bots.collect_skill_names([policy.as_bot])),
        quick_paths=match_quick_paths(task.question, ctx.workspace_id, ctx.datasource_id),
        user_id=ctx.user_id, workspace_id=ctx.workspace_id, datasource_id=ctx.datasource_id,
        capabilities=policy.manifest(),
        report_theme_id=ctx.extra.get("report_theme") or "",
        site_code=ctx.extra.get("site_code") or "",
        # None → 由 prompt_composer 按会话身份现推（bind_resources 在首次工具调用才跑，
        # 此处不能当成“已算出空集”传空列表，否则数据源拓扑段永远注不进去）
        authorized_sites=ctx.extra.get("authorized_sites"),
        # 本轮问题:多技能绑定时用于决定展开哪几个技能正文(见 prompt_composer._select_skill_bodies)
        question=task.question or "",
    )
    common = dict(tools=[], allowed_tools=sorted(policy.allowed), permission_mode="default",
                  setting_sources=[], cwd=str(runtime.workspace), add_dirs=[], env=env,
                  mcp_servers=servers, agents={}, system_prompt=prompt, resume=runtime.sdk_session_id or None,
                  max_turns=int(adapter.config.get("max_turns") or 20), include_partial_messages=True,
                  can_use_tool=permit, hooks={"PreToolUse": [sdk.HookMatcher(hooks=[before_tool])]})
    # 不要把 SDK options 的 `stderr` 回调接进来：一旦传了它，SDK 会多起一个读取任务，
    # 使 execute_stream.pump 里嵌套的 anyio CancelScope / move_on_after 归属跳任务，
    # 流跑完在退出作用域时报“Attempted to exit a cancel scope that isn't the current
    # task's current cancel scope”——表现为“答案已完整输出但仍报执行未完成”。
    # 可诊断性改由 _execute_stream 错误文案里的 model/cli_path 承担
    # （实测就是靠它定位到“模型下架”）。
    from qodercn_agent_sdk.auth import AccessTokenAuthOptions
    token = resolve_access_token(adapter.config.get("env") or {})
    if not token:
        raise ValueError("执行层凭据未配置(QODERCN_PERSONAL_ACCESS_TOKEN)")
    # 模型优先级：前端选择/AS-BOT.models[0] → 执行层 adapter.model →
    # .env QODER_MODEL (AS-BOT 未绑模型时的兜底) → None (SDK 默认 Auto)。
    # 旧实现只到 adapter.model，AS-BOT 没绑模型且执行层也没配时就直接 Auto，
    # .env 里的 QODER_MODEL 不生效（子进程 env 已被剥除，仅能走 SDK options）。
    from backend.common.config import QODER_MODEL
    model = ctx.extra.get("model_ref") or adapter.model or QODER_MODEL or None
    return sdk.QoderAgentOptions(**common, cli_path=_sdk_cli_path(adapter),
                                model=model,
                                auth=AccessTokenAuthOptions(access_token=token),
                                strict_mcp_config=True, allowed_mcp_server_names=list(servers), skills=[])


def place_attachments(task, runtime):
    """把随消息上传的附件落盘到会话工作区 uploads/,并校验既有工作区文件引用.

    task.attachments 元素两种形态:
    - {"filename","category","size","content": bytes} —— 本次上传,写入 workspace/uploads/;
    - {"path": "uploads/x.png"}                        —— 引用会话工作区既有文件,校验后直用。
    归一为 {filename, category, path(工作区相对), size},供 prompt 清单与 done 事件回带。
    附件即工作区文件,随会话目录清理,无独立附件存储/元数据表。
    """
    if not task.attachments:
        return
    from backend.modules.mind.multimodal import MAX_FILE_SIZE, MAX_FILES_PER_REQUEST, classify_extension
    from backend.modules.mind.multimodal.loader import resolve_workspace_file, write_upload_file

    if len(task.attachments) > MAX_FILES_PER_REQUEST:
        raise ValueError(f"单次最多携带 {MAX_FILES_PER_REQUEST} 个附件")
    output = []
    for att in task.attachments:
        content = att.get("content")
        if content is not None:
            if len(content) > MAX_FILE_SIZE:
                raise ValueError(f"附件超过大小限制(20MB): {att.get('filename', '')}")
            output.append(write_upload_file(runtime.workspace, att.get("filename", ""), content))
        else:
            rel = att.get("path", "")
            target = resolve_workspace_file(runtime.workspace, rel)
            if not target.is_file():
                raise ValueError(f"附件不存在: {rel}")
            size = target.stat().st_size
            if size > MAX_FILE_SIZE:
                raise ValueError(f"附件超过大小限制(20MB): {target.name}")
            output.append({
                "filename": target.name,
                "category": classify_extension(target.suffix) or "document",
                "path": str(target.relative_to(Path(runtime.workspace).resolve())),
                "size": size,
            })
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


# 收尾核验后台任务登记表：done 事件不再等 disconnect/confirm_stopped/finish（历史缺陷：
# 答案已流式输出完、前端因 done 未达而一直转圈）。任务强引用防 GC；失败不静默：
# 会话状态留在 running→下轮 preflight 显式 409 提醒「上一轮仍在停止」，异常进日志。
_teardown_tasks: set = set()


def _spawn_teardown(coro_factory, runtime, label: str) -> None:
    import asyncio

    async def _runner():
        try:
            await coro_factory()
        except Exception:  # noqa: BLE001 — 后台收尾失败只暴露到日志与会话状态，不重投执行
            logger.exception("SDK 后台收尾失败(%s)：会话执行权释放异常，下轮发送将被 409 显式拦截", label)

    task = asyncio.create_task(_runner(), name=f"sdk-teardown:{label}")
    _teardown_tasks.add(task)
    task.add_done_callback(_teardown_tasks.discard)
    if runtime is not None:
        runtime.closing = True


async def _teardown_client(client, runtime, final, result, query_started) -> None:
    """done 发出后的后台收尾：断连、等在跑工具、核验子进程已停、释放执行权。

    领取线程不可被取消；本函数运行在独立后台任务里，不受请求生命周期（断连/取消）影响。
    """
    with anyio.CancelScope(shield=True):
        stopped = client is None
        if runtime is not None:
            runtime.closing = True
        if client is not None:
            try:
                process = getattr(getattr(client, "_transport", None), "_process", None)
                if process is not None and runtime is not None and not runtime.sdk_pid:
                    await run_owned_sync(runtime.record_client, client)
                # disconnect 必须限时: CLI 子进程卡死(如协议不匹配不退)时无限挂起,
                # 收尾永无宁日;超时后强制 kill 并走 confirm_stopped 核验。
                with anyio.move_on_after(15) as dc:
                    await client.disconnect()
                if dc.cancelled_caught:
                    logger.warning("SDK disconnect 超时(15s)，强制终止 CLI 子进程")
                    if process is not None and process.returncode is None:
                        try:
                            process.kill()
                        except Exception:
                            logger.exception("强制终止 CLI 子进程失败")
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
                # 释放失败：会话留在 running，下轮 preflight 会显式 409 提醒用户，
                # 不伪装成已停止（no-silent-degradation）。
                logger.exception("会话停止核验或状态提交失败；执行权未释放，下轮发送将被拦截")


def _obs_phase(name: str, elapsed_s: float) -> None:
    """把回合前置阶段耗时落成 span（kind="phase"）。

    日志里的 [timing] 行只能 grep，管理端 trace 看不到。llm_call 时长口径修正后，
    SDK 建连这类**固定成本**必须在 trace 里单独可见，否则只剩“总时长减去各 span”的
    差值，下一次归因又得回到翻日志。观测旁路，异常一律吞（护栏 §9）。
    """
    try:
        from backend import observability
        observability.record_span(kind="phase", name=name, status="success",
                                  duration_ms=int(elapsed_s * 1000))
    except Exception as exc:  # noqa: BLE001 — 观测不得影响主链路
        logger.debug("phase span 记录失败: %s", exc)


async def _execute_stream(adapter, task, backend):
    import time as _time
    from backend.modules.mind.execution.session_workspace import claim_session
    from backend.modules.mind.execution.sdk_tools.compat import load_sdk
    from backend.modules.mind.execution.stream_utils import ToolEventTracker
    from backend.modules.mind.execution.resource_guard import bind_resources
    runtime = None
    client = None
    context_token = None
    result = None
    final = None
    streamed = False
    done_delivered = False
    query_started = False
    starting_sdk = False
    texts = []
    tracker = ToolEventTracker()
    _t0 = _time.time()
    try:
        runtime = await run_owned_sync(claim_session, task, backend, adapter.config)
        _el = _time.time() - _t0
        logger.info("[timing] claim_session: %.1fms", _el * 1000)
        _obs_phase("claim_session", _el)
        _t1 = _time.time()
        await run_owned_sync(bind_resources, task.context, runtime.policy)
        _el = _time.time() - _t1
        logger.info("[timing] bind_resources: %.1fms", _el * 1000)
        _obs_phase("bind_resources", _el)
        _t1 = _time.time()
        if set(runtime.policy.standard) & {"read", "write", "edit", "glob", "grep", "bash"}:
            from backend.modules.platform.services.sandbox_executor import SessionToolSandbox
            await run_owned_sync(SessionToolSandbox.ensure_available)
        _el = _time.time() - _t1
        logger.info("[timing] sandbox_ensure: %.1fms", _el * 1000)
        _obs_phase("sandbox_ensure", _el)
        _t1 = _time.time()
        await run_owned_sync(place_attachments, task, runtime)
        _el = _time.time() - _t1
        logger.info("[timing] place_attachments: %.1fms", _el * 1000)
        _obs_phase("place_attachments", _el)
        _t1 = _time.time()
        if runtime.policy.external:
            from backend.modules.mind.execution.sdk_tools.external_tools import build_external_servers
            task.context.extra["external_servers"] = await build_external_servers(backend, task.context, runtime)
        _el = _time.time() - _t1
        logger.info("[timing] external_servers: %.1fms", _el * 1000)
        _obs_phase("external_servers", _el)
        _t1 = _time.time()
        options = build_options(adapter, task, backend)
        context_token = set_execution_context(task.context)
        sdk = load_sdk(backend)
        client_type = sdk.QoderSDKClient
        starting_sdk = True
        client = client_type(options=options)
        await client.connect()
        _el = _time.time() - _t1
        logger.info("[timing] sdk_connect: %.1fms", _el * 1000)
        _obs_phase("sdk_connect", _el)
        _t1 = _time.time()
        await run_owned_sync(runtime.record_client, client)
        yield {"type": "capabilities", "data": runtime.policy.manifest()}
        prompt = adapter._prompt_with_attachments(task, include_history=not runtime.sdk_session_id)
        _el = _time.time() - _t1
        logger.info("[timing] prompt_build: %.1fms", _el * 1000)
        _obs_phase("prompt_build", _el)
        query_started = True
        # 模型窗口锚点：前置阶段（会话领取/资源绑定/建连/拼提示词）到此全部做完。
        # 不标注的话，第一条 llm_call 会把这 5~8s 固定成本当成“模型耗时”（历史归因失真根因）。
        from backend import observability
        observability.mark_model_window_start()
        _t1 = _time.time()
        await client.query(prompt)
        _el = _time.time() - _t1
        logger.info("[timing] client_query_sent: %.1fms", _el * 1000)
        _obs_phase("client_query_sent", _el)
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
                from backend.modules.mind.execution.adapters.qoder_sdk_adapter import _obs_record_assistant
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
                        from backend.modules.mind.execution.adapters.qoder_sdk_adapter import _obs_record_tool_result
                        _obs_record_tool_result(event, tracker.arguments_of(event["tool_call_id"]))
                        yield event
            elif kind == "ResultMessage":
                final = msg
                from backend.modules.mind.execution.adapters.qoder_sdk_adapter import _obs_record_result
                _obs_record_result(msg)
        if final is None:
            # 流在收到 ResultMessage 前结束（SDK 子进程崩溃/被杀/传输中断），非模型结果。
            raise RuntimeError("模型执行未返回结果：响应流在 ResultMessage 前结束（SDK 子进程崩溃/中断）")
        if getattr(final, "is_error", False):
            # SDK 报告结构化错误结果（error_max_turns / error_during_execution 等）。
            # 把 subtype/errors/terminal_reason 带进异常文本供服务端日志诊断；
            # 对外（result.error）仍走通用文案，不外泄细节（护栏 §7）。
            # model/cli_path 必须一起发：实测 CLI 独立跑正常、只有经 SDK 传了显式
            # model 时全量失败（num_turns=0），不带这两项时日志里看不出方向，
            # 只能看到“执行未完成”——可诊断性为零。
            detail = "; ".join(getattr(final, "errors", None) or []) or (getattr(final, "result", "") or "")
            raise RuntimeError(
                "模型执行返回错误结果: subtype=%s terminal_reason=%s stop_reason=%s num_turns=%s "
                "model=%s cli=%s detail=%s"
                % (getattr(final, "subtype", "?"), getattr(final, "terminal_reason", "") or "-",
                   getattr(final, "stop_reason", "") or "-", getattr(final, "num_turns", None),
                   getattr(options, "model", None) or "auto",
                   getattr(options, "cli_path", "") or "-", detail[:300]))
        meta = {"capabilities": runtime.policy.manifest(), "num_turns": getattr(final, "num_turns", None),
                "duration_ms": getattr(final, "duration_ms", None),
                # 回带落盘后的附件清单(工作区相对路径),供前端持久化消息历史引用;
                # 仅取已归一项(含 path),绝不携带上传字节
                "attachments": [
                    {"filename": a.get("filename"), "category": a.get("category"),
                     "path": a.get("path"), "size": a.get("size")}
                    for a in (task.attachments or []) if isinstance(a, dict) and a.get("path")
                ]}
        tracker.update_meta(meta)
        result = ExecutionResult(success=True, output=getattr(final, "result", "") or "".join(texts), meta=meta)
    except HTTPException as exc:
        result = ExecutionResult(success=False, error=str(exc.detail), meta={"status_code": exc.status_code})
    except (PermissionError, ValueError) as exc:
        logger.exception("AS-BOT 执行配置或权限错误")
        result = ExecutionResult(success=False, error=("模型执行失败，请联系管理员查看日志" if starting_sdk else str(exc)))
    except Exception:
        logger.exception("AS-BOT 安全执行失败")
        result = ExecutionResult(success=False, error="执行未完成，请检查会话状态、工具配置或联系管理员查看日志")
    finally:
        # 领取线程不可被取消；即使 await 未交回，也必须接管它已写入的 runtime。
        # 断连取消（GeneratorExit/CancelledError 属 BaseException，不走 except 分支）
        # 同样要后台收尾释放执行权，不能只挂在正常路径上。
        runtime = runtime or task.context.extra.get("secure_runtime")
        # done 先行、收尾后置：此时无未交付异常在传播（异常已被 except 接住或正在
        # 断连拆流），yield 合法；若消费者已断开，GeneratorExit 会在本 yield 处
        # 接管，下方移交已在 yield 前完成，执行权释放不会丢。
        # 历史缺陷：done 排在 disconnect/confirm_stopped/finish 之后，CLI 退出前
        # done 发不出去，答案已完整展示前端仍长时间转圈。收尾失败不静默：
        # 执行权留在 running → 下轮 preflight 显式 409 提醒「上一轮仍在停止」。
        if result is not None and not done_delivered:
            _spawn_teardown(lambda: _teardown_client(client, runtime, final, result, query_started),
                            runtime, task.context.extra.get("conversation_id") or "new")
            done_delivered = True
            yield {"type": "done", "result": result}
        elif not done_delivered:
            _spawn_teardown(lambda: _teardown_client(client, runtime, final, result, query_started),
                            runtime, task.context.extra.get("conversation_id") or "new")
        if context_token is not None:
            ExecutionContextVar.reset(context_token)
