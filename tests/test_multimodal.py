"""Unit tests for multimodal package — 分类/解析器/OpenCV工具/上传接口/content blocks."""

import io
import json
import os
import sys
import zipfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datamind.multimodal import classify_extension
from services.datamind.multimodal.table_parser import parse_table_file
from services.datamind.multimodal.doc_parser import extract_document_text
from services.datamind.multimodal import opencv_tools
from services.datamind.multimodal.loader import build_user_content


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


# ── table_parser ───────────────────────────────────────────────────

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
        # 大量长文本行触发截断
        lines = ["col1,col2"] + [f"v{i}," + "x" * 200 for i in range(500)]
        p = tmp_path / "big.csv"
        p.write_text("\n".join(lines), encoding="utf-8")
        result = parse_table_file(str(p), "big.csv")
        assert len(result["preview_text"]) <= 6000 + 20


# ── doc_parser ─────────────────────────────────────────────────────

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


# ── opencv_tools ───────────────────────────────────────────────────

@pytest.fixture
def sample_image_att(tmp_path):
    """生成一张 100x80 的测试 PNG 图片附件行."""
    import cv2
    import numpy as np

    img = np.full((80, 100, 3), 200, dtype=np.uint8)
    cv2.rectangle(img, (20, 20), (80, 60), (0, 0, 0), 2)  # 画一个矩形框
    path = str(tmp_path / "test_img.png")
    cv2.imwrite(path, img)
    return {
        "id": "att001", "user_id": 1, "workspace_id": 0,
        "filename": "test_img.png", "category": "image",
        "storage_path": path, "size": os.path.getsize(path),
    }


def _mock_save_derived(source_att, img_bytes, filename, meta=None):
    return {
        "id": "derived001", "user_id": source_att.get("user_id", 0),
        "workspace_id": 0, "filename": filename, "category": "image",
        "storage_path": f"/tmp/{filename}", "size": len(img_bytes),
    }


class TestOpenCVTools:
    def test_image_info(self, sample_image_att):
        info = opencv_tools.image_info(sample_image_att)
        assert info["width"] == 100
        assert info["height"] == 80
        assert info["channels"] == 3
        assert "mean_brightness" in info and "contrast_std" in info

    def test_image_info_missing_file(self):
        result = opencv_tools.image_info({"filename": "x.png", "storage_path": "/nonexistent.png"})
        assert result["error"]

    def test_image_process_resize(self, sample_image_att):
        with patch("services.datamind.multimodal.loader.save_derived_attachment", side_effect=_mock_save_derived):
            result = opencv_tools.image_process(sample_image_att, "resize", {"width": 50})
        assert result["success"]
        assert result["width"] == 50
        assert result["height"] == 40  # 等比缩放
        assert result["attachment_id"] == "derived001"

    def test_image_process_crop_and_grayscale_and_edges(self, sample_image_att):
        with patch("services.datamind.multimodal.loader.save_derived_attachment", side_effect=_mock_save_derived):
            crop = opencv_tools.image_process(sample_image_att, "crop", {"x": 10, "y": 10, "width": 30, "height": 20})
            gray = opencv_tools.image_process(sample_image_att, "grayscale")
            edges = opencv_tools.image_process(sample_image_att, "edges")
        assert crop["success"] and crop["width"] == 30 and crop["height"] == 20
        assert gray["success"] and gray["width"] == 100
        assert edges["success"]

    def test_image_process_invalid_op(self, sample_image_att):
        result = opencv_tools.image_process(sample_image_att, "blur_all")
        assert result["error"]

    def test_image_process_crop_out_of_range(self, sample_image_att):
        result = opencv_tools.image_process(sample_image_att, "crop", {"x": 200, "y": 200, "width": 10, "height": 10})
        assert result["error"]

    def test_detect_table_region(self, tmp_path):
        # 白底上画一个大矩形模拟表格区域
        import cv2
        import numpy as np

        img = np.full((300, 400, 3), 255, dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (350, 250), (0, 0, 0), 2)
        path = str(tmp_path / "table.png")
        cv2.imwrite(path, img)
        att = {"id": "att002", "user_id": 1, "filename": "table.png",
               "storage_path": path, "size": os.path.getsize(path)}
        with patch("services.datamind.multimodal.loader.save_derived_attachment", side_effect=_mock_save_derived):
            result = opencv_tools.detect_table_region(att)
        assert "error" not in result
        if result.get("detected"):
            assert result["region"]["width"] > 0
            assert result["attachment_id"] == "derived001"

    def test_summarize_image(self, sample_image_att):
        summary = opencv_tools.summarize_image(sample_image_att)
        data = json.loads(summary)
        assert data["width"] == 100


# ── loader.build_user_content ─────────────────────────────────────

