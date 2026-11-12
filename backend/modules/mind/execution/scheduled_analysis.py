"""无人值守分析适配器：按任务属主的角色继承能力与可查范围，与 Chat 同一裁决点。

运行经 qodercli -q 一次性任务模式（qodercn_agent_sdk.query 单发），无会话状态。

2026-11 架构调整：任务配置里**不再有 AS-BOT 与数据源**（工作空间的定时任务只有 Agent 分析
形态；SQL 定时任务在数据中台的数据同步里配置）。
- **AS-BOT**：属主角色绑定的默认 AS-BOT（`is_default` 优先，否则首个）——与 Chat 完全同口径，
  每次执行与每次工具调用重新解析，角色换绑立即生效；
- **可查范围**：属主角色的数据源授权集（唯一裁决层 `resource_guard.bind_resources`）：
  唯一候选自动选定；多站落「聚合态站点」，由工具按 `site`/`datasource` 业务名逐查询自解析。

授权、资源范围、脱敏一律复用交互链路的同一份实现（`tool_policy.check_tool` +
`resource_guard.validate_tool_resources` + `sdk_tools.compat` 的 guarded handler）；
本模块不新建第二套「谁能做什么」判断。
"""
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException

logger = logging.getLogger(__name__)

from backend.modules.mind.execution.models import ExecutionContext
from backend.modules.mind.execution.sdk_tools.context import set_execution_context, ExecutionContextVar
from backend.modules.mind.execution.stream_utils import ToolEventTracker
from backend.core.task_runtime import RunInterrupted

SDK_BACKEND = "qoder"

# 无人在场时"口径不明就停"的表达：交互链路用 ask_user，这里用它显式失败（不猜口径）。
CLARIFICATION_TOOL = "needs_clarification"
CLARIFICATION_DESCRIPTION = "口径或业务意图不明确、或可查站点无法判定时停止本轮分析，交由人工补充任务配置。"
CLARIFICATION_QUALIFIED = f"mcp__datahub_scheduled__{CLARIFICATION_TOOL}"

# 无人值守不注册的能力（原因写进提示词与日志，不静默）：
# - 会话工作区文件/命令执行（datahub_workspace）：依赖会话目录归属与人在场的副作用收敛；
# - 外部 MCP 意图服务：需要会话级安全初始化（external_servers 只在交互回合准备）。
UNATTENDED_SKIP_REASON = "无人值守链路不启用会话工作区文件/命令执行与外部 MCP 工具"

# 定时分析必须能查目录并做声明式取数，否则产不出可校验的数字（与旧链路同口径）。
REQUIRED_TOOLS = {"get_metrics", "run_semantic_query"}


class ScheduledScopeError(PermissionError):
    def __init__(self, message, code="ASBOT_SCOPE_DENIED"):
        super().__init__(message)
        self.code = code


@dataclass
class UnattendedRuntime:
    """无人值守一次运行的可信运行时替身。

    只满足 `check_tool` / `validate_tool_resources` 需要的接口面（policy、ceiling、
    layer_id/backend、tool_tasks、verify()），**不做会话认领**：定时任务没有 SDK 会话，
    也就没有 `adh_agent_sessions` 行与目录归属。裁决口径仍走交互链路那一份实现，
    本类不新增任何判断分支。
    """
    user_id: int
    workspace_id: int
    layer_id: int = 0
    backend: str = SDK_BACKEND
    ceiling: object = None
    user_role: str = ""
    policy: object = None
    root: object = None
    layer_bound: bool = True
    tool_tasks: set = field(default_factory=set, repr=False)
    closed: bool = False
    closing: bool = False
    unsafe: bool = False

    @property
    def workspace(self):
        return self.root / "workspace"

    @property
    def runtime_dir(self):
        return self.root

    def verify(self):
        """每次工具调用实时校验：运行未终止 + 属主角色仍绑定同一个 AS-BOT。

        角色换绑 AS-BOT 后本任务不得继续用旧配置取数（与 SessionWorkspace.verify 的
        "执行权未被回收"等价：都是让配置漂移立即拦住，而不是静默换身份跑完）。
        """
        if self.closed or self.closing or self.unsafe:
            raise ScheduledScopeError("本次无人值守运行已终止，不再执行工具", "RUN_CLOSED")
        # 与 load_profile 同一份 live 档配置缓存（写时失效），不额外实读放大延迟。
        from backend.modules.mind.execution import as_bots
        current = as_bots.default_as_bot(as_bots.resolve_as_bots(
            self.workspace_id, self.user_role, "", self.user_id, use_cache=True, live=True))
        bound = str(((self.policy.as_bot or {}) if self.policy else {}).get("as_bot_key") or "")
        if not current or str(current.get("as_bot_key") or "") != bound:
            raise ScheduledScopeError("任务属主的角色 AS-BOT 绑定已变化，本轮中止，下次执行按新配置进行",
                                      "ASBOT_REQUIRED")


