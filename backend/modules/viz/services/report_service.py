"""Report Service — Report generation and retrieval.

Handles LLM-powered report generation from templates and query data.
Tables: adh_reports, adh_saved_queries（报告模板不在库里：唯一事实源 = 字模库 report_template）
"""

import asyncio
import json
import logging
import re
import secrets
import threading
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
    # 分析来源站点（业务名/代码，如 '中国'/'cn'）：intent 来源靠它定生效源。
    # 业务对象的绑定按站点登记，不带站点时 ds=0 永远解不出来（域规则：业务取数必须带具体源）。
    # 会话内发起的报告由服务端从会话上下文注入（不让 LLM 传内部标识）；REST/定时可直接传。
    site_code: str = ""

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
    can_access_report, public_report, json_object, is_content_report, body_pending_migration)

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


# ── 正文存取：对象存储是唯一事实源 ────────────────────────────────────────────
# ``adh_reports.content`` 退役为恒空串：正文一律写对象存储，库里只存 key
# （见 docker/mysql/report_content_oss_migration.sql）。读取时按 key 现拼「域名 + 签名」，
# 所以库里存 key 而不是存整条 URI —— 域名随部署配置走，签名是短期授权，落库即成死链。
#
# 失败纪律（no-silent-degradation）：上传失败一律外抛，**绝不**回退写 DB 的 content 列。
# 双真值源迟早飘，而且回退会把"对象存储配错了"这类部署缺陷掩成没人知道的哑巴。
CONTENT_URL_TTL = 300   # 直链有效期：只够浏览器取一次正文；本项目当前不开放报表分享
_CONTENT_MIME = {"markdown": "text/markdown; charset=utf-8", "html": "text/html; charset=utf-8"}


def content_storage():
    from backend.common.object_storage import get_object_storage
    return get_object_storage()


def put_report_content(report_id: int, owner_id: int, fmt: str, content: str) -> tuple[str, int]:
    """把正文写进对象存储，回 ``(key, 字节数)``；空正文不产对象（排队中的报表即此状态）。"""
    from backend.common.object_storage import build_report_content_key
    body = str(content or "").encode("utf-8")
    if not body.strip():
        return "", 0
    key = build_report_content_key(owner_id, report_id)
    try:
        content_storage().upload_bytes(
            key, body, content_type=_CONTENT_MIME.get(fmt, _CONTENT_MIME["markdown"]))
    except Exception as exc:
        logger.error("报表 %s 正文写入对象存储失败 (key=%s): %s", report_id, key, exc)
        raise ValueError("报告正文保存失败：对象存储不可用。请检查对象存储配置后重试，"
                         "正文未写入元数据库（不存在半成品副本）") from None
    return key, len(body)


def drop_report_content(row: dict) -> None:
    """删报表时带走正文对象。

    库行已删 = 用户意图达成；这里失败只会留下孤儿对象，记错误日志供清理，
    不能因为删对象失败而把删除报成失败（那会让人以为报表还在）。
    """
    key = str((row or {}).get("content_object_key") or "")
    if not key:
        return
    if not content_storage().delete(key):
        logger.error("报表正文对象删除失败，成为孤儿对象（需人工清理）: key=%s report=%s",
                     key, (row or {}).get("id"))


def body_missing_reason(row: dict) -> str:
    """key 为空时的可读原因 —— 详情与代理端点共用一句，不在两处各写一遍。"""
    return ("该报告正文尚未迁移到对象存储，请运行 "
            "backend/scripts/migrate_report_content_to_oss.py"
            if str((row or {}).get("content") or "").strip()
            else "报告正文为空，请重新生成或重新保存")


# 无人值守/派发各阶段的失败码 → 用户可读原因（单一口径：详情、分享视图、导出共用）。
# 只说“错在哪一步、现在能做什么”，不回显原始报错栈与基础设施标识（护栏 §7）。
_STAGE_ERROR_HINTS = {
    "LLM_UNAVAILABLE": "分析模型（Harness）未完成本轮运行，未拿到分析终稿",
    "QUERY_FAILED": "取数执行失败",
    "ANALYSIS_FAILED": "分析未完成",
    "DISPATCH_FAILED": "任务队列派发失败（队列不可用）",
    "TIMEOUT": "生成超时（租约到期，结果已作废）",
    "CANCELLED": "已被人工停止",
    "ITERATION_LIMIT": "分析回合数达到上限，未能收尾出结果",
    "NO_QUERY_RESULT": "本轮没有产出任何已授权的取数结果",
    "NEEDS_CLARIFICATION": "口径或取数范围不明确，无人值守分析已按规则停止（不猜口径）",
    "SOURCE_REQUIRED": "报告属主的角色没有已授权数据源",
    "TOOL_NOT_ALLOWED": "报告属主的角色未授权语义取数工具",
    "ASBOT_REQUIRED": "报告属主的角色没有可用的 AS-BOT 配置",
    "ASBOT_SCOPE_DENIED": "报告属主的角色授权已变化，本轮取数被拒",
    "CLIENT_ABORTED": "重新生成时页面被关闭，本轮已按离开中止（未产出新版本正文）",
    "CAPACITY_UNAVAILABLE": "平台会话调度异常，已暂停接收新的重新生成回合",
    "REFRESH_STREAM_REQUIRED": "重新生成需在报告页在线执行（队列侧不再代跑报告重跑）",
    "LAYER_UNAVAILABLE": "执行层（Harness）未启用或不可用",
    "LAYER_UNSUPPORTED": "执行层适配器不支持当前配置",
    "EMPTY_QUESTION": "报告未登记可复跑的原始问题",
}


def generation_failure_reason(row: dict) -> str:
    """生成未完成时的可读原因（错码 + 出路），供详情与列表共用。"""
    code = str((row or {}).get("stage_error_code") or "").strip()
    hint = _STAGE_ERROR_HINTS.get(code, "取数或分析环节失败")
    return (f"本次生成未完成：{hint}。可点「用最新数据重新生成」重试；"
            "切换历史版本仍可查看之前生成的正文")


def data_report_block_reason(row: dict) -> str:
    """数据派生报表当前不可读时，照实说是哪一类（no-silent-degradation §1）。

    旧写法把「排队中 / 生成失败 / 超时 / 权限快照变化」压成一句“报告未就绪或权限已变化”，
    用户据此去查权限，而真因是那次重跑根本没跑成（历史缺陷：报表重跑失败显示成权限问题，
    切换历史版本也归因到同一句）。
    """
    status = str((row or {}).get("generation_status") or "")
    if status in ("queued", "running"):
        # 租约是"还有实例在产这一版"的唯一事实：过期就是没人了（进程重启/被杀/派发丢了）。
        # 无条件说"正在生成中，稍后自动刷新"会把一个没人产出的版本讲成还在跑，
        # 用户只能干等 —— 而把它翻成 timeout 的清理任务是另一条腿，不保证已经跑过。
        if lease_expired(row):
            return ("这一版的生成进程已中断（租约过期，没有实例还在产出正文），"
                    "请用「用最新数据重新生成」重跑一次")
        return "报告正在生成中，稍后自动刷新…"
    if status in ("failed", "timeout", "cancelled"):
        return generation_failure_reason(row)
    if not report_access.has_body(row):
        return body_missing_reason(row)
    if not report_access.snapshot_is_current(row):
        return ("本报告生成时的数据权限快照已变化（数据源授权/敏感策略/数据集范围被收紧），"
                "历史正文不可重放；请用最新数据重新生成")
    return "报告未就绪，请重新生成"


def lease_expired(row: dict) -> bool:
    """租约是否已过期 —— 只读事实，不改状态（改状态是 ``cleanup_stale_reports`` 的活）。

    与 ``_now()`` 同一口径（本地时钟、秒精度），所以这里按 "%Y-%m-%d %H:%M:%S" 字符串比，
    不引第二套时区判断；驱动返回的 datetime 与 ISO 串都归一到同一形状。
    """
    value = (row or {}).get("lease_expires_at")
    if not value:
        return False
    return str(value)[:19].replace("T", " ") <= _now()


def read_report_content_bytes(row: dict) -> bytes:
    """服务端读正文的**唯一**入口（本地模式代理端点用它）；对外只回直链，不回正文。

    两种"读不到"分开报：key 空 = 还没搬/本来没正文（问题在库），对象缺 = 正文真丢了
    （问题在对象存储）。合成一句会让人跑错方向去查桶。
    """
    key = str((row or {}).get("content_object_key") or "")
    if not key:
        raise LookupError(body_missing_reason(row))
    data = content_storage().download_bytes(key)
    if data is None:
        raise ValueError("报告正文在对象存储里取不到了，请重新生成该报告")
    return data


def content_access(row: dict) -> dict:
    """按 key 现拼正文直链，回 ``{content_url, content_size, content_expires_at, content_warning}``。

    两种存储后端的寻址形式不同，但对外只有 **一个** 字段：前端一律 ``fetch(content_url)``，
    不需要知道背后是什么，也就不存在前端侧的第二套判断。
    * 对象存储模式 → 预签名直链，浏览器直连对象存储（桶需允许跨域 GET；已实测
      ``AllowedMethods`` 含 GET、``AllowedOrigins=*``）；
    * 本地回退模式（未配 OBJECT_STORAGE_*）→ 无签名可拼，给同源代理路径
      ``/api/reports/{id}/content`` 由服务端读盘回正文。
      这不是失败降级分支，是两种受支持的存储后端各自的地址形式，与内容附件的
      ``download-url`` / ``download`` 同一口径。
    """
    key = str((row or {}).get("content_object_key") or "")
    size = int((row or {}).get("content_size") or 0)
    if not key:
        # 只可能是存量正文还没搬：显式指向迁移脚本，绝不回退去读库里的 content 列
        return {"content_url": "", "content_size": size, "content_expires_at": None,
                "content_warning": body_missing_reason(row)}
    storage = content_storage()
    if not storage.is_object_storage:
        return {"content_url": f"/api/reports/{int(row['id'])}/content", "content_size": size,
                "content_expires_at": None, "content_warning": ""}
    url = storage.get_presigned_url(key, expires=CONTENT_URL_TTL)
    if not url:
        logger.error("报表 %s 正文直链生成失败 (key=%s)", row.get("id"), key)
        return {"content_url": "", "content_size": size, "content_expires_at": None,
                "content_warning": "报告正文直链生成失败，请稍后重试（对象存储签名不可用）"}
    from datetime import datetime, timedelta, timezone
    return {"content_url": url, "content_size": size,
            "content_expires_at": (datetime.now(timezone.utc)
                                   + timedelta(seconds=CONTENT_URL_TTL)).isoformat(),
            "content_warning": ""}


# ── Report CRUD ─────────────────────────────────────────────────────────────


