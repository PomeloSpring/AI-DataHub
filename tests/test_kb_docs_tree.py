"""知识库文档树（内容中心「知识库文档」）的离线回归: 不连库、不连 CLI、不碰真云端。

这批的出错代价是"改坏 AI 的知识"或"把云端故障演成空库", 所以断言集中在:

- 目录树组装: 父子嵌套、父缺失挂根并标注、扁平库不假造层级;
- **只提交修改的部分**: 保存只动当前一篇(upload 一次 + delete 旧 id 一次), 其余不碰;
- 保存后 source_id 变更要如实回给前端(拿旧 id 再改就是改不存在的文档);
- 乐观并发: 云端基线变了就 409, 不覆盖别人的编辑;
- **派生物只读**: 带 model-version-stamp 的本体上云文档一律 403, 指回本体建模;
  带 content-version-stamp 的内容语料同样 403, 指回内容管理（下次同步会整体覆盖人工改动）;
  人工新建也不许伪造这两个戳（戳是自动通道的归属凭据）;
- 云端失败必须外抛可读错因 —— 回空清单会被读成"库里没文档"(no-silent-degradation);
- 内部标识不外泄: uri/originUrl/notebookId/orgId 不进响应;
- 旧的 `/knowledge-bases/{id}/documents` 缺陷不复发(查不存在的列 + except 吞成空列表)。
"""
import asyncio
import json

import pytest

from backend.modules.platform.services import kb_docs_service as svc
from backend.modules.mind.rag.qmind_retriever import QMindCliError

DERIVED = ("<!-- model-version-stamp model_id=1793775073151614 "
           "model_version=2026-11-04T15:19:27 synced_at=2026-11-04T07:19:55 -->\n\n"
           "# 本体模型: 应用使用域\n\n正文")
PLAIN = "# 站点全景\n\n默认分站呈现, 不跨站合计。"


class FakeCli:
    """qmind CLI 替身: 记录调用, 失败可注入。"""

    QMindCliError = QMindCliError

    def __init__(self, sources=None, contents=None, fail_list=False, fail_upload=False,
                 fail_delete=False):
        self.sources = sources if sources is not None else []
        self.contents = contents or {}
        self.fail_list = fail_list
        self.fail_upload = fail_upload
        self.fail_delete = fail_delete
        self.uploads = []          # [(title, parent_id, path)]
        self.deletes = []
        self.dirs = []
        self._seq = 5000

    def list_sources(self, notebook_id):
        if self.fail_list:
            raise QMindCliError("FORBIDDEN: insufficient notebook permission")
        return [dict(s) for s in self.sources]

    def read_source_content(self, notebook_id, source_id):
        if self.fail_list:
            raise QMindCliError("FORBIDDEN: insufficient notebook permission")
        return self.contents.get(str(source_id), "")

    def upload_source(self, notebook_id, local_path, title, *, parent_id="", path="", source_type="markdown"):
        with open(local_path, encoding="utf-8") as f:
            body = f.read()
        self._seq += 1
        nid = f"new-{self._seq}"
        self.uploads.append({"id": nid, "title": title, "parent_id": parent_id,
                             "path": path, "body": body})
        if self.fail_upload:
            raise QMindCliError("FILE_TOO_LARGE: 2 MB exceeded")
        return nid

    def delete_source(self, notebook_id, source_id):
        self.deletes.append(str(source_id))
        if self.fail_delete:
            raise QMindCliError("INTERNAL: delete denied")

    def build_source_tree(self, items):
        from backend.modules.mind.rag.qmind_retriever import build_source_tree
        return build_source_tree(items)

    def create_source_dir(self, notebook_id, title, *, parent_id=""):
        self._seq += 1
        nid = f"dir-{self._seq}"
        self.dirs.append({"id": nid, "title": title, "parent_id": parent_id})
        return nid


def _src(sid, title, is_dir=False, parent="", status="ready", **over):
    row = {"id": sid, "title": title, "isDir": is_dir, "parentSourceId": parent,
           "path": "", "sourceType": "markdown", "status": status,
           "updatedAt": {"seconds": 1791452226, "nanos": 0},
           "uri": "notebook/sources/secret.md", "originUrl": "https://oss/presigned?sig=abc",
           "orgId": "org-1", "userId": "u-1", "notebookId": "nb-1", "errorMessage": ""}
    row.update(over)
    return row


