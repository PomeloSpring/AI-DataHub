"""数据集批量操作（批量删除 / 批量改数据源）离线回归。

锁定的契约（对齐"批量=循环复用单条实现 + 逐条回报"范式）：
  - 逐条调用与单条完全同一个函数（`delete_dataset` / `update_dataset`），门禁/权限不另造第二套；
  - 部分成功 + 逐条点名：失败项带业务名与原因，成功项可带方言覆盖告警；
  - 空勾选 / 超过上限 / 未指定要改字段 → 直接 400（不开"点了但什么都没改"的空承诺）；
  - 可用范围只传业务名，执行源传 id（内部标识，护栏 §7 不外泄给前端展示）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException                                    # noqa: E402
from backend.modules.viz.api import datasets as api                  # noqa: E402
from backend.modules.viz.services import dataset_service as svc      # noqa: E402

USER = {"user_id": 7, "username": "analyst01", "role": "analyst"}
WS = 301


def _row(name):
    return {"id": 1, "name": name, "status": "active", "owner_id": 7}


# ── 批量删除 ──────────────────────────────────────────────────────────────

class TestBatchDelete:
    def test_empty_selection_is_400(self):
        with pytest.raises(HTTPException) as e:
            api.batch_delete_datasets({"ids": []}, user=USER, workspace_id=WS)
        assert e.value.status_code == 400

    def test_over_limit_is_400(self):
        ids = list(range(1, api.MAX_BATCH_ACTION + 2))
        with pytest.raises(HTTPException) as e:
            api.batch_delete_datasets({"ids": ids}, user=USER, workspace_id=WS)
        assert "最多" in str(e.value.detail)

    def test_per_item_reuses_single_delete_and_reports_reason(self, monkeypatch):
        deleted, calls = set(), []
        names = {1: "可删", 2: "被引用", 3: "无权限"}
        monkeypatch.setattr(svc, "get_dataset", lambda did: _row(names.get(did, "")))

        def fake_delete(did, identity):
            calls.append((did, identity["user_id"]))
            if did == 2:
                raise ValueError("数据集被看板引用, 无法删除: 经营看板")
            if did == 3:
                raise ValueError("仅创建者或管理员可编辑/删除该数据集")
            deleted.add(did)
            return {"success": True}

        monkeypatch.setattr(svc, "delete_dataset", fake_delete)
        out = api.batch_delete_datasets({"ids": [1, 2, 3]}, user=USER, workspace_id=WS)

        assert deleted == {1}
        assert [c[0] for c in calls] == [1, 2, 3]              # 逐条走同一实现
        assert calls[0][1] == 7                                # 可信身份透传（不取 body）
        assert out["succeeded_count"] == 1 and out["failed_count"] == 2
        failed = {f["id"]: f for f in out["failed"]}
        assert "被看板引用" in failed[2]["reason"] and failed[2]["name"] == "被引用"
        assert "仅创建者" in failed[3]["reason"]

    def test_invalid_id_reported_not_crash(self, monkeypatch):
        monkeypatch.setattr(svc, "delete_dataset", lambda did, ident: {"success": True})
        out = api.batch_delete_datasets({"ids": ["abc"]}, user=USER, workspace_id=WS)
        assert out["succeeded_count"] == 0 and out["failed"][0]["reason"] == "数据集标识无效"


# ── 批量改数据源 ──────────────────────────────────────────────────────────

class TestBatchUpdateDatasource:
    def test_no_field_is_400(self):
        with pytest.raises(HTTPException) as e:
            api.batch_update_datasource({"ids": [1]}, user=USER, workspace_id=WS)
        assert "未指定要修改的字段" in str(e.value.detail)

    def test_only_chosen_fields_patched_per_item(self, monkeypatch):
        seen = []
        monkeypatch.setattr(svc, "get_dataset", lambda did: _row("x"))
        monkeypatch.setattr(svc, "update_dataset",
                            lambda did, req, ident: (seen.append((did, dict(req))), {"id": did})[1])
        out = api.batch_update_datasource(
            {"ids": [1, 2], "available_datasources": ["prd-jp"]}, user=USER, workspace_id=WS)
        assert out["succeeded_count"] == 2
        assert all(req == {"available_datasources": ["prd-jp"]} for _did, req in seen)
        assert all("datasource_id" not in req for _did, req in seen)   # 未带的字段不动

    def test_exec_source_and_scope_together(self, monkeypatch):
        seen = []
        monkeypatch.setattr(svc, "get_dataset", lambda did: _row("x"))
        monkeypatch.setattr(svc, "update_dataset",
                            lambda did, req, ident: (seen.append(dict(req)), {"id": did})[1])
        api.batch_update_datasource(
            {"ids": [5], "datasource_id": 11, "available_datasources": ["prd-cn", "prd-jp"]},
            user=USER, workspace_id=WS)
        assert seen[0] == {"datasource_id": 11, "available_datasources": ["prd-cn", "prd-jp"]}

    def test_es_dataset_reported_as_not_processed(self, monkeypatch):
        """ES 型不挂在数据源上：单条实现会拒，批量逐条回报原因（不静默剔掉）。"""
        def fake_update(did, req, ident):
            if did == 900:
                raise ValueError("ES 数据集不挂在数据源上，没有可用数据源范围可配")
            return {"id": did}
        monkeypatch.setattr(svc, "get_dataset", lambda did: _row("设备告警按天"))
        monkeypatch.setattr(svc, "update_dataset", fake_update)
        out = api.batch_update_datasource(
            {"ids": [1, 900], "available_datasources": ["prd-jp"]}, user=USER, workspace_id=WS)
        assert out["succeeded_count"] == 1 and out["failed_count"] == 1
        assert out["failed"][0]["name"] == "设备告警按天" and "ES" in out["failed"][0]["reason"]

    def test_dialect_warnings_surface_on_succeeded(self, monkeypatch):
        monkeypatch.setattr(svc, "get_dataset", lambda did: _row("x"))
        monkeypatch.setattr(svc, "update_dataset",
                            lambda did, req, ident: {"id": did, "warnings": ["prd-sls 缺 postgres 正文"]})
        out = api.batch_update_datasource({"ids": [1], "datasource_id": 11}, user=USER, workspace_id=WS)
        assert out["succeeded"][0]["warnings"] == ["prd-sls 缺 postgres 正文"]
        assert "方言覆盖告警" in out["message"]

