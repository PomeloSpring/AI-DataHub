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
from dataclasses import replace

logger = logging.getLogger(__name__)

READONLY_ANNOTATIONS = {"readOnlyHint": True}

SERVER_NAME = "datahub_functions"

# ── 惰性接入：只向模型暴露两个壳工具 ────────────────────────────────────────
# 功能动作不再逐个注册（17 份 schema + 描述常驻上下文 = 每一轮白付 token，
# 且大部分会话根本不用平台功能）。改为：LLM 判断需要操作平台功能时先调
# ``list_platform_functions`` 拿**当前角色**可用清单，再用 ``run_platform_function`` 派发。
#
# 授权口径一个没变：动作精确名仍在 ``policy.allowed`` 里，run 内部逐动作走
# ``check_tool`` + ``validate_tool_resources``（与逐工具注册时同一条裁决线）；
# 清单直接取 ``policy.function_names`` / ``policy.unavailable``，不新建第二套判断。
DISCOVER_TOOL = "list_platform_functions"
INVOKE_TOOL = "run_platform_function"


# ── 共用小工具 ────────────────────────────────────────────────────────────────

def _text(data, is_error: bool = False) -> dict:
    """统一 CallToolResult 构造（content 块 + MCP 标准 isError 键）。"""
    body = data if isinstance(data, str) else json.dumps(
        data, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": body}],
            **({"isError": True} if is_error else {})}


def _identity():
    """服务端解析调用者身份，失败返回 None（fail-closed）。"""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.common.auth import resolve_execution_owner
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
    from backend.common.db import execute_query
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
    """报表清单：只回自己创建的报表的标题/格式/状态/浏览次数。不含正文、分享令牌、证据摘要。

    报表是属主私有资源（列表/查看都按 owner_id fail-closed），AI 通道不得跨用户
    把别人的报表标题递出去。
    """
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    rows = _select(
        "SELECT id, title, format, generation_status, source_type, view_count, created_at "
        "FROM adh_reports WHERE owner_id = %s ORDER BY created_at DESC LIMIT %s",
        (int(ident["user_id"]), _limit(args)))
    return _text({"reports": [_iso(r, "created_at") for r in rows],
                  "note": "仅含本人创建报表的业务摘要；报表正文与分享令牌不在 AI 可见范围内"})


def _list_dashboards(args):
    """看板清单：可见范围复用 visible_dashboard_ids（角色授权唯一裁决）。

    ``visible_dashboard_ids`` 对 admin 返回 **None**（= 全可见），与空集（fail-closed）
    语义不同。历史缺陷（2026-11-17）：``if not ids`` 把 admin 的 None 当成「无可见看板」，
    admin 在「数据看板」页看得到全部看板，AI 通道却回「当前角色无可见看板」。
    """
    from backend.modules.viz.services.dashboard_service import visible_dashboard_ids
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    ws = int(ident.get("workspace_id") or 0)
    ids = visible_dashboard_ids(int(ident["user_id"]), ident.get("role") or "", ws)
    limit = _limit(args)
    cols = "id, name, description, status, is_public, is_default, sort_order, updated_at"
    # 排序与服务端目录同口径(is_default→sort_order→id), 末尾 id 兜底保证 limit 截断确定
    order = "ORDER BY is_default DESC, sort_order, id LIMIT %s"
    if ids is None:                      # admin：全可见（与「数据看板」页同口径）
        rows = _select(f"SELECT {cols} FROM adh_dashboards {order}", (limit,))
        note = "管理员可见全部看板；布局与筛选参数不在 AI 可见范围内"
    elif not ids:                        # fail-closed：角色未获授任何看板
        return _text({"dashboards": [],
                      "note": "当前角色未获授任何看板（角色授权为空即一律不可见）"})
    else:
        ph = ",".join(["%s"] * len(ids))
        rows = _select(
            f"SELECT {cols} FROM adh_dashboards WHERE id IN ({ph}) {order}",
            (*ids, limit))
        note = "可见范围以角色授权为准；布局与筛选参数不在 AI 可见范围内"
    if len(rows) >= limit:               # 截断必须显式（不得静默少给）
        note += f"；结果已按上限 {limit} 条截断，可调大 limit（最大 200）"
    return _text({"dashboards": [_iso(r, "updated_at") for r in rows], "note": note})


def _list_scheduled_tasks(args):
    """调度任务清单：只回自己创建的任务名称/周期/状态。不含 Webhook 密钥、任务配置、上次错误原文。

    任务调度跟随创建者（用户），不按角色/工作空间共享：同角色的两个人在同一工作空间里
    也不得经 AI 通道拿到对方的任务（与 REST 列表、报表清单同一口径）。
    """
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    rows = _select(
        "SELECT id, name, description, task_type, cron_expression, trigger_type, timezone, "
        "       is_active, last_run_at, last_status, run_count, created_at "
        "FROM adh_scheduled_tasks WHERE owner_id = %s ORDER BY created_at DESC LIMIT %s",
        (int(ident["user_id"]), _limit(args)))
    return _text({"tasks": [_iso(r, "last_run_at", "created_at") for r in rows],
                  "note": "仅含本人创建的调度任务；Webhook 密钥与上次错误原文不在 AI 可见范围内"})


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
    """数据集清单：只回名称/来源类型/预设图表/可用数据源业务名。不含 SQL 原 文与字段配置。

    数据源一律以**业务名**下发（护栏 §7：内部 datasource_id 是黑盒）——多源数据集
    要让 LLM 知道可以用 site/datasource 指定这次跑哪个源，否则它只能看见一个名字。

    可见范围与「数据集」页同口径（`dataset_service.dataset_visible_to`）：admin 全可见；
    其余 = 属主（含 private 个人产物）∪ workspace 行命中角色授权，**空授权不等于全量**。
    历史缺陷（2026-11-17）：这里不过角色过滤，AI 能列出用户在页面上看不到的数据集；
    反向缺陷（2026-11-20 修正）：直接短路空授权，会让用户看不到自己 chat 建的数据集。
    """
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    limit = _limit(args)
    cols = ("d.id, d.name, d.description, d.source_type, d.object_key, d.chart_type, "
            "d.status, d.available_datasources, d.visibility, d.owner_id, "
            "COALESCE(u.username, '') AS owner_name, ds.name AS datasource_name, "
            "d.created_at, d.updated_at")
    base = ("FROM adh_datasets d "
            "LEFT JOIN adh_datasources ds ON ds.id = d.datasource_id "
            "LEFT JOIN adh_users u ON u.id = d.owner_id "
            "WHERE d.status = 'active'")
    # updated_at 同秒的行很多, 末尾 d.id 兜底保证 LIMIT 截断确定（list-pagination §2）
    order = "ORDER BY d.updated_at DESC, d.id DESC LIMIT %s"
    if (ident.get("role") or "") == "admin":
        rows = _select(f"SELECT {cols} {base} {order}", (limit,))
    else:
        from backend.core.role_service import role_service

        allowed_ids = role_service.get_user_allowed_datasets(int(ident["user_id"]))
        ph = ",".join(["%s"] * len(allowed_ids)) if allowed_ids else ""
        scope = ("d.owner_id = %s OR (d.visibility = 'workspace'"
                 + (f" AND d.id IN ({ph})" if ph else " AND 1=0") + ")")
        rows = _select(f"SELECT {cols} {base} AND ({scope}) {order}",
                       (int(ident["user_id"]), *sorted(allowed_ids), limit))
    for r in rows:
        r["datasource"] = str(r.pop("datasource_name", "") or "") or "默认引擎"
        r["visibility"] = str(r.pop("visibility", "") or "workspace")
        r["is_owner"] = int(r.pop("owner_id", 0) or 0) == int(ident["user_id"])
        raw = r.pop("available_datasources", None)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = []
        scope = [str(x).strip() for x in (raw or []) if str(x or "").strip()]
        names = list(dict.fromkeys([r["datasource"]] + scope if r["datasource"] else scope))
        if len(names) > 1:                     # 单源数据集不加噪声
            r["available_datasources"] = names
    note = ("清单 = 你创建的数据集 + 已按角色共享给你的数据集（他人私有集不可见不可用）；"
            "SQL 原文与字段配置不在 AI 可见范围内；数据源只给业务名，"
            "跨源数据集按 site/datasource 指定其一（不得传 id）")
    if len(rows) >= limit:                   # 截断必须显式（不得静默少给）
        note += f"；结果已按上限 {limit} 条截断，可调大 limit（最大 200）"
    return _text({"datasets": [_iso(r, "created_at", "updated_at") for r in rows],
                  "note": note})


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
    from backend.modules.gov.services.quality_engine import execute_single_rule
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
    from backend.modules.viz.services import report_service
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)

    sources = {k: args.get(k) for k in ("dataset_id", "intent", "saved_query_id")
               if args.get(k) is not None}
    if len(sources) != 1:
        return _text({"error": "分析来源必须且只能指定一种：dataset_id / intent / saved_query_id"},
                     is_error=True)

    title = str(args.get("title") or "").strip()
    if not title:
        return _text({"error": "title 必填：报告标题不能为空"}, is_error=True)
    request = {"workspace_id": int(ident.get("workspace_id") or 0), "title": title}
    request.update(sources)
    # 报告模板按**业务名**收（AI 拿不到也不该猜字模 id，见 waker-datasource-domain §2）；
    # 模板读不到就带可用清单回错，不得静默按通用版式出一张"以为套了模板"的报告（§1）。
    tpl_name = str(args.get("template") or "").strip()
    if tpl_name:
        hit = report_service.resolve_report_template_by_name(tpl_name)
        if not hit.get("found"):
            avail = "、".join(hit.get("available") or []) or "（当前没有可用模板，可先到报告模板配置页新建）"
            return _text({"error": f"{hit.get('reason')}；可用模板：{avail}"}, is_error=True)
        request["template_key"] = hit["template_key"]
    # intent 来源必须带站点才能定生效数据源（业务对象绑定按站点登记，ds=0 解不出来）。
    # 会话站点由**服务端从执行上下文注入**（不让 LLM 传站点/源标识，守 waker-datasource-domain §2）；
    # 聚合态（全部站点）下 resolve_site_to_source 会回可操作错因，不静默选一个站充数。
    if sources.get("intent") is not None:
        from backend.modules.mind.execution.sdk_tools.context import get_execution_context

        _ctx = get_execution_context()
        _site = str((getattr(_ctx, "extra", None) or {}).get("site_code") or "").strip()
        if _site:
            request["site_code"] = _site

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


