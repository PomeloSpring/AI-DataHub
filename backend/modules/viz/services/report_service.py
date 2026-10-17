"""Report Service — Report generation and retrieval.

Handles LLM-powered report generation from templates and query data.
Tables: adh_reports, adh_report_templates, adh_saved_queries
"""

import json
import logging
import re
import secrets
import time
from typing import Optional

from backend.common.db import DBConnection
from backend.core.governed_query import governed_execute
from backend.common.auth import authorize_resource_scope, authorize_workspace, resolve_execution_owner
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.modules.viz.services import report_access


class ReportSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: int = Field(ge=0)
    title: str = "分析报告"
    template_key: str = ""
    dataset_id: Optional[int] = Field(None, gt=0)
    dataset_query: dict = Field(default_factory=dict)
    intent: Optional[dict] = None
    saved_query_id: Optional[int] = Field(None, gt=0)

    @model_validator(mode="after")
    def validate_source(self):
        if sum(v is not None for v in (self.dataset_id, self.intent, self.saved_query_id)) != 1:
            raise ValueError("必须指定且只能指定一种分析来源")
        if self.intent is not None:
            from backend.semantics.intent import parse_intent
            if any(k in self.intent for k in ("datasource_id", "user_id", "workspace_id")):
                raise ValueError("分析意图不能指定基础设施身份")
            query, error, _ = parse_intent(self.intent)
            if error or query.dry_run:
                raise ValueError("报告需要合法的可执行语义意图")
        return self
from backend.modules.viz.services.report_access import (
    can_access_report, public_report, json_object, is_content_report)

logger = logging.getLogger(__name__)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _generate_id() -> int:
    return int(time.time() * 1000000)


def _normalize_report(row: dict) -> dict:
    """Normalize report row for JSON serialization."""
    for ts in ("created_at",):
        if hasattr(row.get(ts), "isoformat"):
            row[ts] = row[ts].isoformat()
    return row


# ── Report CRUD ─────────────────────────────────────────────────────────────