KB_ROWS = {
    "云端库": {"name": "云端库", "description": "", "kb_type": "qmind", "status": "active",
               "qmind_notebook_id": "nb-1", "source_config": json.dumps({"notebook_id": "nb-1"})},
    "本地库": {"name": "本地库", "description": "", "kb_type": "local", "status": "active",
               "qmind_notebook_id": None, "source_config": json.dumps({"path": "/data/kb"})},
    "没绑库": {"name": "没绑库", "description": "", "kb_type": "qmind", "status": "active",
               "qmind_notebook_id": None, "source_config": "{}"},
    "停用库": {"name": "停用库", "description": "", "kb_type": "qmind", "status": "disabled",
               "qmind_notebook_id": "nb-9", "source_config": "{}"},
    # 百炼：有意只读的外部语料（不是"本批没做"），文案必须与未接入类型区分开
    "百炼库": {"name": "百炼库", "description": "", "kb_type": "cloud_rag", "status": "active",
               "qmind_notebook_id": None,
               "source_config": json.dumps({"provider": "aliyun", "index_id": "r0t4r2c4ig"})},
}


class FakeDB:
    def query(self, sql, params=None, fetchone=False):
        params = tuple(params or ())
        if sql.startswith("SELECT name, description, kb_type, status, source_config, qmind_notebook_id "
                          "FROM adh_knowledge_bases WHERE name="):
            row = KB_ROWS.get(str(params[0]))
            return [dict(row)] if row else []
        if sql.startswith("SELECT name, description, kb_type, status, source_config, qmind_notebook_id "
                          "FROM adh_knowledge_bases ORDER BY name"):
            return [dict(r) for r in KB_ROWS.values()]
        if sql.startswith("SELECT name FROM adh_knowledge_bases ORDER BY name"):
            return [{"name": n} for n in KB_ROWS]
        raise AssertionError(f"FakeDB 未覆盖的 SQL: {sql[:120]}")


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(svc, "execute_query", FakeDB().query)
    return FakeDB()


def use_cli(monkeypatch, cli):
    monkeypatch.setattr(svc, "_qmind_cli", lambda: cli)
    return cli


def _flat_cli(**kw):
    return FakeCli(sources=[_src("s1", "00-说明.md"), _src("s2", "01-站点.md", status="pending")],
                   contents={"s1": PLAIN, "s2": "# 站点\n"}, **kw)


# ── 1. 清单与目录树 ───────────────────────────────────────────────────────────

def test_list_bases_keeps_unsupported_types_visible_with_reason(db):
    """不支持的类型照样列出来并写明原因 —— 藏起来会被读成"库没了"。"""
    items = svc.list_bases()
    by = {i["name"]: i for i in items}
    assert by["云端库"]["supports_docs"] and by["云端库"]["can_edit"]
    assert by["本地库"]["supports_docs"] is False and "本批未接入" in by["本地库"]["reason"]
    assert "还没有绑定云端 notebook" in by["没绑库"]["reason"]
    assert "未启用" in by["停用库"]["reason"]
    blob = json.dumps(items, ensure_ascii=False)
    assert "nb-1" not in blob and "nb-9" not in blob, "notebook id 的值不进响应体"


def test_external_readonly_kb_reason_is_not_mistaken_for_unimplemented(db):
    """「外部只读」是产品决策、「通道未接入」是本批没做 —— 混成一句会把决策说成缺陷。"""
    by = {i["name"]: i for i in svc.list_bases()}
    assert by["百炼库"]["supports_docs"] is False
    assert "外部只读语料" in by["百炼库"]["reason"] and "本批未接入" not in by["百炼库"]["reason"]
    assert "本批未接入" in by["本地库"]["reason"] and "外部只读" not in by["本地库"]["reason"]


