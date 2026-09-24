"""system 工具组 — 本系统运营/可观测只读能力（AS-BOT 聚焦本系统）。

AS-BOT 是应用内运维助手，回答“本系统”的用量/健康/建模队列等问题应走这里，
而不是拿业务本体（如 test-alb）知识检索充数。

安全：
- 全部只读；仅管理员可用（经 resolve_execution_owner 服务端解析身份 + 角色，fail-closed）。
- 复用既有 observability_service / 元库聚合，不新建取数通道、不连业务数据源。
- 不回显数据源主机/账号/密码等基础设施细节。
"""

import logging
from datetime import date, datetime, timedelta
from typing import Annotated, Optional

from services.datamind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

_ENTRYPOINTS = ("chat", "playground", "agent", "scheduled", "embed", "report")


def _admin_identity():
    """服务端解析当前调用者身份；非管理员返回 None（fail-closed）。"""
    from services.datamind.execution.sdk_tools.context import get_execution_context
    from services.shared.common.auth import resolve_execution_owner
    ctx = get_execution_context()
    uid = int((ctx.user_id if ctx else 0) or 0)
    ws = int((ctx.workspace_id if ctx else 0) or 0)
    if uid <= 0:
        return None
    try:
        identity = resolve_execution_owner(uid, ws)
    except Exception:
        return None
    return identity if identity.get("role") == "admin" else None


def _period(args) -> tuple[str, str]:
    """解析时间范围：显式 start/end 优先，否则按 days（默认 3）回看。"""
    start = (args.get("start") or "").strip()
    end = (args.get("end") or "").strip()
    if start and end:
        return start, end
    try:
        days = max(1, min(int(args.get("days") or 3), 365))
    except (TypeError, ValueError):
        days = 3
    today = date.today()
    return (today - timedelta(days=days - 1)).isoformat(), (end or today.isoformat())


def _count(sql: str, params: tuple = ()):
    """单值计数，best-effort：任何异常（表/列缺失）降级为 None，不阻断总览。"""
    from services.shared.common.db import execute_query
    try:
        row = execute_query(sql, params, fetchone=True)
        return int(list(row.values())[0]) if row else 0
    except Exception as e:  # noqa: BLE001
        logger.debug("[system_overview] count skipped: %s", e)
        return None


async def system_usage(args):
    """本系统 LLM 交互用量/活跃（复用 observability_service，仅管理员）。"""
    identity = _admin_identity()
    if not identity:
        return _text({"error": "系统用量仅管理员可查，当前身份无权限"}, is_error=True)
    from services.authservice.services import observability_service as obs

    start, end = _period(args)
    entrypoint = (args.get("entrypoint") or "").strip()
    if entrypoint and entrypoint not in _ENTRYPOINTS:
        entrypoint = ""
    params = {
        "start": start, "end": end, "entrypoint": entrypoint,
        "workspace_id": int(args.get("workspace_id") or 0),
        "status": (args.get("status") or "").strip(),
    }
    group_by = (args.get("group_by") or "summary").strip()
    try:
        if group_by == "user":
            items = obs.usage_by_user(params)
            return _text({"period": {"start": start, "end": end}, "entrypoint": entrypoint or "all",
                          "group_by": "user", "users": items[:50], "user_count": len(items)})
        if group_by == "model":
            items = obs.usage_by_model(params)
            return _text({"period": {"start": start, "end": end}, "entrypoint": entrypoint or "all",
                          "group_by": "model", "models": items[:50]})
        result = obs.usage_summary(params)
        summary = result.get("summary") or {}
        return _text({
            "period": {"start": start, "end": end}, "entrypoint": entrypoint or "all",
            "group_by": "summary",
            "active_users": summary.get("users"), "turns": summary.get("turns"),
            "sessions": summary.get("sessions"), "llm_calls": summary.get("llm_calls"),
            "total_tokens": summary.get("total_tokens"), "credits": summary.get("credits"),
            "cost_usd": summary.get("cost_usd"), "errors": summary.get("errors"),
            "thumbs_up": summary.get("thumbs_up"), "thumbs_down": summary.get("thumbs_down"),
            "has_usage": bool((summary.get("turns") or 0) > 0),
            "daily": result.get("daily") or [],
            "note": "数据来自本系统 LLM 交互可观测（adh_llm_traces）。active_users>0 即该期间有用户使用。",
        })
    except Exception as e:  # noqa: BLE001
        logger.error("[system_usage] failed: %s", e)
        return _text({"error": "系统用量查询未完成，请稍后重试或查看『LLM 交互可观测』看板"}, is_error=True)