def list_reports(user_id: int, workspace_id: int = 0, page: int = 1, size: int = 20,
                 user: dict = None) -> dict:
    """报表清单：报表跟随属主（与资产清单同域），默认跨工作站；workspace_id>0 才收窄。

    可见性 = 仅创建者：查询硬绑 owner_id（fail-closed，缺可信身份直接拒），
    因此不再叠加 authorize_workspace —— 存量报表的 workspace_id 可能是 0（任务未归属），
    拿工作空间当过滤条件只会让人看不到自己的报表。
    """
    if not user or int(user.get("user_id") or 0) != int(user_id):
        raise PermissionError("报告列表缺少可信身份")
    with DBConnection() as conn:
        with conn.cursor() as cur:
            conditions = ["r.owner_id = %s"]
            params: list = [user_id]
            if int(workspace_id or 0) > 0:
                conditions.append("r.workspace_id = %s")
                params.append(int(workspace_id))
            where = "WHERE " + " AND ".join(conditions)

            cur.execute(f"SELECT COUNT(*) AS total FROM adh_reports r {where}", params)
            total = cur.fetchone()["total"]

            offset = (page - 1) * size
            # 任务名 JOIN：列表展示 name 而非裸 id（ui-resource-display）
            cur.execute(
                f"SELECT r.*, t.name AS task_name FROM adh_reports r "
                f"LEFT JOIN adh_scheduled_tasks t ON t.id = r.task_id "
                f"{where} ORDER BY r.created_at DESC LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            rows = cur.fetchall()
            return {"items": [public_report(r, content=False) for r in rows], "total": total}


def load_report(report_id: int) -> Optional[dict]:
    """仅供服务内部读取原始记录，不可作为对外响应。"""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM adh_reports WHERE id = %s", (report_id,))
            return cur.fetchone()


def get_report(report_id: int, access_token: str = None, user: dict = None) -> Optional[dict]:
    row = load_report(report_id)
    if not row:
        return None
    if not can_access_report(row, user, access_token):
        if user and int(row.get("owner_id") or 0) == int(user.get("user_id") or 0):
            result = public_report(row, content=False)
            result["content"] = ""
            result["access_warning"] = (
                "报表正文为空，请重新保存" if is_content_report(row)
                else "报告未就绪或权限已变化，请重新生成")
            return result
        return None
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE adh_reports SET view_count = view_count + 1 WHERE id = %s", (report_id,))
    return public_report(row)


def create_report(*, task_id: int = None, log_id: int = None, title: str,
                  content: str, format: str = "markdown", workspace_id: int,
                  owner_id: int, security_context: dict = None, run_key: str = None,
                  generation_status: str = "degraded", evidence_summary: dict = None,
                  analysis_source: dict = None, source_type: str = "scheduled") -> dict:
    """数据派生报表的唯一持久化入口；未认证的旧结果只能保存为不可分享的降级报告。

    内容型报表（人写/会话保存）走 ``create_content_report``，两者共用一张表但
    可见性口径不同（见 report_access）。
    """
    if int(owner_id or 0) <= 0:
        raise PermissionError("报告缺少创建者")
    report_id = _generate_id()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_reports (id, task_id, log_id, title, content, format, access_mode, "
                "workspace_id, owner_id, created_at, updated_at, generation_status, publication_status, "
                "security_context, run_key, evidence_summary, analysis_source, source_type, lease_expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,'private',%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,"
                "DATE_ADD(NOW(), INTERVAL 10 MINUTE)) ON DUPLICATE KEY UPDATE id=id",
                (report_id, task_id, log_id, title, content, format, workspace_id, owner_id, _now(),
                 _now(), generation_status, json.dumps(security_context or {}, ensure_ascii=False),
                 run_key, json.dumps(evidence_summary or {}, ensure_ascii=False, default=str),
                 json.dumps(analysis_source or {}, ensure_ascii=False, default=str), source_type),
            )
            if run_key:
                cur.execute("SELECT id, owner_id, workspace_id, generation_status FROM adh_reports WHERE run_key=%s", (run_key,))
                existing = cur.fetchone()
                if not existing or existing["owner_id"] != owner_id or existing["workspace_id"] != workspace_id:
                    raise PermissionError("报告运行键冲突")
                report_id, generation_status = existing["id"], existing["generation_status"]
    return {"id": report_id, "run_id": run_key, "access_mode": "private", "generation_status": generation_status}


# ── 内容型报表（人写 / 会话保存）：报表中心的「管理」面 ────────────────────────

CONTENT_FORMATS = ("markdown", "html")
_MAX_TITLE_CHARS = 200
_MAX_CONTENT_CHARS = 200_000


def create_content_report(*, title: str, content: str, format: str = "markdown",
                          workspace_id: int, owner_id: int, source_type: str = "manual",
                          origin: dict = None) -> dict:
    """保存一份内容型报表：正文由人写或取自会话已展示给属主本人的内容。

    不取数、不过数据源，因此不产生权限快照；相应地只能属主私有查看，
    不参与外链/公开分享（``publication_status`` 固定 draft，见 report_access）。
    """
    if int(owner_id or 0) <= 0:
        raise PermissionError("报表缺少创建者")
    if source_type not in report_access.EDITABLE_SOURCES:
        raise ValueError("内容型报表来源只能是 manual 或 conversation")
    title = str(title or "").strip()
    if not title:
        raise ValueError("报表标题不能为空")
    if format not in CONTENT_FORMATS:
        raise ValueError(f"报表格式只能是 {'/'.join(CONTENT_FORMATS)}")
    content = str(content or "")
    if not content.strip():
        raise ValueError("报表正文不能为空")
    report_id = _generate_id()
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_reports (id, task_id, log_id, title, content, format, access_mode, "
                "workspace_id, owner_id, created_at, updated_at, generation_status, publication_status, "
                "security_context, evidence_summary, analysis_source, source_type) "
                "VALUES (%s,NULL,NULL,%s,%s,%s,'private',%s,%s,%s,%s,'ready','draft',NULL,NULL,%s,%s)"
                " ON DUPLICATE KEY UPDATE id=id",
                (report_id, title[:_MAX_TITLE_CHARS], content[:_MAX_CONTENT_CHARS], format,
                 int(workspace_id or 0), int(owner_id), _now(), _now(),
                 json.dumps({"origin": origin or {}}, ensure_ascii=False, default=str), source_type),
            )
    return {"report_id": report_id, "title": title[:_MAX_TITLE_CHARS], "format": format,
            "source_type": source_type, "generation_status": "ready"}


def _load_owned_report(report_id: int, user: dict) -> dict:
    """属主自检取行（fail-closed）：无可信身份/非属主一律 PermissionError。"""
    if not user or int(user.get("user_id") or 0) <= 0:
        raise PermissionError("缺少可信身份")
    row = load_report(report_id)
    if not row:
        raise ValueError("报表不存在或已删除")
    if int(row.get("owner_id") or 0) != int(user["user_id"]):
        raise PermissionError("只能管理自己创建的报表")
    return row


def update_content_report(report_id: int, user: dict, *, title: str = None,
                          content: str = None, format: str = None) -> dict:
    """重命名 / 编辑内容型报表正文。数据派生报表正文不可手改（证据链唯一）。"""
    row = _load_owned_report(report_id, user)
    if not report_access.is_editable_report(row):
        if report_access.is_content_report(row):
            raise ValueError("这是迁移前由定时任务产出的历史报表，只读可查不可改；"
                             "如需一份可编辑的报表，请新建一份或从会话另存")
        raise ValueError("定时任务/分析来源产出的报表正文由受治理取数生成，不可在线编辑；"
                         "如需修改请重新生成，或在会话里另存一份报表")
    sets, params = ["updated_at=%s"], [_now()]
    if title is not None:
        title = str(title).strip()
        if not title:
            raise ValueError("报表标题不能为空")
        sets.append("title=%s")
        params.append(title[:_MAX_TITLE_CHARS])
    if content is not None:
        if not str(content).strip():
            raise ValueError("报表正文不能为空")
        sets.append("content=%s")
        params.append(str(content)[:_MAX_CONTENT_CHARS])
        sets.append("generation_status=%s")
        params.append("ready")
    if format is not None:
        if format not in CONTENT_FORMATS:
            raise ValueError(f"报表格式只能是 {'/'.join(CONTENT_FORMATS)}")
        sets.append("format=%s")
        params.append(format)
    from backend.common.db import execute_write
    execute_write(f"UPDATE adh_reports SET {','.join(sets)} WHERE id=%s AND owner_id=%s",
                  tuple(params) + (report_id, int(user["user_id"])))
    return {"report_id": report_id, "updated": True}


def delete_report(report_id: int, user: dict) -> dict:
    """删除报表：仅属主、仅内容型（含历史报表清理）；任务产出的数据派生报表跟执行记录绑定。"""
    row = _load_owned_report(report_id, user)
    if not report_access.is_content_report(row):
        raise ValueError("定时任务/分析来源产出的报表与其执行记录一一对应，"
                         "请在任务调度的执行记录中管理")
    from backend.common.db import execute_write
    if execute_write("DELETE FROM adh_reports WHERE id=%s AND owner_id=%s",
                     (report_id, int(user["user_id"]))) != 1:
        raise ValueError("报表删除失败（记录已变化），请刷新后重试")
    return {"report_id": report_id, "deleted": True}


def _md_cell(value) -> str:
    import html
    text = html.escape(str(value if value is not None else "")).replace("\n", " ")
    for marker in (chr(92), "`", "*", "_", "[", "]", "(", ")", "!", "|"):
        text = text.replace(marker, chr(92) + marker)
    return text


# ── 哪些回合算「报表」：单一口径，同时约束前端按钮展示与保存校验 ──────────────
# 报表 = 本轮真的产出过数据/分析结果。纯问答、能力介绍、系统操作确认（如“已记住…”）
# 不是报表，不给归档入口（否则报表中心会堆满无意义条目）。
# 取数/分析类工具清单与 sdk_tools 注册表对齐（能回数据行的那几条）。
DATA_BEARING_TOOLS = ("execute_sql", "query_execute", "run_semantic_query",
                      "query_dataset", "run_quality_check", "generate_report")
_CHART_BLOCK = re.compile(r"```chart\b")


def turn_reportability(turn: dict) -> tuple[bool, str]:
    """判定一个助手回合是否可存为报表，返回 ``(可存, 可读原因)``。

    入参是 done 事件/已落库消息的形状（reply/content/result/sql/chart_type/tool_calls）。
    """
    if not isinstance(turn, dict):
        return False, "回合记录无效"
    if turn.get("error"):
        return False, "该回合执行未完成，没有可归档的结果"
    result = turn.get("result") or {}
    if isinstance(result, dict) and (result.get("columns") or []) and (result.get("rows") or []):
        return True, "含查询结果表"
    if turn.get("sql"):
        return True, "含已执行的查询"
    if turn.get("chart_type"):
        return True, "含图表产出"
    body = "\n".join(str(turn.get(k) or "") for k in ("content", "reply", "analysis"))
    if _CHART_BLOCK.search(body):
        return True, "含图表产出"
    for call in turn.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        name = str(call.get("tool") or "").rsplit("__", 1)[-1]
        if name in DATA_BEARING_TOOLS:
            return True, "本轮已完成取数/分析"
    return False, "该回合是问答或系统操作说明，不是数据分析结果；如需归档可在报表中心「新建报表」"


def with_reportability(payload: dict) -> dict:
    """给 done 事件/已落库消息补 ``reportable`` + ``reportable_reason``（返回副本）。

    前端只消费这个字段决定「存为报表」按钮是否出现，不得自己再写一套判断。
    """
    ok, reason = turn_reportability(payload)
    out = dict(payload)
    out["reportable"] = ok
    out["reportable_reason"] = reason
    return out


def render_conversation_report(*, question: str, answer: str, result: dict = None) -> str:
    """把会话里一轮已展示给用户的内容拼成报表正文（Markdown）。

    只拼用户已经看到的文字与结果表（最多 200 行），不回捞原始 SQL / 物理标识。
    """
    lines = []
    if question:
        lines += [f"> 提问：{_md_cell(question)}", ""]
    lines.append((answer or "").strip())
    columns = (result or {}).get("columns") or []
    rows = (result or {}).get("rows") or []
    keys = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]
    if keys and rows:
        lines += ["", "## 查询结果", "",
                  "| " + " | ".join(_md_cell(k) for k in keys) + " |",
                  "| " + " | ".join("---" for _ in keys) + " |"]
        for row in rows[:200]:
            values = [row.get(k) for k in keys] if isinstance(row, dict) else row
            lines.append("| " + " | ".join(_md_cell(v) for v in values) + " |")
        if len(rows) > 200:
            lines += ["", f"> 仅展示前 200 行（共 {len(rows)} 行）。"]
    return "\n".join(lines).strip()