def _save_report(args):
    """把已展示给用户的分析结论存为报表（内容型，同步入库，报告中心可管理）。

    与 ``_generate_report`` 的分工：那条是“按分析来源异步取数产出”（数据派生，
    带权限快照）；本工具不取数、不新建取数通道，只把会话里已经给用户看过的结论
    落为属主私有报表（fde-evolution §2：同一持久化入口 report_service）。
    正文过 outbound_guard：命中物理标识/凭据/生成 SQL 即拒写（fail-loud，不静默截删）。
    """
    from backend.modules.mind.execution import outbound_guard
    from backend.modules.viz.services import report_service
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "")
    fmt = str(args.get("format") or "markdown").strip().lower()
    if not title:
        return _text({"error": "缺少报表标题 title"}, is_error=True)
    if not content.strip():
        return _text({"error": "缺少报表正文 content（写用户已在本会话看到的结论）"}, is_error=True)
    hits = outbound_guard.find_sensitive_hits(content)
    if hits:
        return _text({"error": "报表正文包含不应归档的基础设施信息（命中: "
                     + ", ".join(hits) + "）。请改用业务名称重新表述结论，"
                       "不要写 SQL、物理表/列名、账号或地址。"}, is_error=True)
    try:
        result = report_service.create_content_report(
            title=title, content=content, format=fmt,
            workspace_id=int(ident.get("workspace_id") or 0), owner_id=int(ident["user_id"]),
            source_type="conversation", origin={"channel": "as_bot"})
    except (PermissionError, ValueError) as exc:
        return _text({"error": str(exc)}, is_error=True)
    except Exception:  # noqa: BLE001
        logger.exception("[function_tools] 保存报表失败")
        return _text({"error": "保存报表失败，详见服务端日志"}, is_error=True)
    return _text({
        "report_id": result.get("report_id"),
        "title": result.get("title"),
        "generation_status": result.get("generation_status"),
        "note": "已存入报告中心（仅本人可见与编辑）；告知用户可在「报告中心」实时查看",
    })


def _create_scheduled_task(args):
    """创建定时 Agent 分析任务（委托 ScheduledTaskService.create_task）。

    Webhook Token / Secret 由服务端生成，**一律不回显**；返回值只留任务标识与状态。
    任务不持有 AS-BOT 与数据源：能力与可查范围每次执行按属主角色重解（服务层会剥除
    模型传入的内部键）。
    """
    from backend.modules.flow.services.scheduled_task_service import ScheduledTaskService
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)

    name = str(args.get("name") or "").strip()
    cron = str(args.get("cron_expression") or "").strip()
    if not name:
        return _text({"error": "缺少任务名称"}, is_error=True)
    if not cron:
        return _text({"error": "缺少执行周期表达式 cron_expression"}, is_error=True)

    ws = int(ident.get("workspace_id") or 0)
    data = {
        "name": name,
        "description": str(args.get("description") or ""),
        "task_type": "agent",
        "cron_expression": cron,
        "timezone": str(args.get("timezone") or "Asia/Shanghai"),
        "is_active": 1,
    }
    # 任务配置只收问题清单与背景说明；SQL 列表/身份字段一律拒收（不开第二套取数调度）。
    config = args.get("task_config")
    if not isinstance(config, dict):
        return _text({"error": "task_config 必须是对象，至少包含 questions: [{title, question}]"}, is_error=True)
    data["task_config"] = {k: v for k, v in config.items()
                           if k in ("questions", "context", "site_code")}
    if not (data["task_config"].get("questions") or []):
        return _text({"error": "任务至少要有一个分析问题（questions）"}, is_error=True)

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
        "task_type": data["task_type"],
        "note": "Webhook Token/Secret 由服务端生成并保存，不在 AI 可见范围内",
    })


# ═════════════════════════════════════════════════════════════════════════════
# 内容中心（资讯栏目）— 生成内容与管理内容
# ═════════════════════════════════════════════════════════════════════════════
#
# 授权口径（perm_link 的 A 维度，不另造第二套判断）：
#   * 六个动作全部挂 ``article:manage``（AI 级别 write）：能让 AI 代编的，就是能自己编的人；
#   * **发布 / 下架 / 审核不给 AI**：``article:publish`` 与 ``article:review`` 的
#     ``ai_access=none``（对外内容代表公司立场，必须有人负责）。被拒时如实说缺哪个码，
#     并指回「内容中心 → 内容管理」（审核与发布都在行上，旧「审核发布」页已并入），不得换工具绕过；
#   * **同步知识库语料不给 AI**：``kbdoc:write`` 的 ``ai_access=none``（改语料等于改 AI
#     自己的知识），需要时由人在内容管理页点「同步到知识库」。
# 标识口径（护栏 §7 + ui-resource-display §2）：入参与返回一律用**业务标识**
#   （slug / 栏目 key / 标签名）。条目 id、revision id、category_id 既不下发也不收 ——
#   让模型传它看不到的内部 id，只会换来猜值与被拒。

CONTENT_HIDDEN_KEYS = ("id", "category_id", "head_revision_id", "live_revision_id",
                       "source_ref", "cover_object_key")

# 这些字段不接受 AI 传值：发布态/编排态/身份字段都由页面动作与人决定（fail-loud 拒收，
# 不是"传了但忽略"——那会让模型以为已经发布成功了）。
# 版本策略与期次同属人的编排动作：给哪一期命名、“历史期次给不给读者看”都由人在内容中心
# 定（模型顺手传 latest_only 等于替读者做了“看不见历史”的决定，而且它自己发不了布）。
CONTENT_REJECTED_KEYS = frozenset({
    "id", "article_id", "category_id", "kb_id", "datasource_id", "workspace_id",
    "user_id", "created_by", "updated_by", "status", "state", "live",
    "published", "publish", "approved", "approve", "archived", "archive",
    "reviewed", "waive_review", "revision_id", "source_type",
    "versioning", "release", "release_label",
})


def _err(exc) -> str:
    """业务异常转可读文案（ContentError.detail 是给前端的那一份，原样递给模型）。"""
    return str(getattr(exc, "detail", "") or exc)


def _reject_forbidden_keys(args):
    hits = sorted({str(k).lower() for k in (args or {})} & CONTENT_REJECTED_KEYS)
    if hits:
        raise ValueError(
            "这些字段不接受 AI 传值：" + ", ".join(hits) +
            "。发布/下架/审核必须由人在「内容中心」操作（对应权限码未开放给 AI）；"
            "定位条目用 slug，栏目用 list_content_taxonomy 返回的 key。")


def _require_content_perm(label: str) -> None:
    """写动作直执行前的权限码把关（fail-closed，与策略注册同一口径的纵深）。"""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.core.perm_link import require_write_perm

    ctx = get_execution_context()
    require_write_perm(getattr(ctx, "user_id", 0) or 0,
                       getattr(ctx, "workspace_id", 0) or 0, "article:manage", label)


def _content_cat_keys() -> dict:
    """category_id → 栏目业务 key（AI 侧只认 key，不认 id）。"""
    from backend.modules.platform.services import content_service as cs
    return {int(c["id"]): str(c.get("key") or "") for c in cs.list_categories(include_inactive=True)}


def _content_out(a: dict, cat_keys: dict) -> dict:
    """内容条目的 AI 投影：剥内部 id，栏目补回业务 key。"""
    out = {k: v for k, v in (a or {}).items() if k not in CONTENT_HIDDEN_KEYS}
    key = cat_keys.get(int((a or {}).get("category_id") or 0), "")
    out["category"] = key
    if not key:
        out["category_hint"] = "该条目没有栏目（或栏目已不在名册里）：提交审核前必须选一个在用栏目"
    return out


def _content_payload(args, *, partial: bool = False) -> dict:
    """白名单收字段：只搬运内容本身的业务字段，其余一律不收。"""
    allowed = ("title", "slug", "summary", "body", "category", "tags", "kind",
               "link_url", "link_label", "refs", "pinned", "featured", "review_by",
               "change_note")
    payload = {k: args[k] for k in allowed if args.get(k) is not None}
    if not partial:
        if not str(payload.get("title") or "").strip():
            raise ValueError("缺少标题 title")
        if not str(payload.get("body") or "").strip():
            raise ValueError("缺少正文 body（站内正文形态必须有内容；外链条目请传 kind=link 与 link_url）")
    return payload


def _resolve_content_id(cs, slug: str) -> int:
    """slug → 条目 id（服务端解析，内部 id 不进 LLM 视野）。"""
    text = str(slug or "").strip()
    if not text:
        raise ValueError("缺少 slug（条目业务标识，可从 list_content_articles 取得）")
    return int(cs.get_article(slug=text, can_edit=True, revision="head")["id"])


def _list_content_articles(args):
    """内容清单（安全视图）：业务字段 + 同步状态，不含内部 id。"""
    from backend.modules.platform.services import content_service as cs
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    try:
        size = max(1, min(int(args.get("limit") or 20), 50))
    except (TypeError, ValueError):
        return _text({"error": "limit 必须是整数"}, is_error=True)
    try:
        data = cs.list_articles(category=str(args.get("category") or ""),
                                kind=str(args.get("kind") or ""),
                                tag=str(args.get("tag") or ""),
                                q=str(args.get("q") or ""),
                                status=str(args.get("status") or "all"),
                                page=1, page_size=size)
    except cs.ContentError as e:
        return _text({"error": _err(e)}, is_error=True)
    keys = _content_cat_keys()
    return _text({"articles": [_content_out(a, keys) for a in data["items"]],
                  "total": data["total"],
                  "note": "定位条目请用 slug；正文要看得用 get_content_article。"
                          "发布/下架/审核不在 AI 可调用范围内，需由人在内容中心操作。"})


def _get_content_article(args):
    """单篇详情（含工作稿正文）+ 门禁预检结果，让模型知道还差什么才能提交。"""
    from backend.modules.platform.services import content_service as cs
    if not _identity():
        # revision=head 走的是 can_edit 通道（拿得到未发布工作稿），身份不明就不给
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    try:
        art = cs.get_article(slug=str(args.get("slug") or ""), can_edit=True,
                             revision=str(args.get("revision") or "head"))
    except cs.ContentError as e:
        return _text({"error": _err(e)}, is_error=True)
    keys = _content_cat_keys()
    out = _content_out(art, keys)
    body = str(art.get("body") or "")
    hard, warns = cs.gate_article(art, body)
    out["gate_hard"] = hard
    out["gate_warnings"] = warns
    out["note"] = ("body 是工作稿（head），已发布版本可能与它不同（看 is_live / has_pending_change）。"
                   "revision=head 取工作稿、live 取线上版；gate_hard 非空时提交审核会被拒。")
    return _text(out)


def _list_content_taxonomy(args):
    """栏目与标签名册：写作前先取合法取值，避免模型编造栏目名/标签。"""
    from backend.modules.platform.services import content_service as cs
    include = bool(args.get("include_inactive"))
    try:
        cats = [{"key": c.get("key"), "label": c.get("label"),
                 "description": c.get("description") or "", "is_active": c.get("is_active"),
                 "article_count": c.get("count")}
                for c in cs.list_categories(include_inactive=include)]
        tags = [{"name": t.get("tag"), "is_active": t.get("is_active"),
                 "published_count": t.get("count")} for t in cs.list_tags(include_inactive=include)]
    except cs.ContentError as e:
        return _text({"error": _err(e)}, is_error=True)
    return _text({"categories": cats, "tags": tags,
                  "note": "写内容时 category 只能取这里的 key（不要编造栏目名），tags 取 name。"
                          "栏目/标签的增删改属编排配置，请由人在「内容中心 → 栏目与标签」做。"})


def _create_content_draft(args):
    """新建内容草稿（只落 draft，永不发布）。"""
    from backend.modules.platform.services import content_service as cs
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    try:
        _reject_forbidden_keys(args)
        _require_content_perm("创建内容草稿")
        leaks = cs.body_leak_labels(str(args.get("body") or ""))
        if leaks:
            raise ValueError("正文含内部标识：" + "；".join(leaks) +
                             "。对外内容不得出现数据源/主机/连接串/生成 SQL/报错栈，"
                             "请改成业务名称后重写")
        payload = _content_payload(args)
        art = cs.create_article(payload, str(ident["username"]))
    except (PermissionError, ValueError, cs.ContentError) as e:
        return _text({"error": _err(e)}, is_error=True)
    keys = _content_cat_keys()
    hard, warns = cs.gate_article(art, str(art.get("body") or ""))
    return _text({"article": _content_out(art, keys), "gate_hard": hard, "gate_warnings": warns,
                  "note": "已存为草稿（draft），门户看不到。要上线请：修完 gate_hard → "
                          "submit_content_review 提交审核 → 由审核人/发布人在内容中心发布"
                          "（发布不开放 AI）。"})


