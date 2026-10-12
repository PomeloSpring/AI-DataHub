"""SLS（PG 协议）schema 元数据获取回归。

锁定：SLS 的表/列清单与 * 展开取列，必须走 SLS PG 协议**确实支持**的
information_schema 查询（官方文档"查询元数据"），不得依赖 SLS 不填充/不支持的
table_catalog / ::regclass / obj_description / col_description / ordinal_position。
真实 postgres 分支保持不变（回归里断言其仍用 regclass 注释函数）。
"""

import asyncio

from backend.modules.catalog.services.datasource_service import datasource_service
from backend.modules.semhub.api import playground


class _FakeCur:
    def __init__(self, sink, rows):
        self._sink = sink
        self._rows = rows

    def execute(self, sql, params=None):
        self._sink["sql"] = sql
        self._sink["params"] = params

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, sink, rows):
        self._sink = sink
        self._rows = rows

    def cursor(self):
        return _FakeCur(self._sink, self._rows)

    def close(self):
        pass


def _sls_ds():
    return {"db_type": "sls", "host": "h", "port": 5432, "username": "u",
            "password": "p", "database_name": "alliedstar-prd"}


def test_list_db_tables_sls_avoids_pg_only_features(monkeypatch):
    sink = {}
    monkeypatch.setattr(datasource_service, "get_datasource_conn_from_dict",
                        lambda ds: _FakeConn(sink, [{"TABLE_NAME": "observability_rum"}]))
    rows = asyncio.run(datasource_service._list_db_tables(_sls_ds()))
    sql = sink["sql"]
    assert "information_schema.tables" in sql
    assert "table_schema" in sql
    # SLS 不支持/不填充的 PG 专有特性一律不得出现
    for bad in ("::regclass", "obj_description", "table_catalog"):
        assert bad not in sql
    assert sink["params"] == ("alliedstar-prd",)
    assert rows == [{"TABLE_NAME": "observability_rum"}]


def test_list_db_columns_sls_uses_documented_fields(monkeypatch):
    sink = {}
    monkeypatch.setattr(datasource_service, "get_datasource_conn_from_dict",
                        lambda ds: _FakeConn(sink, [{"COLUMN_NAME": "__time__"}]))
    asyncio.run(datasource_service._list_db_columns(_sls_ds(), "observability_rum"))
    sql = sink["sql"]
    assert "information_schema.columns" in sql
    for bad in ("::regclass", "col_description", "ordinal_position", "table_catalog"):
        assert bad not in sql
    assert sink["params"] == ("observability_rum",)


def test_list_db_columns_real_postgres_still_uses_comments(monkeypatch):
    """真实 postgres 分支不得被 SLS 改动波及：仍用 col_description/ordinal_position。"""
    sink = {}
    ds = _sls_ds() | {"db_type": "postgres"}
    monkeypatch.setattr(datasource_service, "get_datasource_conn_from_dict",
                        lambda d: _FakeConn(sink, []))
    asyncio.run(datasource_service._list_db_columns(ds, "t"))
    assert "col_description" in sink["sql"] and "ordinal_position" in sink["sql"]


def test_fetch_table_columns_sls_live_introspection(monkeypatch):
    """* 展开取列：SLS 走实连 information_schema.columns，不再落到空快照返回 None。"""
    sink = {}
    rows = [{"column_name": "__time__"}, {"column_name": "username"}]
    monkeypatch.setattr(playground, "_open_datasource",
                        lambda ds_id: (_FakeConn(sink, rows), "sls"))
    cols = playground._fetch_table_columns(123, "observability_rum")
    assert cols == ["__time__", "username"]
    assert "information_schema.columns" in sink["sql"]
    assert sink["params"] == ("observability_rum",)


# ── 元数据同步（纳入本体建模） ──────────────────────────────────────

def test_sls_fetchers_return_mysql_shaped_dicts():
    """SLS fetcher 输出必须与 MySQL 同构（复用 _apply_sync），且只用文档保证字段。"""
    from backend.modules.catalog.services import metadata_sync as ms

    sink = {}
    tbl_rows = [{"TABLE_NAME": "observability_rum", "TABLE_COMMENT": ""}]
    tables = ms._fetch_tables_sls(_FakeConn(sink, tbl_rows), "alliedstar-prd")
    assert tables == [{"TABLE_NAME": "observability_rum", "TABLE_COMMENT": ""}]
    assert "information_schema.tables" in sink["sql"] and "table_schema" in sink["sql"]
    assert sink["params"] == ("alliedstar-prd",)
    for bad in ("::regclass", "obj_description", "table_catalog"):
        assert bad not in sink["sql"]

    col_rows = [{"COLUMN_NAME": "__time__", "DATA_TYPE": "long",
                 "COLUMN_COMMENT": "", "COLUMN_KEY": "", "IS_NULLABLE": "YES"}]
    cols = ms._fetch_columns_sls(_FakeConn(sink, col_rows), "observability_rum")
    assert cols[0]["COLUMN_NAME"] == "__time__" and cols[0]["DATA_TYPE"] == "long"
    for bad in ("::regclass", "col_description", "ordinal_position", "table_catalog"):
        assert bad not in sink["sql"]


def test_sync_metadata_dispatches_sls(monkeypatch):
    """sync_metadata 必须把 sls 路由到 _sync_sls_metadata（不再 raise 暂不支持）。"""
    from backend.modules.catalog.services import metadata_sync as ms

    called = {}
    monkeypatch.setattr(ms, "_get_datasource_config",
                        lambda ds_id: {"db_type": "sls", "database": "alliedstar-prd"})
    monkeypatch.setattr(ms, "_sync_sls_metadata",
                        lambda ds_id, cfg: called.setdefault("sls", ds_id))
    ms.sync_metadata(42)
    assert called.get("sls") == 42


def test_sync_metadata_unknown_type_still_fails_loud(monkeypatch):
    """未知类型仍必须 fail-loud（不得静默回退），sls 支持不能把 oracle 也放行。"""
    import pytest
    from backend.modules.catalog.services import metadata_sync as ms

    monkeypatch.setattr(ms, "_get_datasource_config", lambda ds_id: {"db_type": "oracle"})
    with pytest.raises(ValueError, match="暂不支持"):
        ms.sync_metadata(1)
