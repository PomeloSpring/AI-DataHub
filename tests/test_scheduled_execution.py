"""定时（无人值守）执行回归：角色继承解析、同源裁决、调度不重复派发。

2026-11 架构口径：工作空间的定时任务只有 Agent 分析形态，任务配置里**不持有 AS-BOT 与
数据源**——每次执行按任务属主的角色实时继承分析能力与可查数据范围（与 Chat 同一裁决点）。
SQL 定时任务归数据中台的「数据同步」（adh_sync_tasks.schedule_cron / DAG cron），
executor 里那条分支已删除，本文件也不再为它保留用例。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.modules.flow.tasks import executor


def _site(code, label, aggregate=0, datasource_ids=None):
    return {"site_code": code, "display_name": label, "is_aggregate": aggregate,
            "datasource_ids": datasource_ids or []}


@pytest.fixture
def scheduled_as_bot(monkeypatch):
    """无人值守解析所需的全部外部事实，离线不连真库。

    覆盖 AS-BOT（按角色解析）、角色数据源授权、可查站点、执行层、报告来源快照——
    这些都是 `load_profile` 真实走过的路径，打桩在 seam 上而不是绕过被测代码。
    """
    import copy
    from backend.common import db, auth, as_bot_config_cache
    from backend.modules.mind.execution import resource_guard, service as exec_service
    from backend.modules.mind.execution import manager as exec_manager
    from backend.modules.mind.execution.sdk_tools import external_tools
    from backend.core.role_service import role_service
    from backend.core import site_service
    from backend.modules.viz.services import report_service

    state = {"as_bot": {"id": 1, "as_bot_key": "sales", "name": "销售分析", "is_default": 1,
                        "system_prompt": "你是销售分析", "knowledge_base_ids": [],
                        "mcp_server_ids": [], "models": [],
                        "tools": {"mcp": {"semantic": ["get_metrics", "run_semantic_query"]}}},
             "sources": [8], "kbs": [4], "enabled": True, "sites": [], "queries": [],
             "server": {"id": 9, "name": "srv", "transport": "sse", "url": "http://test.invalid",
                        "tools_config": [{"name": "propose_semantic_intent"}]},
             "layer": {"id": 4, "status": "active", "is_default": 1,
                       "config": {"mode": "sdk", "cli_name": "qoder", "allowed_tools": None}}}

    def query(sql, params=(), fetchone=False):
        state["queries"].append((sql, params))
        if "adh_as_bots" in sql:
            return [copy.deepcopy(state["as_bot"])] if state["enabled"] else []
        if "adh_mcp_servers" in sql:
            assert "workspace_id" not in sql and params == (9,)
            return copy.deepcopy(state["server"])
        if "adh_knowledge_bases" in sql:
            assert "workspace_ids" not in sql
            # kb_type 是绑定校验的必读列（未接入检索通道的类型绑了也拒）
            return [{"id": i, "name": f"kb-{i}", "kb_type": "qmind"} for i in params if i in state["kbs"]]
        raise AssertionError(sql)

    monkeypatch.setattr(db, "execute_query", query)
    monkeypatch.setattr(resource_guard, "execute_query", query)
    monkeypatch.setattr(external_tools, "execute_query", query)
    # 配置缓存关掉：测试要的是"每次实读→撤权立即可见"，不吃 5s TTL（也避免用例互相污染）
    monkeypatch.setattr(as_bot_config_cache, "_ENABLED", False)
    # 角色-AS-BOT 一对一解析：离线模拟用户角色，不连真库
    monkeypatch.setattr(role_service, "get_user_roles",
                        lambda uid, ws=None: [{"id": 1, "name": "admin"}])
    monkeypatch.setattr(role_service, "get_user_role_ai_perms", lambda *a, **k: {})
    # 纯角色裁决：数据源可用集=属主角色授权（唯一裁决层）
    monkeypatch.setattr(role_service, "get_user_allowed_datasources",
                        lambda uid, ws=0: list(state["sources"]))
    monkeypatch.setattr(auth, "resolve_execution_owner", lambda uid, ws: {
        "user_id": uid, "workspace_id": ws, "role": "admin", "username": "创建者"})
    # 站点是可查范围的派生视图（拓扑离线，按 state["sites"] 模拟）
    monkeypatch.setattr(site_service, "authorized_sites",
                        lambda uid, ws=0: list(state["sites"]))
    def source_for_site(code, allowed_ids, model_id=0):
        allowed = set(allowed_ids or ())
        for s in state["sites"]:
            if s["site_code"] != code or int(s.get("is_aggregate") or 0):
                continue
            ids = [i for i in (s.get("datasource_ids") or []) if i in allowed]
            return ids[0] if len(ids) == 1 else 0
        return 0
    monkeypatch.setattr(site_service, "session_source_for_site", source_for_site)
    def is_aggregate(ref):
        code = str(ref or "")
        return any(s["site_code"] == code and int(s.get("is_aggregate") or 0) for s in state["sites"])
    monkeypatch.setattr(site_service, "is_aggregate_site", is_aggregate)
    # 执行层：默认层口径的唯一实现（service.pick_active_layer）读全局清单
    # —— active+healthy → is_default → 首条。工作空间绑定表已退役（写入 API 已删），
    # 不再当配置真值源，所以这里打桩在 list_layers 而不是 get_workspace_layers。
    monkeypatch.setattr(exec_service, "list_layers", lambda: [state["layer"]])
    monkeypatch.setattr(exec_service, "is_healthy_layer", lambda layer: True)
    monkeypatch.setattr(exec_service, "get_layer", lambda lid: state["layer"])
    monkeypatch.setattr(exec_manager, "get_execution_layer_manager", lambda: SimpleNamespace(
        build_adapter=lambda row: SimpleNamespace(config={"_layer_id": row["id"]}, timeout=300)))
    # 报告来源快照（报告证据链）：物理来源枚举与策略摘要离线
    monkeypatch.setattr(report_service, "_snapshot",
                        lambda *a: {"sources": [{"datasource_id": 8, "table": "orders"}],
                                    "policy_digest": "test"})
    monkeypatch.setattr(report_service, "_verify_snapshot", lambda *a: None)
    return state


def _grant_mcp(state):
    state["as_bot"]["mcp_server_ids"] = [9]
    state["as_bot"]["tools"]["external"] = {"9": ["propose_semantic_intent"]}


IDENTITY = {"user_id": 7, "workspace_id": 3}
SEMANTIC_QUERY = "mcp__datahub_semantic__run_semantic_query"


@pytest.mark.parametrize("results,required,report_ok,expected", [
    ([], False, False, "failed"),
    ([{"status": "failed"}], False, False, "failed"),
    ([{"status": "success"}, {"status": "failed"}], False, False, "partial"),
    ([{"status": "success"}], True, False, "failed"),
    ([{"status": "success"}], True, True, "success"),
    ([{"status": "success"}], False, False, "success"),
])
def test_truthful_status(results, required, report_ok, expected):
    assert executor.execution_status(results, required, report_ok) == expected


@pytest.mark.parametrize("owner", [0, -1, None])
def test_missing_owner_never_queries(owner, monkeypatch):
    from backend.common import auth
    lookup = Mock()
    monkeypatch.setattr(auth, "get_user_by_id", lookup)
    with pytest.raises(PermissionError):
        auth.resolve_execution_owner(owner, 3)
    lookup.assert_not_called()


def test_disabled_owner_rejected(monkeypatch):
    from backend.common import auth
    monkeypatch.setattr(auth, "get_user_by_id", lambda uid: {"status": "disabled"})
    with pytest.raises(PermissionError):
        auth.resolve_execution_owner(7, 3)


def test_log_projection_drops_legacy_tokens():
    from backend.modules.flow.api.scheduled import _safe_log
    out = _safe_log({"id": 1, "report_access_token": "secret", "access_token": "old",
                     "result_data": [{"sql": "secret"}], "error_message": "host password"})
    assert out == {"id": 1}


# ── 角色继承：任务不持有 AS-BOT 与数据源 ────────────────────────────────

def test_as_bot_comes_from_owner_role_not_task_config(scheduled_as_bot):
    """配置里写着 AS-BOT 与数据源也不采信：一律按属主角色重解（旧任务自动跟随新架构）。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    profile = sa.load_profile({"as_bot_key": "ops", "datasource_id": 99}, IDENTITY)
    assert profile["policy"].as_bot["as_bot_key"] == "sales"
    assert profile["context"].datasource_id == 8            # 唯一候选自动选定
    assert profile["sources"] == {8}
    assert profile["site_code"] == ""
    assert SEMANTIC_QUERY in profile["tools"]               # 能力面与 Chat 同源