def _update_content_draft(args):
    """改草稿或改已发布内容（改动只落工作稿，线上版不动）。"""
    from backend.modules.platform.services import content_service as cs
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    try:
        _reject_forbidden_keys(args)
        _require_content_perm("编辑内容草稿")
        leaks = cs.body_leak_labels(str(args.get("body") or ""))
        if leaks:
            raise ValueError("正文含内部标识：" + "；".join(leaks) + "，请改成业务名称后重写")
        if args.get("expected_version") is None:
            raise ValueError("缺少 expected_version（乐观锁）：请先 get_content_article 取回当前 version 再改，"
                             "避免覆盖别人的编辑")
        aid = _resolve_content_id(cs, str(args.get("slug") or ""))
        art = cs.update_article(aid, _content_payload(args, partial=True), str(ident["username"]),
                                expected_version=int(args["expected_version"]))
    except (PermissionError, ValueError, cs.ContentError) as e:
        return _text({"error": _err(e)}, is_error=True)
    keys = _content_cat_keys()
    hard, warns = cs.gate_article(art, str(art.get("body") or ""))
    return _text({"article": _content_out(art, keys), "gate_hard": hard, "gate_warnings": warns,
                  "note": "改动只落工作稿：已发布内容改完后线上仍是旧版，需重新提交审核并发布才生效。"})


def _submit_content_review(args):
    """提交审核（不发布）：门禁硬阻断会把逐条原因原样回传。"""
    from backend.modules.platform.services import content_service as cs
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    try:
        _reject_forbidden_keys(args)
        _require_content_perm("提交内容审核")
        aid = _resolve_content_id(cs, str(args.get("slug") or ""))
        out = cs.submit_review(aid, str(ident["username"]), channel="ai")
    except (PermissionError, ValueError, cs.ContentError) as e:
        return _text({"error": _err(e)}, is_error=True)
    return _text({**out,
                  "note": "已进入待审队列，但尚未发布：审核与发布是人的动作（article:review / "
                          "article:publish 未开放 AI 调用），请在「内容中心 → 内容管理」处理。"})


#: 提议类工具禁收的内部标识键（LLM 拿不到这些 id，只能猜；security-guardrails §7）
_PROPOSAL_REJECTED_KEYS = {"id", "kb_id", "source_id", "doc_id", "model_id",
                           "object_id", "datasource_id", "workspace_id", "user_id",
                           "scenario_id", "applied_ref"}


def _create_knowledge_proposal(args):
    """提交一条知识更新**提议**（只入进化中心待审队列，不直写任何资产）。

    为什么不让 AI 直接改：知识库语料写入是 `kbdoc:write` 且 `ai_access=none`
    （改语料等于改 AI 自己的知识，必须人负责）；本体改动要走两段式发布。
    所以这里的可写边界就是“把话记下来 + 说清为什么”，落库由人在进化中心确认。
    """
    from backend.modules.catalog.services import knowledge_update_service as kus
    from backend.core.perm_link import require_write_perm
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    ctx = get_execution_context()
    rejected = sorted({str(k).lower() for k in (args or {})} & _PROPOSAL_REJECTED_KEYS)
    if rejected:
        return _text({"error": "这些字段不接受传入：" + ", ".join(rejected) + "。"
                               "目标只用业务名（target_hint：知识库名/文档标题/场景标题/对象业务名），"
                               "具体落到哪一行由人在进化中心用选择框确认"}, is_error=True)
    try:
        require_write_perm(int(ctx.user_id or 0), int(ctx.workspace_id or 0),
                           "knowledge:manage", "提交知识更新提议")
        out = kus.create_proposal(
            source="chat",
            target_type=str(args.get("target_type") or ""),
            title=str(args.get("title") or ""),
            content=str(args.get("content") or ""),
            rationale=str(args.get("rationale") or ""),
            target_hint=str(args.get("target_hint") or ""),
            datasource_name=str(args.get("datasource") or ""),
            origin_conversation_id=int((ctx.extra or {}).get("conversation_id") or 0),
            origin_message_uuid=str((ctx.extra or {}).get("message_uuid") or ""))
    except (PermissionError, ValueError) as e:
        return _text({"error": _err(e)}, is_error=True)
    return _text({"submitted": True, "status": out.get("status"),
                  "target_type": args.get("target_type"),
                  "title": str(args.get("title") or ""),
                  "queue": "进化中心 → 知识更新（待人工确认）",
                  "note": "已入待审队列，当前没有修改任何本体/知识库/场景。"
                          "请在「进化中心 → 知识更新」逐条确认后再落库；"
                          "不要向用户声称已经改好了。"})


# ═════════════════════════════════════════════════════════════════════════════
# 惰性接入的两个壳工具 —— 按当前角色下发功能清单 / 派发到具体动作
# ═════════════════════════════════════════════════════════════════════════════

def _live_function_policy():
    """取本次调用的实时生效策略（与 ``check_tool`` 同一入口，不另造裁决）。

    读不到可信会话返回 ``(None, None)``；策略解析失败（AS-BOT 未授权/模型未授权等）
    原样抛 PermissionError，由 compat 收口透传真因，不静默当“没功能”。
    """
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.modules.mind.execution.tool_policy import resolve_policy, live_ceiling
    ctx = get_execution_context()
    runtime = (ctx.extra or {}).get("secure_runtime") if ctx is not None else None
    if runtime is None:
        return None, None
    policy = resolve_policy(ctx, live_ceiling(ctx, runtime), use_cache=True, live=True)
    return ctx, policy


def _list_platform_functions(args):
    """按当前用户角色返回可用平台功能清单（域分组/场景/规则/参数/读写 + 不可用原因）。

    清单数据源就是会话能力面板那一份（``policy.function_names`` / ``policy.unavailable``），
    所以“模型看到的功”与“管理员在面板上配出的功”天然同口径，不会两套真相。

    每项携带能力域标签（``domain``）与适用场景/规则提示（从系统本体反查投影）：
    解决“系统能力 vs 数据分析”分不清导致的工具误选——LLM 第一跳先按域缩小
    选择面（可选入参 ``domain``），再在域内挑功能。这只是选择引导，
    能不能做仍由 run 内的 check_tool/require_write_perm 按权限码裁决。
    """
    from backend.modules.mind.execution.capability_contracts import (
        action_context_index, action_domain,
    )

    try:
        _ctx, policy = _live_function_policy()
    except PermissionError as exc:
        return _text({"error": str(exc)}, is_error=True)
    if policy is None:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    domain_filter = str(args.get("domain") or "").strip().lower()
    allowed = set(policy.function_names or ())
    # 场景/规则反查索引：构建失败显式标注（不静默空清单装作“无约束”）
    context_error = ""
    try:
        ctx_index = action_context_index()
    except Exception as exc:  # noqa: BLE001 — 反查失败不得拖垮清单本身
        ctx_index = {}
        context_error = f"场景/规则提示暂不可用：{exc}"
    items = []
    for action_key, spec in FUNCTION_ACTION_SPECS.items():
        if spec["tool_name"] not in allowed:
            continue
        domain = action_domain(action_key)
        if domain_filter and domain != domain_filter:
            continue
        schema = spec.get("schema") or {}
        props = schema.get("properties") or {}
        item_ctx = ctx_index.get(action_key) or {}
        items.append({
            "function": spec["tool_name"],
            "domain": domain,
            "label": spec.get("label") or spec["tool_name"],
            "description": spec.get("description") or "",
            "read_only": bool(spec.get("read_only", True)),
            "params": {k: str(v.get("description") or "") for k, v in props.items()},
            "required": list(schema.get("required") or []),
            "scenarios": list(item_ctx.get("scenarios") or []),
            "rules_hint": list(item_ctx.get("rules") or []),
        })
    items.sort(key=lambda x: (x["domain"], x["function"]))
    prefix = f"mcp__{SERVER_NAME}__"
    unavailable = sorted(
        ({"function": str(k).rsplit("__", 1)[-1], "reason": str(v)}
         for k, v in (policy.unavailable or {}).items() if str(k).startswith(prefix)),
        key=lambda r: r["function"])
    out = {
        "functions": items,
        "unavailable": unavailable,
        "how_to_call": (f'用 {INVOKE_TOOL} 调用：{{"function": "<上面某个 function>", '
                        f'"arguments": {{...}}}}；无参数的功能 arguments 传 {{}}'),
        "note": "清单按你当前角色的权限码实时生成，按能力域分组：system=平台运营/编排，"
                "data=数据分析取数，modeling=本体/字典建模，content=内容/知识，"
                "governance=治理/进化/评测，operations=调度/流程。"
                "选工具先看 domain 与 scenarios，不要跨域误用（如拿平台功能答取数问题）；"
                "rules_hint 是该功能的专属约束，调用前必须遵守。"
                "unavailable 里的功能对本会话不可用，原因需原样告知用户；"
                "不得凭记忆猜功能名或参数，也不得换工具绕过。",
    }
    if context_error:
        out["context_error"] = context_error  # 显式降级标注（no-silent-degradation）
    return _text(out)


def _tool_action_map() -> dict[str, str]:
    """tool_name → action_key 预构建映射（壳参数是工具名，只在这里换算一次）。"""
    return {s["tool_name"]: k for k, s in FUNCTION_ACTION_SPECS.items()}


def run_action_by_key(action_key: str, args: dict, *, ctx=None):
    """按 action key 直接执行功能动作（P2 内部入口）。

    壳是**注册表直通**（``check_tool`` + ``validate_tool_resources`` + handler，
    与逐工具注册时同一条裁决线），不受 active 系统本体发布状态约束 ——
    保持 run 壳行为不变；流程/运行时的契约执行链（指纹、Schema、超时、
    输出校验、幂等）见 ``capability_runtime.execute_action``（P3 按步骤执行用）。
    """
    spec = FUNCTION_ACTION_SPECS.get(action_key)
    if spec is None:
        return _text({"error": f"未知动作 key「{action_key}」（注册表无此功能动作）"},
                     is_error=True)
    label = spec.get("label") or action_key
    qualified = qualified_tool_name(action_key)
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.modules.mind.execution.tool_policy import check_tool
    from backend.modules.mind.execution.resource_guard import validate_tool_resources, ResourceScopeError
    runtime_ctx = ctx if ctx is not None else get_execution_context()
    try:
        check_tool(runtime_ctx, qualified)
        call_args = validate_tool_resources(runtime_ctx, qualified, args or {})
    except (PermissionError, ResourceScopeError, ValueError) as exc:
        # 拒因透传（可诊断），只补上业务功能名；不换成“联系管理员”兑底文案
        return _text({"error": f"功能「{label}」本次不可用：{exc}"}, is_error=True)
    return spec["handler"](call_args)


