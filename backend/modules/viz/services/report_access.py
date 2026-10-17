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


def is_content_report(report: dict) -> bool:
    """是否内容型报表。``source_type`` 缺失按数据派生处理（沿用快照口径，不放宽）。"""
    return (report.get("source_type") or "") in CONTENT_SOURCES


def is_editable_report(report: dict) -> bool:
    """正文可否在线编辑（人写/会话保存可以，任务产出的历史报表只读）。"""
    return (report.get("source_type") or "") in EDITABLE_SOURCES


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
                and bool(str(report.get("content") or "").strip()) and _owner_matches(report, user))
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


def public_report(report: dict, *, content: bool = True) -> dict:
    fields = ("id", "task_id", "log_id", "title", "format", "access_mode", "view_count",
              "created_at", "updated_at", "generation_status", "publication_status",
              "run_key", "task_name", "source_type", "workspace_id", "origin")
    result = {key: report.get(key) for key in fields}
    if result.get("origin") is None:
        # 溯源只暴露可读信息（来自哪个会话/哪条消息），不外露内部存储标识
        result["origin"] = json_object(report.get("analysis_source")).get("origin") or {}
    if content:
        result["content"] = report.get("content") or ""
        result["evidence_summary"] = json_object(report.get("evidence_summary"))
    for key, value in list(result.items()):
        if isinstance(value, datetime):
            result[key] = value.isoformat()
    return result