def save_conversation_report(*, user: dict, conversation_id: int, title: str = "",
                             message_index: int = None, message_uuid: str = "") -> dict:
    """把智能问数会话里的一轮回答存为报表（内容只从服务端已落库的会话读）。

    正文不信客户端传入：按 ``conversation_id`` + 属主取 ``adh_conversations.messages``，
    再按 message_uuid（优先）/ 下标定位那一条助手回答。
    """
    from backend.common.db import execute_query
    from backend.modules.mind.execution import outbound_guard
    if int(conversation_id or 0) <= 0:
        raise ValueError("缺少会话标识，无法保存报表")
    conv = execute_query(
        "SELECT id, workspace_id, title, messages FROM adh_conversations "
        "WHERE id=%s AND user_id=%s", (conversation_id, int(user["user_id"])), fetchone=True)
    if not conv:
        raise PermissionError("会话不存在或不属于当前用户")
    messages = conv.get("messages")
    if isinstance(messages, str):
        messages = json.loads(messages or "[]")
    messages = messages if isinstance(messages, list) else []
    answer = None
    if message_uuid:
        answer = next((m for m in messages
                       if isinstance(m, dict) and m.get("role") == "assistant"
                       and str(m.get("message_uuid") or "") == str(message_uuid)), None)
    if answer is None and message_index is not None:
        try:
            candidate = messages[int(message_index)]
        except (ValueError, TypeError, IndexError):
            candidate = None
        if isinstance(candidate, dict) and candidate.get("role") == "assistant":
            answer = candidate
    if answer is None:
        answer = next((m for m in reversed(messages)
                       if isinstance(m, dict) and m.get("role") == "assistant"), None)
    if answer is None:
        raise ValueError("会话记录尚未保存或没有可存的分析结果，请完成本轮对话后重试")
    reportable, reason = turn_reportability(answer)
    if not reportable:
        # 与前端按钮展示同一口径：不展示的东西也不允许绕过前端存进来（原因可解释）
        raise ValueError(reason)
    question = str(answer.get("question") or "").strip()
    answer_text = str(answer.get("content") or answer.get("reply") or "").strip()
    if not answer_text:
        raise ValueError("该回合没有可保存的正文（可能执行失败或被中断）")
    content = render_conversation_report(question=question, answer=answer_text,
                                        result=answer.get("result") if isinstance(answer.get("result"), dict) else None)
    hits = outbound_guard.find_sensitive_hits(content)
    if hits:
        raise ValueError("该回答包含不应归档的基础设施信息（命中: "
                         + ", ".join(hits) + "），不能存为报表；请在对话中重新表述业务结论")
    report_title = str(title or "").strip() or (question[:60] or str(conv.get("title") or "")) or "会话分析报表"
    result = create_content_report(
        title=report_title, content=content, format="markdown",
        workspace_id=int(conv.get("workspace_id") or 0), owner_id=int(user["user_id"]),
        source_type="conversation",
        origin={"channel": "chat", "conversation_id": int(conv["id"]),
                "conversation_title": conv.get("title") or "",
                "message_uuid": str(message_uuid or answer.get("message_uuid") or "")})
    result["workspace_id"] = int(conv.get("workspace_id") or 0)
    return result