def test_role_without_as_bot_fails_loud(scheduled_as_bot):
    from backend.modules.mind.execution import scheduled_analysis as sa
    scheduled_as_bot["enabled"] = False
    with pytest.raises(sa.ScheduledScopeError) as exc:
        sa.load_profile({}, IDENTITY)
    assert exc.value.code == "ASBOT_REQUIRED"
    assert "AS-BOT 配置" in str(exc.value)     # 给出可操作指引，不静默换个 AS-BOT 跑


def test_multi_site_falls_back_to_aggregate_scope(scheduled_as_bot):
    """≥2 站点且任务未点名 → 落聚合态站点：datasource_id 合法为 0，由工具逐站自解析。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    scheduled_as_bot["sources"] = [8, 9]
    scheduled_as_bot["sites"] = [_site("cn", "中国", datasource_ids=[8]),
                                 _site("us", "美国", datasource_ids=[9]),
                                 _site("global", "全球", aggregate=1)]
    profile = sa.load_profile({}, IDENTITY)
    assert profile["site_code"] == "global"
    assert profile["site_labels"] == ["中国", "美国"]
    assert profile["context"].datasource_id == 0
    assert profile["sources"] == {8, 9}


def test_named_site_narrows_to_that_site(scheduled_as_bot):
    from backend.modules.mind.execution import scheduled_analysis as sa
    scheduled_as_bot["sources"] = [8, 9]
    scheduled_as_bot["sites"] = [_site("cn", "中国", datasource_ids=[8]),
                                 _site("us", "美国", datasource_ids=[9]),
                                 _site("global", "全球", aggregate=1)]
    profile = sa.load_profile({"site_code": "cn"}, IDENTITY)
    assert profile["site_code"] == "cn"
    assert profile["context"].datasource_id == 8


def test_scope_guidance_tells_agent_to_split_by_site(scheduled_as_bot):
    """逐站不相加合计这条口径必须写进提示词（无人值守没人在现场纠偏）。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    scheduled_as_bot["sources"] = [8, 9]
    scheduled_as_bot["sites"] = [_site("cn", "中国", datasource_ids=[8]),
                                 _site("us", "美国", datasource_ids=[9]),
                                 _site("global", "全球", aggregate=1)]
    text = sa._scope_guidance(sa.load_profile({}, IDENTITY))
    assert "分站呈现" in text and "不得把各站数字相加" in text
    single = sa.load_profile({}, IDENTITY)          # 收窄到一站
    single["site_labels"] = ["中国"]
    assert "唯一可查站点" in sa._scope_guidance(single)


def test_no_authorized_datasource_fails_loud(scheduled_as_bot):
    from backend.modules.mind.execution import scheduled_analysis as sa
    scheduled_as_bot["sources"] = []
    with pytest.raises(sa.ScheduledScopeError) as exc:
        sa.load_profile({}, IDENTITY)
    assert exc.value.code == "SOURCE_REQUIRED"


