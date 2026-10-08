"""业务域标记事实源归一（adh_tag_values → adh_table_info.domain_tag）回归。

背景：`adh_table_info.domain_tag` 原先由 `metadata_sync.extract_domain_tag` 从
**表名前缀**推断（只认 dim_/dwd_/ods_/adh_），对 `t_*` 业务表全部回落 'other'——
249 张表里 249 张都是 'other'，后端检索/分批吃到的域信号实际是失效的；
而真正在用的人工标注（`adh_tag_values` 的「业务域」分类）只有前端在读。
更糟的是元数据同步会用推断值**覆盖**已有值，人工标注会被冲掉。

锁住的行为：
1. 事实源 = adh_tag_values 人工标注，adh_table_info.domain_tag 只是派生缓存；
2. 唯一匹配才派生；同名多源 / 同表多域 → 不猜，显式记录；
3. 表名启发式对业务表失效这一事实不得被"改进"掉而忘记修根因。

纯函数 + fake DB，不触碰数据库。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services import tags_service
from backend.modules.catalog.services.metadata_sync import extract_domain_tag


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self._sql = " ".join(str(sql).split())
        # 记完整 SQL（不截断），否则断言条件子句时会因截断误报
        self.db.calls.append((self._sql, params))
        if self._sql.upper().startswith("UPDATE ADH_TABLE_INFO"):
            self.db.updates.append((self._sql, params))

    def fetchall(self):
        if "adh_tag_values" in self._sql:
            return self.db.tag_rows
        if "adh_table_info" in self._sql:
            return self.db.table_rows
        return []


class _FakeDB:
    def __init__(self, tag_rows, table_rows):
        self.tag_rows = tag_rows
        self.table_rows = table_rows
        self.calls = []
        self.updates = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return _FakeCursor(self)


def _derive(monkeypatch, tag_rows, table_rows):
    fake = _FakeDB(tag_rows, table_rows)
    monkeypatch.setattr(tags_service, "DBConnection", lambda: fake)
    return tags_service.derive_table_domain_tags(), fake


class TestDeriveDomainTags:
    def test_derives_from_manual_labels(self, monkeypatch):
        """人工标注的业务域必须落到 domain_tag（此前一直是 'other'）。"""
        tags = [{"table_name": "t_case_records", "domain_name": "案例域"},
                {"table_name": "t_user_customer", "domain_name": "客户域"}]
        tables = [{"id": 1, "datasource_id": 7, "table_name": "t_case_records", "domain_tag": "other"},
                  {"id": 2, "datasource_id": 7, "table_name": "t_user_customer", "domain_tag": "other"}]
        r, fake = _derive(monkeypatch, tags, tables)
        assert r["updated"] == 2
        assert sorted(r["updated_tables"]) == ["t_case_records", "t_user_customer"]
        assert len(fake.updates) == 2
        written = {p[0] for _, p in fake.updates}
        assert written == {"案例域", "客户域"}

    def test_idempotent_when_already_derived(self, monkeypatch):
        """值已一致就不重复写（打标会触发派生，必须幂等）。"""
        tags = [{"table_name": "t_case_records", "domain_name": "案例域"}]
        tables = [{"id": 1, "datasource_id": 7, "table_name": "t_case_records", "domain_tag": "案例域"}]
        r, fake = _derive(monkeypatch, tags, tables)
        assert r["updated"] == 0
        assert fake.updates == []

    def test_unlabelled_table_is_reported_not_guessed(self, monkeypatch):
        """无标注的表计入 uncovered，不得编造业务域。"""
        tags = [{"table_name": "t_case_records", "domain_name": "案例域"}]
        tables = [{"id": 1, "datasource_id": 7, "table_name": "t_case_records", "domain_tag": "other"},
                  {"id": 2, "datasource_id": 7, "table_name": "geo_ip_metadata", "domain_tag": "other"}]
        r, fake = _derive(monkeypatch, tags, tables)
        assert r["updated"] == 1
        assert r["uncovered_count"] == 1
        assert r["uncovered_sample"] == ["geo_ip_metadata"]
        assert len(fake.updates) == 1

    def test_same_name_across_datasources_is_skipped(self, monkeypatch):
        """同名表跨数据源会串味，必须跳过并告警（adh_tag_values.entity_id 是裸表名）。"""
        tags = [{"table_name": "t_order_record", "domain_name": "订单域"}]
        tables = [{"id": 1, "datasource_id": 7, "table_name": "t_order_record", "domain_tag": "other"},
                  {"id": 2, "datasource_id": 9, "table_name": "t_order_record", "domain_tag": "other"}]
        r, fake = _derive(monkeypatch, tags, tables)
        assert r["updated"] == 0
        assert r["ambiguous"] == ["t_order_record"]
        assert fake.updates == []
        assert any("同名表存在于多个数据源" in w for w in r["warnings"])

    def test_conflicting_labels_are_not_guessed(self, monkeypatch):
        """同一张表被打了两个业务域 → 不猜，显式告警且不派生。"""
        tags = [{"table_name": "t_case_records", "domain_name": "案例域"},
                {"table_name": "t_case_records", "domain_name": "订单域"}]
        tables = [{"id": 1, "datasource_id": 7, "table_name": "t_case_records", "domain_tag": "other"}]
        r, fake = _derive(monkeypatch, tags, tables)
        assert r["updated"] == 0
        assert fake.updates == []
        assert any("同时标注了多个业务域" in w for w in r["warnings"])

    def test_datasource_filter_limits_scope(self, monkeypatch):
        """传 datasource_id 时只在该源范围内派生。"""
        tags = [{"table_name": "t_case_records", "domain_name": "案例域"}]
        tables = [{"id": 1, "datasource_id": 7, "table_name": "t_case_records", "domain_tag": "other"}]
        fake = _FakeDB(tags, tables)
        monkeypatch.setattr(tags_service, "DBConnection", lambda: fake)
        tags_service.derive_table_domain_tags(datasource_id=7)
        select_sql, params = fake.calls[1]
        assert "datasource_id = %s" in select_sql
        assert params == [7]


class TestHeuristicIsKnownBroken:
    def test_business_table_falls_back_to_other(self):
        """锁住根因认知：表名启发式对 t_*/open_* 业务表全部失效。

        这条断言红了不代表要改启发式，而是提醒：域信号的事实源是人工标注，
        别再指望 extract_domain_tag 给出正确业务域。
        """
        for t in ("t_user_customer", "t_case_records", "t_order_record", "open_isv_info"):
            assert extract_domain_tag(t) == "other", t

    def test_warehouse_prefixed_tables_are_recognised(self):
        assert extract_domain_tag("dwd_order") == "dwd"
        assert extract_domain_tag("ods_user") == "ods"
        assert extract_domain_tag("adh_metrics") == "adh"


class TestSyncDoesNotOverwriteLabels:
    """源码级锁：元数据同步的更新分支不得用表名推断覆盖已有标注。

    这是「人工标注被同步冲掉」的根因。若有人改回让推断值写回，
    已派生的业务域会再次被抹成 'other'，且要等检索变差才会发现。
    """

    def test_update_branch_keeps_existing_tags(self):
        import inspect
        from backend.modules.catalog.services import metadata_sync
        src = inspect.getsource(metadata_sync._sync_mysql_metadata)
        # 更新分支必须取旧值兜底（同步不覆盖派生/人工标注）
        # ↑ 兜底：old.get(...) 优先，推断值仅在列为空时填入
        assert '"region_tag": old.get("region_tag") or region_tag' in src
        assert '"domain_tag": old.get("domain_tag") or domain_tag' in src

    def test_change_detection_ignores_tags(self):
        """变更判定不得再把 region_tag/domain_tag 纳入，否则每次同步都会触发写回。"""
        import inspect
        from backend.modules.catalog.services import metadata_sync
        src = inspect.getsource(metadata_sync._sync_mysql_metadata)
        assert 'or (old.get("region_tag") or "") != region_tag' not in src
        assert 'or (old.get("domain_tag") or "") != domain_tag' not in src