async def system_overview(args):
    """本系统运行总览：数据源/元数据/本体/知识库/待办队列/近24h任务（仅管理员，只读）。"""
    identity = _admin_identity()
    if not identity:
        return _text({"error": "系统总览仅管理员可查，当前身份无权限"}, is_error=True)
    overview = {
        "datasources": _count("SELECT COUNT(*) FROM adh_datasources"),
        "datasources_active": _count("SELECT COUNT(*) FROM adh_datasources WHERE is_active = 1"),
        "tables": _count("SELECT COUNT(*) FROM adh_table_info WHERE is_active = 1"),
        "columns": _count("SELECT COUNT(*) FROM adh_column_metadata WHERE is_active = 1"),
        "knowledge_bases_active": _count("SELECT COUNT(*) FROM adh_knowledge_bases WHERE status = 'active'"),
        "ontology_models_system": _count(
            "SELECT COUNT(*) FROM adh_ontology_models WHERE (datasource_id IS NULL OR datasource_id = 0) "
            "AND status IN ('active','draft')"),
        "ontology_models_business": _count(
            "SELECT COUNT(*) FROM adh_ontology_models WHERE datasource_id > 0 AND status IN ('active','draft')"),
        "pending_approvals": _count("SELECT COUNT(*) FROM adh_as_bot_approvals WHERE status = 'pending'"),
        "pending_alias_suggestions": _count("SELECT COUNT(*) FROM adh_alias_suggestions WHERE status = 'pending'"),
        "tasks_failed_24h": _count(
            "SELECT COUNT(*) FROM adh_scheduled_logs WHERE status = 'failed' "
            "AND started_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)"),
        "tasks_running": _count("SELECT COUNT(*) FROM adh_scheduled_logs WHERE status IN ('queued','running')"),
    }
    return _text({"generated_at": datetime.now().isoformat(timespec="seconds"),
                  "overview": overview,
                  "note": "系统级只读快照；None 表示该项统计不可用（表/列缺失），不代表 0。"})


TOOL_SPECS = [
    {
        "name": "system_usage",
        "description": (
            "查询【本系统】的 LLM 交互用量与活跃情况（来自 adh_llm_traces 可观测数据，仅管理员）。"
            "回答“近N天有没有用户用 chat/数据分析”“交互可观测用量/活跃用户/token/credit/错误率”"
            "这类**系统运营**问题必须用本工具，禁止用 knowledge_search 拿业务本体充数。"
            "group_by=summary 返回期间活跃用户数/回合/会话/token/credit/错误 + 按日趋势；"
            "user 返回按用户 rollup；model 返回按模型 rollup。"
        ),
        "schema": {
            "days": Annotated[Optional[int], "回看天数（默认 3；start/end 未给时生效，1-365）"],
            "start": Annotated[Optional[str], "起始日期 YYYY-MM-DD（与 end 一起用，优先于 days）"],
            "end": Annotated[Optional[str], "结束日期 YYYY-MM-DD"],
            "entrypoint": Annotated[Optional[str], "入口过滤：chat/playground/agent/scheduled/embed/report；空=全部"],
            "workspace_id": Annotated[Optional[int], "按工作空间过滤；0=全部"],
            "status": Annotated[Optional[str], "按状态过滤：success/error/cancelled；空=全部"],
            "group_by": Annotated[Optional[str], "汇总维度：summary（默认）/user/model"],
        },
        "handler": system_usage,
    },
    {
        "name": "system_overview",
        "description": (
            "获取【本系统】运行总览快照（仅管理员，只读）：数据源/表/列元数据数量、"
            "系统本体与业务本体模型数、启用知识库数、待审批动作数、待处理别名建议数、"
            "近24h失败任务数与运行中任务数。用于回答“系统现在什么状况/有多少待办/建模进度”等运维问题。"
        ),
        "schema": {},
        "handler": system_overview,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_system_server(backend: str = "qoder", tool_names=None):
    """构建 system 进程内 MCP server（qoder / claude）。tool_names 给定时按 waker 逐工具粒度注册。"""
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool

    specs = list(TOOL_SPECS)
    if tool_names is not None:
        selected = set(tool_names)
        specs = [s for s in specs if s["name"] in selected]
    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"],
                  s["handler"], annotations=READONLY_ANNOTATIONS)
        for s in specs
    ]
    return make_server(backend, "datahub_system", tools)