def test_semantic_tools_are_mandatory(scheduled_as_bot):
    """产不出可校验数字的配置不该长出任务：目录 + 语义查询缺一不可。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    scheduled_as_bot["as_bot"]["tools"]["mcp"]["semantic"] = ["get_metrics"]
    with pytest.raises(sa.ScheduledScopeError) as exc:
        sa.load_profile({}, IDENTITY)
    assert exc.value.code == "TOOL_NOT_ALLOWED"


def test_stopped_execution_layer_is_refused(scheduled_as_bot):
    from backend.modules.mind.execution import scheduled_analysis as sa
    scheduled_as_bot["layer"]["status"] = "disabled"
    with pytest.raises(sa.ScheduledScopeError) as exc:
        sa.load_profile({}, IDENTITY)
    assert exc.value.code == "LAYER_UNAVAILABLE"


def test_workspace_mcp_tools_are_not_registered_unattended(scheduled_as_bot):
    """外部 MCP 属会话级能力：无人值守不注册，但必须给出可见原因（不是悄悄少给）。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    _grant_mcp(scheduled_as_bot)
    profile = sa.load_profile({}, IDENTITY)
    external = "mcp__external_9__propose_semantic_intent"
    assert external not in profile["tools"]
    assert sa.UNATTENDED_SKIP_REASON in profile["unavailable"][external]


def test_validate_task_only_accepts_agent_shape(scheduled_as_bot):
    """SQL 定时不在工作空间：旧 query 形态任务连执行核心的门都进不去。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    with pytest.raises(sa.ScheduledScopeError) as exc:
        sa.validate_task_as_bot({"task_type": "query", "owner_id": 7, "workspace_id": 3,
                                 "task_config": {"questions": []}})
    assert exc.value.code == "TASK_TYPE_INVALID"
    assert "数据同步" in str(exc.value)
    assert sa.validate_task_as_bot({"task_type": "agent", "owner_id": 7, "workspace_id": 3,
                                    "task_config": {"questions": [{"question": "统计"}]}})


# ── 同源裁决：无人值守复用交互链路那一份 check_tool ──────────────────────

def test_unattended_runtime_is_injected_for_guards(scheduled_as_bot):
    """不新建第二套「谁能做什么」：check_tool 依赖的 secure_runtime 由无人值守提供。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    from backend.modules.mind.execution.tool_policy import check_tool
    from backend.modules.mind.execution.models import ExecutionContext
    profile = sa.load_profile({}, IDENTITY)
    runtime = profile["context"].extra["secure_runtime"]
    assert isinstance(runtime, sa.UnattendedRuntime)
    assert runtime.layer_id == 4 and runtime.policy is profile["policy"]
    with pytest.raises(PermissionError, match="缺少可信执行会话"):
        check_tool(ExecutionContext(user_id=7, workspace_id=3), SEMANTIC_QUERY)


def test_tool_revocation_blocks_next_call(scheduled_as_bot):
    """运行中收回工具授权 → 下一次工具调用即拒（digest 变化即重发，不拿旧授权继续跑）。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    profile = sa.load_profile({}, IDENTITY)
    run = sa._ScheduledRun("统计", {}, IDENTITY, None, profile)
    asyncio.run(run.authorize(SEMANTIC_QUERY))                     # 基线放行
    scheduled_as_bot["as_bot"]["tools"]["mcp"]["semantic"] = ["get_metrics"]
    with pytest.raises(PermissionError, match="已变化"):
        asyncio.run(run.authorize(SEMANTIC_QUERY))


def test_unregistered_tool_never_reaches_guard(scheduled_as_bot):
    from backend.modules.mind.execution import scheduled_analysis as sa
    profile = sa.load_profile({}, IDENTITY)
    run = sa._ScheduledRun("统计", {}, IDENTITY, None, profile)
    with pytest.raises(sa.ScheduledScopeError) as exc:
        asyncio.run(run.authorize("mcp__datahub_query__execute_sql"))
    assert exc.value.code == "TOOL_NOT_ALLOWED"


def test_role_rebind_stops_running_task(scheduled_as_bot):
    """属主角色换绑 AS-BOT：本轮显式中止，下次执行按新配置——不静默换身份取数。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    runtime = sa.load_profile({}, IDENTITY)["context"].extra["secure_runtime"]
    runtime.verify()
    scheduled_as_bot["as_bot"].update({"as_bot_key": "ops", "name": "运维分析"})
    with pytest.raises(sa.ScheduledScopeError) as exc:
        runtime.verify()
    assert exc.value.code == "ASBOT_REQUIRED"
    runtime.closed = True
    with pytest.raises(sa.ScheduledScopeError) as exc:
        runtime.verify()
    assert exc.value.code == "RUN_CLOSED"


