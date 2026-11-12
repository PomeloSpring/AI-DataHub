"""内容中心 API — 内容生产 + 审核 + 发布。

挂载: ``/api/content``（backend/app/main.py，platform 组）

职责边界（用户 2026-11-04 拍板）: 本模块只做 **创作 → 审核 → 发布**。
**资讯门户不是独立模块**（11-06 改拍）: 面向全员的消费面直接落在首页（资讯流 + ``/read/:slug``）,
它只消费本模块的发布结果 —— 把 ``live_revision_id`` 指向某个版本的动作**只有这里的
publish / republish / rollback 三条路**, 因此门户里出现的每篇内容必然经过审核（或显式的
免审直发 + 审计原因），不存在"门户自己写稿自己上"的旁路。
``republish``（重新上架）只把**下架时在线的那一版**放回门户 —— 那一版当初就是审核通过
才上线的, 下架只收入口不推翻审核结论; 它**不会**把下架后新改的 head 顺带放出去。
``rollback``（切换展示版本）同理: 只把 live 指回一**已发布期次**, 未发布过的 revision 切不上去。

期次口径（用户 2026-11-12）：一个主题 = 一条条目，发布时给被发布的那一版分配期次（第 N 期 +
业务名）。门户展示的仍是 live 版本（= 当前这一期），读者在阅读页翻看历史期次；
``versioning=latest_only`` 的条目只把最新一期给读者（旧期次仅编排面留档）。

权限门控口径（**唯一裁决点是 API 权限中间件**，见 backend/common/api_permission.py）:

===================  =========================================  ==================
权限码                api_pattern / method                       本模块对应端点
===================  =========================================  ==================
``article:manage``   GET,POST,PUT,PATCH,DELETE articles/categories/tags/promotions   写草稿 + 读自己要管的清单 + 报告提升
``article:review``   GET,POST /api/content/reviews*            待审队列/读稿/通过/驳回
``article:publish``  POST /api/content/publish*                发布/下架/重新上架/切换展示版本
===================  =========================================  ==================

面向读者的 ``article:read``（GET articles/categories/tags，见 home_feed_migration.sql）同样
命中本模块的只读端点：中间件是"任一已授权码命中即放行"，所以只勾编辑权的角色照常能加载
管理清单（零回归）。行级可见性（草稿只对可编辑者可见、未发布不给附件）开在服务层，
不靠权限码分隔子资源 —— ``fnmatch`` 的 ``*`` 连 ``/`` 一起吃，pattern 做不了减法。

**为什么发布与审核不复用 ``/api/content/articles/{id}/publish``**：中间件按
``api_pattern × api_method`` 做笛卡尔积匹配，模式集里做不了减法。若发布也挂在
``/api/content/articles*`` 的 POST 下，那么持有 ``article:manage`` 的人就自动获得发布能力
—— 而"谁能写草稿"与"谁能对全员发布"必须是两件事（分级审核的前提）。因此发布与审核
各自走独立前缀，让 ``api_pattern`` 单独就能裁决，handler 内不另造第二套权限判断。

身份一律取服务端 JWT（护栏 §2 身份 fail-closed）: 作者/审核人/发布人字段由这里注入，
不信任请求体里的任何身份字段。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from urllib.parse import quote

from backend.common.auth import get_current_user
from backend.common.object_storage import get_object_storage
from backend.modules.platform.services import content_promote_service as promote_svc
from backend.modules.platform.services import content_service as svc

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 请求模型 ──────────────────────────────────────────────────────────────────

class CategoryPayload(BaseModel):
    key: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    sort: Optional[int] = None
    is_active: Optional[int] = None


class TagPayload(BaseModel):
    tag: Optional[str] = None
    sort: Optional[int] = None
    is_active: Optional[int] = None


class ArticlePayload(BaseModel):
    """内容条目写入体。

    注意**没有 author / actor 字段**: 作者由服务端身份注入，请求体里传了也不认。
    """
    title: Optional[str] = None
    slug: Optional[str] = None
    summary: Optional[str] = None
    category: Optional[str] = None          # 栏目 key 或 id（业务标识，不要求前端传内部 id）
    tags: Optional[list[str]] = None
    refs: Optional[list[dict]] = None
    kind: Optional[str] = None
    body: Optional[str] = None
    link_url: Optional[str] = None
    link_label: Optional[str] = None
    pinned: Optional[int] = None
    featured: Optional[int] = None
    review_by: Optional[str] = None
    versioning: Optional[str] = None      # keep_history | latest_only（读者可见性策略）
    change_note: Optional[str] = None
    expected_version: Optional[int] = None


class ReviewAction(BaseModel):
    comment: Optional[str] = ""
    acknowledged: Optional[list[dict]] = None


class PublishAction(BaseModel):
    action: str = "publish"                 # publish | archive | republish | rollback
    expected_version: Optional[int] = None
    waive_review: bool = False              # 免审直发（需 article:publish + 必填原因）
    reason: Optional[str] = ""
    release_label: Optional[str] = ""       # 期次名（发布人给，缺省自动按当月）
    release: Optional[int] = None           # 目标期次号（仅 rollback；不接受 revision id）


class PromotePayload(BaseModel):
    """从报告中心提交到内容管理审核。

    **没有 author/actor 字段**：作者与提交人由服务端身份注入（护栏 §2）。
    也没有期次名字段：期次在发布那一步才产生，由发布人命名（开两个入口等于两处各存一份）。
    """
    report_id: int
    category: Optional[str] = None          # 栏目 key（新建时必须选；追加到已有条目可沿用）
    title: Optional[str] = None
    summary: Optional[str] = None
    versioning: Optional[str] = None
    submit: Optional[bool] = True           # 默认直接提交审核（不勾 = 只存草稿）


class BatchSlugs(BaseModel):
    """批量写动作请求体（提交审核 / 删除）。

    标识用 ``slug``（业务可读名，与单条端点寻址同一口径），不要求调用方传内部 id。
    """
    slugs: list[str]


class BatchPublishAction(BaseModel):
    """批量发布类动作请求体。**没有 waive_review 字段**：免审直发是逐条写明原因的
    显式例外，不做成批量开关（否则一次勾选就能把未审内容成批上线）。"""
    slugs: list[str]
    action: str = "publish"                 # publish | archive | republish
    reason: Optional[str] = ""


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _actor(user: dict) -> str:
    """操作人显示名（username）。id 不外泄，也不作为判断依据。"""
    return str(user.get("username") or user.get("user_id") or "")


def _can_edit(user: dict) -> bool:
    return svc.viewer_can_edit(user)


def _fail(exc: svc.ContentError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _audit(user: dict, request: Request, action: str, article_id: int, detail: str) -> None:
    """状态转换落系统审计（与内容域审核流水双记：审核流水是真值源，这里供全局审计查询）。

    best-effort：审计失败不得影响主链路（护栏 §9），``log_audit`` 内部已吞异常。
    """
    try:
        from backend.common.auth import log_audit
        log_audit(int(user.get("user_id") or 0), _actor(user), action,
                  target_type="content_article", target_id=int(article_id or 0),
                  detail=detail, ip_address=(request.client.host if request.client else ""),
                  module="content")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[content] 审计写入失败（不影响主链路）: %s", exc)


# ── 批量动作（不新增权限码：逐条走单条实现，落在单条已登记的 api_pattern 里）────
#
# 批量与单条的差别只在“循环 + 逐条回报”，不在“语义”：同一个状态机、同一道门、同一条
# 审计。所以这里不写第二套判断（也不拿清单快照当闸门），只把 svc 的单条函数按顺序调一遍。
# 部分成功是常态而不是异常：未审核的不得发布、已上线的不得删、乐观锁冲突 —— 都逐条给原因，
# 不得静默跳过、不得把部分成功谎称成全部成功（no-silent-degradation）。

#: 动作 → （审计 action, 审计默认文案, 回执里的动词）
#: **rollback（切换展示版本）不在批量清单里**：切到哪一期必须逐条显式指定，
#: 做成批量开关就等于把“整屏内容换版本”藏进一次勾选里（与不代开免审直发同一条纪律）。
_BATCH_KINDS: dict[str, tuple[str, str, str]] = {
    "submit": ("content.article.submit", "提交审核", "提交审核"),
    "publish": ("content.article.publish", "发布上线", "发布"),
    "archive": ("content.article.archive", "下架", "下架"),
    "republish": ("content.article.republish", "重新上架", "上架"),
    "delete": ("content.article.delete", "删除草稿", "删除"),
}


def _batch_run(action: str, article_id: int, actor: str, reason: str) -> dict:
    """单条动作的唯一入口（与上面各单条端点调的是同一个函数，不另列一份判据）。

    批量**不传** ``expected_version``：状态机内部本来就重新加载最新行 + CAS
    （``_set_status`` 的 ``WHERE version=%s``），拿页面快照的版本去卡，只会把“别人改过
    但转换依然合法”的条目成批拒掉；而真正的并发写冲突仍由 CAS 拦成 409。

    批量也不代开免审直发：``waive_review`` 是需要逐条写明理由并入审计的显式例外，
    做成批量开关就等于给分级审核开了个暗门。未审核的条目逐条拒并说清下一步。
    """
    if action == "submit":
        return svc.submit_review(article_id, actor)
    if action == "publish":
        return svc.publish_article(article_id, actor)
    if action == "archive":
        return svc.archive_article(article_id, actor, reason=reason)
    if action == "republish":
        return svc.republish_article(article_id, actor, reason=reason)
    if action == "delete":
        return svc.delete_article(article_id, actor)
    raise HTTPException(status_code=400,
                        detail=f"批量动作取值不对（只支持 {'/'.join(_BATCH_KINDS)}）")


def _batch_action(request: Request, user: dict, action: str, slugs: list[str],
                  reason: str = "") -> dict:
    """批量状态机动作：逐条走与单条完全相同的实现，失败逐条给原因，不整批回滚。

    为什么不整批事务：这些转换本来就各自带 CAS 与审核流水，把已成功的回滚掉只会让人
    以为“什么都没发生”；真实语义是“能做的做了，不能做的逐条说清为什么”。
    """
    if action not in _BATCH_KINDS:
        raise HTTPException(status_code=400,
                            detail=f"批量动作取值不对（只支持 {'/'.join(_BATCH_KINDS)}）")
    audit_action, audit_default, verb = _BATCH_KINDS[action]
    actor = _actor(user)
    picked = list(dict.fromkeys(str(s or "").strip() for s in (slugs or []) if str(s or "").strip()))
    if not picked:
        raise HTTPException(status_code=400, detail="请先勾选要处理的内容")
    if len(picked) > svc.MAX_BATCH_ACTION:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多处理 {svc.MAX_BATCH_ACTION} 篇（本次 {len(picked)} 篇），请分批操作")

    rows = {r["slug"]: r for r in svc.rows_by_slugs(picked)}
    succeeded: list[dict] = []
    failed: list[dict] = []
    for slug in picked:
        row = rows.get(slug)
        if row is None:
            # 勾了但库里没了（已被他人删除）：绝不能当成“反正目标态已达成”静默成功
            failed.append({"slug": slug, "title": "", "reason": "内容不存在或已被删除，请刷新清单"})
            continue
        try:
            out = _batch_run(action, int(row["id"]), actor, reason)
        except svc.ContentError as exc:
            # 拒因来自单条动作本身（未审核不得发布、已上线不能删、仍在审核中），原样转述不编造
            failed.append({"slug": slug, "title": row["title"], "reason": exc.detail})
            continue
        _audit(user, request, audit_action, int(row["id"]),
               f"{reason.strip() or audit_default}（批量）" if action in ("archive", "republish")
               else f"{audit_default}（批量）")
        entry: dict = {"slug": slug, "title": row["title"],
                       "status": out.get("status", ""), "message": out.get("message", "")}
        # 旁路没做干净的提醒（语料还在库里、附件没删干净）与待确认的软提醒都得跟着条目回给前端
        if out.get("warning"):
            entry["warning"] = out["warning"]
        if out.get("warnings"):
            entry["warnings"] = out["warnings"]
        succeeded.append(entry)

    if not succeeded:
        # 一篇都没成: 说清"本次没有改动生效", 别让人读成"已提交审核 0 篇"这种半截结论
        message = f"{len(failed)} 篇未处理（原因逐条列出），本次没有改动生效"
    elif failed:
        message = f"已{verb} {len(succeeded)} 篇；{len(failed)} 篇未处理（原因逐条列出）"
    else:
        message = f"已{verb} {len(succeeded)} 篇"
    logger.info("[content] 批量%s by=%s ok=%s fail=%s", verb, actor, len(succeeded), len(failed))
    return {"action": action, "succeeded": succeeded, "failed": failed,
            "success_count": len(succeeded), "failed_count": len(failed), "message": message}


# ── 读：栏目 / 标签 / 条目 ─────────────────────────────────────────────────────

@router.get("/categories")
def read_categories(include_inactive: int = Query(0)):
    """栏目清单（含每个栏目的在线条目数）。侧栏栏目名一律来自这里，不硬编码。"""
    try:
        return {"items": svc.list_categories(include_inactive=bool(include_inactive))}
    except svc.ContentError as e:
        raise _fail(e) from None


@router.get("/tags")
def read_tags(include_inactive: int = Query(0)):
    """受控标签词表 + 命中篇数。前端「热门标签」只渲染 count>=2 的标签。"""
    try:
        return {"items": svc.list_tags(include_inactive=bool(include_inactive))}
    except svc.ContentError as e:
        raise _fail(e) from None


@router.get("/articles")
def read_articles(
    category: str = Query(""),
    exclude_category: str = Query("", description="排除栏目（key 或 id）：首页资讯流不重复展示简介卡内容"),
    kind: str = Query(""),
    tag: str = Query(""),
    q: str = Query(""),
    status: str = Query(""),
    mine: str = Query("", description="按作者用户名过滤（仅可编辑者生效）"),
    sort: str = Query("latest"),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    user: dict = Depends(get_current_user),
):
    """分页清单，契约 ``{items,total,page,page_size}``。

    默认只回门户可见内容（``live_revision_id<>0`` 且未下架）。草稿必须由**可编辑者**
    显式传 ``status=draft`` 才回 —— 不是靠前端不显示，服务端就一道门。
    ``exclude_category`` 是读侧展示让位（首页右上已有「关于本公司」简介卡），
    只影响这一份清单；编排面与简介卡自身的查询都不传它。
    """
    try:
        return svc.list_articles(category=category, exclude_category=exclude_category,
                                 kind=kind, tag=tag, q=q, status=status,
                                 mine=mine, can_edit=_can_edit(user), sort=sort,
                                 page=page, page_size=page_size)
    except svc.ContentError as e:
        raise _fail(e) from None


@router.get("/articles/{ident}")
def read_article(ident: str, revision: str = Query("live"),
                 release: int = Query(0, ge=0, description="已发布期次号（历史版本阅读）；与 revision=head 互斥"),
                 count_view: int = Query(0, description="1 = 计入阅读数（门户行为）"),
                 user: dict = Depends(get_current_user)):
    """详情。``revision=head`` 是编辑器预览，只有可编辑者拿得到。

    ``release=N`` 读已发布的第 N 期：期次号是业务可读的对外寻址键，revision id 不下发也不接受
    外边传（护栏 §7）。“只保留最新版本”的条目只放开当前一期，旧期次回 404 + 原因（与清单同口径）。

    阅读计数默认**不开**: 它是门户的统计行为。内容管理/审核页每看一次就 +1 会把
    "最多阅读"排成"最近被同事审过的那篇", 污染门户排序。
    """
    try:
        return svc.get_article(slug=ident, can_edit=_can_edit(user), revision=revision,
                               release=int(release or 0), bump_view=bool(count_view))
    except svc.ContentError as e:
        raise _fail(e) from None


@router.get("/articles/{ident}/releases")
def read_releases(ident: str, user: dict = Depends(get_current_user)):
    """已发布期次清单（阅读页历史下拉、编排面版本分区的数据源）。

    读者面与编辑面共用服务层那一份实现，差异只由 ``versioning`` 决定 —— 两处各写一套
    就会出现“清单里没有但直链能读到”这种双口径（已锁进用例）。
    只回期次号/期次名/上线时间，不下发 revision id。
    """
    try:
        return svc.releases_of(_resolve_id(ident, user=user), for_reader=not _can_edit(user))
    except svc.ContentError as e:
        raise _fail(e) from None


@router.get("/articles/{ident}/related")
def read_related(ident: str, limit: int = Query(5, ge=1, le=10),
                 user: dict = Depends(get_current_user)):
    """同栏目推荐（只推在线内容）。首页阅读页用：它面向全员，所以必须按读者口径解析。"""
    article_id = _resolve_id(ident, user=user)
    return {"items": svc.related_articles(article_id, limit=limit)}


@router.get("/articles/{ident}/revisions")
def read_revisions(ident: str, user: dict = Depends(get_current_user)):
    """版本清单（回滚入口）。草稿元信息只对可编辑者开放。"""
    if not _can_edit(user):
        raise HTTPException(status_code=403, detail="版本历史需要编辑权限")
    return {"items": svc.revision_list(_resolve_id(ident))}


@router.get("/articles/{ident}/history")
def read_history(ident: str, user: dict = Depends(get_current_user)):
    """审核与发布流水（谁批的、当时警告了什么）。"""
    if not _can_edit(user):
        raise HTTPException(status_code=403, detail="审核流水需要编辑权限")
    return {"items": svc.review_history(_resolve_id(ident))}


def _resolve_id(ident: str, user: dict | None = None) -> int:
    """slug 或数字 id → 条目 id。

    不传 ``user`` 时保持旧行为（写通道自己会按权限码裁决，解析阶段不判发布状态）。
    传了 ``user`` 且调用者无编辑权（读者面，如相关推荐）则只允许解析**在线**条目：
    否则 ``article:read`` 生效后，旁人可以拿 slug 探测哪些条目存在但尚未发布。
    """
    text = str(ident or "").strip()
    if not text:
        raise HTTPException(status_code=404, detail="内容不存在")
    if text.isdigit():
        return int(text)
    try:
        row = svc.get_article(slug=text, can_edit=(_can_edit(user) if user is not None else True),
                              revision="head" if user is None else "live")
    except svc.ContentError as e:
        raise _fail(e) from None
    return int(row["id"])


# ── 写：条目（article:manage）─────────────────────────────────────────────────

@router.post("/articles")
def create_article(payload: ArticlePayload, request: Request, user: dict = Depends(get_current_user)):
    try:
        article = svc.create_article(payload.model_dump(exclude_none=True), _actor(user))
        _audit(user, request, "content.article.create", int(article["id"]), f"新建草稿 {article['slug']}")
        return article
    except svc.ContentError as e:
        raise _fail(e) from None


@router.put("/articles/{ident}")
def update_article(ident: str, payload: ArticlePayload, request: Request,
                   user: dict = Depends(get_current_user)):
    """写的是 head revision；已发布内容的改动只进草稿，线上继续展示旧版本。"""
    article_id = _resolve_id(ident)
    try:
        article = svc.update_article(article_id, payload.model_dump(exclude_none=True), _actor(user),
                                     expected_version=payload.expected_version)
        _audit(user, request, "content.article.update", article_id, f"编辑 {article['slug']}")
        return article
    except svc.ContentError as e:
        raise _fail(e) from None


@router.delete("/articles/{ident}")
def delete_article(ident: str, request: Request, user: dict = Depends(get_current_user)):
    article_id = _resolve_id(ident)
    try:
        out = svc.delete_article(article_id, _actor(user))
        _audit(user, request, "content.article.delete", article_id, "删除草稿")
        return out
    except svc.ContentError as e:
        raise _fail(e) from None


@router.post("/articles/{ident}/submit")
def submit_article(ident: str, request: Request, user: dict = Depends(get_current_user)):
    """提交审核。硬阻断逐条回原因（422）；软警告随响应返回，由审核人逐条确认。"""
    article_id = _resolve_id(ident)
    try:
        out = svc.submit_review(article_id, _actor(user))
        _audit(user, request, "content.article.submit", article_id, "提交审核")
        return out
    except svc.ContentError as e:
        raise _fail(e) from None


# 批量端点挂在已登记的 pattern 里（`/api/content/articles*` 的 POST = article:manage），
# 所以本批**不新增权限码**，也不动 adh_perm_registry；路径用 `/articles/batch-*` 而不是
# `/articles/batch`，是为了把动作直接写在 URL 上，与发布通道那条分开裁决。

@router.post("/articles/batch-submit")
def batch_submit_articles(payload: BatchSlugs, request: Request,
                          user: dict = Depends(get_current_user)):
    """批量提交审核：逐条跑提交门禁，不合格的逐条回原因（已在审中 / 缺必填正文）。

    每条的软提醒随条目回给前端，审核人仍需逐条确认 —— 批量只批量“提交”，不批量“放行”。
    """
    return _batch_action(request, user, "submit", payload.slugs)


@router.post("/articles/batch-delete")
def batch_delete_articles(payload: BatchSlugs, request: Request,
                          user: dict = Depends(get_current_user)):
    """批量删除：已上线的逐条拒（必须先下架），其余走与单条完全相同的删除路径。

    删除不可逆，所以前端必须走确认框把名单逐条列出来；附件清理没做干净的提醒随条目回。
    """
    return _batch_action(request, user, "delete", payload.slugs)


# ── 写：栏目与标签（article:manage）───────────────────────────────────────────

@router.post("/categories")
def create_category(payload: CategoryPayload, user: dict = Depends(get_current_user)):
    try:
        return svc.create_category(payload.model_dump(exclude_none=True), _actor(user))
    except svc.ContentError as e:
        raise _fail(e) from None


@router.put("/categories/{category_id}")
def update_category(category_id: int, payload: CategoryPayload, user: dict = Depends(get_current_user)):
    try:
        return svc.update_category(category_id, payload.model_dump(exclude_none=True), _actor(user))
    except svc.ContentError as e:
        raise _fail(e) from None


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, user: dict = Depends(get_current_user)):
    try:
        return svc.delete_category(category_id, _actor(user))
    except svc.ContentError as e:
        raise _fail(e) from None


@router.post("/tags")
def create_tag(payload: TagPayload, user: dict = Depends(get_current_user)):
    try:
        return svc.create_tag(payload.model_dump(exclude_none=True), _actor(user))
    except svc.ContentError as e:
        raise _fail(e) from None


@router.put("/tags/{tag_id}")
def update_tag(tag_id: int, payload: TagPayload, user: dict = Depends(get_current_user)):
    try:
        return svc.update_tag(tag_id, payload.model_dump(exclude_none=True), _actor(user))
    except svc.ContentError as e:
        raise _fail(e) from None


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, user: dict = Depends(get_current_user)):
    try:
        return svc.delete_tag(tag_id, _actor(user))
    except svc.ContentError as e:
        raise _fail(e) from None


# ── 附件（kind=file 的载体; 上传/下载/删除）─────────────────────────────────────
#
# 挂载位置是**刻意**的: 全部落在 `/api/content/articles*` 与 `/api/content/reviews*`
# 这两个已登记的 api_pattern 里, 因此本批**不新增权限码**。
#   · 编排侧（上传/清单/删除/下载）挂 articles* → 由 `article:manage` 裁决;
#   · 审核侧只给下载, 挂 reviews* → 由 `article:review` 裁决。
#     为什么单开一条: 只勾「审核内容」的角色拿不到 articles*（见 review_draft 同样的
#     历史缺陷）—— 看不到附件内容的审核是空承诺, 附件形态的稿子就没法审。
#
# 下载一律 `Content-Disposition: attachment`（服务端不接受调用方指定 inline）:
# 用户上传的 HTML/PDF 若以本系统源 inline 渲染就是存储型 XSS, 同源能读到登录态。

def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    """分块读上传体, 超上限立即拒（不把 100MB 整份吸进内存再判）。"""
    limit = svc.MAX_CONTENT_FILE_SIZE
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413,
                                detail=f"附件不能超过 {limit // 1048576}MB，请拆分或压缩后再传")
        chunks.append(chunk)
    return (file.filename or ""), b"".join(chunks)


def _file_meta(article_id: int) -> dict:
    return {"items": svc.list_files(article_id)}


@router.get("/articles/{ident}/files")
def read_article_files(ident: str):
    """条目附件清单（不含 object_key 等内部标识）。"""
    return _file_meta(_resolve_id(ident))


@router.post("/articles/{ident}/files")
def upload_article_file(ident: str, request: Request, file: UploadFile = File(...),
                        user: dict = Depends(get_current_user)):
    """上传附件。条目必须已存在 —— 新建时请先保存草稿再传（附件按条目归属, 没有
    "先传后挂"的暂存区, 免得留下无人认领的文件）。"""
    article_id = _resolve_id(ident)
    filename, data = _read_upload(file)
    try:
        item = svc.upload_file(article_id, filename, data, _actor(user))
        _audit(user, request, "content.file.upload", article_id, f"上传附件 {item.get('filename')}")
        # 附件不进 revision 版本链: 已上线条目的门户下载会立即换成新文件（不走审核）。
        # 这句必须回给前端显示, 不能让作者以为改动还压在草稿里。
        note = ("附件已上传。附件不进版本链：这条若已上线，门户下载会立即指向新文件，"
                "不需要也不会自动触发重新审核 —— 请确认换上的文件本身是可对外发布的版本")
        return {"file": item, "message": note}
    except svc.ContentError as e:
        raise _fail(e) from None


@router.delete("/articles/{ident}/files/{file_id}")
def delete_article_file(ident: str, file_id: int, request: Request,
                        user: dict = Depends(get_current_user)):
    article_id = _resolve_id(ident)
    try:
        out = svc.delete_file(article_id, int(file_id), _actor(user))
        _audit(user, request, "content.file.delete", article_id, f"删除附件 {out.get('filename')}")
        return out
    except svc.ContentError as e:
        raise _fail(e) from None


def _file_target(ident: str, file_id: int, user: dict, *, reviewer: bool = False) -> dict:
    """附件寻址（编排侧与审核侧共用; 权限差异体现在挂载前缀上, 不在这段里）。

    ``allow_draft`` 只给两类人：具备编辑权的人（要下草稿附件才能继续编排）与
    走 ``/reviews*`` 前缀的审核人（看不到稿子的审核是空承诺）。其他人一律按
    “所属内容必须在线”裁决 —— 这里的判定不能省：``article:read`` 的 GET pattern
    写的 ``/api/content/articles*`` 连带盖住了本路径（fnmatch 做不了减法）。
    """
    allow = reviewer or _can_edit(user)
    try:
        return svc.file_for_download(_resolve_id(ident, user=user), int(file_id), allow_draft=allow)
    except svc.ContentError as e:
        raise _fail(e) from None


def _presigned(target: dict) -> dict:
    """对象存储直链（大文件不经服务端回源）。本地回退模式无直链, 显式报错引导代理下载。"""
    storage = get_object_storage()
    if not storage.is_object_storage:
        raise HTTPException(status_code=400,
                            detail="当前附件存储为本地模式，无对象存储直链；请改用 /download 代理下载")
    url = storage.get_presigned_url(target["object_key"], expires=900,
                                    download_filename=target["filename"])
    if not url:
        raise HTTPException(status_code=502, detail="附件直链生成失败，请稍后重试或改用代理下载")
    return {"url": url, "filename": target["filename"], "expires_in": 900}


def _proxy_download(target: dict) -> Response:
    data = get_object_storage().download_bytes(target["object_key"])
    if data is None:
        # 行在、对象没了: 说清是"文件不可用"而不是伪装成 404 附件不存在
        raise HTTPException(status_code=410,
                            detail="附件文件在对象存储里取不到了，请重新上传该文件")
    quoted = quote(target["filename"])
    return Response(
        content=data, media_type=target["mime"] or "application/octet-stream",
        headers={"Content-Disposition":
                 f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"})


@router.get("/articles/{ident}/files/{file_id}/download-url")
def article_file_download_url(ident: str, file_id: int, user: dict = Depends(get_current_user)):
    return _presigned(_file_target(ident, file_id, user))


@router.get("/articles/{ident}/files/{file_id}/download")
def article_file_download(ident: str, file_id: int, user: dict = Depends(get_current_user)):
    return _proxy_download(_file_target(ident, file_id, user))


@router.get("/reviews/{ident}/files/{file_id}/download-url")
def review_file_download_url(ident: str, file_id: int, user: dict = Depends(get_current_user)):
    return _presigned(_file_target(ident, file_id, user, reviewer=True))


@router.get("/reviews/{ident}/files/{file_id}/download")
def review_file_download(ident: str, file_id: int, user: dict = Depends(get_current_user)):
    return _proxy_download(_file_target(ident, file_id, user, reviewer=True))


# ── 审核（article:review，独立前缀）───────────────────────────────────────────

@router.get("/reviews/pending")
def reviews_pending(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50)):
    """审核工作台队列：待审 + 已通过待发布 + 到期复核，三类都要有人处理。"""
    return svc.pending_reviews(page=page, page_size=page_size)


@router.get("/reviews/{ident}/draft")
def reviews_draft(ident: str):
    """审核人读草稿正文（head revision）+ 当时门禁结果。

    为什么不复用 ``GET /api/content/articles/{id}?revision=head``：那条落在
    ``article:manage`` 的 api_pattern 集里，只勾「审核内容」的角色拿不到正文——
    看不到稿子的审核是空承诺。挂 ``/reviews*`` 前缀让 ``article:review`` 自己就能裁决读稿。
    """
    try:
        return svc.review_draft(_resolve_id(ident))
    except svc.ContentError as e:
        raise _fail(e) from None


@router.post("/reviews/{ident}/approve")
def reviews_approve(ident: str, payload: ReviewAction, request: Request,
                    user: dict = Depends(get_current_user)):
    article_id = _resolve_id(ident)
    try:
        out = svc.approve_review(article_id, _actor(user), comment=payload.comment or "",
                                 acknowledged=payload.acknowledged)
        _audit(user, request, "content.article.approve", article_id, "审核通过")
        return out
    except svc.ContentError as e:
        raise _fail(e) from None


@router.post("/reviews/{ident}/reject")
def reviews_reject(ident: str, payload: ReviewAction, request: Request,
                   user: dict = Depends(get_current_user)):
    article_id = _resolve_id(ident)
    try:
        out = svc.reject_review(article_id, _actor(user), comment=payload.comment or "")
        _audit(user, request, "content.article.reject", article_id, payload.comment or "")
        return out
    except svc.ContentError as e:
        raise _fail(e) from None


# ── 发布与下架（article:publish，独立前缀）───────────────────────────────────

@router.post("/publish/{ident}")
def publish_or_archive(ident: str, payload: PublishAction, request: Request,
                       user: dict = Depends(get_current_user)):
    """发布 = ``live := head``（并开一期）；下架 = ``live := 0``；重新上架 = ``live := 下架时那一版``；
    切换展示版本（回滚）= ``live := 第 N 期``。

    ``waive_review=True`` 是分级审核里那条显式例外（跳过审核直发），必须带原因并入审计；
    它不是绕过权限——能进到这里说明中间件已按 ``article:publish`` 放行。

    四个动作同一个前缀同一个权限码（都是“改门户可见性”），不在 handler 里另造第二套判断；
    取何动作由状态机自己裁决（非法转换一律 ContentError 带可读原因）。
    """
    article_id = _resolve_id(ident)
    action = str(payload.action or "").strip().lower()
    try:
        if action == "publish":
            out = svc.publish_article(article_id, _actor(user), expected_version=payload.expected_version,
                                      waive_review=bool(payload.waive_review), reason=payload.reason or "",
                                      release_label=payload.release_label or "")
            _audit(user, request, "content.article.publish", article_id,
                   (("免审直发: " + (payload.reason or "")) if payload.waive_review else "发布上线")
                   + (f"（第 {out.get('release')} 期〈{out.get('release_label')}〉"
                      if out.get("release") else ""))
            return out
        if action == "archive":
            out = svc.archive_article(article_id, _actor(user), expected_version=payload.expected_version,
                                      reason=payload.reason or "")
            _audit(user, request, "content.article.archive", article_id, payload.reason or "下架")
            return out
        if action == "republish":
            out = svc.republish_article(article_id, _actor(user), expected_version=payload.expected_version,
                                        reason=payload.reason or "")
            _audit(user, request, "content.article.republish", article_id, payload.reason or "重新上架")
            return out
        if action == "rollback":
            out = svc.rollback_to_release(article_id, _actor(user), int(payload.release or 0),
                                          expected_version=payload.expected_version,
                                          reason=payload.reason or "")
            _audit(user, request, "content.article.rollback", article_id,
                   (payload.reason or "切换展示版本") + f"（第 {out.get('release')} 期）")
            return out
        raise HTTPException(status_code=400,
                            detail="操作取值只能是 publish / archive / republish / rollback")
    except svc.ContentError as e:
        raise _fail(e) from None


@router.post("/publish-batch")
def publish_batch(payload: BatchPublishAction, request: Request,
                  user: dict = Depends(get_current_user)):
    """批量发布 / 下架 / 重新上架：逐条走与单条同一个状态机，不合格逐条回原因。

    与单条同一个权限码、同一个前缀（``/api/content/publish*`` = ``article:publish``），
    不新增登记行；发布类三个动作由 ``action`` 分开，前端按行上同一判据分组勾选。

    **不接受 rollback**：切换展示版本必须逐条指名期次（见 :data:`_BATCH_KINDS` 上方注释）。

    ``reason`` 对整批共用（写进每条的审核流水）：下架/上架原因是编排面的说明性字段，
    不填不阻断，与单条弹框同口径。发布不接受 reason（非免审直发时服务层本就不用它）。
    """
    action = str(payload.action or "").strip().lower()
    if action not in ("publish", "archive", "republish"):
        raise HTTPException(status_code=400,
                            detail="批量动作只能是 publish / archive / republish"
                                   "（切换展示版本需要逐条指定期次，不开放批量）")
    return _batch_action(request, user, action, payload.slugs, payload.reason or "")


# ── 报告中心提升通道（仍由 article:manage 裁决，不新增权限码）─────────────────
#
# 为什么开独立前缀而不是挂在 articles* 下面一个子路径：提升的入参是**报表**标识（不属于
# 内容域的任何一张表），把它扮成“某条目的子资源”会让审计与 pattern 都看不出这是跨域动作。
# 单独前缀同样落在 article:manage 已登记的 api_pattern 里（content_release_migration.sql
# 只做 CONCAT 追加），所以“能写内容草稿的人才能提交”这一条裁决点没变。
# 报表侧的“是不是本人的报告”由服务层校（身份只取服务端 JWT），这里不另造判断。

@router.get("/promotions/match")
def promote_match(report_id: int = Query(0, ge=0), user: dict = Depends(get_current_user)):
    """提交前的归集预判：会追加到哪一篇的第几期之后，还是会新建。

    归集口径（``report_access.series_key``）是服务端唯一一份，前端不自己猜：
    “提交后才发现历史版本被拆成两条内容”必须先在弹框里看得见。
    """
    try:
        return promote_svc.preview(report_id, user)
    except svc.ContentError as e:
        raise _fail(e) from None


@router.post("/promotions")
def promote_from_report(payload: PromotePayload, request: Request,
                        user: dict = Depends(get_current_user)):
    """把一份报告提交到内容管理审核（同归集键追加新版本，否则新建草稿）。

    只做“定位/新建条目 + 写 head revision + 按需提交审核”三件事：门禁、审核、发布
    全部复用内容中心那套状态机，提升通道够不着发布，也够不着免审直发。
    正文含内部标识在**写入之前**就拒（不先落库再等提交才发现）；门禁没过时草稿确实已存，
    服务层会把“已存草稿、未进审核”连同逐条原因一起说出来。
    """
    try:
        out = promote_svc.promote(payload.model_dump(exclude_none=True), user)
    except svc.ContentError as e:
        raise _fail(e) from None
    # 审计拿真实条目 id（服务端自己解析，不从响应体取内部标识）；没 slug 时退 0 不能把
    # 已成功的提升报成 404。
    aid = _resolve_id(str(out.get("slug") or "")) if out.get("slug") else 0
    _audit(user, request, "content.article.promote", aid,
           f"{'新建' if out.get('created') else '追加'}内容：{out.get('title')}")
    return out