def test_tree_builds_children_and_sorts_dirs_first(db, monkeypatch):
    cli = use_cli(monkeypatch, FakeCli(sources=[
        _src("d1", "合规", is_dir=True), _src("s2", "b.md", parent="d1"), _src("s1", "a.md"),
        _src("s3", "孤儿.md", parent="不存在的目录"),
    ], contents={}))
    out = svc.tree("云端库")
    assert [n["title"] for n in out["nodes"]] == ["合规", "a.md", "孤儿.md"]
    assert out["nodes"][0]["children"][0]["title"] == "b.md"
    assert out["doc_count"] == 3 and out["folder_count"] == 1 and out["flat"] is False
    assert out["nodes"][2].get("orphan") is True      # 父缺失挂根, 但如实标注
    for n in out["nodes"]:
        for forbidden in ("uri", "originUrl", "orgId", "userId", "notebookId"):
            assert forbidden not in json.dumps(n, ensure_ascii=False)
    assert cli.uploads == []                          # 读操作不写云端


def test_flat_notebook_is_not_faking_hierarchy(db, monkeypatch):
    use_cli(monkeypatch, _flat_cli())
    out = svc.tree("云端库")
    assert out["flat"] is True and out["folder_count"] == 0
    assert all(not n["parent_id"] for n in out["nodes"])


def test_cli_failure_is_not_disguised_as_empty_tree(db, monkeypatch):
    """云端失败必须炸出来: 空树 = 用户以为库里没文档(本批修掉的正是这类静默)。"""
    use_cli(monkeypatch, FakeCli(fail_list=True))
    with pytest.raises(svc.KbDocError) as exc:
        svc.tree("云端库")
    assert exc.value.status == 502
    assert "insufficient notebook permission" in exc.value.detail
    with pytest.raises(svc.KbDocError) as exc:
        svc.tree("不存在的库")
    assert exc.value.status == 404 and "现有知识库" in exc.value.detail


def test_unbound_notebook_tells_the_admin_where_to_fix(db):
    with pytest.raises(svc.KbDocError) as exc:
        svc.tree("没绑库")
    assert "系统配置" in exc.value.detail and exc.value.status == 409


# ── 2. 只提交修改的那一篇 ─────────────────────────────────────────────────────

def test_save_touches_only_the_edited_document(db, monkeypatch):
    cli = use_cli(monkeypatch, FakeCli(
        sources=[_src("s1", "00-说明.md"), _src("s2", "01-站点.md")],
        contents={"s1": PLAIN, "s2": "# 别的文档"}))
    doc = svc.read_doc("云端库", "s1")
    out = svc.save_doc("云端库", "s1", "# 站点全景\n\n改了一个字。", doc["content_sha256"], "editor")
    assert len(cli.uploads) == 1 and cli.uploads[0]["title"] == "00-说明.md"
    assert cli.uploads[0]["body"] == "# 站点全景\n\n改了一个字。"
    assert cli.deletes == ["s1"], "只删被替换的那一篇旧件"
    assert out["source_id"] != "s1" and out["previous_source_id"] == "s1"
    assert "只提交这一篇" in out["message"] or "这一篇" in out["message"]


def test_save_conflicts_when_remote_changed_first(db, monkeypatch):
    """乐观并发: 基线不是云端当前内容就 409, 不强覆盖别人的编辑。"""
    cli = use_cli(monkeypatch, FakeCli(sources=[_src("s1", "a.md")], contents={"s1": "云端新版"}))
    with pytest.raises(svc.KbDocError) as exc:
        svc.save_doc("云端库", "s1", "我的改动", "打开时那份的哈希", "editor")
    assert exc.value.status == 409
    assert cli.uploads == [] and cli.deletes == []


def test_empty_oversize_and_credential_content_rejected(db, monkeypatch):
    cli = use_cli(monkeypatch, FakeCli(sources=[_src("s1", "a.md")], contents={"s1": PLAIN}))
    base = svc.read_doc("云端库", "s1")["content_sha256"]
    with pytest.raises(svc.KbDocError) as exc:
        svc.save_doc("云端库", "s1", "   ", base, "editor")
    assert "不许用「清空」代替删除" in exc.value.detail
    with pytest.raises(svc.KbDocError) as exc:
        svc.save_doc("云端库", "s1", "x" * (svc.MAX_DOC_BYTES + 1), base, "editor")
    assert "单篇不能超过" in exc.value.detail
    with pytest.raises(svc.KbDocError) as exc:
        svc.save_doc("云端库", "s1", "连一下库 mysql://root:pass@10.0.0.8:3306/adh2 就好", base, "editor")
    assert exc.value.status == 422 and "凭据" in exc.value.detail
    assert cli.uploads == [], "被拒的写入不得留下云端新件"