def _run_platform_function(args):
    """把一个功能调用派发给对应动作 handler（逐动作实时裁决）。

    这里不是新裁决点：派发经 ``run_action_by_key``（按 action key 直接执行，
    不在运行时按 tool_name 反查），``check_tool`` 按动作精确名重裁（角色权限码 +
    涉密上界 + AS-BOT 减法 + 执行层上限），``validate_tool_resources`` 与逐工具
    注册时同一资源门。handler 自己返回业务级错误文案（不抛），
    权限/资源类异常补上功能名，其余异常仍交给 compat 收口。
    """
    name = str(args.get("function") or "").strip()
    inner = args.get("arguments") or {}
    if not name:
        return _text({"error": f"缺少 function（先用 {DISCOVER_TOOL} 取本角色可用清单）"},
                     is_error=True)
    if not isinstance(inner, dict):
        return _text({"error": f"arguments 必须是对象（无参数传 {{}}；"
                               f"参数清单见 {DISCOVER_TOOL} 返回的 params）"}, is_error=True)
    action_key = _tool_action_map().get(name, "")
    if not action_key:
        return _text({"error": f"未知功能「{name}」，请先用 {DISCOVER_TOOL} 获取清单"},
                     is_error=True)
    return run_action_by_key(action_key, inner)


SHELL_TOOL_SPECS: list[dict] = [
    {"name": DISCOVER_TOOL, "read_only": True,
     "description": "拉取【当前用户角色】可用的平台功能清单（报表/看板/调度任务/质量检核/"
                    "数据集/内容等平台操作），按能力域分组，含每个功能的适用场景、"
                    "规则提示、参数与不可用原因。需要操作平台功能时先调它，"
                    "不要凭记忆猜功能名；取到后用 " + INVOKE_TOOL + " 执行。"
                    "已知问题属于哪个域时传 domain 过滤（system/data/modeling/content/"
                    "governance/operations），只看该域能力面。",
     "schema": {"type": "object", "properties": {
         "domain": {"type": "string",
                    "description": "可选：只看某能力域的清单（system/data/modeling/"
                                   "content/governance/operations）"}},
         "required": []},
     "handler": _list_platform_functions},
    {"name": INVOKE_TOOL, "read_only": False,
     "description": f"执行一个平台功能。function 必须来自 {DISCOVER_TOOL} 的返回，"
                    "arguments 按该功能声明的参数填写。写动作先向用户说明要做什么并取得确认再调；"
                    "功能不可用时把原因原样告知用户。",
     "schema": {"type": "object", "properties": {
         "function": {"type": "string",
                      "description": f"功能名（{DISCOVER_TOOL} 返回的 function 字段）"},
         "arguments": {"type": "object",
                       "description": "该功能的参数；无参数传 {}"}},
         "required": ["function"]},
     "handler": _run_platform_function},
]


# ═════════════════════════════════════════════════════════════════════════
# 决策建议（只读通道）—— AI 能查建议，不能改判断口径
#
# 为什么写面不接 AI：决策树的一行阈值就是全平台的行动标准（相当于指标口径），
# 被 prompt injection 诱导改一个字就会批量影响所有用户得到的建议。
# 所以 decision:manage / decision:publish 在权限表里 ai_access=none，本层只两个只读动作。
# ═════════════════════════════════════════════════════════════════════════

def _decision_eval_context(args):
    """从会话执行上下文造引擎上下文（生效源只取服务端已定的会话源，不接模型传 id）。

    返回 ``(ctx, None)`` 或 ``(None, 可操作错因)``：没定源/没授权时后者必须说清去哪补，
    不能回一个空建议把"没权限"伪装成"没风险"（no-silent-degradation §1）。
    """
    from backend.modules.decision.engine import EvalContext
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    if ctx is None or int(ctx.user_id or 0) <= 0:
        return None, "无法确认调用者身份"
    ds = int(getattr(ctx, "datasource_id", 0) or 0)
    if not ds:
        # 全球/多站会话下没定单站：逐站评估得由模型带 site 参数分站调用，不能合起来判
        # （跨国口径下合判会得到一个哪站都不是的数字）。
        site = str((ctx.extra or {}).get("site_code") or "").strip()
        if _is_aggregate_site(site):
            return None, ("当前会话是「全部站点」聚合态，决策树按分站口径判定；"
                          "请逐站带 site 参数各调一次，不要把多站合起来判")
        return None, ("本次会话还没选定数据源，无法取数评估；"
                      "请先用 list_datasources 看可查范围，并询问用户要查哪个站点")
    return EvalContext(
        mode="full",
        user_id=int(ctx.user_id or 0),
        username=ctx.username or "",
        role=ctx.user_role or "",
        workspace_id=int(ctx.workspace_id or 0),
        datasource_id=ds,
        site_code=str((ctx.extra or {}).get("site_code") or "").strip(),
        trigger_type="manual",
    ), ""


def _is_aggregate_site(site_code: str) -> bool:
    if not site_code:
        return False
    from backend.core.site_service import get_site
    site = get_site(site_code) or {}
    return bool(int(site.get("is_aggregate") or 0))


def _resolve_decision_site(args, base):
    """模型给了 site 业务名/代码时在已授权集内改写到本站；失败回可操作错因。"""
    want = str(args.get("site") or "").strip()
    if not want:
        return base, ""
    from backend.core.site_service import normalize_site_ref, resolve_site_to_source
    from backend.core.role_service import role_service
    allowed = sorted({int(i) for i in (role_service.get_user_allowed_datasources(
        base.user_id, base.workspace_id) or []) if str(i).strip() != ""})
    if not allowed:
        return None, "当前角色没有已授权数据源，无法评估；请联系管理员配置角色数据源授权"
    try:
        ds = resolve_site_to_source(want, allowed)
    except Exception as exc:  # noqa: BLE001 — SiteError 文案已只含站点名与源业务名
        return None, str(exc)
    code = normalize_site_ref(want)
    ctx = replace(base, datasource_id=int(ds or 0), site_code=code)
    return ctx, ""


def _list_decision_trees(args):
    """决策树清单：只列已发布生效的，且只回业务摘要（不回阈值与判定结构）。"""
    from backend.modules.decision import service as decision_service
    ident = _identity()
    if not ident:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    rows = decision_service.active_trees(int(ident.get("workspace_id") or 0))
    limit = _limit(args)
    items = []
    for r in rows[:limit]:
        sites = [s.get("site_code") for s in (r.get("apply_sites") or []) if isinstance(s, dict)]
        items.append({"name": r.get("name"), "tree": r.get("tree_key"),
                      "description": r.get("description") or "",
                      "applies_to_sites": sites or ["按角色授权站点派生"],
                      "version": int(r.get("version") or 0)})
    return _text({
        "decision_trees": items, "total": len(rows),
        "note": "只含已发布的决策树（草案未生效不列出）。具体阈值与判定结构不在 AI 可见范围，"
                "需要看口径请到「决策中心」由有权限的人查看；要拿建议请用 run_decision_evaluation。",
    })


def _run_decision_evaluation(args):
    """跑一次评估：命中/未命中/未完整/未评估四种结果都带原因回传，不折叠成空清单。"""
    from backend.modules.decision import service as decision_service
    ctx, err = _decision_eval_context(args)
    if err:
        return _text({"error": err, "hint": "不要猜站点或数据源；可先用 list_datasources 看可查范围"},
                     is_error=True)
    ctx, err = _resolve_decision_site(args, ctx)
    if err:
        return _text({"error": err}, is_error=True)
    if ctx is None:
        return _text({"error": "无法确定生效站点"}, is_error=True)

    if args.get("all"):
        out = decision_service.evaluate_all(int(ctx.workspace_id or 0), ctx)
        return _text(_decision_brief(out["items"], extra_errors=out.get("errors")))

    ref = str(args.get("tree") or "").strip()
    if not ref:
        return _text({"error": "请传 tree（决策树名称或标识，可先用 list_decision_trees 取得），"
                               "或传 all=true 评估全部"}, is_error=True)
    row = decision_service.get_tree(ref)
    if not row:
        named = next((r for r in decision_service.active_trees(int(ctx.workspace_id or 0))
                      if (r.get("name") or "") == ref), None)
        if not named:
            return _text({"error": f"未找到决策树「{ref}」", "hint": "请用 list_decision_trees 取业务名后重试"},
                         is_error=True)
        row = named
    if row.get("status") != "active":
        # 草案不得被拿来回答用户：那等于把未上线的判断标准当结论递出去
        return _text({"error": f"决策树「{row.get('name')}」是「{row.get('status')}」状态，未生效不可评估",
                      "hint": "只有已发布的决策树会给出建议；草案请提示用户去决策中心评审发布"},
                     is_error=True)
    one = decision_service.evaluate(str(row.get("tree_key")), ctx)
    return _text(_decision_brief([one]))


def _decision_brief(items, extra_errors=None):
    """把评估结果压成 AI 可读、且不含内部标识的摘要（出站前再过一道收口）。"""
    brief = []
    for it in items:
        entry = {
            "tree": it.get("tree_name") or it.get("tree_key"),
            "verdict": it.get("verdict"),
            "hit_path": it.get("hit_path") or [],
            "advice": [{"action": a.get("action") or "", "severity": a.get("severity") or "",
                        "rationale": a.get("rationale") or "", "note": a.get("note") or ""}
                       for a in (it.get("advice") or [])],
            "evidence": [{"metric": e.get("metric"), "comparator": e.get("comparator"),
                          "threshold": e.get("threshold"), "measured": e.get("measured"),
                          "unit": e.get("unit") or "", "note": e.get("note") or ""}
                         for e in (it.get("evidence") or [])],
        }
        if it.get("reason"):
            entry["reason"] = it["reason"]
        if it.get("missing_metrics"):
            entry["missing_metrics"] = it["missing_metrics"]
        if it.get("unresolved"):
            entry["unresolved"] = [{"term": u.get("term"), "reason": u.get("reason"),
                                    "candidates": u.get("candidates") or []}
                                   for u in it["unresolved"]]
        brief.append(entry)
    skipped = [b for b in brief if b.get("verdict") in ("skipped", "partial")]
    out = {"evaluations": brief,
           "note": "建议来自已发布的决策树（确定性判定，不是模型生成）；verdict=skipped/partial "
                   "时必须把 reason 原样告知用户，不得说成“数据显示没有问题”。"}
    if extra_errors:
        out["errors"] = extra_errors
    if skipped:
        out["attention"] = f"有 {len(skipped)} 棵树未能完整评估，原因见各项 reason"
    return out


# ═════════════════════════════════════════════════════════════════════════
# 进化中心审批（Chat 内处置语义缺口与知识更新提议）—— 双闸
#
# 口径（用户拍板「双闸」）：
#   * 能力面：能看审批队列 = ``evolution:read``；能执行处置 = ``evolution:manage``
#     （两者的 ai_access 由 none 抬到 read/write，见
#     docker/mysql/evolution_ai_review_migration.sql）；
#   * 动作面：每个出口**仍按它本来所属域**的权限码裁决（与 HTTP 端点同一把尺、不扩权）：
#       收编别名 / 转术语 / 驳回候选 → ``ontology:save``
#       建指标/维度草稿            → ``metrics:manage``
#       知识更新提议 标记/驳回      → ``knowledge:manage``
#       立即执行归纳               → ``evolution:manage``
#   两道闸都过才执行，少一道即拒且原因可解释（fail-closed）。
#
# 为什么可以给 AI：候选与提议的处置是**对既有事实的确认**（这个词是不是那个口径的别名、
# 这条提议该不该收），落点仍是各域既有写入（本体草案/字典草稿/提议状态机），
# 且 AI 改本体只落草案、发布仍需人；真正的标尺（eval:*）依旧 ai_access='none'。
#
# 标识口径（护栏 §7 + ui-resource-display §2）：入参只收**业务名**
#   （候选的业务词 term / 对象的业务名或 key / 属性的业务名 / 提议标题 title /
#   数据源业务名 datasource）；候选与提议在这里按业务名解析成内部行，
#   内部 id 与物理列名（维度草稿的 target_column）既不下发也不让模型传。
# decided_by 由服务端身份注入，绝不从入参取（护栏 §2）。handler 只是薄适配：
# 业务名入参 → evolution_query_service / evolution_decision_service（P1-4 统一
# 决策链：状态机、版本仲裁、幂等账本、领域写入）→ 安全投影输出，不在这里做
# 查找、权限和状态转换。
# ═════════════════════════════════════════════════════════════════════════

