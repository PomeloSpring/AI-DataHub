"""Unit tests for multimodal package — 分类/解析器/工作区落盘/content blocks/发送解析.

附件即会话工作区文件:无附件 ID、无 adh_chat_attachments 表、无独立附件存储。
"""

import asyncio
import io
import json
import os
import sys
import zipfile
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datamind.multimodal import (
    ALLOWED_EXTENSIONS, MAX_FILE_SIZE, MAX_FILES_PER_REQUEST, classify_extension,
)
from services.datamind.multimodal import loader
from services.datamind.multimodal.loader import (
    build_user_content, normalize_rel_path, resolve_workspace_file,
    save_derived_file, write_upload_file,
)
from services.datamind.multimodal.table_parser import parse_table_file
from services.datamind.multimodal.doc_parser import extract_document_text
from services.datamind.execution.secure_sdk import place_attachments
from services.datamind.api.send_payload import _validate_upload


# ── classify_extension ─────────────────────────────────────────────

class TestClassifyExtension:
    @pytest.mark.parametrize("ext,expected", [
        (".png", "image"), (".JPG", "image"), (".webp", "image"),
        (".csv", "table"), (".xlsx", "table"),
        (".pdf", "document"), (".md", "document"), (".docx", "document"),
        (".obj", "model3d"), (".glb", "model3d"), (".stl", "model3d"),
    ])
    def test_known_extensions(self, ext, expected):
        assert classify_extension(ext) == expected

    def test_unknown_returns_none(self):
        assert classify_extension(".exe") is None
        assert classify_extension("") is None

    def test_allowed_extensions_cover_categories(self):
        assert ".csv" in ALLOWED_EXTENSIONS and ".exe" not in ALLOWED_EXTENSIONS


# ── table_parser / doc_parser (纯文件解析器,契约不变) ──────────────

class TestTableParser:
    def test_parse_csv(self, tmp_path):
        p = tmp_path / "sales.csv"
        p.write_text("id,name,amount\n1,alpha,10.5\n2,beta,20.0\n3,gamma,30.25\n", encoding="utf-8")
        result = parse_table_file(str(p), "sales.csv")
        assert result["row_count"] == 3
        assert [c["name"] for c in result["columns"]] == ["id", "name", "amount"]
        assert "sales.csv" in result["preview_text"]
        assert "alpha" in result["preview_text"]
        assert "error" not in result

    def test_parse_xlsx(self, tmp_path):
        import pandas as pd

        p = tmp_path / "data.xlsx"
        pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_excel(str(p), index=False)
        result = parse_table_file(str(p), "data.xlsx")
        assert result["row_count"] == 2
        assert [c["name"] for c in result["columns"]] == ["a", "b"]

    def test_unsupported_type(self, tmp_path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"\x00\x01")
        result = parse_table_file(str(p), "f.bin")
        assert result["error"]
        assert result["row_count"] == 0

    def test_preview_truncation(self, tmp_path):
        lines = ["col1,col2"] + [f"v{i}," + "x" * 200 for i in range(500)]
        p = tmp_path / "big.csv"
        p.write_text("\n".join(lines), encoding="utf-8")
        result = parse_table_file(str(p), "big.csv")
        assert len(result["preview_text"]) <= 6000 + 20


class TestDocParser:
    def test_txt_and_md(self, tmp_path):
        p = tmp_path / "note.md"
        p.write_text("# 标题\n正文内容", encoding="utf-8")
        result = extract_document_text(str(p), "note.md")
        assert result["text"] == "# 标题\n正文内容"
        assert result["truncated"] is False

    def test_truncation(self, tmp_path):
        p = tmp_path / "long.txt"
        p.write_text("A" * 9000, encoding="utf-8")
        result = extract_document_text(str(p), "long.txt")
        assert result["truncated"] is True
        assert len(result["text"]) < 9000

    def test_docx(self, tmp_path):
        p = tmp_path / "doc.docx"
        xml = (
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>第一段文字</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>第二段文字</w:t></w:r></w:p></w:body></w:document>"
        )
        with zipfile.ZipFile(str(p), "w") as z:
            z.writestr("word/document.xml", xml)
        result = extract_document_text(str(p), "doc.docx")
        assert "第一段文字" in result["text"]
        assert "第二段文字" in result["text"]
        assert "<" not in result["text"]

    def test_broken_pdf_returns_error(self, tmp_path):
        p = tmp_path / "bad.pdf"
        p.write_bytes(b"not a pdf")
        result = extract_document_text(str(p), "bad.pdf")
        assert result["error"]
        assert "解析失败" in result["text"]