def test_derived_ontology_doc_is_read_only(db, monkeypatch):
    """本体上云文档是派生物: 读时标注只读, 写时二次核验并 403(下次同步会覆盖它)。"""
    cli = use_cli(monkeypatch, FakeCli(sources=[_src("s1", "本体模型-x.md")], contents={"s1": DERIVED}))
    doc = svc.read_doc("云端库", "s1")
    assert doc["derived"] is True and "本体建模" in doc["read_only_reason"]
    with pytest.raises(svc.KbDocError) as exc:
        svc.save_doc("云端库", "s1", DERIVED + "\n改一行", doc["content_sha256"], "editor")
    assert exc.value.status == 403
    assert cli.uploads == [] and cli.deletes == []


def test_content_synced_corpus_is_read_only_for_humans(db, monkeypatch):
    """内容中心同步生成的语料：人工在树上改会被下次同步静默顶掉，必须 403 指回内容管理。"""
    corpus = ("<!-- content-version-stamp article=scan-accuracy revision=88 version=3 "
              "published_at=2026-11-04 -->\n\n# 口内扫描精度\n\n正文")
    cli = use_cli(monkeypatch, FakeCli(sources=[_src("s1", "scan-accuracy.md")], contents={"s1": corpus}))
    doc = svc.read_doc("云端库", "s1")
    assert doc["derived"] is True and "内容管理" in doc["read_only_reason"]
    with pytest.raises(svc.KbDocError) as exc:
        svc.save_doc("云端库", "s1", corpus + "\n手工补一句", doc["content_sha256"], "editor")
    assert exc.value.status == 403
    assert cli.uploads == [] and cli.deletes == [], "被拒的人工改动不得已经落云端"


def test_new_doc_cannot_forge_content_stamp(db, monkeypatch):
    """带内容戳的新文档 = 伪造归属凭据，下次同步会把它当自己生成的直接顶掉。"""
    use_cli(monkeypatch, FakeCli(sources=[_src("s1", "a.md")], contents={"s1": PLAIN}))
    with pytest.raises(svc.KbDocError) as exc:
        svc.create_doc("云端库", "伪造.md",
                       "<!-- content-version-stamp article=other revision=1 -->\n# 顶包", "", "editor")
    assert exc.value.status == 422 and "内容同步版本戳" in exc.value.detail


def test_upload_ok_but_old_delete_failed_is_reported(db, monkeypatch):
    """新件已生效、旧件没删掉 = 云端短暂两版。必须说出来, 不能报"已保存"了事。"""
    cli = use_cli(monkeypatch, FakeCli(sources=[_src("s1", "a.md")], contents={"s1": PLAIN},
                                        fail_delete=True))
    base = svc.read_doc("云端库", "s1")["content_sha256"]
    out = svc.save_doc("云端库", "s1", "# 改过了", base, "editor")
    assert out.get("warning") and "旧版没能删除" in out["warning"]
    assert len(cli.uploads) == 1


# ── 3. 新建 ───────────────────────────────────────────────────────────────────

def test_create_doc_sanitizes_title_and_rejects_stamp(db, monkeypatch):
    cli = use_cli(monkeypatch, FakeCli(sources=[_src("d1", "合规", is_dir=True),
                                                _src("s1", "文档.md")], contents={}))
    out = svc.create_doc("云端库", "../../危险名字", "# 内容", "d1", "editor")
    assert "/" not in out["title"] and out["title"].endswith(".md")
    assert cli.uploads[0]["parent_id"] == "d1"
    with pytest.raises(svc.KbDocError) as exc:
        svc.create_doc("云端库", "假派生.md", DERIVED, "", "editor")
    assert exc.value.status == 422
    with pytest.raises(svc.KbDocError) as exc:
        svc.create_doc("云端库", "x.md", "# 内容", "s1", "editor")
    assert "只能在目录下新建文档" in exc.value.detail      # 父是文档 → 拒, 不静默挂到根


