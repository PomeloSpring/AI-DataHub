"""整板批量保存（PUT /dashboard/{id}/batch）的离线回归(不触真库)。

覆盖计划「批次 7 · 保存与反馈」要点:
  - 预检不过则一笔不写(不留半截草稿), 并把原因回给调用方。
  - 引用的图表已被他人删除 -> 中止保存并指名 id, 不静默跳过。
  - 逐条结果带 op/chart_id/error, 前端能定位是哪个图表没存上。
  - 非属主/非 admin -> 403(写链路唯一裁决点)。
"""

import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

dash = importlib.import_module("backend.modules.viz.services.dashboard_service")
api = importlib.import_module("backend.modules.viz.api.dashboard")


class FakeCursor:
    """按 SQL 关键字回放: SELECT 命中集由 existing_ids 决定, 写语句记进 log。"""

    def __init__(self, log, existing_ids=(), rowcount=1):
        self._log = log
        self._existing = list(existing_ids)
        self.rowcount = rowcount
        self._last_select = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self._log.append((normalized, params))
        if normalized.startswith("SELECT id FROM adh_charts"):
            self._last_select = "select"
        else:
            self._last_select = "write"

    def fetchall(self):
        return [{"id": i} for i in self._existing] if self._last_select == "select" else []

    def fetchone(self):
        if self._last_select != "select":
            return None
        return {"id": self._existing[0]} if self._existing else None


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def _install(monkeypatch, log, **kwargs):
    conn = FakeConn(FakeCursor(log, **kwargs))
    monkeypatch.setattr(dash, "get_metadata_conn", lambda: conn)
    return conn


# ── 预检中止：一笔不写 ───────────────────────────────────────────────────────


def test_batch_save_aborts_when_referenced_chart_missing(monkeypatch):
    """changed/deleted 引用了不属于本看板的图表 -> 整体中止, 不留下半截草稿。"""
    log = []
    conn = _install(monkeypatch, log, existing_ids=[5])  # 只有 5 存在, 6 已被删
    outcome = dash.chart_service.batch_save(10, [], {"5": {"name": "A"}, "6": {"name": "B"}}, [])

    assert outcome["ok"] is False
    assert any("6" in e for e in outcome["errors"])
    assert outcome["results"] == []
    # 只跑了预检 SELECT, 一条写语句都没发出
    assert [sql for sql, _ in log] == [sql for sql, _ in log if sql.startswith("SELECT")]
    assert conn.commits == 0


def test_batch_save_aborts_on_invalid_new_chart(monkeypatch):
    """新增图表缺名称/类型 -> 中止且不写入任何条目(含合法的 changed)。"""
    log = []
    conn = _install(monkeypatch, log, existing_ids=[5])
    outcome = dash.chart_service.batch_save(
        10, [{"name": "  ", "chart_type": "bar"}], {"5": {"name": "A"}}, [])

    assert outcome["ok"] is False
    assert any("缺少名称或类型" in e for e in outcome["errors"])
    assert conn.commits == 0
    assert not [sql for sql, _ in log if sql.startswith(("INSERT", "UPDATE", "DELETE"))]


# ── 正常路径：逐条结果可定位 ─────────────────────────────────────────────────


def test_batch_save_creates_reports_real_ids_per_item(monkeypatch):
    log = []
    _install(monkeypatch, log)
    monkeypatch.setattr(dash, "_invalidate_dashboard_payload", lambda *_: None)
    outcome = dash.chart_service.batch_save(
        10, [{"name": "新图", "chart_type": "bar"}, {"name": "新控件", "chart_type": "widget_select"}], {}, [])

    assert outcome["ok"] is True
    creates = [r for r in outcome["results"] if r["op"] == "create"]
    assert len(creates) == 2
    # index 与请求 added 顺序对齐, 前端据此把临时 id 换成正式 id
    assert [c["index"] for c in creates] == [0, 1]
    assert all(c["chart_id"] for c in creates)
    inserts = [sql for sql, _ in log if sql.startswith("INSERT")]
    assert len(inserts) == 2