def submit_report(request: dict, user: dict, background_tasks) -> dict:
    """先保存可追踪的运行，再真实派发；禁止无来源占位报告。"""
    from uuid import uuid4
    from fastapi import HTTPException
    req = ReportSubmission.model_validate(request)
    authorize_workspace(user, req.workspace_id)
    run_id = uuid4().hex
    report = create_report(title=req.title, content="", workspace_id=req.workspace_id,
        owner_id=user["user_id"], run_key=run_id, generation_status="queued",
        analysis_source=req.model_dump(), source_type="analysis")
    try:
        dispatch_report(report["id"], run_id, background_tasks)
    except Exception:
        logger.exception("报告派发失败")
        finish_report(report["id"], generation_status="failed", stage_error_code="DISPATCH_FAILED")
        raise HTTPException(status_code=503, detail="报告队列暂不可用，派发未完成") from None
    return {"report_id": report["id"], "run_id": run_id, "generation_status": "queued"}


def dispatch_report(report_id, run_id, background_tasks):
    import os
    mode = os.getenv("ADH_TASK_EXECUTION_MODE", "celery")
    if mode == "background":
        background_tasks.add_task(run_report, report_id)
    elif mode == "celery":
        from backend.core.task_runtime import send_task
        send_task("flow.generate_report", args=(report_id,), task_id=run_id, queue="scheduled")
    else:
        raise ValueError("不支持的执行适配器")