#: 审批类工具禁收的内部标识键（LLM 拿不到这些 id，只能猜；security-guardrails §7）
_EVO_REJECTED_KEYS = {
    "id", "candidate_id", "proposal_id", "datasource_id", "bound_object_key",
    "object_key", "workspace_id", "user_id", "physical_table", "column_name",
    "target_column", "status", "decision",
}

_EVO_KIND_LABELS = {
    "new_metric": "待建指标", "new_dimension": "待建维度", "alias": "待收编别名",
    "binding_gap": "绑定缺口（待复核）", "term": "待转术语",
}


def _evo_context():
    """(ctx, actor)：身份与审核人只取服务端解析结果（护栏 §2）。"""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context

    ctx = get_execution_context()
    if ctx is None or int(getattr(ctx, "user_id", 0) or 0) <= 0:
        return None, ""
    ident = _identity()
    if not ident:
        return None, ""
    return ctx, str(ident.get("username") or ident.get("user_id") or "")


def _evo_query_context():
    """(exec_ctx, evo_ctx, actor)：执行上下文 → 可信 EvolutionContext（角色域唯一来源）。"""
    from backend.modules.catalog.services import evolution_query_service as queries

    ctx, actor = _evo_context()
    if ctx is None:
        return None, None, ""
    workspace_id = int(getattr(ctx, "workspace_id", 0) or 0)
    if workspace_id <= 0:
        from backend.common.auth import resolve_user_default_workspace_id
        workspace_id = int(resolve_user_default_workspace_id(
            int(getattr(ctx, "user_id", 0) or 0)) or 0)
    evo_ctx = queries.EvolutionContext.create(
        user_id=int(getattr(ctx, "user_id", 0) or 0), workspace_id=workspace_id,
        actor=actor, channel="ai")
    return ctx, evo_ctx, actor


def _evo_decision_error(e) -> dict:
    """决策/查询错误的安全投影：原因 + 错误码 + （半态时）领域落点。"""
    out = {"error": _err(e), "code": str(getattr(e, "code", "") or "")}
    ref = str(getattr(e, "domain_ref", "") or "")
    if ref:
        out["domain_ref"] = ref
    return out


def _evo_rejected(args) -> str:
    """拒收内部标识入参（fail-loud，不是「传了但忽略」）。"""
    hits = sorted({str(k).lower() for k in (args or {})} & _EVO_REJECTED_KEYS)
    if not hits:
        return ""
    return ("这些字段不接受 AI 传值：" + ", ".join(hits) +
            "。定位一律用业务名：候选用 term（业务词）、收编目标用 target（业务名）、"
            "归属对象用 object（业务名或对象 key）、属性用 property（业务名）、"
            "提议用 title（标题）、数据源用 datasource（业务名）；"
            "内部 id、物理列名与状态字段由服务端解析/裁决。")


def _evo_audit(ctx, action: str, target_type: str, detail: str,
               target_id: int = 0, actor: str = "") -> None:
    """审批留痕（best-effort：审计失败不影响主链路，但会落服务端日志）。"""
    try:
        from backend.common.auth import log_audit

        log_audit(int(getattr(ctx, "user_id", 0) or 0), actor, action,
                  target_type=target_type, target_id=int(target_id or 0),
                  detail=detail, module="catalog")
    except Exception as exc:  # noqa: BLE001 — 审计是旁路，不阻断处置
        logger.warning("[evolution] 审计写入失败（不影响主链路）: %s", exc)


def _list_evolution_review_queue(args):
    """列出进化中心待办（待处置候选 + 待审知识更新提议），只读。

    与人用列表共用 evolution_query_service：同一角色域、同一真分页与安全 DTO，
    只回业务投影（内部 id/版本/物理字段已在服务层剥离）。
    """
    from backend.modules.catalog.services import evolution_query_service as queries
    from backend.modules.catalog.services import evolution_service as evo

    bad = _evo_rejected(args)
    if bad:
        return _text({"error": bad}, is_error=True)
    kind = str(args.get("kind") or "").strip()
    datasource = str(args.get("datasource") or "").strip()
    try:
        limit = _limit(args, default=30, cap=queries.MAX_PAGE_SIZE)
        if kind and kind not in evo.KINDS:
            raise ValueError(f"kind 不合法（{kind!r}），应为 {list(evo.KINDS)}")
        _, evo_ctx, _ = _evo_query_context()
        if evo_ctx is None:
            return _text({"error": "无法确认调用者身份"}, is_error=True)
        page = queries.list_candidates(evo_ctx, status="pending", kind=kind,
                                       datasource_name=datasource,
                                       page=1, page_size=limit)
        prop_page = queries.list_proposals(
            evo_ctx, status="pending", page=1,
            page_size=min(limit, queries.MAX_PAGE_SIZE))
        overview = queries.overview(evo_ctx)
    except ValueError as exc:
        return _text({"error": _err(exc)}, is_error=True)
    candidates = []
    for c in page.get("items") or []:
        view = queries.candidate_for_ai(c)
        view["kind_label"] = _EVO_KIND_LABELS.get(str(view.get("kind") or ""), "")
        candidates.append(view)
    proposals = [queries.proposal_for_ai(p) for p in prop_page.get("items") or []]
    return _text({
        "candidates": candidates,
        "candidate_counts": overview.get("pending_by_kind") or {},
        "proposals": proposals,
        "proposal_counts": overview.get("proposal_counts") or {},
        "how_to_act": (
            "候选：收编别名 adopt_evolution_alias(term,target_type,target)；"
            "转术语 move_evolution_to_term(term,description)；"
            "驳回 reject_evolution_candidate(term)；"
            "建指标草稿 create_metric_from_evolution(term,object,formula)；"
            "建维度草稿 create_dimension_from_evolution(term,object,property)。"
            "提议：先在各域真正落库，再 mark_evolution_proposal_done(title,applied_ref)；"
            "驳回 reject_evolution_proposal(title,note)。"),
        "note": ("这里只有待处置项；term/title 必须与清单里的业务名一字不差，"
                 "不得批量臆测、不得替用户挑目标。收编目标与对象/属性必须来自清单，"
                 "查不到就把可选清单原样给用户，不猜。驳回类动作先征得用户确认。"),
    })


def _adopt_evolution_alias(args):
    """把候选的业务词收编为既有字典行/本体对象的别名（双闸在决策服务内）。"""
    from backend.modules.catalog.services import evolution_decision_service as decide

    bad = _evo_rejected(args)
    if bad:
        return _text({"error": bad}, is_error=True)
    try:
        _, evo_ctx, _ = _evo_query_context()
        if evo_ctx is None:
            return _text({"error": "无法确认调用者身份"}, is_error=True)
        result = decide.decide_candidate_by_name(
            evo_ctx, term=str(args.get("term") or ""),
            datasource_name=str(args.get("datasource") or ""),
            action_key="evolution_candidate.adopt",
            input_data={"target_type": str(args.get("target_type") or ""),
                        "target": str(args.get("target") or "")})
    except (PermissionError, ValueError) as e:
        return _text(_evo_decision_error(e), is_error=True)
    if not result.get("changed"):
        # 目标缺行不是失败，是「该走另一个出口」：如实回指引，不假装已收编
        return _text({"adopted": False, "term": str(args.get("term") or ""),
                      "reason": result.get("reason"),
                      "next_action": result.get("next_action"),
                      "message": result.get("message")})
    return _text({"adopted": True, "term": result.get("term"),
                  "target_type": str(args.get("target_type") or ""),
                  "target": result.get("domain_ref"),
                  "note": "已把该词收编为目标的别名，候选转为已处置；"
                          "本体对象别名落的是草案，发布仍需人在本体建模页执行。"})


def _move_evolution_to_term(args):
    """把候选转入按数据源分桶的业务术语缓冲区（双闸在决策服务内）。"""
    from backend.modules.catalog.services import evolution_decision_service as decide

    bad = _evo_rejected(args)
    if bad:
        return _text({"error": bad}, is_error=True)
    try:
        _, evo_ctx, _ = _evo_query_context()
        if evo_ctx is None:
            return _text({"error": "无法确认调用者身份"}, is_error=True)
        result = decide.decide_candidate_by_name(
            evo_ctx, term=str(args.get("term") or ""),
            datasource_name=str(args.get("datasource") or ""),
            action_key="evolution_candidate.to_term",
            input_data={"description": str(args.get("description") or "")})
    except (PermissionError, ValueError) as e:
        return _text(_evo_decision_error(e), is_error=True)
    return _text({"moved": True, "term": result.get("term"),
                  "datasource": result.get("datasource") or "（全局）",
                  "created": bool(result.get("created")),
                  "message": result.get("message")})


def _reject_evolution_candidate(args):
    """驳回候选（不删行：反复被驳的信号本身是数据）。双闸在决策服务内。"""
    from backend.modules.catalog.services import evolution_decision_service as decide

    bad = _evo_rejected(args)
    if bad:
        return _text({"error": bad}, is_error=True)
    try:
        _, evo_ctx, _ = _evo_query_context()
        if evo_ctx is None:
            return _text({"error": "无法确认调用者身份"}, is_error=True)
        result = decide.decide_candidate_by_name(
            evo_ctx, term=str(args.get("term") or ""),
            datasource_name=str(args.get("datasource") or ""),
            kind=str(args.get("kind") or "").strip(),
            action_key="evolution_candidate.reject",
            note=str(args.get("note") or ""))
    except (PermissionError, ValueError) as e:
        return _text(_evo_decision_error(e), is_error=True)
    return _text({"rejected": True, "term": result.get("term"),
                  "note": "已驳回并留痕（不删行）；若用户认为驳回错了，需到下一条新证据里重新提出。"})


def _create_metric_from_evolution(args):
    """从候选建指标草稿（双闸在决策服务内：evolution:manage + metrics:manage）。"""
    from backend.modules.catalog.services import evolution_decision_service as decide

    bad = _evo_rejected(args)
    if bad:
        return _text({"error": bad}, is_error=True)
    try:
        _, evo_ctx, _ = _evo_query_context()
        if evo_ctx is None:
            return _text({"error": "无法确认调用者身份"}, is_error=True)
        result = decide.decide_candidate_by_name(
            evo_ctx, term=str(args.get("term") or ""),
            datasource_name=str(args.get("datasource") or ""),
            action_key="evolution_metric.create",
            input_data={"object": str(args.get("object") or ""),
                        "display_name": str(args.get("display_name") or ""),
                        "agg_type": str(args.get("agg_type") or "COUNT"),
                        "formula": str(args.get("formula") or ""),
                        "unit": str(args.get("unit") or ""),
                        "description": str(args.get("description") or "")})
    except (PermissionError, ValueError) as e:
        return _text(_evo_decision_error(e), is_error=True)
    return _text({"created": True, "metric": result.get("domain_ref"),
                  "is_active": result.get("is_active"),
                  "needs_completion": result.get("needs_completion") or [],
                  "message": result.get("message"),
                  "note": "草稿未启用：语义层不会广告它。补齐口径后需在指标中心启用，"
                          "不得向用户声称指标已生效。"})