def _execution_layer(workspace_id: int, as_bot: dict | None = None) -> dict:
    """取本轮生效的执行层（选层口径走 `service.pick_layer_for_as_bot` 唯一实现）。

    以前此处内联了一份“默认层 → 首个健康层 → 系统级外部层”，与 Chat 那份双口径并存，
    一处判 active、一处判 healthy 就会漂移；现在两边同一实现，并额外支持
    “AS-BOT 绑定了哪条层就用哪条层”（没绑则仍按工作空间默认层）。

    无人值守不开“随便找条层跑”的第二条路：层缺失/停用/不满足 SDK 安全要求一律显式拒绝。
    """
    from backend.modules.mind.execution import service as exec_service
    from backend.modules.mind.execution.manager import get_execution_layer_manager
    from backend.modules.mind.execution.tool_policy import live_ceiling

    try:
        row = exec_service.pick_layer_for_as_bot(as_bot, workspace_id)
    except exec_service.LayerSelectionError as exc:
        raise ScheduledScopeError(f"无人值守分析不可用：{exc}", "LAYER_UNAVAILABLE") from exc
    if row is None or row.get("status") != "active":
        raise ScheduledScopeError("无人值守分析不可用：未找到可用的 Harness（层缺失或未启用）",
                                  "LAYER_UNAVAILABLE")
    try:
        adapter = get_execution_layer_manager().build_adapter(row)
    except (ValueError, KeyError) as exc:
        raise ScheduledScopeError(f"无人值守分析不可用：{exc}", "LAYER_UNSUPPORTED") from exc
    config = adapter.config or {}
    layer_id = int(config.get("_layer_id") or 0)
    backend = str((row.get("config") or {}).get("cli_name") or SDK_BACKEND)
    ceiling = config.get("allowed_tools")
    if layer_id:
        # 与 claim_session 同口径：执行层状态与工具上限每次实读（停用即刻生效）。
        ceiling = live_ceiling(None, SimpleNamespace(layer_id=layer_id, layer_bound=True, backend=backend))
    return {"layer_id": layer_id, "backend": backend, "ceiling": ceiling,
            "timeout": int(getattr(adapter, "timeout", 0) or 0)}


def _profile(ctx, policy, backend: str = SDK_BACKEND) -> dict:
    """把生效策略换算成一次运行的工具面（与 Chat 同源：逐工具注册，不整组放开）。

    ``backend`` 必须是**选层结果**的后端：MCP server 配置按后端创建
    （``make_server`` 用各自 SDK 的 ``create_sdk_mcp_server``），拿 qoder 的 server
    配 claude 的 options 会让工具面静默失联（历史缺陷：这里写死 SDK_BACKEND，
    于是无人值守永远走 Qoder 网关，与 Chat 选的直连层两套口径）。
    """
    from backend.modules.mind.execution.sdk_tools import build_tool_servers

    groups = build_tool_servers(backend, [], selection=policy.selection,
                                function_names=policy.function_names)
    registered = set(groups["allowed_tools"])
    unavailable = dict(policy.unavailable)
    from backend.modules.mind.execution import function_tools
    for name in sorted(policy.allowed - registered):
        # 平台功能动作不是直接可调工具（经 run_platform_function 派发，壳已在 registered 里），
        # 不得当成“本链路不注册”给出误导原因。它们留在 allowed 里是逐动作授权事实。
        if function_tools.is_action_qualified_name(name):
            continue
        # 已授权但本链路不注册（会话文件/命令执行、外部 MCP）：给出可见原因，
        # 提示词据此说明"本轮没有这些能力"，不是悄悄少给。
        unavailable[name] = UNATTENDED_SKIP_REASON
    return {"context": ctx, "policy": policy, "servers": dict(groups["servers"]),
            "tools": sorted(registered), "unavailable": unavailable}


