"""内容中心 → 知识库语料同步 — 把已发布的门户内容推成 AS-BOT 可检索的文档。

需求（用户 2026-11-05）：内容管理要能同步到知识库，并且**可以选择目标知识库**。

四条口径，都是"做错就会污染 AI 答案"的那类，必须写在这里而不是靠调用方自觉：

1. **只同步已发布内容**。草稿/已下架不进语料 —— 语料是 AI 的知识来源，把没定稿的
   口径推上去，Chat 就会拿未审内容回答用户。未发布直接拒，不是"同步了但没生效"。
2. **一篇只对一个库**（表上 `UNIQUE(article_id)`）。换目标库时旧库里那篇必须删掉，
   删不掉要在响应里说出来 —— 两处都有同名语料，检索会给出重复且可能不一致的答案。
3. **不许覆盖人工文档**。云端同名文档如果不带本通道的版本戳，说明是人在「知识库文档」
   里写的：直接覆盖等于毁掉别人的内容。一律拒，并提示改标题或换目录。
   （与 ontology-modeling §1「严禁独立编辑派生物」是同一条纪律的反向：派生物不许人工改，
     人工文档也不许被自动通道顶掉。）
4. **权限仍由唯一裁决点把关**。本服务不判权限：写语料走 ``kbdoc:write``
   （中间件按 ``/api/content/knowledge-bases*`` + POST 裁决）。发布与同步**是两个动作**
   （发布=article:publish，同步=kbdoc:write），所以发布端点里不夹带同步，
   前端在发布成功后另发一次同步请求 —— 合并成一个端点会让"能发布"的人自动获得写语料权。

正文形态：语料是 Markdown 文本。``markdown`` 推正文；``link`` 推"外链条目"（摘要 + 链接，
如实说明原文不在库里）；``file`` **拒**（附件是二进制，本批不接非 markdown 上云，
静默跳过会让人以为已经进了检索）。
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime

from backend.common.db import execute_query, execute_write
from backend.modules.platform.services import content_service as cs
from backend.modules.platform.services import kb_docs_service as kb

logger = logging.getLogger(__name__)

# 本通道生成的语料都带这一行，用来区分"我写的"与"人工写的"（不许覆盖后者）
_STAMP_RE = re.compile(r"content-version-stamp\s+article=(\S+)\s+revision=(\d+)")


class SyncError(Exception):
    """对用户可读的业务失败；``status`` 映射 HTTP 状态码。"""

    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "")


# ── 渲染：门户正文 → 语料文档 ─────────────────────────────────────────────────

def doc_title_for(slug: str) -> str:
    """云端文档标题按 slug 生成（稳定、可反查、不受改标题影响）。"""
    name = str(slug or "").strip().replace("/", "-").replace("\\", "-")
    if not name:
        raise SyncError("条目没有 slug，无法生成语料标题；请先保存草稿", 400)
    return name if name.lower().endswith(".md") else f"{name}.md"


def render_corpus(art: dict, *, revision_id: int, version: int) -> str:
    """把已发布条目渲染成语料 Markdown。

    头部版本戳必须在前 600 字符内（与本体 model-version-stamp 的读法同口径），
    否则冲突闸门看不到它 —— 就会把本通道的文档当人工文档拒掉。
    版本戳的**形状不变**（只往人读的那行元信息里加期次）：闸门靠正则认归属，改形状
    等于让存量语料全部“看起来像人工文档”。
    """
    slug = str(art.get("slug") or "")
    title = str(art.get("title") or slug)
    published = _iso(art.get("published_at"))[:10] or _iso(art.get("updated_at"))[:10]
    tags = "、".join([t for t in (art.get("tags") or []) if t])
    kind = str(art.get("kind") or cs.KIND_MARKDOWN)

    head = (f"<!-- content-version-stamp article={slug} revision={int(revision_id or 0)} "
            f"version={int(version or 0)} published_at={published} -->\n\n")
    meta = [f"# {title}"]
    facts = []
    if art.get("category_label"):
        facts.append(f"栏目：{art['category_label']}")
    if tags:
        facts.append(f"标签：{tags}")
    if published:
        facts.append(f"发布：{published}")
    # 期次写给检索与回答用：同一主题按月发布时，AS-BOT 要能说清引的是哪一期
    # （内容只保留最新版时这里仍是当前那一期的号：语料侧只有 live 那一版会进来）
    release = int(art.get("release") or 0)
    if release:
        label = str(art.get("release_label") or "").strip()
        facts.append(f"期次：第 {release} 期" + (f"（{label}）" if label else ""))
    if facts:
        meta.append("｜".join(facts))
        meta.append("")

    body = str(art.get("body") or "").strip()
    if kind == cs.KIND_LINK:
        url = str(art.get("link_url") or "").strip()
        label = str(art.get("link_label") or "").strip() or url
        meta.append(f"> 这是一条**外链内容**：原文不在本知识库内，下面是门户登记的摘要。")
        meta.append("")
        body = (f"{str(art.get('summary') or '').strip()}\n\n"
                f"原文链接：[{label}]({url})" if url else
                f"{str(art.get('summary') or '').strip()}\n\n（本条目未配置链接地址）")

    parts = [head, "\n".join(meta)]
    if str(art.get("summary") or "").strip() and kind != cs.KIND_LINK:
        parts.append(f"> {str(art['summary']).strip()}")
    if body:
        parts.append(body)
    parts.append("---\n来源：内容中心（资讯栏目）正式发布内容，由平台同步进本知识库；"
                 "如需修改请回到「内容中心 → 内容管理」，在语料侧的改动会在下次同步被覆盖。")
    return "\n\n".join(p for p in parts if p) + "\n"


# ── 前置校验 ──────────────────────────────────────────────────────────────────

def _live_article(ident: str) -> tuple[dict, int, int]:
    """取"可同步的那一版"：必须已发布。返回 (详情, article_id, live_revision_id)。"""
    row = execute_query(
        "SELECT id, slug, status, kind, live_revision_id, version "
        "FROM adh_content_articles WHERE slug=%s OR id=%s",
        (str(ident or "").strip(), int(ident) if str(ident or "").isdigit() else 0),
        fetchone=True)
    if not row:
        raise SyncError("内容不存在（可能已被删除），请刷新列表后重试", 404)
    kind = str(row.get("kind") or "")
    if kind == cs.KIND_FILE:
        raise SyncError(
            "附件形态的条目不能同步：语料是 Markdown 文本，而这篇的正文是 Word/PPT/Excel 等"
            "二进制附件，云端这条通道本批不接收非文本文件。需要进语料请另建一篇站内正文，"
            "把要点写成文字。", 400)
    live = int(row.get("live_revision_id") or 0)
    if not live or str(row.get("status")) == cs.STATUS_ARCHIVED:
        raise SyncError("只有「已发布」的内容能进知识库：草稿与已下架的稿子还没定稿，"
                        "推成语料会让 AS-BOT 拿未审内容回答用户。请先发布（或让审核人发布）再同步。",
                        409)
    art = cs.get_article(article_id=int(row["id"]), can_edit=True, revision="live")
    return art, int(row["id"]), live


def _walk_docs(nodes):
    """树里把文档节点扫出来（tree() 返回的是嵌套结构，flat 只是“是否扁平”的布尔）。"""
    for n in nodes or []:
        if not n.get("is_dir"):
            yield n
        yield from _walk_docs(n.get("children") or [])


def _find_title_conflict(kb_name: str, title: str, my_slug: str) -> str:
    """云端同名文档冲突判定：不是本通道生成的（或归属别的条目）就拒。"""
    tree = kb.tree(kb_name)
    hit = next((n for n in _walk_docs(tree.get("nodes") or [])
                if str(n.get("title") or "") == title), None)
    if not hit:
        return ""
    try:
        remote = kb.read_doc(kb_name, str(hit.get("source_id") or ""))
    except kb.KbDocError as e:
        # 读不到就没法判定归属：宁可拒绝写入，也不覆盖一篇来路不明的文档
        return f"云端已有同名文档「{title}」，但读取它失败（{e.detail}）。为避免覆盖掉别人的内容，本次同步已中止。"
    m = _STAMP_RE.search(str(remote.get("content") or "")[:600])
    if not m:
        return (f"云端已有同名文档「{title}」，而且它不是内容中心同步生成的（没有版本戳）。"
                "覆盖会毁掉人工维护的语料 —— 请换一个文档名，或先在「知识库文档」里确认那篇的去向。")
    if m.group(1) != my_slug:
        return (f"云端同名文档「{title}」属于另一篇内容（{m.group(1)}）。"
                "一篇语料只能对应一篇内容 —— 请给本篇换标题或换目录。")
    return ""


# ── 同步 ──────────────────────────────────────────────────────────────────────

def sync_to_kb(article_ident: str, kb_name: str, *, parent_id: str = "", actor: str = "") -> dict:
    """把一篇已发布内容同步进指定知识库（更新同一篇，不新建副本）。"""
    art, article_id, live_rev = _live_article(article_ident)
    kb_name = str(kb_name or "").strip()
    if not kb_name:
        raise SyncError("请选择目标知识库", 400)

    # 库可用性只问一个口径：kb_docs_service 的清单（类型/绑定/启用状态都在这儿判）
    bases = {b["name"]: b for b in kb.list_bases()}
    if kb_name not in bases:
        known = " / ".join(sorted(bases)) or "（还没登记）"
        raise SyncError(f"知识库「{kb_name}」不存在。现有知识库：{known}", 404)
    target = bases[kb_name]
    if not target.get("supports_docs") or not target.get("can_edit"):
        raise SyncError(f"不能同步到「{kb_name}」：{target.get('reason') or '该库当前不可写'}", 409)

    title = doc_title_for(str(art.get("slug") or ""))
    text = render_corpus(art, revision_id=live_rev, version=int(art.get("version") or 1))
    if not text.strip():
        raise SyncError("渲染出的语料是空的：正文与摘要都没有内容，这篇不进语料", 400)

    mapping = _latest_synced(article_id)
    # 同库同标题才是"更新那一篇"；换库或换标题都要走新建通道（旧库那篇随后撤掉）
    same_target = bool(mapping) and str(mapping.get("kb_name") or "") == kb_name \
        and str(mapping.get("doc_title") or "") == title

    # 冲突闸门：只有"本通道已登记的那一篇"可以免检（它就是我们要更新的对象）
    if not same_target:
        reason = _find_title_conflict(kb_name, title, str(art.get("slug") or ""))
        if reason:
            raise SyncError(reason, 409)

    warnings: list[str] = []
    if same_target and str(mapping.get("revision_id") or "") == str(live_rev) \
            and str(mapping.get("content_hash") or "") == _sha(text):
        # 版本与正文都没变：不发请求。空同步会白换一次云端文档 id 并让云端重跑解析
        return {"kb_name": kb_name, "doc_title": title, "source_id": str(mapping.get("source_id") or ""),
                "unchanged": True, "synced_at": _iso(mapping.get("synced_at")),
                "message": "这篇内容与已同步的语料一致，无需重新提交（空同步会白换一次云端文档标识并重跑解析）。"}

    if same_target:
        # 已登记过 → 走"更新"通道（传新件 + 删旧件），并带上本通道自己的版本戳校验
        try:
            out = kb.save_doc_for_sync(kb_name, str(mapping.get("source_id") or ""), text, actor=actor)
        except kb.KbDocError as e:
            if e.status != 404:
                _record_failure(article_id, kb_name, title, live_rev, text, actor, e.detail)
                raise SyncError(e.detail, e.status) from e
            # 映射还在、云端文档已不在（有人在 Qoder 端删了）：按新建重落一次，
            # 并把这件事说出来 —— 静默"当没事发生"会让人以为还在同一篇上迭代。
            recovered = f"云端已找不到上次同步的那篇（可能已在 Qoder 端删除），本次按新建重新落库：{e.detail}"
            try:
                out = kb.create_doc(kb_name, title, text, parent_id, actor, for_sync=True)
            except kb.KbDocError as e2:
                _record_failure(article_id, kb_name, title, live_rev, text, actor, e2.detail)
                raise SyncError(e2.detail, e2.status) from e2
            out.setdefault("warnings", []).append(recovered)
            out["recovered_from_missing"] = True
        new_id = out["source_id"]
        warnings += [w for w in (out.get("warnings") or []) if w]
        if out.get("warning"):
            warnings.append(str(out["warning"]))
    else:
        try:
            out = kb.create_doc(kb_name, title, text, parent_id, actor, for_sync=True)
        except kb.KbDocError as e:
            _record_failure(article_id, kb_name, title, live_rev, text, actor, e.detail)
            raise SyncError(e.detail, e.status) from e
        new_id = out["source_id"]
        warnings += [w for w in (out.get("warnings") or []) if w]

    # 换目标库（或改了 slug 使文档名变了）：新库里已落定，旧的那篇必须撤掉 —— 两处同名
    # 语料会被同时检索命中，撤不掉不阻断（已经成功了），但必须作为 warning 说出来。
    # same_target 时不重复撤：更新通道本身就是"传新件 + 删旧件"，再删一次只会撞到已删除对象。
    if mapping and not same_target:
        warnings += _drop_old_copy(str(mapping.get("kb_name") or ""),
                                   str(mapping.get("source_id") or ""), actor)

    now = _record_success(article_id, kb_name, parent_id, new_id, title, live_rev, text, actor)
    logger.info("[content-kb-sync] article=%s → kb=%s doc=%s source=%s by=%s",
                article_id, kb_name, title, new_id, actor)
    msg = (f"已同步到知识库「{kb_name}」（{title}）。云端要重新解析一遍，"
           "AS-BOT 检索稍后才用得到这一版。")
    return {"kb_name": kb_name, "doc_title": title, "source_id": new_id,
            "unchanged": False, "synced_at": now, "warnings": warnings,
            "message": msg + (f"；有 {len(warnings)} 条需要处理，见下" if warnings else "")}


def _drop_old_copy(old_kb: str, old_source: str, actor: str) -> list[str]:
    """换目标库后撤掉旧库那篇；撤不掉必须说出来（两处同名语料会被同时检索命中）。"""
    try:
        kb.delete_source_for_sync(old_kb, old_source, actor=actor)
        return []
    except kb.KbDocError as e:
        logger.warning("[content-kb-sync] 旧库文档撤销失败 kb=%s source=%s: %s", old_kb, old_source, e)
        return [f"新库已同步，但旧库「{old_kb}」里的那篇没能撤掉（{e.detail}）。"
                "现在两个库都有这篇语料，检索会重复命中 —— 请到「知识库文档」里处理旧库那篇。"]


def _kb_id(kb_name: str) -> int:
    """水位线表沿用了 `kb_id` 关联列（业务名另存 `kb_name`：删除重建后 id 会变、name 不变）。"""
    row = execute_query("SELECT id FROM adh_knowledge_bases WHERE name=%s", (kb_name,), fetchone=True)
    return int(row["id"]) if row else 0


def _latest_synced(article_id: int) -> dict:
    """当前归属 = 最近一次**成功**同步的水位线行；历史行只用于排障与失败可见。"""
    return execute_query(
        "SELECT id, kb_id, kb_name, revision_id, source_id, doc_title, parent_source_id, "
        "       content_hash, synced_at "
        "FROM adh_content_kb_sync WHERE article_id=%s AND state='synced' ORDER BY id DESC LIMIT 1",
        (int(article_id),), fetchone=True) or {}


def _attempt(article_id: int, kb_name: str, parent_id: str, source_id: str, title: str,
             live_rev: int, text: str, actor: str, *, state: str, error: str = "") -> str:
    """落一行水位线（每次尝试都留痕，重试不覆盖历史）。失败留痕属旁路，不得顶掉真错因。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        execute_write(
            "INSERT INTO adh_content_kb_sync (article_id, kb_id, kb_name, revision_id, state, error, "
            "  source_id, doc_title, parent_source_id, content_hash, synced_by, synced_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (int(article_id), _kb_id(kb_name), kb_name, int(live_rev or 0), state, error[:500],
             str(source_id or ""), title, str(parent_id or ""), _sha(text), actor,
             now if state == "synced" else None))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[content-kb-sync] 水位线写入失败 article=%s state=%s: %s",
                       article_id, state, exc)
    return now


def _record_failure(article_id: int, kb_name: str, title: str, live_rev: int,
                    text: str, actor: str, reason: str) -> None:
    """失败也要留痕：清单上能看到"上次同步失败 + 原因"，而不是静默回到未同步态。"""
    _attempt(article_id, kb_name, "", "", title, live_rev, text, actor,
             state="failed", error=reason)


def _record_success(article_id: int, kb_name: str, parent_id: str, source_id: str, title: str,
                    live_rev: int, text: str, actor: str) -> str:
    return _attempt(article_id, kb_name, parent_id, source_id, title, live_rev, text, actor,
                    state="synced")


# 清单/详情的同步状态不在这里实现：`content_service._kb_sync_of` 是唯一一份
# （列表与详情同源），这里再写一份必然飘。
