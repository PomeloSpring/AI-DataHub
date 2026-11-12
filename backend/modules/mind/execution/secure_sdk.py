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
from backend.modules.mind.execution import capacity
from backend.modules.mind.execution.models import ExecutionResult
from backend.modules.mind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
from backend.modules.mind.execution.tool_policy import check_tool
from backend.modules.mind.execution.session_workspace import run_owned_sync

logger = logging.getLogger(__name__)


class ProviderBusyError(RuntimeError):
    """模型服务繁忙/限流且本轮未能完成（可恢复类失败）。

    单独一个异常是为了把“平台过载”与“执行配置错了”分开：前者对用户是可行动的
    （稍后重试/换模型），后者只能找管理员。`args[0]` 是面向用户的可读中文，
    `detail`（含 subtype/errors）只进服务端日志，不外泄（护栏 §7）。
    """

    def __init__(self, user_message: str, *, kind: str, retry_after_s=None,
                 attempts: int = 0, detail: str = ""):
        super().__init__(user_message)
        self.kind = kind
        self.retry_after_s = retry_after_s
        self.attempts = attempts
        self.detail = detail


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
            f"Harness CLI 缺失: {_RUNTIME_CLI}，请先执行 bash runtime/qodercli/setup.sh")
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
                "reason": f"Harness CLI 缺失: {cli}，请先执行 bash runtime/qodercli/setup.sh"}
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
    from backend.modules.mind.execution.quick_paths import match_quick_paths, paths_by_ids
    from backend.modules.mind.execution.tool_policy import is_system_scope
    from backend.core import scenario_access
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

    # ── 本轮工具轮次上限（本仓侧强制）────────────────────────────
    # 取证（会话 1783069459151，logs/probe_max_turns_semantics.py 复核）：交互 resume 链路上
    # CLI 自报的 num_turns 是**会话轮**而不是工具轮 —— 成功回合 num_turns=6 时实际发生了
    # 17 次工具调用 / 27 次模型请求；三个撞 300s 墙钟的回合也从未回过 error_max_turns。
    # 也就是说 options 里的 max_turns=20 在这条链路上不构成约束，长回合只会被墙钟硬切
    # （整轮答案丢弃）。这里把配置承诺的上限按 tool start 计数变成真约束：超阈值不再放行
    # 新工具，回给模型的是一条收尾指令 —— 用户拿到的是「带可见告警的完整结论」，
    # 而不是「跑到 300s 被中止」。上限值沿用执行层配置 `max_turns`（缺省 20）。
    rounds = {"limit": int(adapter.config.get("max_turns") or DEFAULT_MAX_TOOL_ROUNDS),
              "count": 0, "blocked": 0}
    ctx.extra["_tool_rounds"] = rounds

    async def before_tool(data, tool_use_id, context):
        name = data.get("tool_name", "")
        allowed, reason = enforce_tool_round_limit(rounds, name)
        if not allowed:
            logger.warning("本轮工具调用已达上限 %s 次，拒绝第 %s 次调用并要求模型收尾: %s",
                           rounds["limit"], rounds["count"], name)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        try:
            await run_owned_sync(check_tool, ctx, name)
            return {}
        except Exception:
            logger.exception("SDK 执行前守卫拒绝: %s", name)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": "当前会话未授权该工具或权限已变化"}}

    # 上下文压缩可见性（PreCompact / PostCompact）
    # CLI 的 autoCompact 会在回合中间自行压缩上下文（阈值与触发时机归 CLI，平台不接管，
    # 见 context_usage 模块头）。用户视角只看到“这一轮突然多等十几秒且不知道为什么”，
    # 所以这件事必须透出来：hook 只登记事件，由 _execute_stream 转成 SSE type=context。
    # 事件放 ctx.extra 列表而不是跨任务队列：hook 回调与消息循环同属 pump 任务、同一事件循环，
    # 与 _tool_rounds 用的是同一套共享方式。
    context_events = ctx.extra.setdefault("_context_events", [])

    async def on_pre_compact(data, tool_use_id, context):
        context_events.append({"event": "compacting",
                               "trigger": str((data or {}).get("trigger") or "auto")})
        _obs_compact_span("start")
        return {}

    async def on_post_compact(data, tool_use_id, context):
        context_events.append({"event": "compacted",
                               "trigger": str((data or {}).get("trigger") or "auto"),
                               "summary_chars": len(str((data or {}).get("compact_summary") or ""))})
        _obs_compact_span("end")
        return {}

    env = {k: None for k in os.environ if k not in ("PATH", "LANG", "LC_ALL")}
    home = str(runtime.runtime_dir)
    env.update({"HOME": home, "XDG_CONFIG_HOME": home, "TMPDIR": str(runtime.runtime_dir / "tmp"),
                "QODERCN_CONFIG_DIR": home + "/qoder-cn"})

    # 分析场景（quick_path）的主体门控：与 `/` 候选、管理页可见性说明同一个判定函数。
    # 不能只过滤展示层 —— 否则靠触发词就能把别人主体的场景正文（含对象/指标编排）
    # 带进本轮提示词，那是越权信息面。空授权 = 只看得见「不限主体」的场景（fail-closed）。
    allowed_subjects = scenario_access.visible_subject_keys(
        ctx.user_id, ctx.workspace_id, role=ctx.user_role or "",
        system_scope=is_system_scope(policy))
    _auto_hits = match_quick_paths(task.question, ctx.workspace_id, ctx.datasource_id,
                                   allowed_subjects=allowed_subjects)
    # 用户用 `/` 显式选中的场景：不看触发词也强制注入；被拒的连同原因交给提示词显式告知
    _pinned, _scene_rejected = paths_by_ids(
        list(ctx.extra.get("selected_scenes") or []), allowed_subjects=allowed_subjects)
    _seen_ids = {str(p.get("id")) for p in _pinned}
    # 合并时保留 pinned 标记（paths_by_ids 已打）且置顶：提示词据此分档措辞——
    # “用户点名的场景”不能跟“触发词猜中的建议”混成同一口径，否则模型会把它当噪音
    # （历史缺陷：选了场景、方案也注入了，模型仍按通用清单反问）。
    quick_paths = _pinned + [p for p in _auto_hits if str(p.get("id")) not in _seen_ids]
    # 指定场景的作业行另存一份：它要拼进**回合提示词**而不只靠系统提示词（历史缺陷：
    # 系统提示词里中部命令式 + 尾部复述都到位，模型仍回落到通用反问，见 scene_turn_directive）。
    from backend.modules.mind.execution.prompt_composer import scene_turn_directive
    ctx.extra["_scene_turn_directive"] = scene_turn_directive(
        "、".join(str(p.get("title") or "").strip() for p in _pinned
                  if str(p.get("title") or "").strip()))
    # 子进程环境隔离的两种实现（意图同一个：宿主环境的变量一律不得流进 CLI）：
    #   qoder SDK : 接受 {name: None} 作为“剥掉该变量”的声明；
    #   claude SDK: 先**继承 os.environ** 再用 options.env 覆盖（见其 subprocess_cli.connect），
    #               所以 None 会让 spawn 直接 TypeError，而“删掉键”等于把宿主真实值
    #               原样留给子进程（凭据泄漏）。claude 分支里把剥除意图翻译成显式置空。
    prompt = compose_system_prompt(
        policy.as_bot, [policy.as_bot], ctx.username, ctx.user_role,
        # 提示词组装属轮次起点：知识库元信息走配置事实缓存（写时失效 + 短 TTL）。
        # 知识库里“内容”仍由检索工具实时读取，缓存的只是 id/name/kb_type 清单。
        knowledge_bases=as_bots.load_knowledge_bases(
            policy.as_bot.get("knowledge_base_ids") or [], use_cache=True),
        skills=as_bots.load_skills(as_bots.collect_skill_names([policy.as_bot])),
        quick_paths=quick_paths,
        # 选了但没生效的场景（不存在/未启用/主体无权）：必须写进提示词让用户看见，
        # 不能默默换个方案把答案交出去（no-silent-degradation）
        scene_rejections=_scene_rejected,
        user_id=ctx.user_id, workspace_id=ctx.workspace_id, datasource_id=ctx.datasource_id,
        capabilities=policy.manifest(),
        report_theme_id=ctx.extra.get("report_theme") or "",
        site_code=ctx.extra.get("site_code") or "",
        # None → 由 prompt_composer 按会话身份现推（bind_resources 在首次工具调用才跑，
        # 此处不能当成“已算出空集”传空列表，否则数据源拓扑段永远注不进去）
        authorized_sites=ctx.extra.get("authorized_sites"),
        # 本轮问题:多技能绑定时用于决定展开哪几个技能正文(见 prompt_composer._select_skill_bodies)
        question=task.question or "",
        # 聊天输入框 `/` 选中的技能与指令(服务端字段, 不进 LLM 视野的内容只走提示词):
        # 指定技能强制展开正文, 指令翻译成作业要求; 两者都不改变任何权限裁决。
        pinned_skills=list(ctx.extra.get("selected_skills") or []),
        user_directive=str(ctx.extra.get("slash_directive") or ""),
        # 问答模式（深度分析/快速问答）：由 prompt_composer 同源展开为剧本与作业要求。
        # 目前它只决定本轮作业要求，不绕过任何工具侧权限裁决（模式切换≠开关能力；
        # 按模式 deny_tools / 收紧轮次上限的硬门控尚未接线）。
        chat_mode=str(ctx.extra.get("chat_mode") or ""),
    )
    common = dict(tools=[], allowed_tools=sorted(policy.allowed), permission_mode="default",
                  setting_sources=[], cwd=str(runtime.workspace), add_dirs=[], env=env,
                  mcp_servers=servers, agents={}, system_prompt=prompt, resume=runtime.sdk_session_id or None,
                  max_turns=int(adapter.config.get("max_turns") or 20), include_partial_messages=True,
                  can_use_tool=permit,
                  hooks={"PreToolUse": [sdk.HookMatcher(hooks=[before_tool])],
                         "PreCompact": [sdk.HookMatcher(hooks=[on_pre_compact])],
                         "PostCompact": [sdk.HookMatcher(hooks=[on_post_compact])]})
    # 不要把 SDK options 的 `stderr` 回调接进来：一旦传了它，SDK 会多起一个读取任务，
    # 使 execute_stream.pump 里嵌套的 anyio CancelScope / move_on_after 归属跳任务，
    # 流跑完在退出作用域时报“Attempted to exit a cancel scope that isn't the current
    # task's current cancel scope”——表现为“答案已完整输出但仍报执行未完成”。
    # 可诊断性改由 _execute_stream 错误文案里的 model/cli_path 承担
    # （实测就是靠它定位到“模型下架”）。
    # 模型优先级：前端选择/AS-BOT.models[0] → 执行层 adapter.model →
    # .env QODER_MODEL (AS-BOT 未绑模型时的兜底) → None (SDK 默认 Auto)。
    # 旧实现只到 adapter.model，AS-BOT 没绑模型且执行层也没配时就直接 Auto，
    # .env 里的 QODER_MODEL 不生效（子进程 env 已被剥除，仅能走 SDK options）。
    from backend.common.config import QODER_MODEL
    model = ctx.extra.get("model_ref") or adapter.model or QODER_MODEL or None

    # 可观测登记“本回合实际跑在哪条层、哪个模型通道”：两条通道的计量单位不同
    # （Qoder=credit 不报 token / 直连=token 没有 credit），不分开就会汇总错。
    # 旁路调用：失败不影响执行（护栏 §9）。
    from backend import observability
    observability.set_execution_channel(
        "anthropic-direct" if backend == "claude" else "qoder-gateway",
        layer_id=int((adapter.config or {}).get("_layer_id") or 0),
        layer_name=getattr(adapter, "name", "") or "")

    # claude 后端：Anthropic 协议直连模型供应商（凭据来自模型中心），**不需要 Qoder 账号**，
    # 因此不受企业 BYOK 开关约束。分支必须在取 token 之前返回——直连链路不该因为
    # 没配 QODERCN_PERSONAL_ACCESS_TOKEN 就被拦下。
    if backend == "claude":
        return _build_claude_options(sdk, adapter, common, env, home, model, ctx)

    token = resolve_access_token(adapter.config.get("env") or {})
    if not token:
        raise ValueError("Harness 凭据未配置(QODERCN_PERSONAL_ACCESS_TOKEN)")
    from qodercn_agent_sdk.auth import AccessTokenAuthOptions
    return sdk.QoderAgentOptions(**common, cli_path=_sdk_cli_path(adapter),
                                model=model,
                                auth=AccessTokenAuthOptions(access_token=token),
                                strict_mcp_config=True, allowed_mcp_server_names=list(servers), skills=[])