def test_cancellation_is_observed_at_tool_boundary(scheduled_as_bot):
    """取消/租约失效在每次工具调用边界读一次（无人在场时也要能被停下来）。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    from backend.core.task_runtime import RunInterrupted

    def check():
        raise RunInterrupted("cancelled")

    profile = sa.load_profile({}, IDENTITY)
    run = sa._ScheduledRun("统计", {}, IDENTITY, check, profile)
    with pytest.raises(RunInterrupted):
        asyncio.run(run.authorize(SEMANTIC_QUERY))


# ── 一次性任务：终态诚实、结果只取服务端事实 ─────────────────────────────

class AssistantMessage:
    def __init__(self, content): self.content = content


class UserMessage:
    def __init__(self, content): self.content = content


class ResultMessage:
    def __init__(self, result="已完成", is_error=False, subtype="success", terminal_reason=""):
        self.result, self.is_error = result, is_error
        self.subtype, self.terminal_reason = subtype, terminal_reason


class ToolUseBlock:
    def __init__(self, call_id, name, args):
        self.id, self.name, self.input = call_id, name, args


class TextBlock:
    def __init__(self, text):
        self.text = text


class ToolResultBlock:
    def __init__(self, call_id, content, is_error=False):
        self.tool_use_id, self.content, self.is_error = call_id, content, is_error


_QUERY_RECORD = {"sql": "SELECT 1", "datasource_id": 8, "dialect": "mysql",
                 "columns": [{"name": "金额"}], "rows": [{"金额": 12345}],
                 "row_count": 1, "truncated": False, "source": "run_semantic_query"}


async def _collect(run):
    from backend.modules.mind.execution.sdk_tools.context import record_unattended_query
    assert record_unattended_query(run.ctx, **_QUERY_RECORD) is True


def _scripted(steps, final=None):
    """脚本化一次性消息流：steps 是作用于 run 的异步回调（模拟运行时的工具行为）。"""
    async def stream(run):
        for step in steps:
            await step(run)
            if run.fatal is not None or run.abort_exc is not None:
                break
        yield final or ResultMessage()
    return stream


def _patch_oneshot(monkeypatch, script):
    from backend.modules.mind.execution import scheduled_analysis as sa
    monkeypatch.setattr(sa, "_build_oneshot_options", lambda run: None)
    monkeypatch.setattr(sa._ScheduledRun, "oneshot", lambda self, prompt, options: script(self))


@pytest.mark.parametrize("code,step", [
    ("NO_QUERY_RESULT", None),
    ("TOOL_NOT_ALLOWED", lambda run: run.authorize("mcp__datahub_query__execute_sql")),
    ("NEEDS_CLARIFICATION", lambda run: run.clarify({})),
])
def test_agent_never_fakes_completion_or_bypasses_tools(monkeypatch, code, step,
                                                         scheduled_as_bot):
    from backend.modules.mind.execution.scheduled_analysis import analyze_question
    _patch_oneshot(monkeypatch, _scripted([] if step is None else [step]))
    result = asyncio.run(analyze_question("统计订单", {}, IDENTITY))
    assert result["status"] == "failed" and result["error_code"] == code


def test_report_evidence_is_collected_server_side(monkeypatch, scheduled_as_bot):
    """报告数字来自服务端受控采集（含来源权限快照），模型抄写的正文不算证据。"""
    from backend.modules.mind.execution.scheduled_analysis import analyze_question
    from backend.modules.viz.services import report_service
    snapshot = {"sources": [{"datasource_id": 8, "table": "orders"}], "policy_digest": "d1"}
    guard = Mock(return_value=snapshot)
    monkeypatch.setattr(report_service, "snapshot_for_query", guard)
    _patch_oneshot(monkeypatch, _scripted([_collect],
                                          final=ResultMessage("我算了一下金额是 999")))
    out = asyncio.run(analyze_question("统计", {}, IDENTITY))
    assert out["status"] == "success"
    assert out["_analysis_results"][0]["_security_context"] == snapshot
    # 快照按服务端属主身份算（不是 LLM 能影响的参数）
    assert guard.call_args.args[0] == {"user_id": 7, "workspace_id": 3,
                                       "role": "admin", "username": "创建者"}
    # 终稿文本不进**证据**：事实表的数字只有一个来源（服务端采集）。
    # 文本本身另走 `answer`（报告重跑拿它沿用原版式），带着 degraded + “不可对外发布”才能见用户。
    assert "999" not in str(out["_analysis_results"])
    assert out["answer"] == "我算了一下金额是 999"


def test_collection_without_snapshot_evidence_fails(monkeypatch, scheduled_as_bot):
    """没有来源权限快照的结果等于没有证据：必须冒出去，不出半成品报告。"""
    from backend.modules.mind.execution.scheduled_analysis import analyze_question
    from backend.modules.viz.services import report_service
    monkeypatch.setattr(report_service, "snapshot_for_query",
                        Mock(side_effect=PermissionError("执行身份的角色未授权该分析来源")))
    _patch_oneshot(monkeypatch, _scripted([_collect]))
    out = asyncio.run(analyze_question("统计", {}, IDENTITY))
    assert out["status"] == "failed" and not out.get("_analysis_results")


def test_unattended_run_follows_the_layer_backend(monkeypatch, scheduled_as_bot):
    """执行层选到哪个后端，无人值守就用哪个（与 Chat 同一选层结果）。

    历史缺陷：``load_profile`` 不把 ``layer["backend"]`` 写进 profile，而
    ``_build_oneshot_options``/``oneshot`` 读不到就退回模块常量 qoder，于是
    直连层（claude）上的报告重跑/定时任务全部走 Qoder 网关，网关报错就变成
    “报告未就绪”（LLM_UNAVAILABLE）。
    """
    from backend.modules.mind.execution import scheduled_analysis as sa
    import backend.modules.mind.execution.sdk_tools.compat as compat
    import backend.modules.mind.execution.sdk_tools as sdk_tools
    scheduled_as_bot["layer"]["config"]["cli_name"] = "claude"
    seen_build = []
    real_build = sdk_tools.build_tool_servers

    def spy_build(backend, *a, **kw):
        seen_build.append(backend)
        return real_build(backend, *a, **kw)

    monkeypatch.setattr(sdk_tools, "build_tool_servers", spy_build)
    profile = sa.load_profile({}, IDENTITY)
    assert profile["backend"] == "claude"          # 选层结果随 profile 带走
    assert seen_build[:1] == ["claude"]            # MCP server 按同一后端建（否则工具面静默失联）

    run = sa._ScheduledRun("统计", {}, IDENTITY, None, profile)
    seen = []
    monkeypatch.setattr(compat, "load_sdk",
                        lambda backend: seen.append(backend) or SimpleNamespace(
                            query=lambda **k: None))
    run.oneshot("p", object())            # 非协程函数：直接取它用的 SDK
    assert seen[:1] == ["claude"]                   # 单发流与 options 用同一个 SDK


def test_oneshot_options_are_built_for_the_layer_backend(monkeypatch, tmp_path, scheduled_as_bot):
    """options 真的按层后端建出来（claude 层→ClaudeAgentOptions）。

    这一路靠单测跑通函数体：之前改 backend 变量时拼错一行，只有走真机才爆
    NameError（报告重跑直接 QUERY_FAILED），所以这里必须真进一次构造。
    """
    import claude_agent_sdk
    from backend.modules.mind.execution import scheduled_analysis as sa
    from backend.modules.mind.execution import (anthropic_direct, as_bots, prompt_composer,
                                                quick_paths, secure_sdk)
    from backend.core import scenario_access
    scheduled_as_bot["layer"]["config"]["cli_name"] = "claude"
    profile = sa.load_profile({}, IDENTITY)
    run = sa._ScheduledRun("统计", {}, IDENTITY, None, profile)
    run.run_dir = tmp_path

    monkeypatch.setattr(secure_sdk, "resolve_access_token", lambda *a: "tok")
    monkeypatch.setattr(prompt_composer, "compose_system_prompt", lambda *a, **kw: "SYS")
    monkeypatch.setattr(scenario_access, "visible_subject_keys", lambda *a, **kw: set())
    monkeypatch.setattr(quick_paths, "match_quick_paths", lambda *a, **kw: [])
    monkeypatch.setattr(as_bots, "load_knowledge_bases", lambda *a, **kw: [])
    monkeypatch.setattr(as_bots, "load_skills", lambda *a, **kw: [])
    monkeypatch.setattr(anthropic_direct, "apply_direct_channel",
                        lambda env, home, model, sid: {"model": model or "claude-x",
                                                       "strict_mcp_config": True})
    monkeypatch.setattr(anthropic_direct, "resolve_claude_cli_path", lambda *a: "/usr/bin/true")

    options = sa._build_oneshot_options(run)
    assert isinstance(options, claude_agent_sdk.ClaudeAgentOptions)
    assert options.mcp_servers.get("datahub_scheduled") is not None


def test_unattended_theme_tokens_follow_the_run_config(monkeypatch, tmp_path, scheduled_as_bot):
    """无人值守按本轮配置取主题令牌（报告重跑带上一版主题，定时任务没带则按默认主题包）。

    历史缺陷：这里写死 ``report_theme_id=""``，而 compose 的旧口径是"空主题就整段不注入"，
    于是定时任务/报告重跑产出的 HTML 完全没有主题依据，模型自造配色（与字模库漂移）。
    """
    from backend.modules.mind.execution import (anthropic_direct, as_bots, prompt_composer,
                                                quick_paths, secure_sdk)
    from backend.modules.mind.execution import scheduled_analysis as sa
    from backend.core import scenario_access
    profile = sa.load_profile({}, IDENTITY)
    run = sa._ScheduledRun("统计", {"report_theme": "finance"}, IDENTITY, None, profile)
    run.run_dir = tmp_path
    seen = {}

    def fake_compose(*args, **kwargs):
        seen["theme"] = kwargs.get("report_theme_id")
        return "SYS"

    monkeypatch.setattr(secure_sdk, "resolve_access_token", lambda *a: "tok")
    monkeypatch.setattr(prompt_composer, "compose_system_prompt", fake_compose)
    monkeypatch.setattr(scenario_access, "visible_subject_keys", lambda *a, **kw: set())
    monkeypatch.setattr(quick_paths, "match_quick_paths", lambda *a, **kw: [])
    monkeypatch.setattr(as_bots, "load_knowledge_bases", lambda *a, **kw: [])
    monkeypatch.setattr(as_bots, "load_skills", lambda *a, **kw: [])
    monkeypatch.setattr(anthropic_direct, "apply_direct_channel",
                        lambda env, home, model, sid: {"model": model or "claude-x",
                                                       "strict_mcp_config": True})
    monkeypatch.setattr(anthropic_direct, "resolve_claude_cli_path", lambda *a: "/usr/bin/true")
    sa._build_oneshot_options(run)
    assert seen["theme"] == "finance"


def test_stream_without_final_result_is_retryable_failure(monkeypatch, scheduled_as_bot):
    """流在终稿前中断（子进程被杀/传输断开）= 非模型结果；已采到结果时不重跑。"""
    from backend.modules.mind.execution.scheduled_analysis import analyze_question

    async def stream(run):
        await _collect(run)
        if False:                                  # pragma: no cover — 只为了让它是异步生成器
            yield None

    _patch_oneshot(monkeypatch, stream)
    out = asyncio.run(analyze_question("统计", {}, IDENTITY))
    assert out["status"] == "partial" and out["retryable"] is False
    assert out["error_code"] == "LLM_UNAVAILABLE"


def test_tool_events_in_stream_are_tracked(monkeypatch, scheduled_as_bot):
    """消息流形状（ToolUse/ToolResult）不影响终态判定：成功仍只认采集到的结果。"""
    from backend.modules.mind.execution.scheduled_analysis import analyze_question

    async def stream(run):
        yield AssistantMessage([ToolUseBlock("t1", SEMANTIC_QUERY, {"intent": {"object": "订单"}})])
        await _collect(run)
        yield UserMessage([ToolResultBlock("t1", "语义查询已完成")])
        yield ResultMessage()

    _patch_oneshot(monkeypatch, stream)
    out = asyncio.run(analyze_question("统计", {}, IDENTITY))
    assert out["status"] == "success" and len(out["_analysis_results"]) == 1


def test_success_returns_the_final_answer_text(monkeypatch, scheduled_as_bot):
    """成功时把模型终稿交回调用方（报告重跑拿它沿用原版式当报告正文）。

    旧写法只回 `_analysis_results`（取数行），报告侧拿不到叙述与图表，只能自己拼
    通用事实表格。中途带工具调用的文字是过程解说，不能当成品。
    """
    from backend.modules.mind.execution.scheduled_analysis import analyze_question

    async def stream(run):
        yield AssistantMessage([TextBlock("先算个总数")])
        await _collect(run)
        yield AssistantMessage([TextBlock("## 核心发现\n\n地图正文")])
        yield ResultMessage(result="## 核心发现\n\n地图正文")

    _patch_oneshot(monkeypatch, stream)
    out = asyncio.run(analyze_question("统计", {}, IDENTITY))
    assert out["status"] == "success"
    assert out["answer"] == "## 核心发现\n\n地图正文"
    assert len(out["_analysis_results"]) == 1


def test_answer_falls_back_to_the_last_assistant_text(monkeypatch, scheduled_as_bot):
    """ResultMessage 没带 result（不同 SDK 版本/中断路径）时取末一条助手文字，不空手。"""
    from backend.modules.mind.execution.scheduled_analysis import analyze_question

    async def stream(run):
        yield AssistantMessage([TextBlock("过程解说")])
        await _collect(run)
        yield AssistantMessage([TextBlock("最终正文")])
        yield ResultMessage(result=None)

    _patch_oneshot(monkeypatch, stream)
    out = asyncio.run(analyze_question("统计", {}, IDENTITY))
    assert out["answer"] == "最终正文"


def test_report_layout_contract_lands_in_the_prompt(scheduled_as_bot):
    """报告重跑的版式契约真的进提示词（否则“沿用原版式”只是报告侧自说自话）。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    profile = sa.load_profile({}, IDENTITY)
    plain = sa._ScheduledRun("统计", {}, IDENTITY, None, profile)
    assert "报告版式要求" not in plain.prompt        # 普通定时任务不受影响
    run = sa._ScheduledRun("统计", {"context": "背景",
                                    "layout_contract": "## 报告版式要求\n- 保留 china_map"},
                           IDENTITY, None, profile)
    assert run.prompt.startswith("统计\n任务背景：背景")
    assert run.prompt.endswith("- 保留 china_map")


