"""内容库 seed —— 把 docs/content/insights 的成稿导入内容中心（dry-run 默认）。

背景:
  首批 10 篇成稿（口腔数字化业务的资讯内容）以 markdown + front-matter 的形式落在
  ``docs/content/insights/``，配套 ``manifest.json`` 记录栏目/标签词表与内容规范。
  内容中心（``/content``）上线后需要一个把它们搬进库的入口，且**必须经过发布状态机**，
  不能"导入即上线"。

口径（与 ``docker/mysql/content_center_migration.sql`` 一致）:
  1. **导入只落到「待审核」**：``create_article`` → ``submit_review``。发布是独立动作
     + 独立权限码（``article:publish``，ai_access='none'），必须由人在「审核发布」页点。
     脚本里**不放**任何"直接 UPDATE live_revision_id"的旁路 —— 那等于绕过审核把内容
     推给全员，正是这套状态机要防的事。
  2. **入库前逐条断言 manifest.content_rules**，不通过就报错不写入（宁阻断勿带病入库）:
       - 标题唯一事实源 = front-matter.title → 正文不得出现 H1；
       - 正文小节从 ## 起；
       - 标签必须取自 tag_vocabulary（服务端 ``_clean_tags`` 同样会拒，这里提前拦住）；
       - 占位【待填…未清零 → 不得标记 published:true。
  3. **幂等**：按 slug 命中已有条目则更新 head revision（正文没变就不产生新版本），
     可重复执行。

用法（repo 根目录）:
    venv/bin/python -m backend.scripts.seed_content_library            # 只看计划与校验结果
    venv/bin/python -m backend.scripts.seed_content_library --apply    # 落库并提交审核
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIBRARY = os.path.join(REPO, "docs", "content", "insights")

H1_RE = re.compile(r"^\s*#\s+\S", re.M)
H1_TAG_RE = re.compile(r"<h1[\s>]", re.I)
SECTION_RE = re.compile(r"^\s*(#{1,6})\s", re.M)
PLACEHOLDER_RE = re.compile(r"【待填")


def _split_front_matter(text: str) -> tuple[dict, str]:
    """front-matter 用简易解析（本库的字段都是标量或列表，不需要拉进 yaml 依赖）。"""
    if not text.startswith("---"):
        raise ValueError("缺少 front-matter")
    end = text.index("\n---", 3)
    raw, body = text[3:end].strip(), text[end + 4:].lstrip("\n")
    meta: dict = {}
    key = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")) and ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                meta[key] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
            elif v == "":
                meta[key] = []          # 下面可能是 "- xxx" 列表体
            else:
                meta[key] = v
        elif line.strip().startswith("- ") and key:
            cur = meta.get(key)
            if not isinstance(cur, list):
                cur = []
            cur.append(line.strip()[2:].strip())
            meta[key] = cur
    for k in ("pinned", "featured", "published"):
        if k in meta:
            meta[k] = str(meta[k]).strip().lower() in ("1", "true", "yes")
    return meta, body


def _refs(meta: dict) -> list[dict]:
    """manifest 里的 references 是纯文字（无 URL）。原样带过去，url 留空。

    不编造链接：出处没有可核对地址时，宁可让审核人看到"只有标题"的清单，
    也不生成一个看着像真的的假 URL。
    """
    out = []
    for r in meta.get("references") or []:
        text = str(r).strip()
        if not text:
            continue
        url = ""
        m = re.search(r"https?://\S+", text)
        if m:
            url = m.group(0)
        out.append({"title": text, "url": url})
    return out


def load_articles() -> tuple[list[dict], list[str]]:
    from backend.modules.platform.services import content_service as svc

    with open(os.path.join(LIBRARY, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    vocab = {t for t in (manifest.get("tag_vocabulary") or [])}
    # manifest 用的是 category_key（与 adh_content_categories.category_key 同名）
    categories = {c["category_key"] for c in (manifest.get("categories") or [])}
    rules = manifest.get("content_rules") or []

    items = []
    for entry in manifest["articles"]:
        path = os.path.join(LIBRARY, entry["path"])
        with open(path, encoding="utf-8") as f:
            meta, body = _split_front_matter(f.read())

        problems = []
        # 规则 1/2: 标题不进正文, 小节从 ## 起
        if H1_RE.search(body):
            problems.append("正文出现 H1（标题唯一事实源是 front-matter.title）")
        levels = {len(m.group(1)) for m in SECTION_RE.finditer(body)}
        if 1 in levels:
            problems.append("正文有一级标题小节")
        if not body.strip():
            problems.append("正文是空的")
        # HTML 版式正文：复用门禁同一份正则（不在这里另列一份，两处各维护必然飘移）
        kind = meta.get("kind") or "markdown"
        if kind == svc.KIND_HTML:
            if H1_TAG_RE.search(body):
                problems.append("HTML 正文出现 <h1>（标题唯一事实源是 front-matter.title）")
            bad = svc.HTML_UNSAFE_RE.search(body)
            if bad:
                problems.append(f"HTML 正文含不可用标记: {bad.group(0).strip()[:24]}")
            if body.strip() and not svc.HTML_TAG_RE.search(body):
                problems.append("形态是 html 但正文里没有 HTML 标签（该用 markdown）")
        # 规则 3: 标签受控
        tags = meta.get("tags") or entry.get("tags") or []
        bad_tags = [t for t in tags if t not in vocab]
        if bad_tags:
            problems.append(f"标签不在受控词表: {bad_tags}")
        if meta.get("category") not in categories:
            problems.append(f"栏目 key 不存在: {meta.get('category')}")
        # 规则 4: 占位未清零不得标记已发布。
        # 这是**内容库自身的元数据矛盾**（不是不能入库）：占位稿应当作为草稿导入，
        # 由作者在编辑器里回填；服务端提交门禁本来就会硬拦占位，所以不会漏到线上。
        ph = len(PLACEHOLDER_RE.findall(body))
        conflicts = []
        if ph:
            conflicts.append(f"{ph} 处【待填】占位未回填")
            if meta.get("published"):
                conflicts.append("却标记 published:true（违反 content_rules 第 4 条）")
        # 外部出处为空时只提醒（正文是否引用外部数字需要人判断，不替人下结论）
        refs = _refs(meta)
        warn = "" if refs else "没有登记 references，提交后审核人会看到 refs_missing 软提醒"

        items.append({
            "slug": meta.get("slug") or entry["slug"],
            "title": meta.get("title") or entry["title"],
            "summary": meta.get("summary") or entry.get("summary") or "",
            "category": meta.get("category"),
            "tags": tags,
            "kind": meta.get("kind") or "markdown",
            "body": body,
            "refs": refs,
            "pinned": bool(meta.get("pinned")),
            "featured": bool(meta.get("featured")),
            "review_by": str(meta.get("review_by") or "")[:10],
            "author": str(meta.get("author") or entry.get("author") or "内容库导入"),
            "placeholders": ph,
            "problems": problems,
            "conflicts": conflicts,
            "warn": warn,
        })
    return items, rules


def existing_slug(slug: str) -> dict | None:
    from backend.common.db import execute_query
    return execute_query(
        "SELECT id, head_revision_id, live_revision_id, status, version "
        "FROM adh_content_articles WHERE slug=%s", (slug,), fetchone=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="导入 docs/content/insights 成稿到内容中心")
    ap.add_argument("--apply", action="store_true", help="真正落库（默认只校验并打印计划）")
    args = ap.parse_args()

    from backend.modules.platform.services import content_service as svc

    items, rules = load_articles()
    print(f"内容规范（来自 manifest.content_rules，逐条断言）:")
    for r in rules:
        print(f"  · {r}")
    print(f"\n待导入 {len(items)} 篇\n")

    invalid = [it for it in items if it["problems"]]
    pending = [it for it in items if it["conflicts"]]
    for it in items:
        state = "✗ " + "；".join(it["problems"]) if it["problems"] else (
            "△ " + "，".join(it["conflicts"]) if it["conflicts"] else "✓ 可直接提交审核")
        extra = f"（{it['warn']}）" if it["warn"] else ""
        print(f"  {it['slug']:38} {it['category']:11} {state}{extra}")

    if invalid:
        print(f"\n有 {len(invalid)} 篇存在结构性问题（H1 进正文 / 标签越界 / 栏目悬空 / 正文为空），"
              f"不写入任何内容（宁阻断勿带病入库）。")
        return 1
    if pending:
        print(f"\n{len(pending)} 篇带【待填】占位：会以**草稿**入库，不会提交审核，"
              f"更不会被发布。回填后在「我的草稿」里提交即可。")

    if not args.apply:
        print("\ndry-run 结束（未写库）。加 --apply 才会落库：无占位的进「待审核」，有占位的留「草稿」。")
        return 0

    created, updated, unchanged, blocked, skipped = [], [], [], [], []
    for it in items:
        actor = it["author"]
        payload = {k: it[k] for k in
                   ("slug", "title", "summary", "category", "tags", "kind", "body",
                    "refs", "pinned", "featured", "review_by")}
        row = existing_slug(it["slug"])
        if row:
            current = svc._revision_body(int(row["head_revision_id"] or 0))
            if current == it["body"]:
                unchanged.append(it["slug"])
            else:
                svc.update_article(int(row["id"]), payload, actor,
                                   expected_version=int(row["version"]))
                updated.append(it["slug"])
            article_id = int(row["id"])
        else:
            article_id = int(svc.create_article(payload, actor)["id"])
            created.append(it["slug"])

        # 带占位的留在草稿并说明原因（服务端提交门禁本来也会硬拦，这里不重复撞墙）
        if it["conflicts"]:
            skipped.append((it["slug"], "，".join(it["conflicts"])))
            continue
        article = svc.get_article(article_id=article_id, can_edit=True, revision="head")
        if article["status"] != svc.STATUS_DRAFT:
            continue
        try:
            svc.submit_review(article_id, actor)
        except svc.ContentError as exc:
            blocked.append((it["slug"], exc.detail))

    print(f"\n落库完成: 新建 {len(created)} · 更新 {len(updated)} · 正文未变 {len(unchanged)}")
    if skipped:
        print(f"其中 {len(skipped)} 篇留在草稿（占位未回填，不能提交审核）:")
        for slug, why in skipped:
            print(f"  - {slug}: {why}")
    if blocked:
        print(f"其中 {len(blocked)} 篇被提交门禁拦回草稿（占位/栏目等原因，需作者改完再提交）:")
        for slug, reason in blocked:
            print(f"  - {slug}: {reason}")
    print("下一步（人工）：在「内容中心 → 内容管理」筛出待审、逐条确认提醒并通过，再由具备"
          "「发布与下架内容」权限的人发布。脚本不代做发布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