def claim_report(report_id):
    from backend.common.db import execute_write
    return execute_write("UPDATE adh_reports SET generation_status='running', "
        "lease_expires_at=DATE_ADD(NOW(), INTERVAL 10 MINUTE) WHERE id=%s "
        "AND generation_status='queued' AND lease_expires_at>NOW()", (report_id,)) == 1


def finish_report(report_id, **values):
    from backend.common.db import execute_write
    allowed = {"generation_status", "stage_error_code", "content", "security_context", "evidence_summary"}
    # cancelled=任务监控人工停止；与其它终态一样受 `generation_status IN ('queued','running')` 保护，迟到结果不可覆盖。
    if set(values) - allowed or values.get("generation_status") not in ("ready", "degraded", "failed", "timeout", "cancelled"):
        raise ValueError("报告更新字段或终态无效")
    updates = ",".join(f"`{key}`=%s" for key in values)
    params = tuple(json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, dict) else v
                   for v in values.values()) + (report_id,)
    return execute_write(f"UPDATE adh_reports SET {updates}, "
        "generation_status=IF(lease_expires_at<=NOW(),'timeout',generation_status), "
        "content=IF(lease_expires_at<=NOW(),'',content), "
        "stage_error_code=IF(lease_expires_at<=NOW(),'TIMEOUT',stage_error_code) WHERE id=%s "
        "AND generation_status IN ('queued','running')", params) == 1