def list_reports(user_id: int, workspace_id: int = 0, page: int = 1, size: int = 20,
                 user: dict = None) -> dict:
    """报表清单：报表跟随属主（与资产清单同域），默认跨工作空间；workspace_id>0 才收窄。

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
            result["content_url"] = ""
            result["access_warning"] = (
                # 搬迁没跑完是最需要被看见的一种"读不到"：原因和出路都别的文案给不出
                "报告正文尚未迁移到对象存储，请运行 backend/scripts/migrate_report_content_to_oss.py"
                if body_pending_migration(row) else
                "报告正文为空，请重新保存" if is_content_report(row)
                else data_report_block_reason(row))
            return result
        return None
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE adh_reports SET view_count = view_count + 1 WHERE id = %s", (report_id,))
    # 详情面带证据摘要（ready/degraded 的判定事实），清单面不带（见 public_report 的 content 参数）
    result = public_report(row, content=True)
    access = content_access(row)
    result.update(access)
    if access["content_warning"]:
        # 复用前端已有的 access_warning 通道：正文取不到必须让人看见，不给空白页
        result["access_warning"] = access["content_warning"]
    return result


def create_report(*, task_id: int = None, log_id: int = None, title: str,
                  content: str, format: str = "markdown", workspace_id: int,
                  owner_id: int, security_context: dict = None, run_key: str = None,
                  generation_status: str = "ready", evidence_summary: dict = None,
                  analysis_source: dict = None, source_type: str = "scheduled") -> dict:
    """数据派生报表的唯一持久化入口；未认证的旧结果只能保存为不可分享的降级报告。

    内容型报表（人写/会话保存）走 ``create_content_report``，两者共用一张表但
    可见性口径不同（见 report_access）。
    """
    if int(owner_id or 0) <= 0:
        raise PermissionError("报告缺少创建者")
    from backend.common.db import execute_query, execute_write
    if run_key:
        # 判复用要**先于**写对象：同一 run 的重投递不该产生第二份正文对象。
        existing = execute_query(
            "SELECT id, owner_id, workspace_id, generation_status FROM adh_reports WHERE run_key=%s",
            (run_key,), fetchone=True)
        if existing:
            if existing["owner_id"] != owner_id or existing["workspace_id"] != workspace_id:
                raise PermissionError("报告运行键冲突")
            return {"id": existing["id"], "run_id": run_key, "access_mode": "private",
                    "generation_status": existing["generation_status"]}
    report_id = _generate_id()
    key, size = put_report_content(report_id, int(owner_id), format, content)
    # content 列恒写空串：正文的唯一事实源是 content_object_key
    execute_write(
        "INSERT INTO adh_reports (id, task_id, log_id, title, content, content_object_key, content_size, "
        "format, access_mode, workspace_id, owner_id, created_at, updated_at, generation_status, "
        "publication_status, security_context, run_key, evidence_summary, analysis_source, source_type, "
        "lease_expires_at) "
        "VALUES (%s,%s,%s,%s,'',%s,%s,%s,'private',%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,"
        "DATE_ADD(NOW(), INTERVAL 10 MINUTE)) ON DUPLICATE KEY UPDATE id=id",
        (report_id, task_id, log_id, title, key, size, format, workspace_id, owner_id, _now(),
         _now(), generation_status, json.dumps(security_context or {}, ensure_ascii=False),
         run_key, json.dumps(evidence_summary or {}, ensure_ascii=False, default=str),
         json.dumps(analysis_source or {}, ensure_ascii=False, default=str), source_type),
    )
    return {"id": report_id, "run_id": run_key, "access_mode": "private", "generation_status": generation_status}


# ── 内容型报表（人写 / 会话保存）：报表中心的「管理」面 ────────────────────────

CONTENT_FORMATS = ("markdown", "html")
_MAX_TITLE_CHARS = 200
_MAX_CONTENT_CHARS = 200_000


def create_content_report(*, title: str, content: str, format: str = "markdown",
                          workspace_id: int, owner_id: int, source_type: str = "manual",
                          origin: dict = None, template_key: str = "") -> dict:
    """保存一份内容型报表：正文由人写或取自会话已展示给属主本人的内容。

    不取数、不过数据源，因此不产生权限快照；相应地只能属主私有查看，
    不参与外链/公开分享（``publication_status`` 固定 draft，见 report_access）。

    ``template_key``（仅手写报表用）：按报告模板的版式起稿。模板读不到就显式拒，
    不得静默建一张空白报表冒充“已按模板新建”（§1）；模板名随 origin 落库，报表上能追溯“套的哪个模板”。
    """
    if int(owner_id or 0) <= 0:
        raise PermissionError("报告缺少创建者")
    if source_type not in report_access.EDITABLE_SOURCES:
        raise ValueError("内容型报告来源只能是 manual 或 conversation")
    title = str(title or "").strip()
    if not title:
        raise ValueError("报告标题不能为空")
    if format not in CONTENT_FORMATS:
        raise ValueError(f"报告格式只能是 {'/'.join(CONTENT_FORMATS)}")
    content = str(content or "")
    origin = dict(origin or {})
    tpl_key = str(template_key or "").strip()
    if tpl_key:
        tpl = resolve_report_template(tpl_key)
        if not tpl.get("found"):
            raise ValueError(tpl.get("reason") or "报告模板不可用")
        if not content.strip():
            # 没手写正文才用模板骨架起稿；写了正文就以人的为准（模板只定版式，不抢用户已写的内容）
            content = template_skeleton_from_layout(tpl.get("layout") or {})
            format = str(tpl.get("format") or format)
        origin.update({"template_key": tpl_key, "template_name": tpl.get("name") or "",
                       "report_theme": tpl.get("theme") or ""})
    if not content.strip():
        raise ValueError("报告正文不能为空")
    report_id = _generate_id()
    # 正文先进对象存储再落行：上传失败就没有行，不会留下"行在、正文没了"的半截报表
    key, size = put_report_content(report_id, int(owner_id), format, content[:_MAX_CONTENT_CHARS])
    with DBConnection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO adh_reports (id, task_id, log_id, title, content, content_object_key, "
                "content_size, format, access_mode, workspace_id, owner_id, created_at, updated_at, "
                "generation_status, publication_status, security_context, evidence_summary, "
                "analysis_source, source_type) "
                "VALUES (%s,NULL,NULL,%s,'',%s,%s,%s,'private',%s,%s,%s,%s,'ready','draft',NULL,NULL,%s,%s)"
                " ON DUPLICATE KEY UPDATE id=id",
                (report_id, title[:_MAX_TITLE_CHARS], key, size, format,
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
        raise ValueError("报告不存在或已删除")
    if int(row.get("owner_id") or 0) != int(user["user_id"]):
        raise PermissionError("只能管理自己创建的报告")
    return row


def update_content_report(report_id: int, user: dict, *, title: str = None,
                          content: str = None, format: str = None) -> dict:
    """重命名 / 编辑内容型报表正文。数据派生报表正文不可手改（证据链唯一）。"""
    row = _load_owned_report(report_id, user)
    if not report_access.is_editable_report(row):
        if report_access.is_content_report(row):
            raise ValueError("这是迁移前由定时任务产出的历史报告，只读可查不可改；"
                             "如需一份可编辑的报告，请在智能问数里重新提问后「存为报告」")
        raise ValueError("定时任务/分析来源产出的报告正文由受治理取数生成，不可在线编辑；"
                         "如需修改请「用最新数据重新生成」，或在会话里重新提问后另存一份报告")
    sets, params = ["updated_at=%s"], [_now()]
    if title is not None:
        title = str(title).strip()
        if not title:
            raise ValueError("报告标题不能为空")
        sets.append("title=%s")
        params.append(title[:_MAX_TITLE_CHARS])
    if content is not None:
        if not str(content).strip():
            raise ValueError("报告正文不能为空")
        # key 不带扩展名也不带版本 → 就地覆盖同一对象，不需要搬旧对象、也不会读到旧正文
        fmt = format if format is not None else (row.get("format") or "markdown")
        key, size = put_report_content(report_id, int(row["owner_id"]), fmt,
                                       str(content)[:_MAX_CONTENT_CHARS])
        sets += ["content=''", "content_object_key=%s", "content_size=%s", "generation_status=%s"]
        params += [key, size, "ready"]
    if format is not None:
        if format not in CONTENT_FORMATS:
            raise ValueError(f"报告格式只能是 {'/'.join(CONTENT_FORMATS)}")
        sets.append("format=%s")
        params.append(format)
    from backend.common.db import execute_write
    execute_write(f"UPDATE adh_reports SET {','.join(sets)} WHERE id=%s AND owner_id=%s",
                  tuple(params) + (report_id, int(user["user_id"])))
    return {"report_id": report_id, "updated": True}


def _series_member_rows(row: dict, user: dict) -> list:
    """同一份报表（同归集键）的全部版本行。

    删报表要连「用最新数据重新生成」追加的版本一起删：只删最新一版会把旧版本
    留成一张孤儿卡片（用户看到“删了报表还在”）。归集口径只认 report_access.series_key
    （服务端唯一事实源），按属主取候选行再逐一算键匹配，不在 SQL 里复制一套判断。
    """
    from backend.common.db import execute_query
    key = report_access.series_key(row)
    target_id = int(row.get("id") or 0)
    rows = execute_query(
        "SELECT id, owner_id, task_id, log_id, title, source_type, analysis_source, content_object_key "
        "FROM adh_reports WHERE owner_id=%s", (int(user["user_id"]),)) or []
    members = [r for r in rows if report_access.series_key(r) == key]
    if not any(int(r.get("id") or 0) == target_id for r in members):
        members.append(row)   # 目标行本身一定删（旧行 series_key 计算差异时不遗漏）
    return members


def delete_report(report_id: int, user: dict) -> dict:
    """删除报表：仅属主；内容型与分析生成（含重跑追加的版本）可删，按归集键带走全部历史版本；
    定时任务产出的报表跟执行记录一一对应，只能在任务调度的执行记录里管理。"""
    row = _load_owned_report(report_id, user)
    if not report_access.deletable(row):
        raise ValueError("定时任务产出的报告与其执行记录一一对应，"
                         "请在任务调度的执行记录中管理")
    from backend.common.db import execute_write
    deleted = 0
    for member in _series_member_rows(row, user):
        if execute_write("DELETE FROM adh_reports WHERE id=%s AND owner_id=%s",
                         (int(member["id"]), int(user["user_id"]))) == 1:
            drop_report_content(member)   # 行已删，正文对象跟着走（失败只留孤儿，见函数注释）
            deleted += 1
    if not deleted:
        raise ValueError("报告删除失败（记录已变化），请刷新后重试")
    return {"report_id": report_id, "deleted": deleted}


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


# ── 本轮交付的文件产物（```artifact 块）：HTML/Markdown 报告本身就是报表正文 ──────
# 契约与前端下载卡片同源（backend/modules/mind/execution/chart_contract.py）：模型把报告
# 写进会话工作区，并在回答里输出一个 ```artifact JSON 块。
_ARTIFACT_BLOCK = re.compile(r"```artifact\s*\n(.*?)\n```", re.S)
# 可当报表正文的产物类型 → 报表格式。其余类型（excel/csv/pdf/图片）是数据文件或二进制
# 产物，归档入口在资产清单（报表中心只支持 markdown/html 渲染面，不假装能展示）。
_ARTIFACT_DOC_FORMATS = {"html": "html", "md": "markdown", "markdown": "markdown"}


def conversation_artifacts(answer: dict) -> list:
    """从回答正文里解析文件产物声明（形状非法的块跳过，不猜）。"""
    body = "\n".join(str((answer or {}).get(k) or "") for k in ("content", "reply"))
    out = []
    for raw in _ARTIFACT_BLOCK.findall(body):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and str(data.get("path") or "").strip():
            out.append(data)
    return out


def document_artifact(answer: dict) -> dict:
    """本轮交付的**文档型**产物（可当报表正文的那一类），取最后一份（多次交付以最新为准）。"""
    docs = [a for a in conversation_artifacts(answer)
            if artifact_report_format(a) in CONTENT_FORMATS]
    return docs[-1] if docs else {}


def artifact_report_format(artifact: dict) -> str:
    """产物类型 → 报表格式（不是文档型产物则空）。"""
    return _ARTIFACT_DOC_FORMATS.get(str((artifact or {}).get("type") or "").lower(), "")


def read_document_artifact(*, user: dict, conversation_id: int, artifact: dict) -> tuple:
    """按产物声明从会话工作区读正文，回 ``(报表格式, 正文)``；取不到一律外抛原因。

    不得“读不到就回退去存对话摘要”：那会把一份没归档成功的报告伪装成能用
    （no-silent-degradation §1），而用户要的正是那个 HTML 报告本身。
    """
    from backend.modules.mind.execution.session_workspace import read_conversation_workspace_text

    fmt = artifact_report_format(artifact)
    filename = str(artifact.get("filename") or artifact.get("path") or "").strip()
    try:
        text = read_conversation_workspace_text(
            int(conversation_id), int(user["user_id"]), str(artifact.get("path") or ""),
            max_chars=_MAX_CONTENT_CHARS)
    except (PermissionError, ValueError, FileNotFoundError) as exc:
        raise ValueError(f"本轮报告产物「{filename}」已取不到（{exc}）。报告正文以该产物为事实源，"
                         "不能拿对话摘要冒充；请在对话里重新生成该报告后再存为报告") from None
    return fmt, text


def _artifact_title(artifact: dict, body: str) -> str:
    """产物报表标题：文档自己的 h1/title 优先，其次文件名去扩展名。"""
    for pattern in (r"<h1[^>]*>(.*?)</h1>", r"<title[^>]*>(.*?)</title>"):
        m = re.search(pattern, body or "", re.S | re.I)
        if m:
            cand = _clean_heading(re.sub(r"<[^>]+>", " ", m.group(1)))
            if cand and len(cand) >= 4:
                return cand
    name = re.sub(r"\.[A-Za-z0-9]+$", "", str(artifact.get("filename") or "").strip())
    return _clean_heading(name)


def data_work_evidence(turn: dict) -> str:
    """本轮**事实**上有没有产出过数据/分析结果；有则回可读证据名，没有回空串。

    只看证据（结果集 / SQL / 图表 / 取数类工具调用），不看正文写了什么：
    「帮我输出成html形式的报告」这类续问只是把上一轮已取到的数据换个交付形态
    （工具只有 write/edit），它自己并不构成一次分析——判据用它，不靠措辞猜。
    本函数是这套证据的唯一实现（``turn_reportability`` 与可复跑问题归位都消费它）。
    """
    if not isinstance(turn, dict):
        return ""
    result = turn.get("result") or {}
    if isinstance(result, dict) and (result.get("columns") or []) and (result.get("rows") or []):
        return "含查询结果表"
    if turn.get("sql"):
        return "含已执行的查询"
    if turn.get("chart_type"):
        return "含图表产出"
    body = "\n".join(str(turn.get(k) or "") for k in ("content", "reply", "analysis"))
    if _CHART_BLOCK.search(body):
        return "含图表产出"
    for call in turn.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        name = str(call.get("tool") or "").rsplit("__", 1)[-1]
        if name in DATA_BEARING_TOOLS:
            return "本轮已完成取数/分析"
    return ""


def turn_reportability(turn: dict) -> tuple[bool, str]:
    """判定一个助手回合是否可存为报表，返回 ``(可存, 可读原因)``。

    入参是 done 事件/已落库消息的形状（reply/content/result/sql/chart_type/tool_calls）。
    """
    if not isinstance(turn, dict):
        return False, "回合记录无效"
    if turn.get("error"):
        return False, "该回合执行未完成，没有可归档的结果"
    # 交付了 HTML/Markdown 报告文件 = 本轮成品就是报表（旧写法不看 artifact，
    # 导致整份 HTML 报告的回合连「存为报表」入口都不给）
    if document_artifact(turn):
        return True, "含报告文件产物"
    evidence = data_work_evidence(turn)
    if evidence:
        return True, evidence
    if conversation_artifacts(turn):
        # 交付的是数据文件（Excel/CSV/PDF/图片）：出路是资产清单，不是报表中心
        return False, ("该回合交付的是数据文件（Excel/CSV/PDF/图片），不是可在线查看的报告；"
                       "请在产物卡片上「归档」存入资产清单")
    return False, "该回合是问答或系统操作说明，不是数据分析结果，没有可归档的取数产物"


_MAX_QUESTION_CHARS = 4000
#: 归位后随 origin 登记：弹窗必须如实告诉用户"这句不是本轮原话，是按取数事实归位来的"
QUESTION_CARRIED_NOTE = "本轮只改交付形态/补充内容、没有重新取数，复用问题已归位到会话里实际取数那一轮"


def _turn_question(items: list, idx: int) -> str:
    """这一轮的问题：优先助手消息上服务端登记的 question，其次它前面那条用户消息原文。"""
    q = str((items[idx] or {}).get("question") or "").strip()
    if q:
        return q
    for prev in range(idx - 1, -1, -1):
        if items[prev].get("role") == "user":
            return str(items[prev].get("content") or "").strip()
    return ""


def reproducible_question(messages: list, answer: dict) -> tuple[str, str]:
    """这一轮**可复跑**的分析问题，回 ``(问题正文, 归位说明)``（说明为空=本轮自己的问题）。

    历史缺陷：报表往往存在"帮我输出成html形式的报告""地图怎么没在报告中"这类**续问**回合，
    本轮只有 write/edit、没有取数。把这句原样登记成可复跑问题，定时任务与
    「用最新数据重新生成」就等于去问一句没有分析对象的话（真机：会话 1783069459277，
    报表「direct-ip 页面访问 IP 归属地分布报告」复用问题只剩"帮我输出成html形式的报告"）。

    判据用**事实**（``data_work_evidence``）而不是措辞猜测：本轮没取数 → 往前找最近一个
    真取过数的回合，用它的问题当分析主体，本轮诉求作为「后续要求」一并带走
    （交付形态也是用户要的东西，丢了就等于定时任务产出不一致的版式）。
    """
    items = [m for m in (messages or []) if isinstance(m, dict)]
    if not items:
        return str((answer or {}).get("question") or "").strip(), ""
    uuid = str((answer or {}).get("message_uuid") or "")
    idx = next((i for i, m in enumerate(items)
                if m is answer or (uuid and str(m.get("message_uuid") or "") == uuid)), None)
    if idx is None:
        # 这一轮不在消息清单里（定位键对不上）：不猜它是哪一轮，照登记的问题回
        return str((answer or {}).get("question") or "").strip(), ""
    own = _turn_question(items, idx)
    if not own or data_work_evidence(answer or {}):
        return own, ""
    for prev in range(idx - 1, -1, -1):
        if items[prev].get("role") != "assistant" or not data_work_evidence(items[prev]):
            continue
        base = _turn_question(items, prev)
        if not base:
            break
        if base == own:
            return own, ""
        return f"{base}\n后续要求：{own}"[:_MAX_QUESTION_CHARS], QUESTION_CARRIED_NOTE
    return own, ""


def with_reportability(payload: dict) -> dict:
    """给 done 事件/已落库消息补 ``reportable`` + ``reportable_reason``（返回副本）。

    前端只消费这个字段决定「存为报表」按钮是否出现，不得自己再写一套判断。
    """
    ok, reason = turn_reportability(payload)
    out = dict(payload)
    out["reportable"] = ok
    out["reportable_reason"] = reason
    return out


# ── 报表标题归纳：标题要说“这份报表讲了什么”，不是把用户口语提问原样抄进标题栏 ───
# 全部是确定性抽取规则（不调模型、不花额外时延）：正文里有意义的小标题 → 图表块 title
# → 结果集标题 → 清洗后的提问（去口语前缀、截到首个语义停顿）只作兜底。
_GENERIC_HEADINGS = {
    "查询结果", "结果", "总结", "结论", "说明", "背景", "建议", "概述", "详情", "附录",
    "分析", "分析结果", "数据分析", "核心发现", "关键发现", "数据说明", "口径说明", "回答",
}
_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*$", re.M)
_CHART_FENCE_RE = re.compile(r"```chart\s*\n(.*?)\n```", re.S)
# 版式抽取用：HTML 报告的章节结构在 <h1>~<h4>；上限防把整份长文档的标题全灌进提示词
_HTML_HEADING_RE = re.compile(r"<h([1-4])[^>]*>(.*?)</h\1>", re.I | re.S)
_LAYOUT_MAX_HEADINGS = 12
_LAYOUT_MAX_CHARTS = 8
_QUESTION_PREFIX_RE = re.compile(
    r"^(?:请|麻烦|帮我|帮忙|能不能|能否|可以|我想|我要|我们|现在|你)[\s，,。:：]*"
    r"(?:查一下|查一查|查下|查询|查看|看看|看下|统计|分析|生成|展示|告诉我|介绍|说明|查)?[\s，,。:：]*")
_QUESTION_CUT_RE = re.compile(r"[。，,！？?;；\n]")


def _clean_heading(text) -> str:
    """标题候选清洗：去 markdown 强调/链接/行内代码，压空白，限长 60。"""
    t = re.sub(r"\*\*|__|`", "", str(text or ""))
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip(" \t、,，:：-—·|")
    return t[:60].strip()


def derive_report_title(question: str, answer_text: str, result: dict = None) -> str:
    """按报表实际内容归纳标题（用户提问只作最后兜底，不直接当标题）。

    正文里的小标题/图表标题就是报表自己的名字（如「全国企业分布地图」），
    比“能帮忙展示中国地图吗”这类口语提问更适合当报表清单的条目名。
    """
    body = str(answer_text or "")
    for m in _HEADING_RE.finditer(body):
        cand = _clean_heading(m.group(1))
        if cand and cand not in _GENERIC_HEADINGS and len(cand) >= 4:
            return cand
    for block in _CHART_FENCE_RE.findall(body):
        try:
            spec = json.loads(block)
        except (ValueError, TypeError):
            continue
        if isinstance(spec, dict):
            cand = _clean_heading(spec.get("title") or "")
            if cand and cand not in _GENERIC_HEADINGS and len(cand) >= 2:
                return cand
    for key in ("chart_title", "title"):
        cand = _clean_heading((result or {}).get(key) or "")
        if cand and cand not in _GENERIC_HEADINGS and len(cand) >= 4:
            return cand
    q = _QUESTION_CUT_RE.split(str(question or "").strip(), maxsplit=1)[0]
    q = _clean_heading(_QUESTION_PREFIX_RE.sub("", q))
    return (q[:30].strip() or "会话分析报告")


def decision_advice_block(conversation_id: int, message_uuid: str) -> str:
    """本轮已落的「决策建议」实例 → 报表正文区块（经回调表取，不开 viz→decision 的 L2 横向边）。

    为什么不重算：报表可能在几天后归档，重算拿的是新口径/新数据，存进报表的
    就不是“当时那条建议”（与 Chat 回放同一条例外）。
    读不到就显式说一句，不能沉默地把“没读到”当成“没建议”。
    """
    from backend.core.task_runtime import call

    if int(conversation_id or 0) <= 0:
        return ""
    try:
        return str(call("decision.report_block", int(conversation_id), str(message_uuid or "")) or "")
    except Exception as exc:  # noqa: BLE001 — 决策模块缺席/读失败不能卡住报表归档
        logger.warning("[report] 本轮决策建议读取失败 conv=%s: %s", conversation_id, exc)
        return f"> 本轮决策建议未能随正文归档（读取失败：{type(exc).__name__}）。"


def render_conversation_report(*, answer: str, result: dict = None,
                               advice_block: str = "") -> str:
    """把会话里一轮已展示给用户的内容拼成报表正文（Markdown）。

    只拼用户已经看到的文字与结果表（最多 200 行），不回捞原始 SQL / 物理标识。

    正文不重复用户问题：报表标题（详情头栏与清单条目）已经说明这份报表讲什么，
    正文再抄一遍「提问：…」是同一句话说两次。问题正文仍登记在 ``origin.question``
    （供「按最新数据重新生成」与「创建定时任务」复跑），只是不进正文。
    """
    lines = [(answer or "").strip()]
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
    if (advice_block or "").strip():
        # 建议作为独立区块附在正文后（“建议”本来就是段落白名单里的一类）
        lines += ["", advice_block.strip()]
    return "\n".join(lines).strip()


def _load_conversation_answer(*, user: dict, conversation_id: int, message_index: int = None,
                              message_uuid: str = "") -> tuple:
    """定位会话里那一条助手回答 → ``(conv, answer, messages)``（归位问题要往前翻回合）。

    正文一律不信请求体：按 ``conversation_id`` + 属主取拆行消息表
    （``backend/core/conversation_store.py``），
    再按 message_uuid（优先）/ 下标定位；都定位不到时退回最后一条助手回答
    （旧客户端不传定位键）。报表与报表草稿共用这一条定位口径，不各写一份。
    """
    from backend.common.db import execute_query
    from backend.core import conversation_store
    if int(conversation_id or 0) <= 0:
        raise ValueError("缺少会话标识，无法保存报告")
    conv = execute_query(
        "SELECT id, workspace_id, title, as_bot_key, datasource_id, site_code "
        "FROM adh_conversations WHERE id=%s AND user_id=%s",
        (conversation_id, int(user["user_id"])), fetchone=True)
    if not conv:
        raise PermissionError("会话不存在或不属于当前用户")
    messages = conversation_store.load_messages(int(conversation_id))
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
    return conv, answer, messages


def build_report_draft(*, user: dict, conversation_id: int, message_index: int = None,
                       message_uuid: str = "") -> dict:
    """生成**报表草稿**初稿（留在会话里，可反复调整；点「归档」才进报表中心）。

    为什么草稿不落 ``adh_reports``：报表中心清单不过滤 ``publication_status``，
    草稿一旦建行就会出现在用户报表列表里（未定稿的东西混进正式产物）。
    草稿与会话同生命周期（存进那条消息的 ``reportDraft``，走既有会话保存通道），
    删会话即消失，不新增存储与清理路径。

    治理判定与归档同一口径：不可存的回合（纯问答/系统操作）给同一句可解释原因，
    含基础设施信息的回答**在这里就拦**（而不是等用户改完稿、点归档时才发现存不了）。
    """
    from backend.modules.mind.execution import outbound_guard
    conv, answer, messages = _load_conversation_answer(
        user=user, conversation_id=conversation_id,
        message_index=message_index, message_uuid=message_uuid)
    reportable, reason = turn_reportability(answer)
    if not reportable:
        raise ValueError(reason)
    # 续问（只换交付形态）归位到实际取数那一轮：草稿标题与后续复跑都取这一句
    question, _note = reproducible_question(messages, answer)
    answer_text = str(answer.get("content") or answer.get("reply") or "").strip()
    if not answer_text:
        raise ValueError("该回合没有可保存的正文（可能执行失败或被中断）")
    result = answer.get("result") if isinstance(answer.get("result"), dict) else None
    artifact = document_artifact(answer)
    if artifact:
        # 本轮交付的是 HTML/Markdown 报告文件：正文取产物本身（与下载卡片同一份），
        # 而不是把“报告已生成，包含…”那段目录当报表正文存下来
        fmt, content = read_document_artifact(
            user=user, conversation_id=int(conv["id"]), artifact=artifact)
    else:
        fmt, content = "markdown", render_conversation_report(
            answer=answer_text, result=result,
            advice_block=decision_advice_block(
                int(conv["id"]), str(answer.get("message_uuid") or message_uuid or "")))
    hits = outbound_guard.find_sensitive_hits(content)
    if hits:
        raise ValueError("该回答包含不应归档的基础设施信息（命中: "
                         + ", ".join(hits) + "），不能存为报告；请在对话中重新表述业务结论")
    now = _now()
    artifact_title = _artifact_title(artifact, content) if artifact else ""
    return {
        "title": (artifact_title
                  or derive_report_title(question, answer_text, result))[:_MAX_TITLE_CHARS],
        "markdown": content[:_MAX_CONTENT_CHARS],
        # 正文格式随产物走：html 草稿不是 markdown，前端不得把它当纯文本给入编辑器
        "format": fmt,
        "artifact": ({"filename": str(artifact.get("filename") or ""),
                      "type": str(artifact.get("type") or "")} if artifact else None),
        "created_at": now, "updated_at": now, "revisions": 0,
        "message_uuid": str(answer.get("message_uuid") or message_uuid or ""),
        "conversation_id": int(conv["id"]),
    }


def save_conversation_report(*, user: dict, conversation_id: int, title: str = "",
                             message_index: int = None, message_uuid: str = "") -> dict:
    """把会话里的一轮分析（或其**报表草稿**）归档进报表中心。

    带 ``reportDraft`` 的那一轮以草稿为准（正文=用户改完的 markdown，标题=草稿标题），
    草稿仍从服务端已落库的会话读，不由请求体传入。已归档过的草稿**幂等返回原报表**，
    避免前端标记没写回去时重复落一份。
    """
    from backend.modules.mind.execution import outbound_guard
    conv, answer, messages = _load_conversation_answer(
        user=user, conversation_id=conversation_id,
        message_index=message_index, message_uuid=message_uuid)
    draft = answer.get("reportDraft")
    draft = draft if isinstance(draft, dict) else {}
    archived = draft.get("archived")
    archived = archived if isinstance(archived, dict) else {}
    prior_id = int(archived.get("report_id") or 0)
    if prior_id:
        existing = load_report(prior_id)
        if existing and int(existing.get("owner_id") or 0) == int(user["user_id"]):
            return {"report_id": prior_id, "title": str(existing.get("title") or ""),
                    "format": str(existing.get("format") or "markdown"),
                    "source_type": "conversation", "already_archived": True,
                    "workspace_id": int(existing.get("workspace_id") or 0)}
    reportable, reason = turn_reportability(answer)
    if not reportable:
        # 与前端按钮展示同一口径：不展示的东西也不允许绕过前端存进来（原因可解释）
        raise ValueError(reason)
    # 可复跑问题按会话事实归位（本轮只换交付形态时往前取实际取数那一轮）
    question, question_note = reproducible_question(messages, answer)
    answer_text = str(answer.get("content") or answer.get("reply") or "").strip()
    if not answer_text:
        raise ValueError("该回合没有可保存的正文（可能执行失败或被中断）")
    draft_body = str(draft.get("markdown") or "").strip()
    artifact = document_artifact(answer)
    if draft_body:
        # 草稿即成品：不再追加结果表（结果表已在初稿里，用户删掉就是不要）
        content = draft_body
        draft_format = str(draft.get("format") or "").strip()
        fmt = draft_format if draft_format in CONTENT_FORMATS else (
            artifact_report_format(artifact) if artifact else "markdown")
    elif artifact:
        # 没有草稿正文（旧客户端直接归档）：正文仍取本轮报告产物，不退回存目录摘要
        fmt, content = read_document_artifact(
            user=user, conversation_id=int(conv["id"]), artifact=artifact)
    else:
        fmt = "markdown"
        content = render_conversation_report(
            answer=answer_text,
            result=answer.get("result") if isinstance(answer.get("result"), dict) else None,
            # 草稿正文里已经带过区块（不重复插）；无草稿直接归档时从实例补上
            advice_block=decision_advice_block(
                int(conv["id"]), str(answer.get("message_uuid") or message_uuid or "")))
    hits = outbound_guard.find_sensitive_hits(content)
    if hits:
        raise ValueError("该报告包含不应归档的基础设施信息（命中: "
                         + ", ".join(hits) + "），不能进报告中心；请修改正文后重试")
    report_title = (str(title or "").strip()
                    or str(draft.get("title") or "").strip()
                    or (_artifact_title(artifact, content) if artifact else "")
                    or derive_report_title(question, answer_text,
                                          answer.get("result") if isinstance(answer.get("result"), dict) else None))
    result = create_content_report(
        title=report_title, content=content, format=fmt,
        workspace_id=int(conv.get("workspace_id") or 0), owner_id=int(user["user_id"]),
        source_type="conversation",
        origin={"channel": "chat", "conversation_id": int(conv["id"]),
                "conversation_title": conv.get("title") or "",
                "message_uuid": str(message_uuid or answer.get("message_uuid") or ""),
                # 可复跑事实：问题正文 + 当时的会话站点（重跑与建任务只读这里，不接受
                # 请求体传问题/源，护栏 §2）。as_bot_key/datasource_id 只作当时的事实快照
                # 供排查，不再是可复跑的前提——执行时按属主角色实时继承能力与可查范围。
                "question": question,
                # 归位说明：非空 = 这句不是本轮原话，弹窗/排查都要能看到是按事实归位来的
                "question_note": question_note,
                "as_bot_key": str(conv.get("as_bot_key") or ""),
                "datasource_id": int(conv.get("datasource_id") or 0),
                "site_code": str(conv.get("site_code") or ""),
                # 产物溯源：报表正文来自哪份文件（仅供服务端排查，不对外下发新字段）
                "artifact_filename": str(artifact.get("filename") or ""),
                # 上一版交付时声明的主题（```artifact 块的 theme 字段）：重跑沿用同一主题，
                # 而不是让模型每轮重新猜一套配色（真机实测它会自造 hex，与字模库漂移）
                "theme": str(artifact.get("theme") or "")})
    result["workspace_id"] = int(conv.get("workspace_id") or 0)
    result["archived_at"] = _now()
    return result


# ── 报表复跑：按报表登记的原始问题重新生成 / 快捷创建同款定时任务 ──────────────

def report_origin(row: dict) -> dict:
    """取报表登记的可复跑来源（存报表时由服务端写入），不信请求体。"""
    source = json_object(row.get("analysis_source")) or {}
    origin = source.get("origin")
    return origin if isinstance(origin, dict) else {}


# ── 报表版式：「用最新数据重新生成」= 只换数据，不换原报表的页面样式 ──────────────
# 版式 = 章节骨架 + ```chart 图表规格（chart_type/columns，**不含 rows**）。
# 历史缺陷：重跑分支把正文一律交给 render_fact_report（通用「分析项 N」表格），模型写的
# 叙述与图表整段丢弃 —— 会话里存的那张中国地图报表重跑后变成两张干表格，用户读到的是
# "数据新了但这不是原来那份报表"。版式在点重跑时抽取并随任务带走，产出后按同一份校验。
def has_report_layout(layout: dict) -> bool:
    """有没有可复用的原版式（无结构就别假装能沿用版式，重跑会退回事实表格并说明原因）。"""
    return bool((layout or {}).get("headings") or (layout or {}).get("charts"))


def extract_report_layout(body: str, fmt: str = "markdown") -> dict:
    """从报表正文抽版式骨架（章节标题 + 图表规格），只抽结构不抽数据。

    图表块里缺 ``chart_type`` 或 ``columns`` 的不计入版式：形状不完整的块没法当"同一张图"
    去校验，宁可退回事实表格，也不要拿一套猜出来的版式去判重跑达标（§1）。
    """
    text = str(body or "")
    if str(fmt or "").lower() == "html":
        headings = [_clean_heading(re.sub(r"<[^>]+>", " ", m.group(2)))
                    for m in _HTML_HEADING_RE.finditer(text)]
        return {"format": "html", "headings": [h for h in headings if h][:_LAYOUT_MAX_HEADINGS],
                "charts": []}
    charts = []
    for raw in _CHART_FENCE_RE.findall(text):
        try:
            spec = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(spec, dict):
            continue
        chart_type = str(spec.get("chart_type") or "").strip()
        columns = [str(c).strip() for c in (spec.get("columns") or []) if str(c).strip()]
        if not chart_type or not columns:
            continue
        charts.append({"title": _clean_heading(spec.get("title") or ""),
                       "chart_type": chart_type, "columns": columns})
    # 图表块里的 # 不是章节标题（JSON 字符串里的 markdown 片段会被当成结构）
    prose = _CHART_FENCE_RE.sub("", text)
    headings = [_clean_heading(m.group(1)) for m in _HEADING_RE.finditer(prose)]
    return {"format": "markdown", "headings": [h for h in headings if h][:_LAYOUT_MAX_HEADINGS],
            "charts": charts[:_LAYOUT_MAX_CHARTS]}


def report_layout_contract(layout: dict, theme: str = "") -> str:
    """把原版式写成给无人值守模型的输出契约（整段文本由报表域一处生成，执行链只拼接）。

    最要紧的一条写在最前：**数值必须来自本次查询**——否则模型会照抄上一版正文，
    产出一份"版式完全正确、数字全是旧的"报表，那比版式变了更坏。
    """
    if not has_report_layout(layout):
        return ""
    headings = [str(h) for h in (layout.get("headings") or [])]
    charts = [c for c in (layout.get("charts") or []) if isinstance(c, dict)]
    lines = ["## 报表版式要求（本次是按最新数据重新生成：只换数据，不换版式）",
             "这份报表上一版有固定版式，下列结构必须在你的回答里原样保留："]
    if headings:
        lines.append("- 章节标题逐字保留（层级与文字一致、顺序不变）：" + " / ".join(headings))
    for spec in charts:
        # 标题里的时间窗必须允许改写：版式从真报表沉淀时标题常自带"近 7 天 · Top 15 省份"，
        # 逐字保留会让月度任务挂着一个不相干的窗口 —— 读者是按标题理解口径的，这比版式变了
        # 更误导。事后校验（layout_deviation）只比 chart_type+columns，所以改窗口零达标成本。
        lines.append("- 保留一个 ```chart 块：" + json.dumps(
            {"title": spec.get("title") or "", "chart_type": spec.get("chart_type"),
             "columns": spec.get("columns") or []}, ensure_ascii=False)
            + "（chart_type 与 columns 不得改，只把 rows 换成最新查询结果；"
              "标题里写死的时间范围按本轮实际窗口改写，其余文字沿用）")
    if str(theme or "").strip():
        lines.append(f"- 配色沿用上一版报表的主题 {str(theme).strip()}（令牌取系统提示词「报告主题令牌」段，不得自造颜色）。")
    lines += ["- 所有数值一律取本轮查询返回的结果：严禁沿用上一版的数字、严禁编造；"
              "本轮查不到的项就写" + "\"暂无数据\"" + "，不要留旧值；"]
    # 只有登记了章节才谈"章节下的结论"：纯图表版式（只有一张图、没有固定章节）里这句是空话，
    # 留着会让模型凭空补出契约没要求的章节，反倒把版式改宽了。
    if headings:
        lines.append("- 章节下的结论文字按最新数据重写，数据里没有依据的结论不要写；")
    lines.append("- 只输出报表正文：不要开场白、不要说明你做了什么、不要输出 ```artifact 块。")
    if str(layout.get("format") or "") == "html":
        lines.append("- 正文是整份自包含 HTML 文档（含 <style> 与内联图表），沿用上一版的章节结构与样式基调。")
    else:
        lines.append("- 正文用 Markdown 输出（配合上面的 ```chart 块）。")
    return "\n".join(lines)


def _looks_like_html_doc(text: str) -> bool:
    body = str(text or "").lstrip()
    return body.lower().startswith("<!doctype html") or bool(re.match(r"<html[\s>]", body, re.I))


def unwrap_document_fence(text: str, fmt: str = "markdown") -> tuple:
    """把模型终稿剥成“从正文开始”的成品，回 ``(正文, 拒绝原因)``。

    真机实测：模型常在整份 HTML 文档外面包一层 ```html 围栏，前面再带一句“我直接输出报告”。
    那些东西进隔离 iframe 会被当成正文渲染（“存了但不长那样”同族缺陷），必须剪掉；
    剪不出一份文档就明说原因（不拿带围栏的正文冒充沿用成功）。Markdown 同理只剥
    整段包起来的 ```markdown 围栏（正文内部的 ```chart 围栏不能动）。
    """
    stripped = str(text or "").strip()
    if str(fmt or "markdown") == "html":
        start = re.search(r"<!doctype\s+html|<html[\s>]", stripped, re.I)
        if start:
            end = stripped.rfind("</html>")
            tail = end + len("</html>") if end > start.start() else len(stripped)
            return stripped[start.start():tail].strip(), ""
        return stripped, "正文不是整份 HTML 文档（上一版是 HTML 报告）"
    fenced = re.match(r"^```(?:markdown|md)\s*\n(.*)\n```$", stripped, re.S)
    if fenced:
        return fenced.group(1).strip(), ""
    return stripped, ""


def layout_deviation(body: str, layout: dict) -> list:
    """新版正文相对原版式的偏差清单（空 = 沿用了原版式）。只判结构，不判数字。"""
    layout = layout or {}
    text = str(body or "")
    if not has_report_layout(layout):
        return []
    fmt = str(layout.get("format") or "markdown")
    reasons = []
    if fmt == "html" and not _looks_like_html_doc(text):
        # 先过格式关：格式就不对，再拿 markdown 口径去比章节只会多堆几条没用的原因
        return ["正文不是整份 HTML 文档（上一版是 HTML 报告）"]
    new = extract_report_layout(text, fmt)
    got_headings = new.get("headings") or []
    missing = [h for h in (layout.get("headings") or []) if h not in got_headings]
    if missing:
        reasons.append("缺少章节：" + "、".join(missing[:5]))
    produced = {(c.get("chart_type"), tuple(c.get("columns") or [])) for c in new.get("charts") or []}
    for spec in layout.get("charts") or []:
        key = (spec.get("chart_type"), tuple(spec.get("columns") or []))
        if key not in produced:
            reasons.append(f"缺少图表：{spec.get('chart_type')}（{'/'.join(spec.get('columns') or [])}）")
    if reasons:
        return reasons
    if fmt != "html":
        # 结构对上了再核一遍图有没有真的带数据（空 rows 的图等于没图，不算沿用版式）
        for raw in _CHART_FENCE_RE.findall(text):
            try:
                spec = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(spec, dict) and not (spec.get("rows") or []):
                label = _clean_heading(spec.get("title") or "") or str(spec.get("chart_type") or "图表")
                reasons.append(f"图表「{label}」没有数据行")
    return reasons


def prepare_report_refresh(report_id: int, user: dict) -> dict:
    """按报表登记的原始问题重新取数：先在本报表下建好**新报表版本**，回一份可执行的重跑计划。

    只消费服务端存报表时写下的 origin（问题正文 + 当时的会话站点）与**当前这份报表的正文版式**：
    不接受请求体传问题/数据源/身份（护栏 §2 身份与资源不由客户端指定）。
    重跑不是"重新问一遍拿个新答案"，而是同一份报表换成新数据：原版式（章节 + 图表规格）
    先抽出来当输出契约带走，产出后按同一份校验，不达契约就在版本上显式标注。
    可查范围与能力不按报表里存的 AS-BOT/数据源，而是按报表属主的角色实时继承 ——
    报表存时用的源被收回授权后重跑会被服务端拒，而不是拿旧配置越权取数。
    任务产出的报表不在这里重跑 —— 它的重跑入口是所属定时任务（新版本自然归集）。

    2026-11 改动：不再派发 Celery 后台任务（用户实拍"点了只剩一句生成中，进度看不到，
    离开页面它照跑"）。本函数只做**能在 HTTP 状态码里说清**的部分：建档、认领租约、
    算版式契约与执行身份；运行本身交给 ``stream_report_refresh``，在同一条 SSE 连接里
    跑完 —— 过程可见，观看方离开即终止。原来的「派发失败」这一类事实也随派发一起消失。
    """
    row = _load_owned_report(report_id, user)
    if row.get("task_id"):
        raise ValueError("该报告由定时任务产出，请到任务调度触发该任务重跑（新版本会自动归集为同一份报告的历史版本）")
    origin = report_origin(row)
    question = str(origin.get("question") or "").strip()
    site_code = str(origin.get("site_code") or "").strip()
    if not question:
        raise ValueError("该报告没有登记可复跑的原始分析问题（手写报告或改造前的历史报告），无法按最新数据重新生成；"
                         "可在智能问数里重新提问后另存为报告")
    from uuid import uuid4
    run_id = uuid4().hex
    title = str(row.get("title") or "分析报告")
    # 原版式：抽得到就带走（只换数据、不换版式）；读不到正文不掩成"没版式"，
    # 而是记下原因，产出时一并回显（否则用户只会看到"新报表长得不一样"，查不出为什么）
    layout_error = ""
    body_text = ""
    if report_access.has_body(row):
        try:
            body_text = read_report_content_bytes(row).decode("utf-8", "replace")
        except (ValueError, LookupError) as exc:
            layout_error = f"上一版正文读不到（{exc}），本次无版式可沿用"
            logger.warning("报表 %s 原版式抽取失败: %s", report_id, exc)
    fmt = str(row.get("format") or "markdown")
    layout = extract_report_layout(body_text, fmt)
    if not layout_error and not has_report_layout(layout):
        layout_error = "上一版正文没有可复用的版式结构（无章节标题与图表块）"
    theme = str(origin.get("theme") or "")
    source = {"kind": "agent_question", "question": question,
              "site_code": site_code,
              "refreshed_from": int(report_id),
              # 归集继承来源报表的键：新版本就落在原报表卡片的历史版本里，不另开一张同名卡片
              # （前端归集只看这个键，不自己拼判断）
              "refresh_series": report_access.series_key(row),
              # 版式契约（含 format）：生成侧据此拼提示词并校验产出
              "layout": layout,
              "layout_error": layout_error,
              # 主题沿用：上一版交付声明过的主题（没有则交提示词按默认主题包）
              "report_theme": theme,
              # 新报表自己也要可再次重跑 / 可建任务：可复跑问题随 origin 带走
              # （属主手改过的标记也要带走，否则回填脚本会把人改的那句又盖掉）
              "origin": {"channel": "report_refresh", "question": question,
                         "site_code": site_code,
                         "question_edited": bool(origin.get("question_edited")),
                         "conversation_id": int(origin.get("conversation_id") or 0),
                         "conversation_title": str(origin.get("conversation_title") or "")}}
    report = create_report(
        title=title, content="", format=fmt if has_report_layout(layout) else "markdown",
        workspace_id=int(row.get("workspace_id") or 0),
        owner_id=int(user["user_id"]), run_key=run_id, generation_status="queued",
        source_type="analysis", analysis_source=source)
    # 认领 = 把 queued 翻成 running 并起租约：与队列侧同一个入口、同一套幂等保护。
    # 抢不到说明这一版已有别的执行体在跑（重复点击/重复提交），显式停住而不是并发跑两次
    # 把正文写成半截。
    if not claim_report(report["id"]):
        raise ValueError("这一版报告的上一次重新生成还没结束，请等它完成后再试")
    return {
        "report_id": report["id"], "run_id": run_id, "title": title, "question": question,
        # `_finalize_agent_report` 需要的报表上下文（正文优先级判断只看标题与版式）
        "report": {"id": report["id"], "title": title},
        "source": source,
        # 无人值守配置：与定时任务同一形状（版式契约由报表域算好整段带走）
        "config": {"site_code": site_code, "report_theme": theme,
                   "layout_contract": report_layout_contract(layout, theme)},
        # 执行身份按报表属主的**当前**角色重解（能力与可查范围实时继承，不信报表里存的配置）
        "identity": resolve_execution_owner(int(user["user_id"]),
                                           int(row.get("workspace_id") or 0)),
    }


# ── 在线重跑：过程可见 · 离开即终止 ────────────────────────────────────────────
REFRESH_TICK_S = 1.0          # 播报心跳间隔，同时是"客户端还挂着吗"的探测间隔
REFRESH_ABORT_GRACE_S = 30.0  # 中止请求后留给分析侧自己在消息边界停下的宽限期，过后硬取消
STREAM_END = "__stream_end__"

# 播报协程被撕掉时，落终态的驱动协程必须继续活着。asyncio 只持弱引用，不留强引用
# 就可能在写完报表终态之前被 GC —— 表现为新版本永远停在"生成中"直到租约过期。
_ACTIVE_REFRESH_RUNS: set = set()


def _sse_block(event: str, data: dict) -> bytes:
    """SSE 一帧（与 Chat 同一形状：event/data 两行 + 空行收尾）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


class _RefreshRun:
    """一次在线重跑：分析侧（``drive``）与播报侧（``stream_report_refresh``）之间只隔一条队列。

    为什么拆成两个协程：客户端一关页面，SSE 协程会被 ASGI 直接撕掉（GeneratorExit）。
    若把"跑分析 + 落终态"吊在那条协程上，报表行就停在 running 等租约过期（10 分钟），
    用户回来只看到"生成超时"—— 中止是用户的选择，不该伪装成平台失败。拆开后落库由
    ``drive`` 自己收尾：播报走了，终态照样写（记为 cancelled + CLIENT_ABORTED）。
    """

    def __init__(self, plan: dict):
        self.plan = plan
        self.queue: "asyncio.Queue" = asyncio.Queue()
        self.cancel_event = threading.Event()
        self.loop = None
        self.response: dict = {}
        self.failure_code = "ANALYSIS_FAILED"
        self.final: dict = {"generation_status": "failed", "stage_error_code": "ANALYSIS_FAILED"}

    # ── 播报侧 ────────────────────────────────────────────────────
    def abort(self) -> None:
        """观看方离开了：请求中止本轮生成（分析侧尽快停下，终态记为已取消）。"""
        self.cancel_event.set()

    async def next_event(self, timeout: float = None):
        """取下一条过程事件；回 None = 这一拍没事发生（播报侧据此心跳并探测断连）。

        ``timeout`` 默认值在运行时取模块常量（写成 ``= REFRESH_TICK_S`` 会把定义时的值绑死，
        测试与后续调参都改不动这个心跳节拍）。
        """
        timeout = REFRESH_TICK_S if timeout is None else timeout
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ── 分析侧 ────────────────────────────────────────────────────
    def publish(self, payload: dict) -> None:
        """过程事件入口：只投队列，不碰 HTTP。

        工具 handler 可能在别的线程里回调采集点，因此统一经 ``call_soon_threadsafe``；
        已经在同一个循环上就直接投（保持顺序，也不多余唤醒）。
        投递失败**不改变**生成结果 —— 报表状态以库里那条写行为准，事件流只是直播，
        但要在日志里留下痕迹，否则"进度面板莫名停住"无从诊断。
        """
        if self.loop is None:
            return
        try:
            same_loop = asyncio.get_running_loop() is self.loop
        except RuntimeError:
            same_loop = False
        try:
            if same_loop:
                self.queue.put_nowait(payload)
            else:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, payload)
        except RuntimeError as exc:
            logger.warning("报表 %s 重跑过程事件投递失败（不影响生成结果）: %s",
                           self.plan.get("report_id"), exc)

    async def _on_queued(self, info: dict) -> None:
        self.publish({"type": "capacity", "event": "queued", **info})

    async def _run(self) -> dict:
        """占一个并发名额 → 跑无人值守分析，返回 analyze_question 的 response。

        与 Chat 共用同一个闸门：一次重跑就是一个 CLI 子进程 + 一次模型请求，
        不走闸门等于让报表中心绕过平台的并发上限。
        """
        from backend.modules.mind.execution import capacity
        from backend.modules.mind.execution.scheduled_analysis import analyze_question
        gate = capacity.get_gate()
        slot = await gate.acquire(self._on_queued)
        try:
            return await analyze_question(self.plan["question"], self.plan["config"],
                                          self.plan["identity"], on_event=self.publish,
                                          cancel_event=self.cancel_event)
        finally:
            gate.release(slot)

    async def _await(self, target) -> str:
        """等 ``target`` 结束，期间盯着中止标志。回 ``'ok' | 'aborted' | 'failed'``。

        为什么不直接 await：分析协程内部在等 SDK 消息流，``cancel_event`` 只在消息边界起作用。
        用户离开是"要停"，不是"等它自己什么时候肯停" —— 宽限期后硬取消，
        不能把"我关了页面"变成"这个报表还在背后跑"。
        """
        from backend.core.task_runtime import RunInterrupted
        from backend.modules.mind.execution import capacity
        grace_deadline = None
        while True:
            done, _pending = await asyncio.wait({target}, timeout=REFRESH_TICK_S)
            if done:
                break
            if not self.cancel_event.is_set():
                continue
            if grace_deadline is None:
                grace_deadline = time.monotonic() + REFRESH_ABORT_GRACE_S
                logger.info("报表 %s 重跑收到中止请求，等本轮在消息边界停下（宽限 %s 秒）",
                            self.plan["report_id"], int(REFRESH_ABORT_GRACE_S))
                continue
            if time.monotonic() >= grace_deadline:
                logger.warning("报表 %s 重跑中止后 %s 秒仍未停下，已强制取消本轮",
                               self.plan["report_id"], int(REFRESH_ABORT_GRACE_S))
                target.cancel()
                await asyncio.wait({target}, timeout=REFRESH_TICK_S)
                return "aborted"
        if target.cancelled():
            return "aborted"
        try:
            self.response = target.result()
        except (RunInterrupted, asyncio.CancelledError):
            return "aborted"
        except capacity.GateIntegrityError as exc:
            # 闸门计数不一致：闸门自己已把可读文案准备好了，原样落到错误码之外的原因里
            self.failure_code = "CAPACITY_UNAVAILABLE"
            logger.error("报表重跑被并发闸门拒接: %s", exc)
            return "failed"
        except (PermissionError, ValueError) as exc:
            self.failure_code = str(getattr(exc, "code", "") or "ASBOT_SCOPE_DENIED")
            return "failed"
        except Exception:
            logger.exception("报表 %s 在线重跑未完成", self.plan["report_id"])
            self.failure_code = "LLM_UNAVAILABLE"
            return "failed"
        return "ok"

    async def _settle(self, response: dict) -> None:
        self.final = await asyncio.to_thread(
            _finalize_agent_report, self.plan["report_id"], self.plan["report"],
            self.plan["source"], response)

    async def _settle_terminal(self, status: str, code: str) -> None:
        """把非产出终态（已取消/未跑起来）写进库；正文留空，不占对象存储。"""
        await asyncio.to_thread(finish_report, self.plan["report_id"],
                               generation_status=status, stage_error_code=code, content="")
        self.final = {"generation_status": status, "stage_error_code": code,
                      "reason": generation_failure_reason({"stage_error_code": code})}

    async def drive(self) -> None:
        """排队 → 分析 → 落终态 → 发 done。无论播报端在不在看，这条链都要走完。"""
        self.loop = asyncio.get_running_loop()
        self.publish({"type": "stage", "step": "prepared", "report_id": self.plan["report_id"],
                      "message": "已在本报告下建好新版本（沿用原版式），正在排队…"})
        try:
            outcome = await self._await(asyncio.create_task(self._run()))
            if outcome == "aborted":
                await self._settle_terminal("cancelled", "CLIENT_ABORTED")
            elif outcome == "failed":
                await self._settle_terminal("failed", self.failure_code)
            else:
                await self._settle(self.response)
        except Exception:      # noqa: BLE001 — 收尾本身炸了也不能让播报端无限等
            logger.exception("报表 %s 重跑收尾失败（新版本状态以库里为准）", self.plan["report_id"])
            self.final = {"generation_status": "failed", "stage_error_code": "REPORT_FINALIZE_FAILED",
                          "reason": generation_failure_reason({"stage_error_code": "ANALYSIS_FAILED"})}
        finally:
            done = dict(self.final)
            done.setdefault("report_id", self.plan["report_id"])
            self.publish({"type": "done", **done})
            self.publish({"type": STREAM_END})


async def _client_disconnected(disconnected) -> bool:
    if disconnected is None:
        return False
    try:
        return bool(await disconnected())
    except Exception as exc:      # noqa: BLE001 — 探不到就当还在：下一次 yield 会把断连暴露出来
        logger.debug("报表重跑的断连探测失败（继续播报，下次下发再判）: %s", exc)
        return False


async def stream_report_refresh(plan: dict, disconnected=None):
    """把 ``prepare_report_refresh`` 备好的那次重跑直播成 SSE；**观看方离开就终止生成**。

    事件（形状与 Chat 一致，前端只消费不解释）：
      ``stage``     阶段说明（已建档、判证据、落库）
      ``capacity``  排队位置（平台并发满员时；与 Chat 同一个闸门）
      ``data``      一条已登记的受治理取数（第几条 / 多少行 / 有无截断 / 通道业务名）
      ``tool_start`` / ``tool_result``  工具步（只有名字、成没成、耗时 —— 不带 SQL 与结果正文）
      ``text``      模型正在写正文（只报字数，不播半截正文）
      ``done``      终态（report_id / generation_status / 版式沿用结果 / 证据摘要 / 未完成原因）

    终局一律以库里的报表行为准：事件流断了、页面关了，都不改变"这一版是什么状态"。
    """
    run = _RefreshRun(plan)
    driver = asyncio.create_task(run.drive())
    _ACTIVE_REFRESH_RUNS.add(driver)
    driver.add_done_callback(_ACTIVE_REFRESH_RUNS.discard)
    try:
        while True:
            event = await run.next_event()
            if event is None:
                if await _client_disconnected(disconnected):
                    logger.info("报表 %s 重跑的页面已断开：本轮生成中止，新版本记为已取消",
                                plan["report_id"])
                    return
                yield b": ping\n\n"      # SSE 注释：穿过网关空闲超时，别让它以为流死了
                continue
            etype = str(event.get("type") or "stage")
            if etype == STREAM_END:
                return
            yield _sse_block(etype, {k: v for k, v in event.items() if k != "type"})
    finally:
        # 走到这里的三种情形：正常发完 done、客户端断开被撕（GeneratorExit）、本协程被取消。
        # 后两种用户已经不看了 —— 那就把"离开"如实转成"停手"，别在背后把一轮取数跑完。
        run.abort()


def _persist_report_question(row: dict, origin: dict, question: str) -> None:
    """把校正后的可复跑问题写回 ``analysis_source.origin``（服务端唯一事实源）。

    不写回的后果是同一份报表两个口径：建任务按新那句跑，「用最新数据重新生成」还按旧那句跑。
    打 ``question_edited`` 标记 = 这句是属主亲手改的，归位/回填不得再按会话事实覆盖它。
    """
    from backend.common.db import execute_write
    source = dict(json_object(row.get("analysis_source")) or {})
    source["origin"] = {**(origin or {}), "question": question,
                        "question_note": "", "question_edited": True}
    dumped = json.dumps(source, ensure_ascii=False, default=str)
    execute_write("UPDATE adh_reports SET analysis_source=%s, updated_at=%s WHERE id=%s",
                  (dumped, _now(), int(row["id"])))
    row["analysis_source"] = dumped


def create_task_from_report(report_id: int, user: dict, *, name: str = "",
                            cron_expression: str = "", notify_on_success: bool = True,
                            notify_on_failure: bool = True,
                            report_template_key: str = "", question: str = "") -> dict:
    """从已有报表快捷创建同款 Agent 定时任务（同一问题正文）。

    任务不携带 AS-BOT 与数据源：每次执行按任务属主（=报表属主）的角色实时继承能力与
    可查范围，授权由 `mind.validate_task_as_bot` 与角色数据源裁决统一判定（fail-closed），
    本函数不自建第二套判断，也不回显任何基础设施标识。

    ``question`` 选填：弹窗里预填报表登记的可复跑问题，**可编辑**（登记的句子口语化或
    归位不准时，属主就地改一句）。传了且与登记值不同就写回报表 origin，让重新生成与
    后续建任务共用同一句；不传则沿用登记值。

    ``report_template_key``：选定报告模板后每次执行都按模板版式出报告（执行器已支持）；
    模板不存在/已下架在建任务时就显式拒，不留“任务里选了个读不到的模板”到运行时才失败。
    """
    row = _load_owned_report(report_id, user)
    # 从报表建任务 = 写动作，权限口径与任务调度页单一同源（无 scheduled:manage 即拒，
    # 不得因为“只是从报表点了一下”而绕过菜单/功能权限码）。
    from backend.core.perm_link import require_write_perm
    require_write_perm(int(user["user_id"]), int(row.get("workspace_id") or 0),
                       "scheduled:manage", "创建定时任务")
    origin = report_origin(row)
    registered = str(origin.get("question") or "").strip()
    # 问题正文允许属主就地校正（弹窗里可编辑）：它是用户自己写的分析诉求，
    # 不是身份/资源标识 —— 护栏 §2 禁的是请求体传 user_id/datasource_id 那类内部事实，
    # 任务调度页新建任务本来就是让人写问题正文的。
    edited = str(question or "").strip()[:_MAX_QUESTION_CHARS]
    question = edited or registered
    if not question:
        raise ValueError("请填写要复用的分析问题：该报告未登记原始分析问题（手写报告或改造前的历史报告），"
                         "填好后每次执行与「用最新数据重新生成」都按这句取数")
    cron = str(cron_expression or "").strip()
    if not cron:
        raise ValueError("请填写执行周期（Cron 表达式）")
    # 任务域能力经 core/task_runtime 回调表取（白名单②），不开 viz→flow 的 L2 横向边
    from backend.core.task_runtime import call, get_callback
    call("task.parse_cron", cron)   # 不合法直接抛 ValueError，由 API 层回 422
    task_name = (str(name or "").strip() or f"{row.get('title') or '报告'} 定时报告")[:200]
    tpl_key = str(report_template_key or "").strip()
    if tpl_key:
        tpl = resolve_report_template(tpl_key)
        if not tpl.get("found"):
            raise ValueError(tpl.get("reason") or "报告模板不可用")
    # 校验全过才写回：周期/模板不合法时不得留下"报表问题已被改、任务却没建成"的半截状态
    if edited and edited != registered:
        _persist_report_question(row, origin, edited)
    data = {
        "name": task_name,
        "description": f"由报告「{row.get('title') or ''}」快捷创建",
        "task_type": "agent",
        "task_config": {
            "questions": [{"title": str(row.get("title") or "分析报告"), "question": question}],
            # 不带 as_bot_key/datasource_id：能力与可查范围每次执行按属主角色重解；
            # 报表当时的站点作为默认范围带走（旧报表无站点时为空，走自动/逐站）。
            "site_code": str(origin.get("site_code") or ""),
        },
        "cron_expression": cron,
        "timezone": "Asia/Shanghai",
        # 报告模板 key 与任务同表存储（与任务调度页新建任务同一字段），执行器跑前解析成版式契约
        "report_template_key": tpl_key or None,
        "notify_on_success": notify_on_success,
        "notify_on_failure": notify_on_failure,
        "is_active": True,
        "timeout_seconds": 600,
        "max_retries": 0,
    }
    task_id = get_callback("task.scheduled_service").create_task(
        data=data, owner_id=int(user["user_id"]), workspace_id=int(row.get("workspace_id") or 0))
    return {"task_id": task_id, "name": task_name}


def submit_report(request: dict, user: dict, background_tasks) -> dict:
    """先保存可追踪的运行，再真实派发；禁止无来源占位报告。"""
    from uuid import uuid4
    from fastapi import HTTPException
    req = ReportSubmission.model_validate(request)
    authorize_workspace(user, req.workspace_id)
    run_id = uuid4().hex
    submission = req.model_dump()
    # template_key 早就留在契约里但一直没人消费：现在解析成版式契约随运行带走。
    # 模板读不到就在提交时显式拒，不留一张"以为按模板生成、其实是通用事实表"的报表（§1）。
    tpl_key = str(submission.get("template_key") or "").strip()
    if tpl_key:
        tpl = resolve_report_template(tpl_key)
        if not tpl.get("found"):
            raise HTTPException(status_code=400, detail=tpl.get("reason") or "报告模板不可用")
        submission.update({"template_name": tpl.get("name") or "", "layout": tpl.get("layout") or {},
                           "report_theme": tpl.get("theme") or ""})
    report = create_report(title=req.title, content="", workspace_id=req.workspace_id,
        owner_id=user["user_id"], run_key=run_id, generation_status="queued",
        analysis_source=submission, source_type="analysis")
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
    from backend.common.db import execute_query, execute_write
    # format：重跑开头按原版式建档（html 报表重跑仍建 html），产出退回事实表格时要能改回
    # markdown；不随产出刷新就会留下"库说自己是 HTML、正文却是 Markdown"的假格式。
    # analysis_source：把版式沿用结果（layout_result）与任务定义存在同一份 JSON 里，不开第二列。
    allowed = {"generation_status", "stage_error_code", "content", "security_context",
               "evidence_summary", "format", "analysis_source"}
    # cancelled=任务监控人工停止；与其它终态一样受 `generation_status IN ('queued','running')` 保护，迟到结果不可覆盖。
    if set(values) - allowed or values.get("generation_status") not in ("ready", "degraded", "failed", "timeout", "cancelled"):
        raise ValueError("报告更新字段或终态无效")
    if "format" in values and values["format"] not in CONTENT_FORMATS:
        raise ValueError("报告正文格式无效")
    uploaded_key = ""
    if "content" in values:
        # 正文出库：按属主+报表 id 定 key，库里只跟 key/字节数（content 列恒写空串）
        row = execute_query("SELECT owner_id, format FROM adh_reports WHERE id=%s",
                            (report_id,), fetchone=True)
        if not row:
            raise ValueError("报告记录不存在，无法写入正文")
        uploaded_key, size = put_report_content(report_id, int(row["owner_id"]),
                                               values.get("format") or row.get("format") or "markdown",
                                               values["content"])
        values["content"] = ""
        values["content_object_key"] = uploaded_key
        values["content_size"] = size
    updates = ",".join(f"`{key}`=%s" for key in values)
    params = tuple(json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, dict) else v
                   for v in values.values()) + (report_id,)
    done = execute_write(f"UPDATE adh_reports SET {updates}, "
        "generation_status=IF(lease_expires_at<=NOW(),'timeout',generation_status), "
        "content=IF(lease_expires_at<=NOW(),'',content), "
        "content_object_key=IF(lease_expires_at<=NOW(),'',content_object_key), "
        "stage_error_code=IF(lease_expires_at<=NOW(),'TIMEOUT',stage_error_code) WHERE id=%s "
        "AND generation_status IN ('queued','running')", params) == 1
    if done and uploaded_key:
        # 租约在上传途中过期 → 上面的 IF 把 key 清了，刚上传的对象没人引用：收走，不留孤儿
        stored = execute_query("SELECT content_object_key FROM adh_reports WHERE id=%s",
                               (report_id,), fetchone=True) or {}
        if str(stored.get("content_object_key") or "") != uploaded_key:
            content_storage().delete(uploaded_key)
    return done


def _finalize_agent_report(report_id: int, report: dict, source: dict, response: dict) -> dict:
    """一次无人值守重跑的**唯一收口**：把产出落成报表终态，并回终态摘要给播报侧。

    正文优先级：**模型终稿（沿用原版式）> 事实表格**。旧写法不分情况一律只存
    `render_fact_report`，把模型写的叙述与 ```chart 图表整段丢掉了，报表中心里同一份
    报表的新一代与上一代看上去就是两份不同的东西（用户反馈："数据是新的，但不是原本报表的样式"）。

    终态按事实判（`evidence_verdict`：有已登记的受治理取数 + 正文非空 = ready），
    不再一律写死 degraded；"有没有沿用原版式"是另一件事，记在 `layout_result` 里
    并让用户看得到原因（§1：退回事实表格可以，但要说清是退回）。

    只在这里写终态：在线流式重跑与任何后续产出方都调它，不在别处再拼一套判断。
    回 ``{generation_status, stage_error_code, layout_result, evidence, ...}``。
    """
    if response.get("status") != "success":
        code = str(response.get("error_code") or "ANALYSIS_FAILED")
        finish_report(report_id, generation_status="failed", content="", stage_error_code=code)
        return {"generation_status": "failed", "stage_error_code": code,
                "reason": generation_failure_reason({"stage_error_code": code})}
    layout = source.get("layout") if isinstance(source.get("layout"), dict) else {}
    results = [item for item in (response.get("_analysis_results") or [response])]
    security = next((r.get("_security_context") for r in results
                     if isinstance(r, dict) and r.get("_security_context")), {}) or {}
    facts = render_fact_report(report["title"], results)
    content, reasons = _body_for_layout(response.get("answer"), layout, source)
    if content:
        fmt, layout_status = str(layout.get("format") or "markdown"), "preserved"
    else:
        # 不达契约就退回事实表格：格式跟着正文换，不留"库说自己是 HTML、正文是 Markdown"的假格式
        content, fmt, layout_status = facts, "markdown", "fact_only"
    status, evidence = evidence_verdict(results, content=content)
    stage = "" if layout_status == "preserved" else "FACT_ONLY"
    layout_result = {"status": layout_status, "format": fmt, "reasons": reasons[:5]}
    finish_report(report_id, generation_status=status, content=content, format=fmt,
                  security_context=security, evidence_summary=evidence, stage_error_code=stage,
                  analysis_source={**source, "layout_result": layout_result})
    return {"generation_status": status, "stage_error_code": stage, "format": fmt,
            "layout_result": layout_result, "evidence": evidence,
            "query_count": int(evidence.get("query_count") or 0),
            "reason": "" if status == "ready" else str(
                (evidence.get("reasons") or [""])[0])}


def _body_for_layout(answer, layout: dict, source: dict) -> tuple:
    """模型终稿整形为可入库的报表正文，回 ``(正文, 拒绝原因)``（正文空 = 用事实表格）。

    四道门：有结构可沿 → 有终稿 → 过出站脱敏 → 剥完围栏后与原版式无结构偏差。任一道不过
    都不是“将就着用”，而是显式记下原因（§1：降级可以，但不把“没沿用版式”掩成“新报表天然长这样”）。
    """
    if not has_report_layout(layout):
        return "", [str(source.get("layout_error") or "上一版报告没有可沿用的版式")]
    text = str(answer or "").strip()
    if not text:
        return "", ["分析模型本轮未交回报告正文（只拿到取数结果）"]
    if outbound_guard_hits(text):
        return "", ["模型终稿未通过出站脱敏检查，不采用为报告正文"]
    fmt = str(layout.get("format") or "markdown")
    body, reason = unwrap_document_fence(text, fmt)
    if reason:
        return "", [reason]
    reasons = layout_deviation(body, layout)
    return ("", reasons) if reasons else (body, [])


# ── 报表证据判定：ready / degraded 的唯一口径（生成侧写，前端只消费）────────────
def evidence_verdict(results: list, *, content: str) -> tuple[str, dict]:
    """按**事实**判报表终态与证据摘要，回 ``(generation_status, evidence_summary)``。

    历史缺陷：``validation_status`` 全仓从没被写成过 verified（证据校验实现根本不存在），
    三处生成出口却一律硬编码 ``degraded`` +「仅展示已授权查询返回的样本，尚未完成证据校验，
    不可对外发布」。结果数据是新的、版式也沿用了、取数逐条过了治理的报表，照样挂着
    「降级事实报告」——永久假降级和永久假 ready 一样有害，它把"这份能不能信"变成没人当真的套话。

    口径（只在这一处判，前端与执行器都不得再拼一套）：
      ready    = 至少一条**经治理登记的取数结果**（无人值守 run 级采集点逐条带来源权限快照，
                 快照在执行前已 verify）+ 正文非空；
      degraded = 上述任一不成立，``reasons`` 逐条写清缺哪一项（可见、可诊断，不掩）。
    两件与"能不能信"相关但**不降级**的事实另记：
      completeness：任一结果被截断 → 'sample_only'（样本，不代表总体），否则 'as_queried'；
      版式沿用结局另存 ``layout_result``（本函数不参与，退回事实表格不等于证据不全）。
    出站脱敏不在这里重复判：模型终稿在 `_body_for_layout` 已过闸，事实表格的行来自受治理结果。
    """
    items = [r for r in (results or []) if isinstance(r, dict)]
    governed = [r for r in items if str(r.get("status") or "success") == "success"
                and (r.get("columns") or r.get("rows") is not None)]
    reasons = []
    if not governed:
        reasons.append("本轮没有已登记的受治理取数结果，正文里的数字缺少出处")
    if not str(content or "").strip():
        reasons.append("正文为空（模型未交稿且无取数结果可落）")
    sources = sorted({str(r.get("source") or "").split(":", 1)[0] for r in governed} - {""})
    return ("ready" if not reasons else "degraded"), {
        "validation_status": "governed" if not reasons else "unverified",
        "query_count": len(governed),
        "sources": sources,
        "completeness": "sample_only" if any(r.get("truncated") for r in governed) else "as_queried",
        "reasons": reasons,
    }


# ── 报告模板 → 统一版式契约（定时任务 / 问数出报告 / 报表中心共用一份口径）──────────
# 单一真值源 = 字模库 report_template（编辑页与任务表单都选它，见 ScheduledTaskForm）；
# 遗留 adh_report_templates 退役（Phase 6）。这里把模板解析成与报表重跑同形状的版式契约，
# 让「模板、skills、定时任务、报表中心」说同一种语言，而不是各造一套。
def resolve_report_template(template_key: str) -> dict:
    """把报告模板解析成版式契约，回 ``{found, name, format, theme, layout, body}``。

    缺失/下架/读不到一律回 ``found=False`` + 原因：调用方必须显式处理（定时任务据此判失败），
    **不得**静默退回通用事实表冒充“已按模板生成”（no-silent-degradation §1）。
    旧模板只有 Jinja2 正文（无 layout）时从正文抽版式骨架，新老模板收敛到同一形状。
    """
    key = str(template_key or "").strip()
    if not key:
        return {"found": False, "reason": "未指定报告模板"}
    from backend.modules.viz.services.vis_library_service import is_usable, vis_library_service
    try:
        comp = vis_library_service.get_component(int(key))
    except (ValueError, TypeError):
        comp = vis_library_service.get_component_by_code(key)
    except Exception as exc:      # 库不可用：可诊断的失败，不静默
        logger.warning("报告模板读取失败 key=%s: %s", key, exc)
        return {"found": False, "reason": f"报告模板读取失败（{key}）"}
    if not comp or comp.get("category") != "report_template":
        return {"found": False, "reason": f"报告模板不存在（{key}）"}
    if not is_usable(comp):
        # 下架/禁用都算不可用：管理员在 UI 字模库停用模板后，定时任务不能照用那份版式
        state = "已禁用" if int(comp.get("is_active") or 0) != 0 else "已下架"
        return {"found": False, "reason": f"报告模板「{comp.get('name') or key}」{state}，不可使用"}
    sc = comp.get("style_config")
    if isinstance(sc, str):
        try:
            sc = json.loads(sc or "{}")
        except (ValueError, TypeError):
            sc = {}
    sc = sc if isinstance(sc, dict) else {}
    fmt = str(sc.get("format") or "markdown")
    theme = str(sc.get("theme") or "")
    layout = sc.get("layout") if isinstance(sc.get("layout"), dict) else None
    # 新模板直读登记好的版式（手工编的也要过清洗）；旧模板只有 Jinja2 正文 → 从正文抽版式骨架
    layout = _normalize_layout(layout, fmt) if layout is not None \
        else extract_report_layout(str(sc.get("content") or ""), fmt)
    return {"found": True, "name": str(comp.get("name") or ""), "format": fmt,
            "theme": theme, "layout": layout, "body": str(sc.get("content") or "")}


def resolve_report_template_by_name(name: str) -> dict:
    """按模板**业务名**解析（给 AI 工具侧用）：不得要求模型传它看不到的字模 id。

    找不到时把可用清单一起带回（模糊命中只当候选提示、**不自动绑定**，宁缺勿错）：
    调用方据此给出可操作错误，而不是"生成失败请重试"。
    """
    want = str(name or "").strip()
    if not want:
        return {"found": False, "reason": "未指定报告模板", "available": []}
    from backend.modules.viz.services.vis_library_service import vis_library_service
    try:
        rows = [c for c in vis_library_service.list_components("report_template")
                if int(c.get("is_active") or 0) == 1]
    except Exception as exc:      # 清单读不到：可诊断失败，不静默当"没有模板"继续跑
        logger.warning("报告模板清单读取失败: %s", exc)
        return {"found": False, "reason": "报告模板清单读取失败，请稍后重试", "available": []}
    names = [str(c.get("name") or "").strip() for c in rows if str(c.get("name") or "").strip()]
    hit = next((c for c in rows if str(c.get("name") or "").strip() == want), None) \
        or next((c for c in rows if str(c.get("code") or "") == want), None)
    if not hit:
        hints = [n for n in names if want in n or n in want]
        return {"found": False, "reason": f"报告模板「{want}」不存在或已下架",
                "available": (hints or names)[:8]}
    return dict(resolve_report_template(str(hit["id"])), template_key=str(hit["id"]))


def render_template_report(title: str, results: list, answer, layout: dict, theme: str = "",
                           single_question: bool = True) -> tuple:
    """定时任务按报告模板出正文，回 ``(正文, 格式, layout_result)``。

    与报表中心「用最新数据重新生成」同一套版式校验（``_body_for_layout``）：模型终稿达标就用它
    （带叙述与 ```chart 图表），不达标就退回确定性事实表格并把原因带在 layout_result 上（可见，
    不静默）。``single_question`` 由调用方传：多问题任务里逐问终稿不等于整份报表，只有单问才用终稿。
    """
    facts = render_fact_report(title, results)
    content, reasons = ("", ["本轮为多问题任务，按模板版式以事实表格汇总"])
    if single_question:
        content, reasons = _body_for_layout(answer, layout or {}, {"layout_error": "模板未登记可沿用版式"})
    if content:
        return content, str((layout or {}).get("format") or "markdown"), \
            {"status": "preserved", "format": str((layout or {}).get("format") or "markdown"), "reasons": []}
    return facts, "markdown", {"status": "fact_only", "format": "markdown", "reasons": reasons[:5]}


# ── 模板的使用面：沉淀（报表→模板）/ 套用（模板→骨架）/ 预览（与真实渲染同一管线）──────
# 版式只在上文一处抽取、只在这里一处生成：配置页预览、报表中心「另存为模板/套用模板」、
# 定时任务的输出契约说的都是同一份 layout，不再各写一套“模板语义”。
_SKELETON_HINT = "（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）"
_SKELETON_SAMPLE_LABELS = ("示例甲", "示例乙", "示例丙")


def _skeleton_sample_rows(columns: list) -> list:
    """预览用示例行（首列当维度、其余当数值）。

    **只给预览**：模板资产本身不存数据，套用到真报表时 rows 必须回空，否则真报表看着
    有数、其实全是模板里冻结的旧值（§1 要防的那类假成功）。
    """
    cols = [str(c) for c in (columns or [])]
    if len(cols) < 2:
        return []
    rows = []
    for i, label in enumerate(_SKELETON_SAMPLE_LABELS):
        rows.append([label] + [18 + i * 8 + j * 5 for j in range(len(cols) - 1)])
    return rows


def _normalize_layout(layout: dict, fmt: str = "") -> dict:
    """把版式契约洗成可校验形状（配置页手工编辑的版式也只走这一处，不另写一套判断）。

    缺 ``chart_type`` 或 ``columns`` 的图表直接丢：形状不完整的块没法当“同一张图”去校验，
    留着只会让重跑/任务永远判不达标（与 ``extract_report_layout`` 同一取舍）。
    """
    src = layout if isinstance(layout, dict) else {}
    format_ = str(src.get("format") or fmt or "markdown")
    headings = [_clean_heading(h) for h in (src.get("headings") or [])]
    charts = []
    for item in (src.get("charts") or []):
        if not isinstance(item, dict):
            continue
        chart_type = str(item.get("chart_type") or "").strip()
        columns = [str(c).strip() for c in (item.get("columns") or []) if str(c).strip()]
        if not chart_type or not columns:
            continue
        charts.append({"title": _clean_heading(item.get("title") or ""),
                       "chart_type": chart_type, "columns": columns})
    return {"format": format_, "headings": [h for h in headings if h][:_LAYOUT_MAX_HEADINGS],
            "charts": charts[:_LAYOUT_MAX_CHARTS]}


_SKELETON_HTML_STYLE = """
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: var(--bg); color: var(--fg);
         font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial,
                      "PingFang SC", "Microsoft YaHei", sans-serif; line-height: 1.7; }
  h1, h2, h3, h4 { color: var(--fg); line-height: 1.35; }
  h2 { font-size: 18px; margin: 24px 0 8px; padding-left: 10px;
       border-left: 3px solid var(--primary); }
  h3 { font-size: 16px; margin: 18px 0 6px; }
  h4 { font-size: 14px; margin: 14px 0 4px; }
  p { margin: 8px 0; }
  .skeleton-hint { color: var(--muted-fg); font-size: 13px; }
  .chart-slot { margin: 12px 0; padding: 12px 14px; background: var(--card);
                border: 1px solid var(--border); border-radius: var(--card-radius, 8px);
                color: var(--muted-fg); font-size: 13px; }
""".strip()


def _skeleton_html_head(theme: Optional[str]) -> list:
    """HTML 骨架的 ``<head>``：样式规则恒定，**色值只在渲染面内联**。

    历史缺陷（用户实拍：配置页「预览」一片黑白）：骨架只写了 ``<h2>/<p>``，没有任何
    ``<style>``，而 HTML 报表是在隔离 iframe 里渲染的 —— 主文档的主题变量进不去，
    于是按浏览器 UA 默认样式出图，看着像"模板没带样式"。真实 HTML 报表有样式，是因为
    模型按提示词「报告主题令牌」自己内联了 ``:root``。这里补的是同一份令牌（唯一口径
    ``report_themes.theme_css_block``，此前生产侧零消费）。

    ``theme=None`` 只给 ``var(--x)`` 规则、不落色值：模板资产不保存样式快照（改字模库
    要即时生效）；``theme="<id>"`` 供预览/套用响应内联 ``:root`` 值快照，与 AI 生成的
    报表同一形态。
    """
    parts = ['<head><meta charset="utf-8">', '<title>报告标题</title>', '<style>']
    if theme is None:
        parts.append('  /* 令牌值由渲染方按查看者主题注入 :root；模板资产不保存色值 */')
    else:
        # 延迟导入：viz → mind 的顶层横向 import 是层约束硬禁项（test_layering_contract）
        from backend.modules.mind.execution import report_themes
        parts.append(report_themes.theme_css_block(theme).replace("\n", "\n  "))
    parts += [_SKELETON_HTML_STYLE, '</style>', '</head>', '<body>']
    return parts


def template_skeleton_from_layout(layout: dict, sample: bool = False,
                                  theme: Optional[str] = None) -> str:
    """按版式契约产出一份报表骨架正文。

    ``sample=True`` 仅配置页预览用：图表块带明确标注的示例行，字模样式才能在预览里看得见
    （预览与真实渲染同一管线，否则“配了不知道长什么样”）。``sample=False``（默认）用于
    套用模板新建报表与另存为模板：只给结构，一个数字都不塞。

    ``theme``：HTML 骨架的色值内联口径（见 ``_skeleton_html_head``）；``None``=不落色值。

    layout 不记录图表挂在哪个章节下（抽取时只留结构），因此图表统一列在章节之后；
    这是骨架而不是原样副本，套用时由人/模型把图放回合适章节。
    """
    layout = layout or {}
    fmt = str(layout.get("format") or "markdown")
    headings = [str(h) for h in (layout.get("headings") or [])]
    charts = [c for c in (layout.get("charts") or []) if isinstance(c, dict)]
    if fmt == "html":
        parts = ['<!doctype html>', '<html lang="zh-CN">'] + _skeleton_html_head(theme)
        for h in (headings or ["报告标题"]):
            parts.append(f"<h2>{h}</h2>")
            parts.append(f'<p class="skeleton-hint">{_SKELETON_HINT}</p>')
        for spec in charts:
            cols = " / ".join(str(c) for c in (spec.get("columns") or []))
            parts.append(f'<div class="chart-slot">图表：{spec.get("chart_type")}（{cols}）'
                         '—— 出图样式由系统字模库主题决定</div>')
        parts += ["</body>", "</html>"]
        return "\n".join(parts)
    lines = ["# 报告标题", ""]
    for h in (headings or ["报告概述"]):
        lines += [f"## {h}", "", _SKELETON_HINT, ""]
    if charts:
        lines += ["## 图表", ""]
        for idx, spec in enumerate(charts):
            cols = [str(c) for c in (spec.get("columns") or [])]
            block = {"title": str(spec.get("title") or "") or f"图表 {idx + 1}",
                     "chart_type": spec.get("chart_type"), "columns": cols,
                     "rows": _skeleton_sample_rows(cols) if sample else []}
            lines += [f"### {block['title']}", "", "```chart",
                      json.dumps(block, ensure_ascii=False), "```", ""]
        if sample:
            lines += ["> 预览中的示例甲/乙/丙只为看字模样式，模板本身不保存任何数值。", ""]
    return "\n".join(lines).rstrip() + "\n"