def load_profile(config, identity) -> dict:
    """按任务属主的角色实时解析 AS-BOT、执行层与可查范围（每次工具调用重走一遍）。

    只信持久化的任务属主（`identity` 由服务端从任务 owner_id 解析），不接受请求体/LLM 传身份、
    AS-BOT 或数据源（护栏 §2、§7）。授权变化在工具边界立即生效，不留已撤权的执行窗口。
    """
    from backend.modules.mind.execution.tool_policy import resolve_policy
    from backend.modules.mind.execution.resource_guard import bind_resources
    from backend.core.site_service import authorized_sites

    config = config if isinstance(config, dict) else {}
    user_id = int((identity or {}).get("user_id") or 0)
    workspace_id = int((identity or {}).get("workspace_id") or 0)
    if user_id <= 0:
        raise ScheduledScopeError("无人值守分析缺少可信属主身份", "OWNER_REQUIRED")

    ctx = ExecutionContext(user_id=user_id, workspace_id=workspace_id, datasource_id=0,
                           extra={"as_bot_key": ""})
    try:
        # as_bot_key 留空 = 按属主角色取默认 AS-BOT（与 Chat 同一解析路径）。
        # 先不带 ceiling 解析一次，只为拿到 AS-BOT——它可能绑了执行层，而层的
        # allowed_tools 就是 ceiling 的来源（不先定层就无法算上限）。
        probe = resolve_policy(ctx, None)
    except PermissionError as exc:
        raise ScheduledScopeError(
            f"任务无法按属主角色继承分析能力：{exc}（请管理员在「AS-BOT 配置」为该角色绑定 AS-BOT）",
            "ASBOT_REQUIRED") from exc
    layer = _execution_layer(workspace_id, probe.as_bot)
    runtime = UnattendedRuntime(user_id=user_id, workspace_id=workspace_id,
                                layer_id=layer["layer_id"], backend=layer["backend"],
                                ceiling=layer["ceiling"])
    ctx.extra["secure_runtime"] = runtime
    try:
        # 第二次按真实 ceiling 重编译，并用 inputs 复用同一批 DB 事实
        # （owner/AS-BOT/角色权限码）——不复用就是“每保存/执行一次任务多三组往返”，
        # 与 session_workspace 里对双解析的性能要求同口径。
        policy = resolve_policy(ctx, layer["ceiling"],
                                inputs=getattr(probe, "resolved_inputs", None) or None)
    except PermissionError as exc:
        raise ScheduledScopeError(
            f"任务无法按属主角色继承分析能力：{exc}（请管理员在「AS-BOT 配置」为该角色绑定 AS-BOT）",
            "ASBOT_REQUIRED") from exc
    runtime.policy = policy
    runtime.user_role = ctx.user_role
    ctx.extra["as_bot_key"] = policy.as_bot["as_bot_key"]

    sites = authorized_sites(user_id, workspace_id) or []
    concrete = [s for s in sites if isinstance(s, dict) and s.get("site_code")
                and not int(s.get("is_aggregate") or 0)]
    site = str(config.get("site_code") or "").strip()
    if not site and len(concrete) >= 2:
        # 多站且任务没写站点 → 落聚合态：datasource_id 合法为 0，由工具按 site/datasource
        # 业务名逐查询自解析（闸门已区分"合法为空"与"真没选"，见 site_service.is_aggregate_site）。
        aggregate = next((s for s in sites if int(s.get("is_aggregate") or 0)), None)
        site = str((aggregate or {}).get("site_code") or "")
    ctx.extra["site_code"] = site

    sources = bind_resources(ctx, policy)          # 唯一候选自动选定；站点决定生效源
    ctx.extra["available_datasource_ids"] = sorted(int(x) for x in sources)
    if not sources:
        raise ScheduledScopeError("任务属主的角色没有已授权数据源，无法无人值守取数；"
                                  "请联系管理员配置角色数据源授权", "SOURCE_REQUIRED")
    short_names = {str(name).rsplit("__", 1)[-1] for name in policy.allowed}
    if not REQUIRED_TOOLS <= short_names:
        raise ScheduledScopeError("定时分析需要角色 AS-BOT 授权语义目录（get_metrics）与"
                                  "语义查询（run_semantic_query）", "TOOL_NOT_ALLOWED")

    profile = _profile(ctx, policy, layer["backend"])
    profile["sources"] = sources
    profile["site_code"] = site
    profile["site_labels"] = [str(s.get("display_name") or s.get("site_code")) for s in concrete]
    profile["layer_timeout"] = layer["timeout"]
    # 后端随 profile 带走：options 构造与单发流必须用**同一个** SDK，
    # 缺这个字段会让运行悄悄退回 qoder（见 _build_oneshot_options）。
    profile["backend"] = layer["backend"]
    return profile


def validate_task_as_bot(task) -> dict | None:
    """保存与执行前的统一自检：任务形态 + 属主角色能否解析出可用 AS-BOT 与可查范围。

    任务不持有 AS-BOT/数据源，因此这里校验的是"属主角色当前可用"，而不是配置字段齐全；
    角色后来被改（换绑 AS-BOT、收回数据源）由每次工具调用的实时裁决拦住，不在保存时冻结。
    """
    if task.get("task_type") != "agent":
        raise ScheduledScopeError("工作空间定时任务只有 Agent 分析形态；SQL 定时任务请在"
                                  "数据中台的数据同步里配置", "TASK_TYPE_INVALID")
    return load_profile(task.get("task_config") or {}, {
        "user_id": task.get("owner_id"), "workspace_id": task.get("workspace_id") or 0})


