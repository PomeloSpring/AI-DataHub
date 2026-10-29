"""本体归档还原与手动知识库同步的离线回归：不连接数据库/知识库。

覆盖：还原仅允许 archived→draft 且不直接改派生表、归档模型列表可见口径、
手动同步知识库的失败显式暴露（400/502，不静默吞成成功）。
"""
import pytest
from fastapi import HTTPException

from backend.modules.catalog.services import ontology_service
from backend.modules.catalog.services import ontology_kb_sync
from backend.modules.catalog.api import ontology as ontology_api


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass


# ── 归档还原 ─────────────────────────────────────────────────────────


def test_restore_only_allowed_from_archived(monkeypatch):
    """非归档模型拒绝还原（避免误把 active/draft 状态洗掉）。"""
    for status in ("active", "draft"):
        monkeypatch.setattr(ontology_service, "get_model", lambda mid, s=status: {"id": mid, "status": s})
        with pytest.raises(ValueError, match="仅已归档模型可还原"):
            ontology_service.restore(1)


def test_restore_unknown_model_rejected(monkeypatch):
    monkeypatch.setattr(ontology_service, "get_model", lambda mid: None)
    with pytest.raises(ValueError, match="模型不存在"):
        ontology_service.restore(1)


def test_restore_writes_draft_without_touching_derived_tables(monkeypatch):
    """archived → draft 只改状态行；派生表（对象/图谱）留给 save_draft/activate 级联。"""
    cur = FakeCursor()
    monkeypatch.setattr(ontology_service, "get_metadata_conn", lambda: FakeConn(cur))
    monkeypatch.setattr(ontology_service, "get_model",
                        lambda mid: {"id": mid, "status": "archived"})
    result = ontology_service.restore(9)
    assert result["id"] == 9
    writes = [sql for sql, _ in cur.executed]
    assert len(writes) == 1
    assert "UPDATE adh_ontology_models SET status = 'draft'" in writes[0]
    assert "adh_ontology_objects" not in writes[0]


def test_restore_api_maps_value_error_to_400(monkeypatch):
    def boom(model_id):
        raise ValueError("仅已归档模型可还原")

    monkeypatch.setattr(ontology_service, "restore", boom)
    with pytest.raises(HTTPException) as exc:
        ontology_api.restore_model(1)
    assert exc.value.status_code == 400


# ── 归档模型列表可见 ─────────────────────────────────────────────────


def test_list_models_include_archived_controls_filter(monkeypatch):
    cur = FakeCursor()
    monkeypatch.setattr(ontology_service, "get_metadata_conn", lambda: FakeConn(cur))
    ontology_service.list_models(include_archived=False)
    assert "status <> 'archived'" in cur.executed[-1][0]
    cur2 = FakeCursor()
    monkeypatch.setattr(ontology_service, "get_metadata_conn", lambda: FakeConn(cur2))
    ontology_service.list_models(include_archived=True)
    assert "status <> 'archived'" not in cur2.executed[-1][0]


# ── 手动同步知识库 ───────────────────────────────────────────────────


def test_kb_sync_invokes_sync_service(monkeypatch):
    called = {}
    monkeypatch.setattr(ontology_kb_sync, "sync_model_to_qmind",
                        lambda mid, kb_id=None: called.update(id=mid, kb_id=kb_id) or {"synced": 1, "targets": ["分析知识库"]})
    result = ontology_api.sync_model_kb(7)
    assert called["id"] == 7
    assert called["kb_id"] is None   # 未传 kb_id → 默认路由
    assert result == {"synced": 1, "targets": ["分析知识库"]}


def test_kb_sync_passes_selected_kb(monkeypatch):
    """关系图谱页选库后 kb_id 透传到同步服务（一次性定向推送）。"""
    called = {}
    monkeypatch.setattr(ontology_kb_sync, "sync_model_to_qmind",
                        lambda mid, kb_id=None: called.update(id=mid, kb_id=kb_id) or {"synced": 1, "targets": ["X"]})
    ontology_api.sync_model_kb(7, kb_id=42)
    assert called["kb_id"] == 42