def analyze_report_layout(content: str, fmt: str = "markdown", layout: dict = None,
                          theme: str = "") -> dict:
    """配置页的版式预览与抽取入口：手工编过的版式以它为准，没编过就从正文抽。

    两条路都走全平台同一个口径（``extract_report_layout`` / ``_normalize_layout``），
    前端不自己解析 markdown：预览一个样、生成另一个样就是本类缺陷的历史形态。
    """
    resolved = (_normalize_layout(layout, fmt) if isinstance(layout, dict)
                else extract_report_layout(content, fmt))
    return {"format": str(resolved.get("format") or "markdown"), "layout": resolved,
            "has_layout": has_report_layout(resolved),
            # 预览要看得见样式：按传入主题内联令牌（HTML 在 iframe 里渲染，主文档变量进不去）
            "skeleton": template_skeleton_from_layout(resolved, sample=True, theme=theme)}


def template_skeleton_by_key(template_key: str, theme: str = "") -> dict:
    """按模板取骨架正文（报表中心「套用模板」起稿），回 ``{found, name, format, theme, layout, skeleton}``。

    ``theme`` 由调用方传查看者当前主题（模板自己锁了主题时以模板为准，见 API 层）：
    骨架新建的报表第一版就该带样式，不能等用户重跑一次才长对。
    """
    tpl = resolve_report_template(template_key)
    if not tpl.get("found"):
        return tpl
    # 模板自己锁了主题就以它为准（那是这份资产的语义），没锁才用调用方传的查看者主题
    effective = str(tpl.get("theme") or theme or "")
    return {**tpl, "skeleton": template_skeleton_from_layout(tpl.get("layout") or {}, theme=effective)}