# ── loader: 路径守卫与工作区落盘 ──────────────────────────────────

class TestWorkspacePathGuard:
    def test_normalize_rel_path(self):
        assert normalize_rel_path("/workspace/uploads/a.png") == "uploads/a.png"
        assert normalize_rel_path("workspace/a.png") == "a.png"
        assert normalize_rel_path("./a.png") == "a.png"
        assert normalize_rel_path("a.png") == "a.png"

    def test_resolve_ok(self, tmp_path):
        (tmp_path / "uploads").mkdir()
        (tmp_path / "uploads" / "a.png").write_bytes(b"x")
        target = resolve_workspace_file(tmp_path, "uploads/a.png")
        assert target == (tmp_path / "uploads" / "a.png").resolve()

    def test_resolve_rejects_traversal(self, tmp_path):
        with pytest.raises(ValueError):
            resolve_workspace_file(tmp_path, "../outside.png")
        with pytest.raises(ValueError):
            resolve_workspace_file(tmp_path, "uploads/../../outside.png")

    def test_resolve_rejects_missing_rel(self, tmp_path):
        with pytest.raises(ValueError):
            resolve_workspace_file(tmp_path, "")
        with pytest.raises(ValueError):
            resolve_workspace_file(tmp_path, "/")

    def test_resolve_rejects_symlink_escape(self, tmp_path):
        outside = tmp_path.parent / "outside_secret.png"
        outside.write_bytes(b"secret")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "link.png").symlink_to(outside)
        with pytest.raises(ValueError):
            resolve_workspace_file(ws, "link.png")


class TestWriteUploadFile:
    def test_write_and_classify(self, tmp_path):
        att = write_upload_file(tmp_path, "pic.png", b"\x89PNG-fake")
        assert att == {"filename": "pic.png", "category": "image",
                       "path": "uploads/pic.png", "size": 9}
        assert (tmp_path / "uploads" / "pic.png").read_bytes() == b"\x89PNG-fake"

    def test_dedupe_same_name(self, tmp_path):
        a1 = write_upload_file(tmp_path, "a.csv", b"x")
        a2 = write_upload_file(tmp_path, "a.csv", b"y")
        assert a1["path"] == "uploads/a.csv"
        assert a2["filename"] == "a_1.csv" and a2["path"] == "uploads/a_1.csv"

    def test_rejects_bad_extension(self, tmp_path):
        with pytest.raises(ValueError, match="不支持的文件类型"):
            write_upload_file(tmp_path, "evil.exe", b"MZ")

    def test_rejects_bad_name(self, tmp_path):
        with pytest.raises(ValueError, match="文件名无效"):
            write_upload_file(tmp_path, "", b"x")
        with pytest.raises(ValueError, match="文件名无效"):
            write_upload_file(tmp_path, "..", b"x")

    def test_save_derived_file(self, tmp_path):
        att = save_derived_file(tmp_path, b"\x89PNG-derived", "resized_a.png")
        assert att["category"] == "image"
        assert att["path"] == "uploads/resized_a.png"
        assert (tmp_path / "uploads" / "resized_a.png").exists()


# ── place_attachments (secure_sdk): 上传落盘 + 工作区引用校验 ─────