def test_batch_save_surfaces_single_item_error_without_losing_others(monkeypatch):
    """单条写入抛错只标该条失败并带原因, 其余照常提交(不整体回滚也不静默吞)。"""
    log = []
    _install(monkeypatch, log)
    monkeypatch.setattr(dash, "_invalidate_dashboard_payload", lambda *_: None)
    calls = {"n": 0}
    real_create = dash.chart_service.create_chart

    def flaky(dashboard_id, data):
        calls["n"] += 1
        if data["name"] == "坏图":
            raise RuntimeError("字段过长")
        return real_create(dashboard_id, data)

    monkeypatch.setattr(dash.chart_service, "create_chart", flaky)
    outcome = dash.chart_service.batch_save(10, [{"name": "好图", "chart_type": "bar"}, {"name": "坏图", "chart_type": "bar"}], {}, [])

    assert outcome["ok"] is False
    failed = [r for r in outcome["results"] if not r["ok"]]
    assert len(failed) == 1 and failed[0]["index"] == 1
    assert "字段过长" in failed[0]["error"]
    assert sum(1 for r in outcome["results"] if r["ok"]) == 1


def test_batch_save_empty_draft_is_noop(monkeypatch):
    log = []
    conn = _install(monkeypatch, log)
    outcome = dash.chart_service.batch_save(10, [], {}, [])
    assert outcome["ok"] is True and outcome["results"] == []
    assert conn.commits == 0


# ── 写链路权限把关（fail-closed）─────────────────────────────────────────────


def test_batch_endpoint_rejects_non_owner(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(api, "check_dashboard_editable", lambda *_: False)
    with pytest.raises(HTTPException) as exc:
        api.batch_save_charts_endpoint(
            10, api.ChartBatchSave(added=[{"name": "x", "chart_type": "bar"}]),
            user={"user_id": 2, "role": "analyst"},
        )
    assert exc.value.status_code == 403
    assert "属主" in exc.value.detail


def test_batch_endpoint_passes_editable_user_through(monkeypatch):
    from fastapi import HTTPException  # noqa: F401 — 断言不应抛权限错

    monkeypatch.setattr(api, "check_dashboard_editable", lambda *_: True)
    seen = {}

    def fake_batch_save(dashboard_id, added, changed, deleted):
        seen.update(dashboard_id=dashboard_id, added=added, changed=changed, deleted=deleted)
        return {"ok": True, "errors": [], "results": [], "created": {}}

    monkeypatch.setattr(api.chart_service, "batch_save", fake_batch_save)
    monkeypatch.setattr(api.dashboard_service, "update_dashboard", lambda *a, **k: True)
    body = api.ChartBatchSave(added=[{"name": "x", "chart_type": "bar"}], changed={"5": {"name": "y"}}, deleted=[7],
                              metadata={"name": "看板"})
    out = api.batch_save_charts_endpoint(10, body, user={"user_id": 1, "role": "admin"})

    assert out["success"] is True
    assert seen["dashboard_id"] == 10 and seen["deleted"] == [7]
    assert seen["added"][0]["name"] == "x" and seen["changed"]["5"]["name"] == "y"


def test_batch_save_reports_metadata_write_gap(monkeypatch):
    """看板元数据没写成功时 success 必须为 False(不能只报图表成功)。"""
    from fastapi import HTTPException  # noqa: F401

    monkeypatch.setattr(api, "check_dashboard_editable", lambda *_: True)
    monkeypatch.setattr(api.chart_service, "batch_save",
                        lambda *a, **k: {"ok": True, "errors": [], "results": [], "created": {}})
    monkeypatch.setattr(api.dashboard_service, "update_dashboard", lambda *a, **k: False)
    out = api.batch_save_charts_endpoint(
        10, api.ChartBatchSave(metadata={"name": "看板"}), user={"user_id": 1, "role": "admin"})

    assert out["success"] is False
    assert any("元数据" in e for e in out["errors"])


def test_batch_save_payload_roundtrips_json_columns():
    """请求体里的 config/position 结构在 json.dumps 后可被服务端原样还原。"""
    payload = {"name": "x", "chart_type": "bar", "config": {"pageLayout": {"version": 2, "desktop": {"x": 0, "y": 0, "w": 6, "h": 12}}}}
    body = api.ChartBatchSave(added=[payload])
    restored = json.loads(json.dumps(body.added, ensure_ascii=False))[0]
    assert restored["config"]["pageLayout"]["version"] == 2