SCHEDULED_GUIDANCE = (
    "你是无人值守分析助手，没有人能在现场纠偏，因此口径不确定就停，不要猜。\n"
    "能力与可查数据范围由任务属主的角色决定：你看不到也不需要知道数据源标识；"
    "取数一律用业务名（对象/指标/维度）与站点业务名。\n"
    "作业要求：\n"
    "① 先看实时目录（get_metrics），名称以目录为准；相对日期用 time_window，不要手算绝对日期；\n"
    "② 取数走 run_semantic_query（声明式意图，禁止 SQL 字段）；需要未建模形状时用 execute_sql"
    "（只读 SELECT，服务端治理后才执行）；\n"
    "③ 本轮不执行业务或配置的写动作（本体/指标字典/看板/资产归档等）：定时任务只做分析与产出报表，"
    "需要改配置请交回人工；\n"
    "④ 结论必须来自已授权查询返回的数据；查不到或口径不明就调用 needs_clarification 停止，"
    "严禁用常识、系统表或物理元数据充数。"
)


def _scope_guidance(profile: dict) -> str:
    """把本轮生效的可查范围讲清楚（站点是业务概念，不违反数据源黑盒护栏）。"""
    labels = profile.get("site_labels") or []
    if not labels:
        return ("- 本轮未登记站点信息：不确定该用哪个数据范围时，先用 list_datasources 看候选，"
                "并在每次查询里带上 datasource=<源业务名>；仍判不出来就 needs_clarification。")
    if len(labels) == 1:
        return f"- 本轮唯一可查站点：{labels[0]}。所有查询都落这一站，不要反过来要求用户选择。"
    return (f"- 本轮可查站点（共 {len(labels)} 站）：{' / '.join(labels)}。\n"
            f"- 问题或任务背景点名了站点（或域名等语言段能唯一判定）→ 只查那一站并在结论里说明范围；\n"
            f"- 未点名站点 → 对每个站点各调一次 run_semantic_query(site=<站点业务名>)，"
            f"结果**分站呈现**（每站一行），不得把各站数字相加当作全局值；"
            f"确需全局去重/汇总口径而知识库未定义时，如实说明「各站分别统计、未合计」。")