def test_empty_question_never_starts_a_run():
    from backend.modules.mind.execution.scheduled_analysis import analyze_question
    out = asyncio.run(analyze_question("   ", {}, IDENTITY))
    assert out["status"] == "failed" and out["error_code"] == "EMPTY_QUESTION"


# ── 执行核心：三种派发适配器共用同一状态机与运行键 ────────────────────────

@pytest.fixture
def runtime(monkeypatch, scheduled_as_bot):
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service as service
    task = {"id": 1, "owner_id": 7, "workspace_id": 3, "is_active": True,
            "task_type": "agent", "timeout_seconds": 30, "max_retries": 0,
            "task_config": {"questions": [{"title": "统计", "question": "统计订单"}]}}
    log = {"id": 2, "scheduled_task_id": 1, "status": "queued", "run_key": "run-1"}
    monkeypatch.setattr(service, "get_task", lambda tid: task)
    monkeypatch.setattr(service, "create_log", lambda *a, **kw: 2)
    monkeypatch.setattr(service, "get_log", lambda lid: dict(log))
    def claim(*a, **kw):
        if log["status"] != "queued":
            return False
        log["status"] = "running"
        return True
    monkeypatch.setattr(service, "claim_log", claim)
    def finish(lid, **kw):
        if log["status"] != "running":
            return False
        log.update(kw)
        return True
    monkeypatch.setattr(service, "finish_log", finish)
    monkeypatch.setattr(service, "update_log", lambda lid, **kw: log.update(kw))
    monkeypatch.setattr(service, "claim_notification", lambda lid: False)
    monkeypatch.setattr(executor, "_resolve_owner", lambda t: {"user_id": 7, "workspace_id": 3})
    return task, log


