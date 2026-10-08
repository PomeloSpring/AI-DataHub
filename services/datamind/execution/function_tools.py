"""功能能力工具组 — 把「菜单/权限码背后的平台功能」登记为 LLM 可调用动作。

与「系统数据工具」的区别（见 perm_link 模块顶部）：这里处理的是**平台功能操作**
（报表、看板、调度任务、质量检核、数据集…），不是 nl2sql / execute_sql /
知识库检索那类取数通道。授权从**角色权限码自动继承**（AS-BOT 仅做减法），
级别由 ``adh_perm_registry.ai_access`` 裁定，涉密项由 ``perm_link`` 硬收窄。

安全设计（三条，缺一不可）
--------------------------
1. **显式列白名单**：所有只读视图都写死 SELECT 列，绝不 ``SELECT *``。
   这样即使表结构后续加了 ``webhook_secret`` 之类的新列，也不会自动漏给 LLM。
   （实测：``ScheduledTaskService.list_tasks`` 就是 ``SELECT *`` 且不过滤
   ``webhook_token``/``webhook_secret``，直接复用必然泄密。）
2. **写动作薄委托**：写操作复用既有服务入口（``quality_engine.execute_single_rule`` /
   ``ScheduledTaskService.create_task``），不新建第二条写通道（fde-evolution §2）；
   返回值同样投影成安全摘要。
3. **出站兜底**：即使白名单写漏了，``outbound_guard.assert_no_sensitive``
   在 ``compat.make_tool`` 的收口处还会拦一道（fail-loud，不静默清空）。

身份一律服务端解析（``resolve_execution_owner``），不信任工具入参传来的
user_id / workspace_id（security-guardrails §2）。
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

READONLY_ANNOTATIONS = {"readOnlyHint": True}

SERVER_NAME = "datahub_functions"


# ── 共用小工具 ────────────────────────────────────────────────────────────────

def _text(data, is_error: bool = False) -> dict:
    """统一 CallToolResult 构造（content 块 + MCP 标准 isError 键）。"""
    body = data if isinstance(data, str) else json.dumps(
        data, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": body}],
            **({"isError": True} if is_error else {})}


def _identity():
    """服务端解析调用者身份，失败返回 None（fail-closed）。"""
    from services.datamind.execution.sdk_tools.context import get_execution_context
    from services.shared.common.auth import resolve_execution_owner
    ctx = get_execution_context()
    if ctx is None:
        return None
    uid = int((ctx.user_id or 0))
    if uid <= 0:
        return None
    try:
        return resolve_execution_owner(uid, int((ctx.workspace_id or 0)))
    except Exception:  # noqa: BLE001
        logger.exception("[function_tools] 身份解析失败")
        return None


def _select(sql: str, params: tuple = ()) -> list[dict]:
    from services.shared.common.db import execute_query
    rows = execute_query(sql, params) or []
    return [dict(r) for r in rows]


def _iso(row: dict, *fields):
    for f in fields:
        v = row.get(f)
        if hasattr(v, "isoformat"):
            row[f] = v.isoformat()
    return row


def _limit(args, default: int = 50, cap: int = 200) -> int:
    try:
        return max(1, min(int(args.get("limit") or default), cap))
    except (TypeError, ValueError):
        return default


# ═════════════════════════════════════════════════════════════════════════════
# 只读安全视图 — 显式列白名单，绝不 SELECT *
# ═════════════════════════════════════════════════════════════════════════════

def _list_reports(args):
    """报表清单：只回标题/格式/状态/浏览次数。不含正文、分享令牌、证据摘要。"""
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    rows = _select(
        "SELECT id, title, format, access_mode, generation_status, publication_status, "
        "       view_count, created_at "
        "FROM adh_reports WHERE workspace_id = %s ORDER BY created_at DESC LIMIT %s",
        (int(ident.get("workspace_id") or 0), _limit(args)))
    return _text({"reports": [_iso(r, "created_at") for r in rows],
                  "note": "仅含业务摘要；报表正文与分享令牌不在 AI 可见范围内"})


def _list_dashboards(args):
    """看板清单：可见范围复用 visible_dashboard_ids（角色授权唯一裁决）。"""
    from services.dataviz.services.dashboard_service import visible_dashboard_ids
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    ws = int(ident.get("workspace_id") or 0)
    ids = visible_dashboard_ids(int(ident["user_id"]), ident.get("role") or "", ws)
    if not ids:
        return _text({"dashboards": [], "note": "当前角色无可见看板"})
    ids = list(ids)[:_limit(args)]
    ph = ",".join(["%s"] * len(ids))
    rows = _select(
        "SELECT id, name, description, status, is_public, is_default, sort_order, updated_at "
        f"FROM adh_dashboards WHERE id IN ({ph}) ORDER BY sort_order", tuple(ids))
    return _text({"dashboards": [_iso(r, "updated_at") for r in rows],
                  "note": "可见范围以角色授权为准；布局与筛选参数不在 AI 可见范围内"})


def _list_scheduled_tasks(args):
    """调度任务清单：只回名称/周期/状态。不含 Webhook 密钥、任务配置、上次错误原文。"""
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    rows = _select(
        "SELECT id, name, description, task_type, cron_expression, trigger_type, timezone, "
        "       is_active, last_run_at, last_status, run_count, created_at "
        "FROM adh_scheduled_tasks WHERE workspace_id = %s ORDER BY created_at DESC LIMIT %s",
        (int(ident.get("workspace_id") or 0), _limit(args)))
    return _text({"tasks": [_iso(r, "last_run_at", "created_at") for r in rows],
                  "note": "Webhook 密钥与上次错误原文不在 AI 可见范围内"})


def _list_quality_rules(args):
    """质量规则清单：只回名称/类型/严重程度。不含物理表列名与规则表达式。"""
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    rows = _select(
        "SELECT id, rule_name, description, rule_type, severity, is_active, created_at "
        "FROM adh_quality_rules WHERE workspace_id = %s ORDER BY created_at DESC LIMIT %s",
        (int(ident.get("workspace_id") or 0), _limit(args)))
    return _text({"rules": [_iso(r, "created_at") for r in rows],
                  "note": "物理表列名与规则表达式不在 AI 可见范围内"})


def _list_datasets(args):
    """数据集清单：只回名称/来源类型/预设图表。不含 SQL 原文与字段配置。"""
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    rows = _select(
        "SELECT id, name, description, source_type, object_key, chart_type, status, "
        "       created_at, updated_at "
        "FROM adh_datasets WHERE status = 'active' "
        "ORDER BY updated_at DESC LIMIT %s",
        (_limit(args),))
    return _text({"datasets": [_iso(r, "created_at", "updated_at") for r in rows],
                  "note": "SQL 原文与字段配置不在 AI 可见范围内"})


def _list_registered_datasources(args):
    """数据源清单（安全视图）：只回名称/类型。

    这是**平台资源清单**（供按名选源），与 catalog 工具组的 ``list_datasources``
    （返回当前 AS-BOT 授权集内的查询候选）用途不同，不构成第二条取数通道。
    连接地址 / 账号 / 密码 / 连接串一律不查、不回（security-guardrails §7）。
    注：adh_datasources 无启用/停用状态列，只列已注册项。
    """
    rows = _select(
        "SELECT name, db_type, created_at FROM adh_datasources "
        "ORDER BY name LIMIT %s", (_limit(args),))
    return _text({"datasources": [_iso(r, "created_at") for r in rows],
                  "note": "仅含名称/类型；连接地址与凭据不在 AI 可见范围内"})


def _list_knowledge_bases(args):
    """知识库清单：只回名称/类型/状态。不含接入配置（notebook_id / 凭据）。"""
    rows = _select(
        "SELECT id, name, kb_type, status, created_at FROM adh_knowledge_bases "
        "WHERE status = 'active' ORDER BY name LIMIT %s", (_limit(args),))
    return _text({"knowledge_bases": [_iso(r, "created_at") for r in rows],
                  "note": "接入配置与凭据不在 AI 可见范围内"})


# ═════════════════════════════════════════════════════════════════════════════
# 写动作 — 薄委托到既有服务入口，返回安全摘要
# ═════════════════════════════════════════════════════════════════════════════

def _run_quality_check(args):
    """触发一次质量检核（委托 quality_engine.execute_single_rule）。

    只回通过率统计。``target_table``（物理表名）、``execution``（内部标识）、
    ``detail_samples``（业务数据行采样）一律不下发（security-guardrails §7）。
    """
    from services.datagov.services.quality_engine import execute_single_rule
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)

    try:
        rule_id = int(args.get("rule_id") or 0)
    except (TypeError, ValueError):
        return _text({"error": "rule_id 必须是整数"}, is_error=True)
    if rule_id <= 0:
        return _text({"error": "缺少 rule_id（请先用 list_quality_rules 取得规则ID）"}, is_error=True)

    rules = _select(
        "SELECT id, workspace_id, rule_name, rule_type, rule_config, target_table, "
        "       target_column, severity "
        "FROM adh_quality_rules WHERE id = %s", (rule_id,))
    if not rules:
        return _text({"error": f"质量检核规则 {rule_id} 不存在"}, is_error=True)

    try:
        # include_samples=False: 样本是业务数据行，不进 AI 视野
        result = execute_single_rule(rules[0], dict(ident),
                                     include_samples=False, persist=True)
    except PermissionError as exc:
        return _text({"error": str(exc)}, is_error=True)
    except Exception:  # noqa: BLE001
        logger.exception("[function_tools] 质量检核执行失败 rule_id=%s", rule_id)
        return _text({"error": "质量检核执行失败，详见服务端日志"}, is_error=True)

    # 显式投影：只留统计口径，物理表名/内部标识/采样全部剥除
    return _text({
        "rule_name": result.get("rule_name"),
        "rule_type": result.get("rule_type"),
        "passed": result.get("passed"),
        "total_rows": result.get("total_rows"),
        "failed_rows": result.get("failed_rows"),
        "pass_rate": result.get("pass_rate"),
        "elapsed_ms": result.get("elapsed_ms"),
        "check_time": result.get("check_time"),
    })


class _InlineBackgroundTasks:
    """给 report_service.submit_report 的 BackgroundTasks 替身。

    服务层按 ``ADH_TASK_EXECUTION_MODE`` 决定怎么跑：
      * ``celery``（默认）—— 不用 background_tasks，直接投递队列；
      * ``background`` —— 调 ``add_task``，在 HTTP 响应发送后执行。
    工具调用没有 HTTP 响应周期，所以这里**当场同步执行**，而不是把任务存起来
    永远不跑（那会变成假成功，违反 no-silent-degradation §1）。
    """

    def add_task(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


def _generate_report(args):
    """生成分析报告（委托 report_service.submit_report，唯一报告入口）。

    不新建第二条报告通道（fde-evolution §2）：submit_report 内部会做工作空间授权、
    保存可追踪运行记录、派发任务。分析来源三选一（dataset_id / intent /
    saved_query_id）由服务层校验；intent 里的裸 SQL 会被 parse_intent 拒收。
    返回值只含报告标识与状态，正文与安全上下文不下发。
    """
    from fastapi import HTTPException
    from services.dataviz.services import report_service
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)

    sources = {k: args.get(k) for k in ("dataset_id", "intent", "saved_query_id")
               if args.get(k) is not None}
    if len(sources) != 1:
        return _text({"error": "分析来源必须且只能指定一种：dataset_id / intent / saved_query_id"},
                     is_error=True)

    request = {"workspace_id": int(ident.get("workspace_id") or 0),
               "title": str(args.get("title") or "分析报告").strip() or "分析报告"}
    request.update(sources)

    try:
        result = report_service.submit_report(request, dict(ident), _InlineBackgroundTasks())
    except HTTPException as exc:
        return _text({"error": str(exc.detail)}, is_error=True)
    except (PermissionError, ValueError) as exc:
        return _text({"error": str(exc)}, is_error=True)
    except Exception:  # noqa: BLE001
        logger.exception("[function_tools] 报表生成提交失败")
        return _text({"error": "报表生成提交失败，详见服务端日志"}, is_error=True)

    # 显式投影：不回 content / security_context / evidence_summary / analysis_source
    return _text({
        "report_id": result.get("report_id"),
        "run_id": result.get("run_id"),
        "generation_status": result.get("generation_status"),
        "title": request["title"],
        "note": "已提交生成；正文与安全上下文不在 AI 可见范围内",
    })


def _create_scheduled_task(args):
    """创建调度任务（委托 ScheduledTaskService.create_task）。

    Webhook Token / Secret 由服务端生成，**一律不回显**；返回值只留任务标识与状态。
    """
    from services.dataflow.services.scheduled_task_service import ScheduledTaskService
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)

    name = str(args.get("name") or "").strip()
    cron = str(args.get("cron_expression") or "").strip()
    task_type = str(args.get("task_type") or "query").strip()
    if not name:
        return _text({"error": "缺少任务名称"}, is_error=True)
    if not cron:
        return _text({"error": "缺少执行周期表达式 cron_expression"}, is_error=True)
    if task_type not in ("query", "agent"):
        return _text({"error": "task_type 只能是 query 或 agent"}, is_error=True)

    ws = int(ident.get("workspace_id") or 0)
    data = {
        "name": name,
        "description": str(args.get("description") or ""),
        "task_type": task_type,
        "cron_expression": cron,
        "timezone": str(args.get("timezone") or "Asia/Shanghai"),
        "is_active": 1,
    }
    # 任务配置（SQL 列表 / Agent 问题列表）由调用方给，但绝不由本工具回显
    config = args.get("task_config")
    if config is not None:
        if not isinstance(config, dict):
            return _text({"error": "task_config 必须是对象"}, is_error=True)
        data["task_config"] = config

    try:
        task_id = ScheduledTaskService().create_task(
            data, owner_id=int(ident["user_id"]), workspace_id=ws)
    except (PermissionError, ValueError) as exc:
        return _text({"error": str(exc)}, is_error=True)
    except Exception:  # noqa: BLE001
        logger.exception("[function_tools] 创建调度任务失败")
        return _text({"error": "创建调度任务失败，详见服务端日志"}, is_error=True)

    return _text({
        "created": True,
        "task_id": task_id,
        "name": name,
        "cron_expression": cron,
        "task_type": task_type,
        "note": "Webhook Token/Secret 由服务端生成并保存，不在 AI 可见范围内",
    })


# ═════════════════════════════════════════════════════════════════════════════
# 动作注册表 — 只有登记在此的 action 才会生成 LLM 工具
# ═════════════════════════════════════════════════════════════════════════════
#
# perm_code  : 授权来源（角色权限码，自动继承）
# object_key : 系统本体对象（语义 grounding，见 scripts/seed_system_ontology_objects.py）
# read_only  : 是否只读；写动作必须 ai_access='write' 才会注册
# handler    : 工具实现（已做安全投影）

FUNCTION_ACTION_SPECS: dict[str, dict] = {
    "report.list": {
        "tool_name": "list_reports",
        "label": "查看报表清单",
        "description": "列出当前工作空间的分析报表摘要（标题/格式/状态/浏览次数）。"
                       "不返回报表正文与分享令牌。",
        "perm_code": "report:read", "object_key": "report", "read_only": True,
        "schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"}},
            "required": []},
        "handler": _list_reports,
    },
    "dashboard.list": {
        "tool_name": "list_dashboards",
        "label": "查看看板清单",
        "description": "列出当前角色可见的可视化看板（名称/说明/状态）。"
                       "可见范围以角色授权为准；不返回布局与筛选参数。",
        "perm_code": "dashboard:read", "object_key": "dashboard", "read_only": True,
        "schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"}},
            "required": []},
        "handler": _list_dashboards,
    },
    "scheduled_task.list": {
        "tool_name": "list_scheduled_tasks",
        "label": "查看调度任务",
        "description": "列出当前工作空间的调度任务（名称/执行周期/状态）。"
                       "不返回 Webhook 密钥与上次错误原文。",
        "perm_code": "scheduled:read", "object_key": "scheduled_task", "read_only": True,
        "schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"}},
            "required": []},
        "handler": _list_scheduled_tasks,
    },
    "quality_rule.list": {
        "tool_name": "list_quality_rules",
        "label": "查看质量检核规则",
        "description": "列出当前工作空间的数据质量检核规则（名称/类型/严重程度）。"
                       "不返回物理表列名与规则表达式。",
        "perm_code": "quality:read", "object_key": "quality_rule", "read_only": True,
        "schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"}},
            "required": []},
        "handler": _list_quality_rules,
    },
    "dataset.list": {
        "tool_name": "list_datasets",
        "label": "查看数据集",
        "description": "列出可访问的平台数据集（名称/来源类型/可见范围）。"
                       "不返回 SQL 原文与字段配置。",
        "perm_code": "dataset:read", "object_key": "dataset", "read_only": True,
        "schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"}},
            "required": []},
        "handler": _list_datasets,
    },
    "datasource.list_safe": {
        "tool_name": "list_registered_datasources",
        "label": "查看数据源清单",
        "description": "列出已注册的数据源（名称/类型），供按名选源。"
                       "这是平台资源清单，不返回连接地址、账号与凭据。",
        "perm_code": "datasource:read", "object_key": "datasource", "read_only": True,
        "schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"}},
            "required": []},
        "handler": _list_registered_datasources,
    },
    "knowledge_base.list": {
        "tool_name": "list_knowledge_bases",
        "label": "查看知识库清单",
        "description": "列出启用中的知识库（名称/类型/状态）。不返回接入配置与凭据。",
        "perm_code": "kb:read", "object_key": "knowledge_base", "read_only": True,
        "schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"}},
            "required": []},
        "handler": _list_knowledge_bases,
    },
    "quality_rule.run_check": {
        "tool_name": "run_quality_check",
        "label": "执行质量检核",
        "description": "按规则 ID 触发一次数据质量检核，返回通过率统计。"
                       "不返回失败明细采样与物理表名。请先用 list_quality_rules 取得规则ID。",
        "perm_code": "quality:manage", "object_key": "quality_rule", "read_only": False,
        "schema": {"type": "object", "properties": {
            "rule_id": {"type": "integer", "description": "质量检核规则 ID"}},
            "required": ["rule_id"]},
        "handler": _run_quality_check,
    },
    "scheduled_task.create": {
        "tool_name": "create_scheduled_task",
        "label": "创建调度任务",
        "description": "创建一个定时调度任务（query=SQL 列表 / agent=Agent 问题列表）。"
                       "Webhook 密钥由服务端生成，不会返回。",
        "perm_code": "scheduled:manage", "object_key": "scheduled_task", "read_only": False,
        "schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "任务名称"},
            "cron_expression": {"type": "string", "description": "Cron 表达式，如 0 8 * * *"},
            "task_type": {"type": "string", "enum": ["query", "agent"], "description": "执行模式"},
            "description": {"type": "string", "description": "任务说明"},
            "timezone": {"type": "string", "description": "时区，默认 Asia/Shanghai"},
            "task_config": {"type": "object", "description": "任务配置（SQL 列表或 Agent 问题列表）"}},
            "required": ["name", "cron_expression"]},
        "handler": _create_scheduled_task,
    },
    "report.generate": {
        "tool_name": "generate_report",
        "label": "生成分析报告",
        "description": "提交一次分析报告生成任务（异步产出，可用 list_reports 查看状态）。"
                       "分析来源三选一：dataset_id（数据集）/ intent（声明式分析意图，不接受裸 SQL）"
                       " / saved_query_id（已保存查询）。不返回报告正文。",
        "perm_code": "report:manage", "object_key": "report", "read_only": False,
        "schema": {"type": "object", "properties": {
            "title": {"type": "string", "description": "报告标题"},
            "dataset_id": {"type": "integer", "description": "分析来源：数据集 ID"},
            "intent": {"type": "object", "description": "分析来源：声明式分析意图（不含裸 SQL）"},
            "saved_query_id": {"type": "integer", "description": "分析来源：已保存查询 ID"}},
            "required": []},
        "handler": _generate_report,
    },
}


def action_tool_names() -> list[str]:
    """全部功能动作对应的工具名（供 tool_catalog / 测试核对）。"""
    return [s["tool_name"] for s in FUNCTION_ACTION_SPECS.values()]


def tool_name_for(action_key: str) -> str:
    spec = FUNCTION_ACTION_SPECS.get(action_key)
    return spec["tool_name"] if spec else ""


def qualified_tool_name(action_key: str) -> str:
    """``mcp__datahub_functions__<tool>`` —— 注册与执行授权共用的精确名。"""
    name = tool_name_for(action_key)
    return f"mcp__{SERVER_NAME}__{name}" if name else ""


def build_function_server(backend: str = "qoder", tool_names=None):
    """构建功能能力进程内 MCP server（qoder / claude）。

    ``tool_names`` 给定时只注册被选中的工具；空列表 = 注册零个工具（不是全部）。
    """
    from services.datamind.execution.sdk_tools.compat import make_server, make_tool

    specs = list(FUNCTION_ACTION_SPECS.values())
    if tool_names is not None:
        selected = set(tool_names)
        unknown = selected - {s["tool_name"] for s in specs}
        if unknown:
            raise ValueError(f"存在未知功能工具: {sorted(unknown)}")
        specs = [s for s in specs if s["tool_name"] in selected]

    tools = [
        make_tool(backend, s["tool_name"], s["description"], s["schema"], s["handler"],
                  annotations=READONLY_ANNOTATIONS if s["read_only"] else None,
                  qualified_name=f"mcp__{SERVER_NAME}__{s['tool_name']}")
        for s in specs
    ]
    return make_server(backend, SERVER_NAME, tools)