class TestPlaceAttachments:
    def test_upload_content_lands_in_workspace(self, tmp_path):
        task = SimpleNamespace(attachments=[
            {"filename": "a.csv", "category": "table", "size": 7, "content": b"x,y\n1,2"},
        ])
        runtime = SimpleNamespace(workspace=tmp_path)
        place_attachments(task, runtime)
        assert task.attachments == [
            {"filename": "a.csv", "category": "table", "path": "uploads/a.csv", "size": 7},
        ]
        assert (tmp_path / "uploads" / "a.csv").read_bytes() == b"x,y\n1,2"

    def test_existing_workspace_file_ref(self, tmp_path):
        placed = write_upload_file(tmp_path, "pic.png", b"\x89PNG")
        task = SimpleNamespace(attachments=[{"path": placed["path"]}])
        runtime = SimpleNamespace(workspace=tmp_path)
        place_attachments(task, runtime)
        assert task.attachments == [
            {"filename": "pic.png", "category": "image", "path": "uploads/pic.png", "size": 4},
        ]

    def test_missing_ref_fails_loud(self, tmp_path):
        task = SimpleNamespace(attachments=[{"path": "uploads/none.png"}])
        with pytest.raises(ValueError, match="附件不存在"):
            place_attachments(task, SimpleNamespace(workspace=tmp_path))

    def test_traversal_ref_fails_loud(self, tmp_path):
        task = SimpleNamespace(attachments=[{"path": "../etc/passwd"}])
        with pytest.raises(ValueError):
            place_attachments(task, SimpleNamespace(workspace=tmp_path))

    def test_too_many_files_fails_loud(self, tmp_path):
        task = SimpleNamespace(attachments=[
            {"filename": f"f{i}.csv", "category": "table", "size": 1, "content": b"x"}
            for i in range(MAX_FILES_PER_REQUEST + 1)
        ])
        with pytest.raises(ValueError, match="最多携带"):
            place_attachments(task, SimpleNamespace(workspace=tmp_path))

    def test_oversized_upload_fails_loud(self, tmp_path):
        task = SimpleNamespace(attachments=[
            {"filename": "big.csv", "category": "table", "size": 1,
             "content": b"A" * (MAX_FILE_SIZE + 1)},
        ])
        with pytest.raises(ValueError, match="大小限制"):
            place_attachments(task, SimpleNamespace(workspace=tmp_path))

    def test_no_attachments_noop(self, tmp_path):
        task = SimpleNamespace(attachments=[])
        place_attachments(task, SimpleNamespace(workspace=tmp_path))
        assert task.attachments == []


# ── loader.build_user_content (工作区契约,无 DB/attachment_id) ─────

class TestBuildUserContent:
    def test_no_attachments_returns_string(self, tmp_path):
        assert build_user_content("你好", [], tmp_path) == "你好"

    def test_image_block(self, tmp_path):
        write_upload_file(tmp_path, "p.png", b"\x89PNG-bytes")
        att = {"filename": "p.png", "category": "image", "path": "uploads/p.png"}
        blocks = build_user_content("分析这张图", [att], tmp_path)
        assert isinstance(blocks, list)
        types = [b["type"] for b in blocks]
        assert "image" in types
        img_block = next(b for b in blocks if b["type"] == "image")
        assert img_block["source"]["type"] == "base64"
        assert blocks[-1] == {"type": "text", "text": "分析这张图"}
        # 不再暴露 attachment_id,仅业务名与工作区路径
        assert "attachment_id" not in blocks[0]["text"]
        assert "uploads/p.png" in blocks[0]["text"]

    def test_image_missing_degraded_explicit(self, tmp_path):
        att = {"filename": "gone.png", "category": "image", "path": "uploads/gone.png"}
        blocks = build_user_content("分析", [att], tmp_path)
        assert all(b["type"] == "text" for b in blocks)
        # 降级必须显式标注事实,不静默吞掉
        assert "未能注入" in blocks[0]["text"] or "无法解析" in blocks[0]["text"]

    def test_table_parses_from_workspace(self, tmp_path):
        write_upload_file(tmp_path, "a.csv", "id,amount\n1,10\n2,20\n".encode())
        att = {"filename": "a.csv", "category": "table", "path": "uploads/a.csv"}
        blocks = build_user_content("分析表格", [att], tmp_path)
        assert "amount" in blocks[0]["text"]

    def test_table_missing_fails_visible(self, tmp_path):
        att = {"filename": "gone.csv", "category": "table", "path": "uploads/gone.csv"}
        blocks = build_user_content("分析表格", [att], tmp_path)
        assert "解析失败" in blocks[0]["text"]

    def test_document_parses_from_workspace(self, tmp_path):
        write_upload_file(tmp_path, "note.md", "# 标题\n正文".encode())
        att = {"filename": "note.md", "category": "document", "path": "uploads/note.md"}
        blocks = build_user_content("总结", [att], tmp_path)
        assert "正文" in blocks[0]["text"]
        assert "note.md" in blocks[0]["text"]

    def test_model3d_text_block(self, tmp_path):
        att = {"filename": "a.glb", "category": "model3d", "path": "uploads/a.glb"}
        blocks = build_user_content("看看模型", [att], tmp_path)
        assert "3D模型" in blocks[0]["text"]
        assert "uploads/a.glb" in blocks[0]["text"]


# ── send_payload: 上传校验(替代旧 Upload API 用例) ────────────────