def _agent_mode(status="failed", **over):
    """替身：向 run 级结果列表写入一条终态（真实链路由无人值守采集点产出）。"""
    def mode(task):
        task["_results"].append({"title": "统计", "status": status,
                                 "error": "host password SQL", **over})
        return task["_results"]
    return mode


@pytest.mark.parametrize("adapter", ["sync", "async", "celery"])
def test_adapters_share_status_and_run_key(adapter, runtime, monkeypatch):
    task, log = runtime
    monkeypatch.setattr(executor, "_execute_agent_mode", _agent_mode())
    if adapter == "sync":
        out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    elif adapter == "async":
        out = asyncio.run(executor.execute_scheduled_task_async(1, run_key="run-1"))
    else:
        out = executor.execute_scheduled_task.run(1, "manual", "run-1")
    assert out["status"] == log["status"] == "failed"
    assert "host password" not in str(log)         # 原始异常不入日志（只留状态摘要）
    assert log["questions_failed"] == 1


def test_duplicate_delivery_does_not_execute_again(runtime, monkeypatch):
    task, log = runtime
    mode = Mock(side_effect=_agent_mode("success"))
    monkeypatch.setattr(executor, "_execute_agent_mode", mode)
    executor.execute_scheduled_task_sync(1, run_key="run-1")
    executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert mode.call_count == 1


def test_non_agent_task_never_reaches_analysis(runtime, monkeypatch):
    """存量/伪造的 query 形态：在执行核心就被拒，不进入无人值守链路。"""
    task, log = runtime
    task["task_type"] = "query"
    mode = Mock()
    monkeypatch.setattr(executor, "_execute_agent_mode", mode)
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == "failed" and log["stage_error_code"] == "TASK_TYPE_INVALID"
    mode.assert_not_called()


def test_task_without_question_is_refused(runtime, monkeypatch):
    task, log = runtime
    task["task_config"] = {"questions": []}
    mode = Mock()
    monkeypatch.setattr(executor, "_execute_agent_mode", mode)
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == "failed" and log["stage_error_code"] == "EXECUTION_FAILED"
    mode.assert_not_called()


def test_cancellation_not_overwritten_by_completion(runtime, monkeypatch):
    task, log = runtime
    def mode(t):
        log["status"] = "cancelled"
        t["_results"].append({"title": "统计", "status": "success"})
        return t["_results"]
    monkeypatch.setattr(executor, "_execute_agent_mode", mode)
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == log["status"] == "cancelled"


def test_required_report_failure_is_task_failure(runtime, monkeypatch):
    task, log = runtime
    task["report_template_key"] = "1"
    # 模板解析成功（离线不连字模库）：本用例测的是“报告阶段失败”而非“模板读不到”
    import backend.modules.viz.services.report_service as rs
    monkeypatch.setattr(rs, "resolve_report_template",
                        lambda key: {"found": True, "name": "T", "format": "markdown",
                                     "theme": "", "layout": {}, "body": ""})
    monkeypatch.setattr(executor, "_execute_agent_mode", _agent_mode("success"))
    monkeypatch.setattr(executor, "_generate_report", Mock(side_effect=RuntimeError("private error")))
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == "failed"
    assert log["stage_error_code"] == "REPORT_FAILED"
    assert "private error" not in str(log)


