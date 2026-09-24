"""版本戳跨层契约测试: ontology_kb_sync 写入的头必须能被 qmind_retriever 解析并判新鲜度。

防"写了读不出"的格式漂移(T2 产出 -> T4 消费)。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.datacatalog.services.ontology_kb_sync import _version_header
from services.datamind.rag import qmind_retriever as qr


class TestStampContract:
    def test_written_header_is_parseable(self):
        model = {"id": 123, "updated_at": "2026-09-20T10:00:00"}
        header = _version_header(model)
        stamp = qr._extract_doc_stamp([{"content": header + "\n# 本体模型 ..."}])
        assert stamp is not None
        assert stamp["model_id"] == "123"
        assert stamp["model_version"] == "2026-09-20T10:00:00"

    def test_no_stamp_returns_none(self):
        assert qr._extract_doc_stamp([{"content": "普通文档无版本戳"}]) is None
        assert qr._extract_doc_stamp([]) is None


class TestFreshness:
    def _chunks(self, version):
        return [{"content": f"<!-- model-version-stamp model_id=1 model_version={version} synced_at=x -->"}]

    def test_stale_when_version_differs(self, monkeypatch):
        monkeypatch.setattr(
            "services.datacatalog.services.ontology_kb_sync.current_active_version",
            lambda ds: {"model_id": 1, "model_version": "v2"})
        out = qr._evaluate_doc_freshness(1, self._chunks("v1"))
        assert out["doc_stale"] is True

    def test_fresh_when_version_matches(self, monkeypatch):
        monkeypatch.setattr(
            "services.datacatalog.services.ontology_kb_sync.current_active_version",
            lambda ds: {"model_id": 1, "model_version": "v2"})
        out = qr._evaluate_doc_freshness(1, self._chunks("v2"))
        assert out["doc_stale"] is False

    def test_unknown_when_no_active(self, monkeypatch):
        monkeypatch.setattr(
            "services.datacatalog.services.ontology_kb_sync.current_active_version",
            lambda ds: None)
        out = qr._evaluate_doc_freshness(1, self._chunks("v1"))
        assert out["doc_stale"] is None

    def test_unknown_when_no_stamp(self):
        out = qr._evaluate_doc_freshness(1, [{"content": "无戳"}])
        assert out["doc_stale"] is None and out["doc_version"] is None


class TestKbSeedExtraction:
    """T7 同源契约: to_cloud_md 渲染的对象标题必须能被 extract_object_keys 抽回 key。"""

    def test_render_then_extract(self):
        from services.datacatalog.services.ontology_service import to_cloud_md
        doc = {"domain": "医疗", "objects": [
            {"key": "case", "display_name": "案例", "aliases": ["工单"]},
            {"key": "hospital", "display_name": "医院"},
            {"key": "no_key_obj", "display_name": "其他"},
        ]}
        md = to_cloud_md(doc)
        keys = qr.extract_object_keys([{"content": md}])
        assert keys == ["case", "hospital", "no_key_obj"]

    def test_extract_dedupes_and_caps(self):
        chunks = [{"content": "## 业务对象: 甲 (a)"}, {"content": "## 业务对象: 甲 (a)\n## 业务对象: 乙 (b)"}]
        assert qr.extract_object_keys(chunks) == ["a", "b"]
        assert qr.extract_object_keys(chunks, limit=1) == ["a"]

    def test_extract_ignores_noise(self):
        assert qr.extract_object_keys([]) == []
        assert qr.extract_object_keys([{"content": "普通段落 (x)"}]) == []