def run_report(report_id):
    from billiard.exceptions import SoftTimeLimitExceeded
    from backend.core.task_runtime import RunInterrupted
    report = load_report(report_id)
    if not report or not claim_report(report_id):
        return {"report_id": report_id, "duplicate": True}
    try:
        identity = resolve_execution_owner(report["owner_id"], report["workspace_id"])
        from backend.core.task_runtime import guarded_call
        result = guarded_call(execute_report_source, json_object(report["analysis_source"]), identity,
                              deadline=time.monotonic() + 600)
        content = render_fact_report(report["title"], [result])
        finish_report(report_id, generation_status="degraded", content=content,
            security_context=result.get("_security_context") or {},
            evidence_summary={"validation_status": "unverified", "quality_status": "unknown",
                              "completeness": "sample_only"}, stage_error_code="FACT_ONLY")
    except (RunInterrupted, TimeoutError, SoftTimeLimitExceeded):
        finish_report(report_id, generation_status="timeout", stage_error_code="TIMEOUT")
    except Exception:
        logger.exception("报告 %s 生成失败", report_id)
        finish_report(report_id, generation_status="failed", stage_error_code="QUERY_FAILED", content="")
    return {"report_id": report_id}


def cleanup_stale_reports():
    from backend.common.db import execute_write
    return execute_write("UPDATE adh_reports SET generation_status='timeout', stage_error_code='TIMEOUT', "
                         "content='' WHERE generation_status IN ('queued','running') AND lease_expires_at<=NOW()")


def _get_template_content(template_key: str) -> Optional[str]:
    """Look up report template content by name or ID."""
    with DBConnection() as conn:
        with conn.cursor() as cur:
            # Try by name first
            cur.execute(
                "SELECT content FROM adh_report_templates WHERE name = %s LIMIT 1",
                (template_key,),
            )
            row = cur.fetchone()
            if row:
                return row.get("content")

            # Try by ID
            try:
                tid = int(template_key)
                cur.execute("SELECT content FROM adh_report_templates WHERE id = %s", (tid,))
                row = cur.fetchone()
                if row:
                    return row.get("content")
            except (ValueError, TypeError):
                pass

    return None


def _execute_data_query(data_query: str, owner_id: int = 0, workspace_id: int = 0,
                        datasource_id: int = 0) -> dict:
    """Execute a SQL query through the data moat and return the results.

    身份以报表创建者(owner_id)构造 user_context, 经统一治理执行器(敏感 block/mask
    + RLS + 审计), 无可信身份 -> fail-closed 拒绝取数(I5)。不再裸连数据源。

    Supports referencing saved queries by ID (prefix with 'query:') or direct SQL.
    """
    if not data_query:
        return {"columns": [], "rows": [], "row_count": 0}

    sql = data_query

    # If it's a saved query reference
    if data_query.startswith("query:"):
        try:
            query_id = int(data_query.split(":", 1)[1])
            with DBConnection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT sql_query FROM adh_saved_queries WHERE id = %s", (query_id,))
                    row = cur.fetchone()
                    if row:
                        sql = row["sql_query"]
                    else:
                        return {"columns": [], "rows": [], "row_count": 0, "error": "Saved query not found"}
        except (ValueError, IndexError):
            return {"columns": [], "rows": [], "row_count": 0, "error": "Invalid query reference"}

    # Execute the SQL
    sql = sql.strip().rstrip(";")
    if "limit" not in sql.lower():
        sql += " LIMIT 500"

    try:
        return governed_execute(sql, datasource_id, owner_id, workspace_id)
    except Exception as e:
        logger.warning("Data query execution failed: %s", e)
        return {"columns": [], "rows": [], "row_count": 0, "error": "报告数据查询未完成，请检查权限或联系管理员"}