def _create_dimension_from_evolution(args):
    """从候选建维度草稿（双闸在决策服务内；物理列由服务端映射，不外露）。"""
    from backend.modules.catalog.services import evolution_decision_service as decide

    bad = _evo_rejected(args)
    if bad:
        return _text({"error": bad}, is_error=True)
    try:
        _, evo_ctx, _ = _evo_query_context()
        if evo_ctx is None:
            return _text({"error": "无法确认调用者身份"}, is_error=True)
        result = decide.decide_candidate_by_name(
            evo_ctx, term=str(args.get("term") or ""),
            datasource_name=str(args.get("datasource") or ""),
            action_key="evolution_dimension.create",
            input_data={"object": str(args.get("object") or ""),
                        "property": str(args.get("property") or ""),
                        "display_name": str(args.get("display_name") or ""),
                        "category": str(args.get("category") or "属性"),
                        "description": str(args.get("description") or "")})
    except (PermissionError, ValueError) as e:
        return _text(_evo_decision_error(e), is_error=True)
    # target_column 是物理列名：不外露（护栏 §7），这里只回业务属性名
    return _text({"created": True, "dimension": result.get("domain_ref"),
                  "object": str(args.get("object") or ""),
                  "property": result.get("property"),
                  "message": result.get("message")})


def _mark_evolution_proposal_done(args):
    """把知识更新提议标记为已处理（双闸在决策服务内）。

    必须带 ``applied_ref``：提议只是备忘录，先在各域**真正落库**再回写落点业务名，
    否则「标记已处理」就是一句谎话（no-silent-degradation §1）。
    """
    from backend.modules.catalog.services import evolution_decision_service as decide

    bad = _evo_rejected(args)
    if bad:
        return _text({"error": bad}, is_error=True)
    try:
        _, evo_ctx, _ = _evo_query_context()
        if evo_ctx is None:
            return _text({"error": "无法确认调用者身份"}, is_error=True)
        applied_ref = str(args.get("applied_ref") or "").strip()
        result = decide.decide_proposal_by_name(
            evo_ctx, title=str(args.get("title") or "").strip(),
            target_type=str(args.get("target_type") or "").strip(),
            action_key="evolution_proposal.applied", applied_ref=applied_ref)
    except (PermissionError, ValueError) as e:
        return _text(_evo_decision_error(e), is_error=True)
    return _text({"marked": bool(result.get("changed")),
                  "title": result.get("title"),
                  "status": result.get("status"),
                  "applied_ref": applied_ref,
                  "message": result.get("message") or "已标记为已处理（留痕不删行）。"})


def _reject_evolution_proposal(args):
    """驳回知识更新提议（必须给理由）。双闸在决策服务内。"""
    from backend.modules.catalog.services import evolution_decision_service as decide

    bad = _evo_rejected(args)
    if bad:
        return _text({"error": bad}, is_error=True)
    try:
        _, evo_ctx, _ = _evo_query_context()
        if evo_ctx is None:
            return _text({"error": "无法确认调用者身份"}, is_error=True)
        result = decide.decide_proposal_by_name(
            evo_ctx, title=str(args.get("title") or "").strip(),
            target_type=str(args.get("target_type") or "").strip(),
            action_key="evolution_proposal.reject",
            note=str(args.get("note") or ""))
    except (PermissionError, ValueError) as e:
        return _text(_evo_decision_error(e), is_error=True)
    return _text({"rejected": bool(result.get("changed")),
                  "title": result.get("title"),
                  "status": result.get("status"),
                  "message": result.get("message") or "已驳回并留痕（不删行）。"})


def _run_evolution_digest(args):
    """立即执行一轮进化归纳（异步 job 同步等结果；失败显式暴露，不吞成「没有新待办」）。"""
    from backend.modules.catalog.services import evolution_decision_service as decide

    ctx, evo_ctx, actor = _evo_query_context()
    if evo_ctx is None:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    bad = _evo_rejected(args)
    if bad:
        return _text({"error": bad}, is_error=True)
    try:
        job = decide.submit_digest_job(evo_ctx, run_inline=True)
    except (PermissionError, ValueError) as e:
        return _text(_evo_decision_error(e), is_error=True)
    if str(job.get("status") or "") == "failed":
        return _text({"error": f"归纳未完成：{job.get('error_message') or '作业失败'}"},
                     is_error=True)
    out = job.get("result") or {}
    _evo_audit(ctx, "evolution.digest.run", "evolution_candidate",
               str(out.get("summary") or "")[:500], 0, actor)
    return _text({"summary": out.get("summary"),
                  "pending_by_kind": out.get("pending_by_kind") or {},
                  "feedback": out.get("feedback") or {},
                  "recheck": out.get("recheck") or {},
                  "verification": out.get("verification") or {},
                  "job_id": job.get("job_id"),
                  "note": "归纳已完成并落作业记录；「没有新待办」与「作业失败」是两件事，"
                          "失败时本工具会显式报错，不得把失败说成没有发现。"})


# ═══════════════════════════════════════════════════════════════════════
# 能力流程（P3）：受控 auto 场景的发起入口（编排权限 capability:run 把关）
# ═══════════════════════════════════════════════════════════════════════

