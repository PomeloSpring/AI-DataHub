"""内容中心 → 知识库语料同步的契约用例。

覆盖的是"做错就会污染 AI 答案"的那几条判据，全部在 `content_kb_sync_service` 边界上打桩：
云端与内容库都不真碰，但**调用次序与留痕**必须可断言（谁被调了几次、有没有落水位线）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.platform.services import content_kb_sync_service as sync  # noqa: E402
from backend.modules.platform.services import content_service as cs  # noqa: E402
from backend.modules.platform.services import kb_docs_service as kb  # noqa: E402


# ── 替身 ──────────────────────────────────────────────────────────────────────

class FakeKb:
    """知识库文档通道的替身：只记调用，不碰云端。"""

    # 服务层用 `kb.KbDocError` 判错，替身必须带上同一个异常类（否则整个模块被换掉后捕不到）
    KbDocError = kb.KbDocError

    def __init__(self, *, bases=None, tree_nodes=(), remote_content="", delete_fail=False):
        self.bases = bases if bases is not None else [
            {"name": "云端库", "kb_type": "qmind", "status": "active",
             "supports_docs": True, "can_edit": True, "reason": ""},
            {"name": "本地库", "kb_type": "local", "status": "active",
             "supports_docs": False, "can_edit": False, "reason": "文档树目前只支持云端知识库（qmind）"},
        ]
        self.tree_nodes = list(tree_nodes)
        self.remote_content = remote_content
        self.delete_fail = delete_fail
        self.created: list = []
        self.create_flags: list = []      # 每次新建是不是走的自动通道（for_sync）
        self.saved: list = []
        self.dropped: list = []

    def list_bases(self):
        return self.bases

    def tree(self, kb_name):
        return {"nodes": self.tree_nodes, "doc_count": len(self.tree_nodes),
                "folder_count": 0, "flat": True, "base": {"name": kb_name}}

    def read_doc(self, kb_name, source_id):
        return {"source_id": source_id, "content": self.remote_content}

    def create_doc(self, kb_name, title, content, parent_id, actor, *, for_sync=False):
        self.created.append((kb_name, title, content, parent_id))
        self.create_flags.append(for_sync)
        return {"source_id": f"new-{len(self.created)}", "title": title, "warnings": [],
                "message": "已新建文档"}

    def save_doc_for_sync(self, kb_name, source_id, content, *, actor=""):
        self.saved.append((kb_name, source_id, content))
        return {"source_id": f"upd-{len(self.saved)}", "previous_source_id": source_id,
                "title": "x.md", "warnings": [], "message": "已更新语料"}

    def delete_source_for_sync(self, kb_name, source_id, *, actor=""):
        self.dropped.append((kb_name, source_id))
        if self.delete_fail:
            raise kb.KbDocError("qmind source delete 失败：timeout", 502)
        return {"deleted": True, "title": "x.md"}


class FakeDB:
    """同步服务自己的 SQL 替身：条目行 + 水位线读写。"""

    def __init__(self, *, article=None, rows=()):
        self.article = article or {"id": 11, "slug": "scan-accuracy", "status": "published",
                                   "kind": "markdown", "live_revision_id": 88, "version": 3}
        self.attempts = list(rows)          # 已有水位线
        self.writes: list = []

    def query(self, sql, params=None, fetchone=False):
        # 真 execute_query 在 fetchone=True 时回 dict（无行回 None），替身必须同口径，
        # 否则服务层 `row.get(...)` 会在测试里拿到 list —— 那等于把接口契约判据测丢了
        return self._first(sql, params, fetchone)

    def _first(self, sql, params, fetchone):
        rows = self._rows(" ".join(str(sql).split()), params)
        if fetchone:
            return dict(rows[0]) if rows else None
        return rows

    def _rows(self, sql, params):
        if sql.startswith("SELECT id, slug, status, kind, live_revision_id, version"):
            return [dict(self.article)] if self.article else []
        if sql.startswith("SELECT id FROM adh_knowledge_bases WHERE name="):
            return [{"id": 5}]
        if sql.startswith("SELECT id, kb_id, kb_name, revision_id, source_id, doc_title"):
            hit = next((r for r in reversed(self.attempts) if r.get("state") == "synced"), None)
            return [dict(hit)] if hit else []
        return []

    def write(self, sql, params=None):
        self.writes.append((" ".join(str(sql).split()), tuple(params or ())))
        if str(sql).startswith("INSERT INTO adh_content_kb_sync"):
            p = tuple(params or ())
            self.attempts.append({"id": len(self.attempts) + 1, "article_id": p[0], "kb_id": p[1],
                                  "kb_name": p[2], "revision_id": p[3], "state": p[4],
                                  "error": p[5], "source_id": p[6], "doc_title": p[7],
                                  "parent_source_id": p[8], "content_hash": p[9],
                                  "synced_at": p[11]})
        return 1


def patch(monkeypatch, *, fake_kb, fake_db, article=None):
    """把同步服务的三个外部依赖换成替身（云端 + 内容库 + 语料库）。"""
    for name in ("execute_query", "execute_write"):
        monkeypatch.setattr(sync, name, fake_db.query if name == "execute_query" else fake_db.write)
    monkeypatch.setattr(sync, "kb", fake_kb)
    monkeypatch.setattr(cs, "get_article",
                        lambda **kw: (article or {
                            "slug": "scan-accuracy", "title": "口内扫描精度怎么衡量",
                            "summary": "标称 20μm 说的是实验室单帧重复性", "body": "正文一段",
                            "category_label": "技术白皮书", "tags": ["扫描精度"],
                            "kind": "markdown", "published_at": "2026-11-04 16:56",
                            "version": 3, "live_revision_id": 88}))


# ── 渲染 ──────────────────────────────────────────────────────────────────────

def test_render_corpus_puts_stamp_within_detection_window():
    """版本戳必须落在前 600 字符内 —— 冲突判定与新鲜度都靠读它。"""
    text = sync.render_corpus({"slug": "scan-accuracy", "title": "口内扫描精度", "summary": "摘要",
                               "category_label": "技术白皮书", "tags": ["扫描精度"],
                               "kind": "markdown", "body": "正文", "published_at": "2026-11-04 16:56"},
                              revision_id=88, version=3)
    assert text.index("content-version-stamp") < 600
    assert "article=scan-accuracy revision=88" in text
    assert "正文" in text
    assert "在语料侧的改动会在下次同步被覆盖" in text


def test_render_corpus_link_kind_states_original_not_in_kb():
    """外链条目：语料里只有摘要与链接，必须把"原文不在库里"写进正文，不能让 AI 以为有全文。"""
    text = sync.render_corpus({"slug": "ext-doc", "title": "外部标准", "summary": "要点",
                               "kind": "link", "link_url": "https://example.gov.cn/std",
                               "link_label": "原文", "tags": [], "published_at": ""},
                              revision_id=1, version=1)
    assert "外链内容" in text and "原文不在本知识库内" in text
    assert "https://example.gov.cn/std" in text


def test_render_corpus_states_release_without_reshaping_the_stamp():
    """期次进人读的那行元信息，但**版本戳形状一个字不动**。

    闸门靠正则认归属（``_STAMP_RE``）：把期次塞进戳里或改字段顺序，存量语料就全部
    “看起来像人工文档”，同步会反而拒自己上一轮写进去的那一篇。
    """
    art = {"slug": "scan-accuracy", "title": "口内扫描仪行业月报", "summary": "摘要",
           "category_label": "技术白皮书", "tags": ["质量控制"], "kind": "markdown",
           "body": "正文", "published_at": "2026-11-04 16:56",
           "release": 2, "release_label": "2026-10"}
    text = sync.render_corpus(art, revision_id=88, version=3)
    assert "第 2 期" in text and "2026-10" in text, "AS-BOT 要能回答“引的是哪一期”"
    stamp = next(line for line in text.splitlines() if "content-version-stamp" in line)
    assert stamp.startswith("<!-- content-version-stamp article=scan-accuracy revision=88 ")
    assert "期次" not in stamp.split("--")[1], "期次只给人读，不进归属判据"
    assert "第 2 期" not in sync.render_corpus(dict(art, release=0, release_label=""),
                                            revision_id=88, version=3), \
        "未发布过的条目不许凭空多一行期次"


# ── 前置门 ────────────────────────────────────────────────────────────────────

def test_unpublished_article_is_refused_with_reason(monkeypatch):
    """草稿/已下架不进语料：那是让 AS-BOT 拿未定稿口径回答用户。"""
    fake_kb, fake_db = FakeKb(), FakeDB(article={"id": 11, "slug": "s", "status": "draft",
                                                 "kind": "markdown", "live_revision_id": 0, "version": 1})
    patch(monkeypatch, fake_kb=fake_kb, fake_db=fake_db)
    with pytest.raises(sync.SyncError) as exc:
        sync.sync_to_kb("s", "云端库", actor="editor")
    assert exc.value.status == 409 and "已发布" in exc.value.detail
    assert not fake_kb.created and not fake_kb.saved, "被拒不许已经动过云端"


def test_file_kind_is_refused_not_silently_skipped(monkeypatch):
    """附件形态：正文是二进制，本批不接非文本上云 —— 必须拒，不能"同步成功但没内容"。"""
    fake_db = FakeDB(article={"id": 11, "slug": "s", "status": "published",
                              "kind": "file", "live_revision_id": 88, "version": 1})
    patch(monkeypatch, fake_kb=FakeKb(), fake_db=fake_db)
    with pytest.raises(sync.SyncError) as exc:
        sync.sync_to_kb("s", "云端库", actor="editor")
    assert "附件形态" in exc.value.detail


def test_unsupported_kb_target_is_refused_with_server_reason(monkeypatch):
    patch(monkeypatch, fake_kb=FakeKb(), fake_db=FakeDB())
    with pytest.raises(sync.SyncError) as exc:
        sync.sync_to_kb("scan-accuracy", "本地库", actor="editor")
    assert exc.value.status == 409 and "只支持云端知识库" in exc.value.detail

    with pytest.raises(sync.SyncError) as exc:
        sync.sync_to_kb("scan-accuracy", "没这个库", actor="editor")
    assert exc.value.status == 404 and "云端库" in exc.value.detail, "错因要带现有清单，引导而不是让人猜"


# ── 同步主流程 ────────────────────────────────────────────────────────────────

def test_first_sync_creates_doc_and_writes_watermark(monkeypatch):
    fake_kb, fake_db = FakeKb(), FakeDB()
    patch(monkeypatch, fake_kb=fake_kb, fake_db=fake_db)
    out = sync.sync_to_kb("scan-accuracy", "云端库", actor="editor")

    assert len(fake_kb.created) == 1 and not fake_kb.saved
    kb_name, title, text, parent = fake_kb.created[0]
    assert (kb_name, title, parent) == ("云端库", "scan-accuracy.md", "")
    assert "content-version-stamp" in text
    # 自动通道必须带 for_sync：语料正文天生带内容戳（下一轮的归属凭据），
    # 不传会被 create_doc 的“人工不许伪造戳”闸门拦下 → 同步永远建不出新篇
    assert fake_kb.create_flags == [True], fake_kb.create_flags
    assert out["doc_title"] == "scan-accuracy.md" and out["unchanged"] is False
    row = fake_db.attempts[-1]
    assert row["state"] == "synced" and row["kb_name"] == "云端库" and row["revision_id"] == 88, row
    assert "云端要重新解析" in out["message"], "必须说清“同步了但检索稍后才用得到”"


def test_repeat_sync_updates_same_doc_instead_of_duplicating(monkeypatch):
    """已有水位线 → 更新同一篇（传新件+删旧件），不再新建副本。"""
    row = {"id": 1, "article_id": 11, "kb_id": 5, "kb_name": "云端库", "revision_id": 87,
           "state": "synced", "error": "", "source_id": "old-1", "doc_title": "scan-accuracy.md",
           "parent_source_id": "", "content_hash": "sha-old", "synced_at": "2026-11-01 10:00:00"}
    fake_kb, fake_db = FakeKb(), FakeDB(rows=[row])
    patch(monkeypatch, fake_kb=fake_kb, fake_db=fake_db)
    out = sync.sync_to_kb("scan-accuracy", "云端库", actor="editor")

    assert len(fake_kb.saved) == 1 and not fake_kb.created
    assert fake_kb.saved[0][1] == "old-1", "更新必须打在映射指向的那一篇上"
    assert out["source_id"] == "upd-1"
    assert not fake_kb.dropped, "同库更新不需要撤旧（save 通道自己删了旧件）"


def test_no_change_sync_makes_zero_cloud_calls(monkeypatch):
    """版本与正文都没变 → 一个请求都不发：空同步会白换云端 id 并让云端重跑解析。"""
    text = sync.render_corpus({"slug": "scan-accuracy", "title": "口内扫描精度怎么衡量",
                               "summary": "标称 20μm 说的是实验室单帧重复性", "body": "正文一段",
                               "category_label": "技术白皮书", "tags": ["扫描精度"],
                               "kind": "markdown", "published_at": "2026-11-04 16:56"},
                              revision_id=88, version=3)
    row = {"id": 1, "article_id": 11, "kb_id": 5, "kb_name": "云端库", "revision_id": 88,
           "state": "synced", "error": "", "source_id": "old-1", "doc_title": "scan-accuracy.md",
           "parent_source_id": "", "content_hash": sync._sha(text), "synced_at": "2026-11-04 10:00:00"}
    fake_kb, fake_db = FakeKb(), FakeDB(rows=[row])
    patch(monkeypatch, fake_kb=fake_kb, fake_db=fake_db)
    out = sync.sync_to_kb("scan-accuracy", "云端库", actor="editor")

    assert out["unchanged"] is True
    assert not fake_kb.created and not fake_kb.saved and fake_db.attempts == [row]


def test_switching_target_kb_creates_new_and_drops_old(monkeypatch):
    """换目标库：新库建好之后必须撤掉旧库那篇 —— 两处同名语料会被同时检索命中。"""
    row = {"id": 1, "article_id": 11, "kb_id": 5, "kb_name": "云端库", "revision_id": 87,
           "state": "synced", "error": "", "source_id": "old-1", "doc_title": "scan-accuracy.md",
           "parent_source_id": "", "content_hash": "sha-old", "synced_at": "2026-11-01 10:00:00"}
    fake_kb = FakeKb(bases=FakeKb().bases + [
        {"name": "另一个库", "kb_type": "qmind", "status": "active",
         "supports_docs": True, "can_edit": True, "reason": ""}])
    fake_db = FakeDB(rows=[row])
    patch(monkeypatch, fake_kb=fake_kb, fake_db=fake_db)
    out = sync.sync_to_kb("scan-accuracy", "另一个库", actor="editor")

    assert len(fake_kb.created) == 1 and not fake_kb.saved
    assert fake_kb.dropped == [("云端库", "old-1")], "旧库那篇要撤"
    assert out["warnings"] == []


def test_dropping_old_copy_failure_surfaces_as_warning(monkeypatch):
    """撤不掉不阻断（新库已经成功），但必须把"两处都有这篇"说出来。"""
    row = {"id": 1, "article_id": 11, "kb_id": 5, "kb_name": "云端库", "revision_id": 87,
           "state": "synced", "error": "", "source_id": "old-1", "doc_title": "scan-accuracy.md",
           "parent_source_id": "", "content_hash": "sha-old", "synced_at": "2026-11-01 10:00:00"}
    fake_kb = FakeKb(delete_fail=True, bases=FakeKb().bases + [
        {"name": "另一个库", "kb_type": "qmind", "status": "active",
         "supports_docs": True, "can_edit": True, "reason": ""}])
    patch(monkeypatch, fake_kb=fake_kb, fake_db=FakeDB(rows=[row]))
    out = sync.sync_to_kb("scan-accuracy", "另一个库", actor="editor")

    assert out["warnings"] and "没能撤掉" in out["warnings"][0]
    assert "有 1 条需要处理" in out["message"]


def test_missing_remote_doc_falls_back_to_create_with_notice(monkeypatch):
    """映射还在、云端文档已被人删：按新建重落，并把这件事说出来（不静默"当没事发生"）。"""
    row = {"id": 1, "article_id": 11, "kb_id": 5, "kb_name": "云端库", "revision_id": 87,
           "state": "synced", "error": "", "source_id": "gone-1", "doc_title": "scan-accuracy.md",
           "parent_source_id": "", "content_hash": "sha-old", "synced_at": "2026-11-01 10:00:00"}

    class KbGone(FakeKb):
        def save_doc_for_sync(self, kb_name, source_id, content, *, actor=""):
            self.saved.append((kb_name, source_id, content))
            raise kb.KbDocError("这个文档/目录在知识库裡已经不在了", 404)

    fake_kb, fake_db = KbGone(), FakeDB(rows=[row])
    patch(monkeypatch, fake_kb=fake_kb, fake_db=fake_db)
    out = sync.sync_to_kb("scan-accuracy", "云端库", actor="editor")

    assert len(fake_kb.created) == 1, "回退成新建"
    assert out.get("warnings") and "按新建重新落库" in out["warnings"][0]


# ── 不许覆盖人工语料 ──────────────────────────────────────────────────────────

def test_same_title_without_stamp_is_refused(monkeypatch):
    """云端同名文档没有本通道版本戳 = 人工语料，自动通道无权覆盖。"""
    node = {"source_id": "s9", "title": "scan-accuracy.md", "is_dir": False}
    fake_kb, fake_db = FakeKb(tree_nodes=[node], remote_content="# 手写的语料\n\n人工维护"), FakeDB()
    patch(monkeypatch, fake_kb=fake_kb, fake_db=fake_db)
    with pytest.raises(sync.SyncError) as exc:
        sync.sync_to_kb("scan-accuracy", "云端库", actor="editor")
    assert exc.value.status == 409 and "人工维护的语料" in exc.value.detail
    assert not fake_kb.created, "被拒不许已经写过云端"


def test_same_title_owned_by_other_article_is_refused(monkeypatch):
    node = {"source_id": "s9", "title": "scan-accuracy.md", "is_dir": False}
    remote = "<!-- content-version-stamp article=other-slug revision=1 -->\n\n# 别的内容"
    fake_kb, fake_db = FakeKb(tree_nodes=[node], remote_content=remote), FakeDB()
    patch(monkeypatch, fake_kb=fake_kb, fake_db=fake_db)
    with pytest.raises(sync.SyncError) as exc:
        sync.sync_to_kb("scan-accuracy", "云端库", actor="editor")
    assert "属于另一篇内容" in exc.value.detail and "other-slug" in exc.value.detail


# ── 失败必须留痕 ──────────────────────────────────────────────────────────────

def test_cloud_failure_records_failed_attempt_and_propagates(monkeypatch):
    class KbFail(FakeKb):
        def create_doc(self, kb_name, title, content, parent_id, actor, *, for_sync=False):
            self.created.append((kb_name, title, content, parent_id))
            raise kb.KbDocError("不能写入云端语料：数据库连接串", 422)

    fake_kb, fake_db = KbFail(), FakeDB()
    patch(monkeypatch, fake_kb=fake_kb, fake_db=fake_db)
    with pytest.raises(sync.SyncError) as exc:
        sync.sync_to_kb("scan-accuracy", "云端库", actor="editor")
    assert exc.value.status == 422
    row = fake_db.attempts[-1]
    assert row["state"] == "failed" and "连接串" in row["error"], "失败要能在水位线上看到"


# ── 列表状态 ──────────────────────────────────────────────────────────────────

def test_kb_sync_state_stale_rules():
    """stale 判据：成功行按版本比，最新行是失败就算有正文也仍未同步。"""
    base = {"sync_kb_name": "云端库", "sync_doc_title": "a.md", "live_revision_id": 88,
            "sync_revision_id": 88, "sync_synced_at": None, "sync_state": "synced", "sync_error": ""}
    assert cs._kb_sync_of({**base})["stale"] is False
    assert cs._kb_sync_of({**base, "sync_revision_id": 87})["stale"] is True
    failed = cs._kb_sync_of({**base, "sync_state": "failed", "sync_error": "timeout",
                             "sync_revision_id": 0})
    assert failed["stale"] is True and failed["last_error"] == "timeout"
    assert failed["state"] == "failed"
    assert cs._kb_sync_of({"sync_kb_name": ""}) is None, "从未同步过 → null，不是假装已同步"
    assert "source_id" not in cs._kb_sync_of({**base}), "云端内部标识不进响应体"
