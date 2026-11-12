"""报告中心 → 内容中心提升通道（把分析产物提交到内容管理审核）。

需求（用户 2026-11-12）：好报告不该只留在个人报告中心，要能一键提交给内容团队走
「创作 → 审核 → 发布」对外发布；同一个主题按月发布新版本时，门户只展示最新一期，
读者进去能翻历史期次。

五条口径，都是做错就会出双真值源/越权的那类，写在这里而不是靠调用方自觉：

1. **提升 = 复制正文成条目的一个新版本，不搬报表快照、不依赖报表存活**。
   报表正文存对象存储且有 ``security_context`` 快照（授权收紧即不可读），门户内容不能建在
   这块地基上：否则读者点开一篇已发布内容，会因为原作者的数据权限变化而读不到。
   落地形态就是 ``markdown`` / ``html`` 站内正文（``kind='promoted'`` 因此退役，见
   ``content_service.KIND_PROMOTED``）。
2. **归集键唯一 = ``report_access.series_key``**。同一份报告的后续版本（定时任务每轮执行、
   「用最新数据重新生成」的产物）自然落回**同一条内容条目**的新一期，不积成 N 篇同名内容；
   键存在 ``adh_content_articles.source_ref``（内部标识，不进响应体），``source_type='report'``
   只作为该行的归属标记。**每次提升都新建条目**的写法被否：那会把"同主题多版本"变成
   "一堆互相不知情的条目"，历史期次只能靠人肉归并。
3. **不新建第二套审核回路**。提升只做三件事：定位/新建条目、写 head revision、按需
   ``submit_review``。门禁、软提醒、审核、发布全部复用内容中心那套状态机 —— 提升通道
   够不着发布（``article:publish`` 是独立权限码），也够不着免审直发。
4. **权限裁决点唯一**。端点挂在 ``/api/content/promotions*``，由 ``article:manage`` 的
   ``api_pattern`` 门控（``docker/mysql/content_release_migration.sql`` 只做 CONCAT 追加）：
   能写内容草稿的人才能把报告提成草稿，不给"报告属主"另开一条写内容域的旁路。
   报表侧只多做一件本域该做的事：**属主校验**（``report_access._owner_matches``，
   身份只取服务端 JWT）。
5. **失败即暴露**。正文读不到、快照失效、报表不属于本人、正文含内部标识、条目正在审核中、
   提交门禁硬阻断 —— 一律带原因抛出（``ContentError`` 映射 HTTP 状态），不做"先存成草稿
   再说"的静默半成功；半成功必须显式说出来（草稿已落但没进审核，回执里分开两段讲清）。

内部标识（报表 id / 条目 id / revision id / object_key）不进响应体（护栏 §7），
对外一律 slug 与业务名。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from backend.common.db import execute_query
from backend.modules.platform.services import content_service as cs

logger = logging.getLogger(__name__)

#: 提升来源在条目行上的归属标记（与 content_service.create_article(source_type=...) 同一取值）
SOURCE_TYPE_REPORT = "report"

_H1_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$", re.M)
_MD_NOISE_RE = re.compile(r"[|`*_>#\-]{2,}")


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 报表侧：读取与归属校验 ────────────────────────────────────────────────────

def _load_report(report_id: int, user: dict) -> dict:
    """取报表原始行并校归属：**只允许提升本人的报告**（身份只信服务端 JWT，护栏 §2）。

    内容型报表（会话保存/手写）与数据派生报表（定时任务/分析生成）都走同一判据：
    后者还要求当时的授权快照仍然成立 —— 数据权限被收紧后连属主自己都读不到正文，
    这时提升必然拿不到内容，如实拒掉比"提上去但打不开"诚实。
    """
    from backend.modules.viz.services import report_access, report_service

    try:
        rid = int(report_id or 0)
    except (TypeError, ValueError):
        raise cs.ContentError("报表标识不对，无法提交", 400) from None
    if not rid:
        raise cs.ContentError("缺少报表标识（请从报告中心的某一份报告里提交）", 400)
    row = report_service.load_report(rid)
    if not row:
        raise cs.ContentError("这份报告不存在或已被删除，请刷新报告中心后重试", 404)
    if not report_access._owner_matches(row, user):
        raise cs.ContentError("只能提交本人的报告：这份报告不属于当前账号", 403)
    if not report_access.has_body(row):
        raise cs.ContentError("这份报告还没有正文（生成中/失败/超时），请先生成可用版本再提交", 409)
    if not report_access.can_access_report(row, user):
        raise cs.ContentError("这份报告的取数授权快照已失效（数据权限收紧或执行记录变更），"
                              "现在连属主本人也读不到正文，不能作为内容发布了。"
                              "请用「用最新数据重新生成」出一份新版本后再提交。", 403)
    return row


def _report_body(row: dict) -> tuple[str, str]:
    """读正文文本 + 落地形态。读不到一律抛可读原因，不回退成"提一个空正文的条目"。"""
    from backend.modules.viz.services import report_service

    try:
        raw = report_service.read_report_content_bytes(row)
    except (LookupError, ValueError) as exc:
        raise cs.ContentError(str(exc) or "报告正文不可用，请重新生成后再提交", 409) from exc
    body = (raw or b"").decode("utf-8", "ignore").strip()
    if not body:
        raise cs.ContentError("这份报告的正文是空的，不能提交成内容", 409)
    fmt = str(row.get("format") or "markdown").strip().lower()
    kind = cs.KIND_HTML if fmt == "html" else cs.KIND_MARKDOWN
    return body, kind


def _summary_hint(body: str, kind: str) -> str:
    """摘要建议：取正文里第一段像正文的文字（去掉标题行与表格分隔线）。

    这是**建议值**，弹框里必须能看到并改；提交门禁对"摘要过长/为空"本来就只出软提醒，
    所以留建议值不会把流程卡住。

    HTML 正文要**先剥标签再分行**：不剥就会把 `<style>`/`<td>` 这类标签碎片当成正文
    摘出来（实机报过：建议摘要长这样“， ，”），那比留空更坑 —— 它会直接上卡片。
    """
    text = body or ""
    if kind == cs.KIND_HTML:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text).strip()
    chunks: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _H1_RE.match(line) or line.startswith(("|", "---", "===")):
            continue
        cleaned = _MD_NOISE_RE.sub(" ", line).strip()
        if cleaned:
            chunks.append(cleaned)
        if len("".join(chunks)) >= 100:
            break
    return "，".join(chunks[:3])[:120]


# ── 内容侧：按归集键定位条目 ──────────────────────────────────────────────────

def _article_by_series(series_key: str) -> dict:
    """同一份报告已提升过的那条内容（``source_ref`` 命中即归集到它）。

    只取定位与提示需要的列：状态/指针的裁决留给内容中心那套状态机，不在这里判权限、
    也不在这里判能不能改（``update_article`` 自己会拒审核中的稿）。
    """
    key = str(series_key or "").strip()
    if not key:
        return {}
    row = execute_query(
        "SELECT id, slug, title, status, version, versioning, "
        "       live_revision_id, head_revision_id "
        "FROM adh_content_articles WHERE source_type=%s AND source_ref=%s ORDER BY id LIMIT 1",
        (SOURCE_TYPE_REPORT, key), fetchone=True)
    return dict(row) if row else {}


def _series_key(row: dict) -> str:
    from backend.modules.viz.services import report_access
    return str(report_access.series_key(row) or "").strip()


# ── 对外：归集预判 + 提升 ─────────────────────────────────────────────────────

def preview(report_id: int, user: dict) -> dict:
    """提交前的归集预判：会追加到哪一篇的第几期，还是会新建。

    这件事必须在点下去**之前**演出来：用户实拍过"提交后才发现历史版本被拆成两条内容"，
    归集口径（series_key）是服务端唯一一份，前端不自己猜。
    """
    row = _load_report(report_id, user)
    body, kind = _report_body(row)
    series = _series_key(row)
    article = _article_by_series(series)
    title = str(row.get("title") or "").strip() or "未命名报告"
    return {
        "title": title,
        "summary": _summary_hint(body, kind),
        "kind": kind,
        "kind_label": cs.KIND_LABELS[kind],
        "body_chars": len(body),
        "matched": ({"slug": article["slug"], "title": article["title"],
                     "status": article["status"],
                     "release_count": cs._release_count(int(article["id"]))}
                    if article else None),
        # 期次名由**发布人**在发布时给（内容中心那一步才产生期次），这里只报"将追加到第几期之后"
        "next_release_after": (cs._release_count(int(article["id"])) if article else 0),
        # 文案是直接渲染给人看的（弹框里那段不是 Markdown），所以不夹 ** 标记
        "reason": ("这份报告的历次版本已提升过，本次会作为新一期追加到同一篇内容里"
                   if article else "这份报告还没提升过，本次会新建一篇内容草稿"),
        "versioning_options": [{"value": v, "label": l} for v, l in cs.VERSIONING_LABELS.items()],
        "categories": [{"key": c["key"], "label": c["label"]}
                       for c in cs.list_categories(with_counts=False)],
    }


def promote(payload: dict, user: dict) -> dict:
    """把一份报告提交到内容管理审核（同归集键 → 追加新版本；否则新建条目）。

    ``payload``：``report_id``（必填）、``category``（栏目 key，必填）、``title`` / ``summary``
    （建议值可改）、``versioning``（读者可见性策略）、``submit``（默认 True，提交审核）。
    **不接受**任何身份字段与内部 id：作者是当前登录用户，由服务端注入。
    """
    from backend.modules.viz.services import report_service

    actor = str(user.get("username") or user.get("user_id") or "")
    row = _load_report(int(payload.get("report_id") or 0), user)
    body, kind = _report_body(row)
    series = _series_key(row)

    title = str(payload.get("title") or "").strip() or str(row.get("title") or "").strip()
    if not title:
        raise cs.ContentError("标题不能为空")
    summary = str(payload.get("summary") or "").strip() or _summary_hint(body, kind)
    versioning = cs._validate_versioning(payload.get("versioning"))
    submit = payload.get("submit")
    submit = True if submit is None else bool(submit)

    # 写前就拦内部标识：正文里带连接串/生成 SQL/报错栈属安全边界，不能等提交审核才发现
    # （与 AI 草稿通道同一纪律，护栏 §7）。命中即拒，不裁一半内容塞进去。
    leaks = cs.body_leak_labels(body)
    if leaks:
        raise cs.ContentError("报告正文含内部标识：" + "；".join(leaks)
                              + "。对外内容不得出现数据源/主机/连接串/生成 SQL/报错栈，"
                                "请回到报告里改成业务名称后重新生成，再提交内容审核", 422)

    article = _article_by_series(series)
    head_before = int(article.get("head_revision_id") or 0) if article else 0
    if article:
        aid = int(article["id"])
        # 归集命中：正文写成这条目的新版本（改动只进 head，线上仍展示当前这一期）。
        # 条目正在审核中时 update_article 会拒，那句原因原样端给用户 —— 不去猜"要不要先撤回"。
        saved = cs.update_article(aid, {"title": title, "summary": summary, "kind": kind,
                                        "body": body, "versioning": versioning,
                                        "change_note": f"来自报告中心：{title}（{_now_text()[:10]}）"},
                                 actor, expected_version=int(article["version"]))
        created = False
    else:
        saved = cs.create_article(
            {"title": title, "summary": summary, "kind": kind, "body": body,
             "category": payload.get("category"), "versioning": versioning},
            actor, source_type=SOURCE_TYPE_REPORT, source_ref=series)
        aid = int(saved["id"])
        created = True

    out = {"slug": saved["slug"], "title": saved["title"], "status": saved["status"],
           "created": created, "versioning": versioning,
           "versioning_label": cs.VERSIONING_LABELS[versioning],
           "release": int(saved.get("release") or 0),
           "release_label": str(saved.get("release_label") or ""),
           "release_count": int(saved.get("release_count") or 0),
           "is_live": bool(saved.get("is_live"))}
    # 正文与已有那一版一字不差时，update_article 不会开新 revision（这是内容中口的既有口径）。
    # 不能反过来在提升这里谎称“已追加为新版本”：用户会去版本清单里找那一条不存的新版本。
    head_after = int(_article_by_series(series).get("head_revision_id") or 0) if article else 0
    out["new_version"] = bool(created or (article and head_after and head_after != head_before))
    if created:
        out["message"] = f"已新建内容草稿《{saved['title']}》并写入正文。"
    elif out["new_version"]:
        current = (f"当前线上是第 {out['release']} 期〈{out['release_label']}〉"
                   if out["release"] else "这篇还没有已发布的期次（线上还看不到它）")
        out["message"] = (f"已把这份报告的正文追加为《{saved['title']}》的新版本"
                          f"（{current}，新内容尚未生效）。")
    else:
        out["message"] = (f"这份报告的正文与《{saved['title']}》当前版本一字不差，"
                          "没有产生新版本（内容没变就不多开一条假历史）。")
    out["gate_blocked"] = []
    if not submit:
        out["submitted"] = False
        out["message"] += " 未提交审核：需要时到「内容中心 → 我的草稿」提交。"
        logger.info("[content-promote] 存草稿 report=%s series=%s article=%s created=%s by=%s",
                    int(row.get("id") or 0), series, aid, created, actor)
        return out
    try:
        review = cs.submit_review(aid, actor, channel="human")
    except cs.ContentError as exc:
        # 门禁硬阻断（422）：草稿确实已经存下，这件事不能说漏；直接报错会让人以为"什么都没发生"，
        # 回滚草稿又等于替提交人做了删除决定。所以**半成功显式说出来**：HTTP 仍成功返回，回执里
        # 用 gate_blocked 逐条给出原因 + submitted=False（口径 5）。其余失败（乐观锁 409、
        # 条目正在审核中、无待发布改动）不是门禁问题，原样抛出让用户重试。
        if int(getattr(exc, "status", 0) or 0) != 422:
            raise
        out["submitted"] = False
        # submit_review 把逐条硬阻断拼成"不能提交审核：A；B"，这里拆回**条目级**原因：
        # 弹框要能一条条列出来（与内容中心提交页同一形状），所以只剥那句固定前缀。
        reasons = [t.strip() for t in re.sub(r"^不能提交审核：", "", str(exc.detail or "")).split("；") if t.strip()]
        out["gate_blocked"] = reasons
        out["message"] += "草稿已保存到内容中心，但未能提交审核：" + "；".join(reasons or [str(exc.detail)])
        logger.info("[content-promote] 门禁拒 report=%s article=%s reason=%s",
                    int(row.get("id") or 0), aid, exc.detail)
        return out
    out["submitted"] = True
    out["status"] = review["status"]
    out["warnings"] = review.get("warnings") or []
    out["message"] += review.get("message") or ""
    logger.info("[content-promote] report=%s series=%s article=%s created=%s by=%s",
                int(row.get("id") or 0), series, aid, created, actor)
    return out