def test_missing_report_template_fails_before_agent_run(runtime, monkeypatch):
    """设了模板却读不到 → 跑 agent 前就显式失败，不静默退通用事实表冒充“按模板生成”（§1）。"""
    task, log = runtime
    task["report_template_key"] = "999"
    import backend.modules.viz.services.report_service as rs
    monkeypatch.setattr(rs, "resolve_report_template",
                        lambda key: {"found": False, "reason": "报告模板不存在或已下架（999）"})
    agent = Mock(side_effect=AssertionError("模板不可用就不该跑 agent"))
    monkeypatch.setattr(executor, "_execute_agent_mode", agent)
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == "failed"
    agent.assert_not_called()


def test_expired_deadline_never_overwrites_timeout(runtime, monkeypatch):
    import time as _t
    task, log = runtime
    clock = [1.0]
    monkeypatch.setattr(executor.time, "monotonic", lambda: clock[0])
    def mode(t):
        clock[0] = 100.0
        t["_results"].append({"title": "统计", "status": "success"})
        return t["_results"]
    monkeypatch.setattr(executor, "_execute_agent_mode", mode)
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == log["status"] == "timeout"


def test_notification_contains_no_business_content(monkeypatch):
    """通知正文 = 渠道引用的消息模板渲染结果，且只拿得到执行元信息。

    旧用例测的是 ``notification_sender.send``（executor 直接调拼接器），现在正文唯一来源是模板
    （``config.message_template_key``）：护栏不能只写在提示词里，``build_context`` 只送状态与计数，
    任务名、报告正文、数据行根本进不到渲染面。
    """
    from backend.modules.flow.services import message_template_service as mts
    from backend.modules.flow.services.notification_service import notification_service
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service as service

    channel = {"id": 1, "channel_type": "dingtalk", "workspace_id": 3, "is_active": True,
               "config": {"webhook_url": "https://x", "message_template_key": "mt_test"}}
    sent = {}
    monkeypatch.setattr(service, "get_channel", lambda cid: channel)
    monkeypatch.setattr(service, "update_channel_test_status", lambda *a: None)
    monkeypatch.setattr(notification_service, "get_channel", lambda cid: channel)
    monkeypatch.setattr(notification_service, "_send_dingtalk",
                        lambda config, content, title: sent.update(content=content, title=title) or {"ok": True})
    monkeypatch.setattr(mts.message_template_service, "resolve_reference", lambda key: {
        "found": True,
        "template": {"name": "测试文案", "template_kind": "im", "format": "markdown",
                     "subject": "", "content": "状态：{{status}}（成功 {{succeeded}} / 共 {{total}}）"}})

    out = executor._send_notification({"channel_id": 1, "workspace_id": 3, "name": "敏感任务名"},
                                      [{"status": "failed", "title": "患者", "rows": [[999]]}],
                                      report_content="敏感正文")
    assert out == "sent"
    assert "failed" in sent["content"]
    assert not any(term in sent["content"] for term in ("敏感", "患者", "999", "任务名"))


# ── Agent 有限重试：瞬时故障重试，需人工处理不重试 ────────────────────────

def _agent_task(**cfg):
    base = {"questions": [{"title": "t", "question": "统计"}]}
    base.update(cfg)
    return {"task_config": base, "_identity": {"user_id": 7, "workspace_id": 3},
            "_deadline": _now() + 60, "_log_id": 1, "_stop_event": None,
            "max_retries": base.pop("max_retries", 0), "_results": []}


def _now():
    import time
    return time.monotonic()


def _patch_analyze(monkeypatch, *responses):
    """替身：`mind.analyze_question` 经回调表按属性名实时解析，因此直接 monkeypatch 模块属性。"""
    from backend.modules.mind.execution import scheduled_analysis as sa
    calls = []
    async def fake(question, config, identity, check=None):
        calls.append(question)
        return responses[min(len(calls) - 1, len(responses) - 1)]
    monkeypatch.setattr(sa, "analyze_question", fake)
    return calls


def test_agent_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(executor, "_check_running", lambda task: None)
    monkeypatch.setattr(executor, "_persist_progress", lambda task: None)
    calls = _patch_analyze(monkeypatch,
                           {"status": "failed", "error_code": "LLM_UNAVAILABLE", "retryable": True},
                           {"status": "success", "_analysis_results": [{"status": "success"}]})
    out = executor._execute_agent_mode(_agent_task(max_retries=2))
    assert out[0]["status"] == "success" and out[0]["attempts"] == 2
    assert calls == ["统计", "统计"]


def test_agent_non_retryable_not_repeated(monkeypatch):
    monkeypatch.setattr(executor, "_check_running", lambda task: None)
    monkeypatch.setattr(executor, "_persist_progress", lambda task: None)
    calls = _patch_analyze(monkeypatch,
                           {"status": "failed", "error_code": "QUERY_REJECTED",
                            "needs_attention": True})
    out = executor._execute_agent_mode(_agent_task(max_retries=3))
    assert out[0]["status"] == "failed" and out[0]["error_code"] == "QUERY_REJECTED"
    assert len(calls) == 1          # 需人工处理，不盲目重试


def test_agent_partial_results_are_not_retried(monkeypatch):
    """已采到受控结果的失败不再重跑：重跑等于对同一问题出两份数字。"""
    monkeypatch.setattr(executor, "_check_running", lambda task: None)
    monkeypatch.setattr(executor, "_persist_progress", lambda task: None)
    calls = _patch_analyze(monkeypatch,
                           {"status": "failed", "error_code": "LLM_UNAVAILABLE", "retryable": True,
                            "_analysis_results": [{"status": "success"}]})
    out = executor._execute_agent_mode(_agent_task(max_retries=2))
    assert out[0]["attempts"] == 1 and len(calls) == 1


# ── 时区 Beat：墙钟分钟匹配 + DST 安全运行键 ──────────────

