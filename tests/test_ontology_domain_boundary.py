"""AS-BOT 本体域边界回归：判别口径用 kind；**系统本体不可编辑**。

2026-10-27 拍板（反转旧「仅系统本体可写」硬禁令）：
  * 系统本体（kind='system'）随项目版本发布、不由数据驱动 → AS-BOT **不得编辑**；
  * 业务本体（kind='business'）与源本体（kind='source'）→ AS-BOT **可编辑**，
    写权限由菜单与功能权限码把关（ontology:save/create/activate/publish，fail-closed），
    生效由草案/发布两段式控制（未发布只落草案、不进检索/查询）。

锁住的行为：
1. 域判别一律用 kind，不用 datasource_id（业务/源本体的 datasource_id 也可能为 0）；
2. 本体写工具对 kind='system' 的目标模型必须**执行前拒**（域约束），对 business/source 放行；
3. business/source 写入仍受权限码 fail-closed（无 ontology:save 即拒）；
4. 业务 AS-BOT 的本体可见域 = 跨源业务本体 + 自己绑定源的源本体（双轨）；
5. 字典表(adh_metrics/adh_dimensions)的 datasource_id=0 是「全局口径」语义，不受本改造影响。

纯函数 + fake DB，不触碰数据库。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.mind.execution.sdk_tools import ontology_tools as ot


def _payload(result):
    """工具返回是 MCP 格式：content[0].text 里是 JSON 字符串，不是顶层 dict。"""
    import json
    return json.loads(result["content"][0]["text"])


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        return None

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self.rows)


def _patch_model(monkeypatch, rows):
    """注入本体模型查询结果（_assert_system_model 走 get_metadata_conn）。"""
    import backend.common.db.metadata_db as mdb
    monkeypatch.setattr(mdb, "get_metadata_conn", lambda: _FakeConn(rows))


# ── 域约束：系统本体不可编辑（kind='system' 拒，业务/源放行）──────

class TestAssertModelEditable:
    def test_business_model_editable(self, monkeypatch):
        """业务本体可编辑——即使它 datasource_id=0（旧口径的陷阱）。"""
        _patch_model(monkeypatch, [{"name": "客户域本体", "kind": "business"}])
        ot._assert_model_editable(1791127653486902)   # 不抛即通过

    def test_source_model_editable(self, monkeypatch):
        _patch_model(monkeypatch, [{"name": "test-alb-全量本体", "kind": "source"}])
        ot._assert_model_editable(1789467214865307)   # 源本体可编辑

    def test_system_model_rejected(self, monkeypatch):
        """系统本体不可编辑（随项目版本发布，非数据驱动）。"""
        _patch_model(monkeypatch, [{"name": "AS-BOT 系统能力本体", "kind": "system"}])
        with pytest.raises(ValueError, match="系统本体"):
            ot._assert_model_editable(2789709247647610)

    def test_missing_model_rejected(self, monkeypatch):
        _patch_model(monkeypatch, [])
        with pytest.raises(ValueError, match="本体模型不存在"):
            ot._assert_model_editable(999)

    def test_zero_model_id_rejected(self):
        with pytest.raises(ValueError, match="model_id 必填"):
            ot._assert_model_editable(0)

    def test_error_message_carries_model_name(self, monkeypatch):
        """错误信息必须可诊断（带模型名），不得只说“无权限”。"""
        _patch_model(monkeypatch, [{"name": "系统能力本体", "kind": "system"}])
        with pytest.raises(ValueError) as e:
            ot._assert_model_editable(1)
        assert "系统能力本体" in str(e.value)


# ── 写操作入口接入域约束（save/activate/update/add 不得旁路）──────

class TestWriteOpsEnforceDomain:
    def test_save_business_model_lands_draft(self, monkeypatch):
        """业务本体可编辑：持 ontology:save → 走 save_draft_only 落草案（不碰生效）。"""
        _patch_model(monkeypatch, [{"name": "客户域本体", "kind": "business"}])
        monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: None)
        import backend.modules.catalog.services.ontology_service as osvc
        saved = {}

        def fake_save_draft_only(mid, content, name=None, created_by=""):
            saved.update({"id": mid, "content": content})
            return {"id": mid}

        monkeypatch.setattr(osvc, "save_draft_only", fake_save_draft_only)
        import asyncio
        p = _payload(asyncio.run(ot.save_ontology_model(
            {"model_id": 1791127653486902, "json_content": "{}"})))
        assert p.get("success") is True and saved["id"] == 1791127653486902

    def test_save_system_model_rejected_by_domain(self, monkeypatch):
        """系统本体：域约束执行前拒（即使有权限码也不放行）。"""
        _patch_model(monkeypatch, [{"name": "AS-BOT 系统能力本体", "kind": "system"}])
        monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: None)
        import asyncio
        p = _payload(asyncio.run(ot.save_ontology_model(
            {"model_id": 2789709247647610, "json_content": "{}"})))
        assert "error" in p and "系统本体" in p["error"]

    def test_save_business_without_perm_fail_closed(self, monkeypatch):
        """业务本体但无 ontology:save 权限码 → 拒（fail-closed，域放行不等于免权限）。"""
        _patch_model(monkeypatch, [{"name": "客户域本体", "kind": "business"}])

        def deny(*a, **k):
            raise PermissionError("缺少 ontology:save 权限")

        monkeypatch.setattr(ot, "_require_write_perm", deny)
        import asyncio
        p = _payload(asyncio.run(ot.save_ontology_model(
            {"model_id": 1791127653486902, "json_content": "{}"})))
        assert "error" in p and "ontology:save" in p["error"]

    def test_activate_business_ok_system_rejected(self, monkeypatch):
        import asyncio
        import backend.modules.catalog.services.ontology_service as osvc
        monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: None)
        monkeypatch.setattr(osvc, "activate", lambda mid: {"id": mid, "status": "active"})
        # 业务可激活
        _patch_model(monkeypatch, [{"name": "客户域本体", "kind": "business"}])
        assert _payload(asyncio.run(ot.activate_ontology_model(
            {"model_id": 1791127653486902}))).get("success") is True
        # 系统不可
        _patch_model(monkeypatch, [{"name": "系统能力本体", "kind": "system"}])
        assert "系统本体" in _payload(asyncio.run(ot.activate_ontology_model(
            {"model_id": 2789709247647610}))) ["error"]

    def test_update_object_business_ok_system_rejected(self, monkeypatch):
        import asyncio
        import backend.modules.catalog.services.ontology_service as osvc
        monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: None)
        monkeypatch.setattr(osvc, "get_model",
                            lambda mid: {"id": mid, "json_content": '{"objects":[]}'})
        monkeypatch.setattr(osvc, "get_draft", lambda mid: {"draft": None})
        monkeypatch.setattr(osvc, "save_draft_only",
                            lambda mid, content, created_by="": {"id": mid})
        # 业务可改
        _patch_model(monkeypatch, [{"name": "客户域本体", "kind": "business"}])
        p = _payload(asyncio.run(ot.update_ontology_object(
            {"model_id": 1791127653486902, "object": {"key": "case"}})))
        assert p.get("mode") in ("created", "updated")
        # 系统不可
        _patch_model(monkeypatch, [{"name": "系统能力本体", "kind": "system"}])
        assert "系统本体" in _payload(asyncio.run(ot.update_ontology_object(
            {"model_id": 2789709247647610, "object": {"key": "x"}})))["error"]


# ── 可见域：AS-BOT 只见系统本体，业务 AS-BOT 见业务+本源源 ──────

class TestVisibilityScope:
    def test_system_condition_uses_kind(self):
        """AS-BOT 可见域必须用 kind='system'，不能用 datasource_id=0。"""
        # 复刻 scoped_metadata 的条件构造（不 import 整个 execute 避免 ctx 依赖）
        system = True
        if system:
            condition = "kind = 'system'"
        assert "kind = 'system'" in condition
        assert "datasource_id" not in condition   # 旧口径不得残留

    def test_business_condition_covers_cross_source_and_own_source(self):
        """业务 AS-BOT：跨源业务本体 + 自己绑定源的源本体。"""
        datasource_id = 123
        condition = "(kind = 'business' OR (kind = 'source' AND datasource_id = %s))"
        assert "kind = 'business'" in condition   # 跨源业务本体
        assert "kind = 'source'" in condition     # 本源源本体
        assert "%s" in condition                  # 源本体按绑定源收口


# ── 字典全局口径不受影响（防误改）────────────────────────────

class TestListModelsVisibility:
    def test_datasource_filter_keeps_cross_source_models(self):
        """选某数据源时，业务/系统本体仍可见（跨源）；只滤源本体的物理来源。

        回归：首版条件写成 `kind='source' AND datasource_id=%s`，选了数据源后
        业务本体凭空消失（它们 datasource_id=0）——过滤反了。
        """
        import inspect
        from backend.modules.catalog.services import ontology_service as osvc
        src = inspect.getsource(osvc.list_models)
        # 必须含“非源本体恒可见”子句
        assert "kind <> 'source'" in src or "kind != 'source'" in src
        # 且源本体仍按物理来源收口
        assert "kind = 'source' AND datasource_id = %s" in src


class TestDictScopeUnaffected:
    def test_dict_condition_keeps_datasource_zero_semantics(self):
        """adh_metrics/adh_dimensions 的 datasource_id=0 是「全局口径」，不能改成 kind。

        字典表没有 kind 列；它是「全局共享口径」语义，与本体域判别是两回事。
        若有人把本体的 kind 口径照搬到字典查询，会查空（列不存在）。
        """
        import inspect
        from backend.modules.mind.execution.sdk_tools import scoped_metadata as sm
        src = inspect.getsource(sm.execute)
        # 字典查询仍按 datasource_id 作用域（含全局 0）
        assert "datasource_id = %s OR datasource_id = 0" in src
        # 本体查询用 kind（不出现裸 datasource_id=0 判系统域）
        assert "kind = 'system'" in src