def _run_capability_flow(args):
    """发起受控能力流程（P3）：auto 三条件 + 创建前全步权限预检 + 自动推进。

    人工步骤停在 waiting_human（AI 不得代做）；完成复核读内容服务状态，
    不信任客户端“已完成”。同步/DAG/UDF/物化动作永远进不了流程。
    """
    import hashlib

    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.modules.platform.services import capability_flow_service as flows

    ctx = get_execution_context()
    if ctx is None or int(getattr(ctx, "user_id", 0) or 0) <= 0:
        return _text({"error": "无法确认调用者身份"}, is_error=True)
    scenario = str(args.get("scenario") or "").strip()
    payload = args.get("input") or {}
    if not scenario:
        return _text({"error": "缺少 scenario（场景 key，如 ops_content_publish）"}, is_error=True)
    if not isinstance(payload, dict):
        return _text({"error": "input 必须是对象（场景 input_schema 声明的业务参数）"},
                     is_error=True)
    idem = str(args.get("idempotency_key") or "").strip()
    if not idem:
        # 缺省按 scenario+input 派生：重复调用天然幂等，返回首次运行
        raw = json.dumps({"s": scenario, "i": payload}, sort_keys=True,
                         ensure_ascii=False, default=str)
        idem = f"ai:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"
    try:
        # AI 通道：服务端按 AS-BOT 场景白名单（tools.scenarios）再裁编排层域边界；
        # as_bot 只从可信执行上下文取，不接受入参传身份
        _rt = getattr(ctx, "extra", {}).get("secure_runtime") if ctx is not None else None
        _as_bot = getattr(getattr(_rt, "policy", None), "as_bot", None)
        run = flows.create_run(
            scenario_key=scenario, input_payload=payload, idempotency_key=idem,
            user_id=int(getattr(ctx, "user_id", 0) or 0),
            username=str(getattr(ctx, "username", "") or ""),
            workspace_id=int(getattr(ctx, "workspace_id", 0) or 0),
            channel="ai", as_bot=_as_bot if isinstance(_as_bot, dict) else {})
    except flows.FlowError as e:
        return _text({"error": str(e), "code": e.code}, is_error=True)
    steps = run.get("steps") or []
    return _text({
        "run_id": run.get("run_id"),
        "scenario": run.get("scenario"),
        "status": run.get("status"),
        "current_step": run.get("current_step"),
        "steps": [{"step_no": s.get("step_no"), "label": s.get("label"),
                   "channel": s.get("channel"), "status": s.get("status")}
                  for s in steps],
        "replayed": bool(run.get("replayed")),
        "note": ("人工步骤（channel=human）停在 waiting_human：发布由人在内容中心完成，"
                 "完成后调用复核推进；不得向用户声称人工步骤已完成。"),
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
                       "可见范围以角色授权为准（管理员可见全部看板）；"
                       "不返回布局与筛选参数。",
        "perm_code": "dashboard:read", "object_key": "dashboard", "read_only": True,
        "schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"}},
            "required": []},
        "handler": _list_dashboards,
    },
    "scheduled_task.list": {
        "tool_name": "list_scheduled_tasks",
        "label": "查看调度任务",
        "description": "列出本人创建的调度任务（名称/执行周期/状态）。"
                       "任务跟随创建者，不含他人任务；不返回 Webhook 密钥与上次错误原文。",
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
        "description": "创建一个定时 Agent 分析任务（任务只携带分析问题清单与执行周期）。"
                       "不选 AS-BOT、不选数据源：能力与可查范围按任务创建者的角色实时继承。"
                       "Webhook 密钥由服务端生成，不会返回。",
        "perm_code": "scheduled:manage", "object_key": "scheduled_task", "read_only": False,
        "schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "任务名称"},
            "cron_expression": {"type": "string", "description": "Cron 表达式，如 0 8 * * *"},
            "description": {"type": "string", "description": "任务说明"},
            "timezone": {"type": "string", "description": "时区，默认 Asia/Shanghai"},
            "task_config": {"type": "object", "description":
                            "只接受 {questions: [{title, question}], context: 可选补充说明}；"
                            "不接受 as_bot_key/datasource_id 等内部标识（会被剥除）"}},
            "required": ["name", "cron_expression", "task_config"]},
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
            "saved_query_id": {"type": "integer", "description": "分析来源：已保存查询 ID"},
            "template": {"type": "string", "description":
                         "报告模板的业务名（可选，取「报告模板」配置里的名称）；"
                         "名称不存在时会被拒回并附可用清单，不会静默换成通用版式"}},
            "required": ["title"]},
        "handler": _generate_report,
    },
    "report.save": {
        "tool_name": "save_report",
        "label": "保存分析报表",
        "description": "把本轮已完成并展示给用户的分析结论直接保入报告中心（同步入库，"
                       "不排队、不重新取数）。content 只写用户已经在本会话看到的业务结论（Markdown），"
                       "不得写 SQL、物理表/列名、账号或地址；报表仅本人可见。"
                       "需要按数据源重新取数的报告请改用 generate_report。",
        "perm_code": "report:manage", "object_key": "report", "read_only": False,
        "schema": {"type": "object", "properties": {
            "title": {"type": "string", "description": "报表标题（业务可读名）"},
            "content": {"type": "string", "description": "报表正文（Markdown，仅业务结论）"},
            "format": {"type": "string", "enum": ["markdown", "html"],
                       "description": "正文格式，默认 markdown"}},
            "required": ["title", "content"]},
        "handler": _save_report,
    },
    # ── 内容中心（资讯栏目）：生成内容与管理内容 ──────────────────────────────
    # object_key 必须与系统本体（kind='system'，目录见
    # `backend/modules/catalog/services/system_ontology_catalog.py`）里登记的对象一致：
    # 悬空 key 会让 AS-BOT 的语义 grounding 找不到对象。内容类动作对应
    # content_article / content_category，决策类对应 decision_advice；
    # 本体侧改名/下线时同批回填这里（tests/test_system_ontology_catalog.py 把门）。
    "content_article.list": {
        "tool_name": "list_content_articles",
        "label": "查看内容清单",
        "description": "列出内容中心（资讯栏目）的条目：slug/标题/摘要/栏目/状态/标签/阅读数/"
                       "是否在线/语料同步状态。可按状态、栏目 key、标签、形态与关键词过滤。"
                       "不返回条目内部 id；正文请用 get_content_article。",
        "perm_code": "article:manage", "object_key": "content_article", "read_only": True,
        "schema": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["all", "draft", "in_review", "approved",
                                                  "published", "archived"],
                       "description": "工作流状态，默认 all（含草稿）"},
            "category": {"type": "string", "description": "栏目 key（取自 list_content_taxonomy）"},
            "tag": {"type": "string", "description": "标签名"},
            "kind": {"type": "string", "enum": ["markdown", "link", "file"],
                     "description": "内容形态：站内正文 / 外链 / 附件"},
            "q": {"type": "string", "description": "标题或摘要关键词"},
            "limit": {"type": "integer", "description": "返回条数上限，默认 20，最大 50"}},
            "required": []},
        "handler": _list_content_articles,
    },
    "content_article.get": {
        "tool_name": "get_content_article",
        "label": "查看内容正文",
        "description": "按 slug 取一篇内容的正文与元信息，并带回门禁预检结果（gate_hard / "
                       "gate_warnings）。revision=head 取工作稿（未发布改动），live 取线上版。"
                       "改稿前必须先调本工具取 version，供 update_content_draft 做乐观锁。",
        "perm_code": "article:manage", "object_key": "content_article", "read_only": True,
        "schema": {"type": "object", "properties": {
            "slug": {"type": "string", "description": "条目业务标识（不是内部 id）"},
            "revision": {"type": "string", "enum": ["head", "live"],
                         "description": "取哪一版，默认 head（工作稿）"}},
            "required": ["slug"]},
        "handler": _get_content_article,
    },
    "content_taxonomy.list": {
        "tool_name": "list_content_taxonomy",
        "label": "查看栏目与标签名册",
        "description": "列出可用栏目（key/label/说明/在用状态/已发布篇数）与标签（名称/命中篇数）。"
                       "写作前必须先调本工具：category 只能取返回的 key，tags 只能取返回的名称，"
                       "不得编造。栏目与标签的增删改属编排配置，由人在页面做。",
        "perm_code": "article:manage", "object_key": "content_category", "read_only": True,
        "schema": {"type": "object", "properties": {
            "include_inactive": {"type": "boolean", "description": "是否带出已停用项，默认 false"}},
            "required": []},
        "handler": _list_content_taxonomy,
    },
    "content_draft.create": {
        "tool_name": "create_content_draft",
        "label": "创建内容草稿",
        "description": "新建一篇内容**草稿**（状态固定为 draft，门户看不到，也不会自动进知识库）。"
                       "正文写 Markdown，标题是唯一事实源（正文不要再写 H1）；数字与结论要在 refs 里"
                       "登记出处（标题/链接），没有出处就写定性描述，不得编造数据。"
                       "正文含内部标识（连接串/生成 SQL/主机/报错栈）会被当场拒写。"
                       "发布与审核不开放给 AI：写完请提示用户去「内容中心」走审核发布。",
        "perm_code": "article:manage", "object_key": "content_article", "read_only": False,
        "schema": {"type": "object", "properties": {
            "title": {"type": "string", "description": "标题（必填，业务可读）"},
            "body": {"type": "string", "description": "正文 Markdown（站内正文形态必填）"},
            "summary": {"type": "string", "description": "摘要（列表与检索展示用）"},
            "category": {"type": "string", "description": "栏目 key（必须来自 list_content_taxonomy）"},
            "tags": {"type": "array", "items": {"type": "string"},
                     "description": "标签名清单（必须来自 list_content_taxonomy）"},
            "kind": {"type": "string", "enum": ["markdown", "link"],
                     "description": "形态；附件（file）请由人在编辑器上传，AI 不写附件"},
            "link_url": {"type": "string", "description": "外链地址（kind=link 时必填，http(s) 开头）"},
            "link_label": {"type": "string", "description": "外链显示名"},
            "refs": {"type": "array", "items": {"type": "object"},
                     "description": "外部出处 [{title, url}]：数字/结论的来源，门禁会检查"},
            "slug": {"type": "string", "description": "建议传英文短横线标识（如 scan-accuracy-metric，与现有条目同风格）；不传则按标题生成 —— 中文标题会得到中文 slug，链接与语料文档名都不友好"}},
            "required": ["title", "body"]},
        "handler": _create_content_draft,
    },
    "content_draft.update": {
        "tool_name": "update_content_draft",
        "label": "编辑内容草稿",
        "description": "按 slug 修改一篇内容（只改传了的字段）。**必须**带 expected_version"
                       "（先 get_content_article 取 version），否则拒绝写入 —— 防覆盖他人编辑。"
                       "改动只落工作稿：已发布内容改完线上仍是旧版，需重新走审核与发布才生效。"
                       "不接受 status/published/approved 等发布态字段（发布不开放 AI）。",
        "perm_code": "article:manage", "object_key": "content_article", "read_only": False,
        "schema": {"type": "object", "properties": {
            "slug": {"type": "string", "description": "条目业务标识"},
            "expected_version": {"type": "integer",
                                 "description": "乐观锁：get_content_article 返回的 version"},
            "title": {"type": "string"}, "summary": {"type": "string"}, "body": {"type": "string"},
            "category": {"type": "string", "description": "栏目 key"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "refs": {"type": "array", "items": {"type": "object"}},
            "change_note": {"type": "string", "description": "本次改动说明（进版本清单）"}},
            "required": ["slug", "expected_version"]},
        "handler": _update_content_draft,
    },
    "content_review.submit": {
        "tool_name": "submit_content_review",
        "label": "提交内容审核",
        "description": "按 slug 把稿件提交到待审队列（**不是发布**）。门禁硬阻断项会逐条回传原因"
                       "（占位未填、没选栏目、正文为空、外链缺地址等），修完才能提交成功。"
                       "审核通过与发布/下架都由人操作（article:review / article:publish 未开放 AI）。",
        "perm_code": "article:manage", "object_key": "content_article", "read_only": False,
        "schema": {"type": "object", "properties": {
            "slug": {"type": "string", "description": "条目业务标识"}},
            "required": ["slug"]},
        "handler": _submit_content_review,
    },
    "knowledge_proposal.create": {
        "tool_name": "create_knowledge_proposal",
        "label": "提交知识更新提议",
        "description": "把用户在对话里给出的业务知识纠正/补充（口径说明、文档要补的一段、"
                       "某类分析的做法、对象说明或别名）提交到**进化中心待审队列**。"
                       "这只是提议：**不会立即修改本体、知识库或分析场景**，需要人到"
                       "「进化中心 → 知识更新」逐条确认后才落库；不要向用户声称已经改好了。"
                       "target_type 选落点：kb_doc=知识库文档正文、scenario=分析场景、"
                       "ontology_object=本体对象说明/别名。target_hint 只给**业务名**"
                       "（知识库名或文档标题 / 场景标题 / 对象业务名），不传任何内部 id。"
                       "用户没明确说清落点时可省略或问一句，不要自己编内部标识。",
        "perm_code": "knowledge:manage", "object_key": "knowledge_proposal", "read_only": False,
        "schema": {"type": "object", "properties": {
            "target_type": {"type": "string", "enum": ["kb_doc", "scenario", "ontology_object"],
                            "description": "落点域：知识库文档 / 分析场景 / 本体对象"},
            "title": {"type": "string", "description": "标题（文档标题、场景标题或对象业务名）"},
            "content": {"type": "string",
                        "description": "建议的正文/说明（Markdown；对象类可写一段业务描述）"},
            "rationale": {"type": "string",
                          "description": "为什么提这条（用户纠正了什么、口径依据），供人工判断"},
            "target_hint": {"type": "string",
                            "description": "目标业务名提示（知识库名/文档标题/场景标题/对象业务名）；不要传 id"},
            "datasource": {"type": "string",
                           "description": "所属数据源业务名（仅本体对象类需要，其它可空）"}},
            "required": ["target_type", "title", "content"]},
        "handler": _create_knowledge_proposal,
    },
    # ── 进化中心审批（双闸：evolution:manage + 各出口原本所属域码）──────────
    # 候选/提议是「对既有事实的确认」，处置落点仍是各域既有写入（本体草案 / 字典草稿 /
    # 提议状态机）；eval:* 仍不接 AI（评价 AI 的标尺不能由 AI 自己写）。
    "evolution_queue.list": {
        "tool_name": "list_evolution_review_queue",
        "label": "查看进化中心待办",
        "description": "列出进化中心的**待处置**项：语义缺口候选（缺别名/待建指标/待建维度/"
                       "待转术语/绑定缺口，带服务端重算的处置建议与置信度）与待审知识更新提议"
                       "（标题/落点域/理由）。只回业务词与建议，不含内部 id。处置前先调它拿准业务名。",
        "perm_code": "evolution:read", "object_key": "alias_suggestion", "read_only": True,
        "schema": {"type": "object", "properties": {
            "kind": {"type": "string",
                     "enum": ["new_metric", "new_dimension", "alias", "binding_gap", "term"],
                     "description": "只看某一类候选（缺省全部）"},
            "datasource": {"type": "string",
                           "description": "数据源业务名（缺省全部；多源同名时用来限定）"},
            "limit": {"type": "integer", "description": "候选返回条数上限，默认 30，最大 100"}},
            "required": []},
        "handler": _list_evolution_review_queue,
    },
    "evolution_candidate.adopt": {
        "tool_name": "adopt_evolution_alias",
        "label": "收编别名（审批候选）",
        "description": "把一个候选的业务词收编为**既有**指标/维度字典行或本体对象的别名，"
                       "候选转为已处置。target_type=metric|dimension 时 target 填字典名，"
                       "=object 时填对象业务名；目标必须来自 list_evolution_review_queue 或"
                       "清单里的在用口径（不得手造）。目标不存在时不报错而回「下一步出口」指引"
                       "（new_metric/new_dimension），照实转述给用户，不得说成已收编。"
                       "本体对象别名落的是草案，发布仍需人操作。",
        "perm_code": "evolution:manage", "object_key": "alias_suggestion",
        "read_only": False,
        "schema": {"type": "object", "properties": {
            "term": {"type": "string", "description": "候选的业务词（与清单一字不差）"},
            "target_type": {"type": "string", "enum": ["metric", "dimension", "object"],
                            "description": "收编到指标字典/维度字典/本体对象"},
            "target": {"type": "string", "description": "目标业务名（字典名或对象业务名）"},
            "datasource": {"type": "string",
                           "description": "数据源业务名（多源同名候选时用来限定）"}},
            "required": ["term", "target_type", "target"]},
        "handler": _adopt_evolution_alias,
    },
    "evolution_candidate.to_term": {
        "tool_name": "move_evolution_to_term",
        "label": "候选转业务术语",
        "description": "把候选转入按数据源分桶的业务术语缓冲区（尚未绑定任何对象/字典的纯黑话）。"
                       "已存在的同源同词不会重复建（created=false）。后续映射到对象/字典仍需人在页面确认。",
        "perm_code": "evolution:manage", "object_key": "business_term", "read_only": False,
        "schema": {"type": "object", "properties": {
            "term": {"type": "string", "description": "候选的业务词（与清单一字不差）"},
            "datasource": {"type": "string", "description": "数据源业务名（多源同名时限定）"},
            "description": {"type": "string", "description": "术语说明（可选）"}},
            "required": ["term"]},
        "handler": _move_evolution_to_term,
    },
    "evolution_candidate.reject": {
        "tool_name": "reject_evolution_candidate",
        "label": "驳回进化候选",
        "description": "驳回一条候选（不删行，留痕才能看出这类信号被反复判为无效）。"
                       "驳回前必须先征得用户确认；仅在确实不成立（不是真实业务问法/已有正确叫法）时用。",
        "perm_code": "evolution:manage", "object_key": "alias_suggestion",
        "read_only": False,
        "schema": {"type": "object", "properties": {
            "term": {"type": "string", "description": "候选的业务词（与清单一字不差）"},
            "kind": {"type": "string",
                     "enum": ["new_metric", "new_dimension", "alias", "binding_gap", "term"],
                     "description": "同类多词时用来缩小范围（可选）"},
            "note": {"type": "string", "description": "驳回理由（必填，会落审计）"},
            "datasource": {"type": "string", "description": "数据源业务名（多源同名时限定）"}},
            "required": ["term", "note"]},
        "handler": _reject_evolution_candidate,
    },
    "evolution_metric.create": {
        "tool_name": "create_metric_from_evolution",
        "label": "从候选建指标草稿",
        "description": "把候选建成本体对象上的指标草稿（is_active=0，未启用）。object 必须是"
                       "该数据源下已存在的业务对象（可用业务名或对象 key）；口径（formula）缺省"
                       "时返回 needs_completion=['formula']，必须如实告知用户“草稿已建但缺公式、"
                       "未启用”，不得声称指标已可用。",
        "perm_code": "evolution:manage", "object_key": "metric", "read_only": False,
        "schema": {"type": "object", "properties": {
            "term": {"type": "string", "description": "候选的业务词（与清单一字不差）"},
            "object": {"type": "string", "description": "归属对象：业务名或对象 key"},
            "formula": {"type": "string",
                        "description": "指标公式/口径（如 COUNT(id)、SUM(amount)）；缺省则草稿缺口径"},
            "agg_type": {"type": "string",
                         "description": "聚合方式（COUNT/SUM/AVG/MIN/MAX/COUNT_DISTINCT，缺省 COUNT）"},
            "unit": {"type": "string", "description": "单位（可选）"},
            "display_name": {"type": "string", "description": "业务显示名（缺省用候选词）"},
            "description": {"type": "string", "description": "口径说明（可选）"},
            "datasource": {"type": "string", "description": "数据源业务名（多源同名时限定）"}},
            "required": ["term", "object"]},
        "handler": _create_metric_from_evolution,
    },
    "evolution_dimension.create": {
        "tool_name": "create_dimension_from_evolution",
        "label": "从候选建维度草稿",
        "description": "把候选建成本体对象属性上的维度草稿。object 用归属对象业务名，"
                       "property 用对象主表上的**业务属性名**（来自清单；物理列名由服务端映射，"
                       "不得传也不回显）。属性名查不到时回可选业务属性清单，照实转述。",
        "perm_code": "evolution:manage", "object_key": "dimension", "read_only": False,
        "schema": {"type": "object", "properties": {
            "term": {"type": "string", "description": "候选的业务词（与清单一字不差）"},
            "object": {"type": "string", "description": "归属对象：业务名或对象 key"},
            "property": {"type": "string", "description": "对象主表上的业务属性名（不是物理列名）"},
            "display_name": {"type": "string", "description": "业务显示名（缺省用候选词）"},
            "category": {"type": "string", "description": "维度分类（缺省「属性」）"},
            "description": {"type": "string", "description": "口径说明（可选）"},
            "datasource": {"type": "string", "description": "数据源业务名（多源同名时限定）"}},
            "required": ["term", "object", "property"]},
        "handler": _create_dimension_from_evolution,
    },
    "evolution_proposal.applied": {
        "tool_name": "mark_evolution_proposal_done",
        "label": "标记知识更新已处理",
        "description": "把一条**待审**知识更新提议标记为已处理。提议只是备忘录：必须先在目标域"
                       "（知识库文档/分析场景/本体对象）真正落库，再回写落点业务名（applied_ref）；"
                       "没有落库不得标记。非待处理状态不会覆盖既有结论（marked=false 并回当前状态）。",
        "perm_code": "evolution:manage", "object_key": "knowledge_proposal",
        "read_only": False,
        "schema": {"type": "object", "properties": {
            "title": {"type": "string", "description": "提议标题（与清单一字不差）"},
            "applied_ref": {"type": "string",
                            "description": "落点业务名（如「知识库名 · 文档标题」/场景标题/对象业务名）"},
            "target_type": {"type": "string",
                            "enum": ["kb_doc", "scenario", "ontology_object"],
                            "description": "落点域（同名多条时用来指定）"}},
            "required": ["title", "applied_ref"]},
        "handler": _mark_evolution_proposal_done,
    },
    "evolution_proposal.reject": {
        "tool_name": "reject_evolution_proposal",
        "label": "驳回知识更新提议",
        "description": "驳回一条待审知识更新提议，必须给出理由（note，会落审计）。不删行；"
                       "驳回前先征得用户确认，并说明“被驳的信号本身是数据”。"
                       "只有待处理状态可以驳回，已处置行不会被改回去。",
        "perm_code": "evolution:manage", "object_key": "knowledge_proposal",
        "read_only": False,
        "schema": {"type": "object", "properties": {
            "title": {"type": "string", "description": "提议标题（与清单一字不差）"},
            "note": {"type": "string", "description": "驳回理由（必填，会落审计）"},
            "target_type": {"type": "string",
                            "enum": ["kb_doc", "scenario", "ontology_object"],
                            "description": "落点域（同名多条时用来指定）"}},
            "required": ["title", "note"]},
        "handler": _reject_evolution_proposal,
    },
    "evolution_digest.run": {
        "tool_name": "run_evolution_digest",
        "label": "立即执行进化归纳",
        "description": "立即跑一轮进化归纳（反馈→候选、候选复核、闭环复查），不等次日定时作业。"
                       "返回本轮汇总与待办分布。归纳失败会显式报错（可能耗时较长），"
                       "不得把「没有新待办」与「作业失败」混为一谈。",
        "perm_code": "evolution:manage", "object_key": "alias_suggestion",
        "read_only": False,
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": _run_evolution_digest,
    },
    "capability.run_flow": {
        "tool_name": "run_capability_flow",
        "label": "运行能力流程",
        "description": "按系统能力本体的场景发起受控流程（仅限声明 auto 且在服务端白名单内的场景）："
                       "传场景 key 与运行输入，自动步骤按序执行且逐步过权限码；"
                       "人工步骤停在 waiting_human，由人在界面完成后复核推进（AI 不得代做）。"
                       "缺任一步权限时创建前即拒，不会建半截运行。",
        "perm_code": "capability:run", "object_key": "capability_flow", "read_only": False,
        "schema": {"type": "object", "properties": {
            "scenario": {"type": "string",
                         "description": "场景 key（如 ops_content_publish）"},
            "input": {"type": "object",
                      "description": "运行输入（场景 input_schema 声明的业务参数）"},
            "idempotency_key": {"type": "string",
                                "description": "幂等键（可选；缺省按 scenario+input 派生）"}},
            "required": ["scenario", "input"]},
        "handler": _run_capability_flow,
    },
    "decision.list": {
        "tool_name": "list_decision_trees",
        "label": "查看决策树清单",
        "description": "列出当前已**发布生效**的决策建议树（名称/说明/严重级/适用站点/最近一次评估结果）。"
                       "只回业务摘要，不回条件阈值与判定结构（那是口径资产，由人在决策中心看）；"
                       "草案状态的树不出现（草案未生效，拿它回答用户等于报未上线的判断标准）。",
        "perm_code": "decision:read", "object_key": "decision_advice", "read_only": True,
        "schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"}},
            "required": []},
        "handler": _list_decision_trees,
    },
    "decision.evaluate": {
        "tool_name": "run_decision_evaluation",
        "label": "跑一次决策建议评估",
        "description": "按已发布的决策树重新取数并判定，返回命中路径、建议动作与**脱敏后**的依据"
                       "（只给指标业务名、阈值与实测值，不给物理表列、SQL 与数据源标识）。"
                       "用于用户问「现在该怎么办」且本轮分析结果不够支撑判定时；"
                       "未命中/未评估都会带原因回来，不得把「没有建议」说成「数据正常」。",
        "perm_code": "decision:read", "object_key": "decision_advice", "read_only": True,
        "schema": {"type": "object", "properties": {
            "tree": {"type": "string", "description": "决策树业务名或标识（先调 list_decision_trees 取名字）"},
            "site": {"type": "string", "description": "可选站点业务名/代码；不传用会话生效站点"},
            "all": {"type": "boolean", "description": "true=评估全部已发布树（用户问「最近有什么要处理的」时用）"}},
            "required": []},
        "handler": _run_decision_evaluation,
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


def shell_tool_names() -> list[str]:
    """两个壳工具名（供 tool_policy 授权与 sdk_tools 注册共用一份清单）。"""
    return [s["name"] for s in SHELL_TOOL_SPECS]


def qualified_shell_name(tool_name: str) -> str:
    return f"mcp__{SERVER_NAME}__{tool_name}"


def is_action_qualified_name(name) -> bool:
    """是否为**动作**精确名（只能经 run_platform_function 派发，不可直接调）。

    动作名留在 ``policy.allowed``/``policy.unavailable`` 里是授权事实（逐动作裁决 +
    面板展示），但它们不再是可直接调用的工具：提示词的「实际工具权限」清单
    必须滤掉它们，否则等于告诉模型“只可用下列工具”又列了一批调不了的入口。
    """
    full = str(name or "")
    if not full.startswith(f"mcp__{SERVER_NAME}__"):
        return False
    bare = full.rsplit("__", 1)[-1]
    return any(spec["tool_name"] == bare for spec in FUNCTION_ACTION_SPECS.values())


def build_function_server(backend: str = "qoder", tool_names=None):
    """构建功能能力的进程内 MCP server —— **只注册两个壳**。

    ``tool_names`` = 本次角色可用的动作工具名（由 ``tool_policy.compile_policy`` 按角色
    权限码裁定后传入）：
      * 空列表 / None → 本会话没有任何功能能力，**连壳也不注册**
        （返回 None）—— 提示词不会宣称能操作，也不留一个“调了只会报错”的空入口；
      * 非空 → 注册 list/run 两个壳，具体可用功能由 ``list_platform_functions`` 实时下发。

    动作本身不再逐个注册（17 份 schema 常驻上下文 = 每轮白付 token）；但动作精确名
    仍在 ``policy.allowed`` 里，``run_platform_function`` 逐动作 ``check_tool`` 用的就是它。
    """
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

    selected = set(tool_names or ())
    if not selected:
        return None
    unknown = selected - {s["tool_name"] for s in FUNCTION_ACTION_SPECS.values()}
    if unknown:
        raise ValueError(f"存在未知功能工具: {sorted(unknown)}")

    tools = [
        make_tool(backend, s["name"], s["description"], s["schema"], s["handler"],
                  annotations=READONLY_ANNOTATIONS if s["read_only"] else None,
                  qualified_name=qualified_shell_name(s["name"]))
        for s in SHELL_TOOL_SPECS
    ]
    return make_server(backend, SERVER_NAME, tools)