def test_wallclock_run_key_uses_task_timezone():
    from datetime import datetime, timezone
    from backend.modules.flow.tasks.beat_schedule import WallClockCrontab
    now = datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc)  # 00:00 EDT / 12:00 CST
    assert WallClockCrontab("0 0 * * *", "America/New_York").run_key(5, now=now) == \
        "cron:5:America/New_York:202609210000"
    assert WallClockCrontab("0 0 * * *", "Asia/Shanghai").run_key(5, now=now) == \
        "cron:5:Asia/Shanghai:202609211200"


def test_wallclock_is_due_matches_local_not_utc(monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from backend.modules.flow.tasks.beat_schedule import WallClockCrontab
    cron = WallClockCrontab("30 9 * * *", "America/New_York")
    local = datetime(2026, 3, 10, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(cron, "now", lambda: local)
    due, _ = cron.is_due(local - timedelta(minutes=5))
    assert due
    # 同一 UTC 瞬间在别处不是 09:30 → 不触发
    monkeypatch.setattr(cron, "now", lambda: local.astimezone(ZoneInfo("UTC")))
    off = datetime(2026, 3, 10, 10, 30, tzinfo=ZoneInfo("America/New_York"))
    due2, _ = cron.is_due(off - timedelta(minutes=5))
    assert not due2


def test_wallclock_no_backfill_after_downtime(monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from backend.modules.flow.tasks.beat_schedule import WallClockCrontab
    cron = WallClockCrontab("0 * * * *", "Asia/Shanghai")
    now = datetime(2026, 9, 21, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(cron, "now", lambda: now)
    # 上次运行是昨天：当前 15:30 不匹配整点 → 不补跑
    due, _ = cron.is_due(now - timedelta(days=1))
    assert not due


def test_beat_generates_run_before_publish(monkeypatch):
    from celery.beat import Scheduler, ScheduleEntry
    from backend.modules.flow.tasks.beat_schedule import DatabaseScheduler
    from backend.modules.flow.tasks.celery_app import app
    from backend.modules.flow.services.scheduled_task_service import scheduled_task_service as service
    calls = []
    monkeypatch.setattr(service, "get_task", lambda tid: {"id": tid, "owner_id": 7, "is_active": 1, "workspace_id": 3})
    monkeypatch.setattr(service, "create_log", lambda *a, **kw: calls.append(("log", kw["run_key"])))
    monkeypatch.setattr(Scheduler, "apply_async", lambda self, entry, *a, **kw: calls.append(("publish", entry.kwargs["run_key"])))
    scheduler = DatabaseScheduler(app=app, lazy=True)
    entry = ScheduleEntry(name="task", task="backend.modules.flow.tasks.executor.execute_scheduled_task", args=(1,), app=app)
    scheduler.apply_async(entry)
    assert calls[0][0] == "log" and calls[1][0] == "publish"
    assert calls[0][1] == calls[1][1]


# ── Beat 短租约：丢锁/故障暂停派发，不双发 ─────────────────

class _FakeLock:
    def __init__(self, fail=False, lost=False):
        self.fail, self.lost = fail, lost
        self.acquired = self.reacquired = self.released = 0
    def acquire(self, blocking=False):
        if self.fail:
            import redis
            raise redis.exceptions.RedisError("down")
        self.acquired += 1
        return True
    def reacquire(self):
        if self.lost:
            import redis
            raise redis.exceptions.LockNotOwnedError()
        self.reacquired += 1
    def release(self):
        self.released += 1


def _lease(lock):
    from backend.modules.flow.tasks.celery_app import BeatLease
    class Client:
        def lock(self, *a, **k):
            return lock
    return BeatLease(client=Client())


def test_beat_lease_renew_and_release():
    lock = _FakeLock()
    lease = _lease(lock)
    assert lease.renew() and lease.held
    assert lease.renew() and lock.reacquired == 1
    lease.close()
    assert not lease.held and lock.released == 1


def test_beat_lease_redis_failure_pauses_dispatch():
    lease = _lease(_FakeLock(fail=True))
    assert not lease.renew() and not lease.held


def test_beat_lease_lost_lock_reacquires():
    lock = _FakeLock(lost=True)
    lease = _lease(lock)
    lease.held = True  # 以为持有，实际已丢
    assert lease.renew() and lock.acquired == 1


def test_beat_tick_pauses_without_lease(monkeypatch):
    from celery.beat import Scheduler
    from backend.modules.flow.tasks.beat_schedule import DatabaseScheduler
    from backend.modules.flow.tasks.celery_app import app
    monkeypatch.setattr(Scheduler, "__init__", lambda self, *a, **k: None)
    sched = DatabaseScheduler.__new__(DatabaseScheduler)
    sched._lease = _lease(_FakeLock(fail=True))
    monkeypatch.setattr(Scheduler, "tick", lambda self: (_ for _ in ()).throw(AssertionError("不应派发")))
    assert sched.tick() == 1.0


# ── 取消：阻塞取数期间取消尽快返回，不发布迟到结果 ─────

def test_guarded_call_stops_on_cancel_event():
    import threading, time as _t
    from backend.core.task_runtime import guarded_call, RunInterrupted
    stop, started = threading.Event(), threading.Event()
    def slow():
        started.set()
        _t.sleep(5)
        return "late-result"
    def cancel_soon():
        started.wait(2)
        stop.set()
    threading.Thread(target=cancel_soon, daemon=True).start()
    with pytest.raises(RunInterrupted):
        guarded_call(slow, check=lambda: None, stop_event=stop)


def test_cancelled_run_discards_late_result(runtime, monkeypatch):
    import threading, time as _t
    task, log = runtime
    released = threading.Event()
    def slow_mode(t):
        released.wait(3)
        t["_results"].append({"title": "统计", "status": "success"})
        return t["_results"]
    monkeypatch.setattr(executor, "_execute_agent_mode", slow_mode)
    threading.Thread(target=lambda: (_t.sleep(0.15), log.__setitem__("status", "cancelled")), daemon=True).start()
    threading.Thread(target=lambda: (_t.sleep(0.6), released.set()), daemon=True).start()
    out = executor.execute_scheduled_task_sync(1, run_key="run-1")
    assert out["status"] == log["status"] == "cancelled"
    assert log.get("questions_succeeded", 0) == 0