def save_report_as_template(report_id: int, user: dict, *, name: str = "",
                            description: str = "") -> dict:
    """把一份报表的版式沉淀成报告模板（报表中心=生产者，配置页/任务表单=消费者）。

    只搬结构（章节 + 图表规格 + 主题），正文里的数字一个都不带：模板是版式资产不是数据快照。
    没有可沉淀结构的报表显式拒——配出一张看得见却挂不上的空模板比拒更坏（§1）。
    """
    row = _load_owned_report(report_id, user)
    fmt = str(row.get("format") or "markdown")
    try:
        body = read_report_content_bytes(row).decode("utf-8", "replace")
    except (LookupError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    layout = extract_report_layout(body, fmt)
    if not has_report_layout(layout):
        raise ValueError("该报告正文没有可沉淀的版式结构（章节标题或 ```chart 图表）："
                         "先在报告里把章节和图表补齐，再另存为模板")
    source = json_object(row.get("analysis_source")) or {}
    origin = source.get("origin") if isinstance(source.get("origin"), dict) else {}
    theme = str(source.get("report_theme") or origin.get("theme") or "")
    title = str(row.get("title") or "").strip()
    tpl_name = (str(name or "").strip() or f"{title or '报告'} 版式模板")[:100]
    from backend.modules.viz.services.vis_library_service import vis_library_service
    cid = vis_library_service.create_component(
        {"name": tpl_name, "category": "report_template",
         "style_config": {"format": fmt, "theme": theme, "layout": layout,
                          "content": template_skeleton_from_layout(layout)},
         "description": str(description or "").strip() or f"由报告「{title}」沉淀",
         "is_builtin": False},
        int(user["user_id"]), bool(user.get("role") == "admin"))
    return {"component_id": cid, "name": tpl_name, "format": fmt, "theme": theme, "layout": layout}


# ── 模板中心：报告模板的读写口（存储仍是字模库 report_template，这里只收口 HTTP/权限面）──
# 为什么要单独开口，而不是让配置页继续直接调 /api/vis-library：权限点 report-template:* 过去
# 绑的是已退役的 /api/report-templates*，页面实际靠 vis:read/manage 才能打开 —— 角色页勾
# 「查看报告模板」控不住这个页面（menu-permission-sync §2 的空承诺形态）。收口后
# 菜单 / 权限 / 页面三者同源，字模库只作为存储实现退到幕后。
def _template_usage() -> dict:
    """``{引用键: 任务数}``：报告模板被多少定时任务引用（id 与 code 两种存法都算）。

    不是装饰：删掉正在被引用的模板，那些任务会到运行时才失败；删除门禁必须与
    建任务校验（``create_task_from_report``）同一口径，否则就是"建时拦、删时放"。
    """
    from backend.common.db import execute_query
    usage: dict = {}
    rows = execute_query(
        "SELECT report_template_key AS k, COUNT(*) AS c FROM adh_scheduled_tasks "
        "WHERE report_template_key IS NOT NULL AND report_template_key <> '' "
        "GROUP BY report_template_key") or []
    for r in rows:
        key = str(r.get("k") or "")
        if key:
            usage[key] = usage.get(key, 0) + int(r.get("c") or 0)
    return usage


def list_report_templates(include_inactive: bool = False) -> list:
    """报告模板清单：配置页表格、任务表单与报表中心「套用模板」下拉的同一来源。"""
    from backend.modules.viz.services.vis_library_service import vis_library_service
    usage = _template_usage()
    out = []
    for comp in vis_library_service.list_components("report_template",
                                                    include_inactive=include_inactive):
        if comp.get("category") != "report_template":
            continue
        sc = comp.get("style_config") or {}
        if isinstance(sc, str):
            try:
                sc = json.loads(sc or "{}")
            except (ValueError, TypeError):
                sc = {}
        fmt = str(sc.get("format") or "markdown")
        registered = isinstance(sc.get("layout"), dict)
        layout = (_normalize_layout(sc["layout"], fmt) if registered
                  else extract_report_layout(str(sc.get("content") or ""), fmt))
        tid, code = str(comp["id"]), str(comp.get("code") or "")
        out.append({
            "id": tid, "code": code, "name": str(comp.get("name") or ""),
            "format": fmt, "theme": str(sc.get("theme") or ""),
            "description": str(comp.get("description") or ""),
            "is_builtin": bool(comp.get("is_builtin")),
            "is_active": int(comp.get("is_active") or 0),
            # 版式是"登记过的"还是"从正文抽的"：配置页要能区分（旧模板只有正文）
            "layout_registered": registered,
            "has_layout": has_report_layout(layout),
            "headings": layout.get("headings") or [],
            "charts": layout.get("charts") or [],
            "content": str(sc.get("content") or ""),
            "content_preview": str(sc.get("content") or "")[:240],
            # 编辑对话框要回填完整正文：只给 preview 的话，用户改一下版式再保存就会把正文截断写回去
            "used_by_tasks": usage.get(tid, 0) + (usage.get(code, 0) if code else 0),
            "updated_at": str(comp.get("updated_at") or ""),
        })
    return out


def save_report_template(payload: dict, user: dict, template_id: int = 0) -> dict:
    """新建/更新报告模板：只存版式契约（章节 + 图表规格 + 主题 + 骨架），不存数据。

    服务端也挡一次"配了却不生效"：版式既没章节也没图表规格时直接拒 —— 前端挡一次不算数，
    换个入口（或前端漏判）就会留下一张看得见却约束不了任何东西的模板。
    """
    from backend.modules.viz.services.vis_library_service import vis_library_service
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("请输入模板名称")
    fmt = str(payload.get("format") or "markdown")
    if fmt not in ("markdown", "html"):
        raise ValueError(f"不支持的输出格式: {fmt}（可选 markdown / html）")
    theme = str(payload.get("theme") or "")
    layout = _normalize_layout(payload.get("layout") or {}, fmt)
    if not has_report_layout(layout):
        raise ValueError("版式里没有可校验的章节或图表规格：这样保存的模板在任务里看得见却不生效。"
                         "请在「版式」里补章节（或先从正文抽版式）")
    content = str(payload.get("content") or "").strip() or template_skeleton_from_layout(layout)
    data = {"name": name[:128], "category": "report_template",
            "description": str(payload.get("description") or "")[:512],
            "style_config": {"format": fmt, "theme": theme, "layout": layout, "content": content},
            "is_builtin": bool(payload.get("is_builtin"))}
    uid = int((user or {}).get("user_id") or 0)
    is_admin = bool((user or {}).get("role") == "admin")
    if template_id:
        vis_library_service.update_component(int(template_id), data, uid, is_admin)
        return {"id": int(template_id), "layout": layout}
    return {"id": vis_library_service.create_component(data, uid, is_admin), "layout": layout}


def delete_report_template(template_id: int, user: dict) -> dict:
    """删除前查任务引用：被引用的模板不拦就会让任务跑到运行时才失败 —— 直接拒并给数量。"""
    from backend.modules.viz.services.vis_library_service import vis_library_service
    comp = vis_library_service.get_component(int(template_id))
    if not comp or comp.get("category") != "report_template":
        raise ValueError("报告模板不存在或已删除")
    usage = _template_usage()
    hits = usage.get(str(template_id), 0) + usage.get(str(comp.get("code") or ""), 0)
    if hits:
        raise ValueError(f"该模板正被 {hits} 个定时任务引用：请先把这些任务改选其它模板（或下架本模板），"
                         "再删除 —— 直接删会让任务跑到执行时才失败")
    vis_library_service.delete_component(int(template_id),
                                         int((user or {}).get("user_id") or 0),
                                         bool((user or {}).get("role") == "admin"))
    return {"success": True}


def report_theme_options() -> list:
    """主题包选项（配置页主题下拉的唯一来源）。

    页面不再为了拿主题清单去调 /api/vis-library —— 那会让「模板中心」的可见性
    依赖 vis:* 权限，与 report-template:* 的菜单勾选脱节（本次要修的漂移）。
    """
    from backend.modules.viz.services.vis_library_service import vis_library_service
    out = []
    for comp in vis_library_service.list_components("theme_pack"):
        code = str(comp.get("code") or "")
        if comp.get("category") != "theme_pack":
            continue
        out.append({"code": code, "theme": code[3:] if code.startswith("tp_") else code,
                    "name": str(comp.get("name") or code)})
    return out


def outbound_guard_hits(text: str) -> list:
    """出站脱敏命中列表（单独一个函数是为了给测试留缝，不在此处做第二套脱敏判断）。"""
    from backend.modules.mind.execution import outbound_guard
    return outbound_guard.find_sensitive_hits(text)


def run_report(report_id):
    from billiard.exceptions import SoftTimeLimitExceeded
    from backend.core.task_runtime import RunInterrupted
    report = load_report(report_id)
    if not report or not claim_report(report_id):
        return {"report_id": report_id, "duplicate": True}
    try:
        identity = resolve_execution_owner(report["owner_id"], report["workspace_id"])
        source = json_object(report["analysis_source"])
        if source.get("kind") == "agent_question":
            # 报表「用最新数据重新生成」已改到报表页同一条 SSE 连接里跑完（过程可见、离开即终止），
            # 队列侧不再代跑这一类来源。这里只兜底历史遗留：改造前已入队的行若被重投递到这里，
            # 显式判失败并给出去路，不拿快照通道去跑一个根本没有 SQL 的问题（§1 不静默）。
            finish_report(report_id, generation_status="failed", content="",
                          stage_error_code="REFRESH_STREAM_REQUIRED")
            return {"report_id": report_id}
        from backend.core.task_runtime import guarded_call
        result = guarded_call(execute_report_source, source, identity,
                              deadline=time.monotonic() + 600)
        content = render_fact_report(report["title"], [result])
        # 快照通道（数据集/语义查询/已存查询）只产数据不产模型终稿：套版式无从校验，
        # 必须把"没按模板版式"显式记下来给用户看，不得冒充已按模板生成（§1）。
        layout = source.get("layout") if isinstance(source.get("layout"), dict) else {}
        extra = {}
        if str(source.get("template_key") or "") and has_report_layout(layout):
            extra["layout_result"] = {
                "status": "fact_only", "format": "markdown",
                "reasons": [f"报告模板「{source.get('template_name') or source.get('template_key')}」的版式未套用："
                            "本报告的来源是受治理取数快照通道，没有模型终稿可套结构；"
                            "模板版式对定时任务与报告「用最新数据重新生成」生效"][:5]}
        status, evidence = evidence_verdict([result], content=content)
        finish_report(report_id, generation_status=status, content=content,
            security_context=result.get("_security_context") or {},
            evidence_summary=evidence, stage_error_code="" if status == "ready" else "FACT_ONLY",
            analysis_source={**source, **extra} if extra else source)
    except (RunInterrupted, TimeoutError, SoftTimeLimitExceeded):
        finish_report(report_id, generation_status="timeout", stage_error_code="TIMEOUT")
    except Exception:
        logger.exception("报告 %s 生成失败", report_id)
        finish_report(report_id, generation_status="failed", stage_error_code="QUERY_FAILED", content="")
    return {"report_id": report_id}


def cleanup_stale_reports():
    """把租约过期的排队/生成中报表收成 timeout（beat 周期兜底，认领后没跑完的实例）。

    正文列与对象 key 一起清、对象一起收：只清 content 会留下"状态是超时、正文却还指着
    一个对象"的半截行；漏删对象则每次超时都攒一个没人引用的正文。正常情况下这两列本来就是
    空的（正文只在终态写入），这里按事实兜底，不把"不会发生"当不变量。
    """
    from backend.common.db import execute_query, execute_write
    stale = execute_query(
        "SELECT id, content_object_key FROM adh_reports WHERE "
        "generation_status IN ('queued','running') AND lease_expires_at<=NOW() "
        "AND content_object_key<>''")
    for row in stale or []:
        content_storage().delete(str(row["content_object_key"]))
    return execute_write(
        "UPDATE adh_reports SET generation_status='timeout', stage_error_code='TIMEOUT', "
        "content='', content_object_key='', content_size=0 "
        "WHERE generation_status IN ('queued','running') AND lease_expires_at<=NOW()")


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


# 语义层输出的列名就是业务表头（planner 的 output_header_map 已把 SLS 槽位映回业务名，
# 与 Chat 结果卡同一份名字）；SQL/数据集路径的列名是物理名，未过业务映射认证 → 不对外印。
_BUSINESS_HEADER_SOURCES = ("run_semantic_query", "semantic_intent")


def _column_labels(result: dict, keys: list) -> list:
    """事实表表头：按取数路径判定列名可否直接印给用户（护栏 §7）。

    旧写法不分路径一律印「字段1/字段2」：同一份语义层编译产物在 Chat 里显示「省份」、
    在报表里变成「字段1」，用户读不出那张表在讲什么（多入口口径漂移）。
    表头只影响展示，行值仍按 keys 取。空表头位保留「字段N」兜底（不印空白列）。
    """
    origin = str((result or {}).get("source") or "").split(":")[0]
    if origin in _BUSINESS_HEADER_SOURCES:
        return [str(k or "").strip() or f"字段{n + 1}" for n, k in enumerate(keys)]
    return [f"字段{n + 1}" for n in range(len(keys))]


def render_fact_report(title: str, results: list) -> str:
    """平台事实表格：只搬已鉴权查询返回的行，不做模型推断。

    抬头文案要说事实：结果逐条过了治理（权限快照在执行前已校验），所以不再写
    「尚未完成证据校验、不可对外发布」——那句是全仓硬编码的假降级（证据校验从未实现），
    用户早就当套话忽略；真正值得提醒的是"没有模型叙述"和"行数可能被截断"。
    """
    import html
    def cell(value):
        text = html.escape(str(value)).replace("\n", " ")
        for marker in (chr(92), "`", "*", "_", "[", "]", "(", ")", "!", "|"):
            text = text.replace(marker, chr(92) + marker)
        return text
    if not any(r.get("status") == "success" for r in results):
        raise ValueError("没有成功的分析结果")
    lines = [f"# {cell(title)}", "", "> 平台事实表格：由已鉴权查询返回的结果直接汇总，未经模型叙述与推断。",
             "> 行数受取数上限约束，被截断时仅代表样本，不代表总体，不据此推断趋势或占比。", ""]
    for i, result in enumerate(results, 1):
        lines += [f"## 分析项 {i}", ""]
        if result.get("status") != "success":
            lines += ["该分析项未完成。", ""]
            continue
        columns = result.get("columns") or []
        keys = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]
        labels = _column_labels(result, keys)
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


