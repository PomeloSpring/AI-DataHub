"""本体模型重命名（save_draft 可选 name）回归：版本家族一致性。

锁住的行为（版本管理按 datasource_id+name 归组，见 list_versions）：

1. **整族同步改名**：rename 时同名归档历史版本行同步更新 name，否则改名后
   「版本管理」视图丢失历史版本；
2. **撞名 fail-loud**：另一版本家族已占用新名时拒绝保存（不静默合并家族）；
3. **空名拒绝**：name 为空白串时 ValueError，不落库；
4. **不传 name 不误伤**：普通保存（name=None）不触发任何家族改名 UPDATE。

纯 fake DB，不触碰数据库。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services import ontology_service


# ── 记录型 fake DB ──────────────────────────────────────────────

class _RecCursor:
    def __init__(self, log, collide):
        self._log = log
        self._collide = collide
        self._sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sql = " ".join(str(sql).split())
        self._log.append((self._sql, params))

    def fetchone(self):
        # 仅撞名检查 SELECT 需要回行；其余回 None
        if self._sql.startswith("SELECT id FROM adh_ontology_models"):
            return {"id": 999} if self._collide else None
        return None


class _RecConn:
    def __init__(self, log, collide=False):
        self._log = log
        self._collide = collide

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _RecCursor(self._log, self._collide)

    def commit(self):
        return None


def _run_save(monkeypatch, name_arg, collide=False):
    log = []
    doc = {"objects": [{"key": "case", "primary_table": "t_case_records"}]}
    monkeypatch.setattr(ontology_service, "get_model", lambda mid: {
        "id": 1, "status": "draft", "datasource_id": 7, "kind": "source",
        "name": "旧名字", "domain": "", "datasource_name": "test-alb",
    })
    for fn in ("find_duplicate_object_keys", "find_unregistered_tables",
               "find_orphan_dict_bindings", "find_m234_issues"):
        monkeypatch.setattr(ontology_service, fn, lambda d: [])
    monkeypatch.setattr(ontology_service, "get_metadata_conn",
                        lambda: _RecConn(log, collide))
    monkeypatch.setattr(ontology_service, "to_yaml", lambda d: "")
    monkeypatch.setattr(ontology_service, "to_md", lambda d: "")
    monkeypatch.setattr(ontology_service, "_now", lambda: "2026-01-01 00:00:00")
    ontology_service.save_draft(1, json.dumps(doc, ensure_ascii=False), name=name_arg)
    return log


def _family_updates(log):
    return [p for s, p in log
            if s.startswith("UPDATE adh_ontology_models SET name = %s WHERE COALESCE")]


def _main_update(log):
    hits = [p for s, p in log
            if s.startswith("UPDATE adh_ontology_models SET json_content = %s")]
    assert len(hits) == 1
    return hits[0]


class TestSaveDraftRename:
    def test_rename_rewrites_whole_version_family(self, monkeypatch):
        """当前行 + 同名归档历史版本一起改名（版本管理归组不丢历史）。"""
        log = _run_save(monkeypatch, "新名字")
        fam = _family_updates(log)
        assert len(fam) == 1
        # (新名, 家族 datasource_id, 旧名, 排除当前行 id)；不改 updated_at 保版本排序
        assert fam[0] == ("新名字", 7, "旧名字", 1)
        # 主 UPDATE 同批带上 name
        assert _main_update(log)[-2] == "新名字"

    def test_rename_collision_rejected(self, monkeypatch):
        """另一家族已占用新名 → fail-loud 拒绝，不静默合并版本家族。"""
        with pytest.raises(ValueError, match="已存在同名模型"):
            _run_save(monkeypatch, "被占用的名字", collide=True)

    def test_rename_blank_name_rejected(self, monkeypatch):
        with pytest.raises(ValueError, match="不能为空"):
            _run_save(monkeypatch, "   ")

    def test_save_without_name_touches_no_family(self, monkeypatch):
        """普通保存（name=None）不得触发任何家族改名 UPDATE。"""
        log = _run_save(monkeypatch, None)
        assert _family_updates(log) == []
        # 主 UPDATE 参数无 name 插值：[json, yaml, md, count, now, id]
        assert len(_main_update(log)) == 6