def render_fact_report(title: str, results: list) -> str:
    """M2 只发布私有降级事实表；M4 证据校验就绪前不生成推断性正文。"""
    import html
    def cell(value):
        text = html.escape(str(value)).replace("\n", " ")
        for marker in (chr(92), "`", "*", "_", "[", "]", "(", ")", "!", "|"):
            text = text.replace(marker, chr(92) + marker)
        return text
    if not any(r.get("status") == "success" for r in results):
        raise ValueError("没有成功的分析结果")
    lines = [f"# {cell(title)}", "", "> 降级事实报告：尚未完成证据校验，不可对外发布。",
             "> 仅展示已授权查询返回的样本，不代表总体，不据此推断趋势或占比。", ""]
    for i, result in enumerate(results, 1):
        lines += [f"## 分析项 {i}", ""]
        if result.get("status") != "success":
            lines += ["该分析项未完成。", ""]
            continue
        columns = result.get("columns") or []
        keys = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]
        # 未经业务映射认证的 SQL 列名不能直接向客户端公开。
        labels = [f"字段{n + 1}" for n in range(len(keys))]
        rows = result.get("rows") or []
        if not rows:
            lines += ["本次授权查询未返回数据。", ""]
        elif keys:
            lines += ["| " + " | ".join(labels) + " |", "| " + " | ".join("---" for _ in keys) + " |"]
            for row in rows[:100]:
                values = [row.get(k) for k in keys] if isinstance(row, dict) else row
                lines.append("| " + " | ".join(cell(v) for v in values) + " |")
            lines.append("")
    return "\n".join(lines)


def _authorize_source(identity, datasource_id):
    from backend.core.role_service import role_service
    if int(datasource_id or 0) <= 0:
        raise PermissionError("分析来源未明确绑定")
    # 纯角色裁决: 分析来源可用集=执行身份的角色授权; 空授权 fail-closed。
    allowed = set(role_service.get_user_allowed_datasources(
        identity["user_id"], identity["workspace_id"]))
    if int(datasource_id) not in allowed:
        raise PermissionError("执行身份的角色未授权该分析来源")


def _snapshot(identity, sql, datasource_id, dialect="mysql"):
    from backend.semantics.sql_guard import extract_tables
    sources = [{"datasource_id": datasource_id, "table": t} for t in extract_tables(sql, dialect)]
    return report_access.policy_snapshot(identity["user_id"], identity["workspace_id"], sources)


def _verify_snapshot(identity, security):
    current = report_access.policy_snapshot(identity["user_id"], identity["workspace_id"], security["sources"])
    if current["policy_digest"] != security["policy_digest"]:
        raise PermissionError("执行期间权限发生变化，请重新生成")
    if security.get("dataset_id") and report_access.dataset_snapshot(security["dataset_id"], identity) != security["dataset_digest"]:
        raise PermissionError("执行期间数据集范围发生变化，请重新生成")


def execute_semantic_source(intent, identity, datasource_id=0):
    """复用 intent→binding→plan→七闸门链；内部来源快照不进入 LLM 或客户端。"""
    from backend.semantics.intent import parse_intent
    from backend.semantics.binding_resolver import resolve_binding
    from backend.semantics.planner import plan
    from backend.semantics.gates import execute_semantic
    if not isinstance(intent, dict) or any(k in intent for k in ("user_id", "workspace_id", "datasource_id")):
        raise ValueError("语义意图不能指定执行身份")
    query, error, _ = parse_intent(intent)
    if error or query.dry_run:
        raise ValueError("分析意图无效")
    binding, _ = resolve_binding(query.object, datasource_id=datasource_id)
    if not binding:
        raise ValueError("分析对象尚未绑定或存在歧义")
    _authorize_source(identity, binding.datasource_id)
    query.datasource_id = binding.datasource_id
    query.user_id, query.workspace_id = identity["user_id"], identity["workspace_id"]
    planned = plan(query, binding)
    if not planned.sql or planned.provenance.get("unresolved_terms"):
        raise ValueError("语义分析未通过编译校验")
    security = _snapshot(identity, planned.sql, binding.datasource_id, planned.dialect)
    execution = execute_semantic(query, binding, planned, identity, question="报告分析")
    if not execution.allowed:
        raise PermissionError("分析未通过治理校验或需要人工处理")
    _verify_snapshot(identity, security)
    result = execution.result or {}
    return {"status": "success", **{k: result[k] for k in ("columns", "rows", "row_count", "truncated") if k in result},
            "_security_context": security}