def snapshot_for_query(identity, sql, datasource_id, dialect="mysql") -> dict:
    """为无人值守链路登记来源权限快照（与 execute_semantic_source 同一口径）。

    采集点只交回服务端事实（待执行 SQL / 生效源 / 方言），这里据此枚举物理来源并落
    策略快照；快照本身不下发 LLM/客户端（护栏 §7）。源未明确或角色未授权 → 直接拒，
    不产出一份“没有证据的报告”。
    """
    _authorize_source(identity, datasource_id)
    return _snapshot(identity, sql, datasource_id, dialect)


def _bind_failure_reason(object_ref: str, bind_warnings, datasource_id: int) -> str:
    """报告语义来源定不了源时，照实区分“没定站点”与“真未绑定”（两类动作不同）。

    `binding_resolver` 已经算出了真原因（多站点歧义会把候选写进 warnings），旧写法
    `binding, _ = resolve_binding(...)` 把它丢掉，统一回“分析对象尚未绑定或存在歧义”——
    模型据此向用户说“这个对象没绑定/该站没数据”（与 Chat 侧同类缺陷：会话 1783069459277）。
    对外只给类别与可操作下一步，不回显 warnings 里的源名（避免报告正文插值基础设施命名）。
    """
    if any("多个站点" in str(w) for w in (bind_warnings or [])):
        return ("该分析对象在多个站点都有绑定，而报告来源未指定站点/数据源，无法确定取数范围。"
                "请改用已绑定执行数据源的数据集作为来源（dataset_id），或先按站点分别取数、"
                "再用 save_report 保存已展示给用户的结论；不得据此向用户说“该站点无数据”。")
    if int(datasource_id or 0) <= 0:
        return (f"分析对象 '{object_ref}' 需要按站点/数据源取数，而报告来源未带站点。"
                "请在报告来源里补上 site_code（「数据源拓扑」里的站点业务名，如 '中国'），"
                "或改用已绑定执行数据源的数据集（dataset_id）；"
                "不得据此向用户说“该对象未建模/该站无数据”。")
    return (f"分析对象 '{object_ref}' 在本次生效数据源上未绑定到任何物理表（未绑定 ≠ 无数据）；"
            "请核对数据集/报告来源的执行数据源是否就是数据实际所在的源。")