class _ScheduledRun:
    """一次性任务的单次运行：负责运行内采集、终稿判读与错误码归一。

    工具循环在 qodercli 运行时内完成；工具 handler 用交互链路同一份 guarded 实现
    （check_tool + validate_tool_resources + 出站脱敏），本类只在边界上做实时裁决与结果登记。
    """

    def __init__(self, question, config, identity, check, profile, on_event=None, cancel_event=None):
        self.question = question
        self.config = config if isinstance(config, dict) else {}
        self.check = check
        self.profile = profile
        # 事件流出口（在线「用最新数据重新生成」用）：无人值守本来没有观众，定时任务
        # 这条主路仍然是 None（零开销）。传了就把取数/工具/正文进展逐条推给观看方，
        # 让人在报表页看到过程而不是「生成中」三个字 —— 过程可见，失败原因同样可见。
        self.on_event = on_event
        # 离开即终止：观看方断开时置位这个标志。消息边界只读内存标志，不调 `check` ——
        # `check` 在定时任务链路里是一次库查询（读执行日志状态），逐条 SDK 消息都查
        # 会把它打成热点（`include_partial_messages` 下每轮几十条）。取消要快，
        # 但不能靠加数据库轮询换快；工具边界照旧走 `check`（权威租约/停止判据）。
        self.cancel_event = cancel_event
        self.ctx = profile["context"]
        self.policy = profile["policy"]
        # 取数与守卫只用服务端任务属主身份（含角色/用户名），不接受 LLM 传参。
        self.identity = {"user_id": self.ctx.user_id, "workspace_id": self.ctx.workspace_id,
                         "role": self.ctx.user_role, "username": self.ctx.username}
        self.prompt = question + "\n任务背景：" + str(self.config.get("context") or "")
        # 报表重跑的版式契约（“只换数据、不换版式”）由报表域算好整段交进来，这里只拼接：
        # 执行链不知道报表版式长什么样，也不得反推一套自己的判断。
        self.layout_contract = str(self.config.get("layout_contract") or "").strip()
        if self.layout_contract:
            self.prompt += "\n\n" + self.layout_contract
        # 运行标识随 ctx.extra 注入，LLM 不可见；run 级收集器以本次运行为界。
        self.ctx.extra["task_binding"] = {"kind": "scheduled_analysis", "run_id": uuid4().hex}
        self.ctx.extra["question"] = question or ""
        # 报告证据链：任何经治理的取数结果由采集点登记（含来源权限快照）。
        self.results = []
        self.ctx.extra["unattended_query_sink"] = self._collect
        self.backend = str(profile.get("backend") or SDK_BACKEND)
        self.allowlist = set(profile["tools"])
        self.fatal = None
        self.abort_exc = None
        self.final = None
        # 模型终稿正文（末一条带文字的助手消息）：报表重跑拿它当报表正文，
        # 中途带工具调用的文字是过程解说，会被后一条覆盖，不当下成品。
        self.answer = ""
        self.tracker = ToolEventTracker()
        self.max_turns = max(1, min(int(self.config.get("max_iterations") or 8), 20))
        self.timeout = min(int(self.config.get("timeout_seconds") or profile.get("layer_timeout") or 300), 900)
        self.run_dir = None
        self.rounds = {"limit": self.max_turns, "count": 0, "blocked": 0}

    def _emit(self, payload: dict) -> None:
        """把一条过程事件推给观看方；没有观众（定时任务）就是空操作。

        推失败**不能**影响这一轮分析的终局：报表状态以库里那条写行为准，事件流只是直播。
        失败要留在日志里（否则"进度面板莫名停住"无从诊断），但不改变运行结果。
        """
        if self.on_event is None:
            return
        try:
            self.on_event(dict(payload))
        except Exception as exc:      # noqa: BLE001 — 直播通道故障不拖垮分析
            logger.warning("定时分析过程事件投递失败（不影响生成结果）: %s", exc)

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _collect(self, record):
        """采集点回调（由 `sdk_tools.context.record_unattended_query` 调用）。

        只登记服务端受控结果；来源权限快照按属主身份算，与 `execute_semantic_source` 同口径。
        快照失败必须冒出去：没有证据的结果进报告等于把未验证数字当结论。
        """
        from backend.modules.viz.services import report_service
        security = report_service.snapshot_for_query(
            self.identity, record["sql"], record["datasource_id"], record["dialect"])
        self.results.append({"status": "success", "columns": record["columns"], "rows": record["rows"],
                             "row_count": record["row_count"], "truncated": record["truncated"],
                             "source": record["source"], "_security_context": security})
        # 过程可见：只报「第几条取数、多少行、有没有被截断、走的是哪条受治理通道业务名」。
        # 不外泄列名/SQL/物理标识（护栏 §7），面板要回答的是"跑到哪了"，不是"查了哪张表"。
        self._emit({"type": "data", "query_count": len(self.results),
                    "row_count": int(record["row_count"] or 0),
                    "truncated": bool(record["truncated"]),
                    "source": str(record["source"] or "").split(":", 1)[0]})

    def _fail(self, response):
        """首个治理失败即终局（与旧链路一致：不拿半截结果冒充成功）。"""
        if self.fatal is None:
            self.fatal = response

    def _failure(self, code, retryable):
        return {"status": "partial" if self.results else "failed", "error_code": code,
                "retryable": retryable, "_analysis_results": list(self.results)}

    async def clarify(self, args):
        """口径不明即停：显式失败并提示人工补充任务配置（不猜口径、不产半成品报告）。"""
        from backend.modules.mind.execution.sdk_tools.catalog_tools import _text
        self._fail({"status": "failed", "error_code": "NEEDS_CLARIFICATION", "needs_attention": True})
        return _text("口径或业务意图不明确，本轮分析已停止，请人工补充任务配置后重跑", is_error=True)

    async def authorize(self, name):
        """工具调用前的实时裁决：与 Chat 同一份 `check_tool`（执行层、digest、工具授权）。

        工具循环在 qodercli 运行时内部完成，没人能在现场按下停止键，因此取消与租约
        失效（`check` 由 executor 传入）在每个工具边界读一次——否则一次长取数跑完才发现
        任务已被人工停止，等于把已撤销的执行权又用了一遍。
        """
        if self.check:
            self.check()
        if name == CLARIFICATION_QUALIFIED or str(name).rsplit("__", 1)[-1] == CLARIFICATION_TOOL:
            return          # 停止信号不是数据工具，不涉及资源与授权
        from backend.modules.mind.execution.session_workspace import run_owned_sync
        from backend.modules.mind.execution.tool_policy import check_tool
        if name not in self.allowlist:
            raise ScheduledScopeError("当前角色未授权该定时工具", "TOOL_NOT_ALLOWED")
        await run_owned_sync(check_tool, self.ctx, name)

    async def _consume(self, options):
        """一次性消息流消费：复用 stream_utils 转译与 _obs_record_* 埋点口径，直至终稿。"""
        from contextlib import aclosing
        from backend.modules.mind.execution.adapters.qoder_sdk_adapter import (
            _obs_record_assistant, _obs_record_tool_result, _obs_record_result)
        async with aclosing(self.oneshot(self.prompt, options)) as stream:
            async for msg in stream:
                if self._cancelled():
                    # 观看方已离开：在这里停住（`aclosing` 会关掉 SDK 流），并按人工停止
                    # 定性 —— 不能让它落到 `_finalize` 的「流在终稿前结束 = LLM 不可用」。
                    self.abort_exc = RunInterrupted("cancelled")
                    break
                kind = type(msg).__name__
                if kind == "AssistantMessage":
                    _obs_record_assistant(msg)
                    texts = [block.text for block in (getattr(msg, "content", None) or [])
                             if type(block).__name__ == "TextBlock" and getattr(block, "text", "")]
                    if texts:
                        self.answer = "\n".join(texts)
                        # 只报「正在写正文、目前多少字」：过程文案不给观众看半截正文，
                        # 中途带工具调用的解说会被后一条覆盖，当成品展示就是误导。
                        self._emit({"type": "text", "chars": len(self.answer)})
                    for block in getattr(msg, "content", []) or []:
                        if type(block).__name__ == "ToolUseBlock":
                            event = self.tracker.on_tool_use(block)
                            self._emit({"type": "tool_start", "tool_call_id": event["tool_call_id"],
                                        "tool": event["tool"], "call_index": self.tracker.count})
                elif kind == "UserMessage":
                    for block in (msg.content if isinstance(msg.content, list) else []):
                        if type(block).__name__ == "ToolResultBlock":
                            event = self.tracker.on_tool_result(block)
                            if event:
                                _obs_record_tool_result(event, self.tracker.arguments_of(event["tool_call_id"]))
                                # 结果正文不下发（原始返回体可能含物理表列与 SQL）：
                                # 面板只需要知道这一步成没成、花了多久。
                                self._emit({"type": "tool_result",
                                            "tool_call_id": event["tool_call_id"],
                                            "tool": event["tool"],
                                            "ok": not event["error"], "elapsed": event["elapsed"]})
                elif kind == "ResultMessage":
                    self.final = msg
                    _obs_record_result(msg)
                if self.fatal is not None or self.abort_exc is not None:
                    break

    def oneshot(self, prompt, options):
        """一次性单发通道（sdk.query：无会话、不落 resume）；测试缝。

        SDK 按本次运行的执行层后端取（与 ``_build_oneshot_options`` 同一来源），
        否则 claude 层的 options 会被 qoder SDK 消费。
        """
        from backend.modules.mind.execution.sdk_tools.compat import load_sdk
        return load_sdk(self.backend).query(prompt=prompt, options=options)

    def _answer_text(self, final) -> str:
        """报表/报告正文取模型终稿：优先 ``ResultMessage.result``（SDK 认定的最终回答），
        退而取末一条带文字的助手消息。两者都没就是真的没交稿（不猜、不拼半截）。"""
        return str(getattr(final, "result", "") or "").strip() or str(self.answer or "").strip()

    def _finalize(self) -> dict:
        if self.abort_exc is not None:
            raise self.abort_exc
        if self.fatal is not None:
            return self.fatal
        final = self.final
        if final is None:
            # 流在终稿前结束（SDK 子进程崩溃/被杀/传输中断），非模型结果。
            return self._failure("LLM_UNAVAILABLE", retryable=not self.results)
        if getattr(final, "is_error", False):
            subtype = str(getattr(final, "subtype", "") or "")
            terminal = str(getattr(final, "terminal_reason", "") or "")
            if "max_turns" in subtype or "max_turns" in terminal:
                return {"status": "failed", "error_code": "ITERATION_LIMIT", "needs_attention": True}
            return self._failure("LLM_UNAVAILABLE", retryable=not self.results)
        if not self.results:
            return {"status": "failed", "error_code": "NO_QUERY_RESULT", "needs_attention": True}
        return {"status": "success", "answer": self._answer_text(final),
                "_analysis_results": list(self.results)}

    async def execute(self) -> dict:
        import shutil
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="adh-scheduled-"))
        self.run_dir = root
        (root / "workspace").mkdir()
        (root / "tmp").mkdir()
        self.profile["context"].extra["secure_runtime"].root = root
        # 工具 handler 从 ContextVar 读运行上下文（与交互链路同一注入方式），
        # 因此必须在整条流外设置、结束后复位，不能每次调用各设一次。
        token = set_execution_context(self.ctx)
        try:
            try:
                options = _build_oneshot_options(self)
            except (PermissionError, ValueError, HTTPException) as exc:
                logger.warning("定时分析运行配置错误: %s", exc)
                return {"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"),
                        "needs_attention": True}
            try:
                await asyncio.wait_for(self._consume(options), self.timeout)
            except RunInterrupted:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("定时分析一次性运行超时（max_turns/运行超时约束）")
                return {"status": "failed", "error_code": "ITERATION_LIMIT", "needs_attention": True}
            except (PermissionError, ValueError, HTTPException) as exc:
                logger.warning("定时分析运行权限或配置已失效: %s", exc)
                return {"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"),
                        "needs_attention": True}
            except Exception:
                logger.exception("定时分析一次性运行未完成")
                return self._failure("LLM_UNAVAILABLE", retryable=not self.results)
            return self._finalize()
        finally:
            ExecutionContextVar.reset(token)
            shutil.rmtree(root, ignore_errors=True)