class TestUploadValidation:
    def test_ok(self):
        att = _validate_upload("pic.png", b"\x89PNG-data")
        assert att["filename"] == "pic.png" and att["category"] == "image"
        assert att["size"] == 9 and att["content"] == b"\x89PNG-data"

    def test_rejects_bad_extension(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            _validate_upload("evil.exe", b"MZ")
        assert "不支持的文件类型" in e.value.detail

    def test_rejects_empty(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            _validate_upload("a.csv", b"")
        assert "空文件" in e.value.detail

    def test_rejects_oversized(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            _validate_upload("big.txt", b"A" * (MAX_FILE_SIZE + 1))
        assert "文件过大" in e.value.detail


class TestParseSendRequest:
    """发送接口双模解析:multipart(payload+files)与 JSON(AS-BOT 形态)."""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient
        from services.datamind.api.pipeline import PipelineExecuteRequest
        from services.datamind.api.send_payload import parse_send_request

        app = FastAPI()

        @app.post("/t")
        async def _t(request: Request):
            params = await parse_send_request(request, PipelineExecuteRequest)
            return {
                "question": params.question,
                "workspace_id": params.workspace_id,
                "attachments": [
                    {"filename": a["filename"], "category": a["category"],
                     "size": a["size"], "content_len": len(a["content"])}
                    for a in params.attachments
                ],
            }

        return TestClient(app)

    def test_multipart_with_files(self, client):
        resp = client.post(
            "/t",
            data={"payload": json.dumps({"question": "看图", "workspace_id": 3})},
            files=[("files", ("pic.png", io.BytesIO(b"\x89PNG-x"), "image/png")),
                   ("files", ("a.csv", io.BytesIO(b"i,a\n1,2"), "text/csv"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["question"] == "看图" and data["workspace_id"] == 3
        assert [(a["filename"], a["category"]) for a in data["attachments"]] == [
            ("pic.png", "image"), ("a.csv", "table")]
        assert data["attachments"][0]["content_len"] == 6

    def test_multipart_rejects_bad_extension(self, client):
        resp = client.post(
            "/t",
            data={"payload": json.dumps({"question": "q"})},
            files=[("files", ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream"))],
        )
        assert resp.status_code == 400
        assert "不支持的文件类型" in resp.json()["detail"]

    def test_multipart_rejects_too_many_files(self, client):
        files = [("files", (f"f{i}.csv", io.BytesIO(b"x"), "text/csv"))
                 for i in range(MAX_FILES_PER_REQUEST + 1)]
        resp = client.post("/t", data={"payload": json.dumps({"question": "q"})}, files=files)
        assert resp.status_code == 400
        assert "最多上传" in resp.json()["detail"]

    def test_multipart_missing_payload(self, client):
        resp = client.post("/t", files=[("files", ("a.csv", io.BytesIO(b"x"), "text/csv"))])
        assert resp.status_code == 400
        assert "payload" in resp.json()["detail"]

    def test_json_ok(self, client):
        resp = client.post("/t", json={"question": "q", "pipeline_mode": "agent"})
        assert resp.status_code == 200
        assert resp.json()["attachments"] == []

    def test_json_rejects_attachment_ids(self, client):
        """附件 ID 已退役:JSON 请求携带 attachments 一律显式拒绝,不静默忽略."""
        resp = client.post("/t", json={"question": "q", "attachments": ["dead-beef-id"]})
        assert resp.status_code == 400
        assert "multipart" in resp.json()["detail"]


# ── fail-loud: 带附件但执行层未接住 → 显式 error ───────────────────

class TestAttachmentsFailLoud:
    def test_unhandled_attachments_yield_error_not_fallback(self):
        from services.datamind.services.chat_service import ChatService

        service = ChatService()

        async def empty_dispatch(**kwargs):
            return
            yield  # pragma: no cover — 使其成为异步生成器,但不产出任何事件

        service._try_dispatch_via_execution_layer = lambda **kw: empty_dispatch()
        request = SimpleNamespace()

        async def _not_disconnected():
            return False

        request.is_disconnected = _not_disconnected

        async def collect():
            out = []
            async for ev in service.stream_query(
                question="分析这张图", history=[], datasource_id=0, model_id=None,
                pipeline_mode="agent", retrieval_strategy=None, workspace_id=0,
                user_id=1, username="tester", request=request,
                attachments=[{"filename": "a.png", "category": "image", "size": 1, "content": b"x"}],
            ):
                out.append(ev)
            return out

        events = asyncio.run(collect())
        text = b"".join(events).decode("utf-8")
        assert "event: error" in text and "event: done" in text
        # 显式暴露:提示附件未被处理,而非静默落入忽略附件的内置管线
        assert "附件" in text and "未被处理" in text