def _report_source_datasource(object_ref: str, site_code: str, identity: dict) -> int:
    """报告语义来源带 site_code 时，在角色授权集内定出生效源（对象感知消歧）。

    与 `run_semantic_query` 同一口径：站点内同时挂业务库与观测源时，按对象的
    部署事实路由（否则问 RUM 却定到 MySQL 库）。`agent_question` 那一支早已读
    `source.site_code`，intent 这一支旧写法完全不看它 ⇒ 业务对象永远在 ds=0 上解不
    出来（域规则：业务取数必须带具体源）。
    """
    from backend.core.role_service import role_service
    from backend.core.site_service import SiteError, resolve_site_to_source

    ids = role_service.get_user_allowed_datasources(
        int(identity.get("user_id") or 0), int(identity.get("workspace_id") or 0))
    try:
        return int(resolve_site_to_source(site_code, ids, object_key=object_ref) or 0)
    except SiteError as exc:
        # SiteError 文案只含站点名与源业务名（护栏 §7），原样外抛可诊断
        raise ValueError(f"报告来源站点「{site_code}」未能定出生效数据源：{exc}") from exc


def execute_semantic_source(intent, identity, datasource_id=0, site_code=""):
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
    # 显式 datasource_id（程序化调用方）> 来源站点 site_code > 未定源（回可操作错因）
    if not int(datasource_id or 0) and str(site_code or "").strip():
        datasource_id = _report_source_datasource(query.object, str(site_code).strip(), identity)
    binding, bind_warnings = resolve_binding(query.object, datasource_id=datasource_id)
    if not binding:
        raise ValueError(_bind_failure_reason(query.object, bind_warnings, datasource_id))
    _authorize_source(identity, binding.datasource_id)
    query.datasource_id = binding.datasource_id
    query.user_id, query.workspace_id = identity["user_id"], identity["workspace_id"]
    planned = plan(query, binding)
    if not planned.sql or planned.provenance.get("unresolved_terms"):
        # 回可操作原因而不是只说“未通过编译校验”：planner 已经把候选名列在 warnings 里
        # （“本对象可用指标: […]”），丢掉它就等于让调用方盲重试（告警经 safe_warnings 剥物理细节）。
        from backend.semantics.contract import safe_warnings
        notes = safe_warnings(planned.warnings) or ["对象/指标/维度未能解析，请核对已登记的业务名"]
        raise ValueError("语义分析未通过编译校验：" + "；".join(str(n) for n in notes[:3]))
    security = _snapshot(identity, planned.sql, binding.datasource_id, planned.dialect)
    execution = execute_semantic(query, binding, planned, identity, question="报告分析")
    if not execution.allowed:
        # 旧写法把真原因整条丢掉、只回“分析未通过治理校验或需要人工处理”——调用方（AS-BOT/定时任务）
        # 拿到这句无法修正任何东西（no-silent-degradation §1）。口径与统一执行门面同源：
        # 执行阶段失败按错因分类后给可操作文案（原始细节只进服务端日志，护栏 §7），
        # 非执行阶段的闸门声明式拒绝原因（字段未能解析/需人工审批）原样上报。
        from backend.semantics.contract import sanitize_execute_reason
        raise PermissionError(sanitize_execute_reason(
            execution.reason, execution.blocked_at or "execute",
            str(getattr(binding, "db_type", "") or ""))
            or "分析未通过治理校验")
    _verify_snapshot(identity, security)
    from backend.semantics.execute import apply_output_header
    # SLS/Presto 的槽位列名（o0/o1）必须映回业务名：报告正文是给人看的，
    # 旧写法直接印 o0/o1（与 Chat 同口径的编译产物两个入口两种表头）。
    result = apply_output_header(execution.result or {},
                                 planned.output_header_map or {})
    return {"status": "success", "source": "semantic_intent",
            **{k: result[k] for k in ("columns", "rows", "row_count", "truncated") if k in result},
            "_security_context": security}


def execute_report_source(source: dict, identity: dict) -> dict:
    from backend.common.db import execute_query
    from backend.semantics.sql_guard import bounded_query
    if source.get("intent") is not None:
        return execute_semantic_source(source["intent"], identity,
                                       site_code=str(source.get("site_code") or ""))
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