def execute_report_source(source: dict, identity: dict) -> dict:
    from backend.common.db import execute_query
    from backend.semantics.sql_guard import bounded_query
    if source.get("intent") is not None:
        return execute_semantic_source(source["intent"], identity)
    if source.get("dataset_id"):
        from backend.modules.viz.services import dataset_service
        dataset = dataset_service.get_dataset(source["dataset_id"])
        if not dataset or dataset.get("status") != "active":
            raise PermissionError("分析来源不存在或不可用")
        ds_id = int(dataset.get("datasource_id") or 0)
        dataset_digest = report_access.dataset_snapshot(source["dataset_id"], identity, dataset)
        _authorize_source(identity, ds_id)
        # 执行路径按双定义显式选择: 有执行 SQL 走 SQL 快照; 仅语义对象走编译投影快照。
        if not (dataset.get("sql_query") or "").strip() and (dataset.get("object_key") or "").strip():
            # 数据集 scope 必须经数据集服务；编译相同投影只用于捕获全部物理来源。
            from backend.semantics.binding_resolver import resolve_binding
            from backend.semantics.intent import parse_intent
            from backend.semantics.planner import plan
            params = source.get("dataset_query") or {}
            q, err, _ = parse_intent({"object": dataset["object_key"], "datasource_id": ds_id,
                "metrics": params.get("measures") or params.get("metrics") or [],
                "dimensions": params.get("dimensions") or [], "filters": params.get("filters") or [],
                "order": params.get("order") or [], "limit": params.get("limit") or 200})
            binding, _ = resolve_binding(dataset["object_key"], datasource_id=ds_id)
            if err or not binding:
                raise ValueError("数据集语义来源无效")
            planned = plan(q, binding)
            if not planned.sql or planned.provenance.get("unresolved_terms"):
                raise ValueError("数据集投影未完整解析")
            security = _snapshot(identity, planned.sql, ds_id, planned.dialect)
        else:
            for condition in (source.get("dataset_query") or {}).get("filters") or []:
                if (not isinstance(condition, dict)
                        or not dataset_service._FIELD_RE.fullmatch(str(condition.get("field") or ""))
                        or condition.get("op", "eq") not in dataset_service._OPS
                        or (condition.get("op") == "in" and not isinstance(condition.get("value"), list))
                        or condition.get("value") == []):
                    raise ValueError("数据集过滤条件无效")
            security = _snapshot(identity, dataset.get("sql_query") or "", ds_id)
        security.update(dataset_id=source["dataset_id"], dataset_digest=dataset_digest)
        result = dataset_service.query_dataset(source["dataset_id"], source.get("dataset_query") or {}, identity)
    elif source.get("saved_query_id"):
        saved = execute_query("SELECT * FROM adh_saved_queries WHERE id=%s AND owner_id=%s AND workspace_id=%s",
            (source["saved_query_id"], identity["user_id"], identity["workspace_id"]), fetchone=True)
        if not saved:
            raise PermissionError("分析来源不存在或无权访问")
        ds_id = int(saved.get("datasource_id") or 0)
        _authorize_source(identity, ds_id)
        sql = bounded_query(saved["sql_query"], 1000)
        security = _snapshot(identity, sql, ds_id)
        result = governed_execute(sql, ds_id, identity["user_id"], identity["workspace_id"], identity.get("username", ""))
    else:
        raise ValueError("缺少分析来源")
    if result.get("error"):
        raise ValueError("分析未完成")
    _verify_snapshot(identity, security)
    return {"status": "success", **{k: result[k] for k in ("columns", "rows", "row_count", "truncated") if k in result},
            "_security_context": security}