def _build_claude_options(sdk, adapter, common: dict, env: dict, home: str, model, ctx):
    """claude 后端 options：把模型中心的供应商配置注入子进程 env.

    三条纪律：
    1. **不做静默改道**：模型没在模型中心登记成可用的直连配置时直接报错，而不是
       "回退去走 Qoder 网关"——那会让用户看到 Credits 消耗却以为在用百炼。
    2. **裁剪必须显式**：字段按 SDK options 的真实签名裁剪，只允许已知语义等价项被省略；
       将来 common 新增字段而 claude 侧不支持时会报错，不静默少传。
    3. **密钥只进 env**：ANTHROPIC_AUTH_TOKEN 只出现在子进程环境变量里，
       不进提示词/日志/工具返回（护栏 §7）。
    """
    from backend.modules.mind.execution import anthropic_direct as ad
    from backend.modules.mind.execution.sdk_tools.compat import sdk_options_class, sdk_options_fields

    channel = ad.resolve_direct_channel(model or "")
    if channel is None:
        raise ad.DirectChannelError(
            f"Harness 后端是 claude（供应商直连），但模型「{model or '未指定'}」未在模型中心登记"
            "可用的 API 地址与 API Key。请在「模型中心 · 模型配置」补全该模型，"
            "或把该 Harness 改回 qoder 后端（走 Qoder 网关与 Credits）。")
    runtime = ctx.extra.get("secure_runtime")
    # env 注入 + 会话参数（首轮 session_id / 已有转录则 resume）走同一个实现，
    # 上下文压缩链路（context_usage）也复用它，避免两处各写一份而压缩后丢上下文。
    direct = ad.apply_direct_channel(env, home, model,
                                     getattr(runtime, "sdk_session_id", "") or "")
    channel = direct.pop("_adh_channel")
    new_cli_session = direct.pop("_adh_new_cli_session", False)

    opts_cls = sdk_options_class(sdk)
    supported = sdk_options_fields(sdk)
    # allowed_mcp_server_names 是 qodercn 独有字段：claude 侧由「只传本会话 servers +
    # strict_mcp_config=True」等价承担（不加载文件系统里的其它 MCP 配置）。
    benign_drops = {"allowed_mcp_server_names"}
    dropped = set(common) - supported
    unknown = dropped - benign_drops
    if unknown:
        raise ValueError(f"claude 后端 options 不支持这些字段，需人工确认语义后再接: {sorted(unknown)}")
    kwargs = {k: v for k, v in common.items() if k in supported}
    # 会话参数不能直接沿用 qoder 那套：qoder 拿本系统 32 位 hex 会话 id 做 resume（不存在则自建），
    # 而 Claude CLI 只认带横线的 UUID，且**不存在就报错**——实测结论见
    # anthropic_direct.cli_session_id 文档，因此这里丢掉 common 里的 resume，由 direct 给。
    kwargs.pop("resume", None)
    kwargs.update(direct)
    # 新建 CLI 会话时本轮还没有可用转录：提示词必须自己带上历史，否则多轮上下文静默丢失
    # （qoder 链路靠 resume 拿历史，include_history 因此为 False）。
    ctx.extra["_adh_new_cli_session"] = new_cli_session
    cli_path = ad.resolve_claude_cli_path(adapter.config.get("cli_path") or "")
    # 日志只记通道与实际生效的 CLI/模型（可诊断性），密钥不在其中
    logger.info("[exec] claude 直连通道: model=%s base_url=%s cli=%s",
                channel["model"], channel["base_url"], cli_path)
    # 通道归属登记到会话上下文：直连回合不产生 Qoder Credits，可观测页必须能区分
    # “Credits=0 因为走的是供应商直连”与“Credits=0 因为没跑模型”（否则成本口径会误读）。
    # 只存脱敏摘要，env（含密钥）不进 ctx.extra —— 它会进审计/诊断结构。
    ctx.extra["llm_channel"] = {"channel": channel["channel"], "provider": channel["provider"],
                                "model": channel["model"], "base_url": channel["base_url"]}
    try:
        from backend import observability
        observability.record_span(kind="channel", name=channel["channel"],
                                  model_ref=channel["model"], input_text="",
                                  output_text=json.dumps(ad.redact_secrets(ctx.extra["llm_channel"]),
                                                         ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — 观测是旁路，不得影响取数主链路
        logger.debug("通道登记失败（不影响执行）: %s", exc)
    # model / strict_mcp_config / 会话参数已由 direct 并入 kwargs（重复传会撞关键字参数）
    return opts_cls(**kwargs, cli_path=cli_path)


def place_attachments(task, runtime):
    """把随消息上传的附件落盘到会话工作区 uploads/,并校验既有工作区文件引用.

    task.attachments 元素三种形态:
    - {"filename","category","size","content": bytes} —— 本次上传,写入 workspace/uploads/;
    - {"path": "uploads/x.png"}                        —— 引用会话工作区既有文件,校验后直用;
    - {"cloud_asset_id": "..."}                        —— 聊天框 `@` 的归档资产:从对象存储
      取回内容再落盘 uploads/（副本进本会话工作区,执行层按绝对路径读,与上传附件同权）。
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
        asset_id = att.get("cloud_asset_id")
        if asset_id:
            # 属主校验在取数 SQL 条件里(user_id 来自服务端ExecutionContext, 不信请求体)
            from backend.modules.mind.api.user_assets import fetch_asset_bytes
            uid = int(getattr(task.context, "user_id", 0) or 0)
            name, data = fetch_asset_bytes(str(asset_id), uid)
            if len(data) > MAX_FILE_SIZE:
                raise ValueError(f"附件超过大小限制(20MB): {name}")
            output.append(write_upload_file(runtime.workspace, att.get("filename") or name, data))
            continue
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


# 本轮工具调用次数上限的执行层默认值。注意：配置项名字沿用 `max_turns`，但**本仓侧
# 计的是工具轮次**，因为实测 CLI 在交互 resume 链路上不按工具轮次执行这个配置
# （见 build_options 里的取证说明）。
DEFAULT_MAX_TOOL_ROUNDS = 20

# 回合墙钟的服务端天花板（执行层配置再大也不越过）
_TURN_WALL_CEILING_S = 900

# 触达回合时长上限的取数类工具（用于把"为什么被中止"如实讲清楚）
_QUERY_TOOLS = ("execute_sql", "run_semantic_query", "query_dataset", "check_sql")


def enforce_tool_round_limit(rounds: dict, name: str) -> tuple[bool, str]:
    """计一次工具调用并裁决是否放行：返回 (放行?, 拒绝原因)。

    计数口径 = PreToolUse 触发的每一次工具调用（含被权限拒的那些，它们同样占一轮）。
    超阈值不再放行，把收尾指令回给模型 —— 这是**主动收尾**，不是将答案截断丢弃：
    模型仍会产出一轮完整结论，用户在 done.warnings 里看得到本轮没收尾完。
    """
    rounds["count"] = int(rounds.get("count") or 0) + 1
    limit = int(rounds.get("limit") or DEFAULT_MAX_TOOL_ROUNDS)
    if rounds["count"] <= limit:
        return True, ""
    rounds["blocked"] = int(rounds.get("blocked") or 0) + 1
    return False, _wrap_up_reason(limit)


def _wrap_up_reason(limit: int) -> str:
    """回给模型的收尾指令：既是不放行新工具的**原因**，也是可执行的下一步。"""
    return (f"本轮工具调用已达上限 {limit} 次（执行层配置的 max_turns），不要再发起新的工具调用。"
            "请立即基于已获得的结果给出结论：写清已覆盖的范围、仍未完成的部分，"
            "并提示用户继续追问以完成剩余分析。")


def _round_limit_warning(rounds: dict) -> str:
    """达上限时给用户的可见提示（no-silent-degradation：收尾不是静默截断）。"""
    if not rounds or not rounds.get("blocked"):
        return ""
    return (f"本轮工具调用已达上限 {rounds.get('limit')} 次，模型基于已完成的 "
            f"{rounds.get('count', 0) - rounds.get('blocked', 0)} 次查询给出结论；"
            "未覆盖的部分请继续追问，或让多站点同口径查询改用 execute_sql 的 sites 一次扇出"
            "（服务端并发，占 1 次调用）。")


def _abort_reason(limit_s: int, elapsed_ms: int, tool_calls: int, queries: int, timed_out: bool) -> str:
    """中止原因必须自带诊断（no-silent-degradation §1/§2）。

    历史缺陷：三条只写"执行超时或中断"，用户在会话里只能反问"是单次查询慢还是轮次多"
    （会话 1783069459151 turn[8]），而答案本来就在服务端手里。这里把**上限值、已跑时长、
    工具调用次数、取数次数**一并回给前端，并给出下一步（合并多站查询/继续追问），
    同时保留原句作为可识别前缀（前端错误卡与历史用例都按它匹配）。
    """
    base = "执行超时或中断，请检查会话状态；不会自动重放请求"
    if not timed_out:
        return base
    return (base
            + f"（本轮已跑满 Harness 时长上限 {limit_s}s 被中止：已发出 {tool_calls} 次工具调用，"
              f"其中取数 {queries} 次，实际耗时 {round(elapsed_ms / 1000)}s。"
              "已输出的正文仍保留；多站同口径查询请用 execute_sql 的 sites/datasources 一次扇出"
              "（服务端并发，不必逐站发），或直接追问让我继续完成剩余部分）")


async def execute_stream(adapter, task, backend):
    """SDK 的连接、接收与关闭由同一任务持有；断流等待清理，不提供跨节点流恢复。"""
    queue = asyncio.Queue(maxsize=64)
    ready = asyncio.get_running_loop().create_future()

    async def pump():
        import time as _time
        with anyio.CancelScope() as scope:
            ready.set_result(scope)
            completed = False
            # 墙钟上限取**执行层配置的 timeout**（由 chat_service 写进 task.timeout），
            # 900s 是服务端天花板：再长也只是让一个已经没人看的回合继续占着会话执行权。
            limit_s = min(int(task.timeout or 300), _TURN_WALL_CEILING_S)
            tool_calls = 0
            queries = 0
            t0 = _time.monotonic()
            timed_out = False
            try:
                with anyio.move_on_after(limit_s) as timeout_scope:
                    async with aclosing(_execute_stream(adapter, task, backend)) as stream:
                        async for event in stream:
                            etype = event.get("type")
                            if etype == "tool_start":
                                tool_calls += 1
                                tool_name = str(event.get("tool") or "")
                                if any(tool_name.endswith(q) for q in _QUERY_TOOLS):
                                    queries += 1
                            await queue.put(event)
                            completed = completed or etype == "done"
                timed_out = bool(timeout_scope.cancelled_caught)
            except Exception:
                logger.exception("SDK 执行任务异常退出")
            if not completed:
                elapsed_ms = int((_time.monotonic() - t0) * 1000)
                await queue.put({"type": "done", "result": ExecutionResult(
                    success=False,
                    error=_abort_reason(limit_s, elapsed_ms, tool_calls, queries, timed_out),
                    # 服务端口径的回合耗时与进度计数：前端 elapsed 只是浏览器时钟，
                    # 两者不同源会让"跑满 300s"看起来像 277s（本会话的归因混乱源）。
                    # 键名与成功路径的 meta 对齐：`tool_call_count` 是**次数**，
                    # `tool_calls` 在成功路径下是清单数组，两者混用会让下游 len() 炸。
                    meta={"elapsed_ms": elapsed_ms, "tool_call_count": tool_calls,
                          "query_count": queries, "timed_out": timed_out,
                          # 前端错误卡据此区分“跑满时长上限”与“被中止”（两者文案与出口不同），
                          # 与 capacity.classify_failure 同一套 kind 取值。
                          "error_kind": "timeout" if timed_out else "aborted",
                          "turn_limit_seconds": limit_s})})
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


def _fallback_turn_record(task, texts, tracker, result, message_uuid: str) -> dict | None:
    """构造服务端兜底落库所需的本回合记录（内容 = 用户已经看到的流式正文）。

    会话 1783069459150：回合跑满 126s、烧掉 12 次模型请求，用户中途断连后前端那次
    PUT 永不到达，`messages=[]` —— 产出只在服务端内存里存在过，刷新即蒸发。
    收尾任务持有 result/tracker/texts，是唯一还能把这份用户资产写回会话的地方。
    """
    ctx = task.context
    cid = int((ctx.extra or {}).get("conversation_id") or 0)
    if cid <= 0:
        return None
    meta = (result.meta if result is not None else {}) or {}
    output = (result.output or "") if (result is not None and result.success) else ""
    error = "" if (result is not None and result.success) else ((result.error if result else "") or "")
    content = output or "".join(texts or [])
    if not content.strip() and not error.strip() and not (tracker and tracker.calls):
        return None   # 真正的空回合：没什么可兜的（persist_turn_fallback 同口径再兜一次）
    _from = observability_turn_elapsed_ms()
    message = {
        "role": "assistant",
        "content": content or "",
        "question": task.question or "",
        "elapsed_ms": int(meta.get("elapsed_ms") or _from or 0),
        "tool_calls": (tracker.calls if tracker else []) or [],
        "warnings": list(meta.get("warnings") or []),
    }
    if error.strip():
        message["error"] = error.strip()
    if meta.get("num_turns") is not None or meta.get("tool_call_count"):
        message["executionStats"] = {k: meta.get(k) for k in
                                     ("num_turns", "tool_call_count", "duration_ms",
                                      "elapsed_ms", "queries", "timed_out", "turn_limit_seconds")
                                     if meta.get(k) is not None}
    return {"conversation_id": cid, "user_id": int(ctx.user_id or 0), "message": message,
            "message_uuid": message_uuid or "", "question": task.question or ""}


def observability_turn_elapsed_ms() -> int:
    try:
        from backend import observability
        return int(observability.turn_elapsed_ms() or 0)
    except Exception:  # noqa: BLE001 — 观测旁路不得影响落库（护栏 §9）
        return 0


def _persist_turn_fallback(fallback) -> None:
    """收尾任务里的兜底落库：失败必须显式（日志 + trace 标注），不得静默假成功。"""
    if not fallback:
        return
    from backend.modules.mind.api.chat import persist_turn_fallback
    try:
        outcome = persist_turn_fallback(**fallback)
    except Exception:  # noqa: BLE001 — 落库失败只影响这份兜底内容，不得连带打断执行权释放
        logger.error("服务端兜底落库异常: conversation_id=%s uuid=%s —— 本回合正文可能只在 trace 里",
                     fallback.get("conversation_id"), fallback.get("message_uuid"), exc_info=True)
        _annotate_fallback_failure(f"persist_turn_fallback 异常（conversation_id="
                                  f"{fallback.get('conversation_id')}）")
        return
    if outcome.get("persisted"):
        return
    reason = outcome.get("reason") or ""
    if reason in ("already_persisted_by_client", "empty_turn", "no_conversation_or_identity"):
        return   # 正常路径：客户端已落库 / 空回合 / 本就没有会话归属
    logger.error("服务端兜底落库未完成: conversation_id=%s reason=%s",
                 fallback.get("conversation_id"), reason)
    _annotate_fallback_failure(f"兜底落库未完成({reason})")


def _annotate_fallback_failure(text: str) -> None:
    """把兜底失败标注到本回合 trace（recorder 已 flush 时退化为仅日志，不静默伪装成功）。"""
    try:
        from backend import observability
        observability.set_result(status="error", error=text)
    except Exception:  # noqa: BLE001 — 观测标注失败本身不再影响主链路
        logger.debug("trace 标注失败: %s", text)


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


def _sync_turn_files(runtime, user_id: int) -> None:
    """回合结束把会话产物镜像到云空间（对象存储）。

    后台收尾链路里执行，**失败不打断对话**（用户决策）：可诊断性由服务端 error 日志
    与「会话工作区文件」面板的未同步标记承担（标记按云端实际对象清单实判），
    不静默假装已上云。
    """
    if runtime is None:
        return
    try:
        from backend.modules.mind.execution import session_cloud
        session_cloud.sync_session(runtime.root, user_id, runtime.key, reason="turn")
    except Exception:  # noqa: BLE001 — 上云失败不得连带卡住执行权释放
        logger.exception("会话产物上云失败（对话不受影响，产物面板标记未同步）: session=%s",
                         getattr(runtime, "key", "?"))


async def _teardown_client(client, runtime, final, result, query_started, fallback=None,
                           user_id: int = 0) -> None:
    """done 发出后的后台收尾：兜底落库、断连、等在跑工具、核验子进程已停、释放执行权。

    领取线程不可被取消；本函数运行在独立后台任务里，不受请求生命周期（断连/取消）影响
    —— 正因如此，服务端兜底落库必须放在这里（前端 PUT 可能永不到达）。
    """
    with anyio.CancelScope(shield=True):
        await run_owned_sync(_persist_turn_fallback, fallback)
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
                # 工具全部收尾后镜像产物（写文件的工具已跑完，此刻是本轮文件的稳定点）
                await run_owned_sync(_sync_turn_files, runtime, int(user_id or 0))
                await run_owned_sync(runtime.confirm_stopped)
                await run_owned_sync(runtime.finish, getattr(final, "session_id", "") if final else "",
                                     bool(result and result.success), not query_started)
            except Exception:
                # 释放失败：会话留在 running，下轮 preflight 会显式 409 提醒用户，
                # 不伪装成已停止（no-silent-degradation）。
                logger.exception("会话停止核验或状态提交失败；执行权未释放，下轮发送将被拦截")


def _obs_compact_span(stage: str) -> None:
    """把上下文压缩的两个节点落成 span（kind="phase"）。

    与 `_obs_phase` 同理：日志里的 `[timing]` 行只能 grep，而“这一轮为何多花 12s”
    在管理端 trace 里必须看得见。压缩不是模型请求，不能伪装成 llm_call。
    观测旁路，异常一律吞（护栏 §9）。
    """
    try:
        from backend import observability
        observability.record_span(kind="phase", name=f"context_compact_{stage}", status="success")
    except Exception as exc:  # noqa: BLE001 — 观测不得影响主链路
        logger.debug("compact span 记录失败: %s", exc)


def _obs_rebind_span(info: dict) -> None:
    """把"本回合发生了执行后端重绑"落成 span（kind="phase"）。

    重绑不是模型请求，不能伪装成 llm_call；它必须出现在管理端 trace 里，否则"换了
    Harness 之后答案风格突变"这类现象只能靠翻日志归因。观测旁路，异常一律吞（护栏 §9）。
    """
    try:
        from backend import observability
        observability.record_span(kind="phase",
                                  name=f"harness_rebind:{info.get('from')}→{info.get('to')}",
                                  status="success")
    except Exception as exc:  # noqa: BLE001 — 观测不得影响主链路
        logger.debug("harness_rebind span 记录失败: %s", exc)


def _drain_context_events(extra: dict) -> list:
    """取走 build_options 的 PreCompact/PostCompact 登记的待发送事件（按发生顺序）。

    取完即清空：同一个列表跳重试回合复用，不清就会把旧事件再发一遍。
    """
    events = (extra or {}).get("_context_events")
    if not events:
        return []
    drained = list(events)
    events.clear()
    return [{"type": "context", **ev} for ev in drained]


async def _collect_context_usage(client, extra: dict, backend: str = "qoder"):
    """轮末采集上下文占用（进度条唯一数据源）：必须在 disconnect 前、本轮已有活客户端时做。

    限时 10s：采集失败/卡住只影响进度条（下轮会再采），不能拖住已算完的答案下发。
    backend 透传给采集函数：两个后端的 get_context_usage 返回体字段名不同（写死一套
    就会让另一套永远测不到，实测：直连通道进度条长期 --%）。
    """
    from backend.modules.mind.execution import context_usage
    cid = int((extra or {}).get("conversation_id") or 0)
    if client is None or not cid:
        return None
    try:
        return await asyncio.wait_for(context_usage.collect_and_save(client, cid, backend=backend), 10)
    except asyncio.TimeoutError:
        logger.warning("上下文占用采集超时(10s)：本轮进度条沿用上次值 conversation=%s", cid)
        return None
    except Exception as exc:  # noqa: BLE001 — 展示旁路不得影响取数主链路（护栏 §9）
        logger.warning("上下文占用采集异常（不影响本轮结果）: conversation=%s err=%s", cid, exc)
        return None


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


def build_turn_prompt(adapter, task, *, include_history: bool) -> str:
    """本轮下发给模型的提示词 = 问题(+历史/附件) + 指定分析场景的作业行。

    作业行必须落在**回合提示词末尾**而不是只写进系统提示词：实测（会话 1783069459277）
    系统提示词里中部命令式 + 尾部复述全部到位，模型仍按通用反问模板回答“想查哪类数据”。
    """
    prompt = adapter._prompt_with_attachments(task, include_history=include_history)
    directive = str((task.context.extra or {}).get("_scene_turn_directive") or "").strip()
    return f"{prompt}\n\n{directive}" if directive else prompt


async def _execute_stream(adapter, task, backend):
    import time as _time
    from backend.modules.mind.execution.session_workspace import claim_session
    from backend.modules.mind.execution.sdk_tools.compat import load_sdk, sdk_client_class
    from backend.modules.mind.execution.stream_utils import ToolEventTracker
    from backend.modules.mind.execution.resource_guard import bind_resources
    runtime = None
    client = None
    context_token = None
    result = None
    final = None
    streamed = False
    # 已经下发给前端的正文标记（与 `streamed` 不同：后者只记增量流）。重试守卫用它：
    # 已发出的 token 无法回滚，重跑会把两条不一致正文叠进同一条消息。
    emitted_text = False
    done_delivered = False
    query_started = False
    starting_sdk = False
    texts = []
    # 本回合的排队/重试实况：在 try 外建立，异常路径也能拿到（写在 except 里会丢）。
    retry_state = {"attempts": 0, "error_kind": "", "retry_after_s": None}
    tracker = ToolEventTracker()
    # 本回合的消息标识（trace 的 message_uuid）：兜底落库与前端落库据此判重。
    # 观测未开启时为空串，persist_turn_fallback 退化为"按正文尾部判重"，不猜 id。
    from backend import observability as _obs
    _turn_message_uuid = _obs.message_uuid() or ""
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
        # 审计注入（护栏 §9）：本轮用户问题随上下文到达取数工具，与 SQL 同批落
        # adh_query_audit 的 question 列。服务端写入、不进 prompt、不下发 LLM/前端。
        task.context.extra["question"] = task.question or ""
        context_token = set_execution_context(task.context)
        sdk = load_sdk(backend)
        client_type = sdk_client_class(sdk)
        starting_sdk = True
        client = client_type(options=options)
        await client.connect()
        _el = _time.time() - _t1
        logger.info("[timing] sdk_connect: %.1fms", _el * 1000)
        _obs_phase("sdk_connect", _el)
        _t1 = _time.time()
        await run_owned_sync(runtime.record_client, client)
        yield {"type": "capabilities", "data": runtime.policy.manifest()}
        prompt = build_turn_prompt(
            adapter, task,
            # claude 直连首轮（CLI 侧无转录）时必须带历史；qoder 链路维持原口径不变。
            include_history=(not runtime.sdk_session_id)
            or bool(task.context.extra.get("_adh_new_cli_session")))
        _el = _time.time() - _t1
        logger.info("[timing] prompt_build: %.1fms", _el * 1000)
        _obs_phase("prompt_build", _el)
        query_started = True
        # 模型窗口锚点：前置阶段（会话领取/资源绑定/建连/拼提示词）到此全部做完。
        # 不标注的话，第一条 llm_call 会把这 5~8s 固定成本当成“模型耗时”（历史归因失真根因）。
        from backend import observability
        observability.mark_model_window_start()

        async def _stream_once():
            """下发一次提示词并排空响应流：事件往外 yield，回合结果写回 `final`。"""
            nonlocal streamed, final, emitted_text
            _tq = _time.time()
            await client.query(prompt)
            _eq = _time.time() - _tq
            logger.info("[timing] client_query_sent: %.1fms", _eq * 1000)
            _obs_phase("client_query_sent", _eq)
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
                        emitted_text = True
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
                                emitted_text = True
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
                # CLI 的 autoCompact 发生在回合中间（不插这里就看不见）：按发生顺序透出
                for _ce in _drain_context_events(task.context.extra):
                    yield _ce
            for _ce in _drain_context_events(task.context.extra):
                yield _ce

        # 提供商繁忙的有限自动重试。三个前提缺一不可：
        # 1) 只重试可恢复分类（rate_limited / provider_busy）：超时、被停止、权限拒绝
        #    一律不重试，重试它们等于重复取数与重复写动作；
        # 2) 本轮还**没流出任何正文**：已经给用户看过半截答案再重跑，会出现两条不一致
        #    正文叠在同一条消息里（已下发的 token 无法回滚）；
        # 3) 次数与单次等待都有上限（capacity.max_auto_retries() / max_retry_wait_s()）：
        #    不拿“一直试”冒充“一定会好”。上限在本回合开头取一次，中途改配置不会让
        #    “第 2/3 次”变成“第 2/5 次”这种对不上的文案。
        retry_limit = capacity.max_auto_retries()
        while True:
            async for ev in _stream_once():
                yield ev
            if final is None:
                # 流在收到 ResultMessage 前结束（SDK 子进程崩溃/被杀/传输中断），非模型结果。
                raise RuntimeError("模型执行未返回结果：响应流在 ResultMessage 前结束（SDK 子进程崩溃/中断）")
            if not getattr(final, "is_error", False):
                break
            # SDK 报告结构化错误结果（error_max_turns / error_during_execution 等）。
            # 把 subtype/errors/terminal_reason 带进日志供诊断；对外（result.error）仍走
            # 可读中文，不外泄细节（护栏 §7）。model/cli_path 必须一起发：实测 CLI 独立跑
            # 正常、只有经 SDK 传了显式 model 时全量失败（num_turns=0），不带这两项时
            # 日志里看不出方向，只能看到“执行未完成”——可诊断性为零。
            detail = "; ".join(getattr(final, "errors", None) or []) or (getattr(final, "result", "") or "")
            log_detail = (
                "模型执行返回错误结果: subtype=%s terminal_reason=%s stop_reason=%s num_turns=%s "
                "model=%s cli=%s detail=%s"
                % (getattr(final, "subtype", "?"), getattr(final, "terminal_reason", "") or "-",
                   getattr(final, "stop_reason", "") or "-", getattr(final, "num_turns", None),
                   getattr(options, "model", None) or "auto",
                   getattr(options, "cli_path", "") or "-", detail[:300]))
            failure_kind, retry_after = capacity.classify_failure(
                getattr(final, "subtype", ""), getattr(final, "terminal_reason", ""),
                getattr(final, "stop_reason", ""), detail)
            retry_state["error_kind"] = failure_kind
            retry_state["retry_after_s"] = retry_after
            can_retry = (capacity.is_recoverable(failure_kind) and not emitted_text
                         and retry_state["attempts"] < retry_limit)
            if not can_retry:
                logger.error("%s | 失败分类=%s 已自动重试=%s", log_detail, failure_kind,
                             retry_state["attempts"])
                if capacity.is_recoverable(failure_kind):
                    # 分类为繁忙但没能重试（额度用尽或已流出正文）：仍给可行动文案，
                    # 不能退回“执行未完成，请联系管理员”——用户分不清是自己问法问题还是平台过载。
                    hint = capacity.user_hint(failure_kind)
                    raise ProviderBusyError(
                        f"{hint}，本轮未能完成。" + ("请稍后重试，或切换模型后再问。"
                                                   if not emitted_text else "已输出的正文保留，可继续追问。"),
                        kind=failure_kind, retry_after_s=retry_after,
                        attempts=retry_state["attempts"], detail=log_detail)
                raise RuntimeError(log_detail)
            retry_state["attempts"] += 1
            wait_s = float(retry_after or 5.0)
            logger.warning("%s | 分类=%s，%.0fs 后自动重试（第 %s/%s 次）",
                           log_detail, failure_kind, wait_s, retry_state["attempts"],
                           retry_limit)
            yield {"type": "capacity", "event": "retrying",
                   "attempt": retry_state["attempts"],
                   "max_attempts": retry_limit,
                   "wait_s": round(wait_s, 1),
                   "reason": capacity.user_hint(failure_kind)}
            await asyncio.sleep(wait_s)
            final = None
            texts.clear()   # 重试不叠加正文（重试条件已限定为“未流出任何 token”）
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
        # 轮次上限实况进 meta：chat_service 据此把「本轮是收尾出来的」写成用户可见告警。
        # 计数在 build_options 的 PreToolUse 闸里累加（同一次 ctx.extra 对象），
        # 与 tracker.tool_call_count（SDK 实际观测到的工具调用）互为印证，不是一套新口径。
        rounds = dict((task.context.extra or {}).get("_tool_rounds") or {})
        if rounds:
            meta["tool_rounds"] = rounds
            _warn = _round_limit_warning(rounds)
            if _warn:
                meta["warnings"] = [_warn]
        # 上下文占用采集（进度条唯一数据源）：本轮客户端还活着、尚未 disconnect ——
        # 这是唯一“零额外建连”的采集点（执行层客户端每轮新建，轮间没得可问）。
        # 拿到值后进 done payload，前端不必额外请求就能更新进度条；拿不到时不塞
        # 假数字（采集函数自己已写 context_error，前端据此区分“未测量”与“测量失败”。
        usage_snapshot = await _collect_context_usage(client, task.context.extra, backend)
        if usage_snapshot:
            meta["context_usage"] = usage_snapshot
        if retry_state["attempts"]:
            # 自动重试不是免费午餐（多一次模型请求、多一段等待），必须让用户看得见，
            # 不能把“试了两次才成”伪装成“一次就很快”。
            meta["retry_attempts"] = retry_state["attempts"]
            meta["warnings"] = list(meta.get("warnings") or []) + [
                f"模型服务一度繁忙，已自动重试 {retry_state['attempts']} 次后完成本轮"]
            _obs.set_capacity(retry_count=retry_state["attempts"])
        # 换 Harness 续聊：按产品决策**不向用户弹提示**（用户拍板，2026-11-10）。
        # 但事实不得无痕——重绑意味着 CLI 侧中间过程（此前工具调用细节、已读文件、压缩
        # 状态）不跨后端续用，只保问答文本。可诊断性由三处承担：服务端日志（_rebind_backend）、
        # 本回合 trace span、执行映射的 backend/backend_sessions 落库，评测与排障据此
        # 能分辨"重绑轮"与"正常续聊轮"（no-silent-degradation §2 的可区分要求）。
        rebind = (task.context.extra or {}).get("_harness_rebound") or {}
        if rebind.get("from") and rebind.get("to") and rebind["from"] != rebind["to"]:
            meta["harness_rebound"] = rebind
            _obs_rebind_span(rebind)
        result = ExecutionResult(success=True, output=getattr(final, "result", "") or "".join(texts), meta=meta)
    except ProviderBusyError as exc:
        # 繁忙类失败单独一支：对外给可行动中文（稍后重试/换模型），原始 subtype/errors
        # 只进日志（护栏 §7）；`retryable=True` 让前端的“重试本轮”按钮有依据。
        logger.error("模型服务繁忙导致本回合未完成: kind=%s attempts=%s %s",
                     exc.kind, exc.attempts, exc.detail)
        _obs.set_capacity(retry_count=exc.attempts)
        result = ExecutionResult(success=False, error=str(exc), meta={
            "error_kind": exc.kind, "retry_after_s": exc.retry_after_s,
            "retry_attempts": exc.attempts, "retryable": True})
    except HTTPException as exc:
        result = ExecutionResult(success=False, error=str(exc.detail),
                                 meta={"status_code": exc.status_code, "error_kind": "failed"})
    except (PermissionError, ValueError) as exc:
        logger.exception("AS-BOT 执行配置或权限错误")
        result = ExecutionResult(success=False, error=("模型执行失败，请联系管理员查看日志" if starting_sdk else str(exc)),
                                 meta={"error_kind": "failed"})
    except Exception as exc:
        logger.exception("AS-BOT 安全执行失败")
        # 同一个通用文案背两种事实（“模型过载”与“配置坏了”），对用户是无效提示：
        # 分类只取结构化特征，命中不了才留在 failed（不拿异常文本直接给用户看）。
        failure_kind, retry_after = capacity.classify_failure(str(exc))
        result = ExecutionResult(success=False,
                                 error=(f"{capacity.user_hint(failure_kind)}，请稍后重试或切换模型"
                                        if capacity.is_recoverable(failure_kind)
                                        else "执行未完成，请检查会话状态、工具配置或联系管理员查看日志"),
                                 meta={"error_kind": failure_kind, "retry_after_s": retry_after,
                                       "retryable": capacity.is_recoverable(failure_kind)})
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
        # 兜底落库记录在移交收尾前就地构造：texts/tracker/message_uuid 只在这一刻还齐全
        # （断连取消后本地变量随生成器一起销毁，收尾任务再也拿不到正文）。
        fallback = _fallback_turn_record(task, texts, tracker, result, _turn_message_uuid)
        if result is not None and not done_delivered:
            _spawn_teardown(lambda: _teardown_client(client, runtime, final, result, query_started,
                                                     fallback, int(task.context.user_id or 0)),
                            runtime, task.context.extra.get("conversation_id") or "new")
            done_delivered = True
            yield {"type": "done", "result": result}
        elif not done_delivered:
            _spawn_teardown(lambda: _teardown_client(client, runtime, final, result, query_started,
                                                     fallback, int(task.context.user_id or 0)),
                            runtime, task.context.extra.get("conversation_id") or "new")
        if context_token is not None:
            ExecutionContextVar.reset(context_token)