def test_create_folder_requires_dir_parent(db, monkeypatch):
    cli = use_cli(monkeypatch, FakeCli(sources=[_src("s1", "文档.md")], contents={}))
    with pytest.raises(svc.KbDocError) as exc:
        svc.create_folder("云端库", "新目录", "s1", "editor")
    assert "只能在目录下再建目录" in exc.value.detail
    assert cli.dirs == []


# ── 4. /knowledge-bases/{id}/documents 的旧缺陷不复发 ─────────────────────────

KB_API = "backend/modules/mind/api/knowledge_bases.py"


def test_documents_endpoint_no_longer_queries_missing_columns():
    """锁定根因: 老代码 SELECT adh_knowledge_chunks.knowledge_base_id(不存在的列)
    并 `except Exception` 吞成 documents=[] —— 两个反模式都不许回来。"""
    import ast
    import io
    import os
    path = os.path.join(os.path.dirname(__file__), "..", KB_API)
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "list_knowledge_base_documents")
    # 从 docstring 之后开始查: 历史原因写在文档字符串里是**该留的**, 不能一起当反模式
    lines = src.splitlines(True)
    body = "".join(lines[fn.body[1].lineno - 1: fn.body[-1].end_lineno])
    assert "adh_knowledge_chunks" not in body
    assert "knowledge_base_id" not in body
    assert "except Exception" not in body


def test_documents_endpoint_returns_real_cloud_list(db, monkeypatch):
    from backend.modules.mind.api import knowledge_bases as kb_api
    from backend.modules.mind.rag import qmind_retriever as qr

    class FakeCursor:
        def __init__(self, row): self.row = row
        def execute(self, sql, params=None): self.sql = sql
        def fetchone(self): return self.row

    class FakeConn:
        def __init__(self, row): self.row = row
        def cursor(self):
            outer = self
            class _Ctx:
                def __enter__(_): return FakeCursor(outer.row)
                def __exit__(_, *a): return False
            return _Ctx()
        def close(self): pass

    monkeypatch.setattr(kb_api, "get_metadata_conn",
                        lambda: FakeConn(dict(KB_ROWS["云端库"], id=9)))
    use_cli(monkeypatch, _flat_cli())
    monkeypatch.setattr(qr, "list_sources", lambda nb: [
        _src("s1", "00-说明.md"), _src("d1", "目录", is_dir=True),
        _src("s2", "01-站点.md", parent="d1")])
    res = asyncio.run(kb_api.list_knowledge_base_documents(9, page=1, page_size=20))
    assert res["total"] == 2
    # 遍历是目录树顺序: 目录在前 → 目录里的文档先出, 再是根上的文档
    assert [d["title"] for d in res["documents"]] == ["01-站点.md", "00-说明.md"]
    assert res["knowledge_base"] == "云端库"
    assert all("uri" not in d and "originUrl" not in d for d in res["documents"])


def test_documents_endpoint_fails_loud_for_other_shapes(db, monkeypatch):
    from backend.modules.mind.api import knowledge_bases as kb_api
    from backend.modules.mind.rag import qmind_retriever as qr
    from fastapi import HTTPException

    class FakeCursor:
        def __init__(self, row): self.row = row
        def execute(self, sql, params=None): pass
        def fetchone(self): return self.row

    class FakeConn:
        def __init__(self, row): self.row = row
        def cursor(self):
            outer = self
            class _Ctx:
                def __enter__(_): return FakeCursor(outer.row)
                def __exit__(_, *a): return False
            return _Ctx()
        def close(self): pass

    # local 类型: 元数据里根本没有"某库的文档"这件事 → 409 说明, 不回空列表
    monkeypatch.setattr(kb_api, "get_metadata_conn",
                        lambda: FakeConn(dict(KB_ROWS["本地库"], id=3)))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(kb_api.list_knowledge_base_documents(3, page=1, page_size=20))
    assert exc.value.status_code == 409 and "永远为空" in exc.value.detail

    # 云端失败 → 502(不是 200 + 空清单)
    monkeypatch.setattr(kb_api, "get_metadata_conn",
                        lambda: FakeConn(dict(KB_ROWS["云端库"], id=9)))
    def boom(nb):
        raise QMindCliError("timeout")
    monkeypatch.setattr(qr, "list_sources", boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(kb_api.list_knowledge_base_documents(9, page=1, page_size=20))
    assert exc.value.status_code == 502