def _build_oneshot_options(run: "_ScheduledRun"):
    """一次性运行的 SDK 选项：工具面与交互链路同源（build_tool_servers + guarded handler）。

    与 Chat 的差异只有三处，且都是"无人值守"本身的要求：
    - 不带 resume、不落会话（sdk.query 单发）；
    - 不注册会话工作区文件/命令执行与外部 MCP（原因写进提示词，不静默）；
    - 技能正文一律展开（没人在现场纠偏，不因省字数拿分析剧本冒险）。
    """
    import os
    from backend.modules.mind.execution.sdk_tools.compat import load_sdk, make_server
    from backend.modules.mind.execution.prompt_composer import compose_system_prompt
    from backend.modules.mind.execution.secure_sdk import resolve_access_token, enforce_tool_round_limit
    from backend.modules.mind.execution import as_bots
    from backend.modules.mind.execution.quick_paths import match_quick_paths
    from backend.modules.mind.execution.tool_policy import is_system_scope
    from backend.core import scenario_access
    from qodercn_agent_sdk.auth import AccessTokenAuthOptions

    backend = str(run.profile.get("backend") or SDK_BACKEND)
    sdk = load_sdk(backend)
    ctx = run.ctx
    servers = dict(run.profile["servers"])

    async def _clarify(args):
        return await run.clarify(args)

    servers["datahub_scheduled"] = make_server(backend, "datahub_scheduled", [
        sdk.tool(CLARIFICATION_TOOL, CLARIFICATION_DESCRIPTION,
                 {"type": "object", "properties": {}, "additionalProperties": False})(_clarify)])
    allowed_tools = sorted(set(run.profile["tools"]) | {CLARIFICATION_QUALIFIED})

    async def permit(name, args, context):
        try:
            await run.authorize(name)
            return sdk.PermissionResultAllow(updated_input=args)
        except Exception:
            logger.exception("定时分析权限校验拒绝: %s", name)
            return sdk.PermissionResultDeny(message="当前任务未授权该工具或权限已变化")

    async def before_tool(data, tool_use_id, context):
        name = data.get("tool_name", "")
        within_limit, limit_reason = enforce_tool_round_limit(run.rounds, name)
        if not within_limit:
            logger.warning("定时分析本轮工具调用已达上限，拒绝第 %s 次调用并要求收尾: %s",
                           run.rounds["count"], name)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": limit_reason}}
        try:
            await run.authorize(name)
            return {}
        except Exception:
            logger.exception("定时分析执行前守卫拒绝: %s", name)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": "当前任务未授权该工具或权限已变化"}}

    home = run.run_dir
    # 分析场景按“任务属主可见主体”过滤（与交互会话同一判定函数）：无人值守不能比
    # 真人在线时拿到更多场景正文；空授权 = 只用不限主体的场景（fail-closed）。
    scene_subjects = scenario_access.visible_subject_keys(
        run.ctx.user_id, run.ctx.workspace_id, role=run.ctx.user_role or "",
        system_scope=is_system_scope(run.policy))
    scene_hits = match_quick_paths(run.question, run.ctx.workspace_id, run.ctx.datasource_id,
                                   allowed_subjects=scene_subjects)
    env = {k: None for k in os.environ if k not in ("PATH", "LANG", "LC_ALL")}
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home), "TMPDIR": str(home / "tmp"),
                "QODERCN_CONFIG_DIR": str(home) + "/qoder-cn"})
    system_prompt = "\n".join([
        SCHEDULED_GUIDANCE,
        "## 本轮可查范围（服务端按任务属主角色派生）",
        _scope_guidance(run.profile),
        "",
        compose_system_prompt(
            run.policy.as_bot, [run.policy.as_bot], run.ctx.username, run.ctx.user_role,
            knowledge_bases=as_bots.load_knowledge_bases(
                run.policy.as_bot.get("knowledge_base_ids") or [], use_cache=True),
            skills=as_bots.load_skills(as_bots.collect_skill_names([run.policy.as_bot])),
            quick_paths=scene_hits,
            user_id=run.ctx.user_id, workspace_id=run.ctx.workspace_id,
            datasource_id=run.ctx.datasource_id,
            # 主题令牌按本轮配置取（报表重跑带上一版主题；定时任务没带则按平台默认主题包）：
            # 这里曾写死空串，于是无人值守产出的 HTML 完全没有主题依据，模型只能自造颜色。
            capabilities=run.policy.manifest(),
            report_theme_id=str(run.config.get("report_theme") or ""),
            site_code=str(ctx.extra.get("site_code") or ""),
            authorized_sites=ctx.extra.get("authorized_sites"),
            question=run.question or "",
            # 无人值守：没有人在现场纠偏，不因省字数拿技能正文（分析剧本/交付口径）冒险；
            # 仍走能力门控，未授权的工具不会诱导模型去用。
            force_all_skill_bodies=True,
        ),
    ])
    token = resolve_access_token()
    if not token and backend != "claude":
        # claude 直连不需要 Qoder 凭据（它用模型中心登记的供应商 Key），不能因为
        # 没配 PAT 就把直连的定时任务拦下；非 claude 后端仍保持原有硬要求。
        raise ValueError("Harness 凭据未配置(QODERCN_PERSONAL_ACCESS_TOKEN)")
    # 无人值守同交互链路一致的模型优先级：
    # ctx.extra.model_ref（resolve_policy 已从 AS-BOT.models[0] 默认注入）
    # → AS-BOT.models[0] → .env QODER_MODEL 兜底 → None（SDK Auto）。
    from backend.common.config import QODER_MODEL
    _sem = (run.ctx.extra or {}).get("model_ref") or ""
    _asbot_models = run.policy.as_bot.get("models") or []
    _model = _sem or (_asbot_models[0] if _asbot_models else "") or QODER_MODEL or None
    shared = dict(
        tools=[], allowed_tools=allowed_tools, permission_mode="default", setting_sources=[],
        cwd=str(home / "workspace"), add_dirs=[], env=env,
        mcp_servers=servers, agents={}, system_prompt=system_prompt,
        max_turns=run.max_turns, include_partial_messages=True,
        can_use_tool=permit, hooks={"PreToolUse": [sdk.HookMatcher(hooks=[before_tool])]})
    if backend == "claude":
        # 无人值守一次性任务：凭据与会话参数走与问答同一个实现，否则选了 claude 层的
        # 定时任务会拿着 Qoder token 去跑（表现为“配了百炼但仍在耗 Credits”）。
        # 一次性任务无转录可续，不传 session_id/resume，由 CLI 自建会话。
        from backend.modules.mind.execution import anthropic_direct as ad
        from backend.modules.mind.execution.sdk_tools.compat import sdk_options_class
        direct = ad.apply_direct_channel(env, str(home), _model, "")
        direct.pop("_adh_channel", None)
        direct.pop("_adh_new_cli_session", None)
        return sdk_options_class(sdk)(**shared, cli_path=ad.resolve_claude_cli_path(""), **direct)
    return sdk.QoderAgentOptions(**shared,
        resume=None, cli_path=None, model=_model, auth=AccessTokenAuthOptions(access_token=token),
        strict_mcp_config=True, allowed_mcp_server_names=list(servers), skills=[])


