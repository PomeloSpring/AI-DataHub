"""内容中心服务层 — 栏目/标签/内容条目的读写与 生产→审核→发布 状态机。

口径（与 ``docker/mysql/content_center_migration.sql`` 逐条对应）:

* **正文只存 revision**。文章持两个指针: ``head_revision_id``（当前编辑中的版本）与
  ``live_revision_id``（线上展示版本）。发布 = ``live := head``；回滚 = ``live`` 指回
  历史 revision，不需要重写正文。**编辑已发布内容只产生新 revision，线上继续展示
  live 版本** —— 否则"改一半就全员可见"，整条审核设计就失效了。
  因此 ``status`` 描述的是 **head 的工作流态**，而"门户能不能看到"由
  ``live_revision_id <> 0 AND status <> 'archived'`` 决定（见 :data:`LIVE_WHERE`）。
* **期次 = 已发布的 revision**（用户 2026-11-12：同一主题会按月发布新版本，门户每次展示
  最新一期，读者进去还能翻历史期次）。发布时给被发布的那一版分配本篇内单调递增的
  ``release_seq`` 与人话名 ``release_label``，**不新建"每期一条内容"的表** —— 一个主题
  就是一条条目（slug 稳定、门户一张卡片），否则首页列表、相关推荐、语料同步都要按系列
  折叠，改动面大得多。门户展示的仍然是 ``live_revision_id``，不引入第二个"哪一期在线"
  的事实源。条目可按 :data:`VERSIONING_LATEST` 只把最新一期给读者：那是**读者可见性策略**，
  旧期次在编排面清单、审核流水与"切换展示版本"里照常存在，切回 :data:`VERSIONING_KEEP`
  即恢复可见 —— 策略只收展示面，不毁数据。
* **权限裁决点唯一**。接口能不能调，由 API 权限中间件按 ``adh_perm_registry.api_pattern``
  决定（发布/审核之所以走 ``/api/content/publish*``、``/api/content/reviews*`` 独立前缀，
  就是为了不被 ``article:manage`` 的 ``/api/content/articles*`` 交叉授出——中间件是
  pattern × method 的笛卡尔积，做不了减法）。本模块只做两件事：行级可见性（草稿只对
  可编辑者可见）与状态机合法性，**不新建"谁能做什么"的第二套来源**。
* **门禁结果必须可见**。硬阻断逐条给原因并定位到行；软警告不拦，但必须由审核人逐条
  确认后才算通过。"疑似可识别形态"扫描**只作提醒不自动拒**：它不读数据权限配置，
  自动拒会把已治理维度整体拦掉（与 ``security-guardrails`` §7 对内容形态正则的告诫
  同理），裁决权在人，扫描只负责提醒人去看。
* **失败即暴露**。状态不允许 / 乐观锁冲突 / 栏目悬空一律抛 :class:`ContentError`
  带可读原因，不静默回退成"看起来能用"。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import date, datetime

from backend.common.db import execute_insert, execute_query, execute_write
from backend.common.object_storage import build_content_file_key, get_object_storage

logger = logging.getLogger(__name__)

# ── 字典与常量 ────────────────────────────────────────────────────────────────
STATUS_DRAFT = "draft"
STATUS_IN_REVIEW = "in_review"
STATUS_APPROVED = "approved"
STATUS_PUBLISHED = "published"      # head 已上线（live == head，无待审改动）
STATUS_ARCHIVED = "archived"
STATUSES = (STATUS_DRAFT, STATUS_IN_REVIEW, STATUS_APPROVED, STATUS_PUBLISHED, STATUS_ARCHIVED)

KIND_MARKDOWN = "markdown"
KIND_HTML = "html"
KIND_LINK = "link"
KIND_FILE = "file"
# 历史形态值：报表/会话产物曾经打算以"站内直接渲染报表快照"的形式发布。提升通道真正落地后
# 这条路已不需要（见 content_promote_service：提升=把正文复制成条目的一个新版本，不依赖报表
# 快照），所以它**不再可写**，但常量与存量行保留 —— 删掉取值会让历史行显示成裸英文。
KIND_PROMOTED = "promoted"
# 附件形态已开放（上传通道见本文件末尾）。
# 未开放/已退役形态在写入口**显式拒绝并说明去向**，不做"先存下来但渲染不出"的静默降级。
# html = 站内富版式（时间轴/卡片这类排版 markdown 撑不起来的稿子），
# 渲染侧只过 DOMPurify 白名单（见 frontend/components/HtmlArticle.tsx），入库侧门禁再拦一层可执行标记。
OPEN_KINDS = (KIND_MARKDOWN, KIND_HTML, KIND_LINK, KIND_FILE)
KIND_LABELS = {KIND_MARKDOWN: "站内正文", KIND_HTML: "HTML 版式", KIND_LINK: "外链条目",
               KIND_FILE: "附件"}

# 版本策略（读者可见性口径，唯一裁决在 :func:`releases_of` 与 :func:`get_article`）
VERSIONING_KEEP = "keep_history"      # 读者可翻阅历史期次
VERSIONING_LATEST = "latest_only"     # 只把最新一期给读者，旧期次仅编排面留档
VERSIONING_LABELS = {VERSIONING_KEEP: "保留历史期次", VERSIONING_LATEST: "只保留最新版本"}

# 内容附件的允许类型（发布物, 给人下载, 与聊天附件的"给 LLM 解析"是两套需求:
# 那边不收 ppt/xls/htm, 这边不收 3D 模型 —— 各自维护一份, 不共用一个清单）。
# **一律下载、不站内预览**（用户 2026-11-04 拍板）。两条理由都是硬的:
# 1. 把用户上传的 HTML/PDF 以我们自己的源 inline 渲染, 等于给存储型 XSS 开门 ——
#    同源页面能读到登录态; 所以服务端下载头恒为 Content-Disposition: attachment,
#    响应体里也**不**回 view_mode 这种"服务端并不遵守"的字段（返回 inline 却给下载
#    是双口径, 前端会照着它画预览按钮然后失灵）。
# 2. office 版式/图表还原不了, "抽文本假装预览"给一个残缺视图比诚实说
#    "请下载查看"更坑。
CONTENT_FILE_TYPES = {
    ".pdf":  {"mime": "application/pdf",  "label": "PDF"},
    ".html": {"mime": "text/html",        "label": "HTML"},
    ".htm":  {"mime": "text/html",        "label": "HTML"},
    ".doc":  {"mime": "application/msword", "label": "Word"},
    ".docx": {"mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "label": "Word"},
    ".ppt":  {"mime": "application/vnd.ms-powerpoint", "label": "PPT"},
    ".pptx": {"mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
              "label": "PPT"},
    ".xls":  {"mime": "application/vnd.ms-excel", "label": "Excel"},
    ".xlsx": {"mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "label": "Excel"},
}
# 发布物常见体积比聊天附件大得多（白皮书 PPT 动辄几十 MB）, 单列限额不复用 20MB 档。
MAX_CONTENT_FILE_SIZE = 50 * 1024 * 1024
MAX_CONTENT_FILES_PER_ARTICLE = 10

# 门户可见性唯一判据（列表/详情/相关推荐都走它，避免各处各写一套）
LIVE_WHERE = "live_revision_id <> 0 AND status <> 'archived'"

SUMMARY_SOFT_MAX = 120
PLACEHOLDER_RE = re.compile(r"【待填")
# 疑似患者可识别形态：只按**明确指向病例的键名/称谓**提醒，不做通用内容形态正则
# （手机号/IP 等属业务数据内容，由 adh_sensitive_fields 在取数侧裁决，见护栏 §7）。
PII_HINT_RE = re.compile(r"(患者姓名|病人姓名|就诊号|病历号|病例号|随访电话|牙位图|就诊卡号)")
# 临床样本量提示：正文出现 "N 例" 且 N < 30 时提醒（小样本结论不可外推）。
SMALL_SAMPLE_RE = re.compile(r"(\d{1,2})\s*(?:例|颗|位)(?!次)")
SMALL_SAMPLE_THRESHOLD = 30
# HTML 版式正文里的不可用标记：可执行的（script / 事件属性 / javascript:）与
# 会被渲染侧剥掉但作者以为还在的（iframe/object/style）。入库时就拒，
# 不允许"存得进去、渲染出来缺块东西"的半成品上线（与 placeholder 同一纪律）。
HTML_UNSAFE_RE = re.compile(r"<\s*(script|iframe|object|embed|frame|style|form)\b|\son[a-z]{3,}\s*=|javascript:|vbscript:", re.I)
HTML_TAG_RE = re.compile(r"<[a-zA-Z][a-zA-Z0-9]*(\s|/?>)")

# 内部标识泄露复用出站守卫的同一份清单（凭据/连接串/host/生成 SQL/报错栈），
# 不在这里另列一份——两处各维护一份必然飘移。
_LEAK_LABELS = {
    "sensitive_field": "正文含内部字段名（如 datasource_id / host / password 等键名）",
    "connection_string": "正文含数据库连接串",
    "credential_in_url": "URL 里带了账号密码",
    "generated_sql": "正文含带库表限定的 SQL 语句",
    "error_stack": "正文含程序报错栈",
    "raw_db_error": "正文含数据库原始报错文本",
    "legacy_leak": "正文含内部标识（physical_table / catalog_ref）",
}


class ContentError(Exception):
    """对用户可读的业务失败。``status`` 映射到 HTTP 状态码，``detail`` 直接回给前端。"""

    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _loads(raw, default):
    """JSON 列归一：pymysql 依版本可能回字符串或已解析对象，必须先 loads 再判形状。

    漏这一步会让前端 ``Object.entries`` 把字符串按字符展开（历史缺陷，见
    graph-visualization 规范 §5）。
    """
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("[content] JSON 列解析失败, 按默认值处理: %r", str(raw)[:120])
        return default


def _iso(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else value.isoformat()
    return str(value or "")


def _line_of(text: str, pattern: re.Pattern) -> int:
    """命中处行号（1 起），供门禁"定位到行"。未命中返回 0。"""
    m = pattern.search(text or "")
    return text[: m.start()].count("\n") + 1 if m else 0


def slugify(title: str) -> str:
    """由标题生成 slug。

    保留中文字符（URL 里显示业务可读名，比 ``article-7`` 更好分享），只剥掉标点与
    空白；纯符号标题退化为 ``article``。
    """
    cleaned = re.sub(r"[\s_/\\?&=%#@!。，、：:；;（）()【】\[\]{}\"'`|+*<>~^]+", "-", title or "")
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]", "", cleaned).strip("-").lower()
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned[:60] or "article"


def _unique_slug(base: str, exclude_id: int = 0) -> str:
    slug = base
    seq = 2
    while True:
        row = execute_query(
            "SELECT id FROM adh_content_articles WHERE slug=%s AND id<>%s", (slug, int(exclude_id or 0)),
            fetchone=True)
        if not row:
            return slug
        slug = f"{base}-{seq}"
        seq += 1


# ── 行级可见性：草稿只对可编辑者可见 ──────────────────────────────────────────
EDIT_PERM = "article:manage"


def viewer_can_edit(user: dict, workspace_id: int = 0) -> bool:
    """当前用户能否看到/编辑草稿（复用角色权限码这一唯一裁决源，不另造判断）。

    admin 天然放行 —— 与 ``check_api_permission`` 对 ``role='admin'`` 的口径一致，
    不是本模块新增的特权。
    """
    if not isinstance(user, dict):
        return False
    if str(user.get("role") or "") == "admin":
        return True
    uid = int(user.get("user_id") or 0)
    if not uid:
        return False
    try:
        from backend.core.role_service import role_service
        return EDIT_PERM in set(role_service.get_user_role_perm_codes(uid, workspace_id))
    except Exception as exc:  # noqa: BLE001 — 拿不到权限就当不可见（fail-closed），不得默认放开
        logger.warning("[content] 读取角色权限码失败, 按不可编辑处理: %s", exc)
        return False


# ── 期次（已发布版本）──────────────────────────────────────────────────────────
# 发布不只是把 live 指针挪一下：被发布的那一版同时成为一个「期次」，读者按
# “第 N 期 / 2026-10”寻址历史版本（revision id 不外泄）。清单与可见性只走这里一份实现。

def _release_of_revision(revision_id: int) -> dict:
    """某个版本是哪一期（期次号/期次名/上线时间）。未发布过的版本回 seq=0。

    revision id 属内部标识，**不外泄**（护栏 §7）：对外只回期次号，调用方拿它寻址。
    """
    if not int(revision_id or 0):
        return {"release": 0, "label": "", "released_at": ""}
    row = execute_query("SELECT release_seq, release_label, released_at FROM adh_content_revisions WHERE id=%s",
                        (int(revision_id),), fetchone=True)
    if not row:
        return {"release": 0, "label": "", "released_at": ""}
    return {"release": int(row.get("release_seq") or 0),
            "label": str(row.get("release_label") or ""),
            "released_at": _iso(row.get("released_at"))}


def _revision_by_release(article_id: int, release: int) -> int:
    """期次号 → 已发布版本的 revision id；悬空一律拒（宁阻断勿猜版）。"""
    if not int(release or 0):
        raise ContentError("缺少期次号（可用期次清单见「已发布版本」）")
    row = execute_query("SELECT id FROM adh_content_revisions WHERE article_id=%s AND release_seq=%s",
                        (int(article_id), int(release)), fetchone=True)
    if not row:
        # 别名列不能叫 release：那是 MySQL 保留字（当裸别名直接 1064 → HTTP 500，
        # 而离线替身按 SQL 形状分看不出来），取 release_no 避开
        have = [str(r["release_no"]) for r in execute_query(
            "SELECT release_seq AS release_no FROM adh_content_revisions "
            "WHERE article_id=%s AND release_seq>0 ORDER BY release_seq", (int(article_id),)) or []]
        raise ContentError(f"这篇内容没有第 {int(release)} 期。已发布的期次号："
                           f"{' / '.join(have) or '（还没有）'}", 404)
    return int(row["id"])


def _mark_release(article_id: int, revision_id: int, label: str) -> dict:
    """发布时把被发布的那一版记成一个期次，回 ``{release, label, newly_marked}``。

    已发布过的版本**不重新编号**：回滚/重新上架后再发布同一版，读者看到的仍是当初那一期的
    名字与时间（重编号会把审核结论的版本对应关系搞飘）。
    期次号在本篇内单调递增，靠发布动作自带的乐观锁 CAS 保证并发不重号（只有 CAS 胜者走到这里）。
    """
    now = _release_of_revision(revision_id)
    if now["release"]:
        return {"release": now["release"], "label": now["label"], "newly_marked": False}
    top = execute_query("SELECT COALESCE(MAX(release_seq),0) AS m FROM adh_content_revisions "
                        "WHERE article_id=%s AND release_seq>0", (int(article_id),), fetchone=True)
    seq = int((top or {"m": 0})["m"]) + 1
    text = str(label or "").strip()[:64] or datetime.now().strftime("%Y-%m")
    hit = execute_write("UPDATE adh_content_revisions SET release_seq=%s, release_label=%s, released_at=NOW() "
                        "WHERE id=%s AND release_seq=0", (seq, text, int(revision_id)))
    if not hit:
        raise ContentError("这一版刚刚已被发布过，请刷新后重试", 409)
    return {"release": seq, "label": text, "newly_marked": True}


def releases_of(article_id: int, *, for_reader: bool) -> dict:
    """已发布期次清单（读者面与编排面共用一份实现，差异只由策略决定）。

    不拿审核流水算清单（流水里有 reject/waive 各种动作）：``release_seq>0`` 就是
    “这一版真的上线过”的唯一事实源。
    """
    row = execute_query("SELECT id, status, live_revision_id, versioning FROM adh_content_articles WHERE id=%s",
                        (int(article_id),), fetchone=True)
    if not row:
        raise ContentError("内容不存在或已被删除", 404)
    versioning = str(row.get("versioning") or VERSIONING_KEEP)
    live = int(row.get("live_revision_id") or 0)
    if for_reader and (not live or str(row.get("status")) == STATUS_ARCHIVED):
        # 与 get_article 对未发布给 404 同口径：不区分“存在但没发布”与“不存在”
        raise ContentError("内容尚未发布", 404)
    history_hidden = bool(for_reader and versioning == VERSIONING_LATEST)
    rows = execute_query(
        "SELECT id, release_seq, release_label, released_at FROM adh_content_revisions "
        "WHERE article_id=%s AND release_seq>0 ORDER BY release_seq DESC", (int(article_id),)) or []
    items = []
    for r in rows:
        current = int(r["id"]) == live
        if history_hidden and not current:
            continue
        items.append({"release": int(r["release_seq"]),
                      "label": str(r.get("release_label") or "") or f"第 {int(r['release_seq'])} 期",
                      "released_at": _iso(r.get("released_at")),
                      "is_current": current})
    return {"items": items, "versioning": versioning,
            "versioning_label": VERSIONING_LABELS.get(versioning, versioning),
            "history_hidden": history_hidden,
            # 附件按条目归属、不进 revision 版本链（既有显式取舍）：切换期次不换附件。
            # 这个事实必须随响应下发给 UI 说出来，不能静默让读者以为翻到了“当月的那份附件”。
            "attachments_shared": True}


# ── 栏目 ──────────────────────────────────────────────────────────────────────

def list_categories(*, with_counts: bool = True, include_inactive: bool = False) -> list[dict]:
    sql = ("SELECT id, category_key, label, description, icon, color, sort, is_active "
           "FROM adh_content_categories")
    if not include_inactive:
        sql += " WHERE is_active=1"
    sql += " ORDER BY sort, id"
    rows = execute_query(sql) or []
    out = [{
        "id": int(r["id"]), "key": r["category_key"], "label": r["label"],
        "description": r.get("description") or "", "icon": r.get("icon") or "",
        "color": r.get("color") or "", "sort": int(r.get("sort") or 0),
        "is_active": int(r.get("is_active") or 0),
    } for r in rows]
    if with_counts:
        counts = execute_query(
            f"SELECT category_id, COUNT(*) AS n FROM adh_content_articles "
            f"WHERE {LIVE_WHERE} GROUP BY category_id") or []
        by_id = {int(c["category_id"]): int(c["n"]) for c in counts}
        for item in out:
            item["count"] = by_id.get(item["id"], 0)
    return out


def _category_id(ref, *, required: bool = True) -> int:
    """按 id 或 category_key 解析栏目；悬空一律拒（宁阻断勿悬空引用）。"""
    if ref is None or str(ref).strip() == "":
        if required:
            raise ContentError("必须选择栏目（显示栏目名，不能留空）")
        return 0
    ref = str(ref).strip()
    row = None
    if ref.isdigit():
        row = execute_query("SELECT id FROM adh_content_categories WHERE id=%s AND is_active=1",
                            (int(ref),), fetchone=True)
    if row is None:
        row = execute_query("SELECT id FROM adh_content_categories WHERE category_key=%s AND is_active=1",
                            (ref,), fetchone=True)
    if not row:
        labels = [c["label"] for c in list_categories(with_counts=False)]
        raise ContentError(f"栏目「{ref}」不存在或已停用。现有栏目：{' / '.join(labels) or '（还没有栏目）'}")
    return int(row["id"])


def create_category(payload: dict, actor: str) -> dict:
    label = str(payload.get("label") or "").strip()
    if not label:
        raise ContentError("栏目名称不能为空")
    key = str(payload.get("key") or "").strip() or slugify(label)
    if execute_query("SELECT id FROM adh_content_categories WHERE category_key=%s", (key,), fetchone=True):
        raise ContentError(f"栏目标识「{key}」已存在")
    cid = execute_insert(
        "INSERT INTO adh_content_categories (category_key, label, description, icon, color, sort, is_active) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (key, label, str(payload.get("description") or ""), str(payload.get("icon") or ""),
         str(payload.get("color") or ""), int(payload.get("sort") or 0),
         1 if payload.get("is_active", 1) else 0))
    logger.info("[content] 栏目创建 id=%s label=%s by=%s", cid, label, actor)
    return {"id": int(cid), "key": key, "label": label}


def update_category(category_id: int, payload: dict, actor: str) -> dict:
    row = execute_query("SELECT id FROM adh_content_categories WHERE id=%s", (int(category_id),), fetchone=True)
    if not row:
        raise ContentError("栏目不存在", 404)
    sets, params = [], []
    if payload.get("label") is not None:
        label = str(payload["label"]).strip()
        if not label:
            raise ContentError("栏目名称不能为空")
        sets.append("label=%s"); params.append(label)
    for field, col in (("description", "description"), ("icon", "icon"), ("color", "color")):
        if payload.get(field) is not None:
            sets.append(f"{col}=%s"); params.append(str(payload[field]))
    if payload.get("sort") is not None:
        sets.append("sort=%s"); params.append(int(payload["sort"]))
    if "is_active" in payload:
        sets.append("is_active=%s"); params.append(1 if payload["is_active"] else 0)
    if sets:
        params.append(int(category_id))
        execute_write(f"UPDATE adh_content_categories SET {','.join(sets)} WHERE id=%s", tuple(params))
    logger.info("[content] 栏目更新 id=%s by=%s", category_id, actor)
    return {"id": int(category_id)}


def delete_category(category_id: int, actor: str) -> dict:
    """栏目下有内容就不许删（避免条目变成无栏目的悬空数据）。"""
    used = int((execute_query(
        "SELECT COUNT(*) AS n FROM adh_content_articles WHERE category_id=%s",
        (int(category_id),), fetchone=True) or {"n": 0})["n"])
    if used:
        raise ContentError(f"该栏目下还有 {used} 条内容，请先移动或删除后再删栏目")
    execute_write("DELETE FROM adh_content_categories WHERE id=%s", (int(category_id),))
    logger.info("[content] 栏目删除 id=%s by=%s", category_id, actor)
    return {"deleted": True}


# ── 标签受控词表 ──────────────────────────────────────────────────────────────

def list_tags(*, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT id, tag, sort, is_active FROM adh_content_tag_vocab"
    if not include_inactive:
        sql += " WHERE is_active=1"
    sql += " ORDER BY sort, id"
    rows = execute_query(sql) or []
    # 每个标签的命中篇数由在线条目实数带出：侧栏「热门标签」只渲染 count>=2 的标签
    # （首批成稿踩过坑：26 个标签 21 个只命中 1 篇，点进去只有一个结果，筛选形同虚设）。
    hits = execute_query(
        f"SELECT tags FROM adh_content_articles WHERE {LIVE_WHERE}") or []
    tally: dict[str, int] = {}
    for r in hits:
        for t in _loads(r.get("tags"), []):
            tally[str(t)] = tally.get(str(t), 0) + 1
    return [{"id": int(r["id"]), "tag": r["tag"], "sort": int(r.get("sort") or 0),
             "is_active": int(r.get("is_active") or 0), "count": tally.get(r["tag"], 0)}
            for r in rows]


def create_tag(payload: dict, actor: str) -> dict:
    tag = str(payload.get("tag") or "").strip()
    if not tag:
        raise ContentError("标签不能为空")
    if len(tag) > 32:
        raise ContentError("标签太长了（不超过 32 字）")
    if execute_query("SELECT id FROM adh_content_tag_vocab WHERE tag=%s", (tag,), fetchone=True):
        raise ContentError(f"标签「{tag}」已存在")
    tid = execute_insert("INSERT INTO adh_content_tag_vocab (tag, sort, is_active) VALUES (%s,%s,1)",
                         (tag, int(payload.get("sort") or 0)))
    logger.info("[content] 标签创建 id=%s tag=%s by=%s", tid, tag, actor)
    return {"id": int(tid), "tag": tag}


def update_tag(tag_id: int, payload: dict, actor: str) -> dict:
    row = execute_query("SELECT id, tag FROM adh_content_tag_vocab WHERE id=%s", (int(tag_id),), fetchone=True)
    if not row:
        raise ContentError("标签不存在", 404)
    sets, params = [], []
    if payload.get("tag") is not None:
        tag = str(payload["tag"]).strip()
        if not tag:
            raise ContentError("标签不能为空")
        dup = execute_query("SELECT id FROM adh_content_tag_vocab WHERE tag=%s AND id<>%s",
                            (tag, int(tag_id)), fetchone=True)
        if dup:
            raise ContentError(f"标签「{tag}」已存在")
        # 改名必须同步把条目里的旧标签换掉, 否则历史条目引用悬空(词表是唯一真值源)
        _rename_tag_in_articles(row["tag"], tag)
        sets.append("tag=%s"); params.append(tag)
    if payload.get("sort") is not None:
        sets.append("sort=%s"); params.append(int(payload["sort"]))
    if "is_active" in payload:
        sets.append("is_active=%s"); params.append(1 if payload["is_active"] else 0)
    if sets:
        params.append(int(tag_id))
        execute_write(f"UPDATE adh_content_tag_vocab SET {','.join(sets)} WHERE id=%s", tuple(params))
    logger.info("[content] 标签更新 id=%s by=%s", tag_id, actor)
    return {"id": int(tag_id)}


def _rename_tag_in_articles(old: str, new: str) -> None:
    rows = execute_query("SELECT id, tags FROM adh_content_articles WHERE tags LIKE %s",
                         (f"%{old}%",)) or []
    for r in rows:
        tags = [str(t) for t in _loads(r.get("tags"), [])]
        if old not in tags:
            continue
        merged = []
        for t in tags:
            t = new if t == old else t
            if t not in merged:
                merged.append(t)
        execute_write("UPDATE adh_content_articles SET tags=%s WHERE id=%s",
                      (json.dumps(merged, ensure_ascii=False), int(r["id"])))


def delete_tag(tag_id: int, actor: str) -> dict:
    row = execute_query("SELECT tag FROM adh_content_tag_vocab WHERE id=%s", (int(tag_id),), fetchone=True)
    if not row:
        raise ContentError("标签不存在", 404)
    used = sum(1 for r in (execute_query(f"SELECT tags FROM adh_content_articles WHERE {LIVE_WHERE}") or [])
               if row["tag"] in [str(t) for t in _loads(r.get("tags"), [])])
    if used:
        raise ContentError(f"还有 {used} 条在用的内容带这个标签，请先从条目上去掉")
    execute_write("DELETE FROM adh_content_tag_vocab WHERE id=%s", (int(tag_id),))
    logger.info("[content] 标签删除 id=%s by=%s", tag_id, actor)
    return {"deleted": True}


# ── revision ──────────────────────────────────────────────────────────────────

def _add_revision(article_id: int, body: str, note: str, actor: str) -> int:
    content_hash = hashlib.md5((body or "").encode("utf-8")).hexdigest()
    rid = execute_insert(
        "INSERT INTO adh_content_revisions (article_id, body, format, change_note, content_hash, created_by) "
        "VALUES (%s,%s,'markdown',%s,%s,%s)",
        (int(article_id), body or "", note or "", content_hash, actor))
    return int(rid)


def _revision_body(revision_id: int) -> str:
    if not int(revision_id or 0):
        return ""
    row = execute_query("SELECT body FROM adh_content_revisions WHERE id=%s",
                        (int(revision_id),), fetchone=True)
    return str((row or {}).get("body") or "")


# ── 条目读取 ──────────────────────────────────────────────────────────────────

#: 已退役形态的显示名：存量行仍要给人话读法，不得回退成裸英文取值（ui-resource-display §1）
KIND_LEGACY_LABELS = {KIND_PROMOTED: "平台内提升（历史形态）"}


def _kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind or "") or KIND_LEGACY_LABELS.get(kind or "") or str(kind or "")


def _article_out(row: dict, *, body: str = "", with_body: bool = False) -> dict:
    """响应组装：只回业务可读字段。

    ``source_ref`` / ``cover_object_key`` / revision id 属内部标识，不进响应体
    （护栏 §7）；前端要正文就随详情接口一起给，要版本走版本清单接口。
    期次信息（第几期/期次名/共几期）同样只由 ``_SELECT`` 的相关子查询带出，
    前端不得自己拿 revision 推断。
    """
    live = int(row.get("live_revision_id") or 0)
    release_count = int(row.get("release_count") or 0)
    versioning = str(row.get("versioning") or VERSIONING_KEEP)
    out = {
        "id": int(row["id"]),
        "slug": row.get("slug") or "",
        "title": row.get("title") or "",
        "summary": row.get("summary") or "",
        "category_id": int(row.get("category_id") or 0),
        "category_label": row.get("category_label") or "",
        "category_icon": row.get("category_icon") or "",
        "category_color": row.get("category_color") or "",
        "tags": _loads(row.get("tags"), []),
        "kind": row.get("kind") or KIND_MARKDOWN,
        "kind_label": _kind_label(row.get("kind") or ""),
        "link_url": row.get("link_url") or "",
        "link_label": row.get("link_label") or "",
        "refs": _loads(row.get("refs"), []),
        "pinned": int(row.get("pinned") or 0),
        "featured": int(row.get("featured") or 0),
        "status": row.get("status") or STATUS_DRAFT,
        "is_live": live != 0,
        # ── 期次（已发布版本）：当前在线那一期的号与人话名 + 本篇共几期 ──
        "versioning": versioning,
        "versioning_label": VERSIONING_LABELS.get(versioning, versioning),
        "release": int(row.get("live_release_seq") or 0),
        "release_label": str(row.get("live_release_label") or ""),
        "release_count": release_count,
        # 读者面是否拿不到历史期次：只由策略决定（编排面永远拿得到全量）
        "history_hidden": versioning == VERSIONING_LATEST,
        # 「切换展示版本」有没有可切的目标：至少两个已发布期次才谈得上切换（否则
        # 就是个只能选中自己的死开关，与报告中心“只有一版不给下拉”同口径）
        "can_rollback": bool(live) and release_count >= 2 and str(row.get("status")) != STATUS_ARCHIVED,
        "has_pending_change": (live != 0
                               and int(row.get("head_revision_id") or 0) != live),
        # 重新上架的两条判据（都只由状态机决定，不含权限 —— 权限仍由中间件裁决）：
        # 有下架前那一版可放回 / head 是下架之后新改的没审过的版本。口径见 :func:`_republish_verdict`
        "can_republish": _republish_verdict(row)[0],
        "head_changed_since_archive": _republish_verdict(row)[1],
        "review_by": _iso(row.get("review_by"))[:10],
        "published_at": _iso(row.get("published_at")),
        "archived_at": _iso(row.get("archived_at")),
        "view_count": int(row.get("view_count") or 0),
        "author": row.get("created_by") or "",
        "updated_by": row.get("updated_by") or "",
        "updated_at": _iso(row.get("updated_at")),
        "version": int(row.get("version") or 1),
    }
    # 知识库同步状态：行里带了 JOIN 列才输出（未同步时是 null，前端据此显示“未同步”）
    state = _kb_sync_of(row)
    if state is not None or "sync_kb_name" in row:
        out["kb_sync"] = state
    if with_body:
        out["body"] = body
    return out


_SELECT = (
    "SELECT a.*, c.label AS category_label, c.icon AS category_icon, c.color AS category_color, "
    "       s.kb_name AS sync_kb_name, s.doc_title AS sync_doc_title, "
    "       s.revision_id AS sync_revision_id, s.synced_at AS sync_synced_at, "
    "       s.state AS sync_state, s.error AS sync_error, "
    # 下架时在线的那一版：下架把 live 归零（门户可见性唯一判据需要它），但审计流水按
    # archive 行记下了当时被收走的 revision —— 「重新上架」放回的就是这一版。
    # 不另建指针列：审核流水是真值源，再加一列等于两处各存一份、必然飘移。
    "       (SELECT r.revision_id FROM adh_content_reviews r "
    "         WHERE r.article_id = a.id AND r.action = 'archive' ORDER BY r.id DESC LIMIT 1) "
    "         AS last_archived_revision_id, "
    # 期次三列：当前在线那一版的号/名，以及本篇已发布过几期。不另存指针列：
    # release_seq 只在发布动作里分配，它本身就是“真的上线过”的事实源（与上面同纪律）。
    # 都是主键/索引上的相关子查询（idx_article_release），单页 50 行量级可接受。
    "       (SELECT r.release_seq FROM adh_content_revisions r "
    "         WHERE r.id = a.live_revision_id) AS live_release_seq, "
    "       (SELECT r.release_label FROM adh_content_revisions r "
    "         WHERE r.id = a.live_revision_id) AS live_release_label, "
    "       (SELECT COUNT(*) FROM adh_content_revisions r "
    "         WHERE r.article_id = a.id AND r.release_seq > 0) AS release_count "
    "FROM adh_content_articles a LEFT JOIN adh_content_categories c ON c.id = a.category_id "
    # 水位线是"每次尝试一行"，当前归属 = 最新那一行（不是 JOIN 全表，那会把历史行摊成多行）
    "LEFT JOIN adh_content_kb_sync s ON s.id = "
    "  (SELECT s2.id FROM adh_content_kb_sync s2 WHERE s2.article_id = a.id ORDER BY s2.id DESC LIMIT 1)"
)


def _republish_verdict(row: dict) -> tuple[bool, bool]:
    """已下架条目的重新上架判据 → ``(可一键上架, head 是下架后新改的版本)``。

    - 可一键上架：存在下架时在线的那一版（archive 流水带 revision）。放回去不绕审核 ——
      那一版当初就是审核通过才上线的，下架只是收走门户入口，审核结论对它仍然有效。
    - head 是下架后新改的：head 与那一版不是同一个版本（或根本没有下架流水可追溯）。
      这一版没审过，**不得**跟着上线，只能走「提交审核 → 通过 → 发布」或显式免审直发。

    非已下架条目两个都是 False（前端据此不画上架/提交入口）。
    """
    if str(row.get("status") or "") != STATUS_ARCHIVED:
        return False, False
    back = int(row.get("last_archived_revision_id") or 0)
    head = int(row.get("head_revision_id") or 0)
    return bool(back), back == 0 or head != back


def _kb_sync_of(row: dict) -> dict | None:
    """知识库同步状态（由 _SELECT 里的 LEFT JOIN 列直接算，不另发一次查询）。

    只回业务可读字段：`source_id` 属云端内部标识，不进响应体（护栏 §7）。
    stale 按版本判：已发布版与已同步版不是同一个 revision 就是“有改动未同步”，
    不靠重渲染算哈希（那需要逐篇读正文，列表页就是 N 次开销）。
    """
    kb_name = str(row.get("sync_kb_name") or "")
    if not kb_name:
        return None
    state = str(row.get("sync_state") or "")
    live = int(row.get("live_revision_id") or 0)
    synced = state == "synced"
    return {
        "kb_name": kb_name,
        "doc_title": str(row.get("sync_doc_title") or ""),
        "state": state,
        "synced_at": _iso(row.get("sync_synced_at")),
        # 最新一次尝试是失败 → 有正文就仍算"待同步"；成功 → 按版本比对判新鲜度
        "stale": bool(live) if not synced else live != int(row.get("sync_revision_id") or 0),
        "last_error": "" if synced else str(row.get("sync_error") or ""),
    }


def list_articles(*, category: str = "", exclude_category: str = "", kind: str = "",
                  tag: str = "", q: str = "", status: str = "", mine: str = "",
                  can_edit: bool = False, sort: str = "latest",
                  page: int = 1, page_size: int = 12) -> dict:
    """分页清单。默认只回门户可见内容；草稿只有可编辑者按 status 显式筛才回。

    ``exclude_category`` 与 ``category`` 同一套 key/id 解析口径，供首页资讯流把
    「关于本公司」让给右上角简介卡（同屏两份同一条目是重复信息）；内容管理页
    不传它，编排面照常看全量。NULL 安全用 COALESCE 兜住 —— LEFT JOIN 下未挂栏目
    的条目谓词为 NULL，直接 NOT(...) 会把它们一起筛掉（静默少内容，不是本意）。
    """
    page = max(1, int(page or 1))
    page_size = min(50, max(1, int(page_size or 12)))
    where, params = [], []

    want_live_only = not (can_edit and status)
    if want_live_only:
        where.append(LIVE_WHERE)
    elif status and status != "all":
        if status not in STATUSES:
            raise ContentError(f"状态取值不合法（只支持 {'/'.join(STATUSES)}）")
        where.append("a.status=%s"); params.append(status)

    if category:
        where.append("(c.category_key=%s OR a.category_id=%s)")
        params.extend([category, category if category.isdigit() else 0])
    if exclude_category:
        if exclude_category.isdigit():
            where.append("COALESCE(a.category_id, 0)<>%s"); params.append(exclude_category)
        else:
            where.append("COALESCE(c.category_key, '')<>%s"); params.append(exclude_category)
    if kind:
        if kind not in KIND_LABELS:
            raise ContentError(f"形态取值不合法（只支持 {'/'.join(KIND_LABELS)}）")
        where.append("a.kind=%s"); params.append(kind)
    if tag:
        where.append("a.tags LIKE %s"); params.append(f'%"{tag}"%')
    if q:
        where.append("(a.title LIKE %s OR a.summary LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if mine and can_edit:
        where.append("a.created_by=%s"); params.append(mine)

    order = {"latest": "COALESCE(a.published_at, a.updated_at) DESC",
             "hot": "a.view_count DESC",
             "rec": "a.pinned DESC, a.featured DESC, COALESCE(a.published_at, a.updated_at) DESC"}.get(sort)
    if not order:
        raise ContentError(f"排序方式不合法（只支持 latest/hot/rec）")

    # where 可以为空（可编辑者选「全部状态」且无其他筛选就是合法组合），拼 SQL 时必须
    # 整段省略 WHERE —— 直接 join 空列表会生成 "WHERE ORDER BY" 的语法错（历史缺陷：
    # 内容管理页 500）。
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    base = (" FROM adh_content_articles a LEFT JOIN adh_content_categories c "
            f"ON c.id = a.category_id{clause}")
    total = int((execute_query("SELECT COUNT(*) AS n" + base, tuple(params), fetchone=True) or {"n": 0})["n"])
    rows = execute_query(_SELECT + clause +
                         f" ORDER BY a.pinned DESC, {order}, a.id DESC LIMIT %s OFFSET %s",
                         tuple(params) + (page_size, (page - 1) * page_size)) or []
    return {"items": [_article_out(r) for r in rows], "total": total,
            "page": page, "page_size": page_size}


def get_article(*, slug: str = "", article_id: int = 0, can_edit: bool = False,
                revision: str = "live", bump_view: bool = False, release: int = 0) -> dict:
    """详情。``revision='head'`` 供编辑器预览（只有可编辑者拿得到）。

    ``release=N`` 读**已发布的第 N 期**（历史期次、或编排面回看旧版）：
    · 期次号悬空、或指向从未发布过的版本一律拒 —— 不开“拿 revision id 直读”的口子；
    · 读者面额外要求条目当前在线，且策略允许：``latest_only`` 只放开现在这一期（与
      :func:`releases_of` 同一份口径，两处各写一套就会出现“清单里没有但直链能读到”）；
    · 可编辑者不受策略限制（编排与回滚要能对比旧期）。

    不论走哪个分支，响应里的 ``viewing_*`` 都说清“现在这份正文是哪一期”——
    不然页面拿 status/release 自己推，必把“在看旧期”画成“在看最新”。
    """
    want_release = int(release or 0)
    if want_release and str(revision or "live") != "live":
        # 不做“两个都给就静默优先其中一个”的兼容：双口径参数同时生效就是假能跑
        raise ContentError("期次与版本参数不能同时给：读某一期用 release，看改动中的草稿用 revision=head")
    row = None
    if slug:
        row = execute_query(_SELECT + " WHERE a.slug=%s", (slug,), fetchone=True)
    elif int(article_id or 0):
        row = execute_query(_SELECT + " WHERE a.id=%s", (int(article_id),), fetchone=True)
    if not row:
        raise ContentError("内容不存在或已被删除", 404)

    live = int(row.get("live_revision_id") or 0)
    head = int(row.get("head_revision_id") or 0)
    offline = (not live) or str(row.get("status")) == STATUS_ARCHIVED
    if want_release:
        rid = _revision_by_release(int(row["id"]), want_release)
        if not can_edit:
            if offline:
                raise ContentError("内容尚未发布", 404)
            if str(row.get("versioning") or VERSIONING_KEEP) == VERSIONING_LATEST and rid != live:
                # 不给 403：与“未发布也回 404”同口径，不把“存在但策略不展示”做成可探测差异；
                # 但原因必须说人话（不能伪装成“这一期不存在”，它确实存在，只是不对外）。
                raise ContentError("这篇内容只发布最新版本，历史期次不对外展示。"
                                   "需要翻看旧期请联系管理员把「版本策略」改回「保留历史期次」。", 404)
        body = _revision_body(rid)
        viewing = rid
    elif revision == "head":
        if not can_edit:
            raise ContentError("草稿预览需要编辑权限", 403)
        body = _revision_body(head)
        viewing = head
    elif live and not offline:
        body = _revision_body(live)
        viewing = live
    elif can_edit:
        # 未发布/已下架：可编辑者看自己的草稿，普通读者走上面的 404
        body = _revision_body(head)
        viewing = head
    else:
        raise ContentError("内容尚未发布", 404)

    if bump_view and live:
        execute_write("UPDATE adh_content_articles SET view_count = view_count + 1 WHERE id=%s",
                      (int(row["id"]),))
        row["view_count"] = int(row.get("view_count") or 0) + 1
    out = _article_out(row, body=body, with_body=True)
    # “这一份正文是哪一期”：期次号悬空时回 0，前端据此画“未发布的新版”而不是画第 0 期
    shown = _release_of_revision(viewing) if viewing else {"release": 0, "label": "", "released_at": ""}
    out["viewing_release"] = int(shown["release"])
    out["viewing_release_label"] = shown["label"]
    out["viewing_is_current"] = bool(viewing and viewing == live)
    # 附件清单随详情带出: 详情页/预览页据此渲染下载卡, 不必再发一次请求
    # （列表页不带 —— 每行一查就是 N+1, 卡片上也不需要文件名）
    # 附件按条目归属、不进版本链：翻旧期时看到的仍是“当前挂着的这几个”，这句话由
    # releases 接口的 attachments_shared 交给 UI 说出来，不在这里静默换成“旧期的附件”。
    out["files"] = _files_of(int(row["id"]))
    return out


def related_articles(article_id: int, *, limit: int = 5) -> list[dict]:
    row = execute_query("SELECT category_id, live_revision_id FROM adh_content_articles WHERE id=%s",
                        (int(article_id),), fetchone=True)
    if not row:
        return []
    rows = execute_query(
        _SELECT + f" WHERE {' AND '.join(['a.category_id=%s', 'a.id<>%s', LIVE_WHERE])}"
        " ORDER BY COALESCE(a.published_at, a.updated_at) DESC LIMIT %s",
        (int(row["category_id"] or 0), int(article_id), int(limit))) or []
    return [_article_out(r) for r in rows]


# ── 条目写入 ──────────────────────────────────────────────────────────────────

def _clean_tags(raw) -> list[str]:
    tags = raw if isinstance(raw, list) else _loads(raw, [])
    vocab = {t["tag"] for t in list_tags()}
    out = []
    for t in tags or []:
        t = str(t).strip()
        if not t:
            continue
        if t not in vocab:
            raise ContentError(f"标签「{t}」不在受控词表里，请先在「栏目与标签」中添加该标签")
        if t not in out:
            out.append(t)
    return out


def _validate_kind(kind: str) -> str:
    kind = str(kind or KIND_MARKDOWN).strip()
    if kind not in KIND_LABELS:
        detail = (f"形态取值不合法（只支持 {'/'.join(KIND_LABELS)}）")
        if kind == KIND_PROMOTED:
            # 提升通道落地为"复制正文成条目版本"，不再需要"平台内提升"这个形态；
            # 存量行仍可读（KIND_PROMOTED 常量保留），只是新写入一律拒并说清去向。
            detail = ("「平台内提升」形态已退役：从报告中心提升会把正文落地为"
                      f"「{KIND_LABELS[KIND_MARKDOWN]}」或「{KIND_LABELS[KIND_HTML]}」版本，"
                      "不依赖报表快照")
        raise ContentError(detail)
    if kind not in OPEN_KINDS:
        raise ContentError(f"「{KIND_LABELS[kind]}」形态还没有开放，请先用 "
                           f"{' / '.join(KIND_LABELS[k] for k in OPEN_KINDS)}")
    return kind


def _validate_versioning(value) -> str:
    """版本策略只能取登记的两个值；非法取值直接拒（不静默回默认，那会让策略看起来生效了）。"""
    text = str(value if value is not None else VERSIONING_KEEP).strip()
    if text not in VERSIONING_LABELS:
        raise ContentError(f"版本策略取值不合法（只支持 {' / '.join(VERSIONING_LABELS)}）")
    return text


def create_article(payload: dict, actor: str, *, source_type: str = "manual",
                   source_ref: str = "") -> dict:
    """新建草稿。

    ``source_type`` / ``source_ref`` **不开 HTTP 面**：只供服务内部调用方（如报告中心提升
    通道 :mod:`content_promote_service`）登记溯源。``ArticlePayload`` 里没有这两个字段，
    所以请求体传了也不认（与护栏 §2“身份与内部事实只取服务端”同口径）。
    ``source_ref`` 存的是提升源归集键（内部标识），不进响应体。
    """
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ContentError("标题不能为空")
    kind = _validate_kind(payload.get("kind"))
    versioning = _validate_versioning(payload.get("versioning"))
    category_id = _category_id(payload.get("category"))
    link_url = str(payload.get("link_url") or "").strip()
    if kind == KIND_LINK and not link_url.lower().startswith(("http://", "https://")):
        raise ContentError("外链条目必须填 http(s) 开头的原文地址")
    slug = _unique_slug(slugify(str(payload.get("slug") or "").strip() or title))
    tags = _clean_tags(payload.get("tags"))
    refs = _clean_refs(payload.get("refs"))

    article_id = int(execute_insert(
        "INSERT INTO adh_content_articles (slug, title, summary, category_id, tags, kind, link_url, "
        "link_label, refs, pinned, featured, status, review_by, source_type, source_ref, versioning, "
        "created_by, updated_by, version) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)",
        (slug, title, str(payload.get("summary") or "").strip(), category_id,
         json.dumps(tags, ensure_ascii=False), kind, link_url,
         str(payload.get("link_label") or "").strip(), json.dumps(refs, ensure_ascii=False),
         1 if payload.get("pinned") else 0, 1 if payload.get("featured") else 0,
         STATUS_DRAFT, _date_or_none(payload.get("review_by")),
         str(source_type or "manual"), str(source_ref or ""), versioning, actor, actor)))
    rid = _add_revision(article_id, str(payload.get("body") or ""), "初稿", actor)
    execute_write("UPDATE adh_content_articles SET head_revision_id=%s WHERE id=%s", (rid, article_id))
    logger.info("[content] 条目创建 id=%s slug=%s source=%s by=%s", article_id, slug, source_type, actor)
    return get_article(article_id=article_id, can_edit=True, revision="head")


def update_article(article_id: int, payload: dict, actor: str, *, expected_version: int | None = None) -> dict:
    row = execute_query("SELECT * FROM adh_content_articles WHERE id=%s", (int(article_id),), fetchone=True)
    if not row:
        raise ContentError("内容不存在", 404)
    version = int(row.get("version") or 1)
    if expected_version is not None and int(expected_version) != version:
        # 并发保护：不覆盖他人编辑，要求刷新后重来
        raise ContentError("这篇内容已被他人改过，请刷新后重试", 409)
    # 审核中不开写口：这个判定必须在任何副作用之前。旧写法放在建 revision 之后，
    # 于是“正在审的稿子改一下正文”会先落一行没人引用的 revision（版本清单里多一条假历史）。
    if str(row.get("status")) == STATUS_IN_REVIEW:
        raise ContentError("这篇正在审核中，请先让审核人驳回或撤回后再改")

    sets, params = [], []
    if payload.get("title") is not None:
        title = str(payload["title"]).strip()
        if not title:
            raise ContentError("标题不能为空")
        sets.append("title=%s"); params.append(title)
        if str(row.get("slug") or "") != slugify(title) and not payload.get("slug"):
            sets.append("slug=%s"); params.append(_unique_slug(slugify(title), int(article_id)))
    if payload.get("slug") is not None and str(payload["slug"]).strip():
        sets.append("slug=%s"); params.append(_unique_slug(slugify(str(payload["slug"]).strip()), int(article_id)))
    if payload.get("summary") is not None:
        sets.append("summary=%s"); params.append(str(payload["summary"]).strip())
    if payload.get("category") is not None:
        sets.append("category_id=%s"); params.append(_category_id(payload["category"]))
    if payload.get("tags") is not None:
        sets.append("tags=%s"); params.append(json.dumps(_clean_tags(payload["tags"]), ensure_ascii=False))
    if payload.get("refs") is not None:
        sets.append("refs=%s"); params.append(json.dumps(_clean_refs(payload["refs"]), ensure_ascii=False))
    if payload.get("kind") is not None:
        sets.append("kind=%s"); params.append(_validate_kind(payload["kind"]))
    if payload.get("versioning") is not None:
        sets.append("versioning=%s"); params.append(_validate_versioning(payload["versioning"]))
    if payload.get("link_url") is not None:
        sets.append("link_url=%s"); params.append(str(payload["link_url"]).strip())
    if payload.get("link_label") is not None:
        sets.append("link_label=%s"); params.append(str(payload["link_label"]).strip())
    for flag in ("pinned", "featured"):
        if payload.get(flag) is not None:
            sets.append(f"{flag}=%s"); params.append(1 if payload[flag] else 0)
    if payload.get("review_by") is not None:
        sets.append("review_by=%s"); params.append(_date_or_none(payload["review_by"]))

    body = payload.get("body")
    new_rid = 0
    if body is not None and str(body) != _revision_body(int(row.get("head_revision_id") or 0)):
        new_rid = _add_revision(int(article_id), str(body), str(payload.get("change_note") or "编辑正文"), actor)
        sets.append("head_revision_id=%s"); params.append(new_rid)
        # 已发布内容改了正文：head 变了但 live 不动 → 线上仍是旧版，改动走审核
        if int(row.get("live_revision_id") or 0):
            sets.append("status=%s"); params.append(STATUS_DRAFT)

    if not sets:
        return get_article(article_id=int(article_id), can_edit=True, revision="head")

    sets.append("updated_by=%s"); params.append(actor)
    sets.append("version=%s"); params.append(version + 1)
    params.append(int(article_id))
    hit = execute_write(f"UPDATE adh_content_articles SET {','.join(sets)} WHERE id=%s AND version=%s",
                        tuple(params) + (version,))
    if not hit:
        raise ContentError("这篇内容已被他人改过，请刷新后重试", 409)
    return get_article(article_id=int(article_id), can_edit=True, revision="head")


def delete_article(article_id: int, actor: str) -> dict:
    row = execute_query("SELECT status, live_revision_id FROM adh_content_articles WHERE id=%s",
                        (int(article_id),), fetchone=True)
    if not row:
        raise ContentError("内容不存在", 404)
    if int(row.get("live_revision_id") or 0):
        raise ContentError("已上线的内容不能直接删除，请先在「审核发布」里下架（保留下架流水可追溯）")
    # 附件对象跟条目一起清。只删 DB 行会把文件留成桶里无人引用的孤儿：旧直链仍
    # 能取到已删内容（治理问题）, 存储也在无声膨胀 —— 与 delete_file 同一纪律：
    # 先删行（下载入口立即消失）, 再删对象, 删不掉的必须说出来。
    files = execute_query("SELECT id, object_key, filename FROM adh_content_files WHERE article_id=%s",
                          (int(article_id),)) or []
    execute_write("DELETE FROM adh_content_revisions WHERE article_id=%s", (int(article_id),))
    execute_write("DELETE FROM adh_content_files WHERE article_id=%s", (int(article_id),))
    execute_write("DELETE FROM adh_content_user_state WHERE article_id=%s", (int(article_id),))
    # 审核流水也要一起删。这几张表都没有外键（清理全靠上面的显式 DELETE），漏一条
    # 就留下指向不存在条目的孤儿行：门户与「审核发布」页都按 article_id 取流水，
    # 孤儿行渲染不出来，却会污染一切按 article_id 聚合的统计与巡检（本次实测就撞上了
    # 5 行已删条目的 submit 流水）。追溯价值也不存在 —— 它们指向的版本快照已被删掉。
    # 已上线条目走不到这里（上面要求先下架），所以不存在"把上线审计删了"的情形。
    execute_write("DELETE FROM adh_content_reviews WHERE article_id=%s", (int(article_id),))
    execute_write("DELETE FROM adh_content_articles WHERE id=%s", (int(article_id),))
    logger.info("[content] 条目删除 id=%s by=%s files=%s", article_id, actor, len(files))
    out: dict = {"deleted": True}
    leftover: list[str] = []
    for f in files:
        name = str(f.get("filename") or f"#{f.get('id')}")
        try:
            if not get_object_storage().delete(str(f.get("object_key") or "")):
                leftover.append(name)
        except Exception as exc:  # noqa: BLE001 — 清理失败属旁路, 不得影响"条目已删除"这个事实
            logger.warning("[content] 条目删除时附件对象清理失败 file=%s: %s", f.get("id"), exc)
            leftover.append(name)
    if leftover:
        out["warning"] = (f"内容已删除，但 {len(leftover)} 个附件在对象存储里没删掉"
                          f"（{'、'.join(leftover[:3])}）。不影响页面展示, 只占存储；"
                          "请稍后重试或请管理员清理。")
    return out


def _date_or_none(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        raise ContentError(f"复审到期日格式不对：{text}（应为 2026-12-31）") from None


def _clean_refs(raw) -> list[dict]:
    items = raw if isinstance(raw, list) else _loads(raw, [])
    out = []
    for it in items or []:
        if isinstance(it, str):
            it = {"title": it, "url": ""}
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        if not title and not url:
            continue
        out.append({"title": title or url, "url": url})
    return out


# ── 门禁 ──────────────────────────────────────────────────────────────────────

def gate_article(article: dict, body: str) -> tuple[list[dict], list[dict]]:
    """返回 (硬阻断, 软警告)。每条都是 ``{code, message, line}``，必须原样给用户看。"""
    hard: list[dict] = []
    warns: list[dict] = []
    text = body or ""
    kind = str(article.get("kind") or KIND_MARKDOWN)

    # ── 硬阻断 ──
    if PLACEHOLDER_RE.search(text):
        hard.append({"code": "placeholder", "line": _line_of(text, PLACEHOLDER_RE),
                     "message": "正文还有未填写的占位（【待填…）。填不出来的数据请把该段落删掉，不要带着占位发布"})
    leak = _leak_hits(text)
    if leak:
        hard.append({"code": "internal_leak", "line": leak[0][1],
                     "message": "正文含内部标识：" + "；".join(lbl for lbl, _ in leak)
                             + "。对外内容不得出现数据源/主机/连接串/生成 SQL/报错栈"})
    if not str(article.get("category_id") or 0):
        hard.append({"code": "category_missing", "line": 0, "message": "还没有选择栏目"})
    else:
        exists = execute_query("SELECT is_active FROM adh_content_categories WHERE id=%s",
                               (int(article["category_id"]),), fetchone=True)
        if not exists:
            hard.append({"code": "category_dangling", "line": 0, "message": "所选栏目已不存在，请重新选择"})
        elif not int(exists.get("is_active") or 0):
            hard.append({"code": "category_inactive", "line": 0, "message": "所选栏目已停用，请改选在用栏目"})
    if kind == KIND_LINK and not str(article.get("link_url") or "").lower().startswith(("http://", "https://")):
        hard.append({"code": "link_missing", "line": 0, "message": "外链条目必须有 http(s) 开头的原文地址"})
    if kind in (KIND_MARKDOWN, KIND_HTML) and not text.strip():
        hard.append({"code": "body_empty", "line": 0, "message": "正文是空的"})
    if kind == KIND_HTML:
        bad = HTML_UNSAFE_RE.search(text)
        if bad:
            hard.append({"code": "html_unsafe", "line": text[: bad.start()].count("\n") + 1,
                         "message": f"HTML 正文含不可用标记（命中：{bad.group(0).strip()[:24]}）。"
                                    "站内渲染只保留排版标签与 style/class 属性，"
                                    "script/iframe/style/事件属性/javascript: 会被剥除——去掉这些后重新提交，"
                                    "不要留一份“存得进去但渲染出来缺块东西”的稿"})
    if kind == KIND_FILE:
        files = _files_of(int(article.get("id") or 0))
        if not files:
            hard.append({"code": "file_missing", "line": 0,
                         "message": "附件形态还没有任何文件：请先上传 Word/PPT/Excel/PDF/HTML，"
                                    "否则门户里点不开任何东西"})
        else:
            # 扫描器只能读站内正文: office/PDF 里的文字要抽出来扫, 得引入解析依赖且
            # 还原不了版式（与本批"不假装预览"同一口径）。这里把"没扫"这件事显式交给
            # 审核人逐条确认, 而不是让门禁看起来已经看过附件内容。
            names = "、".join(f["filename"] for f in files[:3]) + ("…" if len(files) > 3 else "")
            warns.append({"code": "attachment_unscanned", "line": 0,
                          "message": f"门禁只扫站内正文，扫不到附件里的文字（{names}）。"
                                     "请下载核对：数字有无出处、患者/客户是否可识别、内部标识有没有带出去"})

    # ── 软警告（不拦，但审核人必须逐条确认）──
    if not _loads(article.get("refs"), []):
        warns.append({"code": "refs_missing", "line": 0, "message": "没有登记外部出处（references）。正文里的外部数字必须有出处，请确认这些都是自己公司的数据"})
    if len(str(article.get("summary") or "")) > SUMMARY_SOFT_MAX:
        warns.append({"code": "summary_long", "line": 0,
                      "message": f"摘要 {len(str(article['summary']))} 字，超过 {SUMMARY_SOFT_MAX} 字在卡片上会被截断"})
    elif not str(article.get("summary") or "").strip():
        warns.append({"code": "summary_empty", "line": 0, "message": "没有摘要，卡片与列表只显示标题"})
    for t in _loads(article.get("tags"), []):
        if str(t) not in {x["tag"] for x in list_tags(include_inactive=True)}:
            warns.append({"code": "tag_uncontrolled", "line": 0, "message": f"标签「{t}」不在受控词表（可能已被停用），侧栏标签云里点不到它"})
    if kind == KIND_HTML and text.strip() and not HTML_TAG_RE.search(text):
        warns.append({"code": "html_no_tags", "line": 0,
                      "message": "形态是 HTML 但正文里没出现任何 HTML 标签，渲染出来就是一段纯文本；"
                                 "如果本来只想写文字，请把形态改回「站内正文」（Markdown）"})
    line = _line_of(text, PII_HINT_RE)
    if line:
        warns.append({"code": "pii_hint", "line": line,
                      "message": f"第 {line} 行出现病例相关字样，请核对是否有患者姓名/就诊号/病历号等可识别信息未脱敏"})
    small = next((m for m in SMALL_SAMPLE_RE.finditer(text)
                  if int(m.group(1)) < SMALL_SAMPLE_THRESHOLD), None)
    if small:
        sline = text[: small.start()].count("\n") + 1
        warns.append({"code": "small_sample", "line": sline,
                      "message": f"第 {sline} 行样本量 {small.group(0)} 偏小，"
                                 f"结论请标注为个案观察、不要外推为普遍水平"})
    review_by = article.get("review_by")
    if isinstance(review_by, (datetime, date)) and review_by < date.today():
        warns.append({"code": "review_expired", "line": 0,
                      "message": f"复审到期日 {_iso(review_by)[:10]} 已过，发布前请确认数字与法规条款仍是最新的"})
    return hard, warns


def body_leak_labels(body: str) -> list[str]:
    """正文里的内部标识命中清单（安全边界，**不分草稿/发布**）。

    给 AI 创作通道在**写入之前**预检用：门禁（gate_article）在提交审核时才拦，
    草稿阶段允许半成品（【待填】、没选栏目），但“把连接串/生成 SQL 写进正文”
    不是进度问题而是安全问题，不能等提交才发现（护栏 §7）。
    扫描器不可用时 `_leak_hits` 返回 guard_unavailable（当成未通过，不静默放行）。
    """
    return [label for label, _ in _leak_hits(body or "")]


def _leak_hits(text: str) -> list[tuple[str, int]]:
    """复用出站守卫的同一份内部标识清单，命中类别翻成可读文案。"""
    try:
        from backend.modules.mind.execution.outbound_guard import find_sensitive_hits
        labels = find_sensitive_hits(text)
    except Exception as exc:  # noqa: BLE001 — 清单拿不到就不能当"已检查通过"
        logger.warning("[content] 内部标识扫描不可用, 按未通过处理: %s", exc)
        return [("guard_unavailable", 0)]
    out = []
    for label in labels:
        out.append((_LEAK_LABELS.get(label, label), 0))
    return out


# ── 状态机 ────────────────────────────────────────────────────────────────────

def _load_for_action(article_id: int) -> dict:
    row = execute_query(_SELECT + " WHERE a.id=%s", (int(article_id),), fetchone=True)
    if not row:
        raise ContentError("内容不存在", 404)
    return row


#: 一次批量动作的上限（与「会话历史批量删除」同口径）：防误全选把整屏状态一次推平，
#: 也让一次请求里的逐条门禁 / 审计 / 流水条数可控。前端按页选（page_size ≤ 50）不会撞到这里。
MAX_BATCH_ACTION = 50


def rows_by_slugs(slugs: list[str]) -> list[dict]:
    """批量动作的寻址预检：``slug → {id, slug, title}``（去重保序，只回库里存在的行）。

    只做寻址，**不做裁决**：状态能不能转、乐观锁冲不冲突，一律留给各条动作自己按**最新行**
    判定（``submit_review`` 等内部会重新 ``_load_for_action``）。预检拿的是清单快照，拿它当
    闸门就会出现“勾选时是待审、执行时已被人改过”的双口径。

    只取三个字段：标题供 UI 点名（ui-resource-display §1：失败清单不能只给裸 slug），
    其余状态字段一概不查，免得批量入口顺手把整行契约当权限判断依据。
    """
    clean = list(dict.fromkeys(str(s or "").strip() for s in slugs if str(s or "").strip()))
    if not clean:
        return []
    placeholders = ",".join(["%s"] * len(clean))
    rows = execute_query(
        f"SELECT id, slug, title FROM adh_content_articles WHERE slug IN ({placeholders})",
        tuple(clean)) or []
    found = {str(r.get("slug") or ""): r for r in rows}
    return [{"id": int(found[s]["id"]), "slug": s, "title": str(found[s].get("title") or "")}
            for s in clean if s in found]


def _set_status(article_id: int, version: int, *, status: str, extra_sets: str = "",
                extra_params: tuple = ()) -> None:
    sql = f"UPDATE adh_content_articles SET status=%s, version=version+1{extra_sets} WHERE id=%s AND version=%s"
    hit = execute_write(sql, (status,) + tuple(extra_params) + (int(article_id), int(version)))
    if not hit:
        raise ContentError("这篇内容已被他人改过，请刷新后重试", 409)


def _log_review(article_id: int, revision_id: int, action: str, comment: str,
                warnings: list[dict] | None, actor: str, channel: str = "human") -> None:
    execute_insert(
        "INSERT INTO adh_content_reviews (article_id, revision_id, action, comment, gate_warnings, actor, actor_channel) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (int(article_id), int(revision_id or 0), action, comment or "",
         json.dumps(warnings or [], ensure_ascii=False), actor, channel))


def submit_review(article_id: int, actor: str, *, expected_version: int | None = None,
                  channel: str = "human") -> dict:
    """提交审核：硬阻断直接拒（逐条原因），软警告随响应返回，审核人必须逐条确认。"""
    row = _load_for_action(article_id)
    version = int(row.get("version") or 1)
    if expected_version is not None and int(expected_version) != version:
        raise ContentError("这篇内容已被他人改过，请刷新后重试", 409)
    status = str(row.get("status"))
    if status == STATUS_IN_REVIEW:
        raise ContentError("已经在审核中了，请等审核结论")
    if status == STATUS_PUBLISHED and int(row.get("head_revision_id") or 0) == int(row.get("live_revision_id") or 0):
        raise ContentError("线上版本没有待发布的改动，无需提交审核")
    body = _revision_body(int(row.get("head_revision_id") or 0))
    hard, warns = gate_article(row, body)
    if hard:
        raise ContentError("不能提交审核：" + "；".join(h["message"] for h in hard), 422)
    _set_status(int(article_id), version, status=STATUS_IN_REVIEW)
    _log_review(int(article_id), int(row.get("head_revision_id") or 0), "submit",
                "提交审核", warns, actor, channel)
    return {"status": STATUS_IN_REVIEW, "warnings": warns,
            "message": "已提交审核" + (f"，有 {len(warns)} 条提醒需要审核人逐条确认" if warns else "")}


def _assert_not_self_review(row: dict, actor: str, revision_id: int) -> None:
    """作者不得自审自己的稿。免审直发是显式例外（走 publish 的 waive 分支并留原因）。"""
    author = _revision_author(revision_id) or str(row.get("created_by") or "")
    if author and author == actor:
        raise ContentError("不能审核自己写的稿。需要直发请走「免审直发」（会留审计原因）", 403)


def _revision_author(revision_id: int) -> str:
    if not int(revision_id or 0):
        return ""
    row = execute_query("SELECT created_by FROM adh_content_revisions WHERE id=%s",
                        (int(revision_id),), fetchone=True)
    return str((row or {}).get("created_by") or "")


def approve_review(article_id: int, actor: str, *, comment: str = "",
                   acknowledged: list[dict] | None = None) -> dict:
    row = _load_for_action(article_id)
    if str(row.get("status")) != STATUS_IN_REVIEW:
        raise ContentError("只有「审核中」的内容才能通过")
    rid = int(row.get("head_revision_id") or 0)
    _assert_not_self_review(row, actor, rid)
    body = _revision_body(rid)
    hard, warns = gate_article(row, body)
    if hard:
        raise ContentError("不能通过：" + "；".join(h["message"] for h in hard), 422)
    if warns:
        acked = {str(w.get("code")) for w in (acknowledged or []) if w.get("acknowledged")}
        missing = [w["message"] for w in warns if w["code"] not in acked]
        if missing:
            raise ContentError("还有提醒没确认完，逐条确认后才能通过：" + "；".join(missing), 409)
    _set_status(int(article_id), int(row.get("version") or 1), status=STATUS_APPROVED)
    _log_review(int(article_id), rid, "approve", comment, acknowledged or warns, actor)
    return {"status": STATUS_APPROVED, "message": "审核通过，可以发布了"}


def reject_review(article_id: int, actor: str, *, comment: str) -> dict:
    reason = str(comment or "").strip()
    if not reason:
        raise ContentError("驳回必须写原因（作者要据此改）")
    row = _load_for_action(article_id)
    if str(row.get("status")) != STATUS_IN_REVIEW:
        raise ContentError("只有「审核中」的内容才能驳回")
    rid = int(row.get("head_revision_id") or 0)
    _set_status(int(article_id), int(row.get("version") or 1), status=STATUS_DRAFT)
    _log_review(int(article_id), rid, "reject", reason, None, actor)
    return {"status": STATUS_DRAFT, "message": "已驳回，作者会看到原因"}


def _release_count(article_id: int) -> int:
    """本篇已发布过几期（``release_seq>0`` 的唯一口径，与 _SELECT 里的子查询同口径）。"""
    row = execute_query("SELECT COUNT(*) AS n FROM adh_content_revisions "
                        "WHERE article_id=%s AND release_seq>0", (int(article_id),), fetchone=True)
    return int((row or {"n": 0})["n"])


def publish_article(article_id: int, actor: str, *, expected_version: int | None = None,
                    waive_review: bool = False, reason: str = "", channel: str = "human",
                    release_label: str = "") -> dict:
    """发布 = ``live := head``，并把 head 记成一个新期次。免审直发必须带原因（入审计），
    且要求调用方已具备发布权限 —— 权限本身由中间件按 ``article:publish`` 门控，本函数不另造判断。

    期次名由发布人给（缺省自动按当月 ``YYYY-MM``）：它是“这一版是什么”的业务名，不是内部
    标识；也不拿 head 的 change_note 当期次名（那是改稿说明，不是对外的期次）。
    回执**不下发 revision id**（护栏 §7），只回期次号：旧写法把 ``live_revision_id`` 直接当
    动作响应返给前端，与本模块“revision 指针不外泄”的口径相互打脸。
    """
    row = _load_for_action(article_id)
    version = int(row.get("version") or 1)
    if expected_version is not None and int(expected_version) != version:
        raise ContentError("这篇内容已被他人改过，请刷新后重试", 409)
    status = str(row.get("status"))
    head = int(row.get("head_revision_id") or 0)
    if not head:
        raise ContentError("还没有可发布的正文版本")

    if waive_review:
        note = str(reason or "").strip()
        if not note:
            raise ContentError("免审直发必须写明原因（会进审计，供事后追溯）")
        hard, _warns = gate_article(row, _revision_body(head))
        if hard:
            raise ContentError("不能发布：" + "；".join(h["message"] for h in hard), 422)
        action = "waive"
    else:
        if status == STATUS_ARCHIVED:
            raise ContentError("这篇已下架。要放回下架前那一版请用「重新上架」；要发新的正文请先提交审核通过，"
                               "或勾选「免审直发」并写明原因")
        if status != STATUS_APPROVED:
            raise ContentError("内容还没有审核通过。需要跳过审核请显式勾选「免审直发」并写明原因")
        action = "publish"

    sets = ", live_revision_id=%s, published_at=COALESCE(published_at, NOW()), archived_at=NULL"
    live_before = int(row.get("live_revision_id") or 0)
    _set_status(int(article_id), version, status=STATUS_PUBLISHED, extra_sets=sets,
                extra_params=(head,))
    # CAS 已成功 ⇒ 并发的第二个发布者在上面就 409 了，走到这里的只有胜者，期次号不会重号
    previous = _release_of_revision(live_before)
    marked = _mark_release(int(article_id), head, release_label)
    _log_review(int(article_id), head, action, str(reason or ""), None, actor, channel)
    logger.info("[content] 发布 id=%s revision=%s release=%s waive=%s by=%s",
                article_id, head, marked["release"], waive_review, actor)
    total = _release_count(article_id)
    message = ("已发布，全体登录用户可见（不受数据权限过滤）" + ("（免审直发）" if waive_review else "")
               + f"。本期：第 {marked['release']} 期〈{marked['label']}〉，本篇共 {total} 期")
    if str(row.get("versioning") or VERSIONING_KEEP) == VERSIONING_LATEST:
        message += ("。策略是「只保留最新版本」：读者只看这一期"
                    + (f"，上一期〈{previous['label']}〉不再对读者展示（编排面版本清单与审核流水仍保留，"
                       "切回「保留历史期次」即恢复可见）" if previous["release"] else ""))
    elif total > 1:
        message += "；读者可在页面翻看历史期次"
    return {"status": STATUS_PUBLISHED, "release": marked["release"],
            "release_label": marked["label"], "release_count": total, "message": message}


def archive_article(article_id: int, actor: str, *, expected_version: int | None = None,
                    reason: str = "", channel: str = "human") -> dict:
    row = _load_for_action(article_id)
    live = int(row.get("live_revision_id") or 0)
    if not live:
        raise ContentError("这篇没有在线版本，无需下架")
    version = int(row.get("version") or 1)
    if expected_version is not None and int(expected_version) != version:
        raise ContentError("这篇内容已被他人改过，请刷新后重试", 409)
    _set_status(int(article_id), version, status=STATUS_ARCHIVED,
                extra_sets=", live_revision_id=0, archived_at=NOW()", extra_params=())
    _log_review(int(article_id), live, "archive", str(reason or ""), None, actor, channel)
    logger.info("[content] 下架 id=%s by=%s", article_id, actor)
    # 下架自动撤回语料本批未接入，但“该做而没做”必须显式说出来：已同步过的内容
    # 下架后语料还在库里，AS-BOT 会继续拿它回答用户（比不提示的危害直接大一档）。
    out = {"status": STATUS_ARCHIVED,
           "message": "已下架，门户不再展示。"}
    synced = execute_query(
        "SELECT kb_name, doc_title FROM adh_content_kb_sync "
        "WHERE article_id=%s AND state='synced' ORDER BY id DESC LIMIT 1",
        (int(article_id),), fetchone=True)
    if synced:
        out["warning"] = (f"已同步进知识库「{synced.get('kb_name')}」的语料（{synced.get('doc_title')}）"
                          "还在那儿 —— 下架只收门户入口，AS-BOT 仍会检索到这一版。"
                          "要彻底不再被引用，请到「内容中心 → 知识库文档」删掉那篇（自动撤回未接入，不做静默处理）。")
    return out


def republish_article(article_id: int, actor: str, *, expected_version: int | None = None,
                      reason: str = "", channel: str = "human") -> dict:
    """重新上架 = 把**下架时在线的那一版**放回门户（``live := 下架前那一版``）。

    为什么这一条不需要重新审核，而「发布」需要：被放回的这一版当初就是审核通过才上线的，
    下架只是收走门户入口，审核结论对它仍然有效；而发布把 ``live`` 指向 head，head 可能是
    从没审过的新改动。所以这里**不拿 head 上线**：下架之后改的正文留在 head 里不动，
    要发那一版还得走审核（回执里会把这件事显式说出来，不让人以为改动也跟着上了线）。

    追溯不到下架那一版（没有 archive 流水）时直接拒，不回退成“那就发 head”——
    那是把未审内容当已审内容放出去（与 ``no-silent-degradation`` 同一口径）。

    不重跑提交门禁：正文按 revision 存且已发布过的 revision 不得原地改，放回的这一版
    与当初上线时逐字节相同，重跑只能得到同一个结论（要改口径就得走发布那条重新审核的路）。
    """
    row = _load_for_action(article_id)
    if str(row.get("status")) != STATUS_ARCHIVED:
        raise ContentError("只有「已下架」的内容才能重新上架")
    version = int(row.get("version") or 1)
    if expected_version is not None and int(expected_version) != version:
        raise ContentError("这篇内容已被他人改过，请刷新后重试", 409)
    if int(row.get("live_revision_id") or 0):
        # status=archived 但 live 不归零 = 数据不一致，不能拿“再上架一次”把它洗成正常
        raise ContentError("这篇当前仍挂在门户上，无需上架；请先下架再试", 409)
    back = int(row.get("last_archived_revision_id") or 0)
    if not back:
        raise ContentError("找不到下架时在线的那一版（审核流水里没有下架记录），无法直接上架；"
                           "请走「提交审核 → 审核通过 → 发布」重新上线", 409)
    head = int(row.get("head_revision_id") or 0)
    # head 就是那一版 → 线上与编辑中同一版，回到 published；
    # head 是下架后改的新版 → status 描述 head 的工作流态，它没审过就是 draft（与
    # “编辑已发布内容只进 head”同一口径），live 指向放回的旧版。
    next_status = STATUS_PUBLISHED if head == back else STATUS_DRAFT
    _set_status(int(article_id), version, status=next_status,
                extra_sets=", live_revision_id=%s, archived_at=NULL", extra_params=(back,))
    _log_review(int(article_id), back, "republish", str(reason or ""), None, actor, channel)
    logger.info("[content] 重新上架 id=%s revision=%s by=%s", article_id, back, actor)
    shown = _release_of_revision(back)
    out = {"status": next_status, "release": shown["release"], "release_label": shown["label"],
           "message": "已重新上架，门户恢复展示下架前那一版（第 "
                      f"{shown['release']} 期〈{shown['label']}〉，这一版当初审核通过才上线，"
                      "不需要重新审核）。"}
    if head and head != back:
        out["warning"] = ("这篇在下架之后又改过正文：改动没跟着上线（那一版没经过审核）。"
                          "要发这一版请「提交审核」通过后发布，或走「免审直发」并写明原因。")
    return out


def rollback_to_release(article_id: int, actor: str, release: int, *,
                        expected_version: int | None = None, reason: str = "",
                        channel: str = "human") -> dict:
    """切换展示版本（回滚）= ``live := 第 N 期``，不重写正文、不分配新期次。

    与 :func:`republish_article` 同一个论证：被放回的这一版当初就是审核通过才上线的，
    审核结论对它仍然有效，所以这一步不重走审核、也不重跑提交门禁。但与发布不同，它
    **不拿 head 上线**：head 里没审过的改动继续留在草稿位（回执里显式说出来）。

    只接受 ``release_seq`` 命中的已发布期次：期次悬空、或想拿一个从未上线过的 revision
    “切上去”一律拒（那是把未审内容当已审内容放出去），不回退成“那就发 head”。
    本动作只改门户指向，不改正文、不新建 revision，也不给“只保留最新”策略偷偷放开历史。
    """
    row = _load_for_action(article_id)
    live = int(row.get("live_revision_id") or 0)
    if not live:
        raise ContentError("这篇当前没有在线版本：要用「发布」或「重新上架」上线，"
                           "切换展示版本只适用于已在线的内容")
    version = int(row.get("version") or 1)
    if expected_version is not None and int(expected_version) != version:
        raise ContentError("这篇内容已被他人改过，请刷新后重试", 409)
    target = _revision_by_release(int(article_id), int(release))
    if target == live:
        shown_now = _release_of_revision(live)
        raise ContentError(f"第 {shown_now['release']} 期〈{shown_now['label']}〉已经在线上，"
                           "无需切换", 409)
    head = int(row.get("head_revision_id") or 0)
    # 与 republish 同一口径：status 描述的是 head 的工作流态 —— 回滚后改过的新版没审过就是 draft。
    next_status = STATUS_PUBLISHED if head == target else STATUS_DRAFT
    _set_status(int(article_id), version, status=next_status,
                extra_sets=", live_revision_id=%s, archived_at=NULL", extra_params=(target,))
    shown = _release_of_revision(target)
    _log_review(int(article_id), target, "rollback",
                str(reason or "") or f"切换展示版本到第 {shown['release']} 期", None, actor, channel)
    logger.info("[content] 切换展示版本 id=%s %s→%s by=%s", article_id, live, target, actor)
    out = {"status": next_status, "release": shown["release"], "release_label": shown["label"],
           "message": f"已把门户展示版本切换到第 {shown['release']} 期〈{shown['label']}〉。"
                      "这一期当初审核通过才上线，不需要重新审核。"}
    if head and head != target:
        out["warning"] = ("这篇的草稿位还有一份没审过的改动，它没跟着上线。"
                          "要发那一版请「提交审核」通过后发布，或走「免审直发」并写明原因。")
    if str(row.get("versioning") or VERSIONING_KEEP) == VERSIONING_LATEST:
        out["note"] = ("这篇的策略是「只保留最新版本」：读者只看得到现在这一期"
                       "（历史期次仍可在编排面翻看）。")
    return out


# ── 审核工作台 ────────────────────────────────────────────────────────────────

def pending_reviews(*, page: int = 1, page_size: int = 20) -> dict:
    """待审队列 + 到期复核清单（两类都要有人处理）。"""
    page = max(1, int(page or 1))
    page_size = min(50, max(1, int(page_size or 20)))
    where = "a.status IN (%s, %s)"
    params = (STATUS_IN_REVIEW, STATUS_APPROVED)
    total = int((execute_query(
        f"SELECT COUNT(*) AS n FROM adh_content_articles a WHERE {where}", params, fetchone=True) or {"n": 0})["n"])
    rows = execute_query(
        _SELECT + f" WHERE {where} ORDER BY a.updated_at ASC LIMIT %s OFFSET %s",
        params + (page_size, (page - 1) * page_size)) or []
    items = []
    for r in rows:
        rid = int(r.get("head_revision_id") or 0)
        hard, warns = gate_article(r, _revision_body(rid))
        item = _article_out(r)
        item["gate_hard"] = hard
        item["gate_warnings"] = [{**w, "acknowledged": False} for w in warns]
        item["submitted_by"] = _revision_author(rid)
        items.append(item)
    expired = execute_query(
        _SELECT + f" WHERE {LIVE_WHERE} AND a.review_by IS NOT NULL AND a.review_by < CURDATE()"
        " ORDER BY a.review_by ASC LIMIT 20") or []
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "review_expired": [_article_out(r) for r in expired]}


def review_draft(article_id: int) -> dict:
    """审核人读稿视图: head 正文 + 当时门禁结果 + 线上版本差异提示。

    与 ``pending_reviews`` 同一份组装口径（都过 :func:`gate_article`），保证审核人在
    队列里看到的警告与点进详情后看到的完全一致 —— 两处算法不同就会出现
    "队列里没警告、点进去一堆警告"。
    """
    row = execute_query(_SELECT + " WHERE a.id=%s", (int(article_id),), fetchone=True)
    if not row:
        raise ContentError("内容不存在或已被删除", 404)
    rid = int(row.get("head_revision_id") or 0)
    body = _revision_body(rid)
    hard, warns = gate_article(row, body)
    item = _article_out(row, body=body, with_body=True)
    item["gate_hard"] = hard
    item["gate_warnings"] = [{**w, "acknowledged": False} for w in warns]
    item["submitted_by"] = _revision_author(rid)
    # 改动是否只是新增（线上还没有版本）: 审核人关心"改了哪"胜过"写了啥"
    item["is_first_release"] = not int(row.get("live_revision_id") or 0)
    item["files"] = _files_of(int(row["id"]))
    return item


def review_history(article_id: int) -> list[dict]:
    rows = execute_query(
        "SELECT action, comment, gate_warnings, actor, actor_channel, revision_id, created_at "
        "FROM adh_content_reviews WHERE article_id=%s ORDER BY id DESC LIMIT 50", (int(article_id),)) or []
    return [{"action": r["action"], "comment": r.get("comment") or "",
             "gate_warnings": _loads(r.get("gate_warnings"), []),
             "actor": r.get("actor") or "", "actor_channel": r.get("actor_channel") or "human",
             "revision_id": int(r.get("revision_id") or 0), "created_at": _iso(r.get("created_at"))}
            for r in rows]


def revision_list(article_id: int) -> list[dict]:
    """版本清单（编辑器用）：已发布期次与改稿历史同列，靠 ``release``/``is_released`` 分区。

    这个接口本就只对可编辑者开放（``GET /articles/{id}/revisions`` 落在 ``article:manage``），
    所以**不受 versioning 策略限制**：“只保留最新”只收读者展示面，编排面必须能看到全量
    （否则历史期次变成只有服务端知道的隐性事实）。
    """
    rows = execute_query(
        "SELECT id, change_note, created_by, created_at, release_seq, release_label, released_at "
        "FROM adh_content_revisions WHERE article_id=%s ORDER BY id DESC LIMIT 50", (int(article_id),)) or []
    live = int((execute_query("SELECT live_revision_id FROM adh_content_articles WHERE id=%s",
                             (int(article_id),), fetchone=True) or {"live_revision_id": 0})["live_revision_id"])
    return [{"id": int(r["id"]), "change_note": r.get("change_note") or "",
             "created_by": r.get("created_by") or "", "created_at": _iso(r.get("created_at")),
             "is_live": int(r["id"]) == live,
             "release": int(r.get("release_seq") or 0),
             "release_label": str(r.get("release_label") or ""),
             "is_released": int(r.get("release_seq") or 0) > 0,
             "released_at": _iso(r.get("released_at"))} for r in rows]


# ── 附件（kind=file 的载体; 上传通道落 backend/common/object_storage.py）────────
#
# 三条必须说在代码里的口径:
# 1. **附件按条目归属, 不进 revision 版本链**（表里没有 revision_id）。正文有版本与
#    回滚, 附件只有"当前挂着的那几个"。后果是替换/删除附件**不会**把已发布内容打回
#    草稿, 门户下载立即变 —— 这不是实现疏漏而是本批的显式取舍, 所以上传响应与编辑器
#    都要把这件事讲出来, 不能让作者以为附件也走了审核。
# 2. **类型只按扩展名白名单**, 不做魔数嗅探: 嗅探只能识破"改了扩展名的图片", 识不了
#    宏文档, 反而给人"已查毒"的错觉。真防病毒属病毒扫描网关的活, 当前未接入 ——
#    不假装做过（no-silent-degradation）。
# 3. **下载一律 attachment**（见 CONTENT_FILE_TYPES 上方注释）。
#
# 门禁: kind=file 且无附件 = 硬阻断（gate_article 里的 file_missing）—— 否则门户里
# 点进去是一个打不开的空条目。

def _file_ext(filename: str) -> str:
    """小写扩展名（含点）; 无扩展名返回空串。路径分隔符先剥掉, 只认最后一段。"""
    name = str(filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name or name.endswith("."):
        return ""
    return "." + name.rsplit(".", 1)[1].lower()


def _file_label(filename: str) -> str:
    return CONTENT_FILE_TYPES.get(_file_ext(filename), {}).get("label", "附件")


def _files_of(article_id: int) -> list[dict]:
    """条目附件清单。门禁判存在、响应带清单都走这一份 SQL, 不各写各的。

    只回业务可读字段: ``object_key`` 是对象存储内部标识, 不出服务层（护栏 §7）。
    """
    if not int(article_id or 0):
        return []
    rows = execute_query(
        "SELECT id, filename, mime, size, sort, created_at FROM adh_content_files "
        "WHERE article_id=%s ORDER BY sort, id", (int(article_id),)) or []
    return [{"file_id": int(r["id"]), "filename": r.get("filename") or "",
             "label": _file_label(r.get("filename") or ""), "mime": r.get("mime") or "",
             "size": int(r.get("size") or 0), "uploaded_at": _iso(r.get("created_at"))}
            for r in rows]


def list_files(article_id: int) -> list[dict]:
    return _files_of(int(article_id or 0))


def upload_file(article_id: int, filename: str, data: bytes, actor: str) -> dict:
    """把文件挂到条目上。

    次序: 校验 → 插行 → 传对象存储; 上传失败**回滚那一行**, 不留"清单里有文件但
    点不开"的悬空行。校验全部在落盘之前, 失败不产生任何副作用。
    """
    name = str(filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not name:
        raise ContentError("文件名为空，无法作为附件上传")
    ext = _file_ext(name)
    spec = CONTENT_FILE_TYPES.get(ext)
    if not spec:
        supported = " / ".join(sorted({f'{v["label"]}（{k}）' for k, v in CONTENT_FILE_TYPES.items()}))
        raise ContentError(f"不支持的附件类型「{ext or '无扩展名'}」。当前只收：{supported}")
    size = len(data or b"")
    if not size:
        raise ContentError(f"「{name}」是 0 字节空文件，确认导出没出错再传")
    if size > MAX_CONTENT_FILE_SIZE:
        raise ContentError(f"「{name}」{size / 1048576:.1f}MB，超过单文件上限 "
                           f"{MAX_CONTENT_FILE_SIZE // 1048576}MB，请拆分或压缩后再传")
    if not execute_query("SELECT id FROM adh_content_articles WHERE id=%s",
                         (int(article_id),), fetchone=True):
        raise ContentError("内容不存在，请先保存草稿再上传附件", 404)
    exist = _files_of(int(article_id))
    if len(exist) >= MAX_CONTENT_FILES_PER_ARTICLE:
        raise ContentError(f"一条内容最多挂 {MAX_CONTENT_FILES_PER_ARTICLE} 个附件，"
                           f"请先删掉不需要的再传")
    if any(str(f["filename"]).lower() == name.lower() for f in exist):
        raise ContentError(f"已经有同名附件「{name}」。换文件请先删原附件再上传 —— "
                           f"两个同名文件在下载清单里分不清")

    key = build_content_file_key(int(article_id), uuid.uuid4().hex[:12], name)
    file_id = int(execute_insert(
        "INSERT INTO adh_content_files (article_id, object_key, filename, mime, size, sort) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (int(article_id), key, name, spec["mime"], size, len(exist) + 1)))
    try:
        get_object_storage().upload_bytes(key, data, content_type=spec["mime"])
    except Exception as exc:  # noqa: BLE001 — 上传失败必须撤销登记再显式报错
        execute_write("DELETE FROM adh_content_files WHERE id=%s", (file_id,))
        # 原始错误只进服务端日志（护栏 §7）; 对外给可读结论与下一步
        logger.warning("[content] 附件写入对象存储失败 article=%s name=%s: %s", article_id, name, exc)
        raise ContentError("附件存储写入失败，本次登记已撤销。请稍后重试；持续失败请联系管理员"
                           "检查对象存储配置", 502) from exc
    logger.info("[content] 附件上传 file=%s article=%s name=%s by=%s", file_id, article_id, name, actor)
    item = next((f for f in _files_of(int(article_id)) if f["file_id"] == file_id),
                {"file_id": file_id, "filename": name, "label": spec["label"],
                 "mime": spec["mime"], "size": size, "uploaded_at": _now(),
                 "_partial": True})
    return item


def delete_file(article_id: int, file_id: int, actor: str) -> dict:
    """从条目去掉附件。先删行（下载入口立即消失）, 再删对象。

    对象删除失败**不回滚**行, 但必须把"桶里还剩一个没人引用的文件"这件事说出来
    （warning 字段随响应给前端）—— 静默假装清干净会让存储无声膨胀且无人知道。
    """
    row = execute_query("SELECT object_key, filename FROM adh_content_files "
                        "WHERE id=%s AND article_id=%s", (int(file_id), int(article_id)),
                        fetchone=True)
    if not row:
        raise ContentError("附件不存在（可能已被删除），请刷新后重试", 404)
    execute_write("DELETE FROM adh_content_files WHERE id=%s", (int(file_id),))
    logger.info("[content] 附件删除 file=%s article=%s name=%s by=%s",
                file_id, article_id, row.get("filename"), actor)
    out: dict = {"deleted": True, "filename": str(row.get("filename") or "")}
    removed = False
    try:
        removed = bool(get_object_storage().delete(str(row.get("object_key") or "")))
    except Exception as exc:  # noqa: BLE001 — 清理失败属旁路, 不影响"已从内容去掉"这个事实
        logger.warning("[content] 附件对象删除失败 file=%s: %s", file_id, exc)
    if not removed:
        out["warning"] = ("内容里已经没有这个附件了，但对象存储里的文件没删掉（不影响展示与下载，"
                          "只占存储）。请稍后重试或请管理员清理。")
    return out


def file_for_download(article_id: int, file_id: int, *, allow_draft: bool = False) -> dict:
    """下载寻址（服务端内部用）。返回 object_key/filename/mime; object_key 不外泄。

    ``allow_draft=False``（默认，读者通道）要求条目**当前在线**才给附件。这一道门必须开在
    服务层而不是靠权限码：``article:read`` 的 ``api_pattern`` 写的是
    ``/api/content/articles*``，而 ``api_permission`` 用 fnmatch 匹配（``*`` 连 ``/`` 一起吃），
    根本分不出“列表”和“某条目的附件子资源”。所以不开这个门 = 首页资讯区一上线，
    **任何人凭 slug + file_id 就能下走未发布草稿的附件**（Word/PDF 原稿）。

    可编辑者与审核人走 ``allow_draft=True``：他们本来就要下草稿附件才能编排与审稿，
    能力由 ``article:manage`` / ``article:review`` 各自的前缀裁决，不在这里重复判断。
    """
    if not allow_draft:
        # 列序与 _load_for_action 用的那条一致，以便下游（含测试替身）只认一份形状
        row = execute_query("SELECT status, live_revision_id FROM adh_content_articles WHERE id=%s",
                            (int(article_id),), fetchone=True)
        if not row:
            raise ContentError("内容不存在或已被删除", 404)
        if int(row.get("live_revision_id") or 0) == 0 or str(row.get("status")) == STATUS_ARCHIVED:
            # 与 get_article 对未发布给 404 同口径: 不区分“存在但没发布”与“不存在”，
            # 否则附件探测能列出哪些草稿在等人写。
            raise ContentError("附件不存在或所属内容尚未发布", 404)
    row = execute_query("SELECT object_key, filename, mime FROM adh_content_files "
                        "WHERE id=%s AND article_id=%s", (int(file_id), int(article_id)),
                        fetchone=True)
    if not row:
        raise ContentError("附件不存在（可能已被删除），请刷新后重试", 404)
    return {"object_key": str(row.get("object_key") or ""),
            "filename": str(row.get("filename") or "attachment"),
            "mime": str(row.get("mime") or "")}