class TestBuildUserContent:
    def test_no_attachments_returns_string(self):
        assert build_user_content("你好", []) == "你好"

    def test_image_block_with_vision(self, sample_image_att):
        blocks = build_user_content("分析这张图", [sample_image_att], supports_vision=True)
        assert isinstance(blocks, list)
        types = [b["type"] for b in blocks]
        assert "image" in types
        img_block = next(b for b in blocks if b["type"] == "image")
        assert img_block["source"]["type"] == "base64"
        assert img_block["source"]["media_type"] == "image/png"
        assert blocks[-1] == {"type": "text", "text": "分析这张图"}

    def test_image_degraded_without_vision(self, sample_image_att):
        blocks = build_user_content("分析", [sample_image_att], supports_vision=False)
        assert all(b["type"] == "text" for b in blocks)
        assert "OpenCV" in blocks[0]["text"]

    def test_table_with_pre_parsed_meta(self):
        att = {"id": "t1", "filename": "a.csv", "category": "table",
               "storage_path": "/x", "parsed_meta": {"preview_text": "预览文本"}}
        blocks = build_user_content("分析表格", [att])
        assert blocks[0]["text"] == "预览文本"

    def test_document_with_pre_parsed_meta(self):
        att = {"id": "d1", "filename": "a.md", "category": "document",
               "storage_path": "/x", "parsed_meta": {"text": "文档正文"}}
        blocks = build_user_content("总结", [att])
        assert "文档正文" in blocks[0]["text"]
        assert "a.md" in blocks[0]["text"]

    def test_model3d_text_block(self):
        att = {"id": "m1", "filename": "a.glb", "category": "model3d",
               "storage_path": "/data/a.glb"}
        blocks = build_user_content("看看模型", [att])
        assert "3D模型" in blocks[0]["text"]
        assert "/data/a.glb" in blocks[0]["text"]


# ── Upload API ─────────────────────────────────────────────────────

@pytest.fixture
def upload_client(tmp_path):
    """构建带鉴权覆盖的 TestClient,DB 写入全部 mock."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.datamind.api.attachments import router
    from services.shared.common.auth import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/chat/attachments")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": 1, "username": "tester", "role": "admin",
    }
    return TestClient(app), tmp_path


def _mock_conn():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn, cursor


class TestUploadAPI:
    def test_upload_png_success(self, upload_client):
        client, tmp_path = upload_client
        conn, cursor = _mock_conn()
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with patch("services.shared.common.config.ADH_UPLOAD_DIR", str(tmp_path)), \
             patch("services.shared.common.db.metadata_db.get_metadata_conn", return_value=conn):
            resp = client.post(
                "/api/chat/attachments/upload",
                files=[("files", ("pic.png", io.BytesIO(png_bytes), "image/png"))],
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["attachments"]) == 1
        att = data["attachments"][0]
        assert att["category"] == "image"
        assert att["filename"] == "pic.png"
        assert att["url"].endswith("/file")
        assert cursor.execute.called  # INSERT 被调用
        # 文件实际落盘
        user_dir = tmp_path / "1"
        assert any(f.name.endswith("pic.png") for f in user_dir.iterdir())

    def test_upload_multi_category(self, upload_client):
        client, tmp_path = upload_client
        conn, _ = _mock_conn()
        files = [
            ("files", ("a.csv", io.BytesIO(b"x,y\n1,2"), "text/csv")),
            ("files", ("b.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")),
            ("files", ("c.glb", io.BytesIO(b"glTF"), "model/gltf-binary")),
        ]
        with patch("services.shared.common.config.ADH_UPLOAD_DIR", str(tmp_path)), \
             patch("services.shared.common.db.metadata_db.get_metadata_conn", return_value=conn):
            resp = client.post("/api/chat/attachments/upload", files=files)
        assert resp.status_code == 200
        cats = [a["category"] for a in resp.json()["attachments"]]
        assert cats == ["table", "document", "model3d"]

    def test_upload_rejects_bad_extension(self, upload_client):
        client, tmp_path = upload_client
        with patch("services.shared.common.config.ADH_UPLOAD_DIR", str(tmp_path)):
            resp = client.post(
                "/api/chat/attachments/upload",
                files=[("files", ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream"))],
            )
        assert resp.status_code == 400
        assert "不支持的文件类型" in resp.json()["detail"]

    def test_upload_rejects_too_many_files(self, upload_client):
        client, tmp_path = upload_client
        files = [
            ("files", (f"f{i}.txt", io.BytesIO(b"x"), "text/plain")) for i in range(6)
        ]
        with patch("services.shared.common.config.ADH_UPLOAD_DIR", str(tmp_path)):
            resp = client.post("/api/chat/attachments/upload", files=files)
        assert resp.status_code == 400
        assert "最多上传" in resp.json()["detail"]

    def test_upload_rejects_oversized_file(self, upload_client):
        client, tmp_path = upload_client
        conn, _ = _mock_conn()
        big = b"A" * (20 * 1024 * 1024 + 1)
        with patch("services.shared.common.config.ADH_UPLOAD_DIR", str(tmp_path)), \
             patch("services.shared.common.db.metadata_db.get_metadata_conn", return_value=conn):
            resp = client.post(
                "/api/chat/attachments/upload",
                files=[("files", ("big.txt", io.BytesIO(big), "text/plain"))],
            )
        assert resp.status_code == 400
        assert "文件过大" in resp.json()["detail"]
