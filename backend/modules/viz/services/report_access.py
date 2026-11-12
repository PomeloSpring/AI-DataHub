"""报告快照的统一访问控制；公开/外链发布不等于绕过敏感数据策略。"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from backend.common.auth import authorize_resource_scope, authorize_workspace, get_user_by_id

# ── 报表来源分型（唯一口径，改动须同步 report_center_management_migration.sql）──
# 数据派生：正文由受治理取数产出，``security_context`` 是它的授权证据 ——
#   快照失效即不可读（防历史重放），也不允许在线改正文。
DATA_SOURCES = ("scheduled", "analysis")
# 内容型：正文是人写的、或会话里已经展示给属主本人的结论（含 M2 之前的存量 legacy）。
#   没有可校验的取数快照，因此只开放「属主在应用内实时查看」，一律不参与外链/公开分享。
CONTENT_SOURCES = ("manual", "conversation", "legacy")
# 内容型可见的状态：ready/degraded 之外，存量历史行状态是 'legacy'（迁移前的报告）。
_CONTENT_VIEWABLE = ("ready", "degraded", "legacy")
# 可在线编辑正文的来源：只有人写与会话保存。存量 legacy 虽属内容型（无快照可校），
# 但正文当初由任务取数产出 —— 让人改它就等于篡改取数结果，因此只读（可删除清理）。
EDITABLE_SOURCES = ("manual", "conversation")
# 可在报表中心删除的来源：内容型（含 legacy 存量清理）+ 分析生成（提交式分析、
# 「用最新数据重新生成」追加的版本 —— 它们不绑任何执行记录，删了就是删这份报表本身）。
# 唯一定时任务产出（scheduled）不在列：它与执行日志一一对应，删除入口在任务调度，
# 在报表中心删会割裂「执行记录 ↔ 报表」的对应关系。
DELETABLE_SOURCES = ("manual", "conversation", "legacy", "analysis")


def json_object(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def policy_snapshot(owner_id: int, workspace_id: int, sources: list[dict]) -> dict:
    from backend.core.enforcer import permission_enforcer
    live = get_user_by_id(owner_id) if owner_id else None
    if not live or live.get("status") != "active":
        raise PermissionError("报告创建者身份无效")
    user = {"user_id": owner_id, "role": live.get("user_role") or "viewer"}
    authorize_workspace(user, workspace_id)
    policies = []
    restricted = False
    for source in sources:
        ds_id = int(source["datasource_id"])
        table = permission_enforcer._policy_table(source["table"], ds_id)
        access = permission_enforcer.check_access(owner_id, workspace_id, ds_id, table)
        if not access.allowed:
            raise PermissionError("报告来源不再允许访问")
        policies.append({"source": source, "filter": access.row_filter,
                         "hidden": sorted(access.hidden_columns), "masked": access.masked_columns})
        restricted = restricted or bool(access.row_filter or access.hidden_columns or access.masked_columns)
    canonical = {"owner": owner_id, "workspace": workspace_id, "role": user["role"], "policies": policies}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return {"version": 1, "sources": sources, "policy_digest": digest,
            "restricted": restricted, "share_eligible": False}


def dataset_snapshot(dataset_id: int, identity: dict, dataset: dict = None) -> str:
    """数据集配置与当前主体行范围也属于报告授权快照，收紧后禁止历史重放。

    可见性功能已退役: 访问控制由取数治理入口(governed_execute/七闸门)与
    数据源授权(_authorize_source)承担, 此处只锁配置/行范围快照。
    """
    from backend.modules.viz.services import dataset_service
    dataset = dataset if dataset is not None else dataset_service.get_dataset(dataset_id)
    if not dataset or dataset.get("status") != "active":
        raise PermissionError("数据集不可用")
    scopes = dataset_service._scope_filters(dataset_id, identity)
    return hashlib.sha256(json.dumps({"dataset": dataset, "scopes": scopes}, sort_keys=True,
                                    ensure_ascii=False, default=str).encode()).hexdigest()


def snapshot_is_current(report: dict) -> bool:
    security = json_object(report.get("security_context"))
    if security.get("version") != 1 or not security.get("policy_digest"):
        return False
    try:
        if security.get("dataset_id"):
            from backend.common.auth import resolve_execution_owner
            identity = resolve_execution_owner(report["owner_id"], report["workspace_id"])
            digest = dataset_snapshot(security["dataset_id"], identity)
            if not hmac.compare_digest(digest, security.get("dataset_digest") or ""):
                return False
        if report.get("log_id"):
            from backend.common.db import execute_query
            log = execute_query("SELECT status FROM adh_scheduled_logs WHERE id=%s",
                                (report["log_id"],), fetchone=True)
            if not log or log["status"] not in ("success", "partial"):
                return False
        current = policy_snapshot(int(report.get("owner_id") or 0),
                                  int(report.get("workspace_id") or 0), security.get("sources") or [])
        return hmac.compare_digest(current["policy_digest"], security["policy_digest"])
    except Exception:
        return False


def _unexpired(value) -> bool:
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def has_body(report: dict) -> bool:
    """报表是否已有正文 —— 唯一判据是对象 key 非空。

    ``adh_reports.content`` 列已退役（恒空串），**不得**再拿它判断正文存在与否：
    那会在迁移前后给出两种答案。key 为空而 content 非空 = 存量还没搬，
    由 ``report_service.content_access`` 显式提示跑迁移脚本，不在这里回退读库正文。
    """
    return bool(str((report or {}).get("content_object_key") or "").strip())


def body_pending_migration(report: dict) -> bool:
    """key 空而库内 content 非空 —— 存量正文还没搬到对象存储。

    这是一个**必须对用户可见**的部署状态：不搬完，报表中心点开就是"正文为空"，
    而真正的原因（迁移脚本没跑）会被那句笼统提示吃掉。
    """
    return not has_body(report) and bool(str((report or {}).get("content") or "").strip())


def is_content_report(report: dict) -> bool:
    """是否内容型报表。``source_type`` 缺失按数据派生处理（沿用快照口径，不放宽）。"""
    return (report.get("source_type") or "") in CONTENT_SOURCES


def is_editable_report(report: dict) -> bool:
    """正文可否在线编辑（人写/会话保存可以，任务产出的历史报表只读）。"""
    return (report.get("source_type") or "") in EDITABLE_SOURCES


def deletable(report: dict) -> bool:
    """能否在报表中心删除（属主自检后调用）。

    口径与前端 isDeletableReport 同源：内容型与分析生成可删，唯定时任务产出留给任务调度。
    缺 source_type 的存量行按数据派生处理（沿用快照口径，不放宽到可删）。
    """
    return (report.get("source_type") or "") in DELETABLE_SOURCES


def series_key(report: dict) -> str:
    """报表归集键（版本分组的**唯一口径**，服务端一处算，前端只消费）。

    * 任务产出 → 按任务归集（每次执行自然成为一个历史版本）；
    * 「用最新数据重新生成」的产物 → 继承来源报表的键：新版本落在**原报表卡片**里，
      不再另开一张卡（历史缺陷：会话保存的报表重跑后变成两张同名卡片，
      用户在新卡片里只看到失败的空正文，以为历史版本丢了）；
    * 内容型（人写/会话保存）→ 一条即一份报表（编辑就地更新，不按标题聚合）；
    * 其余数据派生（提交式分析报表、迁移前 legacy）→ 按标题归集。
    """
    if report.get("task_id"):
        return f"task:{int(report['task_id'])}"
    inherited = str(json_object(report.get("analysis_source")).get("refresh_series") or "").strip()
    if inherited:
        return inherited
    report_id = int(report.get("id") or 0)
    if (report.get("source_type") or "") in EDITABLE_SOURCES and report_id:
        return f"report:{report_id}"
    return f"title:{report.get('title') or ''}"


def _owner_matches(report: dict, user: dict = None) -> bool:
    """属主 + 工作空间授权（身份只信服务端，异常一律拒）。"""
    if not user or not user.get("user_id"):
        return False
    if int(report.get("owner_id") or 0) != int(user["user_id"]):
        return False
    try:
        authorize_workspace(user, report.get("workspace_id") or 0)
        return True
    except Exception:
        return False


def can_access_report(report: dict, user: dict = None, token: str = None) -> bool:
    if is_content_report(report):
        # 内容型：仅属主应用内查看；分享/公开分支根本不参与（无快照=无发布依据）。
        return (report.get("generation_status") in _CONTENT_VIEWABLE
                and has_body(report) and _owner_matches(report, user))
    if report.get("generation_status") not in ("ready", "degraded") or not snapshot_is_current(report):
        return False
    if _owner_matches(report, user):
        return True
    security = json_object(report.get("security_context"))
    if (report.get("generation_status") != "ready" or report.get("publication_status") != "published"
            or not security.get("share_eligible") or security.get("restricted")
            or report.get("share_revoked") or not _unexpired(report.get("share_expires_at"))):
        return False
    if report.get("access_mode") == "public":
        return True
    digest = report.get("share_token_hash") or ""
    return bool(token and digest and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), digest))


#: 溯源对外只留可读信息；可复跑配置（AS-BOT / 数据源）属服务端内部标识，不下发（护栏 §7）。
#: `question_note` 不是新事实而是“这句不是本轮原话”的来源说明：复用问题弹窗要能如实展示它。
_ORIGIN_PUBLIC_KEYS = ("channel", "conversation_id", "conversation_title", "message_uuid",
                       "question", "question_note")


def refreshability(report: dict) -> dict:
    """报表能否「按最新数据重新生成」+ 按钮该说什么 + 不可行时的可读原因。

    判断口径只在这一处（前端不得再拼一套），且只读服务端存的可复跑事实。
    `available` 的语义 = **从这个页面点下去有没有能生效的动作**，不是"能不能重跑"的字面判断：
    任务产出的报表走"触发所属任务"（新版本自然归集为历史版本），那也是能生效的动作，
    所以 available=True + channel='task' + 自己的 action_label。
    （历史缺陷：这里回 available=False，前端先 `if (!available) toast.error(hint); return`
    就退出了，下面"触发所属任务"的分支永远走不到——用户在定时任务报表上点重跑只得到一句报错。）
    """
    if report.get("task_id"):
        return {"available": True, "channel": "task", "action_label": "触发所属任务重新生成",
                "hint": "本报告由定时任务产出：点一下触发所属任务跑一轮，新版本自动归集为历史版本"
                        "（不在报告侧重开一条取数链路，口径与任务调度一致）"}
    origin = json_object(report.get("analysis_source")).get("origin") or {}
    if not str(origin.get("question") or "").strip():
        return {"available": False, "channel": "", "action_label": "",
                "hint": "该报告未登记可复跑的原始问题（手写或改造前的历史报告），无法按最新数据重新生成"}
    # 不再要求报表登记 AS-BOT 与数据源：重跑身份与可查范围按报表属主的角色实时继承
    # （与会话存报表时选的源/AS-BOT 无关，授权收回后重跑会被服务端拒）。
    return {"available": True, "channel": "report", "action_label": "用最新数据重新生成",
            "hint": "按报告登记的原始问题重新取数（按你的角色权限执行），沿用本报告当前的版式追加一个历史版本"}


def layout_status(report: dict) -> dict:
    """「用最新数据重新生成」的版本有没有沿用原报表版式（回读投影，判断与原因在生成侧写）。

    回显而不沉默：新版长得跟原版不一样时，用户必须能看到“为什么没沿用”（上一版正文读不到 /
    模型未交稿 / 缺章节缺图表），而不是自己猜“报表中心把报表改坏了”。非重跑产物的报表回空状态。
    """
    source = json_object(report.get("analysis_source"))
    result = source.get("layout_result")
    if not isinstance(result, dict):
        return {"has_layout": bool(source.get("layout")), "status": "", "format": "", "reasons": []}
    return {"has_layout": True, "status": str(result.get("status") or ""),
            "format": str(result.get("format") or ""),
            "reasons": [str(r) for r in (result.get("reasons") or []) if str(r).strip()][:5]}


#: 对外投影字段白名单。``content_object_key`` **不在**其中：对象 key 属内部存储标识
#: （同 ``adh_content_files.object_key`` 的口径），客户端只拿读取时现拼的短期签名直链。
def public_report(report: dict, *, content: bool = True) -> dict:
    """报表对外投影。``content=True`` = 详情面（带证据摘要）。

    正文本身**不在**投影里：它是对象存储上的对象，直链由调用方
    （``report_service.get_report`` → ``content_access``）按 key 现拼后附加，
    纯投影函数不做网络 I/O。
    """
    fields = ("id", "task_id", "log_id", "title", "format", "access_mode", "view_count",
              "created_at", "updated_at", "generation_status", "publication_status",
              "run_key", "task_name", "source_type", "workspace_id", "origin", "content_size")
    result = {key: report.get(key) for key in fields}
    if result.get("origin") is None:
        # 溯源只暴露可读信息（来自哪个会话/哪条消息），不外露内部存储标识
        result["origin"] = json_object(report.get("analysis_source")).get("origin") or {}
    result["origin"] = {k: v for k, v in (result["origin"] or {}).items() if k in _ORIGIN_PUBLIC_KEYS}
    result["refresh"] = refreshability(report)
    # 归集键随响应下发：前端不得再拼第二套“哪些报表算同一份的历史版本”判断
    result["series_key"] = series_key(report)
    # 版式沿用结果（重跑版本专用）：前端在版本清单与详情头部回显，不自己拼判断
    layout = layout_status(report)
    if layout["status"]:
        result["layout"] = layout
    if content:
        result["evidence_summary"] = json_object(report.get("evidence_summary"))
    for key, value in list(result.items()):
        if isinstance(value, datetime):
            result[key] = value.isoformat()
    return result