def test_kb_sync_value_error_maps_to_400(monkeypatch):
    monkeypatch.setattr(ontology_kb_sync, "sync_model_to_qmind",
                        lambda mid, kb_id=None: (_ for _ in ()).throw(ValueError("模型不存在")))
    with pytest.raises(HTTPException) as exc:
        ontology_api.sync_model_kb(7)
    assert exc.value.status_code == 400


def test_kb_sync_failure_is_loud_not_silent(monkeypatch):
    """同步失败必须显式报 502 并带可读提示，不得吞成成功/空结果。"""
    monkeypatch.setattr(ontology_kb_sync, "sync_model_to_qmind",
                        lambda mid, kb_id=None: (_ for _ in ()).throw(RuntimeError("qmind unreachable")))
    with pytest.raises(HTTPException) as exc:
        ontology_api.sync_model_kb(7)
    assert exc.value.status_code == 502
    assert "同步未完成" in exc.value.detail


# ── 关系图谱页手动选库（kb_id 一次性覆盖默认路由）─────────────


def _stub_sync_internals(monkeypatch, uploaded):
    """把 sync_model_to_qmind 的 CLI/渲染/水位线副作用换成可观察的桩。"""
    monkeypatch.setattr(ontology_kb_sync, "_render_cloud_doc", lambda model: "语义文档正文")
    monkeypatch.setattr(ontology_kb_sync, "ensure_notebook",
                        lambda kb: (kb["notebook_id"], ""))
    monkeypatch.setattr(ontology_kb_sync, "_delete_old_sources", lambda nb, title: 0)
    monkeypatch.setattr(ontology_kb_sync, "_upload_markdown",
                        lambda nb, title, md: uploaded.append(nb) or (True, ""))
    monkeypatch.setattr(ontology_kb_sync, "_write_state", lambda *a, **k: None)


def test_sync_selected_kb_overrides_default_routing(monkeypatch):
    """手动选库应显式推送到该库，而非默认路由的目标库（源本体退役后语义不变）。"""
    uploaded = []
    _stub_sync_internals(monkeypatch, uploaded)
    monkeypatch.setattr(ontology_service, "get_model", lambda mid: {
        "id": mid, "name": "ARMS RUM 观测本体", "kind": "business",
        "datasource_id": 5, "status": "active"})
    # 默认路由指向另一库（验证手动选库确实走的是覆盖，而非默认）
    monkeypatch.setattr(ontology_kb_sync, "sync_targets",
                        lambda: [{"id": 5, "name": "全局库", "notebook_id": "nb-global"}])
    assert ontology_kb_sync.sync_targets_for_model(
        {"kind": "business", "datasource_id": 5})[0]["notebook_id"] == "nb-global"
    monkeypatch.setattr(ontology_kb_sync, "resolve_kb_target",
                        lambda kb_id: {"id": kb_id, "name": "运维知识库", "notebook_id": "nb-ops"})

    result = ontology_kb_sync.sync_model_to_qmind(1, kb_id=88)
    assert result["synced"] == 1 and result["targets"] == ["运维知识库"]
    assert uploaded == ["nb-ops"]  # 推到了所选库的 notebook，而非默认 nb-global
    assert "skipped" not in result


def test_sync_selected_kb_unavailable_is_loud(monkeypatch):
    """指定库不可用（已删/非 qmind/无 notebook）时显式 skipped，不静默换到默认库。"""
    uploaded = []
    _stub_sync_internals(monkeypatch, uploaded)
    monkeypatch.setattr(ontology_service, "get_model", lambda mid: {
        "id": mid, "name": "业务本体", "kind": "business", "datasource_id": 0, "status": "active"})
    monkeypatch.setattr(ontology_kb_sync, "resolve_kb_target", lambda kb_id: None)

    result = ontology_kb_sync.sync_model_to_qmind(2, kb_id=999)
    assert result["synced"] == 0
    assert result["skipped"] == "specified_kb_unavailable"
    assert uploaded == []  # 未向任何库推送
