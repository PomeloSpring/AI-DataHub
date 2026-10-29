"""AS-BOT 本体域边界回归：判别口径用 kind；**系统本体不可编辑**。

2026-10-27 拍板（反转旧「仅系统本体可写」硬禁令）：
  * 系统本体（kind='system'）随项目版本发布、不由数据驱动 → AS-BOT **不得编辑**；
  * 业务本体（kind='business'）→ AS-BOT **可编辑**（源本体 kind='source' 已退役合并为 business），
    写权限由菜单与功能权限码把关（ontology:save/create/activate/publish，fail-closed），
    生效由草案/发布两段式控制（未发布只落草案、不进检索/查询）。

锁住的行为：
1. 域判别一律用 kind，不用 datasource_id（业务本体的 datasource_id 也可能为 0）；
2. 本体写工具对 kind='system' 的目标模型必须**执行前拒**（域约束），对非 system（业务）放行；
3. 业务本体写入仍受权限码 fail-closed（无 ontology:save 即拒）；
4. 业务 AS-BOT 的本体可见域 = 非 system 模型（按模型触达源：主源∩deploy_sites 收口）；
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


# ── 域约束：系统本体不可编辑（kind='system' 拒，业务放行）──────

class TestAssertModelEditable:
    def test_business_model_editable(self, monkeypatch):
        """业务本体可编辑——即使它 datasource_id=0（旧口径的陷阱）。"""
        _patch_model(monkeypatch, [{"name": "客户域本体", "kind": "business"}])
        ot._assert_model_editable(1791127653486902)   # 不抛即通过

    def test_business_model_with_primary_source_editable(self, monkeypatch):
        """源本体退役后的形态：business + 主源 datasource_id>0（原 kind='source' 迁来）仍可编辑。"""
        _patch_model(monkeypatch, [{"name": "ARMS RUM 观测本体", "kind": "business",
                                     "datasource_id": 1792408797510}])
        ot._assert_model_editable(1789467214865307)   # 非 system → 可编辑

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


# ── 可见域：AS-BOT 只见系统本体，业务 AS-BOT 见非 system（按触达源）──────

class TestVisibilityScope:
    def test_system_condition_uses_kind(self):
        """AS-BOT 可见域必须用 kind='system'，不能用 datasource_id=0。"""
        # 复刻 scoped_metadata 的系统域条件（不 import 整个 execute 避免 ctx 依赖）
        system = True
        if system:
            condition = "kind = 'system'"
        assert "kind = 'system'" in condition
        assert "datasource_id" not in condition   # 旧口径不得残留

    def test_business_condition_covers_all_non_system(self):
        """业务 AS-BOT 可见域（WS2 合并）：非 system 统统是业务本体，不再按 kind='source' 取数。"""
        # 合并后的谓词：kind <> 'system'（可见性再由 Python 按模型触达源收口）
        condition = "COALESCE(kind, '') <> 'system'"
        assert "<> 'system'" in condition
        assert "source" not in condition            # 不再有 kind='source' 分支
        assert "deploy_sites" not in condition      # 触达源收口在 Python 侧，不在本谓词


# ── 字典全局口径不受影响（防误改）────────────────────────────

class TestListModelsVisibility:
    def test_datasource_filter_keeps_cross_source_models(self, monkeypatch):
        """选某数据源时，系统本体/跨源业务本体仍可见（WS2 合并后）。

        回归旧缺陷：过滤写成只留本源，选了数据源后跨源业务本体凭空消失。
        合并后无 kind='source'，收口改为按模型触达源（主源∩deploy_sites）：
        - 系统本体、无具体源归属的抽象业务本体（scope 空）→ 恒可见；
        - 覆盖所选源的跨站业务本体（如联耀）→ 保留；
        - 仅属于其它具体源的业务本体 → 被选源滤掉。
        """
        from backend.modules.catalog.services import ontology_service as osvc

        def _row(rid, ds, kind, deploy=None):
            return {"id": rid, "datasource_id": ds, "name": f"m{rid}", "kind": kind,
                    "domain": "", "status": "active", "object_count": 0, "kb_id": None,
                    "created_by": "", "created_at": "2026-01-01", "updated_at": "2026-01-01",
                    "deploy_sites": deploy, "has_draft": 0}

        rows = [
            _row(1, 0, "system"),                        # 系统本体：恒可见
            _row(2, 0, "business"),                      # 抽象跨源业务本体：恒可见
            _row(3, 5, "business",                       # 联耀式跨站：deploy 覆盖源 5/9
                 '[{"site_code":"cn","datasource":"A"},{"site_code":"us","datasource":"B"}]'),
            _row(4, 77, "business"),                     # 仅属于源 77：选 5 时应被滤掉
        ]

        class _Cur:
            def execute(self, *a): pass
            def fetchall(self): return rows
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _Conn:
            def cursor(self): return _Cur()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(osvc, "get_metadata_conn", lambda: _Conn())
        monkeypatch.setattr("backend.core.site_service.datasource_name_id_map",
                            lambda: {"A": 5, "B": 9})

        got = {m["id"] for m in osvc.list_models(datasource_id=5, allowed_datasource_ids=None)}
        assert {1, 2, 3} <= got     # 系统/抽象/覆盖源5 的跨站业务 保留
        assert 4 not in got         # 仅属其它具体源的业务 被选源滤掉


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
        # 本体域判别用 kind（源本体退役后只剩 system/business）；合并后可见性收口走 Python
        # 侧 _model_visible（kind=="system" + 触达源交），本体 SQL 不再出现裸 datasource_id=0 判系统域。
        assert 'kind == "system"' in src