async def analyze_question(question: str, config: dict, identity: dict, check=None,
                           on_event=None, cancel_event=None) -> dict:
    """无人值守分析单题入口：qodercli -q 一次性任务模式（sdk.query 单发）。

    每个 question 一次独立运行（无 resume/会话状态，不走 ChatService 会话派发）；工具面与
    交互链路同源注册（in-process MCP），取数一律经统一治理执行口，结果由 run 级采集点登记
    为报告证据。返回结构 {status, error_code, needs_attention, retryable, answer, _analysis_results}
    与错误码语义保持旧链路不变（``answer`` = 模型终稿正文，报表重跑拿它沿用原版式当正文）。

    ``on_event`` / ``cancel_event`` 只服务「报表页里有人在线看着重跑」这一种用法：
    前者收过程事件（data/tool_start/tool_result/text），后者被置位后本轮在下一条 SDK
    消息边界停下并抛 ``RunInterrupted('cancelled')``（调用方负责把终态写成已取消）。
    定时任务两者都不传，行为与之前完全一致。
    """
    if not question.strip():
        return {"status": "failed", "error_code": "EMPTY_QUESTION"}
    try:
        profile = await asyncio.to_thread(load_profile, config, identity)
    except (PermissionError, ValueError, HTTPException) as exc:
        logger.warning("定时分析授权拒绝: %s", exc)
        return {"status": "failed", "error_code": getattr(exc, "code", "ASBOT_SCOPE_DENIED"),
                "needs_attention": True}
    return await _ScheduledRun(question, config, identity, check, profile,
                               on_event=on_event, cancel_event=cancel_event).execute()
