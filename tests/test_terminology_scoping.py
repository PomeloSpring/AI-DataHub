"""terminology_manager 数据源分桶单测: 同名术语在不同数据源不得互相串味。

用内存假连接替换 _get_connection, 按 SQL 中的 datasource 过滤条件回放行,
不依赖元数据库。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.datamind.rag import terminology_manager as tm


TERMS = [
    {"id": 1, "datasource_id": 1, "term_cn": "活跃用户", "term_en": "active_user",
     "term_aliases": "下单用户", "term_type": "指标", "target_table": "t_order",
     "target_column": "uid", "description": ""},
    {"id": 2, "datasource_id": 2, "term_cn": "活跃用户", "term_en": "active_user",
     "term_aliases": "登录用户", "term_type": "指标", "target_table": "t_login",
     "target_column": "uid", "description": ""},
    {"id": 3, "datasource_id": 0, "term_cn": "患者", "term_en": "patient",
     "term_aliases": "病人", "term_type": "对象", "target_table": "t_patient",
     "target_column": "id", "description": ""},
]

TABLE_KEYWORDS = [
    {"table_name": "t_order", "keywords": "订单,交易", "datasource_id": 1},
    {"table_name": "t_login", "keywords": "登录,活跃用户", "datasource_id": 2},
]


class _FakeCursor:
    def __init__(self):
        self._rows = []

    def execute(self, sql, params=None):
        params = list(params or [])
        # 模拟 pymysql 的参数格式化契约：占位符与参数数量不一致时驱动层直接报错，
        # 防止再次出现字面量 0 占位不匹配、被 except 静默回退空缓存的存量 bug。
        if params and sql.count("%s") != len(params):
            raise ValueError("not all arguments converted during string formatting")
        if "adh_business_terms" in sql:
            rows = TERMS
        elif "adh_table_info" in sql:
            rows = TABLE_KEYWORDS
        else:
            rows = []
        # datasource_id > 0 时查询带 (datasource_id = %s OR datasource_id = 0)
        if params:
            ds = params[0]
            rows = [r for r in rows if r.get("datasource_id") in (ds, 0)]
        self._rows = [dict(r) for r in rows]

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    monkeypatch.setattr(tm, "_get_connection", lambda: _FakeConn())
    tm.clear_cache()
    yield
    tm.clear_cache()


class TestTerminologyScoping:
    def test_same_term_not_cross_polluting(self):
        # ds=1 的"活跃用户"只扩展出"下单用户", 不见 ds=2 的"登录用户"
        exp1 = set(tm.expand_synonyms(["活跃用户"], datasource_id=1))
        assert "下单用户" in exp1
        assert "登录用户" not in exp1

        exp2 = set(tm.expand_synonyms(["活跃用户"], datasource_id=2))
        assert "登录用户" in exp2
        assert "下单用户" not in exp2

    def test_global_terms_visible_to_all(self):
        for ds in (1, 2):
            m = tm.get_synonym_map(datasource_id=ds)
            assert "病人" in m  # datasource_id=0 的全局术语对本源可见

    def test_datasource_zero_keeps_legacy_full_load(self):
        # ds=0(系统/全局调用)保持现行为: 全量加载所有数据源的术语行
        # (同名术语的 synonym_map 仍为既有的后写覆盖合并语义, 不在此断言)
        terms0 = tm.get_all_terms(datasource_id=0)
        assert {t["datasource_id"] for t in terms0} == {0, 1, 2}

    def test_table_keywords_scoped(self):
        kws1 = tm.get_all_terms(datasource_id=1)
        assert all(t["datasource_id"] in (0, 1) for t in kws1)
        # ds=1 的关键词索引不含 ds=2 的表关键词
        m1 = tm.get_synonym_map(datasource_id=1)
        assert "交易" in m1
        assert "登录" not in m1

    def test_clear_cache_single_bucket(self):
        tm.get_synonym_map(datasource_id=1)
        tm.get_synonym_map(datasource_id=2)
        tm.clear_cache(datasource_id=1)
        assert 1 not in tm._cache
        assert 2 in tm._cache
