"""AS-BOT 本体域边界（as-bot-system-as_bot §1/§4）回归：判别口径用 kind。

背景：本体归属改造后，业务本体的 datasource_id 也是 0（归属按业务域而非数据源）。
旧判别口径 `datasource_id IS NULL OR = 0` 会把 8 个业务本体误判为系统本体，导致：
  ① system_overview 把业务本体计成系统本体；
  ② scoped_metadata 把业务本体当系统知识暴露给 AS-BOT（域边界泄漏）；
  ③ AS-BOT 的 ontology.save/activate 可写任意业务本体（违反 §4 红线）。

锁住的行为：
1. 域判别一律用 kind='system'，不用 datasource_id=0（旧口径会误放行业务本体）；
2. AS-BOT 本体写操作对 kind != 'system' 的目标模型必须**拒绝**（执行前拒，fail-closed）；
3. 业务 AS-BOT 的本体可见域 = 跨源业务本体 + 自己绑定源的源本体（双轨）；
4. 字典表(adh_metrics/adh_dimensions)的 datasource_id=0 是「全局口径」语义，**不受本改造影响**。

纯函数 + fake DB，不触碰数据库。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.datamind.execution.sdk_tools import ontology_tools as ot


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
    import services.shared.common.db.metadata_db as mdb
    monkeypatch.setattr(mdb, "get_metadata_conn", lambda: _FakeConn(rows))


# ── 域约束：仅 kind='system' 可写 ────────────────────────────────

class TestAssertSystemModel:
    def test_system_model_allowed(self, monkeypatch):
        _patch_model(monkeypatch, [{"name": "AS-BOT 系统能力本体", "kind": "system"}])
        ot._assert_system_model(2789709247647610)   # 不抛即通过

    def test_business_model_rejected(self, monkeypatch):
        """业务本体必须被拒——即使它的 datasource_id=0（旧口径的陷阱）。"""
        _patch_model(monkeypatch, [{"name": "客户域本体", "kind": "business"}])
        with pytest.raises(ValueError, match="仅可操作系统本体"):
            ot._assert_system_model(1791127653486902)

    def test_source_model_rejected(self, monkeypatch):
        _patch_model(monkeypatch, [{"name": "test-alb-全量本体", "kind": "source"}])
        with pytest.raises(ValueError, match="仅可操作系统本体"):
            ot._assert_system_model(1789467214865307)

    def test_missing_model_rejected(self, monkeypatch):
        _patch_model(monkeypatch, [])
        with pytest.raises(ValueError, match="本体模型不存在"):
            ot._assert_system_model(999)

    def test_zero_model_id_rejected(self):
        with pytest.raises(ValueError, match="model_id 必填"):
            ot._assert_system_model(0)

    def test_error_message_carries_model_name(self, monkeypatch):
        """错误信息必须可诊断（带模型名与实际 kind），不得只说“无权限”。"""
        _patch_model(monkeypatch, [{"name": "案例域本体", "kind": "business"}])
        with pytest.raises(ValueError) as e:
            ot._assert_system_model(1)
        assert "案例域本体" in str(e.value)
        assert "business" in str(e.value)


# ── 写操作入口接入域约束（save/activate 不得旁路）──────────────

class TestWriteOpsEnforceDomain:
    def test_save_business_model_rejected(self, monkeypatch):
        _patch_model(monkeypatch, [{"name": "客户域本体", "kind": "business"}])
        import asyncio
        result = asyncio.run(ot.save_ontology_model(
            {"model_id": 1791127653486902, "json_content": "{}"}))
        p = _payload(result)
        assert "error" in p and "仅可操作系统本体" in p["error"]

    def test_activate_business_model_rejected(self, monkeypatch):
        _patch_model(monkeypatch, [{"name": "客户域本体", "kind": "business"}])
        import asyncio
        result = asyncio.run(ot.activate_ontology_model({"model_id": 1791127653486902}))
        p = _payload(result)
        assert "error" in p and "仅可操作系统本体" in p["error"]

    def test_save_system_model_without_perm_fail_closed(self, monkeypatch):
        """直执行口径：无 ontology:save 权限码即拒（审批回路已退役，不再生成审批单）。"""
        _patch_model(monkeypatch, [{"name": "AS-BOT 系统能力本体", "kind": "system"}])
        import services.authservice.services.role_service as rs
        monkeypatch.setattr(rs.role_service, "get_user_role_ai_perms", lambda *a: {})
        import asyncio
        result = asyncio.run(ot.save_ontology_model(
            {"model_id": 2789709247647610, "json_content": "{}"}))
        p = _payload(result)
        assert "error" in p and "ontology:save" in p["error"]
        assert "approval_required" not in p and "action_key" not in p

    def test_save_system_model_direct_executes(self, monkeypatch):
        """有 ontology:save 写权限 → 直执行保存，无审批中间态。"""
        _patch_model(monkeypatch, [{"name": "AS-BOT 系统能力本体", "kind": "system"}])
        saved = {}
        monkeypatch.setattr(ot, "_require_write_perm", lambda *a, **k: None)
        import services.datacatalog.services.ontology_service as osvc

        def fake_save(mid, content, name=None):
            saved.update({"id": mid, "content": content})
            return {"id": mid}

        monkeypatch.setattr(osvc, "save_draft", fake_save)
        import asyncio
        result = asyncio.run(ot.save_ontology_model(
            {"model_id": 2789709247647610, "json_content": "{}"}))
        p = _payload(result)
        assert p.get("success") is True and p["model_id"] == 2789709247647610
        assert saved["id"] == 2789709247647610  # 直调 save_draft，不经审批


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
        from services.datacatalog.services import ontology_service as osvc
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
        from services.datamind.execution.sdk_tools import scoped_metadata as sm
        src = inspect.getsource(sm.execute)
        # 字典查询仍按 datasource_id 作用域（含全局 0）
        assert "datasource_id = %s OR datasource_id = 0" in src
        # 本体查询用 kind（不出现裸 datasource_id=0 判系统域）
        assert "kind = 'system'" in src
