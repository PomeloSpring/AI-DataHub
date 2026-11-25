"""system 工具组 — 本系统运营/可观测只读能力（AS-BOT 聚焦本系统）。

AS-BOT 是应用内运维助手，回答“本系统”的用量/健康/建模队列等问题应走这里，
而不是拿业务本体（如 test-alb）知识检索充数。

安全：
- 全部只读；仅管理员可用（经 resolve_execution_owner 服务端解析身份 + 角色，fail-closed）。
- 复用既有 observability_service / 元库聚合，不新建取数通道、不连业务数据源。
- 不回显数据源主机/账号/密码等基础设施细节。
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Annotated, Optional

from backend.modules.mind.execution.sdk_tools.catalog_tools import _text

logger = logging.getLogger(__name__)

_ENTRYPOINTS = ("chat", "playground", "agent", "scheduled", "embed", "report")


def _admin_identity():
    """服务端解析当前调用者身份；非管理员返回 None（fail-closed）。"""
    from backend.modules.mind.execution.sdk_tools.context import get_execution_context
    from backend.common.auth import resolve_execution_owner
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
    from backend.common.db import execute_query
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
    from backend.modules.auth.services import observability_service as obs

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
            "cost_amount": summary.get("cost_amount"), "cost_currency": summary.get("cost_currency"),
            "errors": summary.get("errors"),
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
        # 注：adh_datasources 无启用状态列，不再报 datasources_active（旧值因列缺失恒降级为 None）
        "datasources": _count("SELECT COUNT(*) FROM adh_datasources"),
        "tables": _count("SELECT COUNT(*) FROM adh_table_info WHERE is_active = 1"),
        "columns": _count("SELECT COUNT(*) FROM adh_column_metadata WHERE is_active = 1"),
        "knowledge_bases_active": _count("SELECT COUNT(*) FROM adh_knowledge_bases WHERE status = 'active'"),
        # 判别口径用 kind（x3 归属键改造），不用 datasource_id=0：
        # 本体归属已按业务域而非数据源，业务本体的 datasource_id 也是 0，
        # 旧口径会把业务本体误计为系统本体（as-bot-system-waker §1 域边界）。
        "ontology_models_system": _count(
            "SELECT COUNT(*) FROM adh_ontology_models WHERE kind = 'system' "
            "AND status IN ('active','draft')"),
        "ontology_models_business": _count(
            "SELECT COUNT(*) FROM adh_ontology_models WHERE kind = 'business' "
            "AND status IN ('active','draft')"),
        "pending_contract_changes": _count(
            "SELECT COUNT(*) FROM adh_data_product_versions WHERE status = 'pending'"),
        "pending_alias_suggestions": _count("SELECT COUNT(*) FROM adh_alias_suggestions WHERE status = 'pending'"),
        "tasks_failed_24h": _count(
            "SELECT COUNT(*) FROM adh_scheduled_logs WHERE status = 'failed' "
            "AND started_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)"),
        "tasks_running": _count("SELECT COUNT(*) FROM adh_scheduled_logs WHERE status IN ('queued','running')"),
    }
    return _text({"generated_at": datetime.now().isoformat(timespec="seconds"),
                  "overview": overview,
                  "note": "系统级只读快照；None 表示该项统计不可用（表/列缺失），不代表 0。"})


# ══════════════════════════════════════════════════════════════════════
# 任务流（系统能力本体 scenarios[].flow）—— 把行为/规则/场景串成可执行计划
# ══════════════════════════════════════════════════════════════════════


def _norm(s) -> str:
    """名称归一：直接用系统本体的归一口径。

    不能在这里另写一份：alias 匹配与目录的「别名撞车」自检必须同口径，
    否则会出现「目录认为不冲突、这里却挑不出唯一场景」的诡异现象。
    """
    from backend.modules.catalog.services.system_ontology_catalog import normalize_scenario_name

    return normalize_scenario_name(s)


def _active_system_doc() -> dict:
    """取生效的系统能力本体 doc（kind='system' + status='active'）。

    特意读库而不是读代码目录：AS-BOT 通过知识库看到的就是库里这份，工具不能对不上。
    读不到就抛错（不静默回退），由调用方显式告知用户。
    """
    from backend.common.db import execute_query

    row = execute_query(
        "SELECT name, json_content FROM adh_ontology_models "
        "WHERE kind = 'system' AND status = 'active' "
        "ORDER BY updated_at DESC, id DESC LIMIT 1",
        fetchone=True)
    if not row:
        raise LookupError("系统能力本体未激活（无 kind='system' AND status='active' 行）")
    content = row.get("json_content")
    if isinstance(content, (bytes, str)):
        content = json.loads(content)
    if not isinstance(content, dict):
        raise ValueError("系统能力本体的 json_content 不是对象结构")
    return content


def _pick_scenario(scenarios: list, query: str) -> tuple[Optional[dict], list]:
    """按 key/标题/业务别名选场景；命中多个回候选（宁回抛也不猜）。"""
    q = _norm(query)
    if not q:
        return None, []

    def _names(s: dict) -> list[str]:
        return [_norm(s.get("key")), _norm(s.get("title"))] \
            + [_norm(a) for a in (s.get("aliases") or [])]

    exact = [s for s in scenarios if q in _names(s)]
    if len(exact) == 1:
        return exact[0], []
    pool = exact or [s for s in scenarios
                     if any(q in n for n in _names(s) if n)]
    if len(pool) == 1:
        return pool[0], []
    return None, pool


def _step_runnable(channel: str, code: str, read_only: bool,
                   role_perms: dict) -> tuple[bool, str]:
    """该步骤当前身份能不能做：channel=human 恒不可由 AI 做；ai 看持码与有效级别。

    本函数只**描述**可执行性，不代替执行裁决——真调用时工具仍由权限码 fail-closed 把关。
    """
    if channel == "human":
        return False, "该步骤只能由人在对应界面操作（AI 无此工具；不得代做或声称已完成）"
    if channel != "ai":
        return False, f"步骤未声明可执行的通道（channel={channel or '空'}）"
    if not code:
        return False, "该步骤未登记权限码，无法判定"
    item = role_perms.get(code)
    if not item:
        return False, f"当前角色未持有权限码 {code}"
    from backend.core import perm_link

    level, capped = perm_link.effective_level(code, item.get("ai_access"),
                                             item.get("ai_note") or "")
    need = "read" if read_only else "write"
    if perm_link.AI_LEVELS.index(level) < perm_link.AI_LEVELS.index(need):
        why = f"权限码 {code} 的 AI 级别为 {level}，该步骤需要 {need}"
        if capped:
            why += f"（{capped}）"
        return False, why
    return True, ""


async def system_capability_plan(args):
    """任务流查询：场景 → 有序步骤 → 每步动作/权限码/规则门/执行通道 + 可执行性。"""
    identity = _admin_identity()
    if not identity:
        return _text({"error": "系统能力计划仅管理员可查，当前身份无权限"}, is_error=True)
    query = str(args.get("scenario") or "").strip()
    try:
        doc = _active_system_doc()
    except Exception as e:  # noqa: BLE001 — 未激活/内容坏必须显式回传，不静默换数据源
        logger.error("[system_capability_plan] 读取系统本体失败: %s", e)
        return _text({"error": f"系统能力本体不可用：{e}"}, is_error=True)

    scenarios = [s for s in (doc.get("scenarios") or []) if isinstance(s, dict)]
    if not scenarios:
        return _text({"error": "系统能力本体还没有登记任何场景"}, is_error=True)
    if not query:
        # 不给名字就让模型先看清单：比逼它猜一个业务名再回候选少一轮无效调用
        return _text({
            "scenario": None,
            "executable": False,
            "candidates": [{"key": s.get("key"), "title": s.get("title"),
                            "aliases": s.get("aliases") or [],
                            "executable": bool(s.get("flow"))} for s in scenarios],
            "note": "未指定场景：以上是全部候选（title/aliases/key 任一可作为 scenario 重试）。"
                    "executable=false 的是问答型场景（没有任务步骤），按 uses_objects/uses_rules 口径回答。",
        })
    scn, candidates = _pick_scenario(scenarios, query)
    if scn is None:
        pool = candidates or scenarios
        return _text({
            "error": f"没有唯一匹配到场景「{query}」（不猜，请用候选业务名重试）",
            "candidates": [{"key": s.get("key"), "title": s.get("title"),
                            "aliases": s.get("aliases") or [],
                            "executable": bool(s.get("flow"))} for s in pool],
        }, is_error=True)

    rules = {str(r.get("key") or ""): r for r in (doc.get("rules") or [])}
    actions = {str(a.get("key") or ""): a
               for o in (doc.get("objects") or []) for a in (o.get("actions") or [])}
    try:
        from backend.core.role_service import role_service
        role_perms = role_service.get_user_role_ai_perms(
            int(identity.get("user_id") or 0), 0) or {}
    except Exception as e:  # noqa: BLE001 — 判不了可执行性就说不清，不许猜
        logger.error("[system_capability_plan] 角色权限读取失败: %s", e)
        return _text({"error": "读取角色权限失败，无法判定每步能否执行（不猜）"}, is_error=True)

    header = {"key": scn.get("key"), "title": scn.get("title"),
              "goal": scn.get("goal"), "actors": scn.get("actors") or []}
    flow = [x for x in (scn.get("flow") or []) if isinstance(x, dict)]
    if not flow:
        return _text({
            "scenario": header,
            "executable": False,
            "rules": [{"key": rk, "statement": (rules.get(str(rk)) or {}).get("statement", "")}
                      for rk in (scn.get("uses_rules") or [])],
            "note": "这是问答型场景：本体里没有任务步骤（flow）。按 uses_objects / uses_rules 的口径"
                    "回答即可，不得自行编造步骤顺序。",
        })

    def _order(st) -> int:
        try:
            return int(st.get("order"))
        except (TypeError, ValueError):
            return 99

    steps = []
    for st in sorted(flow, key=_order):
        action_key = str(st.get("action") or "")
        act = actions.get(action_key) or {}
        code = str(st.get("perm_code") or "")
        channel = str(st.get("channel") or "")
        read_only = bool(act.get("read_only"))
        can, why = _step_runnable(channel, code, read_only, role_perms)
        steps.append({
            "order": st.get("order"),
            "action": action_key,
            "label": st.get("action_label") or act.get("label") or action_key,
            "purpose": st.get("purpose") or "",
            "channel": channel,
            "perm_code": code,
            "read_only": read_only,
            "consumes": st.get("consumes") or "",
            "produces": st.get("produces") or "",
            "rule_gates": [{"key": str(rk),
                            "statement": (rules.get(str(rk)) or {}).get("statement", "")}
                           for rk in (st.get("gated_by") or [])],
            "you_can_run": can,
            "why_not": why,
        })

    return _text({
        "scenario": header,
        "executable": True,
        "step_count": len(steps),
        "steps": steps,
        "rules": [{"key": rk, "statement": (rules.get(str(rk)) or {}).get("statement", "")}
                  for rk in (scn.get("uses_rules") or [])],
        "note": "按 order 逐步执行：channel=ai 的步骤可直接调对应功能工具；channel=human 的步骤"
                "必须交给人到界面操作。每步权限仍由权限码在调用时裁决，本计划不替代裁决；"
                "you_can_run=false 的步骤不得声称已完成。",
    })


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
            "系统本体与业务本体模型数、启用知识库数、待变更数据产品契约数、待处理别名建议数、"
            "近24h失败任务数与运行中任务数。用于回答“系统现在什么状况/有多少待办/建模进度”等运维问题。"
        ),
        "schema": {},
        "handler": system_overview,
    },
    {
        "name": "system_capability_plan",
        "description": (
            "查询【本系统】某项平台任务的执行计划（系统能力本体 scenarios[].flow，仅管理员，只读）："
            "按序返回步骤（每步动作/用途/权限码/规则门/产出与依赖），并标注 channel："
            "channel=ai 的步骤可直接调对应功能工具，channel=human 的步骤必须交给人到界面操作。"
            "回答“怎么发布一篇公告/发布看板要几步/推进口径认证该先做什么/契约变更流程”这类"
            "**多步任务**问题时，先取计划再逐步执行，不要凭印象编顺序；"
            "you_can_run=false 的步骤不得声称已完成。"
        ),
        "schema": {
            "scenario": Annotated[Optional[str],
                                  "场景业务名、别名或 key（如「内容发布」「看板发布」「口径认证」「契约变更」）；"
                                  "拿不准先不传，会把全部候选（key/标题/是否可执行）列回来"],
        },
        "handler": system_capability_plan,
    },
]

READONLY_ANNOTATIONS = {"readOnlyHint": True}


def build_system_server(backend: str = "qoder", tool_names=None):
    """构建 system 进程内 MCP server（qoder / claude）。tool_names 给定时按 AS-BOT 逐工具粒度注册。"""
    from backend.modules.mind.execution.sdk_tools.compat import make_server, make_tool

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
